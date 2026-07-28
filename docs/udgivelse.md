# Udgivelsesguide — MadShopper i App Store og Google Play

Følg trinnene i rækkefølge. Alt hvad der kunne gøres på forhånd, **er gjort** —
det her er kun de skridt der kræver dine konti, dine penge eller en Windows-maskine.

Detaljer om nøgler og Cloudflare-vars: `docs/env-setup.md` §5.
Status og hvad der mangler i det store billede: `docs/Features.md` § Publish-status.

---

## 0. Sådan ser udgangspunktet ud (skal ikke laves om)

| Ting | Hvor |
|---|---|
| Ikoner, splash, adaptive lag | `apps/mobile/assets/` — genereres af `python3 scripts/build-icons.py` |
| Play-butiksikon + feature graphic | `apps/mobile/store/graphics/` |
| iPhone-screenshots (1290x2796) | `apps/mobile/store/screenshots/iphone-6.7/` |
| Butikstekster (kort/lang/keywords) | `apps/mobile/store/da-DK/` |
| Notes for Review (engelsk) | `apps/mobile/store/review-notes.md` |
| App Privacy + Data safety, felt for felt | `apps/mobile/store/privacy-answers.md` |
| EAS-projekt + alle 5 `EXPO_PUBLIC_*`-secrets | Expo-dashboardet, Cartspotter-org |

Appen er `version: 1.0.0`, `supportsTablet: false`, har in-app kontosletning og
ingen "kommer snart"-knapper. Lav ikke om på det uden at læse hvorfor i
`docs/Features.md`.

---

## 1. Gratis første runde: Android på Windows

Kan gøres **før** du betaler noget som helst, og afklarer den eneste tekniske
usikkerhed der er tilbage: at Google Sign-In virker i en rigtig binary.

### 1a. Ret fingerprint FØR du bygger

Google-OAuth-klienten kender lige nu SHA-1'en fra Mac'ens
`~/.android/debug.keystore`. En EAS-bygget APK er signeret med **EAS' egen**
keystore, så uden det her trin fejler login med `DEVELOPER_ERROR` (statuskode
10) — og det ligner en kodefejl, selvom det ikke er det.

```bash
cd apps/mobile
eas login
eas credentials            # vælg Android → Keystore → vis SHA-1 og SHA-256
```

