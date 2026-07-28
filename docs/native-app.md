# MadShopper Native App — Fuld implementeringsguide

**Status (2026-07-28):** Fase 0–8 er implementeret i `apps/mobile`. Fase 9 er forberedt i kode (`eas.json`, EAS-scripts, AASA/assetlinks, `store/`-metadata, mobile CI), EAS-projekt + secrets er sat, og review-blockerne er ryddet: in-app kontosletning (5.1.1(v), server-delen verificeret end-to-end 28/7), ægte brand-ikoner genereret fra `static/favicon.svg`, `supportsTablet: false`, v1.0.0, timeouts på alle API-kald, butikstekster + privacy-svar klar i `apps/mobile/store/`. Mangler stadig: Apple/Play-konti, Team ID + SHA-256 → deploy, første EAS-build, Android-screenshots, store submit. **Fremgangsmåde: [`docs/udgivelse.md`](udgivelse.md)**; status: `docs/Features.md` § Publish-status.  
**Ikke tilladt som “færdig”:** WebView-wrapper, Capacitor-shell omkring madshopper.dk, eller bevidst nedskåret MVP.  
**Backend:** Scraping, matching, D1/Supabase-cache forbliver. Native erstatter klienten og kræver JSON-API’er for produktlister.

Kildegrundlag (webklient pr. 2026-07):

| Fil | Rolle |
|---|---|
| `static/js/script.js` (~4421 linjer) | Kurv, SCO, søgning, overlay, shared cart, lister |
| `static/js/auth.js` (~644 linjer) | Auth, personlig kurv-sync, AuthBridge |
| `templates/base.html` | Shell-UI, overlay, auth-modal, settings, kurv |
| `macros/product_card.html` | Produktkort `data-*`-kontrakt |
| `app.py` / `app_support.py` | Routes, filtre, API’er |
| `scripts/supabase-carts.sql` | Personlig `carts` + `delete_own_account` |
| `scripts/supabase-shared-carts.sql` | Delt kurv-RPC’er |
| `docs/Features.md` | Roadmap (features *uden for* denne rewrite) |

---

## 0. Principper (låst)

1. **Én sandhed for forretningslogik.** Port SCO-, deal-, merge- og shared-cart-semantik 1:1 fra JS — ikke “forbedre” midt i rewrite.
2. **To kurv-lag.** Rich lokal model vs kompakt cloud `{p,q,n,i,s,pr}`.
3. **Priser i cloud er stale by design.** SCO genhenter altid live via `GET /api/products`.
4. **Anon-nøgle + RPC only.** Aldrig `service_role` i appen. Ingen direkte INSERT til `price_alerts` / `cart_events`.
5. **Prod vs staging.** Samme Auth-projekt; skrive-tabeller via `TABLE_SUFFIX` (`""` / `"_dev"`).
6. **Stubs forbliver stubs** i v1-port, medmindre de eksplicit færdiggøres (prisalarm-UI, push, email). Personlig besparelse er færdiggjort.
7. **Ingen shortcuts.** Alle features nedenfor skal spejles — ikke “vi tager SCO senere”.

---

## 1. Anbefalet tech stack

| Lag | Valg | Hvorfor |
|---|---|---|
| App | **Flutter** *eller* **React Native (Expo)** | Én codebase → iOS + Android |
| Auth | Supabase Auth SDK (native) | Samme users/RLS |
| Google | Native Google Sign-In → `signInWithIdToken` | Spejler GIS ID-token-flow |
| Charts | Native chart-lib (fx fl_chart / victory-native) | Erstat Chart.js |
| Secure storage | Keychain / EncryptedSharedPreferences | Refresh tokens |
| Deep links | Universal Links / App Links | `https://madshopper.dk/?liste=` |
| Analytics | ATT + native SDK (eller ingen indtil privacy er klar) | Erstat Zaraz |

**Ikke:** Ren WebView. Det er ikke “full native”.

---

## 2. Arkitektur

```
┌─────────────────────────────────────────────┐
│  Native App (iOS/Android)                   │
│  Screens + local cart store + SCO engine    │
└───────────────┬─────────────────────────────┘
                │ HTTPS
     ┌──────────┴──────────┐
     │                     │
┌────▼─────┐        ┌──────▼──────┐
│ MadShopper│        │  Supabase   │
│ Edge API  │        │ Auth + RPC  │
│ (Workers) │        │ carts/share │
└────┬─────┘        └─────────────┘
     │
  D1 + KV (read) / PostgREST RPC (write)
```

### 2.1 Nye backend-krav (blokerende)

I dag returnerer listing-ruter **HTML**. Native skal have JSON for:

| Ny / udvidet endpoint | Erstatter |
|---|---|
| `GET /api/home` | `home()` + `partials/index_products.html` |
| `GET /api/category/<slug>` | `category()` + pagination |
| `GET /api/sale` | `ugens_tilbud()` |
| `GET /api/search` | `/search` HTML-fragment + `/search/results` |

**Eksisterende JSON-API’er genbruges uændret** (kontrakt nedenfor).

Alle listing-API’er skal acceptere samme query-params som web:

`stores`, `sort`, `min_price`, `max_price`, `sale`, `organic`, `lactose`, `min_weight`, `max_weight`, `page`, `subcategory`, `q`.

---

## 3. Produkt-datamodel (JSON-kontrakt)

