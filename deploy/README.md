# Deploying

Single host, Docker Compose behind nginx, with a systemd timer running the daily
snapshot.

> **Status: written, not yet run.** These files were authored and checked for
> syntax, but they have not been built or deployed — the development machine has
> no Docker installed and there is no target host. Until someone runs the steps
> below successfully, nothing in this repository claims a live deployment. If you
> are that someone and a step is wrong, the fix belongs in this file.

## What runs where

| Piece | Where | Why |
|---|---|---|
| `api` | container, bound to `127.0.0.1:8000` | not exposed directly; nginx terminates TLS |
| `web` | container, bound to `127.0.0.1:3000` | same |
| `options-data` | named Docker volume | the accumulated IV history — the only irreplaceable state here |
| snapshot | systemd timer, `docker compose run --rm` | survives reboots and logs to the journal |

## 1. Host setup

Tested target: Ubuntu 24.04 LTS, 1 vCPU / 1 GB. SQLite and a handful of chains
need very little.

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"   # log out and back in
```

## 2. Get the code

```bash
sudo mkdir -p /opt/options-analytics
sudo chown "$USER":"$USER" /opt/options-analytics
git clone https://github.com/Tejas12972/stockwatchlist.git /opt/options-analytics
cd /opt/options-analytics
```

## 3. Configure

```bash
cp .env.example .env
```

Set `PUBLIC_API_URL` to the URL **the browser** will call — that is
`https://your-domain/api`, not `http://api:8000`. The Next.js client bundle bakes
this in at build time, so changing it later means rebuilding the web image, not
just restarting it. This is the single most common way to get a working deploy
that shows an empty page.

## 4. Build and start

```bash
docker compose build
docker compose up -d
docker compose ps          # both services should be healthy
curl -s localhost:8000/health
```

## 5. nginx and TLS

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/options-analytics
sudo sed -i "s/options.example.com/YOUR-DOMAIN/g" /etc/nginx/sites-available/options-analytics
sudo ln -sf /etc/nginx/sites-available/options-analytics /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

sudo nginx -t                       # must pass before reloading
sudo systemctl reload nginx
sudo certbot --nginx -d YOUR-DOMAIN
```

`certbot` rewrites the TLS certificate paths in place, so run it after copying
the config, not before.

## 6. The daily snapshot

```bash
sudo cp deploy/options-snapshot.service deploy/options-snapshot.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now options-snapshot.timer

systemctl list-timers options-snapshot.timer
sudo systemctl start options-snapshot.service   # run once now
journalctl -u options-snapshot.service -n 50 --no-pager
```

The timer fires weekdays at 21:15 UTC — after the US close in both EST and EDT,
so the captured chain is the settled one rather than a mid-session reading whose
volatility depends on what time the job happened to run.

`Persistent=true` matters more than it looks: if the host is down at the
scheduled time the run happens at next boot instead of being skipped. **A missed
day is a permanent hole in the IV history** — historical implied volatility is
not purchasable from any free source, so it cannot be backfilled.

## 7. Verify

```bash
# Idempotency: the second run must not add rows.
sudo systemctl start options-snapshot.service
docker compose run --rm snapshot | tail -2   # expect "refreshed", not "captured"

# Migrations are applied with the API stopped, against the same volume.
docker compose run --rm api alembic upgrade head
```

## Backups

Only the volume matters. Everything else is rebuildable from git.

```bash
docker run --rm -v options-analytics_options-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/options-history-$(date +%F).tar.gz -C /data .
```

Worth putting on its own weekly timer. The database is small — a year of daily
snapshots for a handful of tickers is tens of megabytes — and it is the only
thing here that cannot be recreated.

## Upgrading

```bash
cd /opt/options-analytics
git pull
docker compose run --rm api alembic upgrade head   # before starting new code
docker compose build
docker compose up -d
```

Run the migration before the new containers start, so the schema is never behind
the code reading it.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Pages load, all data empty | `PUBLIC_API_URL` wrong, or set after the web image was built. Rebuild the web image. |
| `provider_rate_limited` in the UI | Yahoo is throttling. It clears within minutes; the source is unofficial and has no SLA. |
| IV rank says "insufficient history" | Working as designed. It needs `OPTIONS_MIN_HISTORY_DAYS` (default 20) stored days. |
| Timer never fires | `systemctl list-timers`; check the unit is enabled and the host clock is in UTC. |
| Snapshot writes nothing | `journalctl -u options-snapshot.service`. A ticker failing does not abort the rest of the run, so check per-symbol warnings. |
