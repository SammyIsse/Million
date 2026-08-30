#!/usr/bin/env python3
"""Regressionstest for matchmotorens gates.

Kør: python3 scripts/test-matching.py

BAGGRUNDEN, kort: matchmotoren i updater.py afgør om to varer fra hver sin
butik vises som ét kort. Et forkert match viser kunden en forkert pris. Motoren
har været gennem flere revisioner, og hver revision har efterladt sin
begrundelse som en kommentar - men ingen test. En gate, der blev tilføjet for
at fange et konkret fejlmatch, kan derfor stille falde ud igen ved næste
omskrivning, uden at nogen opdager det før en kunde gør.

Denne test låser de par, koden selv dokumenterer som afgørende, plus dem der
blev fundet ved matchmotor-analysen 25-08-2026.

FORVENTEDE FEJL (kendte huller, ikke regressioner) markeres med
``expect_fail=True``. De skal fejle nu og bestå senere - og når hullet lukkes,
fejler testen med "består uventet", så markeringen bliver fjernet i stedet for
at blive glemt.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_support import (  # noqa: E402
    ean_looks_valid, fuzzy_score, normalize_name, parse_weight_to_grams,
)
import updater  # noqa: E402
from updater import (  # noqa: E402
    _merge_cards_sharing_ean, annotate_match_signals,
    backfill_attributes_by_ean, brands_conflict, build_token_idf,
    cross_store_pair_verdict, cross_store_tokens, distinctive_token_shared,
    photo_distance, stk_validates_pack_size,
)

fails: list[str] = []
unexpected_passes: list[str] = []


def check(label: str, ok: bool, expect_fail: bool = False) -> None:
    if expect_fail:
        if ok:
            print(f"  UVENTET OK  {label}  <- hullet er lukket, fjern expect_fail")
            unexpected_passes.append(label)
        else:
            print(f"  kendt hul   {label}")
        return
    print(("  OK    " if ok else "  FEJL  ") + label)
    if not ok:
        fails.append(label)


def product(name, brand='', weight='', kategori='Kolonial', price=20.0, hash_int=None,
            kg_price=None, ean=''):
    p = {'name': name, 'brand': brand, 'weight': weight, 'Kategori': kategori,
         'price': price, 'kg_price': kg_price, 'ean': ean, '_hash_int': hash_int}
    if annotate_match_signals(p) is None:
        raise AssertionError(f"{name!r} blev klassificeret som ikke-mad")
    p['_cross_match_tokens'] = cross_store_tokens(p['_norm_name'])
    return p


def matches(a: dict, b: dict) -> bool:
    """Accepterer gate-kæden parret i mindst én retning?

    Begge retninger prøves, fordi fase 2 itererer over alle butikker som base -
    hvilken vare der er "base" afhænger af butiksrækkefølgen.
    """
    ok_ab, _, _ = cross_store_pair_verdict(a, b, a['_norm_name'], a['_cross_match_tokens'])
    ok_ba, _, _ = cross_store_pair_verdict(b, a, b['_norm_name'], b['_cross_match_tokens'])
    return ok_ab or ok_ba


def must_not_match(label, a, b, expect_fail=False):
    check(f"AFVIS  {label}", not matches(a, b), expect_fail)


def must_match(label, a, b, expect_fail=False):
    check(f"ACCEPT {label}", matches(a, b), expect_fail)


def test_ean_validering() -> None:
    print("\nEAN-validering (ean_looks_valid)")
    # Ægte stregkoder fra produktionscachen.
    for ean in ('5701977002961', '80177173', '5711953134616', '5704000437495'):
        check(f"{ean} er en gyldig GTIN", ean_looks_valid(ean))
    # Coop-avisens interne data-id, som updater.py læste som EAN.
    for ean in ('1066581', '1051122'):
        check(f"{ean} (Coop data-id) afvises", not ean_looks_valid(ean))
    check("forkert kontrolciffer afvises", not ean_looks_valid('5701977002960'))
    check("tom/nan/None afvises",
          not any(ean_looks_valid(x) for x in ('', 'nan', 'None', None)))
    check("ikke-cifre afvises", not ean_looks_valid('571197700296x'))


def test_procent_gate() -> None:
    print("\nProcent-gate (fedt-/alkohol-/kakao-%)")
    # Dokumenteret i README: alkoholfri og almindelig deler emballage, så
    # billedet er netop ikke bevis - kun tallet skiller dem.
    must_not_match("Tuborg 4,6% vs 0,0%",
                   product('Tuborg Classic 4,6%', 'Tuborg', '33 cl'),
                   product('Tuborg Classic 0,0%', 'Tuborg', '33 cl'))
    must_not_match("Piskefløde 38% vs 36%",
                   product('Piskefløde 38%', 'Arla', '250 ml', 'Køl'),
                   product('Piskefløde 36%', 'Arla', '250 ml', 'Køl'))
    # Ost angiver fedt som "45+"/"60+" - aldrig med procenttegn.
    must_not_match("Gouda 45+ vs 60+",
                   product('Gouda 45+', 'Arla', '400 g', 'Køl'),
                   product('Gouda 60+', 'Arla', '400 g', 'Køl'))
    # En side der blot udelader tallet er ikke en modsigelse.
    must_match("Piskefløde 38% vs Piskefløde (tavs)",
               product('Piskefløde 38%', 'Arla', '250 ml', 'Køl'),
               product('Piskefløde', 'Arla', '250 ml', 'Køl'))


def test_koed_gate() -> None:
    print("\nKødtype-gate")
    # Hakket kød deler vægt, fedtprocent og næsten hele navnet på tværs af
    # kødtyper - ingen anden gate kan skelne dem.
    must_not_match("hakket okse vs kylling",
                   product('Hk. oksekød 4-7%', 'Rema 1000', '400 g', 'Kød & Fisk'),
                   product('Hakket kyllingekød 4-7%', 'Salling', '400 g', 'Kød & Fisk'))
    must_not_match("hakket okse vs okse/kylling-blanding",
                   product('Hakket oksekød 8-12%', 'Levevis', '400 g', 'Kød & Fisk'),
                   product('Hakket okse- og kyllingekød 8-12%', 'Salling', '400 g', 'Kød & Fisk'))
    # Tavshed på én side er ikke en modsigelse.
    must_match("Frikadeller vs Frikadeller m. svinekød",
               product('Frikadeller', 'Salling', '400 g', 'Kød & Fisk'),
               product('Frikadeller m. svinekød', 'Levevis', '400 g', 'Kød & Fisk'))


def test_smag_og_variant() -> None:
    print("\nSmag-, form- og variant-gates")
    must_not_match("Pepsi Max vs Pepsi Max Lime",
                   product('Pepsi Max', 'Pepsi', '50 cl', 'Drikkevarer'),
                   product('Pepsi Max Lime', 'Pepsi', '50 cl', 'Drikkevarer'))
    must_not_match("Hindbær- vs solbærmarmelade",
                   product('Hindbærmarmelade øko', 'Den Gamle Fabrik', '270 g'),
                   product('Solbærmarmelade øko', 'Den Gamle Fabrik', '270 g'))
    must_not_match("Energidrik vs Energidrik sukkerfri",
                   product('Energidrik', 'Salling', '250 ml', 'Drikkevarer'),
                   product('Energidrik sukkerfri', 'Salling', '250 ml', 'Drikkevarer'))
    # Form: en proteindrik er ikke en proteinbudding, uanset fælles ord.
    must_not_match("Protein drik vs protein budding",
                   product('Arla Protein Drik Chokolade', 'Arla', '250 ml', 'Køl'),
                   product('Arla Protein Budding Chokolade', 'Arla', '250 ml', 'Køl'))


def test_vaegt_og_antal() -> None:
    print("\nVægt- og antals-gates")
    must_not_match("350 g vs 500 g",
                   product('Fødselsdagsboller', 'Belbake', '350 g', 'Brød & Kager'),
                   product('Fødselsdagsboller', 'Amo', '500 g', 'Brød & Kager'))
    # Multipak parses som totalvægt, så en enkeltdåse aldrig matcher en 6-pak.
    must_not_match("enkeltdåse vs 6-pak",
                   product('Cola', 'Coca-Cola', '0.33 l', 'Drikkevarer', price=8.0),
                   product('Cola', 'Coca-Cola', '6 x 0.33 l', 'Drikkevarer', price=45.0))
    # Småvarer: den absolutte bund skaleres ned, så dobbelt størrelse afvises.
    must_not_match("20 g vs 40 g pastiller",
                   product('Pastiller pebermynte', 'Läkerol', '20 g', 'Slik'),
                   product('Pastiller pebermynte', 'Läkerol', '40 g', 'Slik'))
    # Afrundinger må ikke skille ellers identiske varer.
    must_match("500 g vs 510 g",
               product('Rugbrød', 'Schulstad', '500 g', 'Brød & Kager'),
               product('Rugbrød', 'Schulstad', '510 g', 'Brød & Kager'))


def test_navnescore() -> None:
    print("\nNavnescore (fuzzy_score)")
    # Dokumenteret i app.py: dette par scorer 0,42 - over erstatningsvare-
    # bunden på 0,35. Låser at det ikke stiger yderligere.
    score = fuzzy_score(normalize_name('English Earl Grey'), normalize_name('Grillpølser 450g'))
    check(f"'English Earl Grey' vs 'Grillpølser 450g' forbliver lavt ({score:.2f} < 0.50)",
          score < 0.50)
    # Rema-forkortelser skal kunne møde fulde butiksnavne.
    score = fuzzy_score(normalize_name('HK. OKSEKØD 4-7%'), normalize_name('Hakket oksekød 4-7% fedt'))
    check(f"'HK. OKSEKØD 4-7%' møder 'Hakket oksekød 4-7% fedt' ({score:.2f} >= 0.65)",
          score >= 0.65)


def test_normalisering() -> None:
    print("\nNavnenormalisering")
    # _OKOLOGISK_RE ledte kun efter 'okologisk' (med o), men normalize_name
    # bevarer ø, så strippen fyrede aldrig på den fulde form: "Øko Mælk" blev
    # "mælk", mens "Økologisk Mælk" forblev "økologisk mælk" - score 0,44 på
    # samme vare. Øko er et variant-flag, ikke en del af navnet.
    check("'Øko' og 'Økologisk' normaliseres ens",
          normalize_name('Øko Mælk') == normalize_name('Økologisk Mælk'))
    check("'Økologi' rammer samme stamme",
          normalize_name('Økologi Havregryn') == normalize_name('Øko Havregryn'))
    # Må ikke over-matche: stammen kræver hele 'økologi'.
    check("'Økonomipakke' røres ikke", 'økonomipakke' in normalize_name('Økonomipakke'))
    check("'Okonomiyaki' røres ikke", 'okonomiyaki' in normalize_name('Okonomiyaki'))
    # Rema-forkortelser skal udvides, så terse feeds møder fulde navne.
    check("'HK.' udvides til 'hakket'", normalize_name('HK. OKSEKØD') == 'hakket oksekød')
    check("apostrof fjernes ('Lay's' -> 'lays')", normalize_name("Lay's") == 'lays')


def test_stk_validering() -> None:
    print("\nStk-antal som validering af pakkestørrelse")
    # Selve stk-GATEN er uændret: forskelligt antal afviser stadig.
    check("1 stk mod 6 stk afvises stadig af gaten",
          not matches(product('Cola', 'Coca-Cola', '1 stk', 'Drikkevarer', price=8.0),
                      product('Cola', 'Coca-Cola', '6 stk', 'Drikkevarer', price=40.0)))
    # Men "1 stk" mod "1 stk" må ikke tælle som bekræftet pakkestørrelse og
    # dermed sænke navnegulvet for et vægtløst par fra 0,75 til 0,65.
    check("1 stk mod 1 stk validerer ikke pakkestørrelsen",
          not stk_validates_pack_size(1, 1))
    check("6 stk mod 6 stk validerer pakkestørrelsen",
          stk_validates_pack_size(6, 6))
    check("manglende antal validerer ikke", not stk_validates_pack_size(None, 6))


def test_brand_gate() -> None:
    print("\nMærke-gate (kun ægte, modstridende nationale mærker afviser)")
    # De to par lå begge i produktionscachen som accepterede match.
    must_not_match("Den Gamle Fabrik vs St. Dalfour marmelade",
                   product('Jordbærmarmelade', 'Den Gamle Fabrik', '380 g'),
                   product('Jordbærmarmelade', 'St. Dalfour', '380 g'))
    must_not_match("Queen vs Aruna hønsebouillon",
                   product('Queen Hønsebouillon', 'Queen', '100 g'),
                   product('Hønsebouillon', 'Aruna', '100 g'))
    must_not_match("Barral oliven vs Kilic sesamfrø",
                   product('Sorte oliven m. sten', 'Barral', '150 g'),
                   product('Sorte sesamfrø', 'Kilic', '150 g'))

    # Gaten skal IKKE fyre på forkortelser og afledte mærker - producent-feltet
    # er upålideligt for 6 af 14 butikker.
    check("'D.G.F' regnes som samme mærke som 'Den Gamle Fabrik'",
          not brands_conflict('D.G.F Brombærmarmelade', 'D.G.F',
                              'Brombærmarmelade', 'Den Gamle Fabrik'))
    check("'Kk' regnes som samme mærke som 'Arla Karolines Køkken'",
          not brands_conflict('Kk Mornay Sauce', 'Kk',
                              'Karolines Køkken Mornaysauce', 'Arla Karolines Køkken'))
    check("'Steff-H' regnes som samme mærke som 'Steff Houlberg'",
          not brands_conflict('Steff-H Hamburgerryg', 'Steff-H',
                              'Hamburgerryg', 'Steff Houlberg'))
    check("'Danonino' regnes som samme mærke som 'Danone'",
          not brands_conflict('Danonino Yoghurt', 'Danonino',
                              'Yoghurt', 'Danone'))
    check("egne mærker på tværs af kæder er ikke en konflikt",
          not brands_conflict('Xtra Havregryn', 'Xtra',
                              'First Price Havregryn', 'First Price'))
    check("kort skraldemærke ('Dg') kan ikke bære en afvisning",
          not brands_conflict('Dg Rugbrød', 'Dg', 'Rugbrød', 'Schulstad'))


def test_billede_gate() -> None:
    print("\nBilled-gate (pHash)")
    base_hash = 0xC5FD2B949CA2902F

    def flip(bits):
        """Samme hash med *bits* ændrede bit = Hamming-afstand *bits*."""
        out = base_hash
        for i in range(bits):
            out ^= (1 << i)
        return out

    check("afstand beregnes som Hamming",
          photo_distance({'_hash_int': base_hash}, {'_hash_int': flip(3)}) == 3)
    check("manglende hash giver None",
          photo_distance({'_hash_int': base_hash}, {'_hash_int': None}) is None)

    # Tydeligt forskellige fotos afviser, selv når navnet er identisk.
    must_not_match("identisk navn, men vidt forskellige fotos",
                   product('Kikærter øko', 'Bonduelle', '400 g', hash_int=base_hash),
                   product('Kikærter øko', 'Bonduelle', '400 g', hash_int=flip(30)))
    # Nær-identisk foto må ikke redde et par som en anden gate afviser.
    must_not_match("nær-identisk foto redder ikke en procent-konflikt",
                   product('Tuborg Classic 4,6%', 'Tuborg', '33 cl', hash_int=base_hash),
                   product('Tuborg Classic 0,0%', 'Tuborg', '33 cl', hash_int=flip(1)))
    # Men det må bære et svagt navn over navnegulvet.
    must_match("nær-identisk foto bærer et svagt navn over gulvet",
               product('Paradiso Kongeasp. Grøn 11c', 'Paradiso', '330 g', hash_int=base_hash),
               product('Hele grønne asparges', 'Paradiso', '330 g', hash_int=flip(2)))
    # Uden hash på begge sider må billedet hverken redde eller afvise.
    check("manglende foto afviser ikke",
          matches(product('Rugbrød', 'Schulstad', '500 g', 'Brød & Kager', hash_int=base_hash),
                  product('Rugbrød', 'Schulstad', '500 g', 'Brød & Kager', hash_int=None)))


def test_ean_backfill() -> None:
    print("\nEAN-backfill af vægt og antal")
    # Dagrofa har EAN paa 98 % af varerne, men vægt paa under 5 %; Salling har
    # vægt paa de SAMME EAN-numre. Vægt-gaten var derfor blind for ~8.000 varer.
    dagrofa = {'name': 'Gs Jordbærmarmelade', 'brand': 'Gestus', 'weight': '',
               'Kategori': 'Kolonial', 'price': 21.95, 'ean': '5701410363901'}
    salling = {'name': 'Jordbærmarmelade', 'brand': 'Den Gamle Fabrik', 'weight': '380 g',
               'Kategori': 'Kolonial', 'price': 24.95, 'ean': '5701410363901'}
    for p in (dagrofa, salling):
        annotate_match_signals(p)
    check("Dagrofa-varen mangler vægt før backfill", dagrofa['_weight_g'] is None)

    data = {'mk': ([dagrofa], {}, [], {}), 'bilka': ([salling], {}, [], {})}
    filled = backfill_attributes_by_ean(data)
    check("backfill udfyldte et felt", filled == 1)
    check("Dagrofa-varen har nu vægt fra samme EAN", dagrofa['_weight_g'] == 380.0)

    # En butiks egen oplysning maa aldrig overskrives.
    own = {'name': 'Marmelade', 'brand': 'X', 'weight': '250 g', 'Kategori': 'Kolonial',
           'price': 10.0, 'ean': '5701410363901'}
    annotate_match_signals(own)
    backfill_attributes_by_ean({'a': ([own], {}, [], {}), 'b': ([salling], {}, [], {})})
    check("egen vægt overskrives ikke af en anden butiks", own['_weight_g'] == 250.0)

    # Modstridende vægte for samme EAN: udfyld ingenting, et gæt ville vaere
    # vaerre end intet.
    tom = {'name': 'Marmelade', 'brand': 'Y', 'weight': '', 'Kategori': 'Kolonial',
           'price': 10.0, 'ean': '5701410363901'}
    annotate_match_signals(tom)
    backfill_attributes_by_ean({'a': ([own], {}, [], {}), 'b': ([salling], {}, [], {}),
                                'c': ([tom], {}, [], {})})
    check("modstridende vægte for samme EAN udfylder intet",
          tom['_weight_g'] is None)


def test_tilbudsavis_vaegt() -> None:
    """Vægtparsing i Tjek-aviserne fodrer vægt-gaten direkte."""
    print("\nTilbudsavis: vægt ud af fritekst (scraper/tjek_tilbud_scraper.py)")
    # Scraperne køres med scraper/ på stien (de importerer 'keywords' fladt),
    # så den skal med her for at kunne importere modulet.
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scraper'))
    from tjek_tilbud_scraper import parse_description

    def grams(desc):
        _t, w, _k = parse_description(desc)
        return parse_weight_to_grams(w) if w else None

    # Multipakninger blev laest som EN enhed: "24x33 cl" gav 330 g for en
    # 7,92-liters kasse, saa vaegt-gaten sammenlignede 330 g mod 7920 g.
    check("24x33 cl læses som totalvægt",
          grams("24x33 cl. + pant. Pr. liter 8,71 Max. 6 stk.") == 7920.0)
    check("6 x 0,5 l læses som totalvægt", grams("6 x 0,5 l") == 3000.0)
    check("3 X 200 G læses som totalvægt", grams("3 X 200 G") == 600.0)
    # Enkeltvaegte skal vaere uaendrede.
    check("enkeltvægt uændret", grams("Velsmag hakket oksekød 8-12%, 400 g.") == 400.0)
    check("liter uændret", grams("1,5 l") == 1500.0)
    # Intervaller er ikke en vaegt - den oevre graense blev foer angivet som
    # praecis nettovaegt.
    check("vægtinterval giver ingen vægt", grams("Flere varianter, 105-150 g") is None)
    check("kg-interval giver ingen vægt", grams("Kyllingebryst, 1,2-1,8 kg") is None)


def install_representative_idf() -> None:
    """Byg en token-IDF der ligner produktionens, før gate-testene kører.

    Gaten "distinktivt ord" afhænger af, hvor almindeligt et ord er i HELE
    kataloget. Uden en fast IDF ville testresultatet afhænge af, hvilken test
    der tilfældigvis kørte sidst - og et ukendt ord tæller som maksimalt
    distinktivt, så en tom IDF gør gaten kunstigt streng.

    Katalogets sammensætning her afspejler produktionen: farve-, størrelses- og
    varegruppeord er hyppige, mens mærke- og sortsnavne er sjældne.
    """
    catalogue = []
    # Farve-, størrelses- og tilstandsord er de allerhyppigste i et
    # dagligvarekatalog - de må aldrig blive "det distinktive ord".
    for i, word in enumerate(['rød', 'blå', 'grøn', 'hvid', 'mørk', 'lys',
                              'stor', 'lille', 'frisk', 'dansk']):
        for j in range(200):
            catalogue.append(product(f'{word} vare {i}{j}', 'Mærke', '380 g'))
    # Varegruppeord er moderat hyppige.
    for i, word in enumerate(['marmelade', 'konditorfarve', 'bouillon',
                              'oliven', 'chokolade', 'mælk', 'ost', 'brød']):
        for j in range(20):
            catalogue.append(product(f'{word} type {i}{j}', 'Mærke', '380 g'))
    # Sorts- og smagsnavne er sjældne - det er dem gaten skal hænge sit hat på.
    for rare in ('hindbær', 'blåbær', 'kalamata'):
        catalogue.append(product(f'{rare} vare', 'Mærke', '380 g'))
    build_token_idf({'x': (catalogue, {}, [], {})})


def test_distinktivt_ord() -> None:
    print("\nDistinktivt ord (kun aktiv naar foto mangler)")
    a = product('Hindbærmarmelade øko', 'X', '380 g')
    b = product('Blåbærmarmelade øko', 'X', '380 g')
    check("mest distinktive ord genfindes ikke -> ingen deling",
          not distinctive_token_shared(a['_cross_match_tokens'], b['_cross_match_tokens']))
    c = product('Hindbær marmelade øko', 'X', '380 g')
    check("samme distinktive ord -> deling",
          distinctive_token_shared(a['_cross_match_tokens'], c['_cross_match_tokens']))
    # Almindelige ord maa ikke goere gaten streng: 'blaa'/'roed' er hyppige i
    # kataloget, saa det distinktive ord er 'konditorfarve', som deles.
    d = product('Blå konditorfarve', 'Dr. Oetker', '20 g')
    e = product('Rød konditorfarve', 'Dr. Oetker', '20 g')
    check("hyppige farveord goer ikke gaten streng",
          distinctive_token_shared(d['_cross_match_tokens'], e['_cross_match_tokens']))

    # Uden IDF skal gaten vaere inaktiv (sikkert udgangspunkt, fx hvis
    # indlaesningen fejler).
    saved = updater._TOKEN_IDF
    updater._TOKEN_IDF = {}
    try:
        check("uden IDF er gaten inaktiv",
              distinctive_token_shared(a['_cross_match_tokens'], b['_cross_match_tokens']))
    finally:
        updater._TOKEN_IDF = saved


def _card(store, title, ean, weight_g, price, matches=None):
    return {
        '/product/store': store, '/product/title': title, '/product/ean': ean,
        '/product/weight_g': weight_g, '/product/price': price,
        '/product/imageLink': f'https://example/{title}.jpg',
        '/product/store_matches': matches or {},
    }


def test_ean_invariant() -> None:
    print("\nEAN-invariant (samme stregkode må ikke ligge på to kort)")
    # Målt i produktionscachen: 436 EAN-numre laa paa mere end ét faerdigt kort,
    # fordi Rema-annoteringen koerer foer EAN-grupperingen og hiver
    # EAN-baerende varer ud af deres egne grupper.
    a = _card('Netto', 'Havredrik cremet', '5701977002961', 1000.0, 12.0,
              {'netto': {'name': 'Havredrik cremet', 'ean': '5701977002961',
                         'price': 12.0, '_weight_g': 1000.0}})
    b = _card('Bilka', 'Havredrik cremet', '5701977002961', 1000.0, 11.0,
              {'bilka': {'name': 'Havredrik cremet', 'ean': '5701977002961',
                         'price': 11.0, '_weight_g': 1000.0}})
    out = _merge_cards_sharing_ean([a, b])
    check("to kort med samme EAN flettes til ét", len(out) == 1)
    check("begge butikker bevares i det flettede kort",
          set(out[0]['/product/store_matches']) >= {'netto', 'bilka'})

    # Men et sanity-tjek skal forhindre, at en EAN-kollision i raadata fletter
    # to reelt forskellige varer (fx 250 g mod 1500 g).
    c = _card('Netto', 'Appelsinjuice', '5701410408008', 250.0, 2.95,
              {'netto': {'name': 'Appelsinjuice', 'ean': '5701410408008',
                         'price': 2.95, '_weight_g': 250.0}})
    e = _card('Spar', 'Gestus Appelsinjuice', '5701410408008', 1500.0, 26.0,
              {'spar': {'name': 'Gestus Appelsinjuice', 'ean': '5701410408008',
                        'price': 26.0, '_weight_g': 1500.0}})
    out = _merge_cards_sharing_ean([c, e])
    check("uforenelige vægte flettes IKKE, selv med samme EAN", len(out) == 2)

    # Ugyldige "EAN" (Coop-avisens data-id) maa ikke kunne binde kort sammen.
    f = _card('SuperBrugsen', 'Cremefine', '1066581', 250.0, 10.0)
    g = _card('Kvickly', 'Dansk broccoli', '1066581', 250.0, 10.0)
    check("ugyldig EAN binder ikke to kort sammen",
          len(_merge_cards_sharing_ean([f, g])) == 2)


def test_billedsignalets_tre_roller() -> None:
    """Revisionen 29-08-2026: én konstant styrede tre forskellige beslutninger.

    Billedet bruges til at (a) åbne blokeringen, (b) lempe hårde gates og
    navnegulvet, og (c) afvise. Alle tre brugte grænsen 4, men kalibreringen bag
    de 4 var domineret af par der DELER billedfil (butikker i samme feed), hvor
    afstanden er 0 pr. konstruktion. På tværs af feeds er medianafstanden mellem
    to fotos af samme vare 22 af 64 bit, så afvisning ved 4 lukkede 95,7 % af de
    beviseligt korrekte par ude.
    """
    print("\nBilledsignalets tre roller (aabner / lemper / afviser)")
    base = 0xC5FD2B949CA2902F

    def flip(bits):
        out = base
        for i in range(bits):
            out ^= (1 << i)
        return out

    check("lempelsesgraensen er stadig stram", updater._PHOTO_SAME_MAX_DIST == 4)
    check("afvisningsgraensen er bredere", updater._PHOTO_REJECT_MAX_DIST == 20)

    # Samme vare fotograferet af to forskellige kilder: afstand 12 er normalt.
    must_match("foto med afstand 12 afviser ikke laengere samme vare",
               product('Kikaerter oeko', 'Bonduelle', '400 g', hash_int=base),
               product('Kikaerter oeko', 'Bonduelle', '400 g', hash_int=flip(12)))
    # Men tydeligt forskellige fotos afviser stadig.
    must_not_match("foto med afstand 30 afviser stadig",
                   product('Kikaerter oeko', 'Bonduelle', '400 g', hash_int=base),
                   product('Kikaerter oeko', 'Bonduelle', '400 g', hash_int=flip(30)))
    # Den brede graense maa ikke lempe en HAARD gate - kun typen og blokeringen.
    must_not_match("afstand 12 lemper IKKE en procent-konflikt",
                   product('Tuborg Classic 4,6%', 'Tuborg', '33 cl', hash_int=base),
                   product('Tuborg Classic 0,0%', 'Tuborg', '33 cl', hash_int=flip(12)))
    must_not_match("afstand 12 lemper IKKE en koedtype-konflikt",
                   product('Hakket oksekoed', '', '400 g', 'Koed', hash_int=base),
                   product('Hakket kyllingekoed', '', '400 g', 'Koed', hash_int=flip(12)))


def test_vaegtloest_positivt_bevis() -> None:
    """Vaegtloest-gulvet krtaevede navnescore >= 0,75 - men paa kryds-feed-par er
    mediannavnescoren for KORREKTE par 0,609 og for FORKERTE 0,720. Navnet er
    altsaa anti-korreleret med korrekthed dér, og kravet er byttet til et
    positivt bevis (foto, stk-antal, kg-pris, delt distinktivt ord eller
    frugt/groent)."""
    print("\nVaegtloest par - positivt bevis frem for navnelighed")

    # Uden vaegt paa den ene side og med et lavt navne-match: kg-prisen redder.
    a = product('Gestus Hvidloeg I Kryd.Olie', 'Gestus', '', kg_price=95.0)
    b = product('Hvidloeg i olie m. krydderier', '', '280 g', kg_price=98.0)
    check("kg-pris paa begge sider taeller som bevis",
          updater.cross_store_pair_verdict(a, b, a['_norm_name'], a['_cross_match_tokens'])[0]
          or updater.cross_store_pair_verdict(b, a, b['_norm_name'], b['_cross_match_tokens'])[0])

    # Uden noget som helst bevis gaelder det gamle, hoeje gulv fortsat.
    check("gulvet gaelder stadig naar intet bekraefter parret",
          updater._WEIGHTLESS_NAME_FLOOR == 0.75)


def test_laengde_forfilter_invariant() -> None:
    """Forfilteret lover at det aldrig afviser et par, som fuzzy_score ville
    have accepteret. Revisionen 29-08-2026 mistaenkte loeftet for at vaere
    brudt; efterproevningen viste at det HOLDER, og at det er skarpt.

    2*min/(la+lb) er den maksimalt opnaaelige ratio, og fuzzy_score returnerer
    max(rapid_ratio, rapid_token_sort) - begge paa de samme to strenge, altsaa
    samme graense. Testen laaser den lighed fast, saa en fremtidig aendring af
    fuzzy_score (fx en scorer der IKKE er laengde-bundet) ikke stille goer
    forfilteret strengere end gulvet det efterligner.
    """
    print("\nLaengde-forfilterets invariant")
    from app_support import fuzzy_score as _fs
    for a, b in (('aeg', 'oekologiske aeg fra frilandshoens 10 stk'),
                 ('kk remoulade', 'karolines koekken remoulade'),
                 ('smoer', 'saltet smoer fra danske malkekoeer 200 g')):
        bound = 2.0 * min(len(a), len(b)) / (len(a) + len(b))
        check("score overstiger aldrig forfilterets graense (%s)" % a,
              _fs(a, b) <= bound + 1e-9)
        if updater._cross_store_length_prefilter(len(a), len(b)):
            check("afvist paa laengde => ville ogsaa falde paa navnegulvet (%s)" % a,
                  _fs(a, b) < updater._CROSS_STORE_NAME_FLOOR)

    # Det ENESTE sted de to kunne divergere, var billed-lempelsen: navnegulvet
    # lempes af et foto, forfilteret gjorde ikke. Nu springer photo_compatible
    # hele blokeringen over.
    base = 0xC5FD2B949CA2902F
    must_match("foto springer laengde-forfilteret over",
               product('Paradiso Kongeasp. Groen 11c', 'Paradiso', '330 g', hash_int=base),
               product('Hele groenne asparges i glas fra Paradiso', 'Paradiso', '330 g',
                       hash_int=base ^ 0b111))


def test_pl_maerker_er_ikke_distinktive() -> None:
    """brands_conflict dokumenterer PL<->PL paa tvaers af kaeder som TILSIGTEDE
    match, men IDF-gaten afviste dem: kaedemaerket er sjaeldent i katalogets
    navne og vandt derfor IDF-konkurrencen."""
    print("\nKaedemaerker taeller ikke som 'det distinktive ord'")
    a = product('Xtra Havregryn', 'Xtra', '1000 g')
    b = product('Budget Havregryn', 'Budget', '1000 g')
    check("'xtra' og 'budget' er kendte PL-tokens",
          'xtra' in updater._PL_BRAND_TOKENS and 'budget' in updater._PL_BRAND_TOKENS)
    check("PL-maerker fjernes foer det distinktive ord vaelges",
          distinctive_token_shared(a['_cross_match_tokens'], b['_cross_match_tokens']))
    must_match("Xtra Havregryn moeder Budget Havregryn", a, b)


def test_procent_intervaller() -> None:
    """_PCT_RE fangede kun tallet lige foer '%', altsaa intervallets OEVRE
    graense: '4-7%' blev {7}, saa en punktangivelse inde i intervallet blev
    laest som en modsigelse."""
    print("\nProcent-intervaller")
    from updater import get_product_percents
    check("'4-7%' foldes ud til hele intervallet",
          get_product_percents('Hakket oksekoed 4-7%') >= {4.0, 5.0, 6.0, 7.0})
    must_match("'4-7%' moeder '5%'",
               product('Hakket oksekoed 4-7%', '', '400 g', 'Koed'),
               product('Hakket oksekoed 5%', '', '400 g', 'Koed'))
    must_not_match("'4-7%' moeder IKKE '8-12%'",
                   product('Hakket oksekoed 4-7%', '', '400 g', 'Koed'),
                   product('Hakket oksekoed 8-12%', '', '400 g', 'Koed'))
    check("kampagnetekst folder ikke 31 vaerdier ud",
          len(get_product_percents('Spar 20-50%')) <= 2)


def test_produkt_id_variant() -> None:
    """normalize_name fjerner 'oekologisk' - med vilje, for scoringens skyld -
    men den normaliserede streng blev genbrugt som IDENTITET. 289 ID'er blev
    delt af flere kort, og seed-d1.py droppede 312 kort tavst."""
    print("\nProdukt-ID skelner mellem variant-udgaver")
    from updater import build_store_display_products
    plain = product('Agurker', '', '', 'Frugt & Groent')
    org = product('Oekologiske Agurker', '', '', 'Frugt & Groent')
    a = build_store_display_products([plain], 'meny')[0]['/product/id']
    b = build_store_display_products([org], 'meny')[0]['/product/id']
    check("oeko og almindelig faar FORSKELLIGT id", a != b)
    again = build_store_display_products([product('Agurker', '', '', 'Frugt & Groent')],
                                         'meny')[0]['/product/id']
    check("id er stadig stabilt for samme vare", a == again)


def test_kendte_huller() -> None:
    """Huller fundet ved matchmotor-analysen 25-08-2026, endnu ikke lukket."""
    print("\nKendte huller (skal fejle nu, bestå senere)")

    # Etape 1.2: Dagrofa-feedet skriver ø som ä ("Hänsebouillon"). Skaden er
    # MINDRE end først antaget: navnescoren overlever (0,92), og 102 af de 103
    # ramte varer har et andet rent ord at blive blokeret ind på. Kun når det
    # korrupte ord står ALENE, forsvinder parret helt - så det er dét tilfælde
    # der låses her. (Hovedbegrundelsen for at rette feedet er, at navnet vises
    # forkert for kunden, ikke at matchingen bryder sammen.)
    must_match("enkeltords-navn vs mojibake-udgave",
               product('Hønsebouillon', '', '100 g'),
               product('Hänsebouillon', '', '100 g'),
               expect_fail=True)

    # Farvevarianter er ikke dækket af nogen gate: 'blå' og 'rød' er hverken
    # smag, form eller variant. Samme klasse som Nan 1/Nan 2 nedenfor.
    must_not_match("Blå vs rød konditorfarve",
                   product('Blå konditorfarve', 'Dr. Oetker', '20 g'),
                   product('Rød konditorfarve', 'Dr. Oetker', '20 g'),
                   expect_fail=True)

    # Etape 4: "Nan 1" og "Nan 2" er forskellige aldersgrupper af
    # modermælkserstatning - ingen gate ser tal-varianter i navnet.
    must_not_match("Nan 1 vs Nan 2 modermælkserstatning",
                   product('Nan 1 Expertpro Sensilac fra 0 mdr.', 'Nestlé', '800 g'),
                   product('Nan 2 Expertpro Sensilac fra 6 mdr.', 'Nestlé', '800 g'),
                   expect_fail=True)


def main() -> int:
    print("=" * 62)
    print("MATCHMOTOR - REGRESSIONSTEST")
    print("=" * 62)
    # Skal koere FOER gate-testene: uden en fast IDF afhaenger resultatet af
    # testraekkefoelgen, og en tom IDF goer "distinktivt ord"-gaten kunstigt
    # streng (ukendte ord taeller som maksimalt distinktive).
    install_representative_idf()
    test_ean_validering()
    test_tilbudsavis_vaegt()
    test_procent_gate()
    test_koed_gate()
    test_smag_og_variant()
    test_vaegt_og_antal()
    test_normalisering()
    test_navnescore()
    test_stk_validering()
    test_brand_gate()
    test_billede_gate()
    test_ean_backfill()
    test_ean_invariant()
    test_distinktivt_ord()
    # Revisionen 29-08/30-08-2026
    test_billedsignalets_tre_roller()
    test_vaegtloest_positivt_bevis()
    test_laengde_forfilter_invariant()
    test_pl_maerker_er_ikke_distinktive()
    test_procent_intervaller()
    test_produkt_id_variant()
    test_kendte_huller()

    print()
    if unexpected_passes:
        print(f"{len(unexpected_passes)} KENDT(E) HUL(LER) ER LUKKET:")
        for label in unexpected_passes:
            print(f"  - {label}")
        print("Fjern expect_fail=True for disse, så de fremover er en rigtig gate.")
        return 1
    if fails:
        print(f"{len(fails)} KONTROL(LER) FEJLEDE:")
        for label in fails:
            print(f"  - {label}")
        return 1
    print("ALLE TESTS BESTAAET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
