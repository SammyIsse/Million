# MadShopper — Danish Grocery Price Comparison

A web application that aggregates and compares grocery prices across major Danish supermarkets, helping users find the cheapest options and plan their shopping.

Live site: [madshopper.dk](https://madshopper.dk)

## Features

- **Price comparison** across 14+ stores: Rema 1000, Bilka, Netto, Føtex, Meny, Spar, SuperBrugsen, Brugsen, Kvickly, Min Købmand, 365 Discount, Lidl, Løvbjerg, ABC Lavpris
- **Shopping cart** with cheapest-store routing — find the optimal store combination for your basket
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
| AI classifier | Ollama (`gemma3:4b`) — local, no API key needed |

## Supported Stores

| Store | Scraper |
|---|---|
| Rema 1000 | Rema XML feed (`updater.py`) |
| Bilka | `scraper/bilka_katalog.py` |
| Netto | `scraper/webscrape_netto.py` |
| Netto+ +Priser | `scraper/netto_plus_priser.py` (p-club, personligt token) |
| Føtex | `scraper/webscrape_foetex.py` |
| Føtex+ +Priser | `scraper/foetex_plus_priser.py` (p-club, personligt token) |
| Meny | `scraper/webscrape_Meny.py` |
| Spar | `scraper/webscrape_spar.py` |
| SuperBrugsen | `scraper/webscrape_superbrugsen.py` |
| Brugsen | `scraper/webscrape_brugsen.py` |
| Kvickly | `scraper/webscrape_kvickly.py` |
| Min Købmand | `scraper/webscape_minkøbmand.py` |
| 365 Discount | `scraper/webscrape_365discount.py` |
| Lidl | `scraper/webscrape_lidl.py` |
| Løvbjerg | `scraper/webscrape_lovbjerg.py` |
| ABC Lavpris | `scraper/webscrape_abc_lavpris.py` |

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
| `DEPLOY_KEY` | Supabase service key (scrapers/updater only — not needed for local app) |
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
│   ├── scraper_utils.py     # Shared scraper utilities
│   ├── supabase_utils.py    # Supabase sync helpers
│   └── webscrape_*.py       # Per-store scrapers
├── scripts/
│   ├── verify-integrations.py
│   ├── seed-d1.py           # Supabase → Cloudflare D1
│   └── build-pages.sh       # Edge deploy bundle
├── data/
│   └── *_normal_prices.json # Cached store price data
├── templates/           # Jinja2 HTML templates
├── requirements.txt     # CI/scraper dependencies
└── pyproject.toml       # EdgeKit / uv (Cloudflare deploy)
```

## License

Private project — all rights reserved.
