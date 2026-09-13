"""The daily snapshot job -- the piece that makes IV rank possible at all.

Free market-data sources do not sell historical implied volatility. Rather than
substitute realised volatility and call it the same thing, this job captures the
full chain once a day and writes it, with our own computed IV, to SQLite.
History accumulates from day one forward (SPEC.md section 3).

**Idempotency is the requirement.** The job may be re-run: a cron timer fires
twice, a run half-fails and gets retried, someone runs it by hand. Running it
again on the same day must update that day's rows, never append a second copy --
a duplicated day would silently double-weight itself in the IV-rank window.

That is enforced in two places. The unique constraints in `models.py` make
duplicates impossible at the database level, and the `ON CONFLICT DO UPDATE`
upserts here make a re-run a correct refresh rather than an error.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from options_tool.analytics.atm_iv import atm_iv_for_expiry, constant_maturity_atm_iv
from options_tool.analytics.chain import build_chain_frame
from options_tool.db.models import Contract, Snapshot, Ticker
from options_tool.providers.base import MarketDataProvider, ProviderError

__all__ = ["SnapshotResult", "snapshot_ticker", "snapshot_watchlist", "get_or_create_ticker"]

logger = logging.getLogger(__name__)

# How many expiries to capture per ticker per day. Enough to bracket the 30-day
# constant-maturity point and give the term structure some shape, without
# pulling every LEAPS strike daily for data nobody queries.
DEFAULT_MAX_EXPIRIES = 6


@dataclass
class SnapshotResult:
    """What one ticker-day of snapshotting actually did."""

    ticker: str
    snapshot_date: date
    contracts_written: int = 0
    contracts_solved: int = 0
    expiries: list[date] = field(default_factory=list)
    atm_iv_30d: float | None = None
    atm_iv_front: float | None = None
    created: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def solve_rate(self) -> float:
        return self.contracts_solved / self.contracts_written if self.contracts_written else 0.0


def get_or_create_ticker(session: Session, symbol: str, active: bool = True) -> Ticker:
    """Fetch the ticker row, inserting it if this is the first time we've seen it."""
    normalised = symbol.strip().upper()
    ticker = session.scalar(select(Ticker).where(Ticker.symbol == normalised))
    if ticker is None:
        ticker = Ticker(symbol=normalised, active=active)
        session.add(ticker)
        session.flush()
    return ticker


def snapshot_ticker(
    session: Session,
    provider: MarketDataProvider,
    symbol: str,
    risk_free_rate: float,
    dividend_yield: float = 0.0,
    max_expiries: int = DEFAULT_MAX_EXPIRIES,
    as_of: datetime | None = None,
) -> SnapshotResult:
    """Capture one ticker's chains for one day. Safe to run more than once a day."""
    as_of = as_of or datetime.now(UTC)
    snapshot_date = as_of.date()
    result = SnapshotResult(ticker=symbol.upper(), snapshot_date=snapshot_date)

    ticker = get_or_create_ticker(session, symbol)
    expiries = provider.get_expiries(symbol)[:max_expiries]
    if not expiries:
        raise ProviderError(f"{symbol}: no listed expiries to snapshot")

    frames: list[pd.DataFrame] = []
    atm_by_maturity: dict[float, float] = {}
    spot: float | None = None

    for expiry in expiries:
        try:
            chain = provider.get_chain(symbol, expiry)
        except ProviderError as exc:
            # One bad expiry must not lose the whole day. Record it, keep going,
            # and let the stored solve rate show the day was partial.
            logger.warning("%s %s: skipping expiry (%s)", symbol, expiry, exc)
            result.errors.append(f"{expiry}: {exc}")
            continue

        frame = build_chain_frame(chain, risk_free_rate, dividend_yield, as_of)
        if frame.empty:
            continue

        frames.append(frame)
        result.expiries.append(expiry)
        spot = chain.spot

        atm = atm_iv_for_expiry(frame)
        if atm is not None:
            atm_by_maturity[float(frame.attrs["time_to_expiry"])] = atm

    if not frames or spot is None:
        raise ProviderError(f"{symbol}: every expiry failed; nothing to snapshot")

    combined = pd.concat(frames, ignore_index=True)
    result.contracts_written = len(combined)
    result.contracts_solved = int(combined["iv"].notna().sum())
    result.atm_iv_30d = constant_maturity_atm_iv(atm_by_maturity)
    result.atm_iv_front = atm_by_maturity[min(atm_by_maturity)] if atm_by_maturity else None

    snapshot_id, result.created = _upsert_snapshot(
        session,
        ticker_id=ticker.id,
        snapshot_date=snapshot_date,
        as_of=as_of,
        provider_name=provider.name,
        spot=spot,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
        result=result,
    )
    _upsert_contracts(session, snapshot_id, combined)

    logger.info(
        "%s %s: %d contracts (%.0f%% solved), 30d ATM IV %s",
        result.ticker,
        snapshot_date,
        result.contracts_written,
        result.solve_rate * 100,
        f"{result.atm_iv_30d:.4f}" if result.atm_iv_30d else "unavailable",
    )
    return result


