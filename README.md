# MadShopper - Danish Grocery Price Comparison

A web application that aggregates and compares grocery prices across major Danish supermarkets, helping users find the cheapest options and plan their shopping.

Live site: [madshopper.dk](https://madshopper.dk)

## Features

- **Price comparison** across 14+ stores: Rema 1000, Bilka, Netto, Føtex, Meny, Spar, SuperBrugsen, Brugsen, Kvickly, Min Købmand, 365 Discount, Lidl, Løvbjerg, ABC Lavpris
- **Shopping cart** with cheapest-store routing - find the optimal store combination for your basket
- **Price history** (30 days) stored in Supabase, updated daily via `updater.py`, incl. a "30-day low" badge on product cards (`price_history_low30` view)
- **Product search** with fuzzy matching and abbreviation normalization
- **AI-assisted product classification** using a local Ollama model (Gemma 3)
- **Nutrition data** per product card (Rema API → Salling Algolia → Open Food Facts fallback), built offline by `scripts/build-nutrition.py`
- **Cart popularity** ("Brugernes Favoritter" on the front page) - ranked by real add-to-cart clicks, atomically counted via a Supabase RPC
- **Price alerts** - users can set a target price per product (`POST /api/create-alert`); persisted to `price_alerts`, notification delivery not yet built (see `docs/Features.md` / `docs/prisovervaagning.md`)
- User feedback, relayed to a Google Sheet via `scripts/relay-feedback-to-sheet.py`

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask |
| Production | Cloudflare Workers (EdgeKit/Pyodide), D1 (product cache mirror), KV (`cache_version`, `home_data_v1`, edge response cache) |
| Scrapers | Selenium, Requests |
| Database | Supabase (`app_cache`, `produkter`, `price_history`, `nutrition_data`, `cart_popularity`, `price_alerts`, `feedback`) |
| Fuzzy search | RapidFuzz |
| Frontend | Jinja2 templates, vanilla JS |
| AI classifier | Ollama (`gemma3:4b`) - local, no API key needed |
| CI/CD | GitHub Actions (per-store scrapers, cache updater, edge deploy, smoke tests, uptime check) |
| Deploy/smoke tests | Playwright (Node) - `scripts/smoke-test.mjs`, `scripts/playwright-uptime-check.mjs` |

## Supported Stores

| Store | Scraper |
|---|---|
| Rema 1000 | Rema XML feed (`updater.py`) |
| Bilka | `scraper/bilka_katalog.py` (Algolia-katalog, komplet med pris) |
| Netto | `scraper/netto_katalog.py` (Algolia-katalog, primær pris) + `scraper/webscrape_netto.py` (Tjek tilbudsavis) |
| Føtex | `scraper/foetex_katalog.py` (Algolia-katalog, primær pris) + `scraper/webscrape_foetex.py` (Tjek tilbudsavis) |
| Meny | `scraper/webscrape_Meny.py` (wrapper om `scraper/dagrofa_scraper.py`) |
| Spar | `scraper/webscrape_spar.py` (wrapper om `scraper/dagrofa_scraper.py`) |
| SuperBrugsen | `scraper/webscrape_superbrugsen.py` |
| Brugsen | `scraper/webscrape_brugsen.py` |
| Kvickly | `scraper/webscrape_kvickly.py` |
| Min Købmand | `scraper/webscape_minkøbmand.py` (wrapper om `scraper/dagrofa_scraper.py`) |
| 365 Discount | `scraper/webscrape_365discount.py` (Tjek tilbudsavis) |
| Lidl | `scraper/lidl_katalog.py` (hyldepriser, primær) + `scraper/webscrape_lidl.py` (Tjek tilbudsavis) |
| Løvbjerg | `scraper/webscrape_lovbjerg.py` (Tjek tilbudsavis, via `scraper/tjek_tilbud_scraper.py`) |
| ABC Lavpris | `scraper/webscrape_abc_lavpris.py` (Tjek tilbudsavis, via `scraper/tjek_tilbud_scraper.py`) |

Meny, Spar og Min Købmand kører på samme Dagrofa-webshopplatform, så al scraping-logik ligger samlet i `scraper/dagrofa_scraper.py` - hver butik gemmes dog stadig helt separat i Supabase. Netto, Føtex og 365 Discount henter tilbudsavis via Tjek/ShopGun-API'et (samme mønster som `scraper/tjek_tilbud_scraper.py`, men med egen inline-kopi).

## Getting Started

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) (optional, for AI product classification)

