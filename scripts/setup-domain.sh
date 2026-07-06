#!/usr/bin/env bash
# Opsæt madshopper.dk på Cloudflare Workers.
# Kræver: wrangler login, gh auth (til GitHub secrets).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DOMAIN="madshopper.dk"
APP_URL="https://${DOMAIN}"
CF_NS1="cleo.ns.cloudflare.com"
CF_NS2="melina.ns.cloudflare.com"

echo "==> MadShopper domæneopsætning: ${DOMAIN}"
echo ""

if ! npx wrangler whoami >/dev/null 2>&1; then
  echo "Kør først: npx wrangler login"
  exit 1
fi

echo "==> Deploy Worker med custom domains"
if [ -f .edge-secret ]; then
  CACHE_REFRESH_SECRET="$(cat .edge-secret)"
else
  CACHE_REFRESH_SECRET="${CACHE_REFRESH_SECRET:-}"
fi
CACHE_REFRESH_SECRET="$CACHE_REFRESH_SECRET" bash scripts/build-pages.sh
cd dist
if ! npx wrangler deploy; then
  echo ""
  echo "Deploy fejlede - tjek at zonen er aktiv i Cloudflare."
  exit 1
fi

echo ""
echo "==> Opdater GitHub secrets"
if command -v gh >/dev/null 2>&1; then
  gh secret set APP_URL --body "$APP_URL" -R SammyIsse/Million 2>/dev/null || \
    gh secret set APP_URL --body "$APP_URL"
  echo "APP_URL sat til ${APP_URL}"
else
  echo "gh ikke fundet - sæt APP_URL=${APP_URL} manuelt i GitHub → Settings → Secrets"
fi

echo ""
echo "Færdig på Cloudflare-siden."
echo ""
echo "Hvis ${DOMAIN} endnu ikke virker, skift nameservere hos simply.dk til:"
echo "  1. ${CF_NS1}"
echo "  2. ${CF_NS2}"
echo ""
echo "Test bagefter:"
echo "  curl -I ${APP_URL}/"
echo "  curl ${APP_URL}/robots.txt"
