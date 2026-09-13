"""CSV export and the earnings flag."""

from __future__ import annotations

import csv
import sys
import types
from datetime import date
from pathlib import Path

import pytest

from options_tool.cli import main
from options_tool.providers.fixture_provider import FixtureProvider
from options_tool.providers.yfinance_provider import YFinanceProvider


def run(*args: str) -> int:
    return main(["--provider", "fixture", *args])


@pytest.fixture
def seeded(settings: object, capsys: pytest.CaptureFixture[str]) -> None:
    run("watchlist", "--add", "AAPL")
    run("snapshot", "AAPL")
    capsys.readouterr()


class TestExport:
    def test_daily_export(self, seeded: None, tmp_path: Path) -> None:
        target = tmp_path / "daily.csv"
        assert run("export", "--out", str(target)) == 0

        rows = list(csv.DictReader(target.open()))
        assert len(rows) == 1
        assert rows[0]["ticker"] == "AAPL"
        assert float(rows[0]["atm_iv_30d"]) > 0
        # The assumptions travel with the data; without them a stored IV cannot
        # be interpreted by whoever opens this file later.
        assert rows[0]["risk_free_rate"] == "0.04"
        assert rows[0]["provider"] == "fixture"

    def test_contracts_export_carries_our_greeks(self, seeded: None, tmp_path: Path) -> None:
        target = tmp_path / "contracts.csv"
        assert run("export", "AAPL", "--what", "contracts", "--out", str(target)) == 0

        rows = list(csv.DictReader(target.open()))
        assert len(rows) > 100
        solved = [row for row in rows if row["iv"]]
        assert solved
        assert all(row["delta"] for row in solved)

    def test_missing_values_are_empty_not_zero(self, seeded: None, tmp_path: Path) -> None:
        """A blank cell reads as "not available"; a 0 reads as a measurement."""
        target = tmp_path / "contracts.csv"
        run("export", "AAPL", "--what", "contracts", "--out", str(target))

        rows = list(csv.DictReader(target.open()))
        unsolved = [row for row in rows if not row["iv"]]
        assert unsolved, "the capture contains stale deep-ITM quotes"
        for row in unsolved:
            assert row["iv"] == ""
            assert row["delta"] == ""
            assert row["iv_status"] not in ("", "ok")

    def test_creates_missing_directories(self, seeded: None, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "deep" / "out.csv"
        assert run("export", "--out", str(target)) == 0
        assert target.exists()

    def test_export_states_the_greeks_are_ours(
        self, seeded: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("export", "--out", str(tmp_path / "x.csv"))
        assert "computed by this tool" in capsys.readouterr().out

    def test_empty_watchlist_exports_nothing_gracefully(
        self, settings: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run("export", "--out", str(tmp_path / "x.csv")) == 0
        assert "nothing to export" in capsys.readouterr().out

    def test_csv_round_trips_through_a_reader(self, seeded: None, tmp_path: Path) -> None:
        """Guards against an unquoted comma or newline corrupting the file."""
        target = tmp_path / "contracts.csv"
        run("export", "AAPL", "--what", "contracts", "--out", str(target))

        with target.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        widths = {len(row) for row in rows}
        assert len(widths) == 1, "every row must have the same number of fields"


class TestScreenCommand:
    def test_reports_how_many_were_screened(
        self, seeded: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An empty screen must not look like a screen that failed to run."""
        assert run("screen") == 0
        out = capsys.readouterr().out
        assert "screened 1 symbols" in out
        assert "insufficient history" in out

    def test_empty_watchlist(self, settings: object, capsys: pytest.CaptureFixture[str]) -> None:
        assert run("screen") == 0
        assert "watchlist is empty" in capsys.readouterr().out

    def test_csv_output(self, seeded: None, tmp_path: Path) -> None:
        target = tmp_path / "screen.csv"
        assert run("screen", "--csv", str(target)) == 0
        assert target.exists()
        assert "ticker" in target.read_text().splitlines()[0]


class TestEarningsDate:
    def test_the_default_provider_says_unknown_rather_than_raising(
        self, provider: FixtureProvider
    ) -> None:
        """Unknown is a valid answer; a missing date must not break the chain."""
        assert provider.get_next_earnings_date("AAPL") is None

    @pytest.mark.parametrize(
        "calendar",
        [
            {"Earnings Date": [date(2026, 10, 30)]},
            {"Earnings Date": date(2026, 10, 30)},
            {"earningsDate": ["2026-10-30"]},
        ],
    )
    def test_parses_the_shapes_yahoo_returns(
        self, calendar: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Ticker:
            def __init__(self, symbol: str) -> None:
                self.calendar = calendar

        module = types.ModuleType("yfinance")
        module.Ticker = Ticker  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "yfinance", module)

        assert YFinanceProvider().get_next_earnings_date("AAPL") == date(2026, 10, 30)

    @pytest.mark.parametrize(
        "calendar", [{}, {"Earnings Date": []}, {"Earnings Date": ["not-a-date"]}, None]
    )
    def test_unusable_calendars_yield_none_not_an_exception(
        self, calendar: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Ticker:
            def __init__(self, symbol: str) -> None:
                self.calendar = calendar

        module = types.ModuleType("yfinance")
        module.Ticker = Ticker  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "yfinance", module)

        assert YFinanceProvider().get_next_earnings_date("AAPL") is None

    def test_a_raising_calendar_does_not_propagate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Losing a decorative flag is fine; losing the chain because of it is not."""

        class Ticker:
            def __init__(self, symbol: str) -> None:
                pass

            @property
            def calendar(self) -> dict:
                raise RuntimeError("Yahoo changed the endpoint again")

        module = types.ModuleType("yfinance")
        module.Ticker = Ticker  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "yfinance", module)

        assert YFinanceProvider().get_next_earnings_date("AAPL") is None