Port felt-for-felt fra `macros/product_card.html` + `product_to_display_dict`.

### 3.1 Kernfelter

| Felt | Betydning |
|---|---|
| `id` | Raw produkt-id (UI-prefix `product` kun lokalt hvis DOM-id spejles) |
| `name`, `brand`, `description` | Visning |
| `image` / `main_image`, `rema_image` | Billed-URL’er |
| `category`, `subcategory` | Kategori + underkategori |
| `store` | Display-label (default `"Rema 1000"`) |
| `price`, `normal_price`, `is_sale`, `sale_end_date` | Pris |
| `unit_measure`, `weight_g`, `stk_count`, `kg_price` | Mål |
| `multi_deal` | Fx `"2 for 30"` |
| `is_organic`, `is_lactose_free` | Filter-heuristik |
| `has_match`, `has_match_rema` | Match-flag |
| `cheapest_at` | Store-key for billigste |
| `lowest_price_30d` | Findes i cache; **web renderer den ikke** — valgfri i native for paritet |

### 3.2 `store_matches[key]`

For hver butiksnøgle (`bilka`, `foetex`, `mk`, …):

```json
{
  "name": "...",
  "price": 12.5,
  "normal_price": 15.0,
  "is_sale": true,
  "image": "...",
  "brand": "...",
  "description": "...",
  "weight": "...",
  "kg_price": 25.0,
  "multi_deal": "2 for 30",
  "ean": "...",
  "Kategori": "..."
}
```

### 3.3 Product card `data-*` (web-kontrakt der skal spejles i JSON)

Faste attributter på kortet:

| Attribut | Betydning |
|---|---|
| `data-rema-price` | Rema-pris |
| `data-rema-weight` | Enhedstekst |
| `data-weight-g` | Vægt i gram |
| `data-stk-count` | Antal stk |
| `data-rema-kg-price` | Rema kg-pris |
| `data-store` | Visningsbutik-label |
| `data-has-match` | Match eller positiv Rema-pris |
| `data-has-match-rema` | Positiv Rema-pris |
| `data-rema-is-sale` | Rema på tilbud |
| `data-rema-id` | Produkt-id kun hvis display-store er Rema |
| `data-multideal` | Multi-deal på visningsbutik |
| `data-cheapest-at` | Store-key for billigste |
| `data-category` | Default `Andre varer` |
| `data-subcategory` | Undercategory-label |
| `data-main-image` / `data-rema-image` | Billeder |
| `data-is-organic` / `data-is-lactose-free` | Boolean-strenge |

Per match-key: `data-{{key}}-price`, `-name`, `-kg-price`, `-is-sale`, `-id` (EAN), `-multideal`.

### 3.4 Butikskatalog (`GET /api/stores`)

14 butikker fra `_STORE_CONFIGS`:

| key | label | Logo (static) |
|---|---|---|
| rema | Rema 1000 | `Rema1000-logo.png` |
| bilka | Bilka | `bilka-logo.png` |
| netto | Netto | `netto-logo.png` |
| foetex | Føtex | `foetex-logo.png` |
| mk | Min Købmand | `Min_kobmand_logo.png` |
| meny | Meny | `meny-logo.png` |
| spar | Spar | `spar-logo.png` |
| sb | SuperBrugsen | `superbrugsen-logo.png` |
| brugsen | Brugsen | `brugsen-logo.png` |
| kvickly | Kvickly | `kvickly-logo.png` |
| discount365 | 365 Discount | `365discount-logo.png` |
| lidl | Lidl | `lidl-logo.png` |
| loevbjerg | Løvbjerg | `loevbjerg-logo.png` |
| abclavpris | ABC Lavpris | `abc-lavpris-logo.png` |

Inkluder `logo`-URL + `STORE_CATALOG_VERSION` (auto-enable af nye butikker).

### 3.5 Image CDN-hosts (whitelist i app ATS/network)

Fra `_IMG_HOSTS` i `app.py`:

- `rema-product-images.digital.rema1000.dk`
- `digitalassets.sallinggroup.com`
- `dagrofa-dam.s3.eu-central-1.amazonaws.com`
- `image-transformer-api.tjek.com`
- `imgproxy-retcat.assets.schwarz` (Lidl)
- `image.prod.iposeninfra.com`
- `nxtumbraco.azurewebsites.net`

---

## 4. Eksisterende JSON-API’er (genbrug uændret)

### 4.1 `GET /api/stores`

Butikskatalog + version til klienten.

### 4.2 `GET /api/products`

Slim priser til kurv-sammenligning. Ingen params.

```json
{
  "success": true,
  "rema_products": [
    {
      "/product/id": "...",
      "/product/price": 12.5,
      "/product/sale_price": null,
      "/product/store_matches": {
        "<store_key>": { "price": 11.0 }
      }
    }
  ],
  "bilka_products": []
}
```

`bilka_products` er altid `[]` (legacy) — ignorér i native.  
`getProductPrice`: brug `sale_price` hvis sat, ellers `price`.

### 4.3 `POST /api/alternatives`

```json
{
  "missing_items": [
    {
      "cart_id": "product…",
      "product_id": "product…",
      "store": "Bilka",
      "category": "…",
      "name": "…",
      "weight_str": "…",
      "price": 12.95,
      "image": "…"
    }
  ]
}
```

