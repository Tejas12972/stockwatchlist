"""Black-Scholes-Merton pricing and analytic greeks.

The vendor ships a `delta` column. We do not read it. Everything here is derived
from the model so the assumptions are visible and testable, which is the whole
point of the project (SPEC.md section 4).

Conventions
-----------
All inputs are in natural units: `sigma` and `r` are annualised decimals (0.25 =
25%), `T` is in years. Greeks are returned **raw** -- per 1.00 of the underlying
variable, per year for theta. Trading desks quote them scaled, so `Greeks`
exposes `vega_per_point`, `theta_per_day` and `rho_per_point` for display. Tests
check the raw values against finite differences of `price`, which only works
because the raw form is the true derivative.

No scipy: the normal CDF comes from `math.erf`, and chains are small enough
(hundreds to a few thousand rows) that scalar Python is milliseconds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["Right", "Greeks", "price", "greeks", "d1_d2", "norm_cdf", "norm_pdf"]

_SQRT_2 = math.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)

# Below this, time or volatility is treated as zero rather than divided by.
_EPS = 1e-12

DAYS_PER_YEAR = 365.0
"""Theta is divided by this for per-day display. Calendar days, not trading days:
an option decays over weekends too."""


class Right(StrEnum):
    """Option right. A StrEnum so it serialises and compares as 'call'/'put'."""

    CALL = "call"
    PUT = "put"

    @classmethod
    def parse(cls, value: str | Right) -> Right:
        """Accept 'c', 'C', 'call', 'CALL', Right.CALL ... and reject the rest."""
        if isinstance(value, cls):
            return value
        text = str(value).strip().lower()
        if text in {"c", "call", "calls"}:
            return cls.CALL
        if text in {"p", "put", "puts"}:
            return cls.PUT
        raise ValueError(f"not an option right: {value!r}")


@dataclass(frozen=True, slots=True)
class Greeks:
    """Raw (unscaled) greeks. See module docstring for the convention."""

    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float

    @property
    def vega_per_point(self) -> float:
        """P/L for a 1 percentage-point move in implied vol."""
        return self.vega / 100.0

    @property
    def theta_per_day(self) -> float:
        """P/L for one calendar day of decay, all else equal."""
        return self.theta / DAYS_PER_YEAR

    @property
    def rho_per_point(self) -> float:
        """P/L for a 1 percentage-point move in the risk-free rate."""
        return self.rho / 100.0


def norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / _SQRT_2))


def norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def _validate(S: float, K: float, T: float, sigma: float) -> None:
    """Reject inputs that are wrong, as opposed to merely degenerate.

    A zero spot or strike is a data error and must surface loudly. Expired or
    zero-vol contracts are legitimate market states and are handled in-band.
    """
    if not math.isfinite(S) or S <= 0.0:
        raise ValueError(f"spot must be finite and positive, got {S!r}")
    if not math.isfinite(K) or K <= 0.0:
        raise ValueError(f"strike must be finite and positive, got {K!r}")
    if not math.isfinite(T):
        raise ValueError(f"time to expiry must be finite, got {T!r}")
    if not math.isfinite(sigma):
        raise ValueError(f"sigma must be finite, got {sigma!r}")


def d1_d2(
    S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0
) -> tuple[float, float]:
    """The two Black-Scholes moneyness terms.

    Only defined for T > 0 and sigma > 0; callers must handle the degenerate
    branches before reaching here.
    """
    vol_sqrt_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_sqrt_t
    return d1, d1 - vol_sqrt_t


def _forward_intrinsic(S: float, K: float, T: float, r: float, q: float, right: Right) -> float:
    """Discounted intrinsic value of the forward.

    This is the correct price in both degenerate branches: at T = 0 the discount
    factors are 1 and it collapses to plain intrinsic value; at sigma = 0 the
    underlying arrives at its forward with certainty.
    """
    carried_spot = S * math.exp(-q * T)
    discounted_strike = K * math.exp(-r * T)
    if right is Right.CALL:
        return max(carried_spot - discounted_strike, 0.0)
    return max(discounted_strike - carried_spot, 0.0)


def price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    q: float = 0.0,
    right: Right | str = Right.CALL,
) -> float:
    """Black-Scholes-Merton price of a European option.

    Parameters
    ----------
    S : spot price of the underlying
    K : strike
    T : time to expiry in years
    r : annualised continuously-compounded risk-free rate
    sigma : annualised volatility
    q : continuous dividend yield
    right : 'call' or 'put'
    """
    right = Right.parse(right)
    _validate(S, K, T, sigma)

    # Expired, or a zero-vol underlying: both collapse to discounted intrinsic.
    if T <= _EPS or sigma <= _EPS:
        return _forward_intrinsic(S, K, max(T, 0.0), r, q, right)

    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    carried_spot = S * math.exp(-q * T)
    discounted_strike = K * math.exp(-r * T)

    if right is Right.CALL:
        return carried_spot * norm_cdf(d1) - discounted_strike * norm_cdf(d2)
    return discounted_strike * norm_cdf(-d2) - carried_spot * norm_cdf(-d1)


def greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    q: float = 0.0,
    right: Right | str = Right.CALL,
) -> Greeks:
    """Analytic delta, gamma, vega, theta and rho. Raw units -- see module docstring."""
    right = Right.parse(right)
    _validate(S, K, T, sigma)

    if T <= _EPS or sigma <= _EPS:
        return _degenerate_greeks(S, K, max(T, 0.0), r, q, right)

    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    sqrt_t = math.sqrt(T)
    carry = math.exp(-q * T)
    discount = math.exp(-r * T)
    pdf_d1 = norm_pdf(d1)

    # Identical for calls and puts.
    gamma = carry * pdf_d1 / (S * sigma * sqrt_t)
    vega = S * carry * pdf_d1 * sqrt_t

    # Shared first term of theta: the pure time-decay of optionality.
    decay = -S * carry * pdf_d1 * sigma / (2.0 * sqrt_t)

    if right is Right.CALL:
        delta = carry * norm_cdf(d1)
        theta = decay - r * K * discount * norm_cdf(d2) + q * S * carry * norm_cdf(d1)
        rho = K * T * discount * norm_cdf(d2)
    else:
        delta = -carry * norm_cdf(-d1)
        theta = decay + r * K * discount * norm_cdf(-d2) - q * S * carry * norm_cdf(-d1)
        rho = -K * T * discount * norm_cdf(-d2)

    return Greeks(delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho)


def _degenerate_greeks(S: float, K: float, T: float, r: float, q: float, right: Right) -> Greeks:
    """Greeks where the distribution has collapsed to a point.

    With no time or no volatility the payoff is a step function: gamma and vega
    vanish, delta is an indicator, and theta/rho are just the derivatives of the
    discount factors on whichever side is in the money. Exactly at the money the
    derivative does not exist; we report 0 delta rather than pick a side.
    """
    forward = S * math.exp((r - q) * T)
    in_the_money = forward > K if right is Right.CALL else forward < K

    if not in_the_money:
        return Greeks(delta=0.0, gamma=0.0, vega=0.0, theta=0.0, rho=0.0)

    carry = math.exp(-q * T)
    discount = math.exp(-r * T)
    if right is Right.CALL:
        # d/dS, d/dT and d/dr of (S e^-qT - K e^-rT)
        return Greeks(
            delta=carry,
            gamma=0.0,
            vega=0.0,
            theta=q * S * carry - r * K * discount,
            rho=K * T * discount,
        )
    return Greeks(
        delta=-carry,
        gamma=0.0,
        vega=0.0,
        theta=r * K * discount - q * S * carry,
        rho=-K * T * discount,
    )
