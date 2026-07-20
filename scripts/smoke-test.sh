#!/usr/bin/env bash
# Røgtest efter deploy: rammer sitet med SAMTIDIGE requests og fejler ved
# ikke-200-svar. Fanger fejlklassen fra 2026-07-19 (1101 asyncio-reentrancy
# og 1102 CPU-grænse), som kun viser sig når flere renders er i gang på én
# gang - fejlene kom dengang under 2 minutter efter deploy, men blev aldrig
# fanget af enkeltstående funktionstjek uden samtidighed.
#
# Brug: scripts/smoke-test.sh <base-url>
#   fx  scripts/smoke-test.sh https://madshopper.dk
#
# 3 runder x 2 stier x 10 requests (5 parallelle) = 60 requests over ~1,5
# minut. Runde 1 cache-buster med ?smoke=, så alle samtidige requests reelt
# renderer koldt (som lige efter deploy/seed, hvor cache_version-bump gør alt
# koldt). Runde 2-3 rammer de RIGTIGE URL'er, hvor edge-cachen må hjælpe -
# det er sådan trafikken faktisk ser ud.
#
# ADVARSEL - skru IKKE op for PARALLEL/PER_ROUND uden god grund: ved 10
# parallelle x 20 pr. runde med cold-bust i alle runder væltede testen selv
# den ellers stabile produktionsversion 77ce1327 (målt 2026-07-19: 500/503
# fra runde 3, og fejltilstanden VARENDE VED efter belastningen stoppede).
# Free-planens CPU-budget tåler kun begrænset samtidig kold rendering, så
# gaten skal ligge i det realistiske trafikbånd - nok til at fange
# 1101/1102-fejlklassen (i går udløst ved lavere belastning end dette),
# ikke nok til selv at lægge sitet ned.
# Tolerance på 2 ikke-200 i alt, så et enkelt netværksblip ikke fejler et
# ellers sundt deploy.
set -uo pipefail

BASE="${1:?brug: smoke-test.sh <base-url>}"
BASE="${BASE%/}"
ROUNDS=3
PER_ROUND=10
PARALLEL=5
STIER=("/" "/Mejeri")
TOLERANCE=2

total=0
total_bad=0
for round in $(seq "$ROUNDS"); do
  for sti in "${STIER[@]}"; do
    if [ "$round" = "1" ]; then
      url="${BASE}${sti}?smoke=$(date +%s)"
    else
      url="${BASE}${sti}"
    fi
    codes=$(seq "$PER_ROUND" | xargs -P "$PARALLEL" -I{} \
      curl -s -o /dev/null -w '%{http_code}\n' --max-time 30 \
        -H 'User-Agent: madshopper-deploy-smoke' \
        "$url" || true)
    bad=$(printf '%s\n' "$codes" | grep -vc '^200$' || true)
    dist=$(printf '%s\n' "$codes" | sort | uniq -c | xargs)
    echo "runde ${round} ${sti}: ${dist} (${bad} ikke-200)"
    total=$((total + PER_ROUND))
    total_bad=$((total_bad + bad))
  done
  if [ "$round" -lt "$ROUNDS" ]; then sleep 20; fi
done

echo "I alt: ${total_bad} ikke-200 af ${total} requests"
if [ "$total_bad" -gt "$TOLERANCE" ]; then
  echo "::error::Røgtest fejlede (${total_bad}/${total} ikke-200 mod ${BASE}). Mønsteret fra 2026-07-19: fejl kommer straks efter deploy under samtidig trafik. Rul tilbage i Cloudflare-dashboardet (Workers & Pages -> madshopper -> Deployments -> Rollback). Hjælper rollback ikke, er det free-planens CPU-budget der er udtømt af selve belastningen - så vent nogle minutter og genkør, i stedet for at rulle længere tilbage."
  exit 1
fi
echo "OK: ${BASE} holder til samtidig trafik"