Max 30 items. Rate-limited. `category` og `name` er påkrævede — uden dem springes varen over.
`price` er varens kendte pris i de butikker der fører den (billigste); uden den
frafalder pris-gaten, og forslagene bliver ringere. Forslag er butiks-specifikke:
send varerne for **den butik brugeren kigger på**, ikke ét samlet kald for hele kurven.

Søgelogik (server, `app.py::_find_alternative`): kandidater i samme kategori +
underkategori som butikken faktisk fører (på edge afgjort i SQL sammen med
vægtinterval og ordfilter) → pris inden for 0,4–2,0× → vægt ±25 % → navnescore
≥ 0,35 **og** mindst ét fælles indholdsord der ikke er brandnavnet → samme
variant (øko/laktose-/sukker-/gluten-/alkoholfri) og samme kødtype → blandt de
lige gode forslag vælges det billigste.

Response-item:

```json
{
  "cart_id": "...",
  "store": "Bilka",
  "alt_id": "...",
  "alt_name": "...",
  "alt_price": 10.0,
  "alt_image": "...",
  "alt_storePrices": { "Rema 1000": 10.0 },
  "alt_category": "...",
  "alt_unitMeasure": "...",
  "alt_kgPrice": "...",
  "alt_store": "..."
}
```

### 4.4 `GET /api/autocomplete?q=&stores=`

- `q` min 2 tegn (ellers tom)
- Max **8** unikke navne
- Response:

```json
{
  "suggestions": [
    { "name": "...", "brand": "...", "price": 1.0, "is_sale": false, "image": "...", "category": "..." }
  ],
  "query_suggestion": "<raw query>"
}
```

Klient: debounce **200 ms**.

### 4.5 `GET /api/price-history/<id>`

```json
{
  "success": true,
  "history": [{ "price": 10.0, "date": "YYYY-MM-DD" }],
  "history_by_store": {
    "rema": [{ "price": 10.0, "date": "YYYY-MM-DD" }],
    "bilka": []
  }
}
```

Keys = store keys (`rema`, `bilka`, …), ikke labels. Sidste 30 dage.  
Klient patcher dagens pris fra overlay; single-point → flat 30-dages linje.

### 4.6 `GET /api/nutrition/<id>`

```json
{
  "success": true,
  "nutrition": null | {
    "per": "100 g",
    "rows": [{ "label": "Energi", "value": "…" }],
    "ingredients": "…" | null,
    "source": "rema" | "salling" | "off"
  }
}
```

UI-kilde-labels: Rema / butikkens varedeklaration / Open Food Facts.

### 4.7 `POST /api/cart-event`

Rate limit: **20/min/IP**.

```json
{ "event": "add" | "compare", "items": [{ "id": "<rawId uden product-prefix>", "qty": 1 }] }
```

Caps: max 50 ids, qty 1–99. SQL-vægt: add=1, compare=3.  
Response: `{ ok, persisted }`. Anonym by design.

Legacy accepteres også: `{ product_id }` / `{ product_ids: [] }`.

### 4.8 `POST /api/create-alert`

API er færdig, men **UI kalder den ikke** (stub). For paritet: kald den ikke fra native v1.

```json
{
  "product_id": "≤64",
  "product_name": "≤200",
  "target_price": 1.0,
  "current_price": 2.0
}
```

### 4.9 `POST /api/feedback`

```json
{
  "type": "feedback|bug|feature|other",
  "name": "≤120 optional",
  "email": "≤200 optional",
  "subject": "≤200 optional",
  "message": "10–5000 chars required",
  "page_url": "≤500"
}
```

Persistens: D1 `pending_feedback` → GitHub Actions → Google Sheet (ikke Supabase).

### 4.10 Rate limits (`app_support.py`)

| Limiter | Cap |
|---|---|
| `api_limiter` | 60 / 60s / IP |
| `cart_event_limiter` | 20 / 60s / IP |

---

## 5. Skærme og navigation (fuld paritet)

### 5.1 Shell

- Logo, søgefelt, kurv-badge (**sum af quantities**), indstillinger, auth
- Kategori-nav (horizontal scroll): Ugens Tilbud + 8 kategorier
- Web ≤767px: hamburger + side-paneler + filter bottom sheet — native skal have **alle** destinations (tab bar og/eller drawer)

### 5.2 Forside (`/`)

Sektioner i rækkefølge:

1. Hero / intro
2. Filtre (sort, pris, tilbud, øko, laktose)
3. Butiksfilter-chips (alle 14)
4. **Ugens Tilbud** — op til 10 kort, “Vis alle” → sale-liste
5. **Populære varer** — op til 10 (fra `cart_popularity` ≥2; fallback staples)
6. **Køl** — op til 10, link til `/Mejeri`
7. **Personlig besparelse** — live (login + `get_personal_savings` / `record_compare_savings`)

Server-caps i `home()`: sale 60 / favoritter 20 / mejeri 60; UI viser 10/10/10.

### 5.3 Kategorisider

| Visningsnavn | URL-slug |
|---|---|
| Køl | `/Mejeri` (alias `/Køl`) |
| Kød & Fisk | `/Koed_og_fisk` |
| Frugt & Grønt | `/Frugt_og_groent` |
| Brød & Kager | `/Broed_og_kager` |
| Frost | `/Frost` |
| Kolonial | `/Kolonial` |
| Drikkevarer | `/Drikkevarer` |
| Slik | `/Slik` |
| Ugens Tilbud | `/ugens_tilbud` |

