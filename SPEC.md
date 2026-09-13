# Build Spec — Options & Markets Analytics Tool

**For:** Karthik Tejas Basavarajappa · **Written:** 2026-09-12
**How to use:** drop this file into a fresh repo as `SPEC.md`, open Claude Code there, and say
*"build this following SPEC.md, one milestone at a time."*

---

## 0. Why this project exists (read before choosing anything)

This is a **resume artifact with a real user (me)**. Every design decision below is made to close a
specific, documented gap. Do not "improve" the plan by dropping these — they are the point:

| Gap being closed | How this project closes it |
|---|---|
| **Data/analytics is the #1 requested specialization** across 1,036 co-op postings; my CV lists Pandas and SQL as bare keywords with **no supporting bullet** | Pandas is the analysis layer, and a real stored dataset backs it |
| **Test/QA is #2 requested (67 postings); I have zero testing evidence** | pytest + GitHub Actions are **milestone 4, not optional** |
| **No CI/CD experience** — had to concede this in a Philips cover letter | GitHub Actions on every push |
| **No Linux evidence**, though Philips lists it as a *stated requirement* | Milestone 6 deploys to a Linux box in Docker |
| **95 finance-sector roles; my "why finance?" is an assertion with no artifact** | The artifact |
| I trade options personally but nothing public shows it | The tool does what I actually do |

**Scope discipline is itself a goal.** My GitHub already has `stockwatchlist` — an empty stub with a
LICENSE and a 69-byte README. A second abandoned repo is worse than no repo. **Milestones 1-4 are
the deliverable. 5-7 are bonus.** Ship 1-4 completely before starting 5.

---

## 1. What it does

A personal options-analytics dashboard:

1. **Watchlist** — track tickers, see price and basic stats
2. **Options chain viewer** — full chain for a ticker/expiry, with greeks **computed by me, not
   taken from the vendor** (see §4 — this is deliberate)
3. **IV rank / IV percentile** — where today's implied volatility sits against its own history
4. **Spread payoff visualizer** — build a multi-leg position, see P/L at expiry and today
5. **Daily snapshot job** — persists chains so IV history accumulates (see §3 — this is the
   interesting engineering problem)

**Not** a trading bot, not a recommendation engine, not a backtester. See §9.

---

## 2. Stack (decided — do not re-litigate)

| Layer | Choice | Why |
|---|---|---|
| Analysis | **Python 3.11 + Pandas + NumPy** | The gap being closed. Non-negotiable. |
| API | **FastAPI** + Pydantic | Typed, auto-docs, fast to write |
| Storage | **SQLite** via SQLAlchemy | Zero-ops, real SQL, gives the SQL keyword an artifact. Postgres only if deploying multi-user. |
| Front end | **Next.js + React + TypeScript** | Already my strongest skill — keeps it visible without being the point |
| Charts | **Recharts** or hand-rolled SVG | No heavy dep |
| Tests | **pytest** (+ `pytest-cov`), **vitest** for the front end | Milestone 4 |
| CI | **GitHub Actions** | Runs tests + lint on push |
| Container | **Docker + docker-compose** | Milestone 6 |

**Market data: `yfinance` as the default.** Free, no key, has options chains.
**Be honest about it in the README:** it is an unofficial Yahoo Finance scraper, it breaks without
warning, and it is not a licensed feed. Put the fetch behind a `MarketDataProvider` interface so a
swap to Polygon/Tradier is a one-file change — *that abstraction is itself a talking point*.

---

## 3. The interesting problem: building IV history

IV rank needs historical implied volatility. **Free APIs do not give you this.** Do not fake it and
do not silently substitute realized volatility.

**The design:** a scheduled job snapshots the full options chain daily and writes it to SQLite.
History accumulates from day one forward. On day 1 IV rank is undefined and the UI must **say so**
rather than render a misleading zero.

This is the best engineering story in the project — a real data pipeline with idempotency, schema
design, and a degraded-state UI. Build it properly:

- `snapshot` command, idempotent per (ticker, expiry, strike, right, date) — safe to re-run
- Store raw mid-price, bid, ask, volume, OI, **and my computed IV** (§4)
- IV rank over a trailing window: `(iv_today - iv_min) / (iv_max - iv_min) * 100`
- **Require a minimum history window (e.g. 20 trading days) before showing a rank.** Below that,
  return `null` and have the UI render "insufficient history (N/20 days)".

---

## 4. Compute the greeks yourself

Take the **price** from the vendor. Compute **IV and the greeks** yourself.

Reason: anyone can render a vendor's `delta` column. Implementing Black-Scholes, then solving for
implied volatility numerically, is the part that demonstrates quantitative ability — and it is the
thing a finance-sector interviewer will actually ask about.

- **Black-Scholes-Merton** for price; delta, gamma, theta, vega, rho analytically
- **Implied vol** by Newton-Raphson on vega, bisection fallback when it fails to converge
- Handle the ugly cases explicitly: zero/near-zero time to expiry, deep ITM/OTM, zero bid,
  non-convergence. **Return `None`, never a silently wrong number.**

**State the limitation in the README:** Black-Scholes assumes European exercise, but US equity
options are American and can be exercised early. For liquid non-dividend names the error is small;
for deep ITM puts and pre-dividend calls it is not. Naming this limitation is worth more in an
interview than hiding it.

---

## 5. Milestones

Each milestone ends with **working, committed, tested code**. Do not start the next until the
previous is green in CI.

