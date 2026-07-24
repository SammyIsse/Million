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
- **Backend/scraping**: Python 3 + Flask (`app.py`, `app_support.py`), Supabase som database, RapidFuzz til fuzzy-matching, Ollama (`gemma3:4b`) til lokal AI-klassifikation af produkter.
- **Produktion/edge**: Cloudflare Workers + Pages ("EdgeKit"/Pyodide), D1 og KV (`src/worker.py`, `wrangler.toml`). Supabase-data seedes til D1 via `scripts/seed-d1.py`; deploy via `scripts/build-pages.sh` + `scripts/deploy-worker.sh` (purger også Cloudflare CDN-cache). Samme `app.py` kører både lokalt (Flask) og på edge.

**Mappestruktur:**
- `app.py` / `app_support.py` - Flask-routes, API, sikkerhedsheaders/CSP, logging, cache, søgeindeks
- `updater.py` - genopbygger produkt-cache + prishistorik (køres af GitHub Actions cache-updater)
- `src/worker.py` - Cloudflare Workers entry point: edge-cache (Cache API), rate limiting, sikkerhedslogning, staging-adgangsspærring
- `scraper/` - per-butik scrapers (Selenium/Requests), `dagrofa_scraper.py` (Meny/Spar/Min Købmand), `tjek_tilbud_scraper.py`, `*_katalog.py` (Bilka/Netto/Føtex/Lidl), `ai_classifier.py`, `keywords.py`, `supabase_utils.py`
- `scripts/` - deploy (`build-pages.sh`, `deploy-worker.sh`, `setup-domain.sh`, `setup-edge-secrets.sh`, `setup-feedback-sheet.sh`), `seed-d1.py`, `build-nutrition.py`, `audit-site.py`, `verify-integrations.py`, `relay-feedback-to-sheet.py`, `smoke-test.mjs` + `playwright-uptime-check.mjs` (Playwright), samt `supabase-*.sql`
- `data/` - cachede butikspriser, AI-classifier cache/log, `nutrition_data.json`, Rema pHash-cache
- `templates/` (+ `macros/`, `partials/`) / `static/` - Jinja2 + CSS/JS (`script.js`, `auth.js`, `supabase.min.js`)
- `docs/` - `Dev.md` (dev/staging-workflow), `Features.md` (roadmap), `prisovervaagning.md`, `email-bekraeftelse.md`, `Github_fifs.md`
- `.github/workflows/` - per-butik-scrapers, cache-updater, nutrition-build, edge-deploy (prod+dev), smoke/uptime-test, feedback-relay, dependency-audit
- `wrangler.toml`, `pyproject.toml` - Cloudflare/EdgeKit-konfiguration (uv)

Fuld tech stack, butiksliste og mappetræ: `README.md` § Tech Stack / Supported Stores / Project Structure.

## Data & tabeller

**Supabase:** `app_cache` (produkt-cache i chunks), `produkter` (rå butiksdata), `price_history` (30 dage), `nutrition_data`, `cart_popularity` + `cart_events` (anonym kurv-aktivitet), `price_alerts`, `carts` (gemt kurv pr. bruger, RLS-låst).
**Cloudflare D1:** read-only mirror af produkt-cachen (seedet nightly), `pending_feedback`, `security_events`.
**Cloudflare KV:** `cache_version` (bumpes ved hvert seed → invaliderer al edge-cache), `home_data_v1` (forudberegnede forsidepuljer, sparer ~4 D1/Supabase-kald pr. render).

Skrive-tabellerne (`cart_popularity`, `cart_events`, `price_alerts`, `carts`) vælges via `TABLE_SUFFIX`: tom i produktion, `_dev` lokalt og på staging - kør `scripts/supabase-dev-tables.sql` én gang.

**SQL-scripts (køres manuelt i Supabase SQL Editor):**
- `supabase-grants.sql` - service_role-rettigheder til prishistorik (ved permission-fejl)
- `supabase-price-history.sql` - unikke indeks/upsert (ved upsert-fejl)
- `supabase-lowest-price.sql` - view til "30 dages laveste"-badget
- `supabase-app-cache-swap.sql` / `supabase-produkter-swap.sql` - atomisk swap, så en samtidig læser aldrig ser en halv/tom cache. Uden dem bruges automatisk den gamle to-kalds-metode
- `supabase-cart-increment.sql` - `record_cart_activity`-RPC (SECURITY DEFINER, eneste skrivevej til `cart_events`)
- `supabase-nutrition.sql`, `supabase-carts.sql`, `supabase-dev-tables.sql`
- `supabase-rls-audit.sql` (ren læsning), `supabase-lockdown.sql`, `supabase-hardening.sql` - sikkerhed/RLS