Pagination: **60 pr. side**, bevar query-args, jump-input hvis `total_pages > 5`.

#### Undercategories (`?subcategory=`)

Kun på kategorisider (ikke forside/sale/søgning):

| Kategori | Undercategories |
|---|---|
| Drikkevarer | Øl & Cider, Vin & Spiritus, Kaffe & Te, Juice & Smoothie, Saft & Sirup, Vand, Sodavand & Energi (+ Øvrige) |
| Køl | Mælk & Fløde, Yoghurt & Kvark, Ost, Smør & Fedtstof, Æg, Pålæg & Kølvarer |
| Kød & Fisk | Oksekød & Kalv, Svinekød, Fjerkræ, Lam & Vildt, Fisk & Skaldyr, Pølser |
| Frugt & Grønt | Frugt, Grøntsager, Svampe, Krydderurter |
| Brød & Kager | Rugbrød & Knækbrød, Brød, Boller, Kager & Wienerbrød, Kiks & Vafler, Bagning |
| Frost | Is & Desserter, Frossen Fisk, Frossen Kød, Frossen Grønt & Frugt, Frost Brød, Færdigretter |
| Kolonial | Pasta & Ris, Konserves & Dåse, Morgenmad, Krydderier & Sauce, Olie & Eddike, Nødder & Tørret Frugt, Bagning & Sødning, Supper & Snacks |
| Slik | Chokolade, Slik & Vingummi, Chips & Snacks, Proteinbarer |

### 5.4 Søgning

**A. Live-panel (primær UX)**

- Debounce søgning **500 ms**
- Autocomplete debounce **200 ms**
- `GET /api/autocomplete` + `GET /api/search` (JSON produkter)
- Første autocomplete-række: “Søg efter **q**”
- Footer: “Se alle resultater…”
- Ny søgning nulstiller advanced filters
- Escape/back lukker panel

**B. Fuld resultatside**

- Pagination 60, filtre, default sort `relevance` = `search_match_score`
- Tom `q` → home

### 5.5 Produktkort — badges

| Badge | Betingelse |
|---|---|
| Tilbud | `is_sale` eller `is_any_sale` |
| Store-badge | Visningsbutik / client swap |
| Kun hos X | ingen `store_matches` |
| Øko / laktosefri | **ingen** kort-badge på web (kun filter) |
| 30-dages laveste | Data findes; **ikke renderet** på web |

### 5.6 Produkt-detalje (overlay)

Sektioner i rækkefølge:

1. Billede + zoom
2. Brand, titel, beskrivelse, tilbudsudløb
3. Pris (sale/original)
4. “Overvåg pris” → **coming soon**-modal (paritet)
5. Quantity stepper (min 1)
6. Add to cart (tekst inkl. valgt butik)
7. Prissammenligning: op til **5** billigste blandt `selectedStores` (`OVERLAY_COMP_MAX_STORES = 5`)
   - pris, tilbud-tag, kg-pris, multi-deal, “Billigst” / “+X.XX kr”
8. Prishistorik 30 dage (chart skifter ved butiksklik)
9. Insight-badge: `Stabil pris` | `Godt tilbud!` (`cur < 0.9 * avg`) | `Lille besparelse`
10. Næringsindhold: tabel / ingredients / empty / source

14 butikker hardcodet i comparison-cards (rema … abclavpris).

### 5.7 Kurv-panel

- Grupperet på `category`
- Qty ±, slet med animation (~300 ms)
- Footer-total: **uden** multi-deal bundles
- Actions: Ryd, Gem liste, Del kurv, Find billigste (SCO), Butiksrute
- Tabs hvis shared: medlemmer / lister

### 5.8 Settings

| Feature | Status | Persistens |
|---|---|---|
| Dark mode | Reel | `madshopper_darkmode` → `data-theme=dark` |
| Standardbutikker | Reel | `selectedStores` + store-persist |
| Push | Stub | Kun lokal toggle |
| Nyhedsbrev | Stub | Kun lokal toggle |

### 5.9 Juridiske sider

| Side | Routes |
|---|---|
| Vilkår | `/terms-of-service`, `/vilkaar.html` |
| Privatliv | `/privacy`, `/privatliv`, `/privatliv.html` |
| Om os | `/about`, `/om-os.html` |
| Feedback | `/feedback`, `/feedback.html` |

In-app: native screens eller WebView. Contact: `kontakt@madshopper.dk`.  
Privacy’s cookie-sektion skal omskrives til app-permissions/analytics.

---

## 6. Filtre & butikker

### 6.1 Sort

`relevance` | `price-asc` | `price-desc` | `kg-price-asc` | `name-asc`

### 6.2 Flags / ranges

- `sale=true`, `organic=true`, `lactose=true`
- `min_price` / `max_price`
- `min_weight` / `max_weight` — understøttet server-side; **UI-inputs mangler på web** (død kode). Port API; UI valgfri for paritet.

### 6.3 Butiksfilter

- Globalt `selectedStores: Set<label>`
- Persist: localStorage + (web) cookie `madshopper_stores` kun ved Zaraz-samtykke `icuR`
- URL `?stores=Rema 1000,Bilka,…` når **ikke alle** er valgt
- Auto-enable nye butikker via `STORE_CATALOG_VERSION` / `madshopper_store_version`
- `product_for_active_stores`: promover anden butiks pris når Rema er fravalgt
- Fravalgte butikker indgår **ikke** i SCO / rute / footer

