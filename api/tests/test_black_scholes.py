"""Black-Scholes pricing and greeks.

Two independent kinds of check, because they fail differently:

- **published reference values**, which catch a formula transcribed wrongly
- **finite differences of our own `price`**, which catch a greek that does not
  actually differentiate the pricer it ships with

A formula can match a textbook and still be wired to the wrong pricer; a greek
can agree with finite differences and still be the wrong formula. Both together
leave very little room.
"""

from __future__ import annotations

import math

import pytest

from options_tool.analytics.black_scholes import (
    DAYS_PER_YEAR,
    Greeks,
    Right,
    greeks,
    norm_cdf,
    norm_pdf,
    price,
)
from tests.conftest import REF_RATE, REF_SIGMA, REF_SPOT, REF_STRIKE, REF_YEARS


class TestNormal:
    def test_cdf_is_symmetric_about_zero(self) -> None:
        assert norm_cdf(0.0) == pytest.approx(0.5)
        for x in (0.25, 1.0, 2.5):
            assert norm_cdf(-x) == pytest.approx(1.0 - norm_cdf(x))

    def test_cdf_matches_known_quantiles(self) -> None:
        assert norm_cdf(1.959963985) == pytest.approx(0.975, abs=1e-9)
        assert norm_cdf(-1.644853627) == pytest.approx(0.05, abs=1e-9)

    def test_pdf_is_the_derivative_of_the_cdf(self) -> None:
        h = 1e-6
        for x in (-1.5, 0.0, 0.75):
            numeric = (norm_cdf(x + h) - norm_cdf(x - h)) / (2 * h)
            assert numeric == pytest.approx(norm_pdf(x), rel=1e-6)


class TestReferenceValues:
    """S=100, K=100, T=1, r=5%, sigma=20%, q=0 -- the standard worked example."""

    def test_call_price(self) -> None:
        assert price(
            REF_SPOT, REF_STRIKE, REF_YEARS, REF_RATE, REF_SIGMA, right=Right.CALL
        ) == pytest.approx(10.450583572185565, abs=1e-10)

    def test_put_price(self) -> None:
        assert price(
            REF_SPOT, REF_STRIKE, REF_YEARS, REF_RATE, REF_SIGMA, right=Right.PUT
        ) == pytest.approx(5.573526022256971, abs=1e-10)

    @pytest.mark.parametrize(
        ("field", "expected"),
        [
            ("delta", 0.6368306511756191),
            ("gamma", 0.018762017345846895),
            ("vega", 37.52403469169379),
            ("theta", -6.414027546438197),
            ("rho", 53.232481545376345),
        ],
    )
    def test_call_greeks(self, field: str, expected: float) -> None:
        computed = getattr(greeks(REF_SPOT, REF_STRIKE, REF_YEARS, REF_RATE, REF_SIGMA), field)
        assert computed == pytest.approx(expected, rel=1e-12)

    def test_put_delta(self) -> None:
        put = greeks(REF_SPOT, REF_STRIKE, REF_YEARS, REF_RATE, REF_SIGMA, right=Right.PUT)
        assert put.delta == pytest.approx(-0.3631693488243809, rel=1e-12)


class TestPutCallParity:
    """C - P = S*e^-qT - K*e^-rT. Holds for every input, so it catches sign errors."""

    @pytest.mark.parametrize("S", [50.0, 95.0, 100.0, 140.0])
    @pytest.mark.parametrize("T", [0.02, 0.5, 3.0])
    @pytest.mark.parametrize("q", [0.0, 0.03])
    def test_parity_holds(self, S: float, T: float, q: float) -> None:
        call = price(S, 100.0, T, 0.05, 0.3, q, Right.CALL)
        put = price(S, 100.0, T, 0.05, 0.3, q, Right.PUT)
        expected = S * math.exp(-q * T) - 100.0 * math.exp(-0.05 * T)
        assert call - put == pytest.approx(expected, abs=1e-10)

    @pytest.mark.parametrize("q", [0.0, 0.02])
    def test_delta_parity(self, q: float) -> None:
        """delta_call - delta_put = e^-qT."""
        call = greeks(100.0, 105.0, 0.75, 0.04, 0.25, q, Right.CALL)
        put = greeks(100.0, 105.0, 0.75, 0.04, 0.25, q, Right.PUT)
        assert call.delta - put.delta == pytest.approx(math.exp(-q * 0.75), abs=1e-12)

    def test_gamma_and_vega_are_right_agnostic(self) -> None:
        call = greeks(107.0, 100.0, 0.4, 0.03, 0.35, 0.01, Right.CALL)
        put = greeks(107.0, 100.0, 0.4, 0.03, 0.35, 0.01, Right.PUT)
        assert call.gamma == pytest.approx(put.gamma, rel=1e-12)
        assert call.vega == pytest.approx(put.vega, rel=1e-12)


