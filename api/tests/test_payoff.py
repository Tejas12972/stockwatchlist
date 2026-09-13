"""Multi-leg payoff.

Three layers of check:

1. **hand-computed values** for each standard structure -- a spread's maximum
   profit is width minus debit, and that is arithmetic a reader can verify
2. **the golden file**, shared with the TypeScript suite so the client-side
   preview cannot drift from the server engine
3. **structural invariants** -- the extremes and breakevens are derived
   analytically, so they must not depend on the plotting grid at all
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from options_tool.analytics.payoff import (
    OPTION_MULTIPLIER,
    Leg,
    LegKind,
    build_payoff_curve,
    payoff_at_expiry,
    value_today,
)

GOLDEN = Path(__file__).parent / "fixtures" / "payoff_golden.json"

SPOT, RATE, T, Q = 100.0, 0.05, 0.25, 0.0


def call(qty: int, premium: float, strike: float, vol: float | None = 0.25) -> Leg:
    return Leg(LegKind.CALL, qty, premium, strike, vol)


def put(qty: int, premium: float, strike: float, vol: float | None = 0.25) -> Leg:
    return Leg(LegKind.PUT, qty, premium, strike, vol)


def stock(qty: int, premium: float) -> Leg:
    return Leg(LegKind.STOCK, qty, premium)


def curve_for(legs: list[Leg], **kwargs: Any):  # type: ignore[no-untyped-def]
    return build_payoff_curve(legs, SPOT, T, RATE, Q, **kwargs)


class TestLegValidation:
    def test_zero_quantity_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-zero"):
            call(0, 5.0, 100.0)

    def test_negative_premium_rejected_with_a_useful_message(self) -> None:
        """A short leg is expressed by a negative quantity, not a negative premium."""
        with pytest.raises(ValueError, match="negative quantity"):
            call(1, -5.0, 100.0)

    def test_option_without_a_strike_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive strike"):
            Leg(LegKind.CALL, 1, 5.0, None)

    def test_stock_with_a_strike_rejected(self) -> None:
        with pytest.raises(ValueError, match="no strike"):
            Leg(LegKind.STOCK, 100, 100.0, 95.0)

    def test_multiplier(self) -> None:
        assert call(1, 5.0, 100.0).multiplier == OPTION_MULTIPLIER
        assert stock(100, 100.0).multiplier == 1

    def test_cost_sign_follows_quantity(self) -> None:
        assert call(1, 5.0, 100.0).cost == pytest.approx(500.0)
        assert call(-1, 5.0, 100.0).cost == pytest.approx(-500.0)


class TestHandComputedStructures:
    """Each expected value is arithmetic a reader can check without running code."""

    def test_long_call(self) -> None:
        c = curve_for([call(1, 5.0, 100.0)])
        assert c.net_cost == pytest.approx(500.0)
        assert c.max_loss == pytest.approx(-500.0)
        assert c.max_profit is None, "a long call's upside is genuinely unbounded"
        assert c.breakevens == pytest.approx([105.0])

    def test_short_call_has_unlimited_loss(self) -> None:
        c = curve_for([call(-1, 5.0, 100.0)])
        assert c.max_profit == pytest.approx(500.0)
        assert c.max_loss is None
        assert c.unlimited_loss

    def test_long_put_profit_is_large_but_finite(self) -> None:
        """The case a naive implementation calls unlimited: the stock stops at 0."""
        c = curve_for([put(1, 4.0, 100.0)])
        assert c.max_profit == pytest.approx(100.0 * OPTION_MULTIPLIER - 400.0)
        assert not c.unlimited_profit
        assert c.max_loss == pytest.approx(-400.0)
        assert c.breakevens == pytest.approx([96.0])

    def test_bull_call_spread(self) -> None:
        """Max profit = width - net debit."""
        c = curve_for([call(1, 5.0, 100.0), call(-1, 1.5, 110.0, 0.23)])
        debit = 350.0
        assert c.net_cost == pytest.approx(debit)
        assert c.max_profit == pytest.approx(10.0 * OPTION_MULTIPLIER - debit)
        assert c.max_loss == pytest.approx(-debit)
        assert c.breakevens == pytest.approx([103.5])

    def test_iron_condor(self) -> None:
        """Max loss = wing width - net credit, on either side."""
        c = curve_for(
            [
                put(1, 0.50, 90.0, 0.30),
                put(-1, 1.50, 95.0, 0.27),
                call(-1, 1.60, 105.0, 0.24),
                call(1, 0.60, 110.0, 0.23),
            ]
        )
        credit = 200.0
        assert c.net_cost == pytest.approx(-credit)
        assert c.max_profit == pytest.approx(credit)
        assert c.max_loss == pytest.approx(-(5.0 * OPTION_MULTIPLIER - credit))
        assert c.breakevens == pytest.approx([93.0, 107.0])

    def test_covered_call_loss_is_bounded(self) -> None:
        """P/L falls as the stock falls, but the stock bottoms out at zero."""
        c = curve_for([stock(100, 100.0), call(-1, 3.0, 105.0, 0.24)])
        assert c.max_loss == pytest.approx(-(100 * 100.0) + 300.0)
        assert not c.unlimited_loss
        assert c.max_profit == pytest.approx(5.0 * OPTION_MULTIPLIER + 300.0)

    def test_call_ratio_spread_has_two_breakevens_and_unlimited_loss(self) -> None:
        """Opened for a debit, yet loses without limit -- the shape that surprises."""
        c = curve_for([call(1, 5.0, 100.0), call(-2, 1.5, 110.0, 0.23)])
        assert c.net_cost == pytest.approx(200.0)
        assert c.max_profit == pytest.approx(800.0)
        assert c.max_loss is None
        assert c.breakevens == pytest.approx([102.0, 118.0])

    def test_collar(self) -> None:
        c = curve_for([stock(100, 100.0), put(1, 3.0, 95.0, 0.28), call(-1, 2.5, 110.0, 0.23)])
        assert c.max_profit == pytest.approx(950.0)
        assert c.max_loss == pytest.approx(-550.0)
        assert not c.unlimited_profit and not c.unlimited_loss


class TestGoldenFile:
    """Shared with vitest. Drift between the two implementations fails here."""

    @staticmethod
    def load() -> dict[str, Any]:
        with GOLDEN.open() as handle:
            return json.load(handle)  # type: ignore[no-any-return]

    def test_golden_file_exists_and_covers_the_structures(self) -> None:
        data = self.load()
        names = {case["name"] for case in data["cases"]}
        assert {"long_call", "iron_condor", "covered_call", "call_ratio_spread"} <= names
        assert data["parameters"]["option_multiplier"] == OPTION_MULTIPLIER

    def test_every_case_reproduces_exactly(self) -> None:
        data = self.load()
        params = data["parameters"]

        for case in data["cases"]:
            legs = [
                Leg(
                    LegKind(item["kind"]),
                    item["quantity"],
                    item["premium"],
                    item.get("strike"),
                    item.get("volatility"),
                )
                for item in case["legs"]
            ]
            curve = build_payoff_curve(
                legs,
                params["spot"],
                params["time_to_expiry"],
                params["risk_free_rate"],
                params["dividend_yield"],
                tuple(params["price_range"]),  # type: ignore[arg-type]
                params["points"],
            )
            expected = case["expected"]
            name = case["name"]

            assert curve.net_cost == pytest.approx(expected["net_cost"]), name
            assert curve.breakevens == pytest.approx(expected["breakevens"]), name
            assert curve.max_profit == pytest.approx(expected["max_profit"]), name
            assert curve.max_loss == pytest.approx(expected["max_loss"]), name
            assert curve.unlimited_profit == expected["unlimited_profit"], name
            assert curve.unlimited_loss == expected["unlimited_loss"], name
            assert curve.pnl_at_expiry == pytest.approx(expected["pnl_at_expiry"]), name


class TestTodayCurve:
    def test_today_requires_a_volatility_for_every_option_leg(self) -> None:
        c = curve_for([call(1, 5.0, 100.0, None)])
        assert c.pnl_today is None
        assert c.today_unavailable_reason is not None
        assert "implied volatility" in c.today_unavailable_reason

    def test_missing_vol_does_not_suppress_the_expiry_curve(self) -> None:
        """The expiry curve needs no volatility, so it is still exact."""
        c = curve_for([call(1, 5.0, 100.0, None)])
        assert c.pnl_at_expiry
        assert c.max_loss == pytest.approx(-500.0)

    def test_stock_only_position_needs_no_volatility(self) -> None:
        c = curve_for([stock(100, 100.0)])
        assert c.pnl_today is not None

    def test_today_exceeds_expiry_for_a_long_option_before_expiry(self) -> None:
        """Time value is still there, so a long option is worth more than intrinsic."""
        legs = [call(1, 5.0, 100.0, 0.25)]
        at_spot_expiry = payoff_at_expiry(legs, 100.0)
        at_spot_today = value_today(legs, 100.0, T, RATE, Q)
        assert at_spot_today is not None
        assert at_spot_today > at_spot_expiry

    def test_today_converges_to_expiry_as_time_runs_out(self) -> None:
        """The gap must shrink monotonically toward zero as T does.

        Not asserted at a single small T: an at-the-money option's time value
        decays as sqrt(T), not T, so at T=1e-9 a contract still legitimately
        holds ~3 cents. Convergence is the property worth pinning down; an
        arbitrary tolerance at one T would only encode that quirk.
        """
        legs = [call(1, 5.0, 100.0, 0.25), call(-1, 1.5, 110.0, 0.23)]

        for underlying in (85.0, 100.0, 115.0):
            expiry = payoff_at_expiry(legs, underlying)
            gaps = []
            for years in (1e-2, 1e-4, 1e-6, 1e-8):
                today = value_today(legs, underlying, years, RATE, Q)
                assert today is not None
                gaps.append(abs(today - expiry))

            assert gaps == sorted(gaps, reverse=True), f"not converging at S={underlying}"

            # The remaining gap is time value, bounded by the standard
            # at-the-money approximation 0.4 * sigma * S * sqrt(T) per share.
            # At T=1e-8 that is ~$0.10 per contract, and the worst case here
            # lands within a few percent of it.
            bound = 0.4 * 0.25 * 100.0 * math.sqrt(1e-8) * OPTION_MULTIPLIER
            assert gaps[-1] <= bound * 1.1, f"{gaps[-1]:.4f} exceeds the sqrt(T) bound {bound:.4f}"

    def test_at_expiry_today_equals_the_expiry_payoff_exactly(self) -> None:
        legs = [call(1, 5.0, 100.0, 0.25), call(-1, 1.5, 110.0, 0.23)]
        for underlying in (85.0, 100.0, 105.0, 115.0):
            assert value_today(legs, underlying, 0.0, RATE, Q) == pytest.approx(
                payoff_at_expiry(legs, underlying), abs=1e-9
            )

    def test_at_the_money_time_value_decays_as_the_square_root_of_time(self) -> None:
        """Quartering the time left should halve an ATM option's time value."""
        legs = [call(1, 0.0, 100.0, 0.25)]
        long_dated = value_today(legs, 100.0, 4e-4, RATE, Q)
        short_dated = value_today(legs, 100.0, 1e-4, RATE, Q)
        assert long_dated is not None and short_dated is not None
        assert long_dated / short_dated == pytest.approx(2.0, rel=1e-2)

    def test_value_today_returns_none_rather_than_assuming_a_vol(self) -> None:
        assert value_today([call(1, 5.0, 100.0, None)], 100.0, T, RATE, Q) is None


