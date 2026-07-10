# Million Project - Claude Instructions

## Sprog

Svar altid på dansk.

## Adfærd

- Læs ALTID relevante filer selv før du svarer - spørg aldrig brugeren om filindhold
- Brug tools proaktivt uden at bede om lov
- Du har fuld adgang til projektmappen - antag altid at filer eksisterer og læs dem
- Når du får en opgave, start med at liste og læse relevante filer selv

## Projekt

MadShopper ([madshopper.dk](https://madshopper.dk)) - dansk pris-sammenligning for dagligvarer på tværs af 14+ butikker (Rema 1000, Bilka, Netto, Føtex, Meny, Spar, SuperBrugsen, Brugsen, Kvickly, Min Købmand, 365 Discount, Lidl, Løvbjerg, ABC Lavpris).

**To lag:**
- **Backend/scraping**: Python 3 + Flask (`app.py`, `app_support.py`), Supabase som database (`app_cache`, `produkter`, `price_history`), RapidFuzz til fuzzy-matching, Ollama (`gemma3:4b`) til lokal AI-klassifikation af produkter.
- **Produktion/edge**: Cloudflare Workers + Pages ("EdgeKit"), D1 og KV (`src/worker.py`, `wrangler.toml`). Supabase-data seedes til D1 via `scripts/seed-d1.py`; deploy via `scripts/build-pages.sh` + `scripts/deploy-worker.sh` (purger også Cloudflare CDN-cache).

**Mappestruktur:**
- `app.py` / `app_support.py` - Flask-routes, logging, cache, søgeindeks
- `updater.py` - genopbygger produkt-cache + prishistorik (køres af GitHub Actions cache-updater)
- `src/worker.py` - Cloudflare Workers entry point (produktion)
- `scraper/` - per-butik scrapers (Selenium/Requests), `ai_classifier.py`, `keywords.py`, `supabase_utils.py`
- `scripts/` - deploy (`build-pages.sh`, `deploy-worker.sh`, `setup-domain.sh`, `setup-edge-secrets.sh`), `seed-d1.py`, `audit-site.py`, `verify-integrations.py`, `relay-feedback-to-sheet.py`, samt `supabase-*.sql` (grants/price-history/swap-scripts)
- `data/` - cachede butikspriser, AI-classifier cache/log, Rema pHash-cache
- `templates/` / `static/` - Jinja2 + CSS/JS
- `docs/` - supplerende docs (fx prisovervågning)
- `wrangler.toml`, `pyproject.toml` - Cloudflare/EdgeKit-konfiguration (uv)

Fuld tech stack, butiksliste og mappetræ: `README.md` § Tech Stack / Supported Stores / Project Structure.

Prishistorik (30 dage) gemmes i Supabase-tabellen `price_history` - opdateres dagligt via `updater.py` (GitHub Actions cache-updater). Ved permission-fejl: kør `scripts/supabase-grants.sql` i Supabase SQL Editor. Ved upsert-fejl: kør også `scripts/supabase-price-history.sql`. For atomisk butiks-swap ved scrape (undgår tomt vindue hvis netværket dør midt i swap): kør `scripts/supabase-produkter-swap.sql` (samme mønster som `scripts/supabase-app-cache-swap.sql` for `app_cache`) - indtil da bruges automatisk den gamle to-kalds-metode.

## Produktmatching (`updater.py`)

Tre **stages** efter EAN-status. Kun stage 3 initierer fuzzy matching; stage 1 og 2 er passive targets.

| Stage | Betingelse | Adfærd |
|---|---|---|
| **1 - EAN-match** | Samme EAN i ≥2 butikker | Grupperes via EAN (ingen fuzzy) |
| **2 - EAN, ingen match** | EAN findes kun i én butik | Solokort; passivt fuzzy-target |
| **3 - Ingen EAN** | Intet EAN | **Eneste stage der initierer fuzzy** |

Fuzzy vurderer: **navn**, **type**, **vægt** (enhed) og **antal** (`stk`) - vægt og antal er separate attributter.

Pipeline: Rema-annotering → fase 1 (EAN-gruppering) → fase 2 (stage 3 fuzzy mod unmatched) → fase 2b (stage 3 fuzzy mod stage-1-grupper) → solokort.

Fuld dokumentation: `README.md` § Product matching.

## Regler

- Rediger kode direkte uden at spørge om lov
- Vis altid ændringer du laver
- Hvis noget er ødelagt, fix det med det samme
- Optimer kode når du ser mulighed for det
