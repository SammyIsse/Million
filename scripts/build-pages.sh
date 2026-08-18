#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Escaper en værdi så den kan stå i en TOML "basic string". Uden dette brækker
# ét enkelt " (eller \) i en secret hele dist/wrangler.toml, og deployet fejler
# på en måde der peger alle andre steder hen end på secret'en.
toml_escape() {
  local s="${1-}"
  s="${s//\\/\\\\}"        # backslash FØRST, ellers dobbelt-escapes de næste
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

# Kræver at en værdi er et rent heltal. Rate-limit-tallene skrives UDEN
# anførselstegn i TOML'en, så en tastefejl her giver ugyldig TOML (i bedste
# fald) eller en meningsløs grænse (i værste).
require_int() {
  case "$2" in
    ''|*[!0-9]*) echo "fejl: $1 skal være et heltal, fik '$2'" >&2; exit 1 ;;
  esac
}

# DEPLOY_ENV=staging bygger madshopper-dev (egen KV/D1, custom domain
# dev.madshopper.dk + den gratis workers.dev-URL som fallback) i stedet for
# produktions-workeren. Bruges af deploy-edge-dev.yml (dev-branch) og kan
# køres lokalt til test.
#
# Fail-safe: KUN de to kendte værdier accepteres. Før faldt ALT andet end
# præcis "staging" (tastefejl som "stage", "Staging", "dev") ned i ELSE-grenen
# og byggede et PRODUKTIONS-bundle med prod-KV, prod-D1 og madshopper.dk-routes
# - den farligst mulige retning at gætte forkert i. Nu fejler vi i stedet.
DEPLOY_ENV="${DEPLOY_ENV:-production}"
case "$DEPLOY_ENV" in
  production|staging) ;;
  *)
    echo "fejl: DEPLOY_ENV='${DEPLOY_ENV}' er ukendt." >&2
    echo "       Gyldige værdier: 'production' (eller tom) og 'staging'." >&2
    echo "       Bygger IKKE - en ukendt værdi må aldrig ende som et prod-bundle." >&2
    exit 1
    ;;
esac

if [ "$DEPLOY_ENV" = "staging" ]; then
  WORKER_NAME="madshopper-dev"
  WORKERS_DEV="true"
  KV_NAMESPACE_ID="b879e69c3a1f477c9c69bbc7e7b041df"
  D1_DATABASE_NAME="madshopper-dev"
  D1_DATABASE_ID="fa7fab55-a5e8-485a-9084-068890e9c8c5"
  SITE_URL_VALUE="https://dev.madshopper.dk"
  TABLE_SUFFIX_VALUE="_dev"
  # Custom domain så staging er nemmere at finde end workers.dev-URL'en
  # (samme adgangsspærring gælder stadig, se STAGING_ACCESS_SECRET nedenfor).
  ROUTES_BLOCK='
[[routes]]
pattern = "dev.madshopper.dk"
custom_domain = true'
  # Rate limit pr. IP. Kun overstyrbar paa staging, og kun til
  # kapacitetsmaaling: en load-test koerer fra ÉN ip og bliver derfor bremset
  # af de normale 150/min, laenge foer serveren er presset - saa maaler man
  # sin egen ip-kvote i stedet for sitets kapacitet (set 2026-07-24: 35 af 36
  # "fejl" var 429, ikke serverfejl). Saet fx RATE_LIMIT_PER_MIN=100000,
  # maal, og deploy bagefter UDEN varen for at faa de 150 tilbage.
  RATE_LIMIT_PER_MIN="${RATE_LIMIT_PER_MIN:-150}"
  require_int RATE_LIMIT_PER_MIN "$RATE_LIMIT_PER_MIN"
  # App-limiteren (app_support.py: api_limiter, 60/min pr. IP) er STRAMMERE end
  # Cloudflares og er den der reelt bremser en load-test fra én IP. Linjen
  # udelades helt naar varen ikke er sat, saa app'ens egen default gaelder.
  API_RATE_LIMIT_LINE=""
  if [ -n "${API_RATE_LIMIT_PER_MIN:-}" ]; then
    require_int API_RATE_LIMIT_PER_MIN "$API_RATE_LIMIT_PER_MIN"
    API_RATE_LIMIT_LINE="API_RATE_LIMIT_PER_MIN = \"${API_RATE_LIMIT_PER_MIN}\""
  fi
