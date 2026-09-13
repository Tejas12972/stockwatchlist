"""The implied-volatility solver.

The contract under test is not "is it accurate" but **"does it ever return a
wrong number"**. Accuracy is checked by round-tripping price -> IV -> price; the
rest of this file is a catalogue of the ways a chain breaks a solver, each one
asserting `None` and a named reason.
"""

from __future__ import annotations

import math

import pytest

from options_tool.analytics.black_scholes import Right, price
from options_tool.analytics.implied_vol import (
    MAX_SIGMA,
    MIN_SIGMA,
    IVStatus,
    implied_vol,
    solve_implied_vol,
)

RATE = 0.05


class TestRoundTrip:
    """price(sigma) -> solve -> sigma. The strongest available correctness check."""

    SIGMAS = [0.01, 0.05, 0.1, 0.2, 0.35, 0.5, 1.0, 2.5, 4.5]
    MATURITIES = [0.0001, 0.01, 0.08, 0.5, 1.0, 2.0]

    @pytest.mark.parametrize("sigma", SIGMAS)
    @pytest.mark.parametrize("right", [Right.CALL, Right.PUT])
    def test_at_the_money(self, sigma: float, right: Right) -> None:
        target = price(100.0, 100.0, 1.0, RATE, sigma, 0.0, right)
        assert implied_vol(target, 100.0, 100.0, 1.0, RATE, 0.0, right) == pytest.approx(
            sigma, abs=1e-8
        )

    @pytest.mark.parametrize("T", MATURITIES)
    @pytest.mark.parametrize("strike", [60.0, 90.0, 100.0, 110.0, 160.0])
    @pytest.mark.parametrize("right", [Right.CALL, Right.PUT])
    def test_across_the_surface(self, T: float, strike: float, right: Right) -> None:
        """Every point that produces a representable price must invert exactly.

        Deep wings at short maturities underflow to a zero price, which carries
        no information about volatility -- those are asserted separately below,
        not quietly skipped as a solver failure.
        """
        sigma = 0.30
        target = price(100.0, strike, T, RATE, sigma, 0.0, right)
        result = solve_implied_vol(target, 100.0, strike, T, RATE, 0.0, right)

        if result.status is IVStatus.OK:
            assert result.sigma == pytest.approx(sigma, abs=1e-7)
        else:
            assert result.sigma is None
            assert result.status in {
                IVStatus.NON_POSITIVE_PRICE,
                IVStatus.BELOW_INTRINSIC,
                IVStatus.ABOVE_MAX,
            }

    @pytest.mark.parametrize("q", [0.0, 0.015, 0.05])
    def test_with_a_dividend_yield(self, q: float) -> None:
        target = price(100.0, 105.0, 0.75, RATE, 0.28, q, Right.PUT)
        assert implied_vol(target, 100.0, 105.0, 0.75, RATE, q, Right.PUT) == pytest.approx(
            0.28, abs=1e-8
        )

    def test_reprices_to_the_original_input(self) -> None:
        """The round trip that actually matters to a user: price is recovered."""
        original = price(100.0, 95.0, 0.4, RATE, 0.42, 0.0, Right.CALL)
        sigma = implied_vol(original, 100.0, 95.0, 0.4, RATE, 0.0, Right.CALL)
        assert sigma is not None
        assert price(100.0, 95.0, 0.4, RATE, sigma, 0.0, Right.CALL) == pytest.approx(
            original, abs=1e-10
        )


class TestBisectionFallback:
    """Wing contracts where Newton's step explodes must still solve."""

    WING_CASES = [
        (100.0, 150.0, 0.01, 0.80, Right.CALL),
        (100.0, 160.0, 0.02, 1.20, Right.CALL),
        (100.0, 40.0, 0.05, 0.90, Right.PUT),
        (100.0, 250.0, 0.25, 1.50, Right.CALL),
        (50.0, 500.0, 2.00, 0.60, Right.CALL),
        (100.0, 130.0, 0.003, 2.00, Right.CALL),
    ]

    @pytest.mark.parametrize(("S", "K", "T", "sigma", "right"), WING_CASES)
    def test_wings_converge_via_bisection(
        self, S: float, K: float, T: float, sigma: float, right: Right
    ) -> None:
        target = price(S, K, T, RATE, sigma, 0.0, right)
        result = solve_implied_vol(target, S, K, T, RATE, 0.0, right)
        assert result.status is IVStatus.OK
        assert result.sigma == pytest.approx(sigma, abs=1e-6)

    def test_at_least_one_case_actually_uses_bisection(self) -> None:
        """Guards the fallback against becoming dead code no test exercises."""
        methods = set()
        for S, K, T, sigma, right in self.WING_CASES:
            target = price(S, K, T, RATE, sigma, 0.0, right)
            methods.add(solve_implied_vol(target, S, K, T, RATE, 0.0, right).method)
        assert "bisection" in methods

    def test_newton_handles_the_ordinary_case(self) -> None:
        target = price(100.0, 100.0, 1.0, RATE, 0.25, 0.0, Right.CALL)
        result = solve_implied_vol(target, 100.0, 100.0, 1.0, RATE, 0.0, Right.CALL)
        assert result.method == "newton"
        assert result.iterations < 10


