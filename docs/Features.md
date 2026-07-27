cd /Users/kallekanin/Desktop/Million/Million-main/apps/mobile
npx expo start --dev-client


## Publish-status (2026-07-26)

### Klar (kode/opsætning)
- Hjemmeside: live og sund (smoke/uptime/API OK; `verify-integrations` 35/35).
- Native app fase 0–8: færdig i `apps/mobile`.
- Fase 9 forberedt i repo: `eas.json`, npm `eas:*`-scripts, AASA/assetlinks-routes, `apps/mobile/store/`, CI `mobile-tests.yml`.
- Supabase Auth redirects: `madshopper://`, `madshopper://**`, `exp://127.0.0.1:8081/--/*` (+ web) — på plads.
- Google Cloud: iOS- + Android-OAuth-klienter til `dk.madshopper.app` findes (web-klient urørt).
- Native Google Sign-In (`@react-native-google-signin/google-signin`) + Sign in with Apple (`expo-apple-authentication`) implementeret 2026-07-27 — se `docs/native-app.md` §14 Fase 6.
- Ingen `service_role`/`DEPLOY_KEY` i mobile-config.

### Mangler fra dig (menneske-only)
1. Apple Developer Program (~99 USD/år) → **Team ID** (til iOS OAuth + `APPLE_TEAM_ID` i wrangler).
2. Google Play Console (~25 USD engangs) → app-signing **SHA-256** (`ANDROID_CERT_SHA256`).
3. Terminal: `npm i -g eas-cli` → `cd apps/mobile && eas login` → `eas init`.
4. `eas secret:create` for de 5 `EXPO_PUBLIC_*` (preview + production; inkl. Google iOS/Android Client ID til native Sign-In) — se `docs/env-setup.md` §5a.
5. Giv Team ID + SHA-256 → aktivér wrangler-vars + edge-deploy (kan gøres af agent).
6. Screenshots iht. `apps/mobile/store/metadata.md`.
7. App Store Connect / Play Console: metadata + **Submit for Review** (dig).

Se også: `docs/native-app.md` §Fase 9, `docs/env-setup.md` §5, `apps/mobile/README.md`.

---

Prisovervågning – Klar til udrulning når notifikationer findes (UI + API + auth findes). Se docs/prisovervaagning.md

Personlig besparelse – Live på web + app (login, månedlig total, Top X %). SQL: `scripts/supabase-user-savings.sql` (kørt i Supabase).

Føtex komplet produktkatalog (Algolia prod_FOETEX_PRODUCTS + Salling API priser) – 14.459 produkter med EAN (priser mangler FOETEX_SALLING_STORE i secrets)

Mit køleskab side - ud fra hvad man har i køleskabet, kom med opskrifter

Fra tilbud - kom mig forslag til aftensmad ud fra tilbudsvarerne.

Man skal kunne gemme en opskrift, så den dukker op under "Mine opskrifter" under "Favorit opskrifter"

Butikker opdateringer
- Lidl har flere varer, deres app er bare nede lige nu, så kan ikke tjekke det (5/7-26)
    Det er ulovlig at tage flere varer, uden aftale med dem...

    Vil gerne køre en sikkerheddtest og sørge for at den er helt sikker

    Vil også gerne bare have at alting fungere og se om der er noget der kunne være bedre.
