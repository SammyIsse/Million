# App Privacy / Data Safety — færdige svar

Udfyldes i konsollen (kan ikke uploades). Svarene her er **udledt af koden**,
ikke gættet — kilderne står i parentes, så du kan efterprøve dem, hvis Apple
eller Google spørger.

**Tracking: NEJ i begge butikker.** Appen har ingen reklame- eller
analyse-SDK'er, bruger ikke IDFA/AAID og deler intet med datamæglere. Derfor
skal App Tracking Transparency-prompten heller ikke vises.

---

## Apple — App Privacy

| Datatype | Indsamles | Knyttet til bruger | Formål | Kilde i koden |
|---|---|---|---|---|
| Contact Info → Email Address | Ja, kun ved konto | Ja | App Functionality | `AuthContext` (Supabase Auth) |
| Contact Info → Name | Ja, valgfrit visningsnavn | Ja | App Functionality | `set_my_display_name` |
| User Content → Other User Content (indkøbskurv) | Ja, kun når logget ind | Ja | App Functionality | `carts`-tabel, `AuthContext` |
| Usage Data → Product Interaction | Ja, anonymt | **Nej** | Analytics | `postCartEvent` → `/api/cart-event` |
| User Content → Customer Support (feedback) | Kun hvis du selv skriver | Ja, hvis du angiver navn/mail | Customer Support | `FeedbackScreen` → `/api/feedback` |
| Purchases, Location, Contacts, Health, Browsing History, Identifiers, Diagnostics | **Nej** | — | — | ingen tilsvarende kald |

**"Personal savings"-totalen** (`record_compare_savings`) er afledt af kurven
og hører under samme User Content-punkt — den er knyttet til brugeren og
bruges kun til at vise din egen besparelse.

**Om sikkerhedslogningen på edge:** `src/worker.py` aggregerer
sikkerhedshændelser i hukommelsen og skyller højst 1×/minut. Det er
aggregerede tal uden bruger- eller enheds-id, så det er ikke "collected" i
Apples forstand (data der kan henføres til en bestemt bruger). Vurder det
selv, hvis Apple spørger — men det er ikke et punkt der skal krydses af.

---

## Google Play — Data safety

| Sektion | Svar |
|---|---|
| Indsamler eller deler din app brugerdata? | Ja |
| Er data krypteret under overførsel? | Ja (HTTPS/TLS overalt, ingen cleartext) |
| Kan brugere bede om at få data slettet? | Ja — **både** i appen (Indstillinger → Konto → Slet konto) og via kontakt@madshopper.dk |
| Deles data med tredjeparter? | Nej |
| Er data-indsamlingen valgfri? | Ja — alt kernefunktionalitet virker uden konto |

Datatyper at krydse af:

- **Personal info → Email address** — Collected, Linked, *App functionality* + *Account management*. Optional.
- **Personal info → Name** — Collected, Linked, *App functionality*. Optional.
- **App activity → Other user-generated content** (kurv/lister) — Collected, Linked, *App functionality*. Optional.
- **App activity → Other actions** (anonyme kurv-hændelser) — Collected, **Not linked**, *Analytics*.
- **Messages → Other in-app messages** (feedback) — Collected, Linked hvis du selv skriver navn/mail, *Customer support*. Optional.

Ikke afkrydset: Location, Financial info, Health, Contacts, Photos, Files,
Calendar, Device or other IDs, Installed apps, Web browsing.

**Data deletion URL** (Play kræver et link, når konti kan oprettes):
`https://madshopper.dk/privatliv` — siden beskriver både in-app-sletningen og
mail-vejen. Se afsnittet "MadShopper-appen (iOS og Android)".

---

## Aldersvurdering / content rating

- Ingen vold, gambling, brugerdelt offentligt indhold eller køb i appen.
- Delt kurv deles kun via et link brugeren selv sender videre.
- Forventet: **4+** (App Store) / **PEGI 3, Everyone** (Play).
