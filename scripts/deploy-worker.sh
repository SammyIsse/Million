#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Samme miljøvalg som build-pages.sh, og samme fail-safe: kun kendte værdier.
# Ukendt værdi = byg og deploy IKKE, i stedet for at gætte på produktion.
DEPLOY_ENV="${DEPLOY_ENV:-production}"
case "$DEPLOY_ENV" in
  production|staging) ;;
  *)
    echo "fejl: DEPLOY_ENV='${DEPLOY_ENV}' er ukendt (brug 'production' eller 'staging')." >&2
    exit 1
    ;;
esac

if [ "$DEPLOY_ENV" = "staging" ]; then
  SITE_URL="https://dev.madshopper.dk"
else
  SITE_URL="https://madshopper.dk"
fi

DEPLOY_ENV="$DEPLOY_ENV" bash scripts/build-pages.sh
cd dist
npx wrangler deploy
cd "$ROOT"

# Hvad cacher HVAD (målt i koden, ikke gættet):
#
#   HTML: cacher KUN i workerens egen Cache API (src/worker.py: caches.default),
#   og nøglen dér indeholder cache_version + dagens UTC-dato (_cache_key /
#   _cache_version). Et cache_version-bump gør derfor ALLE gamle HTML-svar
#   uopnåelige med det samme - en purge er ikke nødvendig for HTML. Browseren
#   får i forvejen Cache-Control: no-store (app.py).
#
#   Statiske filer: serveres UDEN OM workeren ([assets] + run_worker_first =
#   false) og har ingen cache_version i nøglen. De versionerede (?v=) er
#   ligegyldige, men de UVERSIONEREDE - /favicon.ico, /static/site.webmanifest,
#   /static/favicon.svg - kan hænge i CDN'et efter et deploy. DET er den reelle
#   grund til at purge stadig er med her.
#
# Derfor: produktion purger hele zonen (billigst og dækker alt), mens staging
# KUN må file-purge sine egne URL'er. dev.madshopper.dk ligger i SAMME zone som
# madshopper.dk, så et purge_everything herfra ville gøre hele produktionen kold
# - præcis den cold-render-situation der giver 1101-fejl.
if [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ZONE_ID:-}" ]; then
  if [ "$DEPLOY_ENV" = "production" ]; then
    echo "==> Sæt Browser Cache TTL = Respect Existing Headers"
    # 0 = Respect Existing Headers. Uden dette overskrev zonen max-age=0 til
    # 4 timer, så besøgende sad med gammel HTML (og gamle ?v=-assets) efter deploy.
    # Zone-indstilling = hele madshopper.dk, så den sættes KUN fra produktion.
    bcttl=$(curl -s -o /tmp/cf-bcttl.json -w '%{http_code}' -X PATCH \
      "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/settings/browser_cache_ttl" \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      --data '{"value":0}' || echo 000)
    if [ "$bcttl" = "200" ]; then
      echo "Browser Cache TTL sat til Respect Existing Headers"
    else
      echo "advarsel: kunne ikke sætte Browser Cache TTL (HTTP $bcttl) - HTML bruger stadig no-store"
    fi

    echo "==> Purger Cloudflare CDN-cache (hele zonen)"
    code=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
      "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/purge_cache" \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      --data '{"purge_everything":true}' || echo 000)
  else
    echo "==> Purger KUN staging-URL'er (purge_everything ville nulstille produktionen)"
    # files-purge virker på alle planer; 'hosts'/'prefixes' kræver Enterprise.
    # Listen er de uversionerede statiske filer - HTML klarer cache_version.
    code=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
      "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/purge_cache" \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      --data "{\"files\":[\"${SITE_URL}/favicon.ico\",\"${SITE_URL}/static/site.webmanifest\",\"${SITE_URL}/static/favicon.svg\"]}" || echo 000)
  fi
  if [ "$code" = "200" ]; then
    echo "CDN-cache purget (HTTP 200)"
  else
    echo "advarsel: cache-purge svarede HTTP $code - deploy er stadig gennemført"
  fi
else
  echo "advarsel: CLOUDFLARE_API_TOKEN / CLOUDFLARE_ZONE_ID ikke sat - CDN-cache er IKKE purget."
  echo "Uversionerede statiske filer (favicon m.fl.) kan hænge i CDN. Purge evt. manuelt i Cloudflare-dashboardet."
fi

# Efter cache_version-bump (CI) / CDN-purge er siderne kolde. Opvarm de
# vigtigste URL'er sekventielt før du sender trafik eller kører smoke-test,
# ellers risikerer du Error 1101 (se scripts/warm-edge-cache.mjs).
# Varmer SITE_URL for det miljø vi lige deployede - ikke hardcodet produktion.
if command -v node >/dev/null 2>&1 && [[ -f "$ROOT/scripts/warm-edge-cache.mjs" ]]; then
  echo "==> Opvarmer edge-cache (${SITE_URL})"
  WARM_KEY=""
  if [ "$DEPLOY_ENV" = "staging" ]; then
    # Staging er adgangsspærret og svarer 404 uden nøglen (src/worker.py).
    WARM_KEY="${STAGING_ACCESS_SECRET:-$(cat "$ROOT/.staging-secret" 2>/dev/null || true)}"
  fi
  (cd "$ROOT" && node scripts/warm-edge-cache.mjs "$SITE_URL" "$WARM_KEY") || \
    echo "advarsel: warmup fejlede - kør manuelt: node scripts/warm-edge-cache.mjs $SITE_URL"
fi
