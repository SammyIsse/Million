# Notes for Review — klar til at kopiere ind

Til feltet **App Review Information → Notes** i App Store Connect (og
"App access"-noten i Play Console). Skriv den på engelsk, som Apple forventer.

---

MadShopper is a Danish grocery price-comparison app. It reads public product
and price data from the developer's own backend (madshopper.dk) — the app does
not scrape retailer sites directly and is not affiliated with any of the
retail chains mentioned in the description.

**Sign-in is optional.** Every core feature — search, browsing offers, the
cart, "Find billigste" (cheapest-store calculation), the multi-store route and
price history — works fully without an account. An account only syncs the cart
between devices and stores a personal savings total. No demo account is
therefore required to review the app; you may create one with any email, or
use Sign in with Apple.

**Account deletion** (Guideline 5.1.1(v)) is in the app under
**Settings → Konto → "Slet konto"** (Danish for "Delete account"). It asks for
confirmation and then permanently deletes the account, the saved cart and the
savings history server-side, and signs the user out.

**Language:** the app ships in Danish only, as it compares prices in Danish
supermarkets for a Danish audience.

**Permissions:** the app requests no camera, contacts, location or photo
permissions, contains no advertising or analytics SDKs, and does not use the
advertising identifier.

Contact for any questions during review: kontakt@madshopper.dk

---

## Huskeliste til selve indsendelsen

- Sprog: dansk (da-DK) som primært sprog — ellers matcher screenshots og
  beskrivelse ikke det App Store viser.
- Aldersgrænse: 4+ / PEGI 3. Ingen brugergenereret offentligt indhold; delt
  kurv deles kun via et link brugeren selv sender.
- Pris: gratis, ingen køb i appen.
- Kategori: Food & Drink (primær), Shopping (sekundær).
- Copyright: dit navn, 2026.
- Sign in with Apple SKAL være med, når Google-login tilbydes — det er den
  allerede (`AuthScreen`), men tjek at knappen er synlig i den byggede app.