else
  WORKER_NAME="madshopper"
  WORKERS_DEV="false"
  KV_NAMESPACE_ID="0e60bdf03ed4490cbfac5fa72c8adca5"
  D1_DATABASE_NAME="madshopper"
  D1_DATABASE_ID="8a43b0d1-1733-4abe-ad71-aa9bde4d4d12"
  SITE_URL_VALUE="https://madshopper.dk"
  TABLE_SUFFIX_VALUE=""
  # Produktion: ALDRIG overstyrbar. En glemt miljoevariabel i en terminal maa
  # ikke kunne saette beskyttelsen ud af kraft paa det rigtige site.
  RATE_LIMIT_PER_MIN=150
  API_RATE_LIMIT_LINE=""
  ROUTES_BLOCK='
[[routes]]
pattern = "madshopper.dk"
custom_domain = true

[[routes]]
pattern = "www.madshopper.dk"
custom_domain = true'
fi

echo "==> EdgeKit build"
uv run edgekit build

# Cache-refresh secret: Cloudflare Python Workers eksponerer kun [vars] i
# os.environ (ikke `wrangler secret`), så vi injicerer den som en var.
# Værdien gemmes lokalt i .edge-secret (ikke i git).
SECRET_FILE="$ROOT/.edge-secret"
if [ -n "${CACHE_REFRESH_SECRET:-}" ]; then
  printf '%s' "$CACHE_REFRESH_SECRET" > "$SECRET_FILE"
elif [ ! -f "$SECRET_FILE" ]; then
  openssl rand -hex 32 > "$SECRET_FILE"
fi
CACHE_REFRESH_SECRET="$(cat "$SECRET_FILE")"
CACHE_REFRESH_SECRET_TOML="$(toml_escape "$CACHE_REFRESH_SECRET")"

# Feedback-sheet-webhook: samme mønster som CACHE_REFRESH_SECRET ovenfor.
# Værdien gemmes lokalt i .feedback-webhook (ikke i git).
WEBHOOK_FILE="$ROOT/.feedback-webhook"
if [ -n "${GOOGLE_SHEET_WEBHOOK_URL:-}" ]; then
  printf '%s' "$GOOGLE_SHEET_WEBHOOK_URL" > "$WEBHOOK_FILE"
fi
GOOGLE_SHEET_WEBHOOK_URL="$(cat "$WEBHOOK_FILE" 2>/dev/null || true)"
GOOGLE_SHEET_WEBHOOK_URL_TOML="$(toml_escape "$GOOGLE_SHEET_WEBHOOK_URL")"

# Staging-adgangsnøgle: madshopper-dev kører den samme kode mod *_dev-tabeller,
# men på en offentlig workers.dev-URL og mod SAMME Supabase-projekt/auth.users
# som produktionen. Uden en spærring er hele feature-fladen frit tilgængelig for
# alle der gætter URL'en. Var'en sættes KUN i staging-bygget; i produktion er
# den tom, og så er spærringen i src/worker.py slået fra.
# Åbn staging første gang med https://<url>/?k=<nøglen> - den sætter en cookie.
STAGING_SECRET_LINE=""
if [ "$DEPLOY_ENV" = "staging" ]; then
  STAGING_FILE="$ROOT/.staging-secret"
  if [ -n "${STAGING_ACCESS_SECRET:-}" ]; then
    printf '%s' "$STAGING_ACCESS_SECRET" > "$STAGING_FILE"
  elif [ ! -f "$STAGING_FILE" ]; then
    openssl rand -hex 24 > "$STAGING_FILE"
    echo "Genereret ny STAGING_ACCESS_SECRET (gemt i .staging-secret)"
  fi
  STAGING_ACCESS_SECRET="$(cat "$STAGING_FILE")"
  STAGING_SECRET_LINE="STAGING_ACCESS_SECRET = \"$(toml_escape "$STAGING_ACCESS_SECRET")\""
  # Nøglen printes KUN ved lokal kørsel. GitHub maskerer ganske vist
  # registrerede secrets i loggen, men gør det ikke for en nøgle som dette
  # script selv har genereret - og bygge-logs er læsbare for alle med adgang
  # til repoet. Lokalt er den derimod netop det, man har brug for at se.
  if [ -z "${CI:-}" ]; then
    echo "==> Staging er adgangsspærret. Åbn med: ${SITE_URL_VALUE}/?k=${STAGING_ACCESS_SECRET}"
  else
    echo "==> Staging er adgangsspærret (nøgle fra STAGING_ACCESS_SECRET)."
  fi
