"""Command-line entry point.

    python -m options_tool chain AAPL --expiry 2027-01-15

Every command takes `--provider` so the whole tool can be driven offline from
the checked-in fixture, which is how the demo stays reproducible when Yahoo's
unofficial endpoint is throttling.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

from options_tool import __version__
from options_tool.providers.base import MarketDataProvider

__all__ = ["main", "build_parser"]

logger = logging.getLogger("options_tool")

DISCLAIMER = "Not investment advice. Personal analytics only."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="options-tool",
        description=f"Options analytics with self-computed greeks. {DISCLAIMER}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"options-tool {__version__}")
    parser.add_argument(
        "--provider",
        choices=("yfinance", "fixture"),
        default=None,
        help="market-data source (default: OPTIONS_PROVIDER, else yfinance)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log provider activity")

    sub = parser.add_subparsers(dest="command", required=True)

    p_quote = sub.add_parser("quote", help="spot quote for a ticker")
    p_quote.add_argument("ticker")

    p_expiries = sub.add_parser("expiries", help="list listed option expiries")
    p_expiries.add_argument("ticker")

    p_chain = sub.add_parser("chain", help="option chain with self-computed greeks")
    p_chain.add_argument("ticker")
    p_chain.add_argument(
        "--expiry", type=date.fromisoformat, default=None, help="YYYY-MM-DD (default: nearest)"
    )
    p_chain.add_argument(
        "--right", choices=("call", "put", "both"), default="both", help="filter by right"
    )
    p_chain.add_argument(
        "--near-the-money",
        type=float,
        default=None,
        metavar="PCT",
        help="only strikes within PCT%% of spot (e.g. 10)",
    )
    p_chain.add_argument("--csv", type=Path, default=None, help="also write the frame to CSV")

    p_watch = sub.add_parser("watchlist", help="show or edit the tracked symbols")
    p_watch.add_argument("--add", nargs="+", metavar="TICKER", default=None)
    p_watch.add_argument("--remove", nargs="+", metavar="TICKER", default=None)
    p_watch.add_argument("--all", action="store_true", help="include removed symbols")

    p_snapshot = sub.add_parser(
        "snapshot",
        help="capture today's chains into the local history (idempotent, safe to re-run)",
    )
    p_snapshot.add_argument(
        "tickers", nargs="*", help="symbols to capture (default: the whole watchlist)"
    )
    p_snapshot.add_argument(
        "--expiries", type=int, default=6, help="expiries to capture per ticker"
    )

    p_ivrank = sub.add_parser("ivrank", help="IV rank from locally stored history")
    p_ivrank.add_argument("tickers", nargs="*", help="default: the whole watchlist")

    p_capture = sub.add_parser(
        "capture-fixture", help="refresh the offline test/demo fixture from the live vendor"
    )
    p_capture.add_argument("tickers", nargs="+")
    p_capture.add_argument("--out", type=Path, default=None)
    p_capture.add_argument("--expiries", type=int, default=3)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Imported lazily so `--help` and `--version` stay instant and import-light.
    from options_tool.providers import ProviderError, get_provider  # noqa: PLC0415

    try:
        provider = get_provider(args.provider)
        # Every handler takes (args, provider) so the dispatch stays uniform;
        # the two that read only local history ignore the provider.
        handlers = {
            "quote": _cmd_quote,
            "expiries": _cmd_expiries,
            "chain": _cmd_chain,
            "watchlist": _cmd_watchlist,
            "snapshot": _cmd_snapshot,
            "ivrank": _cmd_ivrank,
            "capture-fixture": _cmd_capture,
        }
        return handlers[args.command](args, provider)
    except ProviderError as exc:
        # A vendor failure is an expected outcome, not a crash. Say what happened
        # and what to do about it; no traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def _cmd_quote(args: argparse.Namespace, provider: MarketDataProvider) -> int:
    quote = provider.get_quote(args.ticker)
    print(
        f"{quote.ticker}  {quote.price:,.2f} {quote.currency}  "
        f"as of {quote.as_of:%Y-%m-%d %H:%M %Z}"
    )
    if quote.previous_close:
        change = quote.price - quote.previous_close
        print(
            f"  prev close {quote.previous_close:,.2f}  "
            f"change {change:+,.2f} ({change / quote.previous_close:+.2%})"
        )
    return 0


def _cmd_expiries(args: argparse.Namespace, provider: MarketDataProvider) -> int:
    from options_tool.analytics.chain import time_to_expiry  # noqa: PLC0415

    expiries = provider.get_expiries(args.ticker)
    print(f"{args.ticker.upper()}: {len(expiries)} listed expiries")
    for expiry in expiries:
        days = time_to_expiry(expiry) * 365
        print(f"  {expiry}  ({days:6.1f} days)")
    return 0


def _cmd_chain(args: argparse.Namespace, provider: MarketDataProvider) -> int:
    from rich.console import Console  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415

    from options_tool.analytics.chain import build_chain_frame, summarise_chain  # noqa: PLC0415
    from options_tool.config import get_settings  # noqa: PLC0415
    from options_tool.providers.rates import resolve_risk_free_rate  # noqa: PLC0415

    settings = get_settings()
    rate = resolve_risk_free_rate(settings)

    expiry = provider.resolve_expiry(args.ticker, args.expiry)
    chain = provider.get_chain(args.ticker, expiry)
    frame = build_chain_frame(chain, rate, settings.options_dividend_yield, chain.as_of)

    if args.right != "both":
        frame = frame[frame["right"] == args.right]
    if args.near_the_money is not None:
        band = args.near_the_money / 100.0
        frame = frame[(frame["moneyness"] - 1.0).abs() <= band]

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.csv, index=False)

    summary = summarise_chain(frame)
    # Rich falls back to 80 columns when stdout is not a terminal, which silently
    # ellipsises every number in this table when piped to a file or a reviewer.
    console = Console(width=None if sys.stdout.isatty() else 150)

    table = Table(
        title=(
            f"{chain.ticker} {expiry}  spot {chain.spot:,.2f}  "
            f"T {summary['time_to_expiry']:.4f}y  r {rate:.2%}"
        ),
        caption=(
            f"{summary['solved']}/{summary['contracts']} strikes solved "
            f"({summary['solve_rate']:.0%})"
            + (f" · ATM IV {summary['atm_iv']:.1%}" if summary["atm_iv"] else "")
            + f" · greeks computed locally, not vendor-supplied · {DISCLAIMER}"
        ),
    )
    columns = (
        "right",
        "strike",
        "bid",
        "ask",
        "mid",
        "src",
        "IV",
        "delta",
        "gamma",
        "vega",
        "th/day",
        "OI",
    )
    for column in columns:
        table.add_column(
            column,
            justify="left" if column in ("right", "src") else "right",
            no_wrap=True,
            overflow="fold",
        )

    # `to_dict("records")` rather than `itertuples`: the pandas stubs type tuple
    # fields as a union of every possible scalar, which makes arithmetic on a
    # column that is plainly a float unrepresentable to the type checker.
    for row in frame.to_dict("records"):
        theta = row["theta"]
        table.add_row(
            str(row["right"]),
            f"{row['strike']:,.2f}",
            _num(row["bid"]),
            _num(row["ask"]),
            _num(row["mid"]),
            str(row["price_source"] or "-"),
            f"{row['iv']:.1%}" if _present(row["iv"]) else _dash(str(row["iv_status"])),
            _num(row["delta"], 4),
            _num(row["gamma"], 5),
            _num(row["vega"], 3),
            _num(theta / 365 if _present(theta) else None, 4),
            f"{row['open_interest']:,}" if _present(row["open_interest"]) else "-",
            style=None if _present(row["iv"]) else "dim",
        )

    console.print(table)
    if summary["unsolved_reasons"]:
        console.print(f"[dim]unsolved: {summary['unsolved_reasons']}[/dim]")
    if args.csv:
        console.print(f"[dim]wrote {args.csv}[/dim]")
    return 0


def _cmd_watchlist(args: argparse.Namespace, provider: MarketDataProvider) -> int:  # noqa: ARG001
    from options_tool.db.queries import (  # noqa: PLC0415
        add_to_watchlist,
        history_depth,
        list_watchlist,
        remove_from_watchlist,
    )
    from options_tool.db.session import init_db, session_scope  # noqa: PLC0415

    engine = init_db()
    with session_scope(engine) as session:
        for symbol in args.add or []:
            add_to_watchlist(session, symbol)
            print(f"added {symbol.upper()}")
        for symbol in args.remove or []:
            removed = remove_from_watchlist(session, symbol)
            print(f"removed {symbol.upper()}" if removed else f"{symbol.upper()} was not tracked")

        tickers = list_watchlist(session, include_inactive=args.all)
        if not tickers:
            print("watchlist is empty — add one with: options-tool watchlist --add SPY")
            return 0

        print(f"{'symbol':<8} {'days':>5}  history")
        for ticker in tickers:
            days, first, last = history_depth(session, ticker.symbol)
            span = f"{first} to {last}" if days else "no snapshots yet"
            flag = "" if ticker.active else "  (removed)"
            print(f"{ticker.symbol:<8} {days:>5}  {span}{flag}")
    return 0


def _cmd_snapshot(args: argparse.Namespace, provider: MarketDataProvider) -> int:
    from options_tool.config import get_settings  # noqa: PLC0415
    from options_tool.db.queries import contract_count  # noqa: PLC0415
    from options_tool.db.session import init_db, session_scope  # noqa: PLC0415
    from options_tool.db.snapshot import snapshot_ticker, snapshot_watchlist  # noqa: PLC0415
    from options_tool.providers.rates import resolve_risk_free_rate  # noqa: PLC0415

    settings = get_settings()
    rate = resolve_risk_free_rate(settings)
    engine = init_db()

    with session_scope(engine) as session:
        before = contract_count(session)
        if args.tickers:
            results = [
                snapshot_ticker(
                    session,
                    provider,
                    ticker,
                    rate,
                    settings.options_dividend_yield,
                    args.expiries,
                )
                for ticker in args.tickers
            ]
        else:
            results = snapshot_watchlist(
                session, provider, rate, settings.options_dividend_yield, args.expiries
            )
            if not results:
                print("watchlist is empty — add one with: options-tool watchlist --add SPY")
                return 0

        for result in results:
            if result.errors and not result.contracts_written:
                print(f"{result.ticker}: FAILED — {result.errors[0]}")
                continue
            atm = f"{result.atm_iv_30d:.1%}" if result.atm_iv_30d else "n/a"
            verb = "captured" if result.created else "refreshed"
            print(
                f"{result.ticker} {result.snapshot_date}: {verb} "
                f"{result.contracts_written} contracts across {len(result.expiries)} expiries "
                f"({result.solve_rate:.0%} solved), 30d ATM IV {atm}"
            )
            for error in result.errors:
                print(f"  warning: {error}")

        after = contract_count(session)
        print(f"stored contract rows: {before} -> {after}")
    return 0


def _cmd_ivrank(args: argparse.Namespace, provider: MarketDataProvider) -> int:  # noqa: ARG001
    from options_tool.config import get_settings  # noqa: PLC0415
    from options_tool.db.queries import iv_rank_for, list_watchlist  # noqa: PLC0415
    from options_tool.db.session import init_db, session_scope  # noqa: PLC0415

    settings = get_settings()
    engine = init_db()

    with session_scope(engine) as session:
        symbols = args.tickers or [t.symbol for t in list_watchlist(session)]
        if not symbols:
            print("watchlist is empty — add one with: options-tool watchlist --add SPY")
            return 0

        for symbol in symbols:
            result = iv_rank_for(
                session,
                symbol,
                settings.options_iv_window_days,
                settings.options_min_history_days,
            )
            if not result.ok:
                # The degraded state is printed as prominently as a real answer.
                # A blank or a zero here would read as "IV rank is low".
                current = f" (ATM IV {result.current_iv:.1%})" if result.current_iv else ""
                print(f"{symbol.upper():<8} IV rank unavailable: {result.reason}{current}")
                continue
            print(
                f"{symbol.upper():<8} IV rank {result.rank:5.1f}   "
                f"percentile {result.percentile:5.1f}   "
                f"ATM IV {result.current_iv:.1%}   "
                f"range {result.iv_min:.1%}-{result.iv_max:.1%}   "
                f"({result.days_available} days, {result.first_observed} to "
                f"{result.last_observed})"
            )
    print(f"\n{DISCLAIMER}")
    return 0


def _cmd_capture(args: argparse.Namespace, provider: MarketDataProvider) -> int:
    from options_tool.providers.capture import capture_fixture  # noqa: PLC0415
    from options_tool.providers.fixture_provider import DEFAULT_FIXTURE_PATH  # noqa: PLC0415

    out = args.out or DEFAULT_FIXTURE_PATH
    counts = capture_fixture(provider, args.tickers, out, args.expiries)
    total = sum(counts.values())
    print(f"captured {total} contracts to {out}")
    for ticker, count in sorted(counts.items()):
        print(f"  {ticker}: {count}")
    return 0


# --------------------------------------------------------------------------


def _present(value: Any) -> bool:
    """True unless the value is missing -- None, NaN or pandas NA alike."""
    import pandas as pd  # noqa: PLC0415 -- keeps `--help` free of the pandas import

    return value is not None and not bool(pd.isna(value))


def _num(value: Any, places: int = 2) -> str:
    return f"{value:,.{places}f}" if _present(value) else "-"


def _dash(status: str) -> str:
    """Show *why* a strike has no IV rather than an unexplained blank."""
    return {
        "no_quote": "[dim]no quote[/dim]",
        "below_intrinsic": "[dim]stale[/dim]",
        "above_max": "[dim]crossed[/dim]",
        "expired": "[dim]expired[/dim]",
        "non_positive_price": "[dim]no bid[/dim]",
    }.get(status, "[dim]-[/dim]")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