class TestGreeksAgainstFiniteDifferences:
    """Every greek must differentiate the pricer it ships with."""

    CASES = [
        (100.0, 100.0, 1.0, 0.05, 0.20, 0.0),
        (100.0, 120.0, 0.5, 0.03, 0.45, 0.02),
        (80.0, 100.0, 2.0, 0.01, 0.15, 0.0),
        (150.0, 100.0, 0.25, 0.05, 0.60, 0.03),
    ]

    @pytest.mark.parametrize(("S", "K", "T", "r", "sigma", "q"), CASES)
    @pytest.mark.parametrize("right", [Right.CALL, Right.PUT])
    def test_all_five(
        self, S: float, K: float, T: float, r: float, sigma: float, q: float, right: Right
    ) -> None:
        analytic = greeks(S, K, T, r, sigma, q, right)
        h = 1e-5

        def p(**overrides: float) -> float:
            args = {"S": S, "K": K, "T": T, "r": r, "sigma": sigma, "q": q}
            args.update(overrides)
            return price(**args, right=right)  # type: ignore[arg-type]

        assert analytic.delta == pytest.approx((p(S=S + h) - p(S=S - h)) / (2 * h), rel=1e-5)
        assert analytic.vega == pytest.approx(
            (p(sigma=sigma + h) - p(sigma=sigma - h)) / (2 * h), rel=1e-5
        )
        # Theta is decay per unit of *elapsed* time, hence the sign flip.
        assert analytic.theta == pytest.approx(-(p(T=T + h) - p(T=T - h)) / (2 * h), rel=1e-5)
        assert analytic.rho == pytest.approx((p(r=r + h) - p(r=r - h)) / (2 * h), rel=1e-5)

    @pytest.mark.parametrize(("S", "K", "T", "r", "sigma", "q"), CASES)
    @pytest.mark.parametrize("right", [Right.CALL, Right.PUT])
    def test_gamma_differentiates_delta(
        self, S: float, K: float, T: float, r: float, sigma: float, q: float, right: Right
    ) -> None:
        """Gamma is checked against a first difference of delta, not a second
        difference of price.

        A second difference divides by h^2, so at h=1e-5 the 1e-16 relative noise
        in a price of order 10 becomes an absolute error of order 1e-5 -- roughly
        the size of gamma itself. Differentiating the analytic delta (already
        verified against price above) is well-conditioned and tests the same
        relationship.
        """
        h = 1e-4 * S

        def delta_at(spot: float) -> float:
            return greeks(spot, K, T, r, sigma, q, right).delta

        numeric = (delta_at(S + h) - delta_at(S - h)) / (2 * h)
        assert greeks(S, K, T, r, sigma, q, right).gamma == pytest.approx(numeric, rel=1e-6)

    @pytest.mark.parametrize(("S", "K", "T", "r", "sigma", "q"), CASES)
    def test_gamma_also_matches_a_well_scaled_second_difference(
        self, S: float, K: float, T: float, r: float, sigma: float, q: float
    ) -> None:
        """Independent confirmation with a step sized for a second derivative."""
        h = 1e-3 * S
        second = (
            price(S + h, K, T, r, sigma, q, Right.CALL)
            - 2 * price(S, K, T, r, sigma, q, Right.CALL)
            + price(S - h, K, T, r, sigma, q, Right.CALL)
        ) / (h * h)
        assert greeks(S, K, T, r, sigma, q, Right.CALL).gamma == pytest.approx(second, rel=1e-4)


