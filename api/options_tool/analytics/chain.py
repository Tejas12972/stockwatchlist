"""Turn a provider's option chain into a tidy, typed Pandas frame.

One row per contract, every column named and typed, greeks and implied vol
computed here rather than read from the vendor. This frame is the single shape
that the CLI, the snapshot writer and the API all consume -- normalising once,
here, is what keeps vendor quirks from leaking into three places.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from options_tool.analytics.black_scholes import Right, greeks
from options_tool.analytics.implied_vol import IVStatus, solve_implied_vol
from options_tool.providers.base import OptionChain, OptionQuote

__all__ = [
    "CHAIN_COLUMNS",
    "MARKET_TZ",
    "market_date",
    "ChainStats",
    "build_chain_frame",
    "time_to_expiry",
    "summarise_chain",
]

# US equity options stop trading at 16:00 New York time on the expiry date.
_MARKET_CLOSE = (16, 0)
MARKET_TZ = ZoneInfo("America/New_York")
_SECONDS_PER_YEAR = 365.0 * 24 * 60 * 60


def market_date(moment: datetime | None = None) -> date:
    """The US trading date a moment belongs to.

    Deliberately *not* the UTC date. Snapshots are keyed by day, and after 20:00
    Eastern the UTC date has already rolled over -- so a job run at 15:00 and a
    retry at 20:30 on the same trading afternoon would be filed under two
    different dates and stored as two rows, defeating the idempotency guarantee
    in precisely the situation a cron timer creates.
    """
    moment = moment or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(MARKET_TZ).date()


CHAIN_COLUMNS: dict[str, str] = {
    "ticker": "string",
    "expiry": "datetime64[ns]",
    "right": "string",
    "strike": "float64",
    "spot": "float64",
    "bid": "float64",
    "ask": "float64",
    "last": "float64",
    "mid": "float64",
    "price": "float64",
    "price_source": "string",
    "spread": "float64",
    "volume": "Int64",
    "open_interest": "Int64",
    "time_to_expiry": "float64",
    "moneyness": "float64",
    "in_the_money": "boolean",
    "iv": "float64",
    "iv_status": "string",
    "iv_method": "string",
    "delta": "float64",
    "gamma": "float64",
    "vega": "float64",
    "theta": "float64",
    "rho": "float64",
}
"""The contract of this module. Order is the display order; dtypes are enforced.

