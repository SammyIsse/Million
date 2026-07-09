# MadShopper - Danish Grocery Price Comparison

A web application that aggregates and compares grocery prices across major Danish supermarkets, helping users find the cheapest options and plan their shopping.

Live site: [madshopper.dk](https://madshopper.dk)

## Features

- **Price comparison** across 14+ stores: Rema 1000, Bilka, Netto, Føtex, Meny, Spar, SuperBrugsen, Brugsen, Kvickly, Min Købmand, 365 Discount, Lidl, Løvbjerg, ABC Lavpris
- **Shopping cart** with cheapest-store routing - find the optimal store combination for your basket
- **Price history** (30 days) stored in Supabase, updated daily via `updater.py`
- **Product search** with fuzzy matching and abbreviation normalization
- **AI-assisted product classification** using a local Ollama model (Gemma 3)
- **Favorites** and user feedback

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask |
| Production | Cloudflare Workers (EdgeKit), D1, KV |
| Scrapers | Selenium, Requests |
| Database | Supabase (`app_cache`, `produkter`, `price_history`) |
| Fuzzy search | RapidFuzz |
| Frontend | Jinja2 templates, vanilla JS |
| AI classifier | Ollama (`gemma3:4b`) - local, no API key needed |

## Supported Stores

| Store | Scraper |
|---|---|
| Rema 1000 | Rema XML feed (`updater.py`) |
| Bilka | `scraper/bilka_katalog.py` (Algolia-katalog, komplet med pris) |
| Netto | `scraper/netto_katalog.py` (Algolia-katalog, primær pris) + `scraper/webscrape_netto.py` (Tjek tilbudsavis) |
| Netto+ +Priser | `scraper/netto_plus_priser.py` (p-club, personligt token) |
| Føtex | `scraper/foetex_katalog.py` (Algolia-katalog, primær pris) + `scraper/webscrape_foetex.py` (Tjek tilbudsavis) |
| Føtex+ +Priser | `scraper/foetex_plus_priser.py` (p-club, personligt token) |
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
| `DEPLOY_KEY` | Supabase service key (scrapers/updater only - not needed for local app) |
| `NETTO_ID_TOKEN` | Personal p-club token for Netto+ scraper (optional) |
| `FOETEX_ID_TOKEN` | Personal p-club token for Føtex+ scraper (optional) |

### Run

```bash
python app.py
```

The app will be available at `http://localhost:5001`.

### Run cache updater

```bash
python updater.py
```

Rebuilds the product cache from Rema XML + Supabase store data and records daily price history.

### Product matching (`updater.py`)

Products are classified into three **stages** by EAN status. Only stage 3 initiates fuzzy matching; stages 1 and 2 are passive targets.

| Stage | Condition | Behaviour |
|---|---|---|
| **1 - EAN match** | Same EAN in ≥2 stores | Grouped by EAN (exact match, no fuzzy) |
| **2 - EAN, no match** | EAN present but only in one store | Standalone card; passive fuzzy target |
| **3 - No EAN** | No EAN on product | **Only stage that initiates fuzzy matching** |

**Fuzzy matching attributes** (stage 3 initiator; evaluated as hard gates + name score):

- **Name** - product name similarity (primary score)
- **Type** - food category (`unify_category`); must match when both sides are known
- **Weight** - unit weight/volume of a single item (`_weight_g`)
- **Quantity** - number of units in the package (`_stk_count`); separate from weight (e.g. 1×2 L vs 6×0.33 L)

**Updater pipeline** (in `fetch_and_parse_xml`):

1. **Rema annotation** - each Rema product (no EAN) is matched to comparison stores via `_find_generic_match` (acts as stage-3 initiator).
2. **Phase 1** - stage-1 EAN grouping across unmatched comparison-store products.
3. **Phase 2** - stage 3 initiates fuzzy vs remaining unmatched products (including stage-2 passive targets).
4. **Phase 2b** - stage 3 initiates fuzzy vs existing stage-1 EAN groups (passive targets).
5. **Solokort** - remaining stage-2 and unmatched stage-3 products become standalone cards.

**Key rule:** Stages 1 and 2 never initiate fuzzy matching. They can only be matched *against* by a stage-3 product.

### Verify integrations

```bash
python scripts/verify-integrations.py
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
│   ├── *_plus_priser.py     # Netto+/Føtex+ p-club price scrapers
│   └── webscrape_*.py       # Per-store tilbudsavis/katalog scrapers
├── scripts/
│   ├── verify-integrations.py
│   ├── audit-site.py        # Site health/content audit
│   ├── seed-d1.py           # Supabase → Cloudflare D1
│   ├── build-pages.sh       # Edge deploy bundle
│   ├── deploy-worker.sh     # Deploy + purge Cloudflare CDN cache
│   ├── setup-domain.sh / setup-edge-secrets.sh / setup-feedback-sheet.sh
│   ├── relay-feedback-to-sheet.py # D1 feedback → Google Sheet
│   └── supabase-*.sql       # Supabase schema/grants/swap scripts
├── data/
│   ├── *_normal_prices.json # Cached store price data
│   ├── ai_classifier_cache.db / ai_decisions.csv # AI-classifier cache/log
│   ├── app_cache_local.json # Local fallback for app_cache
│   └── rema_hashes.json     # Rema pHash cache
├── templates/           # Jinja2 HTML templates (+ macros/, partials/)
├── static/              # CSS, JS, images
├── docs/                # Supplementary docs (fx prisovervågning)
├── requirements.txt     # CI/scraper dependencies
└── pyproject.toml       # EdgeKit / uv (Cloudflare deploy)
```

## License

Private project - all rights reserved.
