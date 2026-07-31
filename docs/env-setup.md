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
| Supabase redirect URLs til native | Dashboard | **Sat** (`madshopper://`, `madshopper://**`, `exp://127.0.0.1:8081/--/*`, web) |
| Google iOS/Android OAuth-klienter | Google Cloud | **Sat** (`dk.madshopper.app`, package/bundle + SHA-1) |
| Apple Developer / Play Console | Store | **Mangler** (Team ID + SHA-256) |
| EAS projekt + secrets | Expo | **Sat** (projekt `madshopper` under Cartspotter-org, ID `61fb2d3e-805e-4d2f-9c78-5e9705d28fd8`; alle 5 vars sat i preview+production via dashboard, se §5a) |

---

## 1) Supabase — redirect URLs til native-appen

**Status 2026-07-26:** På plads (inkl. `exp://127.0.0.1:8081/--/*`). Trinene nedenfor er kun til reference / hvis noget senere falder ud.

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

**Status 2026-07-26:** iOS- og Android-OAuth-klienter til `dk.madshopper.app` findes allerede (package/bundle ID + SHA-1 korrekt). Web-klienten er urørt. Trinene nedenfor er kun til reference / genoprettelse.

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

### 2e. Hvor Client ID'et skal indsættes bagefter

`apps/mobile/app.config.js` læser `EXPO_PUBLIC_GOOGLE_CLIENT_ID` fra env — der
skal **ikke** redigeres i selve `app.config.js`-filen. Sæt værdien i:
1. `apps/mobile/.env` (lokal dev/Expo Go) — `EXPO_PUBLIC_GOOGLE_CLIENT_ID=<...>`.
2. EAS secret til builds — se §5a for de præcise `eas secret:create`-kommandoer
   (kør for både `preview`- og `production`-scope).

Rør **ikke** den eksisterende web-klient (`683267660851-…`) i `templates/base.html`
eller rod-`.env` — iOS/Android-klienterne er nye, separate credentials i samme
Google Cloud-projekt.

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

> Skal du **udføre** udgivelsen, så følg [`udgivelse.md`](udgivelse.md) i stedet —
> den tager trinnene i den rigtige rækkefølge og indeholder faldgruberne.
> Afsnittet her er referencen for, hvad de enkelte værdier er.

Når appen skal i TestFlight / Play:

### Apple
1. [developer.apple.com](https://developer.apple.com) → Membership (~799 kr/år).
2. Certificates, Identifiers → App ID med bundle `dk.madshopper.app`.
3. Associated Domains til Universal Links (`applinks:madshopper.dk`).
4. Notér **Team ID** (10 tegn) → sæt Worker-var `APPLE_TEAM_ID=…` og deploy, så
   `https://madshopper.dk/.well-known/apple-app-site-association` udfyldes.
5. App Store Connect → ny app → TestFlight.
6. `eas build --profile production --platform ios` + `eas submit`.

### Google Play
1. [play.google.com/console](https://play.google.com/console) (~25 USD engangs).
2. Opret app med package `dk.madshopper.app`.
3. Upload AAB fra EAS (`eas build --profile production --platform android`).
4. Kopiér **SHA-256** fra Play App Signing (eller `eas credentials`) →
   Worker-var `ANDROID_CERT_SHA256=…` (kommaseparer flere) og deploy
   `/.well-known/assetlinks.json`.

EAS (Expo) docs: https://docs.expo.dev/build/setup/  
Lokalt: `apps/mobile/eas.json` + `apps/mobile/README.md` § Store-build.

### 5b. Cloudflare Worker vars — `APPLE_TEAM_ID` / `ANDROID_CERT_SHA256`

`wrangler.toml` har begge nøgler som **udkommenterede** vars under
`[env.production.vars]` (linje ~42-43) — de er bevidst ikke aktive endnu, fordi
`app.py`'s `/.well-known/apple-app-site-association` og `/.well-known/assetlinks.json`
returnerer tomme (men gyldige) payloads uden dem, så Apple/Google ikke cacher en
forkert/tom app-tilknytning. Verificeret lokalt 2026-07-26: begge routes svarer
korrekt både med og uden vars sat (Flask test client).

Disse er **almindelige vars** (ikke secrets — værdierne er offentlige
identifikatorer, ikke hemmeligheder), så de sættes enten direkte i
`wrangler.toml` eller via `wrangler`:

```bash
# Fjern kommentar-tegnet i wrangler.toml [env.production.vars] og udfyld,
# ELLER sæt via CLI (kræver Cloudflare-login/API-token du allerede har i rod-.env):
cd /Users/kallekanin/Desktop/Million/Million-main

# Redigér wrangler.toml til fx:
#   APPLE_TEAM_ID = "AB12CD34EF"
#   ANDROID_CERT_SHA256 = "AA:BB:CC:...:ZZ"
# og deploy derefter (samme flow som .github/workflows/deploy-edge.yml):
bash scripts/build-pages.sh
npx wrangler@4.114.0 deploy --config dist/wrangler.toml --env production
```

Efter deploy: verificér med
`curl https://madshopper.dk/.well-known/apple-app-site-association` og
`curl https://madshopper.dk/.well-known/assetlinks.json` — begge skal nu
indeholde udfyldt `appID` hhv. `sha256_cert_fingerprints`.

### 5a. EAS secrets — nøjagtige kommandoer (kør efter `eas login` + `eas init`)

**Status 2026-07-27:** Alle fem sat via Expo-dashboardet (Project settings →
Environment variables) i både `preview`- og `production`-miljøet, under
Cartspotter-org-projektet `madshopper` (ID `61fb2d3e-805e-4d2f-9c78-5e9705d28fd8`).
Kommandoerne nedenfor er kun til reference / hvis en variabel senere skal
genskabes eller opdateres via CLI i stedet for dashboardet.

`apps/mobile/.env` bruges kun lokalt (Expo Go/dev client læser den direkte).
Til `eas build` skal de samme `EXPO_PUBLIC_*`-værdier findes som **EAS secrets**,
fordi CI-build-serveren ikke har din lokale `.env`. Værdierne er de samme som i
`apps/mobile/.env` (publishable/anon — aldrig service_role).

Kør fra `apps/mobile/`, én gang pr. scope (`preview` = staging-profilen i
`eas.json`, `production` = prod-profilen):

```bash
cd apps/mobile

# Preview/staging-scope (samme Supabase-projekt, kun til intern test)
eas secret:create --scope project --name EXPO_PUBLIC_SUPABASE_URL \
  --value "https://oxzxingkbsnqzpmjtktr.supabase.co" --type string --environment preview
eas secret:create --scope project --name EXPO_PUBLIC_SUPABASE_ANON_KEY \
  --value "<din sb_publishable_... nøgle fra apps/mobile/.env>" --type string --environment preview
eas secret:create --scope project --name EXPO_PUBLIC_GOOGLE_CLIENT_ID \
  --value "<Web Client ID>" --type string --environment preview
eas secret:create --scope project --name EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID \
  --value "<iOS Client ID>" --type string --environment preview
eas secret:create --scope project --name EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID \
  --value "<Android Client ID>" --type string --environment preview

# Production-scope
eas secret:create --scope project --name EXPO_PUBLIC_SUPABASE_URL \
  --value "https://oxzxingkbsnqzpmjtktr.supabase.co" --type string --environment production
eas secret:create --scope project --name EXPO_PUBLIC_SUPABASE_ANON_KEY \
  --value "<din sb_publishable_... nøgle fra apps/mobile/.env>" --type string --environment production
eas secret:create --scope project --name EXPO_PUBLIC_GOOGLE_CLIENT_ID \
  --value "<Web Client ID>" --type string --environment production
eas secret:create --scope project --name EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID \
  --value "<iOS Client ID>" --type string --environment production
eas secret:create --scope project --name EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID \
  --value "<Android Client ID>" --type string --environment production
```

Bemærk:
- Nyere `eas-cli` bruger `--environment` (preview/production/development) i
  stedet for separate profiler; kør `eas secret:list` bagefter for at
  bekræfte at alle **fem** findes i det rigtige scope. Kør `eas secret:create --help`
  hvis din installerede `eas-cli`-version bruger en anden syntaks (ældre
  versioner brugte kun `--scope project` uden `--environment`).
- `EXPO_PUBLIC_GOOGLE_CLIENT_ID` (web-client) bruges som audience til Supabase
  ID-token-verifikation. `EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID`/`_ANDROID_CLIENT_ID`
  bruges af `@react-native-google-signin/google-signin` (native Sign-In,
  tilføjet 2026-07-27) — uden dem bygger EAS appen med Google-login der fejler
  i TestFlight/Play, selv om det virker lokalt. `iosUrlScheme` i
  `app.config.js`'s config-plugin for google-signin læses fra
  `EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID` **på build-tidspunktet** — sørg for at
  secret'en er sat *før* `eas build` køres, ikke bare i runtime-env.
- Aldrig sæt `SUPABASE_KEY`/service_role eller `DEPLOY_KEY` som EAS secret —
  appen må kun bruge publishable/anon-nøglen.

---

## 6) Kør lokalt (når env er på plads)

**Terminal 1 — API/web:**
```bash
cd /Users/kallekanin/Desktop/Million/Million-main
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

## 6a) Windows — Android-emulator lokalt

Verificeret 2026-07-31. Resten af denne guide er Mac-orienteret (paths, `keytool`
til debug-keystore) — dette afsnit dækker kun det Windows-specifikke.

**Forudsætninger** (ingen af delene var installeret/sat op fra start på en frisk
Windows-maskine med kun Android Studio's SDK):

| Krav | Hvorfor | Tjek |
|---|---|---|
| JDK **17** (ikke nyere) | Gradle 8.14.3 (dette projekts version) fejler med `Unsupported class file major version 69` på JDK 25 — build-script-kompileringen (Groovy/ASM) understøtter ikke så ny bytecode, selvom Gradle-daemonen selv starter fint | `java -version` |
| `ANDROID_HOME` sat | Uden den fejler Gradle med `SDK location not found`, selvom `adb`/`emulator` findes i PATH | `echo $ANDROID_HOME` → `%LOCALAPPDATA%\Android\Sdk` |
| En AVD oprettet | `emulator -list-avds` skal vise mindst én | Android Studio → Device Manager, eller `avdmanager` |

JDK 17 kan installeres via winget hvis der ikke allerede findes en (fx en IDE's
bundlede JBR, som typisk er for ny):

```powershell
winget install --id EclipseAdoptium.Temurin.17.JDK -e
```

**Kørsel:**

```bash
# Terminal 1 — backend (samme som §6, ingen uv nødvendig hvis .venv/global python har afhængighederne)
python app.py

# Terminal 2 — emulator
emulator -avd <AVD-navn>   # fx MadShopper_Emulator

# Terminal 3 — native, første gang (bygger + installerer dev-clienten, ikke Expo Go)
cd apps/mobile
export JAVA_HOME="<path til JDK 17>"
export ANDROID_HOME="$LOCALAPPDATA/Android/Sdk"
npx expo run:android --variant debug
# Efterfølgende gange: npm start, tryk "a" — genbruger den installerede dev-client
```

**`apps/mobile/.env` — to faldgruber specifikt for lokal Android-emulator-test:**

1. `EXPO_PUBLIC_API_BASE_URL` skal være `http://10.0.2.2:5001`, **ikke**
   `http://localhost:5001` — Android-emulatorens `localhost` er emulatoren selv,
   ikke hosten. `10.0.2.2` er emulatorens faste alias for hostens loopback.
2. `@react-native-google-signin/google-signin`-config-pluginet (`app.config.js`)
   validerer `iosUrlScheme` (afledt af `EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID`) ved
   **enhver** prebuild, også en ren Android-build uden iOS involveret — er
   variablen tom, fejler build med `Missing iosUrlScheme in provided options`.
   Har du ikke den rigtige iOS-client-ID til rådighed lokalt, sæt en
   pladsholder i formatet `<tal>-<tekst>.apps.googleusercontent.com` for at
   komme forbi valideringen; ægte Google-login vil naturligvis stadig fejle på
   device (forventet, jf. §2 note).

---

## Fil-oversigt

```
.env                 ← Flask, scrapers, deploy (service_role OK her)
apps/mobile/.env     ← kun EXPO_PUBLIC_* (publishable + Google web client)
.env.example         ← skabelon uden hemmeligheder (tracked i git)
apps/mobile/.env.example
docs/env-setup.md    ← denne guide
```
