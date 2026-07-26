# Store metadata — MadShopper

Skabelon/tekst-materiale til App Store Connect og Google Play Console.
Ingen konti oprettes eller udfyldes herfra — kun tekstindhold forberedt på forhånd.

## Kontakt & links

- **Support-email:** kontakt@madshopper.dk
- **Privatlivspolitik (URL):** https://madshopper.dk/privatliv
- **Vilkår (URL):** https://madshopper.dk/vilkaar.html
- **Marketing-URL (valgfri):** https://madshopper.dk

## Tekster

- Kort beskrivelse (Play Store, ≤80 tegn): [`da-DK/description-short.txt`](./da-DK/description-short.txt)
- Lang beskrivelse: [`da-DK/description-long.txt`](./da-DK/description-long.txt)
- Nøgleord (App Store, ≤100 tegn kommasepareret — tjek længde før upload): [`da-DK/keywords.txt`](./da-DK/keywords.txt)

## Kategori (forslag)

- **App Store:** Food & Drink / Shopping
- **Google Play:** Shopping

## Privacy nutrition labels / Data safety (skal udfyldes i konsollen, ikke her)

Kort oversigt til udfyldning (ikke selve formularen):

| Data | Indsamles | Formål | Delt |
|---|---|---|---|
| Email (kun ved login) | Ja, valgfrit | Konto/auth | Nej (Supabase Auth) |
| Indkøbskurv-indhold | Ja, valgfrit (kun ved login) | Sync på tværs af enheder | Nej |
| Anonym brugs-telemetri (cart-event) | Ja, anonymt, uden bruger-id | Popularitets-ranking | Nej |
| Sporing til annoncer | Nej | — | — |

## Screenshots — checklist (ingen fakes lavet endnu, kun krav noteret)

| Enhed | Påkrævet størrelse | Antal (min–max) | Status |
|---|---|---|---|
| iPhone 6.7" (fx iPhone 15/16 Pro Max) | 1290 x 2796 px | 3–10 (Apple kræver min. 3) | Mangler |
| iPhone 6.1" (fx iPhone 15/16) | 1179 x 2556 px | 3–10 | Mangler (kan ofte genbruges fra 6.7" hvis samme aspect ratio accepteres — ellers separate) |
| Android phone (Google Play) | min. 320px, maks. 3840px på korteste side; anbefalet 1080 x 1920 px eller højere | 2–8 | Mangler |
| iPad (valgfri, kun hvis `supportsTablet` skal markedsføres) | 2048 x 2732 px | 3–10 hvis inkluderet | Ikke planlagt (app er portrait-first) |

Anbefalet indhold pr. screenshot (parity med web-flowet i `docs/native-app.md` §5):
1. Forside med Ugens Tilbud
2. Produktdetalje med prissammenligning (5 billigste butikker)
3. Kurv med SCO ("Find billigste")
4. Butiksrute-resultat
5. Delt kurv / gemte lister

Screenshots skal tages på rigtige devices/simulatorer med rigtigt (ikke fake/mockup) UI-indhold —
lav dem først når appen kører på simulator/TestFlight, ikke som del af denne forberedelse.

## App-ikon / feature graphic

- Ikon: `apps/mobile/assets/icon.png` (allerede i repo — tjek Apple 1024x1024 / Google 512x512 krav før upload).
- Google Play feature graphic (1024 x 500 px): mangler — lav når butiksopsætning er i gang.

## Ikke gjort her (kræver menneske/konto)

- Faktiske screenshots
- Upload til App Store Connect / Play Console
- Udfyldelse af Data Safety-formular / App Privacy-spørgeskema
- Aldersvurdering / content rating questionnaire
- Prisfastsættelse (gratis) og markedstilgængelighed
