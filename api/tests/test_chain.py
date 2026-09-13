"""Chain normalisation: the tidy frame every other layer consumes."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from options_tool.analytics.chain import (
    CHAIN_COLUMNS,
    build_chain_frame,
    market_date,
    summarise_chain,
    time_to_expiry,
)
from options_tool.providers.base import OptionChain, OptionQuote
from options_tool.providers.fixture_provider import FixtureProvider

RATE = 0.04


@pytest.fixture
def frame(provider: FixtureProvider) -> pd.DataFrame:
    chain = provider.get_chain("AAPL", date(2027, 1, 15))
    return build_chain_frame(chain, RATE, 0.0, chain.as_of)


class TestTimeToExpiry:
    def test_counts_to_the_afternoon_close_not_midnight(self) -> None:
        """Options trade until 16:00 New York, not 00:00 UTC."""
        as_of = datetime(2026, 9, 14, 12, tzinfo=UTC)  # 08:00 New York
        years = time_to_expiry(date(2026, 9, 14), as_of)
        assert years == pytest.approx(8 / 24 / 365, rel=1e-6)

    def test_never_negative(self) -> None:
        past = datetime(2027, 1, 1, tzinfo=UTC)
        assert time_to_expiry(date(2026, 1, 1), past) == 0.0

    def test_naive_datetimes_are_treated_as_utc(self) -> None:
        aware = datetime(2026, 9, 1, 12, tzinfo=UTC)
        naive = datetime(2026, 9, 1, 12)
        assert time_to_expiry(date(2026, 12, 18), naive) == pytest.approx(
            time_to_expiry(date(2026, 12, 18), aware)
        )

    def test_a_weekend_still_decays(self) -> None:
        """Calendar time, not trading time -- stated in the README limitations."""
        friday = datetime(2026, 9, 11, 20, tzinfo=UTC)
        monday = datetime(2026, 9, 14, 20, tzinfo=UTC)
        assert time_to_expiry(date(2026, 12, 18), friday) > time_to_expiry(
            date(2026, 12, 18), monday
        )


class TestMarketDate:
    @pytest.mark.parametrize(
        ("utc_hour", "expected_day"),
        [(12, 13), (19, 13), (20, 13), (23, 13), (3, 12)],
    )
    def test_uses_new_york_not_utc(self, utc_hour: int, expected_day: int) -> None:
        """After 20:00 UTC the UTC date has rolled but the trading day has not."""
        moment = datetime(2026, 9, 13, utc_hour, tzinfo=UTC)
        assert market_date(moment) == date(2026, 9, expected_day)

    def test_a_late_afternoon_and_an_evening_run_share_a_date(self) -> None:
        """The idempotency case: 15:00 and 20:30 Eastern are one trading day."""
        afternoon = datetime(2026, 9, 13, 19, tzinfo=UTC)  # 15:00 ET
        evening = datetime(2026, 9, 14, 0, 30, tzinfo=UTC)  # 20:30 ET
        assert market_date(afternoon) == market_date(evening)


class TestFrameShape:
    def test_columns_and_order_match_the_contract(self, frame: pd.DataFrame) -> None:
        assert list(frame.columns) == list(CHAIN_COLUMNS)

    def test_dtypes_are_enforced(self, frame: pd.DataFrame) -> None:
        for column, dtype in CHAIN_COLUMNS.items():
            assert str(frame[column].dtype) == dtype, column

    def test_nullable_integers_keep_missing_volume_missing(self, frame: pd.DataFrame) -> None:
        """0 volume and "no reported volume" are different observations."""
        assert str(frame["volume"].dtype) == "Int64"

    def test_one_row_per_contract(self, provider: FixtureProvider) -> None:
        chain = provider.get_chain("AAPL", date(2027, 1, 15))
        assert len(build_chain_frame(chain, RATE, 0.0, chain.as_of)) == len(chain.quotes)

    def test_sorted_by_right_then_strike(self, frame: pd.DataFrame) -> None:
        for _, side in frame.groupby("right", observed=True):
            assert side["strike"].is_monotonic_increasing

    def test_chain_level_facts_live_in_attrs_not_columns(self, frame: pd.DataFrame) -> None:
        assert frame.attrs["ticker"] == "AAPL"
        assert frame.attrs["expiry"] == date(2027, 1, 15)
        assert frame.attrs["risk_free_rate"] == pytest.approx(RATE)
        assert frame.attrs["time_to_expiry"] > 0


class TestComputedValues:
    def test_greeks_are_present_wherever_iv_solved(self, frame: pd.DataFrame) -> None:
        solved = frame[frame["iv"].notna()]
        assert not solved.empty
        for column in ("delta", "gamma", "vega", "theta", "rho"):
            assert solved[column].notna().all(), column

    def test_greeks_are_absent_wherever_iv_did_not(self, frame: pd.DataFrame) -> None:
        """No fabricated delta for a contract with no tradeable market."""
        unsolved = frame[frame["iv"].isna()]
        if unsolved.empty:
            pytest.skip("this expiry solved completely")
        for column in ("delta", "gamma", "vega", "theta", "rho"):
            assert unsolved[column].isna().all(), column

    def test_unsolved_rows_are_kept_and_labelled(self, frame: pd.DataFrame) -> None:
        unsolved = frame[frame["iv"].isna()]
        assert (unsolved["iv_status"] != "ok").all()
        assert (unsolved["iv_status"] != "").all()

    def test_call_deltas_are_positive_and_put_deltas_negative(self, frame: pd.DataFrame) -> None:
        solved = frame[frame["iv"].notna()]
        assert (solved.loc[solved["right"] == "call", "delta"] > 0).all()
        assert (solved.loc[solved["right"] == "put", "delta"] < 0).all()

    def test_gamma_and_vega_are_never_negative(self, frame: pd.DataFrame) -> None:
        solved = frame[frame["iv"].notna()]
        assert (solved["gamma"] >= 0).all()
        assert (solved["vega"] >= 0).all()

    def test_moneyness_and_in_the_money_agree(self, frame: pd.DataFrame) -> None:
        calls = frame[frame["right"] == "call"]
        assert (calls["in_the_money"] == (calls["moneyness"] > 1.0)).all()

    def test_call_delta_is_strictly_decreasing_at_constant_volatility(self) -> None:
        """The pure maths, isolated from the market.

        Delta is monotone in strike only when every strike shares a volatility.
        Priced off one sigma, the ordering must be exact -- any inversion here is
        a bug rather than microstructure.
        """
        from options_tool.analytics.black_scholes import Right, price

        quotes = tuple(
            OptionQuote(
                ticker="TEST",
                expiry=date(2027, 1, 15),
                strike=float(strike),
                right="call",
                bid=(p := price(100.0, float(strike), 0.34, RATE, 0.25, 0.0, Right.CALL)) * 0.99,
                ask=p * 1.01,
            )
            for strike in range(75, 140, 5)
        )
        chain = OptionChain(
            ticker="TEST",
            expiry=date(2027, 1, 15),
            spot=100.0,
            as_of=datetime(2026, 9, 13, 15, tzinfo=UTC),
            quotes=quotes,
        )
        frame = build_chain_frame(chain, RATE, 0.0, chain.as_of).sort_values("strike")
        solved = frame[frame["delta"].notna()]
        assert len(solved) == len(frame), "every synthetic strike should solve"
        assert solved["delta"].is_monotonic_decreasing

    def test_real_chain_deltas_decrease_across_the_liquid_strikes(
        self, frame: pd.DataFrame
    ) -> None:
        """Strict monotonicity only holds where the market is real.

        Every strike on a live chain carries its own smile volatility, so delta
        is not strictly ordered end to end. The inversions are confined to the
        extremes -- deep in-the-money strikes quoted three dollars wide, and far
        out-of-the-money wings with a zero bid and an implied vol that is mostly
        an artefact of the ask. Within the liquid band the ordering is exact.
        """
        calls = frame[(frame["right"] == "call") & frame["delta"].notna()].sort_values("strike")
        assert calls["delta"].iloc[0] > 0.9, "deepest ITM call should be near delta 1"
        assert calls["delta"].iloc[-1] < 0.1, "furthest OTM call should be near delta 0"

        liquid = calls[((calls["moneyness"] - 1.0).abs() <= 0.2) & (calls["price_source"] == "mid")]
        assert len(liquid) > 10, "the fixture should contain a liquid band"
        assert liquid["delta"].is_monotonic_decreasing

    def test_real_chain_shows_a_volatility_smile(self, frame: pd.DataFrame) -> None:
        """Sanity check against real captured data: equity skew slopes down."""
        calls = frame[(frame["right"] == "call") & frame["iv"].notna()]
        spot = float(frame.attrs["spot"])
        near = calls[(calls["moneyness"] - 1.0).abs() < 0.1].sort_values("strike")
        assert len(near) > 4
        assert near["iv"].iloc[0] > near["iv"].iloc[-1], "expected downward equity skew"
        assert spot > 0


class TestPriceSelection:
    @staticmethod
    def one_quote(**overrides: object) -> pd.DataFrame:
        defaults = {"bid": 4.0, "ask": 4.2, "last": 4.1}
        defaults.update(overrides)
        quote = OptionQuote(
            ticker="TEST",
            expiry=date(2027, 1, 15),
            strike=100.0,
            right="call",
            **defaults,  # type: ignore[arg-type]
        )
        chain = OptionChain(
            ticker="TEST",
            expiry=date(2027, 1, 15),
            spot=100.0,
            as_of=datetime(2026, 9, 13, 15, tzinfo=UTC),
            quotes=(quote,),
        )
        return build_chain_frame(chain, RATE, 0.0, chain.as_of)

    def test_two_sided_book_uses_mid(self) -> None:
        row = self.one_quote().iloc[0]
        assert row["price_source"] == "mid"
        assert row["price"] == pytest.approx(4.1)

    def test_one_sided_book_falls_back_to_last_and_says_so(self) -> None:
        row = self.one_quote(bid=0.0).iloc[0]
        assert row["price_source"] == "last"
        assert row["price"] == pytest.approx(4.1)

    def test_crossed_book_falls_back_to_last(self) -> None:
        row = self.one_quote(bid=5.0, ask=3.0).iloc[0]
        assert row["price_source"] == "last"

    def test_no_quote_at_all_yields_no_iv_and_a_reason(self) -> None:
        row = self.one_quote(bid=None, ask=None, last=None).iloc[0]
        assert pd.isna(row["iv"])
        assert row["iv_status"] == "no_quote"
        assert pd.isna(row["price_source"])

    def test_spread_is_none_for_a_one_sided_book(self) -> None:
        assert pd.isna(self.one_quote(bid=None).iloc[0]["spread"])
        assert self.one_quote().iloc[0]["spread"] == pytest.approx(0.2)


class TestSummary:
    def test_counts_add_up(self, frame: pd.DataFrame) -> None:
        stats = summarise_chain(frame)
        assert stats.contracts == len(frame)
        assert stats.solved == int(frame["iv"].notna().sum())
        assert stats.solve_rate == pytest.approx(stats.solved / stats.contracts)

    def test_unsolved_reasons_are_tallied(self, frame: pd.DataFrame) -> None:
        stats = summarise_chain(frame)
        assert sum(stats.unsolved_reasons.values()) == stats.contracts - stats.solved

    def test_atm_iv_is_plausible(self, frame: pd.DataFrame) -> None:
        stats = summarise_chain(frame)
        assert stats.atm_iv is not None
        assert 0.05 < stats.atm_iv < 2.0

    def test_empty_chain_summarises_without_dividing_by_zero(self) -> None:
        chain = OptionChain(
            ticker="TEST",
            expiry=date(2027, 1, 15),
            spot=100.0,
            as_of=datetime(2026, 9, 13, tzinfo=UTC),
            quotes=(),
        )
        stats = summarise_chain(build_chain_frame(chain, RATE))
        assert stats.contracts == 0
        assert stats.solve_rate == 0.0
        assert stats.atm_iv is None
