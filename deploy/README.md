# Deploying

Two Fly.io apps. **The API has no public address** — it is reachable only over
Fly's private network, from the web app, which is the single authenticated
surface.

```
  internet ──TLS──▶  options-analytics-web   (Basic auth, Next.js)
                              │
                              │ server-side proxy, Fly 6PN private network
                              ▼
                     options-analytics-api   (FastAPI, no public address)
                              │
                              ▼
                      volume: /data/options.db
                      (the IV history — the only irreplaceable state)
```

Three consequences worth understanding before changing anything:

- **One auth check covers everything.** The browser can only reach the API
  through `/api/*` on the web app, which is behind the same middleware as every
  other route. There is no second, weaker door to leave open.
- **CORS does not apply.** Same origin, no preflight, no allow-list to keep in
  sync with whatever domain this ends up on.
- **The API address is runtime configuration.** `API_INTERNAL_URL` is read per
  request, so the image is identical in every environment and moving the API
  does not mean rebuilding the front end.

---

## Prerequisites

```bash
curl -L https://fly.io/install.sh | sh
export PATH="$HOME/.fly/bin:$PATH"
fly auth login          # opens a browser
```

No local Docker required — Fly builds the images remotely.

---

## The short version

```bash
fly auth login
./deploy/fly-bootstrap.sh
```

Idempotent — safe to re-run after a partial failure. It creates both apps, the
volume and the login secret, deploys API-then-web in that order, seeds the
watchlist, and prints the generated password once. The steps below are what it
does, for when something needs doing by hand.

---

## 1. Create the apps

```bash
fly apps create options-analytics-api
fly apps create options-analytics-web
```

## 2. Create the volume

```bash
fly volumes create options_data --size 1 --region bos -a options-analytics-api
```

1 GB is generous: a year of daily snapshots for a handful of tickers is tens of
megabytes. Size it up later with `fly volumes extend`.

> A Fly volume attaches to **exactly one machine**. That is why the daily
> snapshot runs inside the API process rather than as a separate scheduled
> machine — a second machine could not open the database while the API held it.
> See `api/options_tool/scheduler.py`.

## 3. Set the login

```bash
fly secrets set \
  APP_USERNAME='you' \
  APP_PASSWORD="$(openssl rand -base64 24)" \
  -a options-analytics-web
```

Print the generated password *before* you set it if you want to keep it —
`fly secrets` is write-only and will not show it back to you.

**If these are unset the app runs open.** That is the right default for
`npm run dev` on localhost and the wrong one on the internet, so `/health` on
the API reports which mode is in force.

## 4. Deploy

The API first — the web app's health check depends on it being resolvable.

```bash
fly deploy -c fly.api.toml
fly deploy -c fly.web.toml
```

## 5. Seed the watchlist

The daily job snapshots whatever is on the watchlist, so it needs at least one
symbol before it does anything.

```bash
fly ssh console -a options-analytics-api -C "python -m options_tool watchlist --add SPY AAPL"
fly ssh console -a options-analytics-api -C "python -m options_tool snapshot"
```

You can also add symbols through the UI once it is up.

## 6. Verify

```bash
fly status -a options-analytics-api
fly status -a options-analytics-web

# The API must NOT be reachable from the internet. This should fail to resolve.
curl -sS https://options-analytics-api.fly.dev/health || echo "correctly unreachable"

# The web app must challenge for credentials.
curl -s -o /dev/null -w '%{http_code}\n' https://options-analytics-web.fly.dev/        # 401
curl -s -o /dev/null -w '%{http_code}\n' https://options-analytics-web.fly.dev/healthz # 200

# And serve with them.
curl -s -u you:PASSWORD https://options-analytics-web.fly.dev/api/health | jq
```

The last call should show `"snapshot_scheduler": true` and the scheduled time.
If it shows `false`, the daily job is not running and no history is
accumulating — which you would otherwise not notice for weeks, when IV rank
never arrives.

---

## The daily snapshot

Runs inside the API process at `OPTIONS_SNAPSHOT_AT` (21:15 UTC by default),
weekdays only — after the US equity close in both EST and EDT, so the chain
captured is the settled one rather than a mid-session reading whose volatility
depends on what time the job happened to fire.

**On start it catches up.** If the machine was down through the scheduled time
and today has no snapshot, it runs immediately. This is the equivalent of
systemd's `Persistent=true`, and it matters because **a missed day is a
permanent hole** — historical implied volatility is not purchasable from any
free source and cannot be backfilled.

`min_machines_running = 1` and no auto-stop on the API app for the same reason:
a machine that suspends overnight misses the snapshot.

```bash
fly logs -a options-analytics-api                       # watch it run
fly ssh console -a options-analytics-api -C "python -m options_tool snapshot"  # force one
```

Running it by hand while the scheduler also fires is safe — the database
constraints make a snapshot idempotent per (ticker, date, expiry, strike, right).

---

## Backups

Only the volume matters; everything else is rebuildable from git.

```bash
fly ssh console -a options-analytics-api -C "python -m options_tool export --what daily --out /tmp/history.csv"
fly ssh sftp get /tmp/history.csv -a options-analytics-api
```

Fly also snapshots volumes daily (`snapshot_retention = 30` in `fly.api.toml`):

```bash
fly volumes snapshots list <volume-id>
```

---

## Upgrading

```bash
git pull
fly deploy -c fly.api.toml      # migrations run at startup, before serving
fly deploy -c fly.web.toml
```

No separate migrate step: the API applies Alembic migrations in its own startup
before accepting traffic. It also *adopts* a schema that was created by the CLI
rather than failing on it — a database with the tables but no `alembic_version`
row gets stamped instead of re-created.

---

## Running it on a plain VPS instead

`docker-compose.yml` mirrors the same topology — API unpublished, web published,
proxy in between — so it behaves the same way.

```bash
cp .env.example .env     # set APP_USERNAME / APP_PASSWORD
docker compose up -d --build
```

Put nginx in front for TLS using `deploy/nginx.conf` (it proxies to the web app
on :3000; the `/api/` block there is now unnecessary, since the web app proxies
internally). `deploy/options-snapshot.{service,timer}` are kept for a setup that
would rather schedule externally than use the in-process scheduler — set
`OPTIONS_SNAPSHOT_ENABLED=false` if you use them, so the job does not run twice.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Pages load, every panel empty | The web app cannot reach the API. Check `API_INTERNAL_URL` and that the API app is running: `fly status -a options-analytics-api`. |
| `502 api_unreachable` from `/api/...` | Same. The proxy returns the API's own error shape, so it looks like an API error but is a connectivity one. |
| Browser keeps asking for credentials | `APP_USERNAME`/`APP_PASSWORD` differ from what you are typing. `fly secrets list` shows names, not values — reset it. |
| Fly health check failing on web | `/healthz` must be reachable without auth. Check the `matcher` in `middleware.ts` still excludes it. |
| `provider_rate_limited` | Yahoo is throttling. It clears within minutes; the source is unofficial and has no SLA. |
| IV rank stuck on "insufficient history" | Working as designed until `OPTIONS_MIN_HISTORY_DAYS` (20) days are stored. Check `/api/health` shows `snapshot_scheduler: true`. |
| History stopped growing | The API machine is suspending. It needs `min_machines_running = 1` and no auto-stop, because it owns the timer. |
