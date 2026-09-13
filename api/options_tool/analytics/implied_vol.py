"""Implied volatility by numerical inversion of Black-Scholes.

Newton-Raphson on vega, falling back to bisection when Newton misbehaves --
which it does, routinely, on exactly the contracts a real chain is full of:
deep in- or out-of-the-money strikes where vega underflows and the update step
explodes.

The contract of this module is that **it never returns a wrong number.** Every
failure mode returns `None` (or an `IVResult` whose `sigma` is `None` and whose
`status` says why). A chain row with no implied vol renders as blank; a chain row
with a silently fabricated one is a lie that propagates into IV rank, into the
snapshot history, and eventually into a trade. See SPEC.md section 4.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from options_tool.analytics.black_scholes import Right, greeks, price

__all__ = ["IVStatus", "IVResult", "implied_vol", "solve_implied_vol"]

# Search bracket. 500% annualised is past anything a listed equity option trades
# at; below 0.01% the price is indistinguishable from intrinsic in float64.
MIN_SIGMA = 1e-4
MAX_SIGMA = 5.0

NEWTON_MAX_ITER = 50
BISECTION_MAX_ITER = 200
# Convergence in price space, in dollars. Tighter than any real quote increment.
PRICE_TOLERANCE = 1e-8
# Vega below this makes the Newton step numerically meaningless.
MIN_VEGA = 1e-8
# Bisection also stops once the sigma bracket itself is this narrow. Without it,
# a deep-wing contract worth a fraction of a cent satisfies an absolute price
# tolerance while its sigma is still wrong in the fourth decimal -- the price is
# simply flat in sigma out there.
SIGMA_TOLERANCE = 1e-9


def _price_tolerance(target_price: float, intrinsic: float) -> float:
    """Scale the price tolerance to the option's *time value*, not its price.

    Only the extrinsic part of a premium carries information about volatility.
    A deep in-the-money put worth $59.36 can have four cents of a millionth in
    time value; a tolerance scaled to $59.36 is then far looser than the entire
    informative signal, and the solver stops with sigma wrong in the fourth
    decimal while reporting a price match. Scaling to the extrinsic value fixes
    that; the floor keeps the target above float64 noise.
    """
    return max((target_price - intrinsic) * 1e-8, 1e-14)


class IVStatus(StrEnum):
    """Why a solve ended the way it did. Carried into the chain for diagnostics."""

    OK = "ok"
    EXPIRED = "expired"
    NO_QUOTE = "no_quote"
    NON_POSITIVE_PRICE = "non_positive_price"
    BELOW_INTRINSIC = "below_intrinsic"
    ABOVE_MAX = "above_max"
    NO_BRACKET = "no_bracket"
    NOT_CONVERGED = "not_converged"
    INVALID_INPUT = "invalid_input"

    @property
    def explanation(self) -> str:
        """Why no implied vol was produced, in words a UI can show verbatim."""
        return _EXPLANATIONS[self]


_EXPLANATIONS: dict[IVStatus, str] = {
    IVStatus.OK: "solved",
    IVStatus.EXPIRED: "contract has expired; its price implies no future volatility",
    IVStatus.NO_QUOTE: "no usable bid/ask or last price",
    IVStatus.NON_POSITIVE_PRICE: "quoted price is zero or negative",
    IVStatus.BELOW_INTRINSIC: "quoted price is below intrinsic value; quote is stale or crossed",
    IVStatus.ABOVE_MAX: "quoted price exceeds the no-arbitrage maximum",
    IVStatus.NO_BRACKET: f"no volatility in [{MIN_SIGMA:g}, {MAX_SIGMA:g}] reproduces this price",
    IVStatus.NOT_CONVERGED: "solver did not converge",
    IVStatus.INVALID_INPUT: "inputs are not finite or not positive",
}


@dataclass(frozen=True, slots=True)
class IVResult:
    """`sigma is None` whenever `status is not IVStatus.OK`. Never both."""

    sigma: float | None
    status: IVStatus
    iterations: int = 0
    method: str = ""

    @property
    def ok(self) -> bool:
        return self.status is IVStatus.OK


def _arbitrage_bounds(
    S: float, K: float, T: float, r: float, q: float, right: Right
) -> tuple[float, float]:
    """(lower, upper) no-arbitrage price bounds for a European option.

    Outside these there is no sigma that reproduces the price, so the honest
    answer is that the quote is stale or crossed, not some best-effort number.
    """
    carried_spot = S * math.exp(-q * T)
    discounted_strike = K * math.exp(-r * T)
    if right is Right.CALL:
        return max(carried_spot - discounted_strike, 0.0), carried_spot
    return max(discounted_strike - carried_spot, 0.0), discounted_strike


def _initial_guess(target: float, S: float, T: float, r: float, q: float) -> float:
    """Brenner-Subrahmanyam at-the-money approximation, clamped to the bracket.

    sigma ~= sqrt(2*pi/T) * C/S is exact for an ATM forward and a decent starting
    point elsewhere; Newton fixes the rest in a handful of steps.
    """
    forward = S * math.exp((r - q) * T)
    seed = math.sqrt(2.0 * math.pi / T) * target / forward if forward > 0 else 0.5
    return min(max(seed, 0.05), 2.0)


def solve_implied_vol(
    target_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float = 0.0,
    right: Right | str = Right.CALL,
) -> IVResult:
    """Solve for the sigma that reproduces `target_price`, with a reason on failure."""
    try:
        right = Right.parse(right)
    except ValueError:
        return IVResult(None, IVStatus.INVALID_INPUT)

    if not all(math.isfinite(x) for x in (target_price, S, K, T, r, q)):
        return IVResult(None, IVStatus.INVALID_INPUT)
    if S <= 0.0 or K <= 0.0:
        return IVResult(None, IVStatus.INVALID_INPUT)

    # An expired option has no implied vol -- its price carries no information
    # about future variance. This is also the zero-bid case for weeklies on
    # expiry day, which is why it gets its own status.
    if T <= 0.0:
        return IVResult(None, IVStatus.EXPIRED)
    if target_price <= 0.0:
        return IVResult(None, IVStatus.NON_POSITIVE_PRICE)

    lower, upper = _arbitrage_bounds(S, K, T, r, q, right)
    # Strictly inside the bounds: at the boundary sigma is 0 or infinite, and in
    # float64 the interior solve degenerates well before you get there.
    if target_price <= lower + PRICE_TOLERANCE:
        return IVResult(None, IVStatus.BELOW_INTRINSIC)
    if target_price >= upper - PRICE_TOLERANCE:
        return IVResult(None, IVStatus.ABOVE_MAX)

    tolerance = _price_tolerance(target_price, lower)

    def error_at(sigma: float) -> float:
        return price(S, K, T, r, sigma, q, right) - target_price

    newton = _newton(error_at, S, K, T, r, q, right, target_price, tolerance)
    if newton is not None:
        sigma, iterations = newton
        return IVResult(sigma, IVStatus.OK, iterations, "newton")

    return _bisect(error_at, tolerance)


def _newton(
    error_at: Callable[[float], float],
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    right: Right,
    target: float,
    tolerance: float,
) -> tuple[float, int] | None:
    """Newton-Raphson on vega. Returns None to hand over to bisection."""
    sigma = _initial_guess(target, S, T, r, q)

    for iteration in range(1, NEWTON_MAX_ITER + 1):
        err = error_at(sigma)
        if abs(err) < tolerance:
            return sigma, iteration

        vega = greeks(S, K, T, r, sigma, q, right).vega
        # Deep ITM/OTM: the price is flat in sigma, so the Newton step is either
        # meaningless or enormous. Bisection handles these without diverging.
        if vega < MIN_VEGA:
            return None

        step = err / vega
        sigma -= step
        if not math.isfinite(sigma) or sigma <= MIN_SIGMA or sigma >= MAX_SIGMA:
            return None
        # Converged in the variable rather than the residual. On a contract whose
        # price is nearly flat in sigma, the residual target can be below float64
        # resolution and unreachable, but the iteration has still stopped moving.
        if abs(step) < SIGMA_TOLERANCE:
            return sigma, iteration

    return None


def _bisect(error_at: Callable[[float], float], tolerance: float) -> IVResult:
    """Bisection over the full sigma bracket. Slow, but it cannot diverge."""
    lo, hi = MIN_SIGMA, MAX_SIGMA
    err_lo, err_hi = error_at(lo), error_at(hi)

    if abs(err_lo) < tolerance:
        return IVResult(lo, IVStatus.OK, 0, "bisection")
    if abs(err_hi) < tolerance:
        return IVResult(hi, IVStatus.OK, 0, "bisection")
    # The price is monotonic in sigma, so no sign change means the target simply
    # is not attainable anywhere in [MIN_SIGMA, MAX_SIGMA].
    if err_lo * err_hi > 0.0:
        return IVResult(None, IVStatus.NO_BRACKET)

    for iteration in range(1, BISECTION_MAX_ITER + 1):
        mid = 0.5 * (lo + hi)
        err_mid = error_at(mid)
        if abs(err_mid) < tolerance or (hi - lo) < SIGMA_TOLERANCE:
            return IVResult(mid, IVStatus.OK, iteration, "bisection")
        if err_lo * err_mid < 0.0:
            hi, err_hi = mid, err_mid
        else:
            lo, err_lo = mid, err_mid

    return IVResult(None, IVStatus.NOT_CONVERGED, BISECTION_MAX_ITER, "bisection")


def implied_vol(
    target_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float = 0.0,
    right: Right | str = Right.CALL,
) -> float | None:
    """`solve_implied_vol` reduced to the number, or None. Never a wrong number."""
    return solve_implied_vol(target_price, S, K, T, r, q, right).sigma
