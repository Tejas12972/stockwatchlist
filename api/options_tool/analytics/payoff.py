"""Profit and loss for a multi-leg options position.

Two curves, because they answer different questions:

- **at expiry** -- intrinsic value only, the classic hockey-stick diagram
- **today** -- every leg repriced through Black-Scholes at the time remaining,
  which is what the position is actually worth if you close it now

The "today" curve reuses `black_scholes.price` rather than reimplementing
anything. That reuse is the reason a spread's greeks and its P/L curve can never
disagree with each other.

Behaviour beyond the plotted range is derived **analytically, not read off the
grid.** Past the outermost strike an option payoff is linear, so the slope there
is exact; a short call's unlimited loss is reported as unlimited rather than as
whatever number the last grid point happened to land on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from options_tool.analytics.black_scholes import Right, price

__all__ = [
    "LegKind",
    "Leg",
    "PayoffCurve",
    "OPTION_MULTIPLIER",
    "payoff_at_expiry",
    "value_today",
    "build_payoff_curve",
]

OPTION_MULTIPLIER = 100
"""Shares per US equity option contract. Getting this wrong is a 100x error in
every dollar figure the tool reports, so it is named rather than inlined."""


class LegKind(StrEnum):
    CALL = "call"
    PUT = "put"
    STOCK = "stock"


@dataclass(frozen=True, slots=True)
class Leg:
    """One leg of a position.

    `quantity` is signed: positive is long, negative is short. `premium` is what
    was paid (or received) per share, always quoted positive -- the sign of the
    cash flow comes from `quantity`, so a short leg's credit does not need the
    caller to also negate the premium.
    """

    kind: LegKind
    quantity: int
    premium: float
    strike: float | None = None
    volatility: float | None = None

    def __post_init__(self) -> None:
        if self.quantity == 0:
            raise ValueError("leg quantity must be non-zero")
        if self.premium < 0:
            raise ValueError(
                f"premium is quoted positive ({self.premium} given); "
                "use a negative quantity for a short leg"
            )
        if self.kind is LegKind.STOCK:
            if self.strike is not None:
                raise ValueError("a stock leg has no strike")
        elif self.strike is None or self.strike <= 0:
            raise ValueError(f"{self.kind} leg needs a positive strike")

    @property
    def multiplier(self) -> int:
        return 1 if self.kind is LegKind.STOCK else OPTION_MULTIPLIER

    @property
    def cost(self) -> float:
        """Signed cash flow to open. Positive is a debit paid, negative a credit."""
        return self.quantity * self.premium * self.multiplier

    def intrinsic(self, underlying: float) -> float:
        """Value of one share/contract-share of this leg at expiry."""
        if self.kind is LegKind.STOCK:
            return underlying
        assert self.strike is not None
        if self.kind is LegKind.CALL:
            return max(underlying - self.strike, 0.0)
        return max(self.strike - underlying, 0.0)


def payoff_at_expiry(legs: list[Leg], underlying: float) -> float:
    """Position P/L at expiry for a given settlement price."""
    value = sum(leg.quantity * leg.intrinsic(underlying) * leg.multiplier for leg in legs)
    return value - sum(leg.cost for leg in legs)


def value_today(
    legs: list[Leg],
    underlying: float,
    time_to_expiry: float,
    risk_free_rate: float,
    dividend_yield: float = 0.0,
) -> float | None:
    """Position P/L if closed now, or None if a leg has no volatility to price with.

    Returning None rather than defaulting the missing volatility is deliberate:
    a P/L curve drawn at an assumed 30% vol looks exactly as authoritative as one
    drawn at the market's real vol, and there is nothing on the chart to tell
    you which you are looking at.
    """
    total = 0.0
    for leg in legs:
        if leg.kind is LegKind.STOCK:
            total += leg.quantity * underlying * leg.multiplier
            continue
        if leg.volatility is None or leg.volatility <= 0:
            return None
        assert leg.strike is not None
        right = Right.CALL if leg.kind is LegKind.CALL else Right.PUT
        leg_price = price(
            underlying,
            leg.strike,
            time_to_expiry,
            risk_free_rate,
            leg.volatility,
            dividend_yield,
            right,
        )
        total += leg.quantity * leg_price * leg.multiplier

    return total - sum(leg.cost for leg in legs)


@dataclass(frozen=True, slots=True)
class PayoffCurve:
    """P/L across a range of underlying prices, plus the position's shape."""

    underlying_prices: list[float]
    pnl_at_expiry: list[float]
    pnl_today: list[float] | None
    net_cost: float
    breakevens: list[float]
    max_profit: float | None
    max_loss: float | None
    upside_slope: float
    downside_slope: float
    time_to_expiry: float
    today_unavailable_reason: str | None = None

    @property
    def unlimited_profit(self) -> bool:
        return self.max_profit is None

    @property
    def unlimited_loss(self) -> bool:
        return self.max_loss is None


