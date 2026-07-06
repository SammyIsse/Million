# Million Project - Claude Instructions

## Sprog

Svar altid på dansk.

## Adfærd

- Læs ALTID relevante filer selv før du svarer — spørg aldrig brugeren om filindhold
- Brug tools proaktivt uden at bede om lov
- Du har fuld adgang til projektmappen — antag altid at filer eksisterer og læs dem
- Når du får en opgave, start med at liste og læse relevante filer selv

## Projekt

Dette er et Python-projekt med web scrapers, en Flask/web app og Supabase som database.

Mappen indeholder: app.py, updater.py, scraper/, data/, templates/, static/

Prishistorik (30 dage) gemmes i Supabase-tabellen `price_history` — opdateres dagligt via `updater.py` (GitHub Actions cache-updater). Ved permission-fejl: kør `scripts/supabase-grants.sql` i Supabase SQL Editor. Ved upsert-fejl: kør også `scripts/supabase-price-history.sql`.

## Regler

- Rediger kode direkte uden at spørge om lov
- Vis altid ændringer du laver
- Hvis noget er ødelagt, fix det med det samme
- Optimer kode når du ser mulighed for det