def _upsert_snapshot(
    session: Session,
    ticker_id: int,
    snapshot_date: date,
    as_of: datetime,
    provider_name: str,
    spot: float,
    risk_free_rate: float,
    dividend_yield: float,
    result: SnapshotResult,
) -> tuple[int, bool]:
    """Insert the snapshot row, or refresh today's if it already exists."""
    existing = session.scalar(
        select(Snapshot.id).where(
            Snapshot.ticker_id == ticker_id, Snapshot.snapshot_date == snapshot_date
        )
    )

    values = {
        "ticker_id": ticker_id,
        "snapshot_date": snapshot_date,
        "as_of": as_of,
        "provider": provider_name,
        "spot": spot,
        "risk_free_rate": risk_free_rate,
        "dividend_yield": dividend_yield,
        "contract_count": result.contracts_written,
        "solved_count": result.contracts_solved,
        "atm_iv_30d": result.atm_iv_30d,
        "atm_iv_front": result.atm_iv_front,
    }

    statement = sqlite_insert(Snapshot).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=["ticker_id", "snapshot_date"],
        set_={k: v for k, v in values.items() if k not in ("ticker_id", "snapshot_date")},
    )
    session.execute(statement)
    session.flush()

    snapshot_id = session.scalar(
        select(Snapshot.id).where(
            Snapshot.ticker_id == ticker_id, Snapshot.snapshot_date == snapshot_date
        )
    )
    assert snapshot_id is not None
    return snapshot_id, existing is None


def _upsert_contracts(session: Session, snapshot_id: int, frame: pd.DataFrame) -> None:
    """Write every contract row, replacing any already stored for this snapshot.

    Chunked because SQLite caps a statement at 999 bound parameters by default
    and a full multi-expiry chain is thousands of rows across ~20 columns.
    """
    rows = [_contract_values(snapshot_id, record) for record in frame.to_dict("records")]
    if not rows:
        return

    updatable = [c for c in rows[0] if c not in ("snapshot_id", "expiry", "strike", "right")]
    chunk_size = max(1, 900 // len(rows[0]))

    for start in range(0, len(rows), chunk_size):
        chunk = rows[start : start + chunk_size]
        statement = sqlite_insert(Contract).values(chunk)
        statement = statement.on_conflict_do_update(
            index_elements=["snapshot_id", "expiry", "strike", "right"],
            set_={column: statement.excluded[column] for column in updatable},
        )
        session.execute(statement)
    session.flush()


def _contract_values(snapshot_id: int, record: Mapping[Any, Any]) -> dict[str, Any]:
    """One frame row as column values, with pandas NA normalised to None.

    `pd.NA` and `NaN` are not values SQLite understands; letting them through
    stores the string 'nan' or raises, depending on the column.
    """

    def clean(key: str) -> object:
        value = record.get(key)
        return None if value is None or pd.isna(value) else value

    expiry = record["expiry"]
    return {
        "snapshot_id": snapshot_id,
        "expiry": expiry.date() if isinstance(expiry, pd.Timestamp) else expiry,
        "strike": float(record["strike"]),
        "right": str(record["right"]),
        "contract_symbol": None,
        "bid": clean("bid"),
        "ask": clean("ask"),
        "last": clean("last"),
        "mid": clean("mid"),
        "volume": clean("volume"),
        "open_interest": clean("open_interest"),
        "price": clean("price"),
        "price_source": clean("price_source"),
        "time_to_expiry": float(record["time_to_expiry"]),
        "moneyness": float(record["moneyness"]),
        "iv": clean("iv"),
        "iv_status": str(record["iv_status"]),
        "delta": clean("delta"),
        "gamma": clean("gamma"),
        "vega": clean("vega"),
        "theta": clean("theta"),
        "rho": clean("rho"),
    }


def snapshot_watchlist(
    session: Session,
    provider: MarketDataProvider,
    risk_free_rate: float,
    dividend_yield: float = 0.0,
    max_expiries: int = DEFAULT_MAX_EXPIRIES,
    as_of: datetime | None = None,
) -> list[SnapshotResult]:
    """Snapshot every active watchlist ticker.

    One ticker failing does not abort the rest -- a rate limit on the third
    symbol should not cost you the history of the other five.
    """
    symbols = list(session.scalars(select(Ticker.symbol).where(Ticker.active.is_(True))))
    results = []
    for symbol in symbols:
        try:
            results.append(
                snapshot_ticker(
                    session,
                    provider,
                    symbol,
                    risk_free_rate,
                    dividend_yield,
                    max_expiries,
                    as_of,
                )
            )
        except ProviderError as exc:
            logger.error("%s: snapshot failed (%s)", symbol, exc)
            failed = SnapshotResult(
                ticker=symbol, snapshot_date=(as_of or datetime.now(UTC)).date()
            )
            failed.errors.append(str(exc))
            results.append(failed)
    return results