Kopiér SHA-1 → [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
→ Android-OAuth-klienten for `dk.madshopper.app` → tilføj/erstat fingerprint.
Gem også SHA-256'eren; den skal bruges i trin 3.

### 1b. Byg APK'en (gratis kø hos Expo)

```bash
cd apps/mobile
npm run eas:build:preview     # eas build --profile preview → APK
```

Byggeriet kører i skyen. Når det er færdigt, får du et download-link.

### 1c. Emulator på Windows

1. Android Studio → Device Manager → Create device.
2. **Vælg et system-image mærket "Google Play"** — ikke "Google APIs", ikke et
   rent AOSP-image. Uden Play Services kan Google Sign-In slet ikke starte.
3. Start emulatoren, og træk `.apk`-filen ind i vinduet. Den installerer sig selv.

### 1d. Test disse fem ting

- [ ] Forsiden henter varer (viser at API og netværk virker fra en rigtig binary)
- [ ] Log ind med Google → kommer du tilbage til appen som logget ind?
- [ ] Læg noget i kurven, log ud, log ind igen → kom kurven med?
- [ ] Indstillinger → Konto → **Slet konto** → bekræft → du bliver logget ud
- [ ] Log ind igen med samme konto → skal fejle ("Invalid login credentials")

De to sidste er Apples Guideline 5.1.1(v). Serverdelen er allerede verificeret
28-07-2026 (konto oprettet, kurv gemt, `delete_own_account` → bruger væk), så
det du tester her, er knappen og dialogen.

### 1e. Screenshots til Play

Play vil have **16:9 eller 9:16** — iPhone-billederne (1290x2796 ≈ 1:2,17) kan
derfor ikke genbruges. Skyd 1080x1920 i emulatoren, samme fem skærme som
iPhone-sættet:

1. Forside med Ugens Tilbud
2. Produktdetalje med prissammenligning
3. Kurv med "Find billigste"
4. Butiksrute-resultat
5. Delt kurv

Læg dem i `apps/mobile/store/screenshots/android/`.

---

## 2. Apple (~99 USD/år)

1. [developer.apple.com](https://developer.apple.com) → Enroll i Apple Developer Program.
   Godkendelse tager typisk 1-2 dage.
2. Certificates, Identifiers & Profiles → App ID med bundle `dk.madshopper.app`,
   slå **Associated Domains** til.
3. Notér **Team ID** (10 tegn, står øverst til højre under Membership).
4. App Store Connect → My Apps → **+** → ny app:
   - Platform: iOS · Navn: MadShopper · Sprog: **Dansk (da-DK)** · Bundle: `dk.madshopper.app`
   - SKU: fx `madshopper-ios`
5. Kopiér appens **Apple ID** (numerisk) ind i `apps/mobile/eas.json` →
   `submit.production.ios.ascAppId` (erstatter `REPLACE_AFTER_APP_STORE_CONNECT`).
6. Byg og send til TestFlight:
   ```bash
   cd apps/mobile
   npm run eas:build:prod        # eas build --profile production
   npm run eas:submit            # eas submit --profile production
   ```
7. Test i TestFlight på din egen iPhone — især Google- og Apple-login, som aldrig
   har kørt på en fysisk enhed.

> Når Team ID findes, virker `npx expo run:ios` også lokalt igen. Indtil da er
> omvejen med `xcodebuild` i `.claude/skills/ios-app/SKILL.md` den eneste vej til
> simulatoren, fordi `associatedDomains` kræver signing.

---

## 3. Google Play (~25 USD engangs)

1. [play.google.com/console](https://play.google.com/console) → opret udviklerkonto.
   Identitetsverifikation kan tage flere dage — start den tidligt.
2. Ny app: MadShopper, dansk, gratis, package `dk.madshopper.app`.
3. Byg en AAB og send den ind:
   ```bash
   cd apps/mobile
   eas build --profile production --platform android
   eas submit --profile production --platform android
   ```
4. Play App Signing giver dig en **ny** SHA-256 (Play signerer selv appen).
   Find den under Release → Setup → App integrity. Den skal med i både:
   - Google Cloud → Android-OAuth-klienten (ellers virker Google-login ikke i
     den udgave brugerne henter), og
   - `ANDROID_CERT_SHA256` i næste trin.

---

## 4. Universal links / App Links (efter trin 2 og 3)

Uden det her åbner `madshopper.dk`-links i browseren i stedet for i appen.

Redigér `wrangler.toml` under `[env.production.vars]` (linje ~42-43), fjern
kommentar-tegnene og udfyld:

```toml
APPLE_TEAM_ID = "AB12CD34EF"
ANDROID_CERT_SHA256 = "AA:BB:...:ZZ"      # kommaseparér flere fingerprints
```

Deploy og verificér:

```bash
bash scripts/build-pages.sh
npx wrangler@4.114.0 deploy --config dist/wrangler.toml --env production

curl https://madshopper.dk/.well-known/apple-app-site-association
curl https://madshopper.dk/.well-known/assetlinks.json
```

Begge skal nu indeholde udfyldt `appID` hhv. `sha256_cert_fingerprints` — indtil
vars er sat, svarer de bevidst med tomme, men gyldige, payloads.

---

## 5. Udfyld konsollerne

Alt indholdet ligger klar; det er ren afskrift.

| Felt | Hent fra |
|---|---|
| Kort/lang beskrivelse, keywords | `apps/mobile/store/da-DK/` |
| Support-URL / privatliv / vilkår | `apps/mobile/store/metadata.md` § Kontakt & links |
| Notes for Review | `apps/mobile/store/review-notes.md` |
| App Privacy (Apple) | `apps/mobile/store/privacy-answers.md` § Apple |
| Data safety (Play) | `apps/mobile/store/privacy-answers.md` § Google Play |
| Aldersvurdering | `privacy-answers.md` § Aldersvurdering — 4+ / PEGI 3 |
| Screenshots | `apps/mobile/store/screenshots/` |
| Feature graphic + butiksikon | `apps/mobile/store/graphics/` |

Vigtigt i begge konsoller: **sprog = dansk (da-DK)** som primært sprog, ellers
matcher hverken tekster eller screenshots det brugerne ser. Og **tracking = nej**
i begge privatlivs-formularer.

---

## 6. Submit

Apple: App Store Connect → version 1.0 → Add for Review → Submit.
Play: Production → Create release → Review → Rollout.

Første Apple-review tager typisk 1-3 dage. Bliver den afvist, står svaret i
Resolution Center — send afvisningsteksten videre, så tolker jeg den.

---

## Hvis noget fejler

| Symptom | Hvad det er |
|---|---|
| `DEVELOPER_ERROR` / statuskode 10 ved Google-login | Fingerprint mangler i Google Cloud. Trin 1a eller 3.4 |
| Google-login gør ingenting i emulatoren | System-image uden Play Services. Trin 1c |
| `No code signing certificates are available` ved `expo run:ios` | Forventet indtil Apple Team ID findes. Brug `xcodebuild`-omvejen |
| `ITMS-90717: Invalid App Store Icon … alpha channel` | Skulle ikke kunne ske — prebuild fladgør ikonet til RGB. Kør `python3 scripts/build-icons.py` og prebuild igen |
| Afvist på 5.1.1(v) "account deletion" | Peg på Indstillinger → Konto → Slet konto i svaret; teksten står i `review-notes.md` |
| Afvist på 2.1 "placeholder content" | Noget ikke-fungerende er sneget sig ind i UI'et. Push/nyhedsbrev blev fjernet 28-07-2026 netop derfor |
| Links åbner i browseren i stedet for appen | Trin 4 mangler, eller vars er ikke deployet |
| En fjernet `Info.plist`-nøgle er der stadig lokalt | `expo prebuild` **tilføjer** kun; den rydder ikke op. Kør `npx expo prebuild --platform ios --clean`. EAS bygger altid rent i skyen, så det rammer kun lokale builds |

---

## Efter udgivelsen

- Bump `version` i `apps/mobile/app.config.js` ved hver ny udgivelse
  (`buildNumber`/`versionCode` styres automatisk af `autoIncrement` i `eas.json`).
- Ændrer du logoet: `python3 scripts/build-icons.py` +
  `node scripts/build-play-graphics.mjs`, så web, app og butik følges ad.
- Nye screenshots hører til samme commit som den UI-ændring de viser.