`Int64`/`boolean` (capitalised) are the nullable pandas dtypes -- an option with
no reported volume must stay null, not silently become 0, because 0 volume is a
real and different observation.
"""


def time_to_expiry(expiry: date, as_of: datetime | None = None) -> float:
    """Years until the 16:00 New York close on `expiry`. Never negative.

    Calendar time, not trading time: an option decays over a weekend. Using whole
    days instead would price every 0-DTE contract at exactly zero and throw away
    the one day of the front expiry that traders care most about.
    """
    as_of = as_of or datetime.now(UTC)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    close = datetime(expiry.year, expiry.month, expiry.day, *_MARKET_CLOSE, tzinfo=MARKET_TZ)
    return max((close - as_of).total_seconds() / _SECONDS_PER_YEAR, 0.0)


def _resolve_price(quote: OptionQuote) -> tuple[float | None, str | None]:
    """Pick the price to imply vol from, and say where it came from.

    Mid of a two-sided book first. A one-sided or crossed book falls back to the
    last trade, flagged, because a stale print is materially weaker evidence than
    a live market -- and the UI should be able to show which it was.
    """
    mid = quote.mid
    if mid is not None and mid > 0:
        return mid, "mid"
    if quote.last is not None and quote.last > 0:
        return quote.last, "last"
    return None, None


def build_chain_frame(
    chain: OptionChain,
    risk_free_rate: float,
    dividend_yield: float = 0.0,
    as_of: datetime | None = None,
) -> pd.DataFrame:
    """Normalise `chain` into the frame described by `CHAIN_COLUMNS`.

    Rows whose implied vol could not be solved keep their quote data and carry a
    null `iv` plus an `iv_status` saying why -- they are never dropped, because a
    strike that cannot be priced is itself information (SPEC.md section 4).
    """
    as_of = as_of or chain.as_of or datetime.now(UTC)
    T = time_to_expiry(chain.expiry, as_of)
    spot = chain.spot

    records = []
    for quote in chain.quotes:
        right = Right.parse(quote.right)
        price, price_source = _resolve_price(quote)

        if price is None:
            iv_result = None
            iv_status, iv_method = IVStatus.NO_QUOTE, ""
        else:
            iv_result = solve_implied_vol(
                price, spot, quote.strike, T, risk_free_rate, dividend_yield, right
            )
            iv_status, iv_method = iv_result.status, iv_result.method

        sigma = iv_result.sigma if iv_result is not None else None
        # Greeks are only meaningful at the vol the market is actually pricing.
        # Substituting a default sigma here would manufacture a delta for a
        # contract that has no tradeable market -- exactly the fabrication this
        # project exists to avoid.
        g = (
            greeks(spot, quote.strike, T, risk_free_rate, sigma, dividend_yield, right)
            if sigma is not None
            else None
        )

        in_the_money = spot > quote.strike if right is Right.CALL else spot < quote.strike
        spread = (
            quote.ask - quote.bid
            if quote.bid is not None and quote.ask is not None and quote.ask >= quote.bid
            else None
        )

        records.append(
            {
                "ticker": chain.ticker,
                "expiry": pd.Timestamp(chain.expiry),
                "right": right.value,
                "strike": quote.strike,
                "spot": spot,
                "bid": quote.bid,
                "ask": quote.ask,
                "last": quote.last,
                "mid": quote.mid,
                "price": price,
                "price_source": price_source,
                "spread": spread,
                "volume": quote.volume,
                "open_interest": quote.open_interest,
                "time_to_expiry": T,
                "moneyness": spot / quote.strike,
                "in_the_money": in_the_money,
                "iv": sigma,
                "iv_status": iv_status.value,
                "iv_method": iv_method,
                "delta": g.delta if g else None,
                "gamma": g.gamma if g else None,
                "vega": g.vega if g else None,
                "theta": g.theta if g else None,
                "rho": g.rho if g else None,
            }
        )

    frame = pd.DataFrame.from_records(records, columns=list(CHAIN_COLUMNS))
    frame = frame.astype(CHAIN_COLUMNS)
    frame = frame.sort_values(["right", "strike"], ignore_index=True)
    # Attributes rather than columns: they describe the chain, not any one row.
    frame.attrs.update(
        {
            "ticker": chain.ticker,
            "expiry": chain.expiry,
            "spot": spot,
            "as_of": as_of,
            "risk_free_rate": risk_free_rate,
            "dividend_yield": dividend_yield,
            "time_to_expiry": T,
        }
    )
    return frame


@dataclass(frozen=True, slots=True)
class ChainStats:
    """Headline numbers for a chain, including how much of it failed to solve."""

    ticker: str
    expiry: date | None
    spot: float
    time_to_expiry: float
    contracts: int
    solved: int
    solve_rate: float
    atm_iv: float | None
    total_volume: int
    total_open_interest: int
    unsolved_reasons: dict[str, int] = field(default_factory=dict)


def summarise_chain(frame: pd.DataFrame) -> ChainStats:
    """Reduce a chain frame to its headline numbers.

    The solve rate is reported on purpose. A chain where a third of the strikes
    have no implied vol is a chain you should not draw conclusions from, and
    hiding that behind a tidy-looking table is how a tool starts lying.
    """
    solved = frame["iv"].notna()
    atm = frame.loc[solved].copy()
    atm_iv = None
    if not atm.empty:
        # Nearest-to-the-money solved strike on each side, averaged: a cheap,
        # transparent ATM vol. Not an interpolated surface -- and not claimed to
        # be. `analytics.atm_iv` does the careful version for IV rank.
        atm["distance"] = (atm["moneyness"] - 1.0).abs()
        nearest = atm.sort_values("distance").groupby("right", observed=True).head(1)
        atm_iv = float(nearest["iv"].mean())

    failures = frame.loc[~solved, "iv_status"].value_counts().to_dict()
    return ChainStats(
        ticker=str(frame.attrs.get("ticker", "")),
        expiry=frame.attrs.get("expiry"),
        spot=float(frame.attrs.get("spot", 0.0)),
        time_to_expiry=float(frame.attrs.get("time_to_expiry", 0.0)),
        contracts=len(frame),
        solved=int(solved.sum()),
        solve_rate=float(solved.mean()) if len(frame) else 0.0,
        atm_iv=atm_iv,
        total_volume=int(frame["volume"].sum(skipna=True) or 0),
        total_open_interest=int(frame["open_interest"].sum(skipna=True) or 0),
        unsolved_reasons={str(k): int(v) for k, v in failures.items()},
    )
