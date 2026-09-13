"""Market data from Yahoo Finance via the `yfinance` package.

This is an **unofficial scraper of a consumer website**, not a licensed feed. It
breaks without warning, it rate-limits, and its schema is whatever Yahoo shipped
this week. That is a deliberate, documented trade-off (free, no key, has chains)
and the reason `MarketDataProvider` exists at all -- see README "Limitations".

Two things are load-bearing here:

1. `yfinance` is imported **inside** the methods. The test suite runs with
   sockets disabled and must never so much as import a networking library by
   accident, and importing yfinance at module scope would drag it into every
   `from options_tool.providers import ...` in the project.
2. The vendor's `impliedVolatility`, `inTheMoney` and greek columns are read and
   then **dropped on the floor**. We compute those ourselves (SPEC.md section 4).
"""

from __future__ import annotations

import logging
import math
import random
import time
from datetime import UTC, date, datetime
from typing import Any

from options_tool.providers.base import (
    MarketDataProvider,
    OptionChain,
    OptionQuote,
    ProviderError,
    Quote,
    RateLimitedError,
    UnknownTickerError,
)

__all__ = ["YFinanceProvider"]

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 1.5

# Substrings Yahoo puts in throttling errors. Matching on message text is ugly,
# but yfinance flattens HTTP status into generic exceptions, so there is nothing
# structured to match on.
_RATE_LIMIT_MARKERS = ("too many requests", "rate limit", "429", "invalid crumb", "unauthorized")


