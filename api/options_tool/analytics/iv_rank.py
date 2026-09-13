"""IV rank and IV percentile over the locally-accumulated history.

    iv_rank = (iv_today - iv_min) / (iv_max - iv_min) * 100

The formula is trivial. The part that matters is **refusing to compute it when
there isn't enough history.**

This tool starts with an empty database and accumulates one observation a day.
On day 3, `iv_min` and `iv_max` are drawn from three points, so today is
guaranteed to be at 0, 50 or 100 -- a number that looks like a signal and is
pure artefact. Worse, a spurious early minimum stays in the trailing window for
a year and depresses every rank computed against it.

So below a configured minimum (20 days by default) this returns a result whose
`rank` is `None`, carrying a reason and a day count the UI renders as
"insufficient history (3/20 days)". That degraded state is a feature of the
design, not a gap in it (SPEC.md section 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

__all__ = ["IVRankStatus", "IVRankResult", "IVObservation", "compute_iv_rank"]


class IVRankStatus(StrEnum):
    OK = "ok"
    INSUFFICIENT_HISTORY = "insufficient_history"
    NO_CURRENT_IV = "no_current_iv"
    DEGENERATE_RANGE = "degenerate_range"


_EXPLANATIONS: dict[IVRankStatus, str] = {
    IVRankStatus.OK: "ranked against stored history",
    IVRankStatus.INSUFFICIENT_HISTORY: "not enough stored history yet",
    IVRankStatus.NO_CURRENT_IV: "no at-the-money implied volatility for the latest day",
    IVRankStatus.DEGENERATE_RANGE: "implied volatility has not moved across the window",
}


@dataclass(frozen=True, slots=True)
class IVObservation:
    """One stored day of at-the-money implied volatility."""

    observed_on: date
    iv: float


@dataclass(frozen=True, slots=True)
class IVRankResult:
    """`rank` and `percentile` are None unless `status is IVRankStatus.OK`."""

    rank: float | None
    percentile: float | None
    current_iv: float | None
    iv_min: float | None
    iv_max: float | None
    iv_mean: float | None
    days_available: int
    days_required: int
    window_days: int
    status: IVRankStatus
    first_observed: date | None = None
    last_observed: date | None = None

    @property
    def ok(self) -> bool:
        return self.status is IVRankStatus.OK

    @property
    def reason(self) -> str:
        """Plain-language explanation, safe to render verbatim in a UI."""
        if self.status is IVRankStatus.INSUFFICIENT_HISTORY:
            return f"insufficient history ({self.days_available}/{self.days_required} days)"
        return _EXPLANATIONS[self.status]


def compute_iv_rank(
    observations: list[IVObservation],
    window_days: int = 252,
    min_history_days: int = 20,
) -> IVRankResult:
    """Rank the most recent observation against the trailing window.

    Parameters
    ----------
    observations : stored daily ATM implied vols, any order. Days where no ATM
        vol could be established are expected to be absent, not present as zero.
    window_days : how many recent observations to rank against
    min_history_days : below this many observations, refuse to produce a rank
    """
    usable = sorted(
        (o for o in observations if o.iv is not None and o.iv > 0),
        key=lambda o: o.observed_on,
    )
    window = usable[-window_days:]
    days_available = len(window)

    def failed(status: IVRankStatus, current: float | None = None) -> IVRankResult:
        return IVRankResult(
            rank=None,
            percentile=None,
            current_iv=current,
            iv_min=None,
            iv_max=None,
            iv_mean=None,
            days_available=days_available,
            days_required=min_history_days,
            window_days=window_days,
            status=status,
            first_observed=window[0].observed_on if window else None,
            last_observed=window[-1].observed_on if window else None,
        )

    if not window:
        return failed(IVRankStatus.NO_CURRENT_IV)
    if days_available < min_history_days:
        # Deliberately still None even though the arithmetic would succeed.
        return failed(IVRankStatus.INSUFFICIENT_HISTORY, window[-1].iv)

    values = [o.iv for o in window]
    current = values[-1]
    iv_min, iv_max = min(values), max(values)

    if iv_max - iv_min <= 0:
        # A flat window makes the rank 0/0. "Undefined" is the honest answer;
        # convention would say 50, which invents a midpoint that means nothing.
        return failed(IVRankStatus.DEGENERATE_RANGE, current)

    rank = (current - iv_min) / (iv_max - iv_min) * 100.0
    # IV percentile answers a different question from IV rank: what share of days
    # were *below* today, rather than where today sits between the extremes. It
    # is the more robust of the two, because a single outlier day sets the range
    # that rank is measured against but moves percentile by one observation.
    at_or_below = sum(1 for value in values if value <= current)
    percentile = at_or_below / len(values) * 100.0

    return IVRankResult(
        rank=rank,
        percentile=percentile,
        current_iv=current,
        iv_min=iv_min,
        iv_max=iv_max,
        iv_mean=sum(values) / len(values),
        days_available=days_available,
        days_required=min_history_days,
        window_days=window_days,
        status=IVRankStatus.OK,
        first_observed=window[0].observed_on,
        last_observed=window[-1].observed_on,
    )
