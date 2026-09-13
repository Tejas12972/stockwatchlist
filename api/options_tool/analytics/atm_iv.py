"""Reduce a day's chains to the one implied-volatility number IV rank needs.

IV rank compares today's implied volatility to its own history, which requires a
single scalar per day. Picking "the IV of some strike" is not good enough: as
spot drifts, a fixed strike slides along the skew, and as days pass the front
expiry rolls. Both make the resulting series move for reasons that have nothing
to do with volatility.

So the daily number is a **30-day constant-maturity at-the-money implied
volatility**:

1. within each expiry, interpolate IV to the exactly-at-the-money strike
2. across expiries, interpolate in *total variance* to a 30-day horizon

Variance, not volatility, because variance is what is additive in time --
interpolating volatility linearly across maturities systematically misprices the
middle. If 30 days cannot be bracketed by the listed expiries, this returns
``None`` and the day is excluded from IV rank rather than extrapolated.
"""

from __future__ import annotations

import pandas as pd

__all__ = ["CONSTANT_MATURITY_DAYS", "atm_iv_for_expiry", "constant_maturity_atm_iv"]

CONSTANT_MATURITY_DAYS = 30.0
"""Horizon for the daily series. 30 days is the market convention (it is what
VIX-style measures target) and is long enough to be liquid, short enough to move."""

# Ignore strikes further than this from spot when interpolating to the money.
# Beyond it the skew is steep enough that interpolation is guesswork.
_MAX_MONEYNESS_DISTANCE = 0.15


def atm_iv_for_expiry(frame: pd.DataFrame) -> float | None:
    """Implied vol at spot for one expiry, or None if it cannot be established.

    Calls and puts are averaged where both sides interpolate. Put-call parity
    means they should agree; averaging damps the disagreement caused by one side
    having a wider or staler market.
    """
    usable = frame[frame["iv"].notna()]
    if usable.empty:
        return None
    usable = usable[(usable["moneyness"] - 1.0).abs() <= _MAX_MONEYNESS_DISTANCE]
    if usable.empty:
        return None

    per_side = [
        value
        for _, side in usable.groupby("right", observed=True)
        if (value := _interpolate_to_spot(side)) is not None
    ]
    if not per_side:
        return None
    return sum(per_side) / len(per_side)


def _interpolate_to_spot(side: pd.DataFrame) -> float | None:
    """Linearly interpolate this side's IV between the strikes bracketing spot."""
    spot = float(side["spot"].iloc[0])
    below = side[side["strike"] <= spot].nlargest(1, "strike")
    above = side[side["strike"] >= spot].nsmallest(1, "strike")

    if below.empty and above.empty:
        return None
    # Spot sits outside the listed strikes on this side: the nearest strike is
    # the best available answer, and it is a real quote rather than an
    # extrapolation off the end of the skew.
    if below.empty:
        return float(above["iv"].iloc[0])
    if above.empty:
        return float(below["iv"].iloc[0])

    k_lo, iv_lo = float(below["strike"].iloc[0]), float(below["iv"].iloc[0])
    k_hi, iv_hi = float(above["strike"].iloc[0]), float(above["iv"].iloc[0])
    if k_hi == k_lo:
        return iv_lo

    weight = (spot - k_lo) / (k_hi - k_lo)
    return iv_lo + weight * (iv_hi - iv_lo)


def constant_maturity_atm_iv(
    by_maturity: dict[float, float], target_days: float = CONSTANT_MATURITY_DAYS
) -> float | None:
    """Interpolate ATM vols to a fixed horizon, in total variance.

    Parameters
    ----------
    by_maturity : {years to expiry: ATM implied vol}
    target_days : the horizon to interpolate to, in calendar days

    Returns None when the horizon cannot be bracketed. Extrapolating the term
    structure past the listed expiries would invent a number, and on day one of
    the IV history that invented number becomes the historical minimum forever.
    """
    points = sorted((T, iv) for T, iv in by_maturity.items() if T > 0 and iv is not None and iv > 0)
    if not points:
        return None

    target = target_days / 365.0

    # An exact or near-exact match needs no interpolation at all.
    for T, iv in points:
        if abs(T - target) < 1e-9:
            return iv

    below = [p for p in points if p[0] < target]
    above = [p for p in points if p[0] > target]
    if not below or not above:
        return None

    t_lo, iv_lo = below[-1]
    t_hi, iv_hi = above[0]

    # Total variance is additive in time; volatility is not.
    var_lo, var_hi = iv_lo * iv_lo * t_lo, iv_hi * iv_hi * t_hi
    weight = (target - t_lo) / (t_hi - t_lo)
    variance = var_lo + weight * (var_hi - var_lo)
    if variance <= 0:
        return None
    return float((variance / target) ** 0.5)
