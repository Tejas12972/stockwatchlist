"""The market-data boundary.

Everything downstream -- analytics, storage, API, UI -- talks to these
dataclasses and never to a vendor object. That is the whole contract: swapping
yfinance for Polygon or Tradier means writing one new file in this package and
changing one setting, and nothing in `analytics/` or `api/` notices.

It also keeps the vendor's own `impliedVolatility` and greek columns from
leaking into the system by construction. `OptionQuote` has nowhere to put them,
so the only implied vol that can ever reach the database is the one solved for
in `analytics.implied_vol` (SPEC.md section 4).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

__all__ = [
    "ProviderError",
    "RateLimitedError",
    "UnknownTickerError",
    "ExpiryNotFoundError",
    "Quote",
    "OptionQuote",
    "OptionChain",
    "MarketDataProvider",
]


class ProviderError(RuntimeError):
    """A market-data lookup failed. Always raised with a human-readable reason."""


class RateLimitedError(ProviderError):
    """The vendor throttled us. Distinct because it is retryable and transient."""


class UnknownTickerError(ProviderError):
    """The vendor has no such symbol. A 404, not a 503."""


class ExpiryNotFoundError(ProviderError):
    """The symbol exists but is not listed for that expiry.

    Separate from `ProviderError` because it is the *caller's* mistake, not an
    upstream failure -- it deserves a 404 carrying the available expiries, not a
    502 that implies the vendor is down.
    """

    def __init__(self, message: str, available: tuple[date, ...] = ()) -> None:
        super().__init__(message)
        self.available = available


@dataclass(frozen=True, slots=True)
class Quote:
    """A spot quote for an underlying."""

    ticker: str
    price: float
    as_of: datetime
    currency: str = "USD"
    previous_close: float | None = None


@dataclass(frozen=True, slots=True)
class OptionQuote:
    """One listed contract, as the vendor reports it.

    Prices only. No greeks and no implied volatility -- see the module docstring.
    """

    ticker: str
    expiry: date
    strike: float
    right: str  # "call" | "put"
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    contract_symbol: str | None = None

    @property
    def mid(self) -> float | None:
        """Mid of a two-sided book, else None.

        Deliberately strict: a one-sided or crossed book has no mid, and
        substituting `last` here would quietly mix a stale print into a live
        quote. `analytics.chain` handles that fallback explicitly and flags it.
        """
        if self.bid is None or self.ask is None:
            return None
        if self.bid <= 0.0 or self.ask <= 0.0 or self.ask < self.bid:
            return None
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True, slots=True)
class OptionChain:
    """Every listed contract for one underlying and one expiry, plus its spot."""

    ticker: str
    expiry: date
    spot: float
    as_of: datetime
    quotes: tuple[OptionQuote, ...]

    def __len__(self) -> int:
        return len(self.quotes)


class MarketDataProvider(ABC):
    """Interface every market-data source implements."""

    name: str = "base"

    @abstractmethod
    def get_quote(self, ticker: str) -> Quote:
        """Spot quote for `ticker`. Raises UnknownTickerError if it does not exist."""

    @abstractmethod
    def get_expiries(self, ticker: str) -> tuple[date, ...]:
        """Listed option expiries for `ticker`, ascending."""

    @abstractmethod
    def get_chain(self, ticker: str, expiry: date) -> OptionChain:
        """Full option chain for one `ticker` and `expiry`."""

    def get_next_earnings_date(self, ticker: str) -> date | None:  # noqa: ARG002
        """Next scheduled earnings date, or None if unknown.

        Optional: the default says "unknown" rather than raising, because no
        free source publishes reliable forward earnings dates for every symbol
        and a provider that cannot supply one should not break the chain view.
        Callers must treat None as "no information", never as "no earnings".
        """
        return None

    def resolve_expiry(self, ticker: str, expiry: date | None) -> date:
        """Pick an expiry: the one given, or the nearest listed one if None.

        Shared by every provider so `--expiry` stays optional on the CLI without
        each implementation reinventing the default.
        """
        expiries = self.get_expiries(ticker)
        if not expiries:
            raise ProviderError(f"{ticker}: no listed option expiries")
        if expiry is None:
            return expiries[0]
        if expiry not in expiries:
            shown = ", ".join(str(e) for e in expiries[:8])
            raise ExpiryNotFoundError(
                f"{ticker.upper()}: no listed chain for expiry {expiry}. "
                f"Available: {shown}" + (" ..." if len(expiries) > 8 else ""),
                available=expiries,
            )
        return expiry