## Miljøer & deploy

| Miljø | Branch | URL | Data |
|---|---|---|---|
| Produktion | `main` | madshopper.dk | prod-tabeller, egen KV + D1 |
| Staging | `dev` | madshopper-dev.kasp478g.workers.dev | læser prod-data, skriver til `*_dev`, egen KV + D1 |
| Lokal | - | localhost:5001 (`python app.py`) | læser prod-data, skriver til `*_dev` |

Push til `dev` → `deploy-edge-dev.yml`; merge `dev` → `main` → `deploy-edge.yml`. Begge kører Playwright-røgtest bagefter. Fuld workflow: `docs/Dev.md`.

Produktion er ramt af et reelt nedbrud 2026-07-19 (1101/1102 CPU-fejl ved samtidige cold renders efter nightly reseed). Derfor: Workers-observability er **permanent slået fra** i `wrangler.toml` (dens introspektion var selve årsagen), edge-cachen er versioneret via `cache_version`, og sikkerhedslogningen i `src/worker.py` aggregeres i hukommelsen og skylles højst 1×/minut pr. isolate. Lav aldrig noget der logger pr. request.

## Brugerkonti

Client-side via `supabase-js` (`static/js/auth.js`) - browseren bruger kun den offentlige publishable-nøgle. Google-login via Identity Services (ID-token-flow, så samtykkeskærmen viser madshopper.dk) + email/adgangskode med "glemt adgangskode". Kurven gemmes komprimeret i `carts` (RLS: `auth.uid() = user_id`); sammenligningspriser genhentes live fra `/api/products`, så der aldrig gemmes forældede priser. Opsætning der kræver manuelle trin (SMTP/branded mails): `docs/email-bekraeftelse.md`.

## Produktmatching (`updater.py`)

Tre **stages** efter EAN-status. Kun stage 3 initierer fuzzy matching; stage 1 og 2 er passive targets.

| Stage | Betingelse | Adfærd |
|---|---|---|
| **1 - EAN-match** | Samme EAN i ≥2 butikker | Grupperes via EAN (ingen fuzzy) |
| **2 - EAN, ingen match** | EAN findes kun i én butik | Solokort; passivt fuzzy-target |
| **3 - Ingen EAN** | Intet EAN | **Eneste stage der initierer fuzzy** |

Fuzzy vurderer: **navn**, **type**, **vægt** (enhed), **antal** (`stk`), **procenter** (fedt/alkohol/kakao), **kødtype**, **smag/form/variant**, **pris-sanity** og **billede (pHash)** - vægt og antal er separate attributter.

Pipeline: Rema-annotering (inkl. EAN-retro-validering + cross-member-validering) → fase 1 (EAN-gruppering) → fase 2 (stage 3 fuzzy mod unmatched) → fase 2b (stage 3 fuzzy mod stage-1-grupper) → solokort → billed-dedup.

Fuld dokumentation med alle gates og tolerancer: `README.md` § Product matching.

## Sikkerhed

Den offentlige Supabase-nøgle ligger i `wrangler.toml`, i git og i hver sides HTML - den
er offentlig med vilje. Alt hviler derfor på, hvad den nøgle **må**: efter
`scripts/supabase-hardening.sql` har den ingen INSERT/UPDATE/DELETE på nogen tabel, og
al skrivning går gennem `SECURITY DEFINER`-RPC'er, der gentager appens validering i SQL.

Regler når du rører de her ting:
- Tilføj **aldrig** direkte tabelskrivning fra browseren eller fra `app.py` med anon-nøglen. Ny skrivning = ny RPC med validering i SQL.
- Ny butik med ny billed-CDN? Tilføj hosten i `_IMG_HOSTS` i `app.py`, ellers blokerer CSP'en billederne.
- Sikkerhedslogningen i `src/worker.py` skal blive ved med at være **aggregeret**. Logning der skalerer med trafikken var årsagen til nedbruddet 19-07-2026. `scripts/test-security-logging.py` håndhæver det og kører ved hvert produktions-deploy.
- Workers-observability skal blive slået fra. Angrebs-synligheden kommer fra D1 + `security-monitor.yml`, ikke fra platformens logs.

Verifikation: `scripts/supabase-rls-audit.sql` (ren læsning) viser grants, RLS-status og policies.

## Regler

- Rediger kode direkte uden at spørge om lov
- Vis altid ændringer du laver
- Hvis noget er ødelagt, fix det med det samme
- Optimer kode når du ser mulighed for det
