"""CLI behaviour.

The CLI is how the daily snapshot job runs, so its exit codes matter: a cron
timer that cannot tell success from failure will silently stop building the IV
history and nobody will notice for weeks.
"""

from __future__ import annotations

import pytest

from options_tool.cli import build_parser, main


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(["--provider", "fixture", *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def flat(text: str) -> str:
    """Collapse whitespace.

    Rich word-wraps table captions to the terminal width, so a phrase that reads
    as one sentence on screen arrives split across lines with padding. Asserting
    on the wrapped form would make these tests depend on the column count.
    """
    return " ".join(text.split())


class TestParser:
    def test_every_command_is_registered(self) -> None:
        parser = build_parser()
        actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
        commands = set()
        for action in actions:
            if isinstance(action.choices, dict):
                commands |= set(action.choices)
        assert {
            "quote",
            "expiries",
            "chain",
            "watchlist",
            "snapshot",
            "ivrank",
            "capture-fixture",
        } <= commands

    def test_a_command_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_help_exits_cleanly(self) -> None:
        with pytest.raises(SystemExit) as info:
            build_parser().parse_args(["--help"])
        assert info.value.code == 0


class TestReadOnlyCommands:
    def test_quote(self, capsys: pytest.CaptureFixture[str], settings: object) -> None:
        code, out, _ = run(capsys, "quote", "AAPL")
        assert code == 0
        assert "AAPL" in out

    def test_expiries(self, capsys: pytest.CaptureFixture[str], settings: object) -> None:
        code, out, _ = run(capsys, "expiries", "AAPL")
        assert code == 0
        assert "3 listed expiries" in out
        assert "2027-01-15" in out

    def test_chain_prints_self_computed_greeks(
        self, capsys: pytest.CaptureFixture[str], settings: object
    ) -> None:
        code, out, _ = run(
            capsys, "chain", "AAPL", "--expiry", "2027-01-15", "--near-the-money", "3"
        )
        assert code == 0
        assert "delta" in out and "gamma" in out and "vega" in out
        assert "computed locally, not vendor-supplied" in flat(out)

    def test_chain_shows_the_disclaimer(
        self, capsys: pytest.CaptureFixture[str], settings: object
    ) -> None:
        _, out, _ = run(capsys, "chain", "AAPL", "--near-the-money", "2")
        assert "Not investment advice" in flat(out)

    def test_chain_reports_the_solve_rate(
        self, capsys: pytest.CaptureFixture[str], settings: object
    ) -> None:
        _, out, _ = run(capsys, "chain", "AAPL", "--expiry", "2027-01-15")
        assert "strikes solved" in flat(out)

    def test_chain_right_filter(self, capsys: pytest.CaptureFixture[str], settings: object) -> None:
        _, out, _ = run(capsys, "chain", "AAPL", "--right", "put", "--near-the-money", "2")
        assert "put" in out
        assert " call " not in out

    def test_chain_csv_export(
        self, capsys: pytest.CaptureFixture[str], settings: object, tmp_path: object
    ) -> None:
        from pathlib import Path

        target = Path(str(tmp_path)) / "out" / "chain.csv"
        code, _, _ = run(capsys, "chain", "AAPL", "--expiry", "2027-01-15", "--csv", str(target))
        assert code == 0
        assert target.exists()

        header = target.read_text().splitlines()[0]
        assert "iv" in header and "delta" in header and "iv_status" in header


class TestErrorsAreReportedNotRaised:
    def test_unknown_ticker_exits_two_without_a_traceback(
        self, capsys: pytest.CaptureFixture[str], settings: object
    ) -> None:
        code, _, err = run(capsys, "quote", "NOSUCHTICKER")
        assert code == 2
        assert err.startswith("error:")
        assert "Traceback" not in err

    def test_unlisted_expiry_suggests_the_alternatives(
        self, capsys: pytest.CaptureFixture[str], settings: object
    ) -> None:
        code, _, err = run(capsys, "chain", "AAPL", "--expiry", "1999-01-01")
        assert code == 2
        assert "Available" in err

    def test_a_malformed_date_is_rejected_by_the_parser(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["chain", "AAPL", "--expiry", "not-a-date"])


class TestStatefulCommands:
    def test_watchlist_add_list_remove(
        self, capsys: pytest.CaptureFixture[str], settings: object
    ) -> None:
        assert run(capsys, "watchlist", "--add", "AAPL", "SPY")[0] == 0

        _, out, _ = run(capsys, "watchlist")
        assert "AAPL" in out and "SPY" in out
        assert "no snapshots yet" in out

        _, removed_output, _ = run(capsys, "watchlist", "--remove", "SPY")
        assert "removed SPY" in removed_output

        _, out, _ = run(capsys, "watchlist")
        assert "SPY" not in out

    def test_removing_an_untracked_symbol_says_so(
        self, capsys: pytest.CaptureFixture[str], settings: object
    ) -> None:
        _, out, _ = run(capsys, "watchlist", "--remove", "ZZZZ")
        assert "was not tracked" in out

    def test_empty_watchlist_suggests_what_to_do(
        self, capsys: pytest.CaptureFixture[str], settings: object
    ) -> None:
        for command in ("watchlist", "snapshot", "ivrank"):
            _, out, _ = run(capsys, command)
            assert "watchlist is empty" in out

    def test_snapshot_then_rerun_reports_a_refresh(
        self, capsys: pytest.CaptureFixture[str], settings: object
    ) -> None:
        code, out, _ = run(capsys, "snapshot", "AAPL", "--expiries", "3")
        assert code == 0
        assert "captured" in out

        code, out, _ = run(capsys, "snapshot", "AAPL", "--expiries", "3")
        assert code == 0
        assert "refreshed" in out

    def test_snapshot_prints_the_row_count_before_and_after(
        self, capsys: pytest.CaptureFixture[str], settings: object
    ) -> None:
        """The idempotency proof a human can read in the terminal."""
        run(capsys, "snapshot", "AAPL")
        _, out, _ = run(capsys, "snapshot", "AAPL")

        line = next(x for x in out.splitlines() if "stored contract rows" in x)
        before, after = (int(part.strip()) for part in line.split(":")[1].split("->"))
        assert before == after

    def test_ivrank_refuses_rather_than_inventing(
        self, capsys: pytest.CaptureFixture[str], settings: object
    ) -> None:
        run(capsys, "snapshot", "AAPL")
        code, out, _ = run(capsys, "ivrank", "AAPL")
        assert code == 0
        assert "IV rank unavailable: insufficient history (1/20 days)" in flat(out)
        assert "ATM IV" in out, "today's vol is still shown"

    def test_snapshot_registers_unknown_symbols_it_is_given(
        self, capsys: pytest.CaptureFixture[str], settings: object
    ) -> None:
        run(capsys, "snapshot", "SPY")
        _, out, _ = run(capsys, "watchlist")
        assert "SPY" in out