def build_payoff_curve(
    legs: list[Leg],
    spot: float,
    time_to_expiry: float,
    risk_free_rate: float,
    dividend_yield: float = 0.0,
    price_range: tuple[float, float] | None = None,
    points: int = 201,
) -> PayoffCurve:
    """Both P/L curves plus breakevens and true (not grid-bounded) extremes."""
    if not legs:
        raise ValueError("a position needs at least one leg")
    if spot <= 0:
        raise ValueError(f"spot must be positive, got {spot}")
    if points < 3:
        raise ValueError("need at least 3 points to draw a curve")

    low, high = price_range or _default_range(legs, spot)
    step = (high - low) / (points - 1)
    prices = [low + i * step for i in range(points)]

    pnl_expiry = [payoff_at_expiry(legs, p) for p in prices]

    missing_vol = [
        leg for leg in legs if leg.kind is not LegKind.STOCK and not (leg.volatility or 0) > 0
    ]
    if missing_vol:
        pnl_today = None
        reason = (
            f"{len(missing_vol)} leg(s) have no implied volatility, so today's value "
            "cannot be priced. Supply a volatility per leg or let the API solve it "
            "from the live chain."
        )
    else:
        pnl_today = [
            value_today(legs, p, time_to_expiry, risk_free_rate, dividend_yield) or 0.0
            for p in prices
        ]
        reason = None

    upside_slope, downside_slope = _terminal_slopes(legs)
    max_profit, max_loss = _extremes(legs, upside_slope)

    return PayoffCurve(
        underlying_prices=prices,
        pnl_at_expiry=pnl_expiry,
        pnl_today=pnl_today,
        net_cost=sum(leg.cost for leg in legs),
        breakevens=_breakevens(legs, upside_slope),
        max_profit=max_profit,
        max_loss=max_loss,
        upside_slope=upside_slope,
        downside_slope=downside_slope,
        time_to_expiry=time_to_expiry,
        today_unavailable_reason=reason,
    )


def _default_range(legs: list[Leg], spot: float) -> tuple[float, float]:
    """A range wide enough to show every strike and the shape around them."""
    strikes = [leg.strike for leg in legs if leg.strike is not None]
    low = min([spot, *strikes]) * 0.7
    high = max([spot, *strikes]) * 1.3
    return max(low, 0.01), high


def _terminal_slopes(legs: list[Leg]) -> tuple[float, float]:
    """dP/dS above every strike, and dP/dS below every strike.

    Above all strikes every call is in the money (slope 1 per share) and every
    put is worthless; below all strikes the reverse. Stock is linear throughout.
    Both are exact derivatives, so "unlimited" can be reported as unlimited
    rather than inferred from where a chart happens to stop.
    """
    upside = 0.0
    downside = 0.0
    for leg in legs:
        weight = leg.quantity * leg.multiplier
        if leg.kind is LegKind.STOCK:
            upside += weight
            downside += weight
        elif leg.kind is LegKind.CALL:
            upside += weight
        else:
            downside -= weight
    return upside, downside


def _kink_prices(legs: list[Leg]) -> list[float]:
    """Underlying prices where the expiry payoff changes slope, plus both ends.

    The expiry payoff is piecewise linear with kinks only at strikes, so these
    points plus the terminal slopes describe it exactly on [0, infinity).
    Zero is included because the underlying can reach it -- that bound is why a
    long put's profit is large but finite.
    """
    strikes = sorted({leg.strike for leg in legs if leg.strike is not None})
    beyond = (strikes[-1] * 2.0) if strikes else 1.0
    return [0.0, *strikes, beyond]


def _extremes(legs: list[Leg], upside_slope: float) -> tuple[float | None, float | None]:
    """Exact (max_profit, max_loss) over all possible settlement prices.

    `None` means genuinely unbounded, which only the upside can be: the
    underlying can rise without limit but cannot fall below zero. That asymmetry
    is why a naked short call has unlimited loss while a covered call -- whose
    P/L also falls as the stock falls -- does not.
    """
    values = [payoff_at_expiry(legs, p) for p in _kink_prices(legs)]
    max_profit = None if upside_slope > 0 else max(values)
    max_loss = None if upside_slope < 0 else min(values)
    return max_profit, max_loss


def _breakevens(legs: list[Leg], upside_slope: float) -> list[float]:
    """Exact underlying prices where expiry P/L crosses zero.

    Solved segment by segment on the piecewise-linear payoff rather than scanned
    off the plotting grid, so a breakeven never lands a few cents out because of
    where the grid points happened to fall -- and one beyond the last strike is
    still found.
    """
    kinks = _kink_prices(legs)
    values = [payoff_at_expiry(legs, p) for p in kinks]
    crossings: list[float] = []

    for i in range(len(kinks) - 1):
        lo, hi = values[i], values[i + 1]
        if lo == 0.0:
            crossings.append(kinks[i])
        elif lo * hi < 0.0:
            weight = abs(lo) / (abs(lo) + abs(hi))
            crossings.append(kinks[i] + weight * (kinks[i + 1] - kinks[i]))

    # Past the outermost strike the payoff is a straight line, so any remaining
    # crossing can be solved for directly instead of extending the grid.
    if upside_slope != 0.0:
        last_kink, last_value = kinks[-1], values[-1]
        crossing = last_kink - last_value / upside_slope
        if crossing > last_kink:
            crossings.append(crossing)
    elif values[-1] == 0.0:
        crossings.append(kinks[-1])

    return sorted(set(crossings))