def _clean(value: Any) -> float | None:
    """Yahoo uses NaN, None and 0.0 interchangeably for 'no quote'. Normalise."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _clean_int(value: Any) -> int | None:
    number = _clean(value)
    return None if number is None else int(number)


def _is_rate_limit(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _RATE_LIMIT_MARKERS)


class YFinanceProvider(MarketDataProvider):
    """Yahoo Finance, with retries around its throttling."""

    name = "yfinance"

    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    # -- vendor plumbing ----------------------------------------------------

    def _ticker(self, ticker: str) -> Any:
        import yfinance  # noqa: PLC0415 -- see module docstring

        return yfinance.Ticker(ticker.upper())

    def _with_retries(self, what: str, fn: Any) -> Any:
        """Run `fn`, retrying throttling with exponential backoff and jitter.

        Only rate limits are retried. A missing ticker or a schema change will
        not fix itself by waiting, so those fail immediately with a real reason.
        """
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 -- yfinance raises bare Exception
                last = exc
                if not _is_rate_limit(exc) or attempt == self.max_retries:
                    break
                delay = self.backoff_seconds * (2 ** (attempt - 1)) * (1 + random.random() * 0.25)
                logger.warning(
                    "yahoo throttled %s (attempt %d/%d), retrying in %.1fs",
                    what,
                    attempt,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)

        assert last is not None
        if _is_rate_limit(last):
            raise RateLimitedError(
                f"Yahoo rate-limited the request for {what} after {self.max_retries} attempts. "
                "This is normal for the unofficial endpoint; wait a minute and retry, "
                "or use --provider fixture."
            ) from last
        raise ProviderError(f"Yahoo lookup failed for {what}: {last}") from last

    # -- MarketDataProvider -------------------------------------------------

    def get_quote(self, ticker: str) -> Quote:
        symbol = ticker.upper()

        def fetch() -> dict[str, Any]:
            fast = self._ticker(symbol).fast_info
            return {
                "price": fast.get("lastPrice") or fast.get("last_price"),
                "previous_close": fast.get("previousClose") or fast.get("previous_close"),
                "currency": fast.get("currency") or "USD",
            }

        raw = self._with_retries(f"quote {symbol}", fetch)
        price = _clean(raw.get("price"))
        if price is None or price <= 0:
            raise UnknownTickerError(f"{symbol}: Yahoo returned no usable price")

        return Quote(
            ticker=symbol,
            price=price,
            as_of=datetime.now(UTC),
            currency=str(raw.get("currency") or "USD"),
            previous_close=_clean(raw.get("previous_close")),
        )

    def get_expiries(self, ticker: str) -> tuple[date, ...]:
        symbol = ticker.upper()
        raw = self._with_retries(f"expiries {symbol}", lambda: self._ticker(symbol).options)
        if not raw:
            raise UnknownTickerError(f"{symbol}: Yahoo lists no option expiries")
        return tuple(sorted(date.fromisoformat(str(value)) for value in raw))

    def get_next_earnings_date(self, ticker: str) -> date | None:
        """Next earnings date from Yahoo's calendar, if it has one.

        Wrapped broadly on purpose: this is a decorative flag on the chain view,
        and Yahoo's calendar endpoint is the flakiest part of an already
        unofficial source. Losing the flag is acceptable; losing the chain
        because the flag failed is not.
        """
        symbol = ticker.upper()
        try:
            calendar = self._ticker(symbol).calendar
        except Exception as exc:  # noqa: BLE001 -- an optional extra, never fatal
            logger.info("no earnings calendar for %s: %s", symbol, exc)
            return None

        raw = None
        if isinstance(calendar, dict):
            raw = calendar.get("Earnings Date") or calendar.get("earningsDate")
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        if raw is None:
            return None

        try:
            if isinstance(raw, date) and not isinstance(raw, datetime):
                return raw
            if isinstance(raw, datetime):
                return raw.date()
            return date.fromisoformat(str(raw)[:10])
        except (ValueError, TypeError):
            logger.info("unparseable earnings date for %s: %r", symbol, raw)
            return None

    def get_chain(self, ticker: str, expiry: date) -> OptionChain:
        symbol = ticker.upper()
        chain = self._with_retries(
            f"chain {symbol} {expiry}",
            lambda: self._ticker(symbol).option_chain(expiry.isoformat()),
        )

        spot = self._spot_from_chain(chain, symbol)
        quotes: list[OptionQuote] = []
        for right, frame in (("call", chain.calls), ("put", chain.puts)):
            quotes.extend(self._rows_to_quotes(frame, symbol, expiry, right))

        if not quotes:
            raise ProviderError(f"{symbol} {expiry}: Yahoo returned an empty chain")

        return OptionChain(
            ticker=symbol,
            expiry=expiry,
            spot=spot,
            as_of=datetime.now(UTC),
            quotes=tuple(quotes),
        )

    def _spot_from_chain(self, chain: Any, symbol: str) -> float:
        """Prefer the spot Yahoo embedded in the chain response.

        It is the price the quotes were struck against; a second call to
        `get_quote` could land on the other side of a tick and make every
        computed greek subtly inconsistent with its own chain.
        """
        underlying = getattr(chain, "underlying", None)
        if isinstance(underlying, dict):
            for key in ("regularMarketPrice", "last_price", "lastPrice"):
                spot = _clean(underlying.get(key))
                if spot is not None and spot > 0:
                    return spot
        return self.get_quote(symbol).price

    @staticmethod
    def _rows_to_quotes(frame: Any, symbol: str, expiry: date, right: str) -> list[OptionQuote]:
        if frame is None or frame.empty:
            return []
        quotes = []
        for row in frame.to_dict("records"):
            strike = _clean(row.get("strike"))
            if strike is None or strike <= 0:
                continue  # a strike-less row is corrupt, not merely unquoted
            quotes.append(
                OptionQuote(
                    ticker=symbol,
                    expiry=expiry,
                    strike=strike,
                    right=right,
                    bid=_clean(row.get("bid")),
                    ask=_clean(row.get("ask")),
                    last=_clean(row.get("lastPrice")),
                    volume=_clean_int(row.get("volume")),
                    open_interest=_clean_int(row.get("openInterest")),
                    contract_symbol=row.get("contractSymbol"),
                )
            )
        return quotes
