"""Snapshot persistence and idempotency.

SPEC.md section 5, M2: *"two runs on the same day produce no duplicate rows."*

Idempotency is checked from both directions — that the writer behaves, and that
the database would stop it even if the writer did not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from options_tool.analytics.chain import market_date
from options_tool.db.models import Contract, Snapshot, Ticker
from options_tool.db.queries import (
    add_to_watchlist,
    contract_count,
    history_depth,
    iv_observations,
    iv_rank_for,
    latest_snapshot,
    list_watchlist,
    remove_from_watchlist,
)
from options_tool.db.snapshot import snapshot_ticker, snapshot_watchlist
from options_tool.providers.base import ProviderError
from options_tool.providers.fixture_provider import FixtureProvider

RATE = 0.04

# The fixture's nearest expiry is 2026-09-14. Simulated history has to sit close
# enough to it that a 30-day constant-maturity point can be bracketed by the
# captured expiries -- otherwise atm_iv_30d is (correctly) null and no IV series
# accumulates at all.
HISTORY_START = datetime(2026, 8, 20, 15, tzinfo=UTC)


def run(session: Session, provider: FixtureProvider, symbol: str = "AAPL", **kwargs: object):  # type: ignore[no-untyped-def]
    return snapshot_ticker(session, provider, symbol, RATE, 0.0, **kwargs)  # type: ignore[arg-type]


class TestIdempotency:
    def test_two_runs_the_same_day_produce_no_duplicate_rows(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        """The headline requirement."""
        first = run(session, provider)
        after_first = contract_count(session)

        second = run(session, provider)
        after_second = contract_count(session)

        assert after_first == after_second
        assert first.contracts_written == second.contracts_written
        assert session.scalar(select(func.count(Snapshot.id))) == 1

    def test_the_second_run_reports_a_refresh_not_a_creation(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        assert run(session, provider).created is True
        assert run(session, provider).created is False

    def test_ten_runs_are_the_same_as_one(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        run(session, provider)
        baseline = contract_count(session)
        for _ in range(9):
            run(session, provider)
        assert contract_count(session) == baseline

    def test_no_duplicate_key_groups_exist_in_sql(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        """Checks the spec's key directly: (ticker, date, expiry, strike, right)."""
        run(session, provider)
        run(session, provider)

        duplicates = session.execute(
            select(
                Snapshot.ticker_id,
                Snapshot.snapshot_date,
                Contract.expiry,
                Contract.strike,
                Contract.right,
                func.count(Contract.id).label("n"),
            )
            .join(Contract, Contract.snapshot_id == Snapshot.id)
            .group_by(
                Snapshot.ticker_id,
                Snapshot.snapshot_date,
                Contract.expiry,
                Contract.strike,
                Contract.right,
            )
            .having(func.count(Contract.id) > 1)
        ).all()
        assert duplicates == []

    def test_the_database_rejects_a_duplicate_even_without_the_upsert(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        """Idempotency is a constraint, not merely careful application code."""
        run(session, provider)
        existing = session.scalars(select(Contract)).first()
        assert existing is not None

        session.add(
            Contract(
                snapshot_id=existing.snapshot_id,
                expiry=existing.expiry,
                strike=existing.strike,
                right=existing.right,
                time_to_expiry=existing.time_to_expiry,
                moneyness=existing.moneyness,
                iv_status="ok",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_two_snapshots_for_one_ticker_on_one_day_are_rejected(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        run(session, provider)
        ticker = session.scalar(select(Ticker).where(Ticker.symbol == "AAPL"))
        assert ticker is not None

        session.add(
            Snapshot(
                ticker_id=ticker.id,
                snapshot_date=market_date(),
                as_of=datetime.now(UTC),
                provider="fixture",
                spot=1.0,
                risk_free_rate=RATE,
                dividend_yield=0.0,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_a_rerun_refreshes_changed_values_in_place(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        """Re-running must update, not merely skip -- a refresh has to refresh."""
        run(session, provider)
        contract = session.scalars(select(Contract).where(Contract.bid.is_not(None))).first()
        assert contract is not None
        original_bid = contract.bid

        contract.bid = -999.0
        session.flush()

        run(session, provider)
        session.expire_all()
        refreshed = session.get(Contract, contract.id)
        assert refreshed is not None
        assert refreshed.bid == pytest.approx(original_bid)


class TestSeparateDays:
    def test_different_days_accumulate(self, session: Session, provider: FixtureProvider) -> None:
        base = HISTORY_START
        for offset in range(3):
            run(session, provider, as_of=base + timedelta(days=offset))

        assert session.scalar(select(func.count(Snapshot.id))) == 3
        days, first, last = history_depth(session, "AAPL")
        assert days == 3
        assert first == market_date(base)
        assert last == market_date(base + timedelta(days=2))

    def test_different_tickers_do_not_collide(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        run(session, provider, "AAPL")
        run(session, provider, "SPY")
        assert session.scalar(select(func.count(Snapshot.id))) == 2
        assert contract_count(session, "AAPL") < contract_count(session)


class TestWhatIsStored:
    def test_our_own_implied_vol_and_greeks_are_persisted(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        run(session, provider)
        solved = session.scalars(select(Contract).where(Contract.iv.is_not(None))).first()
        assert solved is not None
        assert solved.iv is not None and 0 < solved.iv < 5
        assert solved.delta is not None
        assert solved.vega is not None
        assert solved.iv_status == "ok"

    def test_unsolvable_contracts_are_kept_with_a_reason(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        """A strike that cannot be priced is information, not noise to discard."""
        run(session, provider)
        unsolved = session.scalars(select(Contract).where(Contract.iv.is_(None))).all()
        assert unsolved, "the fixture contains stale deep-ITM quotes; some must fail"
        for contract in unsolved:
            assert contract.iv_status not in ("", "ok")
            assert contract.bid is not None or contract.ask is not None or contract.last is not None

    def test_pricing_assumptions_are_stored_with_the_snapshot(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        """Without them a stored IV cannot be interpreted later."""
        run(session, provider)
        snapshot = latest_snapshot(session, "AAPL")
        assert snapshot is not None
        assert snapshot.risk_free_rate == pytest.approx(RATE)
        assert snapshot.dividend_yield == pytest.approx(0.0)
        assert snapshot.provider == "fixture"

    def test_solve_rate_is_recorded(self, session: Session, provider: FixtureProvider) -> None:
        result = run(session, provider)
        snapshot = latest_snapshot(session, "AAPL")
        assert snapshot is not None
        assert snapshot.contract_count == result.contracts_written
        assert snapshot.solved_count == result.contracts_solved
        assert 0.0 < snapshot.solve_rate <= 1.0

    def test_atm_iv_is_computed_and_plausible(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        result = run(session, provider)
        assert result.atm_iv_30d is not None
        assert 0.01 < result.atm_iv_30d < 3.0

    def test_no_nan_reaches_the_database(self, session: Session, provider: FixtureProvider) -> None:
        """pandas NA is not a SQLite value; it must be normalised to NULL."""
        import math

        run(session, provider)
        for contract in session.scalars(select(Contract)).all():
            for field in ("bid", "ask", "last", "mid", "iv", "delta", "gamma", "vega"):
                value = getattr(contract, field)
                assert value is None or not math.isnan(value), f"{field} is NaN"


class TestWatchlistIntegration:
    def test_snapshot_watchlist_covers_active_symbols_only(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        add_to_watchlist(session, "AAPL")
        add_to_watchlist(session, "SPY")
        remove_from_watchlist(session, "SPY")

        results = snapshot_watchlist(session, provider, RATE, 0.0)
        assert [r.ticker for r in results] == ["AAPL"]

    def test_one_failing_ticker_does_not_abort_the_run(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        """A rate limit on one symbol must not cost the history of the others."""
        add_to_watchlist(session, "AAPL")
        add_to_watchlist(session, "NOSUCHTICKER")
        add_to_watchlist(session, "SPY")

        results = snapshot_watchlist(session, provider, RATE, 0.0)
        by_symbol = {r.ticker: r for r in results}

        assert by_symbol["AAPL"].contracts_written > 0
        assert by_symbol["SPY"].contracts_written > 0
        assert by_symbol["NOSUCHTICKER"].errors
        assert by_symbol["NOSUCHTICKER"].contracts_written == 0

    def test_removing_a_symbol_keeps_its_history(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        """A soft delete: months of irreplaceable observations must survive."""
        add_to_watchlist(session, "AAPL")
        run(session, provider)
        before = contract_count(session, "AAPL")

        remove_from_watchlist(session, "AAPL")

        assert contract_count(session, "AAPL") == before
        assert [t.symbol for t in list_watchlist(session)] == []
        assert [t.symbol for t in list_watchlist(session, include_inactive=True)] == ["AAPL"]

    def test_re_adding_reactivates_rather_than_duplicating(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        add_to_watchlist(session, "AAPL")
        remove_from_watchlist(session, "AAPL")
        add_to_watchlist(session, "AAPL")

        assert session.scalar(select(func.count(Ticker.id))) == 1
        assert [t.symbol for t in list_watchlist(session)] == ["AAPL"]

    def test_symbols_are_normalised(self, session: Session) -> None:
        add_to_watchlist(session, "  aapl  ")
        assert [t.symbol for t in list_watchlist(session)] == ["AAPL"]

    def test_removing_an_untracked_symbol_reports_false(self, session: Session) -> None:
        assert remove_from_watchlist(session, "ZZZZ") is False


class TestIVHistoryAccumulation:
    def test_rank_stays_unavailable_until_the_window_is_met(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        """The end-to-end version of the degraded state."""
        base = HISTORY_START
        for offset in range(5):
            run(session, provider, as_of=base + timedelta(days=offset))

        result = iv_rank_for(session, "AAPL", min_history_days=20)
        assert result.rank is None
        assert result.reason == "insufficient history (5/20 days)"

    def test_rank_becomes_available_once_enough_days_exist(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        base = HISTORY_START
        for offset in range(20):
            run(session, provider, as_of=base + timedelta(days=offset))

        observations = iv_observations(session, "AAPL")
        assert len(observations) == 20
        # Every day replays the same fixture, so the series is flat by
        # construction: the honest answer is "undefined", not a fabricated 50.
        result = iv_rank_for(session, "AAPL", min_history_days=20)
        assert result.days_available == 20
        assert result.status.value in ("ok", "degenerate_range")

    def test_observations_come_back_oldest_first(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        base = HISTORY_START
        for offset in range(4):
            run(session, provider, as_of=base + timedelta(days=offset))

        dates = [o.observed_on for o in iv_observations(session, "AAPL")]
        assert dates == sorted(dates)

    def test_unknown_symbol_has_no_history(self, session: Session) -> None:
        assert iv_observations(session, "ZZZZ") == []
        assert history_depth(session, "ZZZZ") == (0, None, None)


class TestFailureModes:
    def test_unknown_ticker_raises_a_provider_error(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        with pytest.raises(ProviderError):
            run(session, provider, "NOSUCHTICKER")

    def test_nothing_is_written_when_the_snapshot_fails(
        self, session: Session, provider: FixtureProvider
    ) -> None:
        """A half-written day would look complete to IV rank."""
        with pytest.raises(ProviderError):
            run(session, provider, "NOSUCHTICKER")
        assert contract_count(session) == 0