### Installation

```bash
git clone https://github.com/your-username/million.git
cd million

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `FLASK_DEBUG` | Set to `1` for development mode |
| `PORT` | Port to run the server on (default: 5001) |
| `ENABLE_PRICE_DB` | `1` to force-enable Supabase features, `0` to disable |
| `NEXT_PUBLIC_SUPABASE_URL` | Your Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Your Supabase publishable key |
| `TABLE_SUFFIX` | Suffix for write tables (`cart_popularity`, `price_alerts`). Empty in production, `_dev` locally/staging so tests never touch prod data (see `scripts/supabase-dev-tables.sql`) |
| `DEPLOY_KEY` | Supabase service key (scrapers/updater only - not needed for local app) |
| `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ZONE_ID` | Only needed locally so `scripts/deploy-worker.sh` can purge the Cloudflare CDN cache after a manual deploy |
| `GOOGLE_SHEET_WEBHOOK_URL` | Apps Script webhook that forwards feedback to a Google Sheet (production instead buffers feedback in D1 and relays it via `feedback-relay.yml`) |

### Run

```bash
python app.py
```

The app will be available at `http://localhost:5001`. Local runs always use the `_dev` write tables (see `TABLE_SUFFIX` above), so testing never pollutes production stats.

### Dev / staging environment

A second Cloudflare Worker (`madshopper-dev`, own KV namespace + D1 database, `env.staging` in `wrangler.toml`) exists to test features on a real edge deployment without touching production:

- Live at `https://madshopper-dev.kasp478g.workers.dev` (no custom domain)
- Reads share production's Supabase tables (always-fresh product data); writes go to the `_dev` tables
- Push to the `dev` branch → `deploy-edge-dev.yml` deploys automatically; merge `dev` into `main` → `deploy-edge.yml` deploys to production
- Full workflow and one-time setup: `docs/Dev.md`

### Run cache updater

```bash
python updater.py
```

Rebuilds the product cache from Rema XML + Supabase store data and records daily price history.

### Edge architecture & caching

Production runs behind Cloudflare's edge, not against Supabase directly:

- **D1** holds a read-only mirror of the product cache, seeded nightly from Supabase by `scripts/seed-d1.py` (after `updater.py` finishes).
- **KV** holds `cache_version` (bumped on every seed; the cache key is versioned with it, so the daily refresh instantly invalidates all edge caches - no staleness window) and `home_data_v1`, a precomputed JSON blob of the front page's three candidate pools (Ugens Tilbud, Køl, Brugernes Favoritter). `app.py::home()` reads it on edge instead of issuing ~4 live D1/Supabase calls per render - store filtering stays per-request since it depends on the visitor's cookie/query param. If the key is missing, `home()` fails open to the old live calls.
- **Cache API** (`src/worker.py`) stores full rendered GET responses per versioned key with a 24h TTL, skipped entirely for AJAX fragment requests (which lack `<head>`/CSS and would otherwise get served as a full page to the next visitor).

This design traces back to the 2026-07-19 outage where concurrent cold renders (all visitors hitting an unversioned cache at once after a nightly reseed) triggered Cloudflare's 1101/1102 CPU-limit errors; see `docs/Dev.md` and the commit history around `scripts/seed-d1.py` for the full incident trail.

### Deployment & CI

All deploys and data refreshes run via GitHub Actions (`.github/workflows/`):

