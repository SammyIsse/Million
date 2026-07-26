#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
bash scripts/build-pages.sh
cd dist
npx wrangler deploy

# Cloudflares CDN cacher HTML via CDN-Cache-Control (se app.py), uafhængigt
# af worker'ens egen cache_version-nøgle. Uden purge her kan en deploy være
# maskeret af gammel HTML i CDN i op til 24 timer. Browseren får no-store og
# cacher derfor ikke HTML lokalt.
if [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CLOUDFLARE_ZONE_ID:-}" ]; then
  echo "==> Sæt Browser Cache TTL = Respect Existing Headers"
  # 0 = Respect Existing Headers. Uden dette overskrev zonen max-age=0 til
  # 4 timer, så besøgende sad med gammel HTML (og gamle ?v=-assets) efter deploy.
  bcttl=$(curl -s -o /tmp/cf-bcttl.json -w '%{http_code}' -X PATCH \
    "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/settings/browser_cache_ttl" \
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    -H "Content-Type: application/json" \
    --data '{"value":0}')
  if [ "$bcttl" = "200" ]; then
    echo "Browser Cache TTL sat til Respect Existing Headers"
  else
    echo "advarsel: kunne ikke sætte Browser Cache TTL (HTTP $bcttl) - HTML bruger stadig no-store"
  fi

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
  echo "Gammel HTML kan blive vist i CDN i op til 24 timer. Purge manuelt i Cloudflare-dashboardet (Caching -> Purge Everything)."
fi

# Efter cache_version-bump (CI) / CDN-purge er siderne kolde. Opvarm de
# vigtigste URL'er sekventielt før du sender trafik eller kører smoke-test,
# ellers risikerer du Error 1101 (se scripts/warm-edge-cache.mjs).
if command -v node >/dev/null 2>&1 && [[ -f "$ROOT/scripts/warm-edge-cache.mjs" ]]; then
  echo "==> Opvarmer edge-cache"
  (cd "$ROOT" && node scripts/warm-edge-cache.mjs https://madshopper.dk) || \
    echo "advarsel: warmup fejlede - kør manuelt: node scripts/warm-edge-cache.mjs https://madshopper.dk"
fi
