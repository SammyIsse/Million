# Env-opsætning — MadShopper (web + native)

Rod-`.env` og `apps/mobile/.env` er allerede udfyldt med de nøgler du har.
Nedenfor: **kun det der typisk stadig mangler**, med trin til at finde dem.

Begge `.env`-filer er gitignored. Commit dem aldrig.

---

## Hurtig status

| Nøgle | Hvor | Status hos dig |
|---|---|---|
| Supabase URL + publishable | rod + mobile | Sat |
| `DEPLOY_KEY` (service_role) | kun rod-`.env` | Sat |
| Cloudflare account/zone/token | kun rod | Sat |
| Salling API | kun rod | Sat |
| Google Sheet webhook | kun rod | Sat |
| Google Client ID (web) | rod + mobile | Sat (fra web GIS) |
| Supabase redirect URLs til `madshopper://` | Dashboard | **Tjek / tilføj** |
| Google iOS/Android OAuth-klienter | Google Cloud | **Til native Google-login** |
| Apple Developer / Play Console | Store | Kun Fase 9 |

---

## 1) Supabase — redirect URLs til native-appen

Uden dette fejler Google-OAuth / password-reset i Expo.

1. Åbn [Supabase Dashboard](https://supabase.com/dashboard) → projekt **oxzxingkbsnqzpmjtktr**.
2. Gå til **Authentication → URL Configuration**.
3. Under **Redirect URLs** tilføj (hvis de mangler):
   - `madshopper://**`
   - `madshopper://`
   - `exp://127.0.0.1:8081/--/*` (Expo Go lokalt — port kan variere)
   - `http://localhost:5001/**` (web — bør allerede være der)
   - `https://madshopper.dk/**` (prod web)
4. **Site URL** kan forblive `https://madshopper.dk`.
5. Gem.

Docs: https://supabase.com/docs/guides/auth/redirect-urls

---

## 2) Google Cloud — native OAuth-klienter (til Google-login i appen)

Du har allerede **Web client ID**  
(`…apps.googleusercontent.com` — den der står i `templates/base.html` / `.env`).

Til rigtig iPhone/Android skal Google have app-specifikke klienter:

### 2a. Åbn projektet
1. Gå til [Google Cloud Console](https://console.cloud.google.com/).
2. Vælg det projekt der allerede bruges til MadShopper-login
   (samme som web Client ID `683267660851-…`).
3. **APIs & Services → Credentials**.

### 2b. iOS OAuth-klient
1. **Create credentials → OAuth client ID → iOS**.
2. **Bundle ID:** `dk.madshopper.app` (matcher `apps/mobile/app.config.js`).
3. Opret → kopiér **Client ID**.
4. Gem den et sikkert sted (kan senere bruges med `@react-native-google-signin/google-signin`).

### 2c. Android OAuth-klient
1. **Create credentials → OAuth client ID → Android**.
2. **Package name:** `dk.madshopper.app`.
3. **SHA-1:** fra din debug-keystore:
   ```bash
   keytool -list -v -alias androiddebugkey \
     -keystore ~/.android/debug.keystore -storepass android -keypass android
   ```
   Kopiér SHA-1-linjen ind i Google Cloud-formularen.
4. Opret → gem Client ID.

### 2d. Supabase Google provider
1. Supabase → **Authentication → Providers → Google**.
2. Sørg for at **Client ID** + **Client Secret** er sat til **Web**-klienten
   (den Supabase bruger til token-udveksling).
3. Aktiver Google hvis den er slået fra.

Docs:
- https://supabase.com/docs/guides/auth/social-login/auth-google
- https://docs.expo.dev/guides/google-authentication/

**Note:** Indtil iOS/Android-klienter er på plads, virker email/adgangskode i appen
stadig. Google-knappen kan fejle på device indtil trin 2 er færdig — det er forventeligt.

---

## 3) Tjek at publishable ≠ service_role

I [Supabase → Project Settings → API Keys](https://supabase.com/dashboard/project/oxzxingkbsnqzpmjtktr/settings/api-keys):

| Type | Må bruges hvor |
|---|---|
| **Publishable / anon** (`sb_publishable_…`) | Web HTML, `apps/mobile/.env` |
| **service_role** / secret (JWT med `"role":"service_role"`) | Kun `DEPLOY_KEY` i rod-`.env` + GitHub Secrets |

Hvis du ved et uheld har puttet service_role i mobil-`.env`: fjern den med det samme
og roter nøglen i Supabase.

---

## 4) Valgfrit — `CACHE_REFRESH_SECRET`

Bruges kun hvis du kalder `/api/refresh-cache` manuelt/edge.

1. Generér en tilfældig streng, fx:
   ```bash
   openssl rand -hex 32
   ```
2. Sæt `CACHE_REFRESH_SECRET=…` i rod-`.env`.
3. Samme værdi i Cloudflare Worker secrets / wrangler hvis I bruger endpointet i prod.

Kan springes over til daglig lokal udvikling.

---

## 5) Fase 9 — store-konti (ikke env-nøgler, men “sidste opsætning”)

Når appen skal i TestFlight / Play:

### Apple
1. [developer.apple.com](https://developer.apple.com) → Membership (~799 kr/år).
2. Certificates, Identifiers → App ID med bundle `dk.madshopper.app`.
3. Associated Domains til Universal Links (`applinks:madshopper.dk`).
4. App Store Connect → ny app → TestFlight.

### Google Play
1. [play.google.com/console](https://play.google.com/console) (~25 USD engangs).
2. Opret app med package `dk.madshopper.app`.
3. Upload AAB fra EAS Build / `eas build`.

EAS (Expo) docs: https://docs.expo.dev/build/setup/

---

## 6) Kør lokalt (når env er på plads)

**Terminal 1 — API/web:**
```bash
cd /Users/kallekanin/Desktop/Million/Million-main_app
uv run python app.py
# → http://localhost:5001
```

**Terminal 2 — native:**
```bash
cd apps/mobile
npm start
# scan QR med Expo Go, eller i / a for simulator
```

Mobilen på fysisk device kan ikke nå `localhost` — brug da din Macs LAN-IP, fx:
`EXPO_PUBLIC_API_BASE_URL=http://192.168.x.x:5001`
(samme Wi-Fi; tillad port 5001 i firewall).

---

## Fil-oversigt

```
.env                 ← Flask, scrapers, deploy (service_role OK her)
apps/mobile/.env     ← kun EXPO_PUBLIC_* (publishable + Google web client)
.env.example         ← skabelon uden hemmeligheder (tracked i git)
apps/mobile/.env.example
docs/env-setup.md    ← denne guide
```
