# MadShopper Native App

Expo (React Native) klient med fuld feature-paritet mål — se [`docs/native-app.md`](../../docs/native-app.md).

Ligger i monorepoet: `apps/mobile/` i [SammyIsse/Million](https://github.com/SammyIsse/Million).

**SDK:** Expo **54** (matcher App Store Expo Go; SDK 55+ kræver pt. TestFlight/`eas go` pga. Apple-review).

## Status

| Fase | Indhold | Status |
|---|---|---|
| 0 | Backend JSON-API'er | Done (`/api/home`, `/sale`, `/category`, `/search`) |
| 1 | Shell, theme, stores, Supabase | Done |
| 2 | Browse + filtre + søgning | Done |
| 3 | Produkt-detalje, chart, nutrition | Done |
| 4 | Cart sync + multi-deal | Done |
| 5 | SCO + butiksrute + alternatives | Done |
| 6 | Auth (email/Google/reset/delete) | Done |
| 7 | Shared cart + lister + deep links | Done (scheme `madshopper://`; Universal Links afventer Team ID) |
| 8 | Settings + legal + feedback | Done |
| 9 | Store release (Apple/Google) | Kode/OAuth/redirects/EAS-projekt/secrets klar. Review-blockers ryddet 28/7 (in-app kontosletning, ægte brand-ikoner, `supportsTablet: false`, v1.0.0, keywords/store-tekster, privacy-svar). Mangler konti (Apple/Play), Team ID/SHA-256, første EAS-build, Android-screenshots, submit — se `docs/Features.md` |

## Kør lokalt

```bash
cd apps/mobile
cp .env.example .env   # udfyld Supabase + evt. lokal API
npm install
npm start
```

> **Expo Go duer ikke længere.** `@react-native-google-signin/google-signin` er
> et native modul, så `npm start` alene fejler med
> `TurboModuleRegistry.getEnforcing(...): 'RNGoogleSignin' could not be found`.
> Byg en dev-client i stedet — den fulde kommandorække står i
> `.claude/skills/ios-app/SKILL.md`.

### Ikoner

App-ikon, splash og de tre Android-lag genereres fra `static/favicon.svg`
sammen med sidens favicon: `python3 scripts/build-icons.py` (macOS, manuelt).
Rediger dem ikke i hånden — så mister web og app fælles glyf.

### Flavors

| | Produktion | Staging / lokal |
|---|---|---|
| `EXPO_PUBLIC_API_BASE_URL` | `https://madshopper.dk` | `http://localhost:5001` el. staging-worker |
| `EXPO_PUBLIC_RPC_SUFFIX` | `` (tom) | `_dev` |
| Auth | Samme Supabase-projekt | Samme (skriver til `*_dev`) |

## Tests

```bash
# Fra apps/mobile
npm test                 # multi-deal + SCO
npx tsc --noEmit

# Fra repo-root
uv run python scripts/test-listing-api.py
uv run python scripts/verify-integrations.py
```

## Store-build (når Apple/Play-konti er klar)

EAS-projektet (`madshopper` under Cartspotter-org) og alle 5 `EXPO_PUBLIC_*`-secrets
(preview + production) er allerede sat op — se `docs/env-setup.md` §5a. Der resterer
kun:

```bash
npm i -g eas-cli
cd apps/mobile
eas login
npm run eas:build:preview   # eller eas:build:prod
npm run eas:submit          # efter App Store Connect-app / Play-app
```

Hele udgivelsesforløbet i rækkefølge — inkl. Android-testen på Windows,
fingerprint-fælden og hvor hvert butiks-felt hentes fra — står i
[`docs/udgivelse.md`](../../docs/udgivelse.md).
Baggrund: `docs/env-setup.md` §5/§5a/§5b og `docs/native-app.md` § Fase 9.

## Principper

- Ingen WebView-wrapper omkring madshopper.dk
- Anon-nøgle + RPC only (aldrig `service_role`)
- SCO / multi-deal / shared cart spejler web 1:1
- Ingen "kommer snart"-stubs i UI'et: push og nyhedsbrev blev fjernet fra
  Indstillinger 28/7, fordi kontakter der ikke gør noget er en 2.1-afvisning.
  De kommer tilbage samtidig med funktionen (`docs/prisovervaagning.md`)