### 6.4 Session-filtre

Web gemmer filtre i `sessionStorage` pr. path (`filter_*`); nulstilles ved path-skift. Spejl i native navigation-state.

---

## 7. Kurv — fuld specifikationskontrakt

Kilde: `addToCart`, `saveCart`, `calculateStoreComparisons`, `auth.js` `cartToRows` / `rowsToCart`.

### 7.1 Rich local item

```ts
{
  id: string,              // "product" + rawId
  name: string,
  store: string,           // primær butik, default "Rema 1000"
  price: number,           // synlig hovedpris
  storePrices: Record<string, number>,      // label → pris
  storeMultiDeals: Record<string, string>,  // label → "2 for 30"
  image: string,
  category: string,        // default "Andre varer"
  unitMeasure: string,     // til alternatives
  kgPrice: string,
  multiDeal?: string,      // hovedkortets deal (primært UI)
  quantity: number         // ≥ 1
}
```

**Legacy-felter** (migrér ved load): `remaPrice`, `bilkaPrice`, `mkPrice`, `menyPrice`, `sparPrice`.

### 7.2 Compact cloud `{p,q,n,i,s,pr}`

| Key | Full felt | Caps |
|---|---|---|
| `p` | `id` | max 64 |
| `q` | `quantity` | int 1–99 |
| `n` | `name` | max 120 |
| `i` | `image` | max 300 |
| `s` | `store` | max 40 |
| `pr` | `price` | number eller `null` |

- Max **100** items; JSON-tekst ≤ **8000** (DB CHECK)
- **Droppes i cloud:** `storePrices`, `storeMultiDeals`, `multiDeal`, `category`, `unitMeasure`, `kgPrice`
- Efter round-trip: SCO afhænger af `/api/products`; alternatives uden `category` giver intet forslag (både lokalt og på D1)

### 7.3 Operationer

| Op | Funktion (web) | Adfærd |
|---|---|---|
| Add (kort) | `addToCart` | Samme `id` → `quantity++`; ellers push; `POST /api/cart-event` add qty=1 |
| Add (overlay) | `addToCartFromOverlay` | Qty fra stepper |
| Remove by id | `removeFromCart` | Filter + save |
| Remove by index | `deleteCartItem` | Fade 300 ms → splice |
| Qty ± | `updateQuantity` | ≤0 → fjern |
| Clear | `clearCart` | Empty + `CartBridge.notify` (brug versionen ~3084) |
| Display | `updateCartDisplay` | Gruppér på `category`; unit = første valid `storePrices` |
| Badge | `updateCartCount` | Sum af `quantity` |
| Accept alt | `acceptAlternative` | Erstat item; `storeMultiDeals: {}`; re-run `showReference` |
| Load list | `loadSavedList` | Erstat cart + save |

### 7.4 Multi-deal

```js
parseMultiDeal(dealStr)  // /(\d+)\s+for\s+([\d.,]+)/i
applyDealPrice(regularPrice, quantity, dealStr)
  // bundles * totalPrice + remainder * regularPrice
```

**Anvendes kun** i `calculateStoreComparisons` (SCO-totals).  
**Ikke** i: kurv-footer, butiksrute, SCO “by store”-gruppering uden for totals.

### 7.5 Bridges

**`window.CartBridge`** (`script.js`):

```js
{
  _onChange: null | (cart) => void,
  get: () => cart,
  applyFromServer(items),  // sætter cart + UI; KALDER IKKE notify
  notify()                 // kalder _onChange
}
```

- Personal sync debounce: **800 ms**
- Shared push debounce: **450 ms**
- Shared poll: **2500 ms**

**`window.AuthBridge`** (`auth.js`):

```js
{
  getUser(), getClient(),
  rpcName(base) => base + __SB_RPC_SUFFIX,  // '' eller '_dev'
  requireAuth(),
  getDisplayName(), ensureDisplayName(),   // max 40
  onSignedIn, onSignedOut
}
```

### 7.6 Login / logout kurv

- **Login:** `pullCart` → `mergeCarts` (max qty, bevar lokale rige felter) → `applyFromServer` → `pushCart`
- **Logout:** flush debounce → `pushCart` → `signOut` → clear lokal kurv
- **INITIAL_SESSION uden session:** behold anonym lokal kurv

---

## 8. SCO & butiksrute (algoritmer — port 1:1)

### 8.1 `calculateStoreComparisons`

1. Init pr. `ALL_STORES.label`: totals, coverage, missingDetails, matchedItems, exclusiveItems.
2. Læs cart fra local storage.
3. `GET /api/products` → map keyed på `String(p['/product/id'])` (uden `"product"`-prefix).
4. For hvert cart-item:
   - Strip `id`: `cartItem.id.replace('product','')`
   - Byg `prices{}` fra `storePrices` (kun `> 0`) eller legacy
   - Augment fra API: manglende Rema via `getProductPrice`; manglende matches via `store_matches[key].price` → label
   - For hver pris hvor `selectedStores.has(label)`:
     - `coverage++`
     - `total += applyDealPrice(p, qty, storeMultiDeals[label])`
     - push til matched
   - Labels uden valid pris → `missingDetails` til alternatives
