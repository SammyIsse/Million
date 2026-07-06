# Prisovervågning (ikke aktiv endnu)

Status: **Klar til udrulning, når brugerprofiler findes** — funktionen er ikke live endnu.

## Hvad der allerede findes

- UI-knap "Overvåg pris" i produkt-overlay (`templates/base.html`)
- Alert-formular og styling (`static/css/styles.css`, `.price-alert-box`)
- API-endpoint `POST /api/create-alert` (`app.py`) — gemmer i Supabase-tabellen `price_alerts`
- "Kommer snart"-overlay vises i stedet for formularen, indtil profiler er på plads (`showPriceAlertComingSoon()`)

## Hvad der mangler før launch

1. **Brugerprofiler** — alerts skal knyttes til en bruger (login/opret konto)
2. **Notifikationer** — e-mail, push eller browser-notifikationer når prisen falder under målprisen
3. **Baggrundsjob** — daglig tjek af `price_alerts` mod aktuelle priser (fx via `updater.py` eller separat workflow)
4. **RLS/policies i Supabase** — sikre at brugere kun ser og sletter egne alerts

## Sådan aktiveres funktionen

1. Implementér profil/login (Supabase Auth eller tilsvarende)
2. Udvid `price_alerts` med `user_id` og opdater `/api/create-alert` til at kræve auth
3. Genaktiver alert-formularen i `templates/base.html` og gendan `savePriceAlert()`-logikken i `static/js/script.js`

## Noter

- Uden profil kan systemet ikke sende notifikationer til den rigtige bruger — derfor vises placeholder-overlayet.
- API-logikken i `/api/create-alert` kan genbruges næsten uændret, når auth er på plads.