| Workflow | Purpose |
|---|---|
| `scraper-*.yml` | One workflow per store, runs nightly |
| `trigger-cache-updater.yml` | Fires `cache-updater.yml` once Meny/Spar/Min Købmand finish |
| `nightly-dispatcher.yml` | DST-aware fallback trigger for the scrapers/cache updater (GitHub cron is UTC-only) |
| `cache-updater.yml` | Runs `updater.py`, then `scripts/seed-d1.py` (D1 reseed, `home_data_v1`, `cache_version`) |
| `build-nutrition.yml` | Runs after the cache updater; incrementally fills `nutrition_data` via `scripts/build-nutrition.py` |
| `deploy-edge.yml` / `deploy-edge-dev.yml` | Builds and deploys the Worker to production / staging, then runs the Playwright smoke test |
| `canary-upload.yml` | Uploads a new Worker version with `wrangler versions upload` - no traffic shifted, manual trigger only |
| `purge-cdn.yml`, `rebuild-full-cache.yml` | Manual-dispatch maintenance workflows |
| `uptime-check.yml` | Playwright-based uptime probe every 5 minutes, e-mails on failure |
| `feedback-relay.yml` | Every 20 min, relays feedback buffered in D1 to the Google Sheet |
| `dependency-audit.yml` | Scheduled dependency vulnerability check |

### Product matching (`updater.py`)

Products are classified into three **stages** by EAN status. Only stage 3 initiates fuzzy matching; stages 1 and 2 are passive targets.

| Stage | Condition | Behaviour |
|---|---|---|
| **1 - EAN match** | Same EAN in ≥2 stores | Grouped by EAN (exact match, no fuzzy) |
| **2 - EAN, no match** | EAN present but only in one store | Standalone card; passive fuzzy target |
| **3 - No EAN** | No EAN on product | **Only stage that initiates fuzzy matching** |

**Fuzzy matching attributes** (stage 3 initiator; evaluated as hard gates + name score):

- **Name** - product name similarity (primary score). Names are normalized
  first (`normalize_name`): lowercased, accents stripped, periods/slashes
  become word breaks, apostrophes removed ("Lay's" ↔ "Lays"), and common
  Danish abbreviations expanded (hk→hakket, fuldk→fuldkorn, eks→ekstra,
  kyl→kylling, kart→kartoffel, champ→champignon, sdj→sønderjysk, ...) so
  Rema's terse feed ("HK. OKSEKØD 4-7%") can meet full store names
  ("Hakket oksekød 4-7% fedt")
- **Type** - food category (`unify_category`); store categories are noisy (the same
  jam sits under "Kolonial" at Rema and "Frost" at Salling), so a mismatch only
  rejects when the name score is below 0.80
- **Weight** - total weight/volume (`_weight_g`); multipacks like `6 x 0.33 liter`
  are parsed as totals so a single can never matches a 6-pack. The absolute
  tolerance floor (20 g) scales down to 25% of the weight for small items, so
  a 20 g pastille tin no longer matches a 40 g tin (spices, gum, chocolate bars)
- **Percentages** - fat/alcohol/cocoa percentages stated in the names are real
  product properties: "Tuborg Classic 4,6%" ≠ "Tuborg Classic 0,0%", "Piskefløde
  38%" ≠ "36%". Rejected only when BOTH sides state percentages and none agree -
  a side that simply omits the number is not a contradiction. Deliberately NOT
  relaxed by matching photos (alcohol-free bottles share the regular design).
  On the candidate side the brand field is included in the extraction - the
  Lidl feed states the fat percentage there ("MADVÆRKET Hakket oksekød" /
  producer "14-18 % fedt.")
- **Meat type** (`get_meat_types`/`_meats_match`) - okse/gris/kylling/kalv/
  lam/skinke/kalkun/tun/laks. Symmetric like the percent gate: when BOTH
  sides name meat types the sets must be identical - minced-meat variants
  share weight, fat percentage and almost the entire name across meat types,
  so "HK. OKSEKØD" must match neither "Hakket kyllingekød" nor the blend
  "Hakket okse- og kyllingekød". Silence on either side is not a
  contradiction ("FRIKADELLER" may still match "Frikadeller m. svinekød").
  No photo relaxation (packaging is near-identical across meat types); also
  enforced in the image dedup (`_dedup_same_product`)