class TestStructuralInvariants:
    def test_extremes_do_not_depend_on_the_plotting_grid(self) -> None:
        """Derived analytically, so more points must not change the answer."""
        legs = [call(1, 5.0, 100.0), call(-1, 1.5, 110.0, 0.23)]
        coarse = curve_for(legs, points=5)
        fine = curve_for(legs, points=1001)
        assert coarse.max_profit == pytest.approx(fine.max_profit)
        assert coarse.max_loss == pytest.approx(fine.max_loss)
        assert coarse.breakevens == pytest.approx(fine.breakevens)

    def test_breakeven_outside_the_plotted_range_is_still_found(self) -> None:
        """A grid scan would miss this; solving the terminal segment does not."""
        c = curve_for([call(1, 5.0, 100.0)], price_range=(60.0, 102.0), points=11)
        assert c.breakevens == pytest.approx([105.0])
        assert max(c.underlying_prices) < 105.0

    def test_net_cost_equals_pnl_at_zero_plus_intrinsic(self) -> None:
        legs = [put(1, 4.0, 100.0), call(1, 5.0, 100.0)]
        assert payoff_at_expiry(legs, 100.0) == pytest.approx(-sum(leg.cost for leg in legs))

    def test_curve_length_matches_requested_points(self) -> None:
        c = curve_for([call(1, 5.0, 100.0)], points=37)
        assert len(c.underlying_prices) == 37
        assert len(c.pnl_at_expiry) == 37
        assert len(c.pnl_today or []) == 37

    def test_short_and_long_are_exact_mirrors(self) -> None:
        long_leg = curve_for([call(1, 5.0, 100.0)])
        short_leg = curve_for([call(-1, 5.0, 100.0)])
        for a, b in zip(long_leg.pnl_at_expiry, short_leg.pnl_at_expiry, strict=True):
            assert a == pytest.approx(-b)


class TestInvalidCurves:
    def test_empty_position(self) -> None:
        with pytest.raises(ValueError, match="at least one leg"):
            curve_for([])

    def test_non_positive_spot(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            build_payoff_curve([call(1, 5.0, 100.0)], 0.0, T, RATE, Q)

    def test_too_few_points(self) -> None:
        with pytest.raises(ValueError, match="at least 3 points"):
            curve_for([call(1, 5.0, 100.0)], points=2)
