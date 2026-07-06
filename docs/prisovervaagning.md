# Prisovervågning (ikke aktiv endnu)

Status: **Klar til udrulning, når brugerprofiler findes** — funktionen er ikke live endnu.

## Hvad der allerede findes

- UI-knap "Overvåg pris" i produkt-overlay (`templates/base.html`)
- Alert-formular og styling (`static/css/styles.css`, `.price-alert-box`)
- Frontend-logik til at oprette alarm (`savePriceAlert()` i `static/js/script.js`)
- API-endpoint `POST /api/create-alert` (`app.py`) — gemmer i Supabase-tabellen `price_alerts`
- "Kommer snart"-overlay vises i stedet for formularen, indtil profiler er på plads

## Hvad der mangler før launch

1. **Brugerprofiler** — alerts skal knyttes til en bruger (login/opret konto)
2. **Notifikationer** — e-mail, push eller browser-notifikationer når prisen falder under målprisen
3. **Baggrundsjob** — daglig tjek af `price_alerts` mod aktuelle priser (fx via `updater.py` eller separat workflow)
4. **RLS/policies i Supabase** — sikre at brugere kun ser og sletter egne alerts

## Sådan aktiveres funktionen

1. Implementér profil/login (Supabase Auth eller tilsvarende)
2. Udvid `price_alerts` med `user_id` og opdater `/api/create-alert` til at kræve auth
3. Skift knappen tilbage fra `showPriceAlertComingSoon()` til `toggleAlertForm()` i `templates/base.html`
4. Fjern eller skjul "kommer snart"-overlayet
5. Tilføj notifikations- og pris-tjek-logik

## Noter

- Uden profil kan systemet ikke sende notifikationer til den rigtige bruger — derfor vises placeholder-overlayet.
- Eksisterende kode i `savePriceAlert()` og `/api/create-alert` kan genbruges næsten uændret, når auth er på plads.
