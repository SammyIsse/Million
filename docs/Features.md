Deling af indkøbskurv (gruppe) – Live delt kurv + fælles gemte lister. Kræver konto, gruppenavn og visningsnavn. Max 6 personer (race-sikret i SQL) og max 10 gemte lister. Én gruppe pr. bruger. Alle er lige. Se `scripts/supabase-shared-carts.sql`.

Gem som liste – Max 10 privat/gruppe. Ved join: øverste private merges først; nederste uden plads slettes. Er gruppen fuld, slettes alle private.

Prisovervågning – Klar til udrulning når notifikationer findes (UI + API + auth findes). Se docs/prisovervaagning.md

Føtex komplet produktkatalog (Algolia prod_FOETEX_PRODUCTS + Salling API priser) – 14.459 produkter med EAN (priser mangler FOETEX_SALLING_STORE i secrets)

Mit køleskab side - ud fra hvad man har i køleskabet, kom med opskrifter

Fra tilbud - kom mig forslag til aftensmad ud fra tilbudsvarerne.

Man skal kunne gemme en opskrift, så den dukker op under "Mine opskrifter" under "Favorit opskrifter"

Butikker opdateringer
- Lidl har flere varer, deres app er bare nede lige nu, så kan ikke tjekke det (5/7-26)
    Det er ulovlig at tage flere varer, uden aftale med dem...