fi

# Staging-login (mail+adgangskode): venligere indgang end secret'en ovenfor
# til et menneske - se _STAGING_LOGIN_PATH i src/worker.py. Samme mønster:
# sat env-var overstyrer, ellers génereres/genbruges en lokal værdi. Er
# nogen af de to ikke sat, udelades linjerne helt, og login-siden falder
# tilbage til det almindelige 404 (kun ?k=-nøglen virker så).
STAGING_EMAIL_LINE=""
STAGING_PASSWORD_LINE=""
if [ "$DEPLOY_ENV" = "staging" ]; then
  EMAIL_FILE="$ROOT/.staging-email"
  if [ -n "${STAGING_ACCESS_EMAIL:-}" ]; then
    printf '%s' "$STAGING_ACCESS_EMAIL" > "$EMAIL_FILE"
  elif [ ! -f "$EMAIL_FILE" ]; then
    printf '%s' "staging@madshopper.dk" > "$EMAIL_FILE"
  fi
  STAGING_ACCESS_EMAIL="$(cat "$EMAIL_FILE")"

  PASSWORD_FILE="$ROOT/.staging-password"
  if [ -n "${STAGING_ACCESS_PASSWORD:-}" ]; then
    printf '%s' "$STAGING_ACCESS_PASSWORD" > "$PASSWORD_FILE"
  elif [ ! -f "$PASSWORD_FILE" ]; then
    openssl rand -hex 8 > "$PASSWORD_FILE"
    echo "Genereret ny STAGING_ACCESS_PASSWORD (gemt i .staging-password)"
  fi
  STAGING_ACCESS_PASSWORD="$(cat "$PASSWORD_FILE")"

  # Escapes: en adgangskode med " eller \ i sig brækkede før hele
  # dist/wrangler.toml, og fejlen så ud som alt andet end en secret.
  STAGING_EMAIL_LINE="STAGING_ACCESS_EMAIL = \"$(toml_escape "$STAGING_ACCESS_EMAIL")\""
  STAGING_PASSWORD_LINE="STAGING_ACCESS_PASSWORD = \"$(toml_escape "$STAGING_ACCESS_PASSWORD")\""
  if [ -z "${CI:-}" ]; then
    echo "==> Login: ${SITE_URL_VALUE}/staging-login  (${STAGING_ACCESS_EMAIL} / ${STAGING_ACCESS_PASSWORD})"
  fi
fi

echo "==> dist/ output"
rm -rf dist
mkdir -p dist

cp -r build/edgekit/wrangler/. dist/
cp -r templates dist/python_modules/templates

# Statiske filer serveres direkte fra Cloudflares CDN under /static/*
# (bypasser worker'en helt → sparer requests + CPU på free-plan). De bundtes
# bevidst IKKE ind i selve Python-workeren (kun i dist/assets nedenfor) -
# duplikatet skubbede workeren over gratisplanens 3 MiB-grænse. Flask-ruten
# /static/<path> er stadig i koden, men rammes reelt aldrig i produktion.
mkdir -p dist/assets/static
cp -r static/. dist/assets/static/