- **Quantity** - number of units in the package (`_stk_count`); separate from weight.
  Parsed from the weight field, with a loose fallback to the product name
  ("Avocado 3 Stk.") - eggs/tea/produce often carry the count only there
- **Price sanity** (two-sided) - a candidate more than 5× cheaper OR more expensive
  than the Rema price is rejected (catches single can vs 6-pack at weight-less
  Dagrofa stores)
- **Flavor/form/variant** - candidate must not claim a flavor (chocolate, thyme,
  garlic, onion, paprika, bacon, ...), fish type (tun ≠ makrel ≠ laks ≠ ørred, ...),
  product form (drik/budding/...) or variant
  (øko/laktosefri/...) that the base product's own text doesn't mention. In the
  cross-store phases (2/2b) the flavor gate is symmetric since both sides are
  terse store names. Flavor keywords require a word boundary on at least one
  side of the hit ('cola' no longer fires inside "chocolat"), compound suffixes
  (-smag/-fyld/-overtræk/-stang) are stripped first so "saltkaramelsmag" still
  yields caramel, and longer keywords consume their text so "hvidløg" (garlic)
  never also yields "løg" (onion).
- **Weight-less candidates** - a candidate with neither weight, EAN nor a
  comparable unit count (typical for Dagrofa/Løvbjerg feeds) can't be validated
  by any physical gate, so the name score alone must reach 0.75 instead of the
  usual floor (relaxed for near-identical photos, and skipped for fruit &
  vegetables where loose produce is weight-less everywhere).
- **Image (pHash)** - boost + gate relaxations; relaxations beyond Hamming
  distance 8 (up to 12) require the two brands to actually match, so
  standardised packaging can't carry unrelated names over the threshold

**Updater pipeline** (in `fetch_and_parse_xml`):

