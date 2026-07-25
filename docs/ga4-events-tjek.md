# Tjek GA4-events på MadShopper

Step-by-step guide til at se, om de nye analyse-events virker.

**Hjemmeside:** [https://madshopper.dk](https://madshopper.dk)

**Events du skal se efter:**

| Event-navn | Hvornår det sendes |
|---|---|
| `search` | Når du søger efter en vare |
| `add_to_cart` | Når du lægger en vare i kurven |
| `compare_prices` | Når du klikker for at sammenligne kurvens priser |
| `category_click` | Når du klikker på en kategori i menuen |

Events sendes **kun**, hvis du har sagt ja til **Analyse**-cookies.

---

## Før du starter

### 1. Sørg for at den nye kode er live

Events ligger i `static/js/script.js` (cache-bust `?v=26`).

1. Åbn [https://madshopper.dk](https://madshopper.dk)
2. Højreklik → **Inspicer** (eller `Cmd + Option + I` på Mac)
3. Gå til fanen **Network** (Netværk)
4. Genindlæs siden (`Cmd + R`)
5. Filtrér på `script.js`
6. Klik på filen og tjek URL’en — den skal slutte med **`script.js?v=26`** (eller højere)

Hvis den stadig siger `?v=25` (eller lavere), er den nye kode **ikke deployet endnu**. Deploy først, ellers kan du ikke se events.

### 2. Hav Google Analytics åben

1. Gå til [https://analytics.google.com](https://analytics.google.com)
2. Vælg MadShopper-property’en
3. I venstremenuen: **Rapporter** → **Realtime** (på dansk: **Realtid**)

Lad Realtime-vinduet stå åbent i en fane, mens du tester på madshopper.dk i en anden.

---

## Del A — Samtykke til Analyse (vigtigt)

Uden Analyse-samtykke sendes **ingen** af de fire events.

### Sådan gør du

1. Åbn [https://madshopper.dk](https://madshopper.dk) i et **inkognito-/privatvindue**  
   (så du får cookie-banneret friskt, uden gammelt samtykke)
2. Når cookie-banneret vises:
   - Enten klik **Acceptér alle**
   - Eller sæt kryds i **Analyse** og klik **Bekræft mine valg**
3. Hvis banneret ikke vises (fordi du allerede har valgt før):
   1. Scroll til bunden af siden
   2. Klik **Cookie-indstillinger**
   3. Slå **Analyse** til
   4. Klik **Bekræft mine valg**

### Hurtig kontrol af samtykke

1. På [https://madshopper.dk](https://madshopper.dk): åbn DevTools → fanen **Console**
2. Skriv dette og tryk Enter:

```js
zaraz.consent.getAll()
```

3. Du skal se noget i stil med:

```js
{ NpgO: true, icuR: true }
```

- `NpgO: true` = **Analyse** er slået til (nødvendigt for GA-events)
- `icuR: true` = **Funktionel** er slået til (butiksvalg m.m. — ikke nødvendigt for de fire events)

Hvis `NpgO` er `false`, går du tilbage til cookie-indstillingerne og siger ja til Analyse.

---

## Del B — Tjek Zaraz (én gang)

Dette sikrer, at Cloudflare faktisk sender `zaraz.track()` videre til Google Analytics.

1. Log ind på [https://dash.cloudflare.com](https://dash.cloudflare.com)
2. Vælg zonen for **madshopper.dk**
3. Gå til **Tag setup** / **Zaraz** (navnet kan hedde “Tag setup” i nyere dashboard)
4. Åbn værktøjet **Google Analytics 4**
5. Under actions / automatic actions skal **Events** være slået til  
   (det er den indstilling, der fanger alle `zaraz.track(...)`-kald)

Hvis **Events** ikke er slået til: slå den til og gem.  
Du behøver normalt **ikke** lave en separat trigger pr. event-navn, når Automatic Events er aktiv.

---

## Del C — Test på hjemmesiden

Hold **GA4 Realtime** åben i den ene fane.  
Test i den anden fane på [https://madshopper.dk](https://madshopper.dk).

I Realtime skal du kigge efter kortet **Event count by Event name** / **Antal hændelser efter hændelsesnavn**.

### Test 1 — `search`

1. Gå til forsiden: [https://madshopper.dk](https://madshopper.dk)
2. Klik i søgefeltet øverst
3. Skriv fx `mælk` og vent til resultaterne viser sig (ca. ½ sekund)
4. Skift til GA4 Realtime
5. Du skal inden for ca. 10–30 sekunder se eventet **`search`**

**Ekstra (valgfrit) i Console på madshopper.dk:**

```js
zaraz.track
```

Skal returnere en funktion (bekræfter at Zaraz er loadet).

### Test 2 — `add_to_cart`

1. Bliv på [https://madshopper.dk](https://madshopper.dk) (eller i søgeresultater)
2. Klik på den grønne kurv-knap på et produktkort
3. Knappen skifter kortvarigt til “Tilføjet”
4. Tjek GA4 Realtime for **`add_to_cart`**

Du kan også åbne et produkt (klik på kortet) og bruge **Tilføj til kurv** i overlay’et — det tæller også som `add_to_cart`.

### Test 3 — `compare_prices`

1. Sørg for at der ligger mindst **1 vare** i kurven
2. Åbn kurven (kurv-ikonet øverst til højre)
3. Klik knappen til at sammenligne priser / **Vis henvisning** (teksten på knappen)
4. Sammenlignings-overlayet åbner
5. Tjek GA4 Realtime for **`compare_prices`**

### Test 4 — `category_click`

1. Fra forsiden [https://madshopper.dk](https://madshopper.dk)
2. Klik fx **Køl**, **Kolonial** eller **Ugens Tilbud** i kategori-menuen
3. Siden skifter til den kategori
4. Tjek GA4 Realtime for **`category_click`**

Tip: klik gerne kategorien **lige før** du kigger i Realtime, så eventet er friskt.

---

## Del D — Sådan ser “succes” ud i GA4

I **Realtime** bør du kunne se noget i stil med:

- `page_view` (kommer allerede i forvejen)
- `search`
- `add_to_cart`
- `compare_prices`
- `category_click`

Klik på et event-navn for at se detaljer. Parametre kan bl.a. være:

| Event | Parametre |
|---|---|
| `search` | `search_term`, `result_count` |
| `add_to_cart` | `product_id`, `category`, `store`, `quantity` |
| `compare_prices` | `item_count`, `total_qty` |
| `category_click` | `category`, `path` |

> Parametre vises ikke altid med det samme i Realtime-rapporter. Det vigtigste første tjek er, at **event-navnene** dukker op.

---

## Del E — Hvis noget mangler

### Jeg ser ingen af de nye events

1. Tjek at `script.js?v=26` er live (Del “Før du starter”)
2. Tjek at `zaraz.consent.getAll()` har `NpgO: true`
3. Tjek at Zaraz → GA4 har **Events** slået til
4. Prøv inkognito igen (gammelt samtykke / cache kan forvirre)
5. Vent 30–60 sekunder — Realtime er hurtig, men ikke altid øjeblikkelig

### Jeg ser `page_view`, men ikke de nye events

Så virker GA generelt, men custom events når ikke frem. Mest sandsynligt:

- **Events**-action mangler i Zaraz for GA4, **eller**
- Du tester på en build **uden** den nye `script.js`

### Jeg har sagt nej til Analyse

Så er det **forventet**, at events ikke sendes. Det er meningen (GDPR).

### Console-fejl?

I DevTools → Console: hvis der står noget om `zaraz is not defined`, er Zaraz ikke loadet (adblocker, script-blokering, eller midlertidigt Cloudflare-problem). Prøv uden adblocker.

---

## Del F — (Valgfrit) Se parametrene permanent i GA4

Realtime er nok til at tjekke, at det virker. Vil du bruge parametrene i almindelige rapporter senere:

1. GA4 → **Admin** (tandhjul)
2. Under property: **Custom definitions** / **Brugerdefinerede definitioner**
3. **Create custom dimension** for dem, du vil bruge, fx:
   - `search_term`
   - `result_count`
   - `product_id`
   - `category`
   - `store`
   - `item_count`
4. Scope: **Event**
5. Event parameter: samme navn som ovenfor

Det er valgfrit og påvirker ikke, om events sendes.

---

## Kort tjekliste

- [ ] [https://madshopper.dk](https://madshopper.dk) kører `script.js?v=26` (eller nyere)
- [ ] Analyse-cookies er accepteret (`NpgO: true`)
- [ ] Zaraz → GA4 har **Events** slået til
- [ ] GA4 Realtime er åben
- [ ] Testet `search` på forsiden
- [ ] Testet `add_to_cart` på et produkt
- [ ] Testet `compare_prices` fra kurven
- [ ] Testet `category_click` i kategori-menuen
- [ ] Alle fire event-navne ses i Realtime

Når alle fire er set i Realtime, er opsætningen i orden.
