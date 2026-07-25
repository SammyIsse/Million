#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
bash scripts/build-pages.sh
cd dist
npx wrangler deploy

# Cloudflares CDN cacher HTML-sider ud fra Cache-Control: s-maxage (se app.py),
# uafhængigt af worker'ens egen cache_version-nøgle. Uden purge her kan en
# deploy være maskeret af gammel cachet HTML i op til 24 timer.
if [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ZONE_ID:-}" ]; then
  echo "==> Purger Cloudflare CDN-cache"
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/purge_cache" \
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    -H "Content-Type: application/json" \
    --data '{"purge_everything":true}')
  if [ "$code" = "200" ]; then
    echo "CDN-cache purget (HTTP 200)"
  else
    echo "advarsel: cache-purge svarede HTTP $code - deploy er stadig gennemført"
  fi
else
  echo "advarsel: CLOUDFLARE_API_TOKEN / CLOUDFLARE_ZONE_ID ikke sat - CDN-cache er IKKE purget."
  echo "Gammel HTML kan blive vist i op til 24 timer. Purge manuelt i Cloudflare-dashboardet (Caching -> Purge Everything)."
fi

# Efter cache_version-bump (CI) / CDN-purge er siderne kolde. Opvarm de
# vigtigste URL'er sekventielt før du sender trafik eller kører smoke-test,
# ellers risikerer du Error 1101 (se scripts/warm-edge-cache.mjs).
if command -v node >/dev/null 2>&1 && [[ -f "$ROOT/scripts/warm-edge-cache.mjs" ]]; then
  echo "==> Opvarmer edge-cache"
  (cd "$ROOT" && node scripts/warm-edge-cache.mjs https://madshopper.dk) || \
    echo "advarsel: warmup fejlede - kør manuelt: node scripts/warm-edge-cache.mjs https://madshopper.dk"
fi