class TestDegenerateBranches:
    """Expired or zero-vol contracts are legitimate market states, not errors."""

    @pytest.mark.parametrize(
        ("S", "K", "right", "expected"),
        [
            (110.0, 100.0, Right.CALL, 10.0),
            (90.0, 100.0, Right.CALL, 0.0),
            (90.0, 100.0, Right.PUT, 10.0),
            (110.0, 100.0, Right.PUT, 0.0),
        ],
    )
    def test_expired_is_intrinsic(self, S: float, K: float, right: Right, expected: float) -> None:
        assert price(S, K, 0.0, 0.05, 0.2, 0.0, right) == pytest.approx(expected)

    def test_expired_greeks_are_a_step_function(self) -> None:
        itm = greeks(110.0, 100.0, 0.0, 0.05, 0.2, 0.0, Right.CALL)
        assert itm.delta == pytest.approx(1.0)
        assert itm.gamma == 0.0
        assert itm.vega == 0.0

        otm = greeks(90.0, 100.0, 0.0, 0.05, 0.2, 0.0, Right.CALL)
        assert otm == Greeks(0.0, 0.0, 0.0, 0.0, 0.0)

    def test_at_the_money_at_expiry_picks_no_side(self) -> None:
        """The derivative does not exist at the kink; 0 is the honest answer."""
        assert greeks(100.0, 100.0, 0.0, 0.05, 0.2, 0.0, Right.CALL).delta == 0.0

    def test_zero_vol_is_discounted_forward_intrinsic(self) -> None:
        S, K, T, r = 100.0, 90.0, 1.0, 0.05
        expected = S - K * math.exp(-r * T)
        assert price(S, K, T, r, 0.0, 0.0, Right.CALL) == pytest.approx(expected)

    def test_negative_time_is_treated_as_expired(self) -> None:
        assert price(110.0, 100.0, -0.5, 0.05, 0.2, 0.0, Right.CALL) == pytest.approx(10.0)

    def test_deep_out_of_the_money_underflows_to_zero_not_negative(self) -> None:
        assert price(100.0, 1000.0, 0.01, 0.05, 0.2, 0.0, Right.CALL) >= 0.0


class TestInvalidInput:
    """Wrong inputs raise; degenerate ones do not. The distinction is deliberate."""

    @pytest.mark.parametrize(("S", "K"), [(0.0, 100.0), (-1.0, 100.0), (100.0, 0.0), (100.0, -5.0)])
    def test_non_positive_spot_or_strike_raises(self, S: float, K: float) -> None:
        with pytest.raises(ValueError, match="positive"):
            price(S, K, 1.0, 0.05, 0.2)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    def test_non_finite_raises(self, bad: float) -> None:
        with pytest.raises(ValueError):
            price(100.0, 100.0, bad, 0.05, 0.2)
        with pytest.raises(ValueError):
            price(100.0, 100.0, 1.0, 0.05, bad)


class TestRight:
    @pytest.mark.parametrize("value", ["c", "C", "call", "CALL", "Calls", Right.CALL])
    def test_parses_call_spellings(self, value: str | Right) -> None:
        assert Right.parse(value) is Right.CALL

    @pytest.mark.parametrize("value", ["p", "PUT", "puts", Right.PUT])
    def test_parses_put_spellings(self, value: str | Right) -> None:
        assert Right.parse(value) is Right.PUT

    @pytest.mark.parametrize("value", ["", "banana", "stock", "1"])
    def test_rejects_everything_else(self, value: str) -> None:
        with pytest.raises(ValueError, match="not an option right"):
            Right.parse(value)


class TestGreekScaling:
    def test_display_scalings(self) -> None:
        g = greeks(REF_SPOT, REF_STRIKE, REF_YEARS, REF_RATE, REF_SIGMA)
        assert g.vega_per_point == pytest.approx(g.vega / 100.0)
        assert g.rho_per_point == pytest.approx(g.rho / 100.0)
        assert g.theta_per_day == pytest.approx(g.theta / DAYS_PER_YEAR)

    def test_theta_per_day_is_a_plausible_magnitude(self) -> None:
        """A 1-year ATM option loses cents, not dollars, per day."""
        theta_per_day = greeks(REF_SPOT, REF_STRIKE, REF_YEARS, REF_RATE, REF_SIGMA).theta_per_day
        assert -0.05 < theta_per_day < 0