5. Returnér stores med `total>0 || coverage>0`:
   `{ name, totalPrice, coverage, totalItems, missingDetails }`  
   + `matchedItemsPerStore`, `linesWithoutMatches`, `exclusiveItems`, `partialItems`

### 8.2 `showReference` (SCO UI)

1. Guard: tom kurv / allerede loading
2. `recordCompareEvent` → `/api/cart-event` `event:'compare'` (dedup in-memory `_comparedProductIds`)
3. `await calculateStoreComparisons()`
4. Sort: **højest coverage**, derefter **lavest totalPrice**
5. Top 5; vælg vinder
6. `selectScoStore` henter `POST /api/alternatives` for **den valgte butik** (cachet pr. butik i `_scoCompData.altByStore`) og re-renderer; forslag er butiks-specifikke, så ét fælles kald for hele kurven kunne kun besvare én butik
7. UI: mangler øverst (med alt-knap), matches nederst; total = matched-pris (**uden** alt-priser før accept)

### 8.3 `showButiksrute`

1. `calculateStoreComparisons()` til single-cheapest baseline
2. For hvert cart-item: billigste butik blandt **selected** — **uden** multi-deal
3. Gruppér varer pr. butik; `routeTotal` = sum
4. `savings = singleCheapest.totalPrice - routeTotal` (kun hvis > 0.05)
5. UI: butikker sorteret efter subtotal desc

**Forskel:** SCO = “køb alt i én butik (dækning + pris)”. Butiksrute = “split pr. vare til billigste butik”.

---

## 9. Shared cart & gemte lister

### 9.1 RPCs

Staging = samme navn + `_dev` via `__SB_RPC_SUFFIX` (undtagen `delete_own_account`).

| RPC | Args | Formål |
|---|---|---|
| `get_my_shared_cart` | — | Hent gruppe |
| `create_shared_cart` | `p_items`, `p_title`, `p_name` | Opret (eller `already:true`) |
| `join_shared_cart` | `p_token`, `p_name` | Join; auto-leave forrige; `full` hvis ≥6 |
| `leave_shared_cart` | — | Forlad; slet kurv hvis sidste |
| `push_shared_cart` | `p_items` | Live overwrite; `revision++` |
| `push_shared_saved_lists` | `p_lists` | Fælles lister |
| `set_my_display_name` | `p_name` | Max 40 |

**Payload:**

```json
{
  "ok": true,
  "cart_id": "...",
  "token": "...",
  "title": "...",
  "items": [],
  "saved_lists": [],
  "revision": 1,
  "updated_at": "...",
  "updated_by": "...",
  "members": 2,
  "max_members": 6,
  "member_list": [{ "id": "...", "name": "...", "me": true }]
}
```

### 9.2 Client-regler

| Koncept | Værdi |
|---|---|
| Poll interval | **2500 ms** |
| Push debounce | **450 ms** |
| Invite URL | `{origin}/?liste={token}` |
| Token | 12 hex chars typisk; accept 8–32 |
| Max members | **6** |
| Pending invite | session-ækvivalent indtil login |
| Max lists | **10** |
| Conflict | **Last-write-wins** (ingen optimistic lock) |
| Poll overwrite | `remoteRev > localRev` **og** `updated_by !== me` |
| Leave | Lokal kurv **beholdes** |
| Enter | Remote items **erstatter** lokal; private lists merges (gruppe først) |

Deep link i native: Universal/App Link til `https://madshopper.dk/?liste=<token>`.

### 9.3 Saved lists

| | Personlig | I gruppe |
|---|---|---|
| Storage | `savedLists:<userId>` lokal | `shared_carts.saved_lists` via RPC |
| Max | 10 | 10 (+ DB CHECK / ~60 KB) |
| Shape (UI) | `{ id, name, createdAt, items: fullCart[] }` | samme efter hydrate |
| Compact | — | `{ id, name, created_at, items: [{p,q,n,i,s,pr}] }` |
| Ops | save / load (replace cart) / delete | samme |

---

## 10. Auth (fuld paritet)

Kilde: `static/js/auth.js` + auth-modal i `base.html`.

### 10.1 Opsætning

```js
supabase.createClient(url, key, {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true }
})
```

Globals: `__SB_URL`, `__SB_KEY`, `__SB_CARTS`, `__SB_RPC_SUFFIX`, `__GOOGLE_CLIENT_ID`.

### 10.2 Flows

| Flow | Adfærd |
|---|---|
| Email signup | `signUp` med `emailRedirectTo: origin`, `data.display_name` (max 40). Email-bekræftelse kan være slået fra (se `docs/email-bekraeftelse.md`) |
| Email login | `signInWithPassword` |
| Google primær | Native Google Sign-In → ID-token + nonce (SHA-256) → `signInWithIdToken({ provider:'google', token, nonce })` |
| Google fallback | OAuth redirect (valgfri) |
| Password reset | `resetPasswordForEmail` → `PASSWORD_RECOVERY` → `updateUser({ password })` (min 8) |
| Logout | Flush sync → push cart → `signOut` → clear lokal kurv |
| Slet konto | Confirm → `rpc('delete_own_account')` **uden** suffix → signOut → clear cart |
| Display name | metadata `display_name` \| `full_name` \| `name`; sync til `set_my_display_name[+_dev]` |

