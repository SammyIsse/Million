# Store metadata — MadShopper

Skabelon/tekst-materiale til App Store Connect og Google Play Console.
Ingen konti oprettes eller udfyldes herfra — kun tekstindhold forberedt på forhånd.

## Kontakt & links

- **Support-URL (påkrævet felt i App Store Connect):** https://madshopper.dk/feedback
- **Support-email:** kontakt@madshopper.dk
- **Privatlivspolitik (URL):** https://madshopper.dk/privatliv
- **Vilkår (URL):** https://madshopper.dk/vilkaar.html
- **Marketing-URL (valgfri):** https://madshopper.dk

## Tekster

- Kort beskrivelse (Play Store, ≤80 tegn): [`da-DK/description-short.txt`](./da-DK/description-short.txt)
- Lang beskrivelse: [`da-DK/description-long.txt`](./da-DK/description-long.txt)
- Nøgleord (App Store, ≤100 tegn kommasepareret): [`da-DK/keywords.txt`](./da-DK/keywords.txt) — 98 tegn. **Bevidst uden butiksnavne** (Rema 1000, Bilka, …): andres varemærker i keywords-feltet er en klassisk 5.2-afvisning. De må gerne stå i den lange beskrivelse, hvor de er faktuel omtale af hvad appen sammenligner.

## Kategori (forslag)

- **App Store:** Food & Drink / Shopping
- **Google Play:** Shopping

## Privacy nutrition labels / Data safety

Færdige, kode-verificerede svar til begge konsoller ligger i
[`privacy-answers.md`](./privacy-answers.md) — inkl. hvilke felter der
**ikke** skal krydses af. Notes for Review-teksten ligger i
[`review-notes.md`](./review-notes.md).

## Screenshots — checklist (ingen fakes lavet endnu, kun krav noteret)

| Enhed | Påkrævet størrelse | Antal (min–max) | Status |
|---|---|---|---|
| iPhone 6.7" (fx iPhone 15/16 Pro Max) | 1290 x 2796 px | 3–10 (Apple kræver min. 3) | **Klar** — 5 stk. i `screenshots/iphone-6.7/`, verificeret 1290x2796 |
| iPhone 6.1" (fx iPhone 15/16) | 1179 x 2556 px | 3–10 | Ikke nødvendig — App Store skalerer fra det største iPhone-format |
| Android phone (Google Play) | min. 320px, maks. 3840px på korteste side; anbefalet 1080 x 1920 px eller højere | 2–8 | Mangler — tages på Windows-maskinen med Android-emulator (intet Android SDK på Mac'en) |
| iPad | 2048 x 2732 px | — | Ikke relevant: `supportsTablet: false` i `app.config.js` |

Anbefalet indhold pr. screenshot (parity med web-flowet i `docs/native-app.md` §5):
1. Forside med Ugens Tilbud
2. Produktdetalje med prissammenligning (5 billigste butikker)
3. Kurv med SCO ("Find billigste")
4. Butiksrute-resultat
5. Delt kurv / gemte lister

Screenshots skal tages på rigtige devices/simulatorer med rigtigt (ikke fake/mockup) UI-indhold —
lav dem først når appen kører på simulator/TestFlight, ikke som del af denne forberedelse.

## App-ikon / feature graphic

Alle genereres fra `static/favicon.svg`, så web og app deler præcis samme
glyf. Kør `python3 scripts/build-icons.py` (macOS) hvis logoet ændres.

- Ikon: `apps/mobile/assets/icon.png` (1024x1024, fullbleed — iOS runder selv).
- Play-butiksikon: `store/graphics/play-icon-512.png` (512x512).
- Play feature graphic: `store/graphics/feature-graphic-1024x500.png` — genereres
  med `node scripts/build-play-graphics.mjs`.
- Android adaptive lag: `android-icon-foreground/background/monochrome.png`.

## Konto-sletning (App Store 5.1.1(v))

Krævet fordi appen har kontooprettelse. Implementeret i appen:
Indstillinger → Konto → "Slet konto" → bekræftelsesdialog → `delete_own_account`-RPC.
Nævn stien i "Notes for Review" i App Store Connect, så reviewer ikke leder.

## Ikke gjort her (kræver menneske/konto)

- Android-screenshots + Play feature graphic
- Upload til App Store Connect / Play Console
- Udfyldelse af Data Safety-formular / App Privacy-spørgeskema
- Aldersvurdering / content rating questionnaire
- Prisfastsættelse (gratis) og markedstilgængelighed