1. **Rema annotation** - each Rema product (no EAN) is matched to comparison stores via `_find_generic_match` (acts as stage-3 initiator).
   - **EAN retro-validation**: when a match carries an EAN, that EAN is looked up in
     every store; if any hit's weight OR stated percentage contradicts Rema's, all
     matches with that EAN are dropped (kills wrong fuzzy matches against weight-less
     Dagrofa listings - or ones missing the literal '%' sign, e.g. "Tuborg Classic
     0,0 6-Pk Ds" - using another store's richer data for the same EAN).
   - **Cross-member validation** (`_drop_cross_conflicting_matches`): the per-store gates
     compare each candidate against the Rema product only, and omission is deliberately
     lenient - so when the Rema text itself states no weight/percentage, two stores'
     matches can contradict EACH OTHER (Netto "Grillpølser 81 % kød" and Bilka
     "Grillpølser 62% kød" on the same Rema card). At most one can be the Rema product;
     without an arbiter all conflicting members are dropped. Pairs sharing an EAN are
     exempt (authoritatively the same product despite label drift, e.g. 1,5% vs 1,6%).
   - **EAN cross-fill** into stores that missed is weight-, quantity- and percentage-gated the same way.
2. **Phase 1** - stage-1 EAN grouping across unmatched comparison-store products.
3. **Phase 2** - stage 3 initiates fuzzy vs remaining unmatched products (including stage-2
   passive targets). Candidates are additionally validated against the members already
   accepted into the cluster (`_group_compatible`), so a weight-/percentage-less base
   can't collect mutually contradicting variants.
4. **Phase 2b** - stage 3 initiates fuzzy vs existing stage-1 EAN groups (passive targets).
   A candidate is validated against **every** member of the group, not just the one it
   fuzzy-matched (`_group_compatible`): the group's members are authoritatively the same
   product (shared EAN), so a single member with an incompatible weight, unit count or
   percentage rejects the whole group. Without this, a weight-less member (typically
   Dagrofa) acts as a backdoor into a group whose other members carry a contradicting
   weight (e.g. Lidl "BELBAKE Fødselsdagsboller 350 g" entering a 500 g group via mk's
   weight-less "Amo Fødselsdagsboller").
5. **Solokort** - remaining stage-2 and unmatched stage-3 products become standalone cards.
6. **Image dedup** - cards sharing an image URL are merged, but only after a sanity
   check (`_dedup_same_product`): compatible weights, matching unit counts (a 6-pack
   and a 10-pack of eggs share photos in the Salling feed), no conflicting
   percentages (alcohol-free beer shares the regular bottle photo) and minimally
   similar names.
   Salling reuses the same photo across pack sizes (0.33 l vs 24-pack), which must
   stay separate cards.

**Key rule:** Stages 1 and 2 never initiate fuzzy matching. They can only be matched *against* by a stage-3 product.

### Verify integrations & smoke tests

```bash
python scripts/verify-integrations.py

# Concurrent-request smoke test against a deployed site (run automatically
# after deploy-edge.yml / deploy-edge-dev.yml)
node scripts/smoke-test.mjs https://madshopper.dk

# Uptime probe used by uptime-check.yml (every 5 min, real headless browser -
# curl can't pass Cloudflare's free Bot Fight Mode JS challenge)
node scripts/playwright-uptime-check.mjs https://madshopper.dk/
```

## Project Structure

```
Million-main/
├── app.py               # Flask application and API routes
├── app_support.py       # Logging, caching, search index helpers
├── updater.py           # Rebuilds product cache + price history
├── src/worker.py        # Cloudflare Workers entry point
├── scraper/
│   ├── ai_classifier.py     # Ollama-based food/non-food classifier
│   ├── keywords.py          # Keyword lists for classification
│   ├── scraper_utils.py     # Shared Selenium scraper utilities
│   ├── supabase_utils.py    # Supabase sync helpers
│   ├── dagrofa_scraper.py   # Shared scraper for Spar/Meny/Min Købmand
│   ├── tjek_tilbud_scraper.py # Shared Tjek/ShopGun tilbudsavis-scraper
│   ├── *_katalog.py         # Full-catalog scrapers (Bilka, Netto, Føtex, Lidl)
│   └── webscrape_*.py       # Per-store tilbudsavis/katalog scrapers
├── scripts/
│   ├── verify-integrations.py
│   ├── audit-site.py        # Site health/content audit
│   ├── build-nutrition.py   # Builds data/nutrition_data.json (Rema/Salling/Open Food Facts)
│   ├── seed-d1.py           # Supabase → Cloudflare D1 + KV (cache_version, home_data_v1)
│   ├── build-pages.sh       # Edge deploy bundle
│   ├── deploy-worker.sh     # Deploy + purge Cloudflare CDN cache
│   ├── smoke-test.mjs               # Post-deploy concurrent-request smoke test (Playwright)
│   ├── playwright-uptime-check.mjs  # 5-min uptime probe (Playwright, real headless browser)
│   ├── setup-domain.sh / setup-edge-secrets.sh / setup-feedback-sheet.sh
│   ├── relay-feedback-to-sheet.py # D1 feedback → Google Sheet
│   └── supabase-*.sql       # Supabase schema/grants/swap/lockdown scripts
├── data/
│   ├── *_normal_prices.json # Cached store price data
│   ├── ai_classifier_cache.db / ai_decisions.csv # AI-classifier cache/log
│   ├── app_cache_local.json # Local fallback for app_cache
│   ├── nutrition_data.json  # Built by scripts/build-nutrition.py
│   └── rema_hashes.json     # Rema pHash cache
├── templates/           # Jinja2 HTML templates (+ macros/, partials/)
├── static/              # CSS, JS, images
├── docs/                # Dev.md (dev/staging workflow), Features.md (roadmap), prisovervaagning.md
├── .github/workflows/   # Scrapers, cache updater, edge deploy, smoke/uptime tests, feedback relay
├── requirements.txt     # CI/scraper dependencies
└── pyproject.toml       # EdgeKit / uv (Cloudflare deploy)
```

## License

Private project - all rights reserved.
