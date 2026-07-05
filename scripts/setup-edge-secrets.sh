#!/usr/bin/env bash
# Sætter cache-refresh secret ens på Worker (via [vars] i build) og GitHub.
# Cloudflare Python Workers eksponerer kun [vars] i os.environ — ikke
# `wrangler secret` — derfor injiceres værdien som en var ved build.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP_URL="${APP_URL:-https://madshopper.dk}"
ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-a592885c7804b0101fa5583ef1f92031}"
SECRET_FILE="$ROOT/.edge-secret"

if [ ! -f "$SECRET_FILE" ]; then
  openssl rand -hex 32 > "$SECRET_FILE"
  echo "Genereret ny CACHE_REFRESH_SECRET (gemt i .edge-secret)"
fi
CACHE_REFRESH_SECRET="$(cat "$SECRET_FILE")"

echo "==> Deploy Worker med secret som [vars]"
CACHE_REFRESH_SECRET="$CACHE_REFRESH_SECRET" bash scripts/deploy-worker.sh >/dev/null

echo "==> GitHub secrets (SammyIsse/Million)"
gh secret set APP_URL --body "$APP_URL"
gh secret set CACHE_REFRESH_SECRET --body "$CACHE_REFRESH_SECRET"
gh secret set CLOUDFLARE_ACCOUNT_ID --body "$ACCOUNT_ID"

echo ""
echo "Færdig. APP_URL=$APP_URL"
