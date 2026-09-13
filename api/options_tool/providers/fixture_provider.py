"""A provider that replays a chain captured from a real vendor response.

This exists for two reasons, and the second one matters more than it looks:

1. **The test suite must pass with the network off** (SPEC.md section 8). Sockets
   are disabled in `pytest` config, so every test that needs a chain gets one
   from here.
2. **The demo has to work when Yahoo does not.** The unofficial endpoint
   rate-limits unpredictably -- it did so while this project was being planned.
   `--provider fixture` means a reviewer who clones the repo sees a working tool
   on the first run instead of a stack trace from someone else's outage.

The fixture is a real capture, not invented numbers. Its provenance is recorded
in the file's own `captured_at` / `source` fields, and the prices are stale by
construction -- that is fine, because nothing here claims to be live.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from options_tool.providers.base import (
    ExpiryNotFoundError,
    MarketDataProvider,
    OptionChain,
    OptionQuote,
    Quote,
    UnknownTickerError,
)

__all__ = ["FixtureProvider", "DEFAULT_FIXTURE_PATH"]

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_FIXTURE_PATH = _PACKAGE_ROOT / "tests" / "fixtures" / "chains.json"


class FixtureProvider(MarketDataProvider):
    """Replays captured chains. Deterministic, offline, and honest about being stale."""

    name = "fixture"

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_FIXTURE_PATH
        if not self.path.exists():
            raise FileNotFoundError(
                f"fixture file not found: {self.path}. "
                "Regenerate it with `python -m options_tool capture-fixture`."
            )
        with self.path.open() as handle:
            self._data = json.load(handle)
        self._captured_at = datetime.fromisoformat(self._data["captured_at"])

    @property
    def captured_at(self) -> datetime:
        """When this data was pulled from the vendor. Callers should surface it."""
        return self._captured_at

    def _underlying(self, ticker: str) -> dict[str, Any]:
        symbol = ticker.upper()
        try:
            return self._data["underlyings"][symbol]  # type: ignore[no-any-return]
        except KeyError:
            available = ", ".join(sorted(self._data["underlyings"]))
            raise UnknownTickerError(
                f"{symbol} is not in the fixture. Captured tickers: {available}"
            ) from None

    def get_quote(self, ticker: str) -> Quote:
        underlying = self._underlying(ticker)
        return Quote(
            ticker=ticker.upper(),
            price=float(underlying["spot"]),
            as_of=self._captured_at,
            currency=underlying.get("currency", "USD"),
            previous_close=underlying.get("previous_close"),
        )

    def get_expiries(self, ticker: str) -> tuple[date, ...]:
        underlying = self._underlying(ticker)
        return tuple(sorted(date.fromisoformat(key) for key in underlying["chains"]))

    def get_chain(self, ticker: str, expiry: date) -> OptionChain:
        symbol = ticker.upper()
        underlying = self._underlying(symbol)
        rows = underlying["chains"].get(expiry.isoformat())
        if rows is None:
            captured = self.get_expiries(symbol)
            raise ExpiryNotFoundError(
                f"{symbol}: fixture has no chain for {expiry}. Captured expiries: "
                + ", ".join(str(e) for e in captured),
                available=captured,
            )

        quotes = tuple(
            OptionQuote(
                ticker=symbol,
                expiry=expiry,
                strike=float(row["strike"]),
                right=row["right"],
                bid=row.get("bid"),
                ask=row.get("ask"),
                last=row.get("last"),
                volume=row.get("volume"),
                open_interest=row.get("open_interest"),
                contract_symbol=row.get("contract_symbol"),
            )
            for row in rows
        )
        return OptionChain(
            ticker=symbol,
            expiry=expiry,
            spot=float(underlying["spot"]),
            as_of=self._captured_at,
            quotes=quotes,
        )
