"""Pydantic models for every request and response.

Two conventions run through all of these:

- **Nullable analytics are `float | None`, never defaulted.** A contract whose
  implied volatility did not solve carries `iv: null` and an `iv_status` saying
  why. Clients must handle the null; there is no zero to mistake for a real
  reading.
- **Degraded states are modelled, not signalled by omission.** `IVRankResponse`
  always returns, carrying `rank: null` plus a `reason` the UI renders verbatim.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "HealthResponse",
    "QuoteResponse",
    "ContractOut",
    "ChainSummary",
    "ChainResponse",
    "IVRankResponse",
    "WatchlistItem",
    "WatchlistResponse",
    "WatchlistAddRequest",
    "LegIn",
    "PayoffRequest",
    "PayoffResponse",
    "SnapshotResponse",
    "ScreenHitOut",
    "ScreenResponse",
    "EarningsResponse",
]

DISCLAIMER = "Not investment advice. Personal analytics only."


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    provider: str
    database_reachable: bool
    snapshot_scheduler: bool = Field(
        default=False,
        description="Whether this instance is running the daily snapshot itself.",
    )
    snapshot_at: str | None = Field(
        default=None, description="Scheduled snapshot time in UTC, when enabled."
    )


class QuoteResponse(BaseModel):
    ticker: str
    price: float
    currency: str
    as_of: datetime
    previous_close: float | None = None


class ContractOut(BaseModel):
    """One contract. Every greek here was computed by this service."""

    model_config = ConfigDict(from_attributes=True)

    expiry: date
    strike: float
    right: Literal["call", "put"]

    bid: float | None
    ask: float | None
    last: float | None
    mid: float | None
    volume: int | None
    open_interest: int | None

    price: float | None = Field(None, description="The price implied vol was solved from.")
    price_source: Literal["mid", "last"] | None = Field(
        None, description="Whether `price` came from a two-sided book or a stale print."
    )
    time_to_expiry: float
    moneyness: float
    in_the_money: bool

    iv: float | None = Field(None, description="Null when the solve failed; see iv_status.")
    iv_status: str = Field(description="'ok', or the named reason no IV was produced.")
    delta: float | None
    gamma: float | None
    vega: float | None
    theta: float | None
    rho: float | None


class ChainSummary(BaseModel):
    contracts: int
    solved: int
    solve_rate: float = Field(description="Share of strikes whose IV solved. Low means distrust.")
    atm_iv: float | None
    total_volume: int
    total_open_interest: int
    unsolved_reasons: dict[str, int]


class ChainResponse(BaseModel):
    ticker: str
    expiry: date
    spot: float
    as_of: datetime
    provider: str
    risk_free_rate: float
    dividend_yield: float
    time_to_expiry: float
    available_expiries: list[date]
    summary: ChainSummary
    contracts: list[ContractOut]
    disclaimer: str = DISCLAIMER


class IVRankResponse(BaseModel):
    """Always returned, even when no rank can be produced.

    `rank` is null until enough history exists. `reason` is written to be shown
    to a user as-is: "insufficient history (3/20 days)".
    """

    ticker: str
    rank: float | None
    percentile: float | None
    current_iv: float | None
    iv_min: float | None
    iv_max: float | None
    iv_mean: float | None
    days_available: int
    days_required: int
    window_days: int
    status: str
    reason: str
    first_observed: date | None
    last_observed: date | None
    disclaimer: str = DISCLAIMER


class WatchlistItem(BaseModel):
    symbol: str
    active: bool
    note: str | None = None
    stored_days: int = Field(description="Snapshot days accumulated for this symbol.")
    first_snapshot: date | None
    last_snapshot: date | None


class WatchlistResponse(BaseModel):
    items: list[WatchlistItem]


class WatchlistAddRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    note: str | None = Field(default=None, max_length=256)

    @field_validator("symbol")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return value.strip().upper()


class LegIn(BaseModel):
    """One leg of a position.

    `quantity` is signed -- negative is short. `premium` is always positive; the
    direction of the cash flow comes from the quantity, so a short leg's credit
    does not need the caller to negate anything.
    """

    kind: Literal["call", "put", "stock"]
    quantity: int = Field(description="Signed: positive long, negative short.")
    premium: float = Field(ge=0, description="Paid or received per share, always positive.")
    strike: float | None = Field(default=None, gt=0)
    volatility: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Implied vol for the today-curve. Omit and the service solves it from "
            "the live chain; if it cannot, the today curve is omitted with a reason."
        ),
    )

    @field_validator("quantity")
    @classmethod
    def _non_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("leg quantity must be non-zero")
        return value


class PayoffRequest(BaseModel):
    legs: list[LegIn] = Field(min_length=1, max_length=8)
    ticker: str | None = Field(
        default=None, description="Used to look up spot and solve missing leg volatilities."
    )
    spot: float | None = Field(default=None, gt=0, description="Overrides the looked-up spot.")
    expiry: date | None = Field(
        default=None, description="Sets time to expiry for the today curve."
    )
    time_to_expiry: float | None = Field(default=None, ge=0, description="Years; overrides expiry.")
    points: int = Field(default=201, ge=3, le=1001)
    price_range: tuple[float, float] | None = None

    @field_validator("ticker")
    @classmethod
    def _normalise(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None


class PayoffResponse(BaseModel):
    underlying_prices: list[float]
    pnl_at_expiry: list[float]
    pnl_today: list[float] | None = Field(
        None, description="Null when a leg's volatility is unknown; see today_unavailable_reason."
    )
    today_unavailable_reason: str | None = None
    net_cost: float = Field(description="Positive is a net debit paid, negative a net credit.")
    breakevens: list[float]
    max_profit: float | None = Field(
        None, description="Null means genuinely unlimited (the underlying can rise without bound)."
    )
    max_loss: float | None = Field(None, description="Null means genuinely unlimited.")
    unlimited_profit: bool
    unlimited_loss: bool
    spot: float | None
    time_to_expiry: float
    disclaimer: str = DISCLAIMER


class SnapshotResponse(BaseModel):
    ticker: str
    snapshot_date: date
    contracts_written: int
    contracts_solved: int
    solve_rate: float
    expiries: list[date]
    atm_iv_30d: float | None
    created: bool = Field(
        description="False means an existing day was refreshed in place, not duplicated."
    )
    errors: list[str]


class ScreenHitOut(BaseModel):
    """One flagged symbol. Descriptive — not a recommendation to trade."""

    ticker: str
    flags: list[str]
    summary: str
    iv_rank: float | None
    current_iv: float | None
    volume_today: int | None
    volume_median: float | None
    volume_multiple: float | None
    open_interest_today: int | None
    open_interest_median: float | None
    days_to_earnings: int | None


class ScreenResponse(BaseModel):
    """`screened` is reported alongside `hits` on purpose.

    An empty result set could otherwise mean "nothing was flagged" or "nothing
    was checked", and those are very different. `notes` carries the symbols that
    could not be screened and why.
    """

    screened: int
    hits: list[ScreenHitOut]
    notes: list[str]
    thresholds: dict[str, float]
    disclaimer: str = DISCLAIMER


class EarningsResponse(BaseModel):
    """`next_earnings` is null when unknown — which is not the same as "none"."""

    ticker: str
    next_earnings: date | None
    days_away: int | None
    known: bool = Field(
        description="False means the source had no date, not that none is scheduled."
    )
