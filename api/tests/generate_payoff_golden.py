"""Regenerate `tests/fixtures/payoff_golden.json`.

    python tests/generate_payoff_golden.py

The golden file is read by **both** test suites -- pytest via
`tests/test_payoff.py` and vitest via `web/lib/__tests__/payoff.test.ts`. That is
the point of it: the TypeScript client-side preview and the Python engine are
two implementations of the same maths, and a shared golden file means they
cannot drift apart without a test going red.

Regenerating is a deliberate act with a visible diff. If a change here alters
existing numbers, that is a change in behaviour and the diff should be read
rather than committed on autopilot.
"""

from __future__ import annotations

import json
from pathlib import Path

from options_tool.analytics.payoff import Leg, LegKind, build_payoff_curve

SPOT, RATE, TIME_TO_EXPIRY, DIVIDEND_YIELD = 100.0, 0.05, 0.25, 0.0
PRICE_RANGE = (60.0, 140.0)
POINTS = 41

OUTPUT = Path(__file__).parent / "fixtures" / "payoff_golden.json"

# Every structure a user of this tool is likely to build, including the two that
# a naive implementation gets wrong: a long put (large but finite profit, because
# the underlying cannot go below zero) and a ratio spread (two breakevens, and
# unlimited loss despite being opened for a debit).
STRUCTURES: dict[str, list[dict[str, object]]] = {
    "long_call": [
        {"kind": "call", "quantity": 1, "premium": 5.00, "strike": 100, "volatility": 0.25}
    ],
    "short_call_naked": [
        {"kind": "call", "quantity": -1, "premium": 5.00, "strike": 100, "volatility": 0.25}
    ],
    "long_put": [
        {"kind": "put", "quantity": 1, "premium": 4.00, "strike": 100, "volatility": 0.25}
    ],
    "short_put": [
        {"kind": "put", "quantity": -1, "premium": 4.00, "strike": 100, "volatility": 0.25}
    ],
    "bull_call_spread": [
        {"kind": "call", "quantity": 1, "premium": 5.00, "strike": 100, "volatility": 0.25},
        {"kind": "call", "quantity": -1, "premium": 1.50, "strike": 110, "volatility": 0.23},
    ],
    "bear_put_spread": [
        {"kind": "put", "quantity": 1, "premium": 4.00, "strike": 100, "volatility": 0.25},
        {"kind": "put", "quantity": -1, "premium": 1.20, "strike": 90, "volatility": 0.28},
    ],
    "iron_condor": [
        {"kind": "put", "quantity": 1, "premium": 0.50, "strike": 90, "volatility": 0.30},
        {"kind": "put", "quantity": -1, "premium": 1.50, "strike": 95, "volatility": 0.27},
        {"kind": "call", "quantity": -1, "premium": 1.60, "strike": 105, "volatility": 0.24},
        {"kind": "call", "quantity": 1, "premium": 0.60, "strike": 110, "volatility": 0.23},
    ],
    "long_straddle": [
        {"kind": "call", "quantity": 1, "premium": 5.00, "strike": 100, "volatility": 0.25},
        {"kind": "put", "quantity": 1, "premium": 4.50, "strike": 100, "volatility": 0.25},
    ],
    "covered_call": [
        {"kind": "stock", "quantity": 100, "premium": 100.0},
        {"kind": "call", "quantity": -1, "premium": 3.00, "strike": 105, "volatility": 0.24},
    ],
    "collar": [
        {"kind": "stock", "quantity": 100, "premium": 100.0},
        {"kind": "put", "quantity": 1, "premium": 3.00, "strike": 95, "volatility": 0.28},
        {"kind": "call", "quantity": -1, "premium": 2.50, "strike": 110, "volatility": 0.23},
    ],
    "call_ratio_spread": [
        {"kind": "call", "quantity": 1, "premium": 5.00, "strike": 100, "volatility": 0.25},
        {"kind": "call", "quantity": -2, "premium": 1.50, "strike": 110, "volatility": 0.23},
    ],
}


def to_legs(raw: list[dict[str, object]]) -> list[Leg]:
    return [
        Leg(
            kind=LegKind(str(item["kind"])),
            quantity=int(item["quantity"]),  # type: ignore[arg-type]
            premium=float(item["premium"]),  # type: ignore[arg-type]
            strike=float(item["strike"]) if item.get("strike") is not None else None,  # type: ignore[arg-type]
            volatility=float(item["volatility"]) if item.get("volatility") is not None else None,  # type: ignore[arg-type]
        )
        for item in raw
    ]


def main() -> None:
    cases = []
    for name, raw_legs in STRUCTURES.items():
        curve = build_payoff_curve(
            to_legs(raw_legs), SPOT, TIME_TO_EXPIRY, RATE, DIVIDEND_YIELD, PRICE_RANGE, POINTS
        )
        cases.append(
            {
                "name": name,
                "legs": raw_legs,
                "expected": {
                    "net_cost": curve.net_cost,
                    "breakevens": curve.breakevens,
                    "max_profit": curve.max_profit,
                    "max_loss": curve.max_loss,
                    "unlimited_profit": curve.unlimited_profit,
                    "unlimited_loss": curve.unlimited_loss,
                    "upside_slope": curve.upside_slope,
                    "downside_slope": curve.downside_slope,
                    "underlying_prices": curve.underlying_prices,
                    "pnl_at_expiry": curve.pnl_at_expiry,
                },
            }
        )

    OUTPUT.write_text(
        json.dumps(
            {
                "note": (
                    "Golden payoff values. Read by BOTH the Python suite "
                    "(tests/test_payoff.py) and the TypeScript suite "
                    "(web/lib/__tests__/payoff.test.ts) so the two implementations "
                    "cannot drift apart unnoticed. Regenerate with "
                    "tests/generate_payoff_golden.py."
                ),
                "parameters": {
                    "spot": SPOT,
                    "risk_free_rate": RATE,
                    "time_to_expiry": TIME_TO_EXPIRY,
                    "dividend_yield": DIVIDEND_YIELD,
                    "price_range": list(PRICE_RANGE),
                    "points": POINTS,
                    "option_multiplier": 100,
                },
                "cases": cases,
            },
            indent=1,
        )
        + "\n"
    )
    print(f"wrote {OUTPUT} with {len(cases)} structures")


if __name__ == "__main__":
    main()