# /favicon.ico hentes UOPFORDRET af browsere, crawlere og link-previews, også
# selvom <head> peger på et andet ikon. Uden filen her ramte den catch-all-ruten
# /<category_name> i app.py og gav "Category not found" - dvs. en worker-
# invocation pr. besøgende for et 404, der aldrig blev cachet (Cache-Control
# sættes kun for status 200). Kopien i assets-roden + undtagelsen i
# _routes.json lader CDN'et svare uden at vække workeren.
cp static/favicon.ico dist/assets/favicon.ico
cat > dist/assets/_headers << 'HEADERS'
/static/*
  Cache-Control: public, max-age=31536000, immutable
  X-Content-Type-Options: nosniff

# Rod-favicon'et har intet ?v= i URL'en og må derfor ikke være immutable -
# ellers kan et nyt ikon ikke slå igennem. Et døgn er rigeligt.
/favicon.ico
  Cache-Control: public, max-age=86400
  Content-Type: image/x-icon
  X-Content-Type-Options: nosniff

/static/css/*
  Content-Type: text/css; charset=utf-8

/static/js/*
  Content-Type: application/javascript; charset=utf-8

# Uden den korrekte type ignorerer Chrome manifestet (og "gem på hjemmeskærm"
# falder tilbage til gættede værdier). .webmanifest er ikke en type CDN'et
# nødvendigvis kender i forvejen.
/static/site.webmanifest
  Content-Type: application/manifest+json; charset=utf-8

/static/favicon.svg
  Content-Type: image/svg+xml; charset=utf-8
HEADERS

printf '%s\n' '"""Autogenerated launcher for Wrangler Python Workers."""' 'from worker import *  # noqa: F403' \
  > dist/python_modules/_edgekit_entrypoint.py

cat > dist/wrangler.toml << WRANGLER
name = "${WORKER_NAME}"
main = "python_modules/_edgekit_entrypoint.py"
compatibility_date = "2026-07-03"
# workers.dev-adressen er slukket for produktion - siden køres kun på
# madshopper.dk (custom domain routes nedenfor). Staging (madshopper-dev)
# har sit eget custom domain (dev.madshopper.dk) men beholder også den
# gratis workers.dev-URL som fallback.
workers_dev = ${WORKERS_DEV}
# edgekit 0.1.1 er bygget til den indbyggede Python-SDK. Den eksterne SDK blev
# default 2026-04-21, så uden dette flag fejler Cloudflares deploy-introspektion
# med "ModuleNotFoundError: No module named 'workers'".
compatibility_flags = ["python_workers", "disable_python_external_sdk"]

# Eksplicit FRA, ikke udeladt - og i ALLE underafsnit, da logs/traces-
# underafsnittene VINDER over [observability].enabled hos Cloudflare. Uden
# denne blok arver hvert nyt deploy Cloudflares platform-standard for
# observability paa byggetidspunktet (bagt ind i wrangler.jsonc pr.
# deployment, ikke en efterfoelgende dashboard-toggle). BEKRAEFTET aarsag
# til produktionsnedbruddene 2026-07-19: persisterede logs viste asyncio-
# reentrancy ("Cannot enter into task ... while another task is being
# executed", kastet fra Cloudflares egen introspection.py i kollision med
# D1-kald under samtidig trafik). Fejlene fulgte builds, ikke kode:
# versioner bygget i vinduet fejlede straks under samtidig trafik, mens
# dashboard-rollback til et aeldre build og dette eksplicitte fra-valg
# (version 77ce1327, 0 fejl) stoppede dem.
[observability]
enabled = false

[observability.logs]
enabled = false

[observability.traces]
enabled = false

[assets]
directory = "assets"
binding = "STATIC"
run_worker_first = false

[[kv_namespaces]]
binding = "CACHE_KV"
id = "${KV_NAMESPACE_ID}"

[[d1_databases]]
binding = "DB"
database_name = "${D1_DATABASE_NAME}"
database_id = "${D1_DATABASE_ID}"

# Gratis native rate limiting (fail-open i worker.py). Beskytter dyre stier
# mod misbrug uden at ramme normale brugere (cache-hits rammes ikke) - dækker
# nu BÅDE alle non-GET requests OG cache-miss GET-renders (tilføjet efter
# 2026-07-19-incidentet, hvor 20 samtidige cold-cache renders væltede siden).
[[unsafe.bindings]]
name = "RATE_LIMITER"
type = "ratelimit"
namespace_id = "1001"
simple = { limit = ${RATE_LIMIT_PER_MIN}, period = 60 }

# Ekstra, STRAMMERE global grænse kun for cart-event/recipe-click (se
# src/worker.py::_cart_rate_ok). app_support.py's cart_event_limiter
# (20/min) er kun pr. isolate - under samtidig trafik på flere isolates kan
# den reelle grænse fra én IP derfor blive højere end tilsigtet, og disse to
# RPC'er kan manipulere cart_popularity/recipe-kliktal (ikke bare koste
# CPU), så en global grænse er værd den ekstra binding. Samme namespace_id-
# mønster som RATE_LIMITER ovenfor (næste ledige id).
[[unsafe.bindings]]
name = "CART_RATE_LIMITER"
type = "ratelimit"
namespace_id = "1002"
simple = { limit = 20, period = 60 }

[vars]
CLOUDFLARE_WORKERS = "1"
ENABLE_PRICE_DB = "0"
SUPABASE_URL = "https://oxzxingkbsnqzpmjtktr.supabase.co"
NEXT_PUBLIC_SUPABASE_URL = "https://oxzxingkbsnqzpmjtktr.supabase.co"
SUPABASE_KEY = "sb_publishable_Jt8N0XezmzfZJSzzSwBBKQ_uGbNoq8f"
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_Jt8N0XezmzfZJSzzSwBBKQ_uGbNoq8f"
CACHE_REFRESH_SECRET = "${CACHE_REFRESH_SECRET_TOML}"
SITE_URL = "${SITE_URL_VALUE}"
# Skrive-tabeller (cart_popularity, price_alerts): "" = produktion, "_dev" =
# dev-kopier (scripts/supabase-dev-tables.sql), så test ikke rører prod-data.
TABLE_SUFFIX = "${TABLE_SUFFIX_VALUE}"
GOOGLE_SHEET_WEBHOOK_URL = "${GOOGLE_SHEET_WEBHOOK_URL_TOML:-}"
${STAGING_SECRET_LINE}
${STAGING_EMAIL_LINE}
${STAGING_PASSWORD_LINE}
${API_RATE_LIMIT_LINE}
${ROUTES_BLOCK}
WRANGLER

# DRIFT-FÆLDE (læs før du "rydder op" i konfigurationen):
#
# 1) _routes.json er et Cloudflare PAGES-koncept. `wrangler deploy` (Workers)
#    læser den ikke - det er [assets] + run_worker_first = false ovenfor der
#    reelt bestemmer at /static/* og /favicon.ico serveres uden om workeren.
#    Filen kopieres med, fordi rod-wrangler.toml stadig har
#    pages_build_output_dir, og fordi en fremtidig Pages-build ellers ville
#    miste undtagelserne. Ændrer du /static- eller favicon-stier, skal BEGGE
#    steder rettes - ellers vågner workeren for hver favicon-request igen.
#
# 2) Rod-wrangler.toml's [env.production]/[env.staging]-blokke er DØD
#    konfiguration for deploy-flowet: både CI og deploy-worker.sh kører
#    `wrangler deploy` fra dist/ og bruger dermed KUN den wrangler.toml der
#    genereres ovenfor. Retter du KV-id, D1-id, vars eller observability i
#    roden, sker der ingenting i produktion. Rod-filen er ikke slettet, fordi
#    den bruges af lokale `wrangler`-kald (fx `wrangler d1 execute madshopper`)
#    og af Pages-konfigurationen - men den er ikke sandheden om et deploy.
cp "$ROOT/_routes.json" dist/_routes.json

rm -rf dist/dist
echo "==> dist klar ($(du -sh dist | cut -f1))"
