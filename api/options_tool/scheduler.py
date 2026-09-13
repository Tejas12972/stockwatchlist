"""In-process scheduler for the daily snapshot.

Why in-process rather than a cron container or a scheduled machine: a Fly volume
attaches to exactly one machine at a time, and the snapshot writes to the same
SQLite database the API reads. A separate scheduled machine could not mount the
volume while the API held it. Running the job inside the API process is the
arrangement that matches the storage.

This is a deliberate trade-off. A single-machine, single-file-database app can
schedule its own work; anything multi-instance could not, because every replica
would fire the same job. If this ever runs more than one machine, move the
schedule out (a Fly scheduled machine plus Postgres, or an external trigger
hitting `POST /snapshot`).

Two properties make it safe:

- **Catch-up on start.** If the process was down at the scheduled time and the
  day has no snapshot yet, it runs immediately rather than skipping. This is the
  equivalent of systemd's `Persistent=true`, and it matters because a missed day
  is a permanent hole — historical implied volatility cannot be bought from a
  free source or backfilled.
- **Idempotency.** A restart loop, a double fire, or a catch-up that overlaps
  the real schedule all refresh the day in place rather than duplicating it. The
  database constraints guarantee that regardless of what this module does.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from datetime import UTC, datetime, time, timedelta

from options_tool.analytics.chain import market_date
from options_tool.config import Settings
from options_tool.db.queries import history_depth, list_watchlist
from options_tool.db.session import session_scope
from options_tool.db.snapshot import snapshot_watchlist
from options_tool.providers import get_provider
from options_tool.providers.rates import resolve_risk_free_rate

__all__ = ["SnapshotScheduler", "next_run_at", "parse_schedule_time"]

logger = logging.getLogger(__name__)

# US equity markets are shut; a weekend snapshot would capture Friday's stale
# chain and store it as a fresh observation.
_TRADING_WEEKDAYS = {0, 1, 2, 3, 4}  # Monday..Friday

# Every deployment firing at exactly HH:MM:00 is when the unofficial vendor
# throttles hardest. Mirrors systemd's RandomizedDelaySec.
_MAX_JITTER_SECONDS = 600


def parse_schedule_time(value: str) -> time:
    """Parse 'HH:MM' into a UTC time, with a useful error if it is malformed."""
    try:
        hour, minute = (int(part) for part in value.strip().split(":", 1))
        return time(hour=hour, minute=minute, tzinfo=UTC)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"OPTIONS_SNAPSHOT_AT must look like '21:15' (24h UTC), got {value!r}"
        ) from exc


def next_run_at(after: datetime, at: time) -> datetime:
    """The next weekday occurrence of `at`, strictly after `after`."""
    candidate = after.astimezone(UTC).replace(
        hour=at.hour, minute=at.minute, second=0, microsecond=0
    )
    if candidate <= after:
        candidate += timedelta(days=1)
    while candidate.weekday() not in _TRADING_WEEKDAYS:
        candidate += timedelta(days=1)
    return candidate


class SnapshotScheduler:
    """Runs the snapshot once per trading day, inside the API process."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.at = parse_schedule_time(settings.options_snapshot_at)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="snapshot-scheduler")
        logger.info(
            "snapshot scheduler started; next run %s",
            next_run_at(datetime.now(UTC), self.at).isoformat(),
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("snapshot scheduler stopped")

    async def _run(self) -> None:
        # Catch up first: if the process was down through today's slot, the day
        # is still recoverable right now but will not be tomorrow.
        try:
            if await asyncio.to_thread(self._needs_catch_up):
                logger.info("no snapshot stored for today yet — running catch-up now")
                await self._snapshot()
        except Exception:
            logger.exception("catch-up snapshot failed; continuing on schedule")

        while True:
            now = datetime.now(UTC)
            target = next_run_at(now, self.at)
            jitter = random.uniform(0, _MAX_JITTER_SECONDS)
            delay = (target - now).total_seconds() + jitter

            logger.info("next snapshot at %s (+%.0fs jitter)", target.isoformat(), jitter)
            await asyncio.sleep(delay)

            try:
                await self._snapshot()
            except Exception:
                # Never let one bad day kill the scheduler; tomorrow still needs
                # to run, and the catch-up path cannot recover a dead task.
                logger.exception("scheduled snapshot failed")

    def _needs_catch_up(self) -> bool:
        """True when the schedule has passed today and nothing is stored for it."""
        now = datetime.now(UTC)
        if now.weekday() not in _TRADING_WEEKDAYS:
            return False
        scheduled_today = now.replace(
            hour=self.at.hour, minute=self.at.minute, second=0, microsecond=0
        )
        if now < scheduled_today:
            return False

        today = market_date(now)
        with session_scope() as session:
            tickers = list_watchlist(session)
            if not tickers:
                return False
            # If any tracked symbol is missing today, the run is worth making;
            # it is idempotent for the ones already captured.
            return any(history_depth(session, ticker.symbol)[2] != today for ticker in tickers)

    async def _snapshot(self) -> None:
        await asyncio.to_thread(self._snapshot_blocking)

    def _snapshot_blocking(self) -> None:
        """The actual work, off the event loop — it is network- and CPU-bound."""
        provider = get_provider(self.settings.options_provider)
        rate = resolve_risk_free_rate(self.settings)

        with session_scope() as session:
            results = snapshot_watchlist(
                session, provider, rate, self.settings.options_dividend_yield
            )

        for result in results:
            if result.errors and not result.contracts_written:
                logger.error("%s: snapshot failed — %s", result.ticker, result.errors[0])
                continue
            logger.info(
                "%s %s: %d contracts (%.0f%% solved), 30d ATM IV %s",
                result.ticker,
                result.snapshot_date,
                result.contracts_written,
                result.solve_rate * 100,
                f"{result.atm_iv_30d:.4f}" if result.atm_iv_30d else "unavailable",
            )
