"""Capture live vendor chains into the offline fixture.

Run occasionally to refresh `tests/fixtures/chains.json`. The fixture is checked
in deliberately: it is what makes `pytest` pass with no network and what makes
`--provider fixture` a working demo. Refreshing it is a conscious act with a
visible diff, not something that happens silently at test time.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from options_tool.providers.base import MarketDataProvider

__all__ = ["capture_fixture"]


def capture_fixture(
    provider: MarketDataProvider,
    tickers: list[str],
    path: Path,
    expiries_per_ticker: int = 3,
) -> dict[str, int]:
    """Write a fixture covering `tickers`. Returns contracts captured per ticker."""
    underlyings: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}

    for raw_ticker in tickers:
        ticker = raw_ticker.upper()
        quote = provider.get_quote(ticker)
        all_expiries = provider.get_expiries(ticker)
        # Front, middle and back of the listed term structure, so the fixture
        # exercises near-expiry (tiny T) and LEAPS (large T) code paths rather
        # than three near-identical weeklies.
        chosen = _spread_expiries(all_expiries, expiries_per_ticker)

        chains: dict[str, list[dict[str, Any]]] = {}
        total = 0
        for expiry in chosen:
            chain = provider.get_chain(ticker, expiry)
            chains[expiry.isoformat()] = [
                {
                    "strike": q.strike,
                    "right": q.right,
                    "bid": q.bid,
                    "ask": q.ask,
                    "last": q.last,
                    "volume": q.volume,
                    "open_interest": q.open_interest,
                    "contract_symbol": q.contract_symbol,
                }
                for q in chain.quotes
            ]
            total += len(chain.quotes)

        underlyings[ticker] = {
            "spot": quote.price,
            "currency": quote.currency,
            "previous_close": quote.previous_close,
            "chains": chains,
        }
        counts[ticker] = total

    payload = {
        "source": provider.name,
        "captured_at": datetime.now(UTC).isoformat(),
        "note": (
            "Real vendor capture, frozen for offline tests and the --provider fixture demo. "
            "Prices are stale by construction and are not represented as live market data."
        ),
        "underlyings": underlyings,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    return counts


def _spread_expiries(expiries: tuple[date, ...], count: int) -> list[date]:
    """Pick `count` expiries spread across the term structure, not just the front."""
    if len(expiries) <= count:
        return list(expiries)
    if count == 1:
        return [expiries[0]]
    step = (len(expiries) - 1) / (count - 1)
    return [expiries[round(i * step)] for i in range(count)]
