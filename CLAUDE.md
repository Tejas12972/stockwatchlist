# CLAUDE.md — options analytics tool

## What this is

A personal options-analytics tool: option chains with **greeks and implied
volatility computed locally**, plus an implied-volatility history built from the
project's own daily snapshots so IV rank can be computed at all.

`SPEC.md` is the contract. Read it before changing anything — several decisions
in it look like they could be simplified and specifically must not be.

## The two rules that are the whole point

1. **Never read the vendor's greeks or `impliedVolatility`.** Take *prices* from
   the provider; derive everything else in `api/options_tool/analytics/`.
   `OptionQuote` has no fields for vendor greeks, which enforces this by
   construction — don't add them.
2. **Never return a fabricated number.** Every solver edge case returns `None`
   with a status explaining why, and IV rank returns `null` plus a reason until
   the minimum history window is met. A blank cell is correct; a plausible-looking
   wrong number is the failure mode this project exists to avoid.

## Layout

- `api/options_tool/providers/` — the market-data boundary. Vendor types stop here.
- `api/options_tool/analytics/` — pure functions, no I/O. `mypy --strict` applies.
- `api/options_tool/db/` — SQLAlchemy models and the snapshot writer.
- `api/options_tool/api/` — FastAPI app.
- `web/` — Next.js front end.

## Running it

```bash
cd api
uv venv --python 3.11 .venv
# This machine's python3.11 is a universal2 binary and uv otherwise resolves
# x86_64 wheels onto an arm64 interpreter, which breaks numpy at import.
uv pip install -e ".[dev]" --python .venv/bin/python --python-platform aarch64-apple-darwin

.venv/bin/python -m options_tool chain AAPL --expiry 2027-01-15
.venv/bin/python -m options_tool --provider fixture chain AAPL   # offline
```

## Testing

```bash
cd api && .venv/bin/pytest            # offline: sockets are disabled in config
.venv/bin/pytest -m network           # opt in to live-vendor tests
.venv/bin/ruff check . && .venv/bin/mypy options_tool
```

The suite runs with `--disable-socket`. If a test needs market data it must use
`FixtureProvider`, never the network. A test that starts failing with a socket
error is telling you a code path acquired a hidden network dependency.

## Constraints

- yfinance is an unofficial Yahoo scraper. It rate-limits and breaks without
  notice. That is why `FixtureProvider` and `--provider fixture` exist.
- Black-Scholes assumes European exercise; US equity options are American. Stated
  in the README's Limitations section and not to be quietly dropped.
- No claim of profitability, returns, or backtested edge belongs anywhere in this
  repo. Not investment advice.