### 10.3 `TABLE_SUFFIX` / `_dev`

| Miljø | Suffix | Skrive-mål |
|---|---|---|
| Produktion | `""` | `carts`, shared_*, alerts, popularity |
| Staging / lokal | `"_dev"` | `*_dev` + RPC’er `*_dev` |

Auth er **delt** (samme `auth.users`). Kun skrive-data adskilles.  
**Undtagelse:** `delete_own_account` har ingen `_dev`-variant.

---

## 11. Analytics & privacy

### 11.1 Web (Zaraz)

| Purpose ID | Betydning |
|---|---|
| `NpgO` | Analyse — kræves for `zaraz.track` |
| `icuR` | Funktionelle — kræves for store-cookie |

Events: `add_to_cart`, `compare_prices`, `category_click`, `search`.

### 11.2 Native

- Erstat Zaraz med platform privacy (ATT på iOS, Play consent)
- Butiksvalg i app-storage (ingen cookies)
- Spejl samme event-navne hvis analytics genindføres
- Privacy-tekst skal opdateres (cookie-sektion er web-specifik)

---

## 12. Security (bevar kontrakten)

| Web-kontrol | Native-implikation |
|---|---|
| Publishable key i klient | OK — aldrig service_role |
| Skrivning kun via SECURITY DEFINER RPC | Bevar |
| Session i localStorage | Secure Store / Keychain |
| Google web popup | Native Google Sign-In SDK |
| `connect-src` self + `*.supabase.co` + Google | Whitelist samme hosts |
| Image CDN-hosts | Whitelist `_IMG_HOSTS` |
| Rate limits på `/api/*` | App rammer samme caps |
| Edge: aggregeret security logging | Bevar hvis worker-logik spejles |

---

## 13. Stubs — port som stubs

| Feature | Adfærd i native v1 |
|---|---|
| Overvåg pris | Modal “under udvikling” |
| Push settings | Local toggle only |
| Email newsletter | Local toggle only |
| Personlig besparelse | Live banner (beløb + Top X %; login-CTA) |
| `POST /api/create-alert` | Findes; UI kalder den **ikke** |

**Uden for denne rewrite** (`docs/Features.md`): Mit køleskab, opskrifter, “Fra tilbud”, osv.

---

## 14. Implementeringsfaser

### Fase 0 — Backend JSON (blokerende)

- [x] `GET /api/home`
- [x] `GET /api/category/<slug>`
- [x] `GET /api/sale`
- [x] `GET /api/search` (erstatter HTML-fragment)
- [x] Stabil produkt-JSON-schema (alle card-felter)
- [x] OpenAPI/kontrakt-test + smoke tests
- [x] Samme filter/pagination/semantik som HTML-routes

### Fase 1 — App skeleton

- [x] Navigation shell, theme (light/dark), store catalog
- [x] Secure Supabase client + env flavors (prod/staging)

### Fase 2 — Browse

- [x] Home sections, category + subcats, sale, pagination
- [x] Product cards + badges + store filter visual swap
- [x] Search autocomplete + results panel + full results

### Fase 3 — Product detail

- [x] Overlay/sheet parity
- [x] Price history charts + insight
- [x] Nutrition
- [x] Add to cart from detail

### Fase 4 — Cart core

- [x] Rich local cart CRUD
- [x] Multi-deal parse/apply
- [x] Cart-event analytics
- [x] Personal cloud sync + merge

### Fase 5 — SCO + route + alternatives

- [x] Exact algorithms (coverage → price)
- [x] Alternatives accept flow
- [x] Top 5 UI

### Fase 6 — Auth

- [x] Email + Google + reset + delete + display name
- [x] Sign in with Apple (`expo-apple-authentication`, iOS-only) — tilføjet 2026-07-27, påkrævet af App Store-regel 4.8 da Google native Sign-In blev tilføjet samme dag. Kode/entitlement klar; kræver Apple Developer Team ID for at virke uden for Simulator (samme blocker som resten af Fase 9).

### Fase 7 — Shared + lists

- [x] Alle RPC’er, poll, invite deep links, saved lists

### Fase 8 — Settings + legal + feedback

- [x] Dark mode, store defaults, stubs
- [x] Privacy / terms / about / feedback

### Fase 9 — Store release

- [x] `apps/mobile/eas.json` (development / preview / production)
- [x] `/.well-known/apple-app-site-association` + `assetlinks.json` routes (aktiveres når `APPLE_TEAM_ID` / `ANDROID_CERT_SHA256` er sat) — verificeret lokalt 2026-07-26 med Flask test client + dummy env: begge routes returnerer gyldig JSON både uden vars (tomme `details`/`[]`) og med vars sat (udfyldt `appID`/`sha256_cert_fingerprints`).
- [x] `apps/mobile/package.json` scripts: `eas:build:dev`, `eas:build:preview`, `eas:build:prod`, `eas:submit` (kalder profilerne i `eas.json`)
- [x] `apps/mobile/store/` — da-DK beskrivelser, keywords, privacy/support, screenshot-checklist
- [x] CI: `.github/workflows/mobile-tests.yml` (mobile tests på PR)
- [x] Supabase Auth redirects til native (`madshopper://`, `madshopper://**`, `exp://127.0.0.1:8081/--/*`)
- [x] Google Cloud iOS- + Android-OAuth-klienter for `dk.madshopper.app` (web-klient urørt)
- [ ] Apple Developer (~99 USD/år) + Google Play (~25 USD engangs)
- [ ] `eas login` + `eas init` + EAS secrets (Supabase publishable, Google client) — se §9.1 + `docs/env-setup.md` §5a
- [ ] Screenshots, privacy nutrition labels, ATT
- [ ] Sæt `APPLE_TEAM_ID` + `ANDROID_CERT_SHA256` i Worker-vars → deploy
- [ ] Universal/App Links for `?liste=` (verificér efter Team ID)
- [ ] TestFlight / internal testing
- [ ] Production review / Submit for Review

