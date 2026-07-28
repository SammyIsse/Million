 https://dev.madshopper.dk/staging-login 

 cd /Users/kallekanin/Desktop/Million/Million-main/apps/mobile
npx expo start --dev-client
 
## Publish-status (2026-07-28)

### Klar (kode/opsætning)
- Hjemmeside: live og sund (smoke/uptime/API OK; `verify-integrations` 35/35).
- Native app fase 0–8: færdig i `apps/mobile`.
- Fase 9 forberedt i repo: `eas.json`, npm `eas:*`-scripts, AASA/assetlinks-routes, `apps/mobile/store/`, CI `mobile-tests.yml`.
- Supabase Auth redirects: `madshopper://`, `madshopper://**`, `exp://127.0.0.1:8081/--/*` (+ web) — på plads.
- Google Cloud: iOS- + Android-OAuth-klienter til `dk.madshopper.app` findes (web-klient urørt).
- Native Google Sign-In (`@react-native-google-signin/google-signin`) + Sign in with Apple (`expo-apple-authentication`) implementeret 2026-07-27 — se `docs/native-app.md` §14 Fase 6.
- Ingen `service_role`/`DEPLOY_KEY` i mobile-config.
- EAS-projekt oprettet (`madshopper` under Cartspotter-org, ID `61fb2d3e-805e-4d2f-9c78-5e9705d28fd8`), koblet i `apps/mobile/app.config.js`. Alle 5 `EXPO_PUBLIC_*`-secrets sat i preview+production via Expo-dashboardet — se `docs/env-setup.md` §5a.
- Review-blockers ryddet 2026-07-28: in-app "Slet konto" (Apple 5.1.1(v)) i `SettingsScreen`, `supportsTablet: false` (så iPad ikke bliver en review-flade uden screenshots), placeholder-switches ("Push"/"Nyhedsbrev", 2.1) fjernet, `version` → `1.0.0`, keywords trimmet til 98 tegn uden butiksvaremærker (5.2).
- iPhone-screenshots (1290x2796, 5 stk.) ligger i `apps/mobile/store/screenshots/iphone-6.7/`.

### Mangler fra dig (menneske-only)
1. Apple Developer Program (~99 USD/år) → **Team ID** (til iOS OAuth + `APPLE_TEAM_ID` i wrangler).
2. Google Play Console (~25 USD engangs) → app-signing **SHA-256** (`ANDROID_CERT_SHA256`).
3. Giv Team ID + SHA-256 → aktivér wrangler-vars + edge-deploy (kan gøres af agent).
4. **Første rigtige EAS-build + test på enhed.** Google/Apple Sign-In er aldrig kørt på en binary — kun i simulator uden native-modulerne. Forvent 1–2 native fejl her (særligt `useFrameworks: 'static'` + Google-podsene).
5. Android-screenshots + Play feature graphic (1024x500) — kræver emulator/enhed.
6. `eas.json` → `submit.production.ios.ascAppId` udfyldes, når appen findes i App Store Connect.
7. App Store Connect / Play Console: metadata, App Privacy/Data Safety, aldersvurdering + **Submit for Review** (dig).

**Trin-for-trin når du går i gang: [`docs/udgivelse.md`](udgivelse.md)** — rækkefølge,
kommandoer, faldgruber og hvor hvert konsol-felt hentes fra.

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

Your app forces HTTPS. Your login system passwords are stored hash, not plain text. You have bot protection on your signups and public forms. Your login sessions actually expire, so a stolen token doesn't just work forever. You have CSRF protection. Your password reset links expire and only work once. Your app's database key is a limited one, not the master key. Your logs don't secretly contain passwords, tokens, or card numbers. You have billing alerts on so you're notified when an attack comes. And you have automated backups