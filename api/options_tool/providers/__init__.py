"""Market-data providers and the factory that picks one.

Import providers from here rather than reaching into the submodules, so the
lazy-import discipline in `yfinance_provider` keeps working.
"""

from __future__ import annotations

from options_tool.providers.base import (
    ExpiryNotFoundError,
    MarketDataProvider,
    OptionChain,
    OptionQuote,
    ProviderError,
    Quote,
    RateLimitedError,
    UnknownTickerError,
)

__all__ = [
    "ExpiryNotFoundError",
    "MarketDataProvider",
    "OptionChain",
    "OptionQuote",
    "ProviderError",
    "Quote",
    "RateLimitedError",
    "UnknownTickerError",
    "get_provider",
]


def get_provider(name: str | None = None) -> MarketDataProvider:
    """Build the named provider, defaulting to the configured one.

    Swapping vendors is this function plus one new module -- nothing in
    `analytics/`, `db/` or `api/` refers to a vendor by name.
    """
    from options_tool.config import get_settings  # noqa: PLC0415 -- avoids an import cycle

    resolved = (name or get_settings().options_provider).strip().lower()

    if resolved == "yfinance":
        from options_tool.providers.yfinance_provider import YFinanceProvider  # noqa: PLC0415

        return YFinanceProvider()
    if resolved == "fixture":
        from options_tool.providers.fixture_provider import FixtureProvider  # noqa: PLC0415

        return FixtureProvider()

    raise ValueError(f"unknown provider {resolved!r}; expected 'yfinance' or 'fixture'")