#### 9.1 `eas init` — projectId (kræver menneske, interaktivt login)

`apps/mobile/app.config.js` har `extra.eas.projectId` sat fra env-variablen
`EAS_PROJECT_ID` (endnu **ikke** sat — der findes ikke noget EAS-projekt endnu).
Dette kræver login og kan ikke gøres af agenten. Sådan gør du:

```bash
npm install -g eas-cli   # hvis ikke installeret
cd apps/mobile
eas login                # interaktivt: Expo-konto (opret gratis på expo.dev hvis nødvendigt)
eas init                 # opretter EAS-projekt, skriver projectId
```

Hvad sker der bagefter:
- `eas init` opretter et projekt på expo.dev og tilføjer `extra.eas.projectId`
  automatisk (enten direkte i `app.json`/`app.config.js`, eller ved at du sætter
  `EAS_PROJECT_ID` i miljøet — her læses den allerede via
  `process.env.EAS_PROJECT_ID` i `app.config.js`, så nemmeste vej er at
  `eas init` opretter projektet og du derefter sætter `EAS_PROJECT_ID=<id>` i
  `apps/mobile/.env` og evt. som EAS-secret/CI-env).
- Ingen build/submit kan køres før dette er gjort (`eas build` fejler med
  "no project ID found").

Efter `eas init` er kørt, kan build/submit køres:

```bash
npm run eas:build:preview   # apps/mobile — profil "preview" (internal APK/IPA)
npm run eas:build:prod      # profil "production"
npm run eas:submit          # eas submit --profile production (kræver App Store Connect-app / Play-app)
```

---

## 15. Acceptance tests (must-pass)

1. Samme produktliste på home for samme `stores`/filtre som web
2. Alle 8 kategorier + undercats + pagination 60
3. Søgning: autocomplete 8 + panel results + full page
4. Overlay: 5 billigste, chart pr. butik, nutrition sources
5. Cart: add/qty/remove/clear; badge = sum qty
6. Multi-deal påvirker **kun** SCO totals, ikke footer/rute
7. SCO sort: coverage → price; alternatives flow
8. Butiksrute savings vs single store
9. Login merge qty; logout clears; cloud compact round-trip
10. Shared: create/join/leave/poll overwrite/invite deep link/max 6
11. Saved lists max 10 personal + group
12. Dark mode persists
13. Feedback POST works
14. Stubs viser stadig “kommer snart” (alerts/push/email)
15. Staging skriver til `*_dev`; prod ikke

---

## 16. Estimat

| Arbejde | Omfang |
|---|---|
| Backend JSON-API’er | 1–2 uger |
| Native app fuld paritet | 3–5+ måneder (1–2 erfarne devs) |
| Store review / polish | 2–4 uger |
| Årlige gebyrer | Apple ~700–800 kr/år + Google engangs ~25 USD |

Scraping / matching / `updater.py` røres **ikke** til denne rewrite.

---

## 17. Hvad der eksplicit ikke må “smarte væk”

- Erstatte SCO med “bare summer billigste”
- Gemme `storePrices` kun i cloud og stole på dem
- Skippe shared cart / lists / alternatives
- WebView til browse “midlertidigt”
- Kun Android eller kun iOS
- Droppe undercategories, multi-deal, nutrition eller price history
- Bruge `service_role` i appen
- Kalde stubs for “færdige features”

---

## 18. Når agenten skal bygge det

Arbejdsordre i rækkefølge:

1. Implementér **Fase 0** JSON-API’er i `app.py` (kontrakt-tests).
2. Opret Flutter/RN monorepo `apps/mobile`.
3. Port datamodeller + SCO til delt modul med unit tests mod fixtures fra web.
4. Byg faser 1→8 med parity checklist (§15).
5. Deep links + store listing (Fase 9).

**Startkommando til agent:** *“Byg native app efter `docs/native-app.md` — start med Fase 0.”*

---

## 19. Relaterede docs

| Doc | Emne |
|---|---|
| `docs/env-setup.md` | Env-filer + guide til manglende nøgler (Google/Supabase redirects) |
| `docs/Dev.md` | Dev/staging-workflow |
| `docs/Features.md` | Roadmap (post-paritet) |
| `docs/prisovervaagning.md` | Prisalarmer (API klar, UI stub) |
| `docs/email-bekraeftelse.md` | Email-bekræftelse / SMTP |
| `docs/ga4-events-tjek.md` | Zaraz/GA4 events |
| `CLAUDE.md` / `README.md` | Projekt-overblik, matching, sikkerhed |

---

*Sidst opdateret: 2026-07-26 — Fase 9 status synket med `docs/Features.md` (OAuth/redirects klar; konti/EAS stadig menneske).*
