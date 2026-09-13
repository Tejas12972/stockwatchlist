"""Reducing a day's chains to the single number IV rank ranks."""

from __future__ import annotations

import pandas as pd
import pytest

from options_tool.analytics.atm_iv import (
    CONSTANT_MATURITY_DAYS,
    atm_iv_for_expiry,
    constant_maturity_atm_iv,
)
from options_tool.analytics.chain import build_chain_frame
from options_tool.providers.fixture_provider import FixtureProvider


class TestConstantMaturityInterpolation:
    def test_exact_maturity_needs_no_interpolation(self) -> None:
        assert constant_maturity_atm_iv({30 / 365: 0.25}) == pytest.approx(0.25)

    def test_interpolates_in_variance_not_volatility(self) -> None:
        """Total variance is additive in time; volatility is not.

        Interpolating volatility linearly would give 0.25 here. The correct
        variance-weighted answer is higher, and the difference is the whole
        reason this function exists.
        """
        result = constant_maturity_atm_iv({20 / 365: 0.20, 60 / 365: 0.30})
        assert result is not None

        t_lo, t_hi, target = 20 / 365, 60 / 365, 30 / 365
        weight = (target - t_lo) / (t_hi - t_lo)
        variance = 0.20**2 * t_lo + weight * (0.30**2 * t_hi - 0.20**2 * t_lo)
        assert result == pytest.approx((variance / target) ** 0.5)

        naive_linear = 0.20 + weight * (0.30 - 0.20)
        assert result != pytest.approx(naive_linear)
        assert result > naive_linear

    def test_flat_term_structure_returns_the_flat_level(self) -> None:
        assert constant_maturity_atm_iv({10 / 365: 0.3, 90 / 365: 0.3}) == pytest.approx(
            0.3, rel=1e-9
        )

    def test_result_lies_between_the_bracketing_vols(self) -> None:
        result = constant_maturity_atm_iv({7 / 365: 0.18, 90 / 365: 0.42})
        assert result is not None
        assert 0.18 < result < 0.42


class TestRefusalToExtrapolate:
    """None, not a guess, whenever the horizon cannot be bracketed."""

    def test_all_expiries_too_long(self) -> None:
        assert constant_maturity_atm_iv({1.0: 0.30, 2.0: 0.32}) is None

    def test_all_expiries_too_short(self) -> None:
        assert constant_maturity_atm_iv({0.01: 0.30, 0.02: 0.31}) is None

    def test_no_expiries_at_all(self) -> None:
        assert constant_maturity_atm_iv({}) is None

    def test_non_positive_vols_are_discarded(self) -> None:
        assert constant_maturity_atm_iv({20 / 365: 0.0, 60 / 365: 0.0}) is None

    def test_a_single_bracketing_side_is_not_enough(self) -> None:
        assert constant_maturity_atm_iv({20 / 365: 0.25}) is None

    def test_why_it_matters(self) -> None:
        """An invented early value would become the window minimum for a year."""
        assert constant_maturity_atm_iv({0.5: 0.15, 1.0: 0.20}) is None


class TestAtmIVForExpiry:
    @staticmethod
    def synthetic(strikes_and_ivs: dict[float, float], spot: float = 100.0) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "right": "call",
                    "strike": strike,
                    "iv": iv,
                    "spot": spot,
                    "moneyness": spot / strike,
                }
                for strike, iv in strikes_and_ivs.items()
            ]
        )

    def test_interpolates_between_the_strikes_bracketing_spot(self) -> None:
        frame = self.synthetic({95.0: 0.20, 105.0: 0.30})
        # spot 100 sits halfway between 95 and 105
        assert atm_iv_for_expiry(frame) == pytest.approx(0.25)

    def test_exact_strike_at_spot_is_used_directly(self) -> None:
        frame = self.synthetic({95.0: 0.20, 100.0: 0.24, 105.0: 0.30})
        assert atm_iv_for_expiry(frame) == pytest.approx(0.24)

    def test_calls_and_puts_are_averaged(self) -> None:
        frame = pd.DataFrame(
            [
                {"right": "call", "strike": 100.0, "iv": 0.20, "spot": 100.0, "moneyness": 1.0},
                {"right": "put", "strike": 100.0, "iv": 0.30, "spot": 100.0, "moneyness": 1.0},
            ]
        )
        assert atm_iv_for_expiry(frame) == pytest.approx(0.25)

    def test_unsolved_strikes_are_ignored(self) -> None:
        frame = self.synthetic({95.0: 0.20, 105.0: 0.30})
        frame.loc[len(frame)] = {
            "right": "call",
            "strike": 100.0,
            "iv": None,
            "spot": 100.0,
            "moneyness": 1.0,
        }
        assert atm_iv_for_expiry(frame) == pytest.approx(0.25)

    def test_no_solved_strikes_returns_none(self) -> None:
        frame = self.synthetic({95.0: 0.20})
        frame["iv"] = None
        assert atm_iv_for_expiry(frame) is None

    def test_strikes_far_from_spot_are_excluded(self) -> None:
        """Beyond the band the skew is too steep for interpolation to mean much."""
        assert atm_iv_for_expiry(self.synthetic({10.0: 1.5, 500.0: 0.9})) is None

    def test_empty_frame(self) -> None:
        frame = self.synthetic({95.0: 0.2}).iloc[0:0]
        assert atm_iv_for_expiry(frame) is None


class TestAgainstRealCapturedChains:
    def test_every_fixture_expiry_yields_a_plausible_atm_vol(
        self, provider: FixtureProvider
    ) -> None:
        for symbol in ("AAPL", "SPY"):
            for expiry in provider.get_expiries(symbol):
                chain = provider.get_chain(symbol, expiry)
                frame = build_chain_frame(chain, 0.04, 0.0, chain.as_of)
                atm = atm_iv_for_expiry(frame)
                assert atm is not None, f"{symbol} {expiry}"
                assert 0.02 < atm < 2.0, f"{symbol} {expiry} -> {atm}"

    def test_term_structure_is_upward_sloping_for_the_captured_data(
        self, provider: FixtureProvider
    ) -> None:
        """A real property of the capture: longer-dated vol was higher."""
        vols = []
        for expiry in provider.get_expiries("SPY"):
            chain = provider.get_chain("SPY", expiry)
            frame = build_chain_frame(chain, 0.04, 0.0, chain.as_of)
            vols.append(atm_iv_for_expiry(frame))
        assert all(v is not None for v in vols)
        assert vols == sorted(vols)  # type: ignore[type-var]

    def test_thirty_day_point_sits_inside_the_term_structure(
        self, provider: FixtureProvider
    ) -> None:
        by_maturity = {}
        for expiry in provider.get_expiries("AAPL"):
            chain = provider.get_chain("AAPL", expiry)
            frame = build_chain_frame(chain, 0.04, 0.0, chain.as_of)
            atm = atm_iv_for_expiry(frame)
            if atm is not None:
                by_maturity[float(frame.attrs["time_to_expiry"])] = atm

        constant_maturity = constant_maturity_atm_iv(by_maturity)
        assert constant_maturity is not None
        assert min(by_maturity.values()) <= constant_maturity <= max(by_maturity.values())

    def test_the_horizon_is_thirty_days(self) -> None:
        assert CONSTANT_MATURITY_DAYS == 30.0
