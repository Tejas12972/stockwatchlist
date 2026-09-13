#!/usr/bin/env bash
#
# One-shot Fly.io bootstrap. Idempotent — safe to re-run after a partial failure.
#
#   fly auth login          # once, interactively
#   ./deploy/fly-bootstrap.sh
#
# Creates both apps, the volume, and the login secret, then deploys API first
# and web second. Prints the generated password once; Fly secrets are write-only
# and will not show it back to you.

set -euo pipefail

API_APP="${API_APP:-options-analytics-api}"
WEB_APP="${WEB_APP:-options-analytics-web}"
REGION="${REGION:-bos}"
VOLUME_NAME="${VOLUME_NAME:-options_data}"
VOLUME_SIZE_GB="${VOLUME_SIZE_GB:-1}"
APP_USERNAME="${APP_USERNAME:-tejas}"

FLY="${FLY:-flyctl}"
command -v "$FLY" >/dev/null 2>&1 || FLY="$HOME/.fly/bin/flyctl"
command -v "$FLY" >/dev/null 2>&1 || {
  echo "flyctl not found. Install it with: curl -L https://fly.io/install.sh | sh" >&2
  exit 1
}

cd "$(dirname "$0")/.."

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

"$FLY" auth whoami >/dev/null 2>&1 || {
  echo "Not logged in. Run: $FLY auth login" >&2
  exit 1
}

say "Creating apps (skipped if they already exist)"
"$FLY" apps create "$API_APP" 2>/dev/null || echo "  $API_APP already exists"
"$FLY" apps create "$WEB_APP" 2>/dev/null || echo "  $WEB_APP already exists"

say "Creating the data volume"
# The volume holds the accumulated IV history — the only state here that cannot
# be re-downloaded from anywhere, at any price.
if "$FLY" volumes list -a "$API_APP" 2>/dev/null | grep -q "$VOLUME_NAME"; then
  echo "  volume $VOLUME_NAME already exists"
else
  "$FLY" volumes create "$VOLUME_NAME" \
    --size "$VOLUME_SIZE_GB" --region "$REGION" -a "$API_APP" --yes
fi

say "Setting the login"
if "$FLY" secrets list -a "$WEB_APP" 2>/dev/null | grep -q APP_PASSWORD; then
  echo "  APP_PASSWORD already set; leaving it alone."
  echo "  To rotate: $FLY secrets set APP_PASSWORD=... -a $WEB_APP"
else
  APP_PASSWORD="$(openssl rand -base64 24)"
  "$FLY" secrets set \
    APP_USERNAME="$APP_USERNAME" \
    APP_PASSWORD="$APP_PASSWORD" \
    -a "$WEB_APP"
  printf '\n  \033[1;33mSave these now — Fly will not show the password again:\033[0m\n'
  printf '    username: %s\n    password: %s\n\n' "$APP_USERNAME" "$APP_PASSWORD"
fi

# API first: the web app's config points at $API_APP.internal, and deploying it
# against an app that does not exist yet fails its health check.
say "Deploying the API (private — no public address)"
"$FLY" deploy -c fly.api.toml -a "$API_APP"

say "Deploying the web app (public, behind Basic auth)"
"$FLY" deploy -c fly.web.toml -a "$WEB_APP"

say "Seeding the watchlist"
# The daily job snapshots whatever is on the watchlist, so an empty one means it
# does nothing at all.
"$FLY" ssh console -a "$API_APP" -C "python -m options_tool watchlist --add SPY AAPL" || \
  echo "  (seed failed — add symbols in the UI instead)"

say "Done"
cat <<SUMMARY

  App:      https://$WEB_APP.fly.dev
  Username: $APP_USERNAME

  The API is deliberately NOT reachable from the internet. Verify:
    curl -s -o /dev/null -w '%{http_code}\n' https://$WEB_APP.fly.dev/          # 401
    curl -s -o /dev/null -w '%{http_code}\n' https://$WEB_APP.fly.dev/healthz   # 200

  Confirm the daily snapshot is scheduled (this is the one that matters — if it
  is false, no history accumulates and IV rank never arrives):
    curl -s -u $APP_USERNAME:PASSWORD https://$WEB_APP.fly.dev/api/health

  Logs:
    $FLY logs -a $API_APP

SUMMARY