### M1 — Core analytics (Python only, no UI)
- [ ] `MarketDataProvider` interface + `YFinanceProvider`
- [ ] Black-Scholes pricing + all five greeks
- [ ] IV solver (Newton-Raphson + bisection fallback), edge cases returning `None`
- [ ] Pandas chain normalization: tidy DataFrame, one row per contract, typed columns
- [ ] CLI: `python -m options_tool chain AAPL --expiry 2027-01-15`
- **Done when:** greeks for a real chain print correctly and match a reference within tolerance

### M2 — Persistence + snapshots
- [ ] SQLAlchemy schema: `tickers`, `snapshots`, `contracts`
- [ ] `snapshot` command, idempotent per (ticker, expiry, strike, right, date)
- [ ] IV rank / percentile with the minimum-history guard from §3
- [ ] Alembic migration (or a documented single-schema-version decision)
- **Done when:** two runs on the same day produce no duplicate rows

### M3 — API
- [ ] FastAPI: `/watchlist`, `/chain/{ticker}`, `/ivrank/{ticker}`, `/payoff`
- [ ] Pydantic models on every response
- [ ] `/payoff` accepts multi-leg positions, returns a P/L curve at expiry **and** at today
- [ ] Errors are typed and useful — never a bare 500
- **Done when:** `/docs` is browsable and every endpoint returns real data

### M4 — Tests + CI  ← **the milestone with the most resume value. Do not skip.**
- [ ] pytest covering: BS against known reference values, IV round-trip
      (price → IV → price), every edge case in §4, IV-rank math, snapshot idempotency
- [ ] **Market data mocked in tests** — the suite must pass offline with no network
- [ ] `pytest --cov`, coverage reported, aim ≥80% on the analytics module
- [ ] GitHub Actions: tests + `ruff` + `mypy` on push and PR
- [ ] Badge in the README
- **Done when:** CI is green on a PR and the suite passes with the network off

### M5 — Front end
- [ ] Next.js + TypeScript, watchlist page, chain table, IV-rank display
- [ ] Payoff diagram for a user-built multi-leg spread
- [ ] **"Insufficient history" state rendered honestly** where IV rank is unavailable
- [ ] Loading and error states — no infinite spinners
- [ ] vitest on the payoff math and any client-side calc

### M6 — Docker + Linux deploy  *(bonus, closes the Linux gap)*
- [ ] Multi-stage Dockerfile, `docker-compose` for api + web
- [ ] Deploy to a cheap VPS or EC2 behind nginx
- [ ] `cron` or systemd timer running the daily snapshot
- [ ] README documents the deploy so it is reproducible

### M7 — Polish *(bonus)*
- [ ] Earnings-date proximity flag
- [ ] Simple screener (high IV rank, unusual volume/OI)
- [ ] Export to CSV

---

## 6. Suggested tree

```
options-analytics/
├─ SPEC.md  README.md  LICENSE
├─ .github/workflows/ci.yml
├─ docker-compose.yml
├─ api/
│  ├─ pyproject.toml
│  ├─ options_tool/
│  │  ├─ providers/{base,yfinance_provider}.py
│  │  ├─ analytics/{black_scholes,implied_vol,iv_rank,payoff}.py
│  │  ├─ db/{models,session,snapshot}.py
│  │  ├─ api/{main,routes,schemas}.py
│  │  └─ cli.py
│  └─ tests/{test_black_scholes,test_implied_vol,test_iv_rank,test_snapshot,test_api}.py
└─ web/   # Next.js + TS
```

---

## 7. The README is part of the deliverable

A recruiter reads the README, not the source. It must contain:

1. **A screenshot or GIF** at the top — first thing seen
2. **What it does and why I built it** — one paragraph, first person, mentions that I trade options
   and wanted the tool
3. **The IV-history design** (§3) — the strongest engineering idea here; explain the snapshot
   approach and why the data isn't otherwise obtainable
4. **Why I compute greeks instead of reading vendor columns** (§4)
5. **An honest Limitations section** — Black-Scholes vs American exercise, yfinance being an
   unofficial and breakable source, minimum history before IV rank means anything
6. **Setup that actually works from a clean clone** — and test it from a clean clone
7. **CI badge and coverage number**

Section 5 is not a weakness. Stating known limitations precisely is the single clearest signal of
engineering maturity a student repo can send.

---

## 8. Definition of done (M1-M4)

- `git clone` → documented setup → `pytest` passes **offline**
- CI green on a PR
- `python -m options_tool chain SPY` prints a chain with self-computed greeks
- `snapshot` run twice in a day creates no duplicates
- IV rank returns `null` with a reason, not a fake number, before minimum history
- README has all seven items from §7

---

## 9. Guardrails

- **Not investment advice.** One line in the README and in the UI footer. A personal analytics tool.
- **No claim of profitability, returns, or backtested edge.** Nothing in this repo should imply a
  trading track record. On the resume: retail, self-directed, analytical — never professional.
- **No paid-feed data redistribution.** yfinance is unofficial; don't republish its data or present
  the tool as a market-data service.
- **No secrets committed.** API keys via `.env`, `.env.example` committed, `.env` gitignored.

---

## 10. Resume bullets this earns (once M1-M4 ship)

Write these only when true:

- Built an options analytics platform in Python/Pandas computing Black-Scholes greeks and
  implied volatility from live chain data, with a daily snapshot pipeline persisting IV history
  to SQLite for IV-rank analysis
- Achieved N% test coverage on the analytics core with a fully offline pytest suite; automated
  tests, lint and type-checking with GitHub Actions on every push
- Designed a provider abstraction isolating market-data vendors, so the source can be swapped
  without touching analytics or API layers

The first bullet alone answers "do you have data experience?", "why finance?", and "can you build
something real?" in one line — which is why this project is ranked first in `upskill/PLAN.md`.
