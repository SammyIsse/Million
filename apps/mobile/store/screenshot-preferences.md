# App Store-skærmbilleder — præferencer og pipeline

Noter til at fortsætte arbejdet med markedsførings-skærmbilleder til App Store/Play,
uden at skulle genopdage de samme løsninger igen.

## Stil-præferencer (bekræftet med brugeren)

- **Baggrund:** mint/emerald (`#D6F5E3`), matcher appens `--green-light`-token.
- **Overskrift:** fed, mørkegrøn (`#047857`), Plus Jakarta Sans. **Ingen undertekst**,
  og **nævn ikke butiksnavne** i teksten (kun generiske fordele, fx "Sammenlign priser
  på tværs af fødevarebutikker").
- **Telefon:** skal være en **rigtig fotorealistisk mockup** (ikke en selvtegnet
  CSS/HTML-telefon) — brugeren afviste flere CSS-baserede forsøg (fladt kort, så en
  version med kunstig metalkant) før vi endte her.
- **Enhed:** iPhone 16 (Dynamic Island), **ikke** iPhone 13. Grunden: appens
  simulator-skærmbilleder har Dynamic Island indbygget i selve skærmbilledet
  (fra iPhone 15 Pro Max-simulatoren) — en iPhone 13-mockup har en klassisk notch,
  og de to former kolliderede visuelt i hjørnet.
- **To vinkler pr. skærm:** `-tilted` (¾-vinkel, dynamisk) og `-straight` (lige forfra,
  roligere). Brugeren vil gerne kunne vælge mellem dem.
- **Alt app-indhold skal være fuldt synligt** — aldrig beskåret. Hvis skærmbilledets
  billedformat ikke matcher telefonens skærmåbning nøjagtigt, skal indholdet
  **skaleres ned og passes ind** (letterbox med appens egen baggrundsfarve), ikke
  beskæres i siderne. Dette var den sidste store rettelse (se "Faldgruber" nedenfor).
- Indholdet skal have en lille luft-margin (~3,5%) til telefonens bezel, så tekst
  ikke rører skærmkanten.

## Kilde til mockup

Gratis PSD-mockup fra **mockups-design.com**, "Free iPhone 16 mockup"
(`https://mockups-design.com/free-iphone-16-mockup/`). Licens: "For private &
comercial purpose", **ingen kreditering krævet**. 5 vinkler inkluderet i zip'en;
vi bruger:
- `Free_iPhone_16_Mockup_2.psd` → "tilted" (ægte 3D-perspektiv-quad)
- `Free_iPhone_16_Mockup_5.psd` → "straight" (ingen forvrængning, ligeud)
- (Angle 1, 3, 4 er enten duplikater eller viser to telefoner front+bag - ikke brugt.)

PSD-filerne (~30-40 MB hver) er **ikke** committet til repoet - de downloades til en
midlertidig mappe når vi arbejder på det (se "Sådan fortsætter du" nedenfor).

## Filer gemt i repoet (så vi ikke skal genopbygge fra bunden)

- `apps/mobile/store/screenshot-tools/build_real_mockup2.py` - selve pipeline'en:
  åbner PSD'en via `psd-tools`, finder "Design"-smart-object-laget og dets
  perspektiv-transformation (`PLACED_LAYER2`-tag), varper vores skærmbillede ind
  med et homography (`find_coeffs`), maskerer med telefonens **rigtige** skærmform
  (udtrukket fra mockup'ens egen tomme-skærm-render via invers warp — ikke en gættet
  rundet firkant), og komponerer med "Highlights"-laget (glasrefleksion, screen-blend).
- `apps/mobile/store/screenshot-tools/compose.py` - lægger mint-baggrund, overskrift
  og en blød skygge under den færdige telefon-mockup.
- `apps/mobile/store/screenshot-tools/fonts/PlusJakartaSans-Variable.ttf` - variable
  font brugt til overskriften (Google Fonts, OFL-licens).

## Sådan fortsætter du

Python-pakker (skal installeres i `.venv` hvis de mangler):
```
pip install psd-tools scipy scikit-image numpy pillow
```

1. Download mockup-PSD'en igen (den store zip-fil holdes ikke i repoet):
   - Gå til `https://mockups-design.com/free-iphone-16-mockup/`, find download-linket
     (kræver at følge en "download-in-progress"-mellemside med et nonce - brug
     browserens netværksfane eller `curl -L` med en normal User-Agent for at følge
     redirects).
   - Pak `Free_iPhone_16_Mockup_2.psd` og `Free_iPhone_16_Mockup_5.psd` ud et sted
     midlertidigt.
2. For hvert skærmbillede i `apps/mobile/store/screenshots/iphone-6.7/*.png`:
   ```
   python3 build_real_mockup2.py <path-to>/Free_iPhone_16_Mockup_2.psd <skærmbillede.png> phone-tilted.png
   python3 build_real_mockup2.py <path-to>/Free_iPhone_16_Mockup_5.psd <skærmbillede.png> phone-straight.png
   python3 compose.py "<Overskrift-tekst>" "" phone-tilted.png <navn>-marketing-tilted.png
   python3 compose.py "<Overskrift-tekst>" "" phone-straight.png <navn>-marketing-straight.png
   ```

## Faldgruber vi allerede har løst (spar tiden næste gang)

- **Hjørnerne stak ud over bezel'en:** en gættet `rounded_rectangle`-maske matcher
  ikke Apples "squircle"-kurve præcist nok. Løsning: udtræk den **rigtige** skærmform
  fra mockup'ens egen (tomme) render og varp den tilbage til kilde-rummet med den
  inverse homography - garanterer pixel-præcist match uanset vinkel.
- **Appens indhold rørte bezel-kanten:** tilføjet en ~3,5% indvendig margin, udfyldt
  med skærmbilledets egen baggrundsfarve (samplet fra et hjørnepixel).
- **"MadShopper"-teksten blev skåret af i venstre side:** det oprindelige
  `cover_resize` (beskær-for-at-fylde) beskar siderne når skærmbilledets
  billedformat ikke matchede mockup'ens skærmåbning præcist - especially slemt for
  forsiden, fordi vi først havde beskåret 300px af bunden for at fjerne
  dev-advarselsbjælken, hvilket ændrede billedformatet markant. Løsning: skiftet til
  `contain_resize` (skalér ned og pas ind, aldrig beskær) + letterbox i appens egen
  baggrundsfarve.
- **PSD'en har ingen egen alpha-kanal:** `psd.composite(force=True)` fylder tomme
  områder med opak sort (`~(9,9,9)`) i stedet for transparent. Løses med en
  chroma-key (`chroma_key_black`) + `binary_opening` for at fjerne støj-specks fra
  mockup'ens "Noise"-lag langs silhuetten.
- **"Delete this layer"-hjælpelaget** i PSD'en skal skjules eksplicit, ellers dækker
  det hele billedet (designeren har efterladt det som en note-til-sig-selv om at
  slette det for at spare diskplads).
- **Dev-bjælken "Open debugger to view warnings"** dukker stadig lejlighedsvis op i
  simulator-skærmbilleder (kendt, uundgåeligt problem i det usignerede dev-client-build
  - se hoved-CLAUDE.md's afsnit om appen). Hvis et kildeskærmbillede har den, beskær
  den væk **før** brug i mockup-pipeline'en, men vælg om muligt et skærmbillede der
  slet ikke har den (undgår at skulle beskære og dermed ændre billedformat).

## Status pr. 2026-07-31

Alle 5 skærme har `-marketing-tilted.png` og `-marketing-straight.png` i
`apps/mobile/store/screenshots/iphone-6.7/`: forside, produktdetalje, kurv-sco,
butiksrute (genbruger SCO-sammenligningsskærmen, se `metadata.md`), delt-kurv.
De ældre CSS-baserede forsøg (`-marketing-a/b/c.png`) ligger stadig ved siden af på
brugerens ønske, men er ikke anbefalet til brug.

Mangler/muligt næste skridt: overskriftstekst for de resterende 4 skærme er kun
groft formuleret undervejs i samtalen - kan finpudses. Butiksrute-skærmen viser
teknisk set SCO-sammenligningen, ikke en rigtig "butiksrute"-skærm, fordi
menupunktet blev fjernet fra appen (commit `a147975`, 2026-07-27) - RouteScreen.tsx
er nu ureachable/dødt kode i appen, værd at rydde op i eller genintroducere en vej dertil.
