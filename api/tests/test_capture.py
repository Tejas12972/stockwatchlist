"""Fixture capture.

The fixture is what makes this suite offline and the demo reliable, so the code
that produces it is tested too -- against the existing fixture as its source, so
no network is involved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from options_tool.providers.capture import _spread_expiries, capture_fixture
from options_tool.providers.fixture_provider import FixtureProvider


class TestSpreadExpiries:
    def test_picks_across_the_term_structure_not_just_the_front(self) -> None:
        """Three near-identical weeklies would not exercise the LEAPS path."""
        expiries = list(range(20))  # type: ignore[arg-type]
        chosen = _spread_expiries(tuple(expiries), 3)  # type: ignore[arg-type]
        assert chosen[0] == 0
        assert chosen[-1] == 19
        assert chosen[1] not in (0, 19)

    def test_returns_everything_when_there_is_little_to_choose_from(self) -> None:
        assert _spread_expiries((1, 2), 5) == [1, 2]  # type: ignore[arg-type,comparison-overlap]

    def test_single_expiry_request(self) -> None:
        assert _spread_expiries((1, 2, 3), 1) == [1]  # type: ignore[arg-type,comparison-overlap]

    def test_no_duplicates(self) -> None:
        chosen = _spread_expiries(tuple(range(10)), 4)  # type: ignore[arg-type]
        assert len(set(chosen)) == len(chosen)


class TestCapture:
    def test_round_trips_through_the_fixture_provider(
        self, provider: FixtureProvider, tmp_path: Path
    ) -> None:
        target = tmp_path / "nested" / "chains.json"
        counts = capture_fixture(provider, ["AAPL"], target, expiries_per_ticker=2)

        assert target.exists()
        assert counts["AAPL"] > 0

        replayed = FixtureProvider(target)
        assert len(replayed.get_expiries("AAPL")) == 2
        assert replayed.get_quote("AAPL").price == pytest.approx(provider.get_quote("AAPL").price)

    def test_records_its_own_provenance(self, provider: FixtureProvider, tmp_path: Path) -> None:
        """A capture that cannot say where or when it came from is not evidence."""
        target = tmp_path / "chains.json"
        capture_fixture(provider, ["AAPL"], target, expiries_per_ticker=1)

        payload = json.loads(target.read_text())
        assert payload["source"] == "fixture"
        assert payload["captured_at"]
        assert "not represented as live market data" in payload["note"]

    def test_no_vendor_greeks_are_written(self, provider: FixtureProvider, tmp_path: Path) -> None:
        target = tmp_path / "chains.json"
        capture_fixture(provider, ["AAPL"], target, expiries_per_ticker=1)

        payload = json.loads(target.read_text())
        chains = payload["underlyings"]["AAPL"]["chains"]
        for rows in chains.values():
            for row in rows:
                assert set(row) == {
                    "strike",
                    "right",
                    "bid",
                    "ask",
                    "last",
                    "volume",
                    "open_interest",
                    "contract_symbol",
                }

    def test_output_is_deterministic(self, provider: FixtureProvider, tmp_path: Path) -> None:
        """Keys are sorted so a refresh produces a readable diff, not a reshuffle."""
        first, second = tmp_path / "a.json", tmp_path / "b.json"
        capture_fixture(provider, ["AAPL"], first, expiries_per_ticker=1)
        capture_fixture(provider, ["AAPL"], second, expiries_per_ticker=1)

        a = json.loads(first.read_text())
        b = json.loads(second.read_text())
        a.pop("captured_at")
        b.pop("captured_at")
        assert a == b

    def test_multiple_tickers(self, provider: FixtureProvider, tmp_path: Path) -> None:
        target = tmp_path / "chains.json"
        counts = capture_fixture(provider, ["AAPL", "spy"], target, expiries_per_ticker=1)
        assert set(counts) == {"AAPL", "SPY"}
        assert set(json.loads(target.read_text())["underlyings"]) == {"AAPL", "SPY"}
