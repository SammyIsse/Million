# MadShopper Native App

Expo (React Native) klient med fuld feature-paritet mål — se [`docs/native-app.md`](../../docs/native-app.md).

Ligger i monorepoet: `apps/mobile/` i [SammyIsse/Million](https://github.com/SammyIsse/Million).

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
| 7 | Shared cart + lister + deep links | Done |
| 8 | Settings + legal + feedback | Done |
| 9 | Store release (Apple/Google) | Afventer developer-konti |

## Kør lokalt

```bash
cd apps/mobile
cp .env.example .env   # udfyld Supabase + evt. lokal API
npm install
npm start
```

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

# Fra repo-root
uv run python scripts/test-listing-api.py
```

## Principper

- Ingen WebView-wrapper omkring madshopper.dk
- Anon-nøgle + RPC only (aldrig `service_role`)
- SCO / multi-deal / shared cart spejler web 1:1
- Stubs forbliver stubs (prisalarm, push, nyhedsbrev, personlig besparelse)
