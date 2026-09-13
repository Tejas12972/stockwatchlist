"""Screen stored snapshots for the things worth a second look.

Two signals, both computed from the history this tool has accumulated rather
than from a single day's chain:

- **elevated or depressed IV rank** -- where today's volatility sits against its
  own past
- **unusual volume or open interest** -- today's activity against its own
  trailing median

Both are *descriptive*. Neither is a signal to trade, and nothing here ranks
opportunities or suggests a position -- it narrows a watchlist down to the names
where something changed. The median is used rather than the mean because option
volume is heavily skewed: one expiry-week spike would drag a mean upward for
weeks and suppress every subsequent reading.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["ScreenFlag", "ScreenHit", "ScreenInput", "screen"]

# An IV rank at or above this is "elevated", at or below the other "depressed".
HIGH_IV_RANK = 70.0
LOW_IV_RANK = 30.0

# Today's activity must exceed this multiple of its trailing median to count.
UNUSUAL_ACTIVITY_MULTIPLE = 2.0

# Fewer stored days than this and a median is not worth computing.
MIN_ACTIVITY_HISTORY = 10


class ScreenFlag(StrEnum):
    HIGH_IV_RANK = "high_iv_rank"
    LOW_IV_RANK = "low_iv_rank"
    UNUSUAL_VOLUME = "unusual_volume"
    UNUSUAL_OPEN_INTEREST = "unusual_open_interest"
    NEAR_EARNINGS = "near_earnings"

    @property
    def explanation(self) -> str:
        return _EXPLANATIONS[self]


_EXPLANATIONS: dict[ScreenFlag, str] = {
    ScreenFlag.HIGH_IV_RANK: "implied volatility is high against its own history",
    ScreenFlag.LOW_IV_RANK: "implied volatility is low against its own history",
    ScreenFlag.UNUSUAL_VOLUME: "option volume is well above its trailing median",
    ScreenFlag.UNUSUAL_OPEN_INTEREST: "open interest is well above its trailing median",
    ScreenFlag.NEAR_EARNINGS: "an earnings date falls before the near expiry",
}


@dataclass(frozen=True, slots=True)
class ScreenInput:
    """One symbol's stored state, as the screener needs it."""

    ticker: str
    iv_rank: float | None
    iv_rank_reason: str
    current_iv: float | None
    volume_today: int | None
    volume_history: list[int]
    open_interest_today: int | None
    open_interest_history: list[int]
    days_to_earnings: int | None = None
    days_to_near_expiry: int | None = None


@dataclass(frozen=True, slots=True)
class ScreenHit:
    """A symbol that tripped at least one flag, with the numbers behind it."""

    ticker: str
    flags: list[ScreenFlag]
    iv_rank: float | None
    current_iv: float | None
    volume_today: int | None
    volume_median: float | None
    volume_multiple: float | None
    open_interest_today: int | None
    open_interest_median: float | None
    days_to_earnings: int | None
    notes: list[str]

    @property
    def summary(self) -> str:
        return ", ".join(flag.explanation for flag in self.flags)


def _activity_multiple(today: int | None, history: list[int]) -> tuple[float | None, float | None]:
    """(median, today/median), or (None, None) when there is too little history.

    Returning None rather than a default is the same discipline as everywhere
    else here: with six stored days a "2x the median" reading says more about the
    sample than about the market.
    """
    usable = [value for value in history if value is not None and value >= 0]
    if today is None or len(usable) < MIN_ACTIVITY_HISTORY:
        return None, None

    median = statistics.median(usable)
    if median <= 0:
        # Everything was zero historically; any activity today is technically
        # infinite growth and tells you nothing useful.
        return median, None
    return median, today / median


def screen(
    inputs: list[ScreenInput],
    high_iv_rank: float = HIGH_IV_RANK,
    low_iv_rank: float = LOW_IV_RANK,
    activity_multiple: float = UNUSUAL_ACTIVITY_MULTIPLE,
) -> list[ScreenHit]:
    """Flag symbols worth a look. Descriptive only -- not a recommendation.

    Results are ordered by how many flags tripped, then by IV rank, so the
    busiest names surface first. A symbol with no flags is omitted entirely
    rather than returned with an empty list.
    """
    hits: list[ScreenHit] = []

    for item in inputs:
        flags: list[ScreenFlag] = []
        notes: list[str] = []

        if item.iv_rank is None:
            # Said out loud rather than silently skipped: a symbol absent from
            # the results because it has no history looks identical to one that
            # simply did not trip anything.
            notes.append(f"IV rank unavailable — {item.iv_rank_reason}")
        elif item.iv_rank >= high_iv_rank:
            flags.append(ScreenFlag.HIGH_IV_RANK)
        elif item.iv_rank <= low_iv_rank:
            flags.append(ScreenFlag.LOW_IV_RANK)

        volume_median, volume_multiple = _activity_multiple(item.volume_today, item.volume_history)
        if volume_multiple is not None and volume_multiple >= activity_multiple:
            flags.append(ScreenFlag.UNUSUAL_VOLUME)
        elif volume_median is None:
            notes.append(
                f"volume baseline needs {MIN_ACTIVITY_HISTORY} stored days "
                f"({len(item.volume_history)} so far)"
            )

        oi_median, oi_multiple = _activity_multiple(
            item.open_interest_today, item.open_interest_history
        )
        if oi_multiple is not None and oi_multiple >= activity_multiple:
            flags.append(ScreenFlag.UNUSUAL_OPEN_INTEREST)

        if (
            item.days_to_earnings is not None
            and item.days_to_near_expiry is not None
            and 0 <= item.days_to_earnings <= item.days_to_near_expiry
        ):
            flags.append(ScreenFlag.NEAR_EARNINGS)

        if not flags:
            continue

        hits.append(
            ScreenHit(
                ticker=item.ticker,
                flags=flags,
                iv_rank=item.iv_rank,
                current_iv=item.current_iv,
                volume_today=item.volume_today,
                volume_median=volume_median,
                volume_multiple=volume_multiple,
                open_interest_today=item.open_interest_today,
                open_interest_median=oi_median,
                days_to_earnings=item.days_to_earnings,
                notes=notes,
            )
        )

    return sorted(hits, key=lambda hit: (-len(hit.flags), -(hit.iv_rank or 0.0)))
