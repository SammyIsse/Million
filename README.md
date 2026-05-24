# Million — Danish Grocery Price Comparison

A web application that aggregates and compares grocery prices across major Danish supermarkets in real time, helping users find the cheapest options and plan their shopping.

## Features

- **Price comparison** across 9 stores: Rema 1000, Bilka, Meny, Spar, SuperBrugsen, Brugsen, Kvickly, Min Købmand, and 365 Discount
- **Shopping cart** with cheapest-store routing — find the optimal store combination for your basket
- **Price history** tracking via SQLite
- **Product search** with fuzzy matching and abbreviation normalization
- **AI-assisted product classification** using a local Ollama model (Gemma 3)
- **Favorites** and user feedback

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask |
| Scrapers | Selenium, Requests, BeautifulSoup |
| Database | SQLite (price history), Supabase (cloud sync) |
| Fuzzy search | RapidFuzz |
| Frontend | Jinja2 templates, vanilla JS |
| AI classifier | Ollama (`gemma3:4b`) — local, no API key needed |

## Supported Stores

| Store | Scraper |
|---|---|
| Rema 1000 | API / XML feed |
| Bilka | `scraper/Webscrape_Bilka.py` |
| Meny | `scraper/webscrape_Meny.py` |
| Spar | `scraper/webscrape_spar.py` |
| SuperBrugsen | `scraper/webscrape_superbrugsen.py` |
| Brugsen | `scraper/webscrape_brugsen.py` |
| Kvickly | `scraper/webscrape_kvickly.py` |
| Min Købmand | `scraper/webscape_minkøbmand.py` |
| 365 Discount | `scraper/webscrape_365discount.py` |

## Getting Started

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) (optional, for AI product classification)

### Installation

```bash
git clone https://github.com/your-username/million.git
cd million

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

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
| `ENABLE_PRICE_DB` | `1` to force-enable price history, `0` to disable |
| `NEXT_PUBLIC_SUPABASE_URL` | Your Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Your Supabase publishable key |

### Run

```bash
python app.py
```

The app will be available at `http://localhost:5001`.

### Run scrapers

```bash
python updater.py
```

This triggers all store scrapers and refreshes the price data.

## Project Structure

```
Million-main/
├── app.py               # Flask application and API routes
├── app_support.py       # Logging, caching, search index helpers
├── updater.py           # Runs all scrapers to refresh data
├── scraper/
│   ├── ai_classifier.py     # Ollama-based food/non-food classifier
│   ├── keywords.py          # Keyword lists for classification
│   ├── scraper_utils.py     # Shared scraper utilities
│   ├── supabase_utils.py    # Supabase sync helpers
│   └── webscrape_*.py       # Per-store scrapers
├── data/
│   ├── *_normal_prices.json # Cached store price data
│   └── ai_classifier_cache.db
├── templates/           # Jinja2 HTML templates
├── price_history.db     # SQLite price history
└── requirements.txt
```

## License

Private project — all rights reserved.
