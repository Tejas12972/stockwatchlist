# Options & Markets Analytics

A personal options-analytics tool. Option chains with **Black-Scholes greeks and
implied volatility computed locally**, and an IV-rank history the tool builds
itself by snapshotting chains daily.

> Not investment advice. A personal analytics tool, built for my own use.

---

## Why I built it

I trade options, and the thing I actually want to look at — where today's implied
volatility sits against its own recent history — is not something a free data
source will give you. So this tool computes the analytics from first principles
and accumulates the history it needs.

Two design decisions drive the whole project:

**I compute the greeks rather than reading the vendor's columns.** Yahoo ships a
`delta` and an `impliedVolatility` field. This tool ignores both. Prices come
from the provider; Black-Scholes-Merton pricing, all five greeks, and the implied
volatility solve all happen in `api/options_tool/analytics/`. The provider's data
classes have nowhere to put a vendor greek, so the rule is enforced by the type
definitions rather than by discipline.

**I build the IV history instead of faking it.** IV rank needs historical implied
volatility, and free APIs do not provide it. Rather than substitute realised
volatility and call it the same thing, the tool runs a daily snapshot job that
writes the full chain — including its own computed IV — to SQLite. History
accumulates from day one forward. Until there are enough stored days, IV rank
returns `null` and a reason, and the UI says *"insufficient history (3/20 days)"*
rather than rendering a misleading zero.

---

## Status

| Milestone | State |
|---|---|
| M1 — core analytics (pricing, greeks, IV solver, chain normalisation, CLI) | done |
| M2 — persistence + daily snapshots + IV rank | in progress |
| M3 — FastAPI | planned |
| M4 — tests + CI | planned |
| M5 — Next.js front end | planned |
| M6 — Docker + Linux deploy | planned |
| M7 — polish (earnings flag, screener, CSV export) | planned |

---

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/Tejas12972/stockwatchlist.git
cd stockwatchlist/api

python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"

cp ../.env.example ../.env     # optional; every setting has a working default
```

### Use it

```bash
# Live chain with self-computed greeks
.venv/bin/python -m options_tool chain AAPL --expiry 2027-01-15

# Only near-the-money calls
.venv/bin/python -m options_tool chain SPY --right call --near-the-money 5

# Offline, from the checked-in capture -- works when Yahoo is throttling
.venv/bin/python -m options_tool --provider fixture chain AAPL

.venv/bin/python -m options_tool expiries AAPL
.venv/bin/python -m options_tool quote SPY
```

```
                       SPY 2026-09-14  spot 764.29  T 0.0050y  r 4.00%
┏━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━┳━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┓
┃ right ┃ strike ┃   bid ┃   ask ┃   mid ┃ src ┃    IV ┃   delta ┃   gamma ┃   vega ┃  th/day ┃
┡━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━╇━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━┩
│ put   │ 750.00 │  0.13 │  0.14 │  0.14 │ mid │ 15.5% │ -0.0399 │ 0.01031 │  4.633 │ -0.1948 │
│ put   │ 760.00 │  0.73 │  0.74 │  0.73 │ mid │ 10.8% │ -0.2201 │ 0.05107 │ 15.949 │ -0.4554 │
│ put   │ 765.00 │  2.20 │  2.23 │  2.21 │ mid │  8.9% │ -0.5448 │ 0.08227 │ 21.348 │ -0.4812 │
│ put   │ 770.00 │  5.76 │  5.84 │  5.80 │ mid │  8.4% │ -0.8888 │ 0.04186 │ 10.203 │ -0.1615 │
└───────┴────────┴───────┴───────┴───────┴─────┴───────┴─────────┴─────────┴────────┴─────────┘
        6/6 strikes solved (100%) · greeks computed locally, not vendor-supplied
```

---

## How the analytics work

**Pricing** is Black-Scholes-Merton with a continuous dividend yield. Delta,
gamma, vega, theta and rho are the analytic partial derivatives, returned in raw
per-unit terms (`Greeks.theta_per_day` and `.vega_per_point` scale them for
display).

**Implied volatility** is solved numerically: Newton-Raphson on vega, with a
bisection fallback for the contracts where Newton misbehaves — deep in- and
out-of-the-money strikes where vega underflows and the update step explodes.
Bisection cannot diverge, so the wings still solve; across a test surface
spanning 1e-4 to 5.0 volatility and 0.0001 to 2 years, round-trip error is under
4e-9.

**Every failure returns `None` with a reason**, never a number:

| Status | Meaning |
|---|---|
| `expired` | contract has expired; its price implies no future volatility |
| `no_quote` | no usable bid/ask or last price |
| `non_positive_price` | quoted price is zero or negative |
| `below_intrinsic` | price is below intrinsic value — quote is stale or crossed |
| `above_max` | price exceeds the no-arbitrage maximum |
| `no_bracket` | no volatility in [0.0001, 5] reproduces this price |
| `not_converged` | solver ran out of iterations |

Strikes that fail are kept in the table, greyed out and labelled. A strike that
cannot be priced is information, so it is shown rather than dropped.

---

## Limitations

These are real and I would rather state them than hide them.

**Black-Scholes assumes European exercise. US equity options are American.** They
can be exercised early, which Black-Scholes does not model. For liquid
non-dividend-paying names the pricing error is small. For deep in-the-money puts
and for calls just before an ex-dividend date it is not small, and the greeks
this tool reports for those contracts will be off.

**yfinance is an unofficial scraper of Yahoo Finance, not a licensed feed.** It
has no SLA, its schema changes without notice, and it rate-limits — it was
throttling this machine during development, which is exactly why the offline
fixture provider exists. Data is delayed, and the tool is not a market-data
service. All market data is fetched behind the `MarketDataProvider` interface, so
moving to Polygon or Tradier is one new file plus a config change.

**IV rank is meaningless until enough history exists.** The tool starts with an
empty database and accumulates one snapshot per day. Below the configured minimum
(20 days by default), it reports `null` and a day count instead of a number.
A tool that showed a rank on day one would be making it up.

**The risk-free rate and dividend yield are assumptions.** The rate defaults to a
configured constant (`OPTIONS_RISK_FREE_RATE`, 4%), optionally the live 13-week
T-bill. Dividend yield defaults to zero for every underlying, which is wrong for
dividend payers and will bias their greeks.

**Time to expiry is calendar time**, measured to the 16:00 New York close, not
trading time. Weekend decay is therefore priced as if it were trading decay.

---

## License

MIT — see [LICENSE](LICENSE).
