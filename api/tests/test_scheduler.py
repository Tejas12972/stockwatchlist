"""The in-process daily snapshot scheduler.

The schedule arithmetic is tested directly; the run loop is exercised through
the catch-up path, which is the part that protects against a permanent hole in
the IV history.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import Engine

from options_tool.config import Settings
from options_tool.db.queries import add_to_watchlist
from options_tool.db.snapshot import snapshot_ticker
from options_tool.providers.fixture_provider import FixtureProvider
from options_tool.scheduler import SnapshotScheduler, next_run_at, parse_schedule_time


class TestParseScheduleTime:
    def test_parses_a_normal_time(self) -> None:
        assert parse_schedule_time("21:15") == time(21, 15, tzinfo=UTC)

    def test_tolerates_surrounding_whitespace(self) -> None:
        assert parse_schedule_time("  09:05 ") == time(9, 5, tzinfo=UTC)

    @pytest.mark.parametrize("bad", ["", "2115", "25:00", "12:75", "noon", "12:xx"])
    def test_rejects_malformed_times_at_startup(self, bad: str) -> None:
        """A typo must stop the process, not silently skip the snapshot for weeks."""
        with pytest.raises(ValueError, match="OPTIONS_SNAPSHOT_AT"):
            parse_schedule_time(bad)


class TestNextRunAt:
    AT = time(21, 15, tzinfo=UTC)

    def test_later_today_when_the_time_has_not_passed(self) -> None:
        now = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)  # Wednesday
        assert next_run_at(now, self.AT) == datetime(2026, 9, 16, 21, 15, tzinfo=UTC)

    def test_tomorrow_when_it_has(self) -> None:
        now = datetime(2026, 9, 16, 22, 0, tzinfo=UTC)
        assert next_run_at(now, self.AT) == datetime(2026, 9, 17, 21, 15, tzinfo=UTC)

    def test_exactly_at_the_scheduled_time_moves_to_the_next_day(self) -> None:
        """Strictly after, so a fire at 21:15:00 cannot immediately re-fire."""
        now = datetime(2026, 9, 16, 21, 15, tzinfo=UTC)
        assert next_run_at(now, self.AT).day == 17

    def test_friday_evening_skips_to_monday(self) -> None:
        """US markets are shut; a weekend run would store Friday's stale chain."""
        friday = datetime(2026, 9, 18, 22, 0, tzinfo=UTC)
        assert next_run_at(friday, self.AT) == datetime(2026, 9, 21, 21, 15, tzinfo=UTC)

    def test_saturday_skips_to_monday(self) -> None:
        saturday = datetime(2026, 9, 19, 10, 0, tzinfo=UTC)
        assert next_run_at(saturday, self.AT).weekday() == 0

    def test_always_returns_a_weekday_in_the_future(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        for offset in range(0, 400, 7):
            now = start + timedelta(days=offset, hours=offset % 24)
            result = next_run_at(now, self.AT)
            assert result > now
            assert result.weekday() < 5


class TestCatchUp:
    """The equivalent of systemd's Persistent=true.

    A day missed to a restart cannot be recovered later: historical implied
    volatility is not purchasable from a free source.
    """

    @staticmethod
    def scheduler(settings: Settings, at: str = "00:01") -> SnapshotScheduler:
        object.__setattr__(settings, "options_snapshot_at", at)
        return SnapshotScheduler(settings)

    def test_catches_up_when_today_has_no_snapshot(
        self, settings: Settings, engine: Engine, session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_to_watchlist(session, "AAPL")
        session.commit()

        scheduler = self.scheduler(settings)
        # 00:01 UTC has certainly passed by any later hour of the day.
        monkeypatch.setattr(
            "options_tool.scheduler.datetime",
            _FrozenDatetime(datetime(2026, 9, 16, 12, 0, tzinfo=UTC)),
        )
        assert scheduler._needs_catch_up() is True

    def test_no_catch_up_before_the_scheduled_time(
        self, settings: Settings, engine: Engine, session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_to_watchlist(session, "AAPL")
        session.commit()

        scheduler = self.scheduler(settings, at="23:59")
        monkeypatch.setattr(
            "options_tool.scheduler.datetime",
            _FrozenDatetime(datetime(2026, 9, 16, 12, 0, tzinfo=UTC)),
        )
        assert scheduler._needs_catch_up() is False

    def test_no_catch_up_at_the_weekend(
        self, settings: Settings, engine: Engine, session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_to_watchlist(session, "AAPL")
        session.commit()

        scheduler = self.scheduler(settings)
        monkeypatch.setattr(
            "options_tool.scheduler.datetime",
            _FrozenDatetime(datetime(2026, 9, 19, 12, 0, tzinfo=UTC)),  # Saturday
        )
        assert scheduler._needs_catch_up() is False

    def test_no_catch_up_with_an_empty_watchlist(
        self, settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scheduler = self.scheduler(settings)
        monkeypatch.setattr(
            "options_tool.scheduler.datetime",
            _FrozenDatetime(datetime(2026, 9, 16, 12, 0, tzinfo=UTC)),
        )
        assert scheduler._needs_catch_up() is False

    def test_no_catch_up_once_today_is_already_stored(
        self,
        settings: Settings,
        engine: Engine,
        session,
        provider: FixtureProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        add_to_watchlist(session, "AAPL")
        snapshot_ticker(session, provider, "AAPL", 0.04, 0.0, as_of=datetime.now(UTC))
        session.commit()

        scheduler = self.scheduler(settings)
        assert scheduler._needs_catch_up() is False


class TestSchedulerLifecycle:
    def test_start_is_idempotent(self, settings: Settings) -> None:
        """Guards against two loops both firing the snapshot."""
        scheduler = SnapshotScheduler(settings)
        assert scheduler._task is None

    def test_construction_validates_the_time(self, settings: Settings) -> None:
        object.__setattr__(settings, "options_snapshot_at", "nonsense")
        with pytest.raises(ValueError, match="OPTIONS_SNAPSHOT_AT"):
            SnapshotScheduler(settings)


class _FrozenDatetime:
    """Stands in for `datetime` so `datetime.now(UTC)` is deterministic."""

    def __init__(self, when: datetime) -> None:
        self._when = when

    def now(self, tz: object = None) -> datetime:
        return self._when

    def __getattr__(self, name: str) -> object:
        import datetime as module

        return getattr(module.datetime, name)


class TestTheRunLoop:
    """Exercised with `asyncio.run` rather than pytest-asyncio, to avoid a
    dependency for three tests."""

    def test_start_then_stop_cleanly(self, settings: Settings, engine: Engine) -> None:
        import asyncio

        async def exercise() -> None:
            scheduler = SnapshotScheduler(settings)
            scheduler.start()
            assert scheduler._task is not None
            # Let the loop reach its first await.
            await asyncio.sleep(0.05)
            await scheduler.stop()
            assert scheduler._task is None

        asyncio.run(exercise())

    def test_start_twice_creates_only_one_task(self, settings: Settings, engine: Engine) -> None:
        """Two loops would fire the snapshot twice a day. Idempotency makes that
        harmless, but it would double the vendor load for no reason."""
        import asyncio

        async def exercise() -> None:
            scheduler = SnapshotScheduler(settings)
            scheduler.start()
            first = scheduler._task
            scheduler.start()
            assert scheduler._task is first
            await scheduler.stop()

        asyncio.run(exercise())

    def test_stop_before_start_is_a_no_op(self, settings: Settings) -> None:
        import asyncio

        asyncio.run(SnapshotScheduler(settings).stop())

    def test_a_failing_snapshot_does_not_kill_the_loop(
        self, settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tomorrow still needs to run, and the catch-up path cannot revive a
        dead task."""
        import asyncio

        calls: list[int] = []

        def boom(self: SnapshotScheduler) -> None:
            calls.append(1)
            raise RuntimeError("vendor exploded")

        monkeypatch.setattr(SnapshotScheduler, "_snapshot_blocking", boom)
        monkeypatch.setattr(SnapshotScheduler, "_needs_catch_up", lambda self: True)

        async def exercise() -> None:
            scheduler = SnapshotScheduler(settings)
            scheduler.start()
            await asyncio.sleep(0.1)
            # The catch-up raised, and the task is still alive and waiting.
            assert calls
            assert scheduler._task is not None
            assert not scheduler._task.done()
            await scheduler.stop()

        asyncio.run(exercise())

    def test_snapshot_writes_through_to_the_database(
        self,
        settings: Settings,
        engine: Engine,
        session,
        provider: FixtureProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The scheduler's own path, not just `snapshot_watchlist` directly."""
        from options_tool.db.queries import contract_count

        add_to_watchlist(session, "AAPL")
        session.commit()

        monkeypatch.setattr("options_tool.scheduler.get_provider", lambda name: provider)
        SnapshotScheduler(settings)._snapshot_blocking()

        assert contract_count(session) > 0

    def test_snapshot_is_idempotent_through_the_scheduler(
        self,
        settings: Settings,
        engine: Engine,
        session,
        provider: FixtureProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from options_tool.db.queries import contract_count

        add_to_watchlist(session, "AAPL")
        session.commit()
        monkeypatch.setattr("options_tool.scheduler.get_provider", lambda name: provider)

        scheduler = SnapshotScheduler(settings)
        scheduler._snapshot_blocking()
        after_first = contract_count(session)
        scheduler._snapshot_blocking()

        assert contract_count(session) == after_first
