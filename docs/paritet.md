# Web/app-paritet - matrix og status

Levende dokument. **Opdatér det i samme commit som du ændrer en feature** -
en matrix der lyver er værre end ingen matrix.

Senest revideret: **19-08-2026** (fuld gennemgang af `templates/`, `static/js/`,
`apps/mobile/src/`, `app.py`, `app_support.py`, `.github/workflows/`).

Grundprincippet: **ét produkt → én backend → én database → to præsentationslag.**
Web og app kalder de samme `/api/*`-endepunkter og de samme Supabase-RPC'er.
Al forretningslogik der kan ligge i backend, ligger i backend
(`app.py` / `app_support.py`), og begge platforme henter den derfra.

Symboler: ✅ implementeret · ⚠️ delvist · ❌ mangler · – ikke relevant

---

## 1. Feature-matrix

### Produkt og listevisning

| Feature | Web | App | Backend | Tests | Status |
|---|---|---|---|---|---|
| Forside med sektioner | ✅ | ✅ | ✅ `/api/home` | ✅ | Complete |
| Kategorisider (9 kategorier) | ✅ | ✅ | ✅ `/api/category/<slug>` | ✅ | Complete |
| Ugens Tilbud | ✅ | ✅ | ✅ `/api/sale` | ✅ | Complete |
| Underkategori-chips | ✅ | ✅ | ✅ | ✅ | Complete |
| Paginering | ✅ | ✅ | ✅ | ✅ | Complete |
| Produktkort: mærke/navn/pris/tilbudsbadge | ✅ | ✅ | ✅ | ✅ | Complete |
| Produktkort: vægt, "X stk", **kg-pris** | ✅ | ✅ | ✅ | ✅ | Complete *(app fik dem 19-08-2026)* |
| "Kun hos <butik>"-badge | ✅ | ✅ | ✅ | ✅ | Complete |
| Tom-tilstand ("ingen varer matcher") | ✅ | ✅ | – | ✅ | Complete |
| Fejltilstand + "Prøv igen" | ✅ | ✅ | – | ⚠️ | Complete |

### Søgning og filtrering

| Feature | Web | App | Backend | Tests | Status |
|---|---|---|---|---|---|
| Fritekstsøgning | ✅ | ✅ | ✅ `/api/search` | ✅ | Complete |
| Autocomplete (debounced) | ✅ | ✅ | ✅ `/api/autocomplete` | ✅ | Complete |
| Stavekorrektion ("mlæk" → "mælk") | ✅ | ✅ | ✅ | ✅ | Complete |
| Sortering (5 typer, inkl. kg-pris) | ✅ | ✅ | ✅ | ✅ | Complete |
| Prisinterval min/max | ✅ | ✅ | ✅ | ✅ | Complete |
| Filtre: tilbud / øko / laktosefri | ✅ | ✅ | ✅ | ✅ | Complete |
| Butiksvalg (14 butikker) | ✅ | ✅ | ✅ `/api/stores` | ⚠️ | Complete *(app fik det i filter-arket 19-08-2026)* |
| Butiksvalg debounced 300 ms | ✅ | ✅ | – | ❌ | Complete *(app 19-08-2026)* |
| Butiksskift nulstiller til side 1 | ✅ | ✅ | – | ❌ | Complete *(app-kategori/tilbud 19-08-2026)* |

### Produktdetaljer

| Feature | Web | App | Backend | Tests | Status |
|---|---|---|---|---|---|
| Prissammenligning på tværs af butikker (maks. 5) | ✅ | ✅ | ✅ | ⚠️ | Complete |
| Kg-pris pr. butik | ✅ | ✅ | ✅ | ⚠️ | Complete |
| Multikøbs-tilbud | ✅ | ✅ | ✅ | ✅ | Complete |
| Prishistorik-graf (30 dage, pr. butik) | ✅ | ✅ | ✅ `/api/price-history` | ❌ | Complete |
| Prisindsigt ("billigere end normalt") | ✅ | ✅ | – | ❌ | Complete |
| Næringsindhold + ingredienser | ✅ | ✅ | ✅ `/api/nutrition` | ❌ | Complete |
| Prisalarm ("Overvåg pris") | ✅ | ✅ | ✅ RPC `create_price_alert` | ❌ | Complete |
| Billed-zoom | ✅ | ❌ | – | – | **Bevidst forskel** (se §3) |

### Kurv, lister og deling

| Feature | Web | App | Backend | Tests | Status |
|---|---|---|---|---|---|
| Kurv: tilføj/fjern/antal | ✅ | ✅ | – | ✅ | Complete |
| Kurv gemt på server pr. bruger | ✅ | ✅ | ✅ `carts` + RLS | ❌ | Complete |
| Kurv-synk web ↔ app | ✅ | ✅ | ✅ | ❌ | Complete |
| Anonym kurv-statistik | ✅ | ✅ | ✅ RPC `record_cart_activity` | ❌ | Complete |
| Gemte lister (maks. 10) | ✅ | ✅ | ✅ | ❌ | Complete |
| Delt kurv (live, maks. 6 medlemmer) | ✅ | ✅ | ✅ 5 RPC'er | ❌ | Complete |
| Invitationslink | ✅ | ✅ | ✅ | ❌ | Complete |
| Forlad gruppe | ✅ | ✅ | ✅ | ❌ | Complete |
| "Find billigste" (SCO) | ✅ | ✅ | ✅ `/api/products` | ✅ | Complete |
| Alternativer til manglende varer | ✅ | ✅ | ✅ `/api/alternatives` | ✅ | Complete |
| Butiksrute (flere butikker) | ✅ | ✅ | – | ❌ | Complete |
| Personlig besparelse | ✅ | ✅ | ✅ RPC `get_personal_savings` | ❌ | Complete |

### Konto

| Feature | Web | App | Backend | Tests | Status |
|---|---|---|---|---|---|
| Opret konto (email) | ✅ | ✅ | ✅ Supabase Auth | ❌ | Complete |
| Log ind / log ud | ✅ | ✅ | ✅ | ❌ | Complete |
| Google-login | ✅ | ✅ | ✅ | ❌ | Complete |
| Apple-login | ⚠️ | ✅ | ✅ | ❌ | **Blokeret** (se §3) |
| Glemt adgangskode | ✅ | ✅ | ✅ | ❌ | Complete |
| Bot-tjek på signup (Turnstile) | ✅ | ✅ | ✅ Auth Hook | ❌ | Complete |
| Vist navn (delt kurv) | ✅ | ✅ | ✅ RPC `set_my_display_name` | ❌ | Complete |
| "Mine prisalarmer" (se/slet) | ✅ | ✅ | ✅ | ❌ | Complete |
| Slet konto | ✅ | ✅ | ✅ RPC `delete_own_account` | ❌ | Complete |

### Indstillinger og indhold

| Feature | Web | App | Backend | Tests | Status |
|---|---|---|---|---|---|
| Mørk tilstand | ✅ | ✅ | – | – | Complete |
| "Følg system"-tema | ✅ | ✅ | – | ✅ | Complete *(web fik det 19-08-2026)* |
| Standardbutikker | ✅ | ✅ | – | – | Complete |
| Feedback / meld fejl | ✅ | ✅ | ✅ `/api/feedback` | ❌ | Complete |
| Vilkår / privatliv / om os | ✅ | ✅ | – | – | Complete |
| Opskrifter (bag gate) | ✅ dev | ✅ dev | ✅ | ❌ | Gated - kun `dev` |
| Cookie-samtykke (Zaraz) | ✅ | – | – | – | **Bevidst forskel** (se §3) |
| Analytics (GA4 via Zaraz) | ✅ | – | – | – | **Bevidst forskel** (se §3) |
| Push-beskeder / nyhedsbrev | ❌ | ❌ | ❌ | – | **Findes ikke** (se §2) |

---

## 2. Åbne gaps

| # | Gap | Prioritet | Note |
|---|---|---|---|
| 1 | Ingen crash-/fejlrapportering i app'en | Høj | Ingen Sentry/Crashlytics. En fejl i produktion ses kun i App Store Connects crash-rapporter. En `ErrorBoundary` (19-08-2026) forhindrer nu hvid skærm, men rapporterer ikke videre. Kræver et leverandør- og privatlivsvalg. |
| 2 | Push-beskeder og nyhedsbrev findes ikke | Lav | To døde kontakter blev fjernet fra web 19-08-2026 (de skrev til localStorage, som intet læste). Skal det bygges, skal det bygges i backend + web + app samtidigt. Prisalarm-mails er den notifikation der faktisk findes. |
| 3 | Tyndt testdække på konto, delt kurv og prisalarmer | Høj | Kun `multiDeal`/`sco` (app) + listing-API-kontrakt (Python) + Playwright-røgtest. Ingen automatiserede tests af login, delt kurv, gemte lister eller prisalarmer på nogen af platformene. |
| 4 | App'ens tilgængelighed er stadig ujævn | Medium | Ikon-/symbol-knapper fik etiketter 19-08-2026, men de fleste skærme har stadig ingen `accessibilityRole`/`accessibilityLabel`, og der er ingen VoiceOver-gennemgang. |
| 5 | Ingen automatiseret web-a11y-kontrol | Medium | `scripts/audit-site.py` findes, men indgår ikke i deploy-workflowet. |

---

## 3. Bevidste, teknisk begrundede forskelle

Disse skal **ikke** rettes - de er dokumenteret her, så de ikke bliver "opdaget"
som gaps igen.

- **Cookie-banner og analytics kun på web.** App'en sætter ingen cookies og
  kalder aldrig ATT. `app.config.js` udelader bevidst
  `NSUserTrackingUsageDescription`, fordi App Privacy erklærer "no tracking".
  Kommer analytics på i app'en, skal begge dele ændres samtidigt.
- **Billed-zoom kun på web.** iOS/Android har systemets egen pinch-zoom-
  konvention; en modal kopi af webbens zoom ville modarbejde den.
- **Apple-login kun i app'en.** Koden ligger klar på web
  (`auth.js::ensureAppleSdk`); den mangler kun `window.__APPLE_CLIENT_ID` og
  Apple-provideren i Supabase. Begge kræver et Apple Developer-medlemskab,
  som ikke findes. Knappen er skjult indtil da - ingen død knap.
- **Butiksfiltrenes placering.** Web: en knaprække på hver liste-side.
  App: chips i filter-arket + kontakter i Indstillinger. Samme tilstand,
  samme spærre, samme persistens - kun placeringen følger platformen.
- **Serverrendering vs. JSON.** Web renderer lister server-side (SEO, hurtig
  first paint), app'en henter de samme data som JSON fra `/api/*`. Samme
  `product_to_display_dict` → samme felter.

---

## 4. Status

**Feature-paritet**

- Web: 100 % af de fælles features
- App: 100 % af de fælles features
- Backend: 100 % - al delt logik ligger i `app.py`/`app_support.py` og RPC'erne
- Tests: ~35 % - kritiske købsflows er dækket, konto/deling/alarmer er ikke

**Åbne fund**

- Kritiske: 0
- Høje: 2 (crash-rapportering i app, testdække på konto/deling/alarmer)
- Medium: 2 (a11y i app, automatiseret web-a11y)
- Lave: 1 (push/nyhedsbrev findes ikke)

**Udgivelsesblokkere: 0.** Ingen af de åbne fund forhindrer udgivelse af den
nuværende funktionalitet; de er efterslæb, ikke defekter.

---

## 5. Rettet 19-08-2026

| Fund | Platform | Rettelse |
|---|---|---|
| "Push-beskeder"/"Nyhedsbreve" var døde kontakter | Web | Fjernet + gamle localStorage-nøgler ryddes |
| Produktkort manglede vægt, "X stk" og kg-pris | App | Tilføjet, samme rækkefølge/betingelser som webbens makro |
| Butiksvalg kun i Indstillinger | App | Butiks-chips i filter-arket (`FiltersBar`) |
| Ét listing-kald pr. butikstryk | App | `queryLabels` - 300 ms debounce som webbens `scheduleStoreContentRefresh()` |
| Butiksskift beholdt sidetallet → tom skærm | App | Nulstiller til side 1 i kategori/tilbud |
| Mærket "None" vist på ~4 % af varerne | **Begge** | `clean_display_text()` i visningslaget + `_clean_field` ved kilden i `updater.py` |
| "Tilføj til kurv" uden varenavn for skærmlæsere | **Begge** | Varenavnet med i `aria-label` / `accessibilityLabel` |
| Ikon-knapper uden etiket (✎, ···, −, +, Fjern) | App | `accessibilityRole` + `accessibilityLabel` |
| Uventet render-fejl gav hvid skærm | App | `ErrorBoundary` yderst i `App.tsx`, verificeret i simulator |
| Mobile-tests kørte kun på pull requests | CI | `push`-trigger på `main`/`dev` (repoet har haft 1 PR i alt) |
| Død markup: `#overlay-pills` | Web | Fjernet (HTML + CSS) |
| `prisovervaagning.md` påstod at "Mine alarmer" manglede | Docs | Rettet - den findes på begge platforme |
| Web manglede app'ens "Følg system"-tema | Web | Tre valg med NØJAGTIG app'ens lagringskontrakt + `scripts/test-theme-parity.mjs` som gate (`parity-tests.yml`) |
