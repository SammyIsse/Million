# Prisovervågning

Status: **Bygget, afventer Resend API-nøgle (secret) før mail rent faktisk sendes.**

> **"Personlig besparelsesoversigt"** på forsiden (web `.savings-widget` + app) er **live**: kræver login, optæller (dyreste − billigste) ved prissammenligning, Top X % blandt brugere, og viser forrige måned de første 7 dage. SQL: `scripts/supabase-user-savings.sql`.

## Sådan virker det

1. I produkt-overlayet trykker en logget ind bruger "Overvåg pris" og sætter en målpris (`templates/base.html`, `.price-alert-box` / `#alert-form`).
2. `savePriceAlert()` i `static/js/script.js` kalder `create_price_alert`-RPC'en **direkte fra browseren** via `window.AuthBridge` (samme mønster som `carts` - ingen tur om `app.py`). Er brugeren ikke logget ind, åbner `AuthBridge.requireAuth()` login-modalen i stedet.
3. RPC'en (SECURITY DEFINER, `scripts/supabase-price-alerts-v2.sql`) kræver `auth.uid()` og henter email fra JWT'et - kan ikke forfalskes af klienten. Én alarm pr. bruger+produkt (nyt kald opdaterer target).
4. Hver nat, som del af `updater.py`s `run_updater()`, tjekker `check_price_alerts()` alle alarmer med `notified_at IS NULL` mod nattens friske priser (samme kilde som prishistorikken, `collect_store_prices()` - laveste pris på tværs af alle butikker).
5. Rammer prisen målprisen eller derunder, sendes en mail via **Resends HTTP API** (`_send_price_alert_email()`), og alarmen markeres `notified_at = now()` så den ikke sender igen. En ny alarm på samme vare nulstiller `notified_at` (RPC'en).

## Hvad der mangler før det virker i produktion

1. **`RESEND_API_KEY`-secret** i GitHub Actions (bruges af `cache-updater.yml` → `updater.py`). Dette er en NY nøgle til Resends HTTP API - ikke de SMTP-oplysninger der allerede er sat op i Supabase Auth til password-reset-mails (`docs/email-bekraeftelse.md`). Oprettes i Resend-dashboardet (samme verificerede domæne `madshopper.dk`) og lægges i repo → Settings → Secrets → Actions.
2. **Kør `scripts/supabase-price-alerts-v2.sql`** i Supabase SQL Editor, én gang (efter `supabase-hardening.sql` og `supabase-dev-tables.sql`).
3. Uden `RESEND_API_KEY` logger `check_price_alerts()` blot "springer over" hver nat - resten af funktionen (login-krav, RPC, dedup) virker allerede.

## Bevidste afgrænsninger (v1)

- Ingen "Mine alarmer"-liste endnu til at se/slette alarmer før de udløser - en alarm er selv-oprensende (sender højst én mail, markeres derefter udløst).
- Kun web-overlayet er koblet på. Den native app viser stadig en "under udvikling"-placeholder (`apps/mobile/src/screens/ProductDetailScreen.tsx`) - ikke rørt her.
- `current_price` gemt på alarmen er kun til visning/logning; selve udløsningen genberegner altid den reelle laveste pris i `updater.py`.