class TestEveryFailureReturnsNone:
    """SPEC.md section 4: return None, never a silently wrong number."""

    def test_expired_contract(self) -> None:
        result = solve_implied_vol(5.0, 100.0, 100.0, 0.0, RATE)
        assert result.sigma is None
        assert result.status is IVStatus.EXPIRED

    def test_negative_time(self) -> None:
        assert solve_implied_vol(5.0, 100.0, 100.0, -1.0, RATE).status is IVStatus.EXPIRED

    @pytest.mark.parametrize("bad_price", [0.0, -0.01, -100.0])
    def test_zero_or_negative_price(self, bad_price: float) -> None:
        """The zero-bid case: a contract nobody will pay for implies nothing."""
        result = solve_implied_vol(bad_price, 100.0, 100.0, 1.0, RATE)
        assert result.sigma is None
        assert result.status is IVStatus.NON_POSITIVE_PRICE

    def test_price_below_intrinsic(self) -> None:
        """A crossed or stale quote on a deep ITM strike."""
        result = solve_implied_vol(0.01, 100.0, 50.0, 1.0, RATE, right=Right.CALL)
        assert result.sigma is None
        assert result.status is IVStatus.BELOW_INTRINSIC

    def test_price_above_the_no_arbitrage_maximum(self) -> None:
        result = solve_implied_vol(500.0, 100.0, 100.0, 1.0, RATE, right=Right.CALL)
        assert result.sigma is None
        assert result.status is IVStatus.ABOVE_MAX

    def test_put_above_its_own_maximum(self) -> None:
        """A put cannot be worth more than its discounted strike."""
        result = solve_implied_vol(200.0, 100.0, 100.0, 1.0, RATE, right=Right.PUT)
        assert result.sigma is None
        assert result.status is IVStatus.ABOVE_MAX

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_inputs(self, bad: float) -> None:
        assert solve_implied_vol(bad, 100.0, 100.0, 1.0, RATE).status is IVStatus.INVALID_INPUT
        assert solve_implied_vol(5.0, bad, 100.0, 1.0, RATE).status is IVStatus.INVALID_INPUT

    @pytest.mark.parametrize(("S", "K"), [(0.0, 100.0), (-10.0, 100.0), (100.0, 0.0)])
    def test_non_positive_spot_or_strike(self, S: float, K: float) -> None:
        assert solve_implied_vol(5.0, S, K, 1.0, RATE).status is IVStatus.INVALID_INPUT

    def test_unparseable_right(self) -> None:
        assert (
            solve_implied_vol(5.0, 100.0, 100.0, 1.0, RATE, right="banana").status
            is IVStatus.INVALID_INPUT
        )

    def test_vol_above_the_search_bracket_is_not_invented(self) -> None:
        """A price implying >500% vol reports no bracket rather than clamping.

        Clamping to MAX_SIGMA would return a plausible-looking 5.0 that is simply
        the edge of the search space, not a measurement.
        """
        just_under_max = price(100.0, 100.0, 1.0, RATE, MAX_SIGMA, 0.0, Right.CALL)
        upper_bound = 100.0  # a call cannot exceed spot when q = 0
        target = (just_under_max + upper_bound) / 2
        result = solve_implied_vol(target, 100.0, 100.0, 1.0, RATE, right=Right.CALL)
        assert result.sigma is None
        assert result.status in {IVStatus.NO_BRACKET, IVStatus.ABOVE_MAX}


class TestResultInvariants:
    def test_sigma_and_status_never_disagree(self) -> None:
        """Either a number with status OK, or None with a reason. Never both."""
        cases = [
            (10.45, 100.0, 100.0, 1.0),
            (0.0, 100.0, 100.0, 1.0),
            (5.0, 100.0, 100.0, 0.0),
            (999.0, 100.0, 100.0, 1.0),
            (0.001, 100.0, 40.0, 1.0),
        ]
        for target, S, K, T in cases:
            result = solve_implied_vol(target, S, K, T, RATE)
            if result.status is IVStatus.OK:
                assert result.sigma is not None
                assert MIN_SIGMA <= result.sigma <= MAX_SIGMA
                assert result.ok
            else:
                assert result.sigma is None
                assert not result.ok

    def test_every_status_has_an_explanation(self) -> None:
        for status in IVStatus:
            assert status.explanation
            assert not status.explanation.endswith(".")

    def test_solved_sigma_is_always_inside_the_bracket(self) -> None:
        for sigma in (0.02, 0.5, 3.0):
            target = price(100.0, 100.0, 1.0, RATE, sigma, 0.0, Right.CALL)
            solved = implied_vol(target, 100.0, 100.0, 1.0, RATE)
            assert solved is not None
            assert MIN_SIGMA <= solved <= MAX_SIGMA


class TestPrecision:
    """Guards the tolerance fix: an absolute price tolerance is not enough.

    A sub-penny wing contract satisfies a $1e-8 absolute price tolerance while
    its sigma is still wrong in the fourth decimal, because out there the price
    is nearly flat in sigma. The solver scales its tolerance to the quote and
    also stops on bracket width.
    """

    @pytest.mark.parametrize(
        ("S", "K", "T", "sigma", "right"),
        [
            (100.0, 150.0, 0.01, 0.80, Right.CALL),
            (100.0, 40.0, 0.05, 0.90, Right.PUT),
            (100.0, 101.0, 0.0001, 0.30, Right.CALL),
        ],
    )
    def test_sub_penny_contracts_still_solve_to_eight_decimals(
        self, S: float, K: float, T: float, sigma: float, right: Right
    ) -> None:
        target = price(S, K, T, RATE, sigma, 0.0, right)
        assert target < 0.01, "this case is meant to be a sub-penny quote"
        solved = implied_vol(target, S, K, T, RATE, 0.0, right)
        assert solved is not None
        assert abs(solved - sigma) < 1e-7

    def test_monotonicity_of_price_in_sigma(self) -> None:
        """The property bisection relies on: without it, bracketing is invalid."""
        previous = -math.inf
        for i in range(1, 60):
            current = price(100.0, 110.0, 0.5, RATE, i * 0.05, 0.0, Right.CALL)
            assert current > previous
            previous = current
