import requests
import re
import xmltodict
import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()
import math
import hashlib
import html as _html
import traceback
import threading

from supabase import create_client

from app_support import (
    configure_logging, db_available,
    build_search_index, logger,
    DEFAULT_HTTP_HEADERS, _STORE_CONFIGS, format_price,
    normalize_name, fuzzy_score,
    parse_weight_to_grams, parse_stk_count, weights_compatible,
    ean_looks_valid,
    _PLACEHOLDER_IMGS,
    CAT_ANDET, CAT_FRUGT_GROENT, unify_category, is_age_restricted,
    compute_image_hash, phash_hex_to_int, hash_candidate_indices,
    _HASH_CANDIDATE_MAX_DIST,
    is_organic, is_lactose_free, is_sugar_free, is_gluten_free, is_alcohol_free,
    get_meat_types, meats_match as _meats_match,
    _compile_keyword_patterns, _extract_keywords,
    get_product_flavors, get_search_flavor_keywords,
    clean_display_text as _clean_field,
)


def _updater_table_suffix() -> str:
    """Suffiks på skrive-tabellerne (cart_events, price_alerts) i updater.py.

    Compliance-audit 19-08-2026 (GDPR-033): denne funktion erstatter det
    tidligere `os.getenv('TABLE_SUFFIX', '')`, som defaultede til PRODUKTION
    når variablen manglede - modsat app.py::_table_suffix(), der defaulter
    til '_dev'. Konsekvens: en udvikler der kørte `python updater.py` lokalt
    uden at have sat TABLE_SUFFIX=_dev (rodens .env gør det ikke) læste og
    RETTEDE PÅ produktionens price_alerts og sendte rigtige prisalarm-mails
    til rigtige brugere.

    GitHub Actions sætter aldrig TABLE_SUFFIX eksplicit for de natlige jobs
    (cache-updater.yml) - de skal fortsat ramme produktion, uændret. GITHUB_
    ACTIONS er derfor det signal der adskiller "kører i CI, ingen suffiks
    sat = production er meningen" fra "kører lokalt, ingen suffiks sat =
    glemte at sætte den, ramte næsten produktion ved et uheld"."""
    suffix = os.environ.get("TABLE_SUFFIX")
    if suffix is not None:
        return suffix
    return "" if os.environ.get("GITHUB_ACTIONS") == "true" else "_dev"


def _get_supabase_client():
    url = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    key = (
        os.getenv('DEPLOY_KEY')
        or os.getenv('SUPABASE_KEY')
        or os.getenv('NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY')
    )
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None

supabase = _get_supabase_client()

configure_logging()


XML_URL = "https://cphapp.rema1000.dk/api/v1/products.xml"

# Rema is the XML data source - not "primary", just the feed format we parse
REMA_KEY       = 'rema'
DB_STORE_KEYS = [k for k, v in _STORE_CONFIGS.items() if v.get('db_key')]


def _cheapest_tie_break_key(store_key: str) -> int:
    """Deterministisk sorteringsnøgle for prislige-tievalg.

    Erstatter `random.choice(cheapest_stores)`, som lod det viste kort
    (titel/billede/mærke/kategori) flippe tilfældigt mellem hver
    cache-genopbygning for enhver Rema-vare med reel prislige (målt 18,4%
    af matchede Rema-kort i lokalt snapshot). Rema foretrækkes ved lige
    pris (det er trods alt kildevaren), derefter butikkens position i
    DB_STORE_KEYS - samme rækkefølge der allerede afgør klynge-anker andre
    steder i filen, så adfærden er konsistent på tværs af pipelinen. Se
    matchmotor-revisionen 2026-08-16, fund C5.
    """
    if store_key == REMA_KEY:
        return -1
    try:
        return DB_STORE_KEYS.index(store_key)
    except ValueError:
        return len(DB_STORE_KEYS)


# Navnegulvet i fase 2/fase 2b (bruges også af _cross_store_length_prefilter
# nedenfor, så det billige forfilter er matematisk garanteret aldrig
# strengere end selve scoregrænsen det skal genskabe billigt).
_CROSS_STORE_NAME_FLOOR = 0.65

# Navnegulv for par hvor mindst én side mangler vægt, OG hvor intet andet
# signal bekræfter matchet (se "vægtløst par"-gaten i cross_store_pair_verdict).
# Gulvet er kun sidste udvej nu; før var det den eneste regel, og den kostede
# 19,7 point recall på kryds-feed-segmentet, fordi navnescoren dér er
# anti-korreleret med korrekthed.
_WEIGHTLESS_NAME_FLOOR = 0.75


def _cross_store_length_prefilter(len_a: int, len_b: int) -> bool:
    """True hvis parret kan afvises billigt FØR fuzzy_score beregnes.

    Den gamle faste "> 20 tegn"-grænse (kun i fase 2, ikke fase 2b) var
    beviseligt strengere end selve navnegulvet ved marginen: RapidFuzz'
    ratio-familie kan aldrig score højere end 2*min(la,lb)/(la+lb), så et
    fast tegnantal afviser par en rigtig, indholds-følsom scoring kunne
    have accepteret (bekræftet med et reelt produktionsmatch, Stryhn's/
    Tulip leverpostej, længdediff 21, faktisk score 0,677). Denne udgave
    bruger samme øvre grænse som fuzzy_score selv, skaleret med de faktiske
    navnelængder. Se matchmotor-revisionen 2026-08-16, fund H7.

    EFTERPRØVET 30-08-2026: invarianten HOLDER, og den er skarp. Grænsen
    2*min/(la+lb) er den maksimalt opnåelige ratio, og fuzzy_score returnerer
    max(rapid_ratio, rapid_token_sort) - begge regnet på de samme to strenge og
    dermed underlagt samme grænse. Målt på de to par revisionen mistænkte:

        "æg" / "økologiske æg fra frilandshøns, 10 stk" : grænse 0,143, score 0,143
        "Kk Remoulade" / "Karolines Køkken Remoulade"   : grænse 0,632, score 0,632

    Scoren RAMMER grænsen i begge tilfælde. Forfilteret er altså ikke en
    tilnærmelse af navnegulvet - det ER navnegulvet, beregnet uden at kalde
    RapidFuzz. Par der afvises på 'længde' ville være afvist på 'navnegulv'
    et par linjer senere.

    Det eneste sted de to kunne divergere, var billed-lempelsen: navnegulvet
    bliver lempet af et foto, forfilteret gjorde ikke. Det hul er lukket ved at
    lade ``photo_compatible`` springe HELE blokeringen over i
    cross_store_pair_verdict, ikke ved at duplikere logik herinde.
    """
    if len_a == 0 or len_b == 0:
        return True
    upper_bound = 2.0 * min(len_a, len_b) / (len_a + len_b)
    return upper_bound < _CROSS_STORE_NAME_FLOOR


# Butiks-label -> butiks-key (omvendt af _STORE_CONFIGS). Bruges i billede-dedup
# til at folde en dublets forside-butik ind i det beholdte korts store_matches.
_LABEL_TO_KEY = {v['label']: k for k, v in _STORE_CONFIGS.items()}

# Single unified cache: store_key -> (products_list, token_index_dict)
_store_caches: dict = {}
_store_cache_lock = threading.Lock()


def load_store_comparison_data(store_key: str) -> tuple:
    """Generic loader: reads from Supabase and builds token + EAN indexes."""
    if store_key in _store_caches:
        return _store_caches[store_key]
    with _store_cache_lock:
        if store_key in _store_caches:
            return _store_caches[store_key]
        
        cfg = _STORE_CONFIGS[store_key]
        products = []
        
        if db_available() and supabase is not None:
            try:
                # Fetch all products for the store using pagination to bypass 1000-row limit
                all_data = []
                last_id = -1
                while True:
                    res = supabase.table("produkter").select("*").eq("butik", cfg['db_key']).gt("id", last_id).order("id").limit(1000).execute()
                    if not res.data:
                        break
                    all_data.extend(res.data)
                    last_id = res.data[-1]['id']
                    
                for row in all_data:
                    raw_price = row.get('pris')
                    if raw_price is None or float(raw_price) <= 0:
                        continue
                    if store_key == 'bilka' and str(row.get('producent') or '').strip().lower().startswith('deli'):
                        continue
                    
                    price = float(raw_price)
                    weight_str = str(row.get('netto_vaegt') or '')
                    weight_g = parse_weight_to_grams(weight_str)
                    ppk = parse_kg_price(row.get('kg_price') or '')
                    price = sanitize_price(price, ppk, weight_g,
                                            context=f"{cfg['label']}: {row.get('navn')}")
                    
                    is_sale_raw = str(row.get('tilbud', 'nej')).lower()
                    is_sale = is_sale_raw in ('ja', 'true', 'yes', '1')
                    
                    ean_raw = str(row.get('varenummer') or '').strip()
                    ean = ean_raw.split('.')[0].strip() if ean_raw not in ('nan', 'None', '') else ''
                    if store_key == 'lidl':
                        # Lidl har ingen offentlig EAN - varenummer er deres interne
                        # erpNumber (SKU), ikke en rigtig stregkode. Bruges derfor ikke
                        # til EAN-baseret cross-store matching (stage 1/2), for at undgå
                        # falske matches hvis en anden butiks ægte EAN-8 tilfældigvis
                        # rammer samme cifre. Lidl er 100% fuzzy (stage 3).
                        ean = ''
                    elif not ean_looks_valid(ean):
                        # Samme problem som Lidl, men skjult bag et tal der LIGNER
                        # en stregkode: Coop-avisernes scraper gemmer avisens interne
                        # data-id i varenummer (scraper_utils.py), så sb/kvickly/
                        # brugsen havde 100 % "EAN-dækning" som var ren fiktion. Den
                        # gik lige ind i ean_index, stage-1-gruppering og EAN-cross-
                        # fill på lige fod med rigtige stregkoder.
                        #
                        # Værre: kortets id bygges af EAN'en (ean_<md5>, se
                        # build_store_display_products), så id'et skiftede hver gang
                        # avisen skiftede uge - og dermed nulstilledes prishistorik og
                        # prisalarmer for de varer.
                        #
                        # Målt på hele produktcachen rammer gaten præcis det rigtige:
                        # 239/239 Coop-data-id'er og 58 øvrige skraldeværdier afvises,
                        # mens alle 24.808 ægte EAN-8/12/13/14 accepteres.
                        #
                        # Bemærk hvad vi giver afkald på: data-id'et er fælles for
                        # HELE Coop-avisen, så det bandt korrekt 112 SuperBrugsen↔
                        # Kvickly-par sammen (samme tilbud, samme uge). Efterprøvet
                        # før ændringen: fuzzy genskaber alle 112 (navnene er
                        # identiske), og ingen af de falske EAN'er havde nogensinde
                        # bundet en IKKE-Coop-butik til en Coop-vare. Nettoprisen for
                        # stabile kort-id'er er altså nul tabte grupperinger.
                        ean = ''

                    p_hash_hex = str(row.get('billede_hash') or '')
                    p_hash_int = phash_hex_to_int(p_hash_hex)
                        
                    np_raw = row.get('normalpris')
                    normal_price = None
                    if np_raw and str(np_raw) not in ('nan', 'None', ''):
                        try:
                            np = float(str(np_raw).replace(',', '.').replace('kr', '').strip())
                            if np > 0:
                                normal_price = np
                        except Exception:
                            pass
                            
                    multi_deal = _clean_field(row.get('multikob'))

                    name_str = str(row.get('navn') or '')
                    # Samme rensning som multikob altid har haft. Uden den kom
                    # strengen "None" (ikke Python-None) fra producent-feltet
                    # hele vejen ud paa produktkortet som maerke - maalt paa
                    # ~4 % af forsidens varer 19-08-2026, paa baade web og app.
                    # app_support.clean_display_text er sikkerhedsnettet i
                    # visningslaget; her stoppes det ved kilden, saa nattens
                    # cache heller ikke baerer skidtet videre.
                    brand_str = _clean_field(row.get('producent'))
                    kategori_str = _clean_field(row.get('kategori'))

                    product = {
                        'name':        name_str,
                        'brand':       brand_str,
                        'weight':      weight_str,
                        'kg_price':    ppk,
                        'price':       price,
                        'normal_price': normal_price,
                        'is_sale':     is_sale,
                        'multi_deal':  multi_deal,
                        'image':       str(row.get('billede_url') or ''),
                        '_image_hash': p_hash_hex,
                        '_hash_int':   p_hash_int,
                        'ean':         ean,
                        'Kategori':    kategori_str,
                    }
                    # Precompute af alle matchsignaler ét sted (delt med fase
                    # 2/2b og måle-harnesset, se annotate_match_signals).
                    # Returnerer None for ikke-mad og tobak, som hverken må
                    # matches eller vises.
                    if annotate_match_signals(product) is None:
                        continue
                    products.append(product)

            except Exception as e:
                logger.warning("Error fetching %s from Supabase: %s", cfg['label'], e)
                
        # Building indexes
        token_idx: dict = {}
        hash_list = []
        ean_index: dict = {}
        _poisoned_eans: set = set()
        for i, p in enumerate(products):
            for token in p['_norm_name'].split():
                # >=3 for at matche fase 2/2b's kandidat-indeks (var >=4 her,
                # udokumenteret inkonsistens - se fund M1)
                if len(token) >= 3:
                    token_idx.setdefault(token, set()).add(i)
            p_hash_int = p.get('_hash_int')
            if p_hash_int is not None:
                hash_list.append((i, p_hash_int))
            ean = p.get('ean')
            if not ean:
                continue
            if ean in _poisoned_eans:
                continue
            existing = ean_index.get(ean)
            if existing is not None and existing is not p:
                # To forskellige varer i samme butik deler samme EAN. Det
                # gamle "ean_index[ean] = p" lod den sidst-indlæste stille
                # vinde og gjorde den tabende vare permanent usynlig for
                # EAN-baseret matching (bekræftet levende: samme marmelade
                # splittet på to kort under forskellig butiksattribution,
                # og en Tuborg-EAN krydstilknyttet på tværs af pakke-
                # størrelser). Udelukker i stedet HELE EAN'en fra denne
                # butiks ean_index, så begge varer i stedet vurderes af
                # fuzzy-matchingens strukturelle gates. Se matchmotor-
                # revisionen 2026-08-16, fund C6.
                _poisoned_eans.add(ean)
                del ean_index[ean]
                continue
            ean_index[ean] = p

        if _poisoned_eans:
            logger.warning(
                "EAN-kollision hos %s: %d EAN-værdi(er) har flere forskellige "
                "varer i samme butik og er udelukket fra ean_index: %s",
                cfg['label'], len(_poisoned_eans), sorted(_poisoned_eans)[:20])

        result = (products, token_idx, hash_list, ean_index)
        _store_caches[store_key] = result
        logger.info("Loaded %s products from Supabase for %s", len(products), cfg['label'])
        return result


import concurrent.futures

def backfill_attributes_by_ean(store_data: dict) -> int:
    """Udfyld manglende vægt/stk fra en ANDEN butiks data for samme EAN.

    EAN er en autoritativ produktidentitet, så nettovægten for EAN X er den
    samme, uanset hvilken butik der oplyser den. Alligevel stod dataene isoleret
    pr. butik, og det ramte skævt: Dagrofa (Meny/Spar/Min Købmand) havde EAN på
    98 % af varerne, men vægt på under 5 % - mens Salling-butikkerne har vægt på
    94-100 % af de SAMME EAN-numre.

    Konsekvensen var, at vægt-gaten var blind for ~8.000 varer, som i stedet
    faldt tilbage på det højere navnegulv for "vægtløse par" - altså navnet
    alene. Efter backfill kan gaten faktisk fyre.

    MÅLT PÅ NY 30-08-2026: Dagrofas egen vægt-dækning er nu 88-96 % (Spar 96 %,
    Meny 93 %, Min Købmand 88 %), ikke under 5 %. Det gamle tal stammer fra data
    der havde stået stille i 20 dage, fordi butikkernes shrink-værn var i stykker
    (se dagrofa_scraper.py). Backfillen er stadig rigtig - den udfylder de
    resterende 4-12 % og hjælper de øvrige EAN-løse butikker - men den bærer ikke
    længere hovedparten af Dagrofas vægte.

    Bemærk konsekvensen for matchingen: da de rigtige vægte kom ind, faldt
    Dagrofas antal match ~20-27 % (Meny 908 -> 678, Spar 729 -> 535). Det er
    IKKE en regression. Vægt-gaten er tavs-lempelig når den ene side mangler
    vægt (weights_compatible returnerer True ved None), så de forsvundne par var
    par der slap igennem netop fordi vægten manglede. Nu bliver de vurderet.

    Kun felter der MANGLER udfyldes; en butiks egen oplysning overskrives
    aldrig. EAN'er der optræder med modstridende vægte på tværs af butikker
    springes over - dér ved vi ikke hvem der har ret, og et gæt ville være
    værre end intet.

    Returnerer antallet af udfyldte felter (til logning).
    """
    weights: dict = {}
    stks: dict = {}
    conflicting: set = set()

    for products, _token_idx, _hash_list, _ean_index in store_data.values():
        for p in products:
            ean = p.get('ean')
            if not ean:
                continue
            w = p.get('_weight_g')
            if w:
                known = weights.get(ean)
                if known is None:
                    weights[ean] = w
                elif not weights_compatible(known, w):
                    # To butikker er uenige om vægten for samme stregkode.
                    conflicting.add(ean)
            s = p.get('_stk_count')
            if s and stks.setdefault(ean, s) != s:
                conflicting.add(ean)

    filled = 0
    for products, _token_idx, _hash_list, _ean_index in store_data.values():
        for p in products:
            ean = p.get('ean')
            if not ean or ean in conflicting:
                continue
            if not p.get('_weight_g') and weights.get(ean):
                p['_weight_g'] = weights[ean]
                filled += 1
            if p.get('_stk_count') is None and stks.get(ean):
                p['_stk_count'] = stks[ean]
                filled += 1

    if conflicting:
        logger.info("EAN-backfill: %d EAN(s) sprunget over pga. modstridende vægt/antal",
                    len(conflicting))
    return filled


def load_all_comparison_data() -> dict:
    """Returns {store_key: (products, token_idx)} for all DB stores."""
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(DB_STORE_KEYS)) as executor:
        future_to_key = {executor.submit(load_store_comparison_data, key): key for key in DB_STORE_KEYS}
        for future in concurrent.futures.as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as e:
                logger.error("Error loading %s concurrently: %s", key, e)
                results[key] = ([], {}, [], {})

    filled = backfill_attributes_by_ean(results)
    if filled:
        logger.info("EAN-backfill: udfyldte %d manglende vægt-/antalsfelter "
                    "fra andre butikkers data for samme EAN", filled)
    # IDF skal bygges EFTER backfill, så den ser de endelige navne, og før
    # matchingen starter - den bruges af gate-kæden.
    build_token_idf(results)
    return results

def _variant_flags(name: str, desc: str = '', brand: str = '') -> tuple:
    """Variant-flags (øko, laktosefri, sukkerfri, glutenfri) som tuple.

    Precomputes én gang pr. produkt - to produkter er variant-kompatible
    præcis når deres tupler er ens (samme semantik som det gamle
    variants_compatible, men uden at genscanne teksterne pr. kandidat-par)."""
    return (
        is_organic(name, desc, brand),
        is_lactose_free(name, desc, brand),
        is_sugar_free(name, desc, brand),
        is_gluten_free(name, desc, brand),
        # Alkoholfri er en SELVSTÆNDIG variant, ikke bare en procentangivelse.
        # Procent-gaten alene var utilstrækkelig: den kunne kun se 0,0% hvis
        # tallet stod i den tekst gaten kiggede i - og Rema skriver det ofte
        # kun i brandfeltet ("CARLSBERG 0,0%"). Resultatet var beviste falske
        # match i produktionscachen, bl.a. alkoholfri Harboe Pilsner sat
        # sammen med almindelig pilsner og med Harboe Apollinaris (dansk
        # vand). Flaget her gør forskellen eksplicit, uanset hvor i teksten
        # den står.
        is_alcohol_free(name, desc, brand),
    )


# Stk-antal nævnt løst i tekst ("Avocado 3 Stk.", "Æg 10 stk") - modsat
# app_supports parse_stk_count, der kræver at HELE strengen er "N stk".
_LOOSE_STK_RE = re.compile(r'\b(\d+)\s*stk\b')


def _stk_count_of(weight_str, name='') -> int | None:
    """Stk-antal fra vægtfeltet, ellers løst fra vægttekst/navn.

    Æg, te og frugt/grønt mangler ofte vægt, men bærer antallet i navnet
    ("Avocado 3 Stk.") eller i et vægtfelt med punktum ("6 stk."), som den
    strikse parser afviser - uden dette fallback er stk-gaten blind netop dér,
    hvor den er eneste mulige gate."""
    n = parse_stk_count(weight_str)
    if n is not None:
        return n
    for text in (str(weight_str or ''), str(name or '')):
        m = _LOOSE_STK_RE.search(text.lower())
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


def sanitize_price(price, ppk, weight_g, context: str = ''):
    """Fallback validation to fix scraped prices that incorrectly concatenated weight and kg-price."""
    if price > 0 and ppk is not None and weight_g is not None and weight_g > 0:
        expected_price = ppk * (weight_g / 1000.0)
        if expected_price > 0 and (price > expected_price * 2.5 or price < expected_price * 0.3):
            corrected = round(expected_price, 2)
            # Ulogget indtil nu - umuligt at auditere hvor ofte korrektionen
            # rammer, eller om den nogensinde har rettet i forkert retning.
            # Se matchmotor-revisionen 2026-08-16, fund M8.
            logger.warning(
                "sanitize_price korrigerede %s: %.2f kr -> %.2f kr (kg-pris %.2f, vægt %sg)",
                context or '(ukendt vare)', price, corrected, ppk, weight_g)
            # If the price is extremely off, trust the kg-price and weight
            return corrected
    return price


def is_price_cheaper(new_p, current_p):
    """Returns True if new_p is strictly cheaper than current_p."""
    if new_p is None: return False
    return new_p < current_p - 0.001


def effective_display_price(display_item):
    """Den pris kunden faktisk betaler for et kort: tilbudsprisen når kortet
    er på tilbud, ellers den viste pris.

    '/product/price' er NORMALprisen når kortet viser tilbud (se
    build_store_display_products), så en sammenligning mod det felt måler mod
    førprisen. Fase 2b flippede derfor "billigst hos" forkert: en base til
    12 kr slog en gruppe hvis billigste var Netto på tilbud til 10 kr med
    førpris 15, fordi 12 < 15."""
    sale = display_item.get('/product/sale_price')
    return sale if sale is not None else display_item.get('/product/price')


def is_price_equal(new_p, current_p):
    """Returns True if new_p is approximately equal to current_p."""
    if new_p is None: return False
    return abs(new_p - current_p) < 0.01


def types_compatible(type_a: str | None, type_b: str | None) -> bool:
    """Type gate for stage-3 fuzzy matching; unknown type stays permissive."""
    if not type_a or not type_b:
        return True
    return type_a == type_b




_PRIVATE_LABEL_BRANDS: frozenset = frozenset({
    # Rema 1000 – basisbrand + øvrige egne mærker
    'rema 1000', 'rema',
    'gram slot', 'kolonihagen', 'solgryn', 'cleverdeli',
    'vigo', 'maximat', 'lev vel', 'ängens',
    'plantekøkkenet', 'plantekokkenet', 'nemt & grønt', 'nemt and grønt',
    # Salling Group – basisbrand + øvrige egne mærker
    'salling', 'salling øko',
    'budget', 'princip', 'levevis', 'vrs', 'spir', 'nemt', 'hello sensitive',
    # Salling Group – kød-private labels
    'slagteren', 'bornholmer slagteren', 'den grønne slagter',
    # Coop – kædemærker og egne mærker
    'coop', 'xtra', 'x-tra', 'änglamark', 'irma', '365discount', 'coop 365', '365',
    'coop okologi', 'coop økologi', '365 okologi', '365 økologi',
    'coop veggie', 'coop glutenfri', 'coop baby', 'coop baby and friends',
    'coop minirisk', 'coop gourmet', 'coop premium', 'cirkel kaffe',
    'nordisk køkken',
    # Dagrofa – egne mærker (MENY, SPAR, Min Købmand, Let-Køb)
    'first price', 'fp', 'grøn balance', 'gestus', 'vores', 'karma', 'k-salat',
    'omhu', 'spicefield', 'banderos', 'fixa', 'praktisk', 'pur aktiv', 'silkline',
    # Kædenavne der også bruges som brand
    'meny', 'spar', 'min kobmand', 'min købmand', 'let-kob', 'let-køb',
    # Lidl – egne mærker
    'lidl', 'milbona', 'crownfield', 'combino', 'deluxe', 'harvest basket',
    # Løvbjerg / ABC Lavpris
    'lovbjerg', 'løvbjerg', 'abc lavpris', 'abc',
    'vita d\'or', 'snack day', 'madværket', 'italiamo', 'belbake', 'parkside',
})

_PRIVATE_LABEL_PREFIXES: tuple = (
    'rema ', 'rema 1000 ', 'gram slot ', 'kolonihagen ', 'cleverdeli ',
    'salling ', 'slagteren ', 'budget ',
    'coop ', 'xtra ', 'x-tra ', 'änglamark ', 'irma ',
    'first price ', 'fp ', 'grøn balance ', 'gestus ', 'levevis ',
    'vores ', 'karma ', 'cirkel ',
    'omhu ', 'spicefield ', 'banderos ', 'praktisk ',
    'milbona ', 'crownfield ', 'combino ', 'deluxe ', 'harvest basket ',
    'vita d\'or ', 'madværket ', 'italiamo ',
)

# Single-word brands that are first words of multi-word private label names.
# extract_producer() in the scrapers only takes the first word of the product name,
# so "First Price Havregryn" → brand="First" - we need this extra check.
_PRIVATE_LABEL_FIRST_WORDS: frozenset = frozenset({
    'first',    # First Price
    'grøn',     # Grøn Balance
    'let-køb', 'let-kob',  # Let-Køb
})


# Enkeltord fra kædernes egne mærker, normaliseret som varenavnene er det.
# Bruges af distinctive_token_shared: et kædemærke er sjældent i katalogets
# navne og vinder derfor IDF-konkurrencen, men det er præcis det ord der SKAL
# være forskelligt mellem to kæders udgave af samme vare.
_PL_BRAND_TOKENS: frozenset = frozenset(
    tok
    for brand in list(_PRIVATE_LABEL_BRANDS) + list(_PRIVATE_LABEL_FIRST_WORDS)
    for tok in normalize_name(brand).split()
    if len(tok) >= 3
)


def is_private_label(brand: str, title: str = '') -> bool:
    """Return True if the product is a private label / store brand."""
    b = brand.lower().strip()
    t = title.lower().strip()
    if b in _PRIVATE_LABEL_BRANDS:
        return True
    if b in _PRIVATE_LABEL_FIRST_WORDS:
        return True
    if any(b.startswith(p) for p in _PRIVATE_LABEL_PREFIXES):
        return True
    if any(t.startswith(p) for p in _PRIVATE_LABEL_PREFIXES):
        return True
    return False


# Procent-angivelser i produktnavne (fedt-%, alkohol-%, kakao-%) er reelle
# produktegenskaber: "Tuborg Classic 4,6%" og "Tuborg Classic 0,0% alkoholfri"
# er IKKE samme vare, og det samme gælder "Piskefløde 38%" ↔ "36%". Gaten er
# symmetrisk og kun aktiv, når BEGGE sider angiver procenter - en side, der
# blot udelader tallet ("Piskefløde"), er ikke en modsigelse.
_PCT_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*%')
# Ost angiver fedt som "45+", "60+" - aldrig med procenttegn. Uden denne var
# procent-gaten blind for netop den varegruppe, hvor tallet ER forskellen:
# cachen indeholdt Gouda 60+ matchet med Gouda 48+, Danablu 50+ med 60+ og
# Rosenborg Brie 45+ med Brie 50+. Vægt, navn og brand er ens på de par, så
# ingen anden gate kunne skelne dem.
_PLUS_PCT_RE = re.compile(r'(?<![\d,.])(\d{2})\s*\+')


# Procent-INTERVALLER: dansk hakket kød mærkes "4-7%", "8-12%", "14-18%", og
# butikkerne bruger forskellige konventioner for SAMME vare. _PCT_RE fanger kun
# tallet lige før '%', altså intervallets ØVRE grænse, så "Hakket oksekød 4-7%"
# og "Hakket oksekød 5%" blev læst som {7} mod {5} - ingen fælles værdi, og
# parret afvist på 'procent', selvom 5 ligger midt i 4-7. Intervallet foldes
# derfor ud til sine hele værdier, så mængde-snittet i _percents_match virker
# uændret. 158 varenavne i produktionscachen bærer et interval.
_PCT_RANGE_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*-\s*(\d+(?:[.,]\d+)?)\s*%')
# Et interval bredere end dette er ikke en varedeklaration men en kampagnetekst
# ("Spar 20-50%"), og skal ikke folde 31 værdier ud.
_PCT_RANGE_MAX_SPAN = 20


def get_product_percents(text: str) -> frozenset:
    """Alle procenttal nævnt i teksten, afrundet til 1 decimal.

    Intervaller ("4-7%") foldes ud til hver hel værdi i intervallet, så en
    punktangivelse inde i intervallet regnes som forenelig.
    """
    pcts = {round(float(m.replace(',', '.')), 1) for m in _PCT_RE.findall(text)}
    pcts.update(float(m) for m in _PLUS_PCT_RE.findall(text))
    for lo_s, hi_s in _PCT_RANGE_RE.findall(text):
        lo = round(float(lo_s.replace(',', '.')), 1)
        hi = round(float(hi_s.replace(',', '.')), 1)
        if lo > hi or hi - lo > _PCT_RANGE_MAX_SPAN:
            continue
        pcts.update(float(v) for v in range(int(math.ceil(lo)), int(hi) + 1))
        pcts.update((lo, hi))
    return frozenset(pcts)


def _percents_match(base_pcts: frozenset, cand_pcts: frozenset) -> bool:
    """Falsk kun når begge sider angiver procenter uden én fælles værdi."""
    return not base_pcts or not cand_pcts or bool(base_pcts & cand_pcts)


def _group_compatible(base_weight, base_stk, base_pcts: frozenset, members, base_variants=None,
                      base_meats=None) -> bool:
    """Valider en EAN-løs base mod ALLE medlemmer af en stage-1 EAN-gruppe.

    Genbruges også (med members=[ét produkt]) som et rent parvist tjek i
    fase 2's post-hoc konflikt-oprydning mellem to klyngemedlemmer - se
    matchmotor-revisionen 2026-08-16, fund H8.

    Fase 2b's gates sammenligner kun med ét gruppemedlem ad gangen, og et
    medlem uden vægtdata (typisk Dagrofa) kan derfor fungere som bagdør ind
    i en gruppe, hvis ØVRIGE medlemmer beviseligt modsiger basen - fx Lidl
    "BELBAKE Fødselsdagsboller 350 g" der kom ind via mk's vægtløse "Amo
    Fødselsdagsboller", selvom Bilka/Føtex i samme gruppe angiver 500 g.
    EAN-gruppens medlemmer er autoritativt samme vare, så ét medlem med
    uforenelig vægt, stk-antal eller procent afviser hele gruppen (samme
    princip som EAN retro-valideringen i Rema-annoteringen).

    Variant-tjekket (øko/laktosefri/sukkerfri/glutenfri) er af samme grund
    tilføjet: fase 2b's egne gates tjekker kun target_p (gruppens repræsen-
    tant), så en base kunne før slippe ind i en gruppe hvis blot repræsen-
    tanten matchede, selvom et ANDET medlem var fx økologisk mod basens
    almindelige (eller omvendt) - fanget ved audit af eksisterende matches
    2026-07-12 (fx "Kartofler" ↔ "Kartofler øko" endt i samme kort)."""
    for m in members:
        if not isinstance(m, dict):
            continue
        if not weights_compatible(base_weight, m.get('_weight_g')):
            return False
        if base_stk is not None and m.get('_stk_count') is not None and base_stk != m.get('_stk_count'):
            return False
        if not _percents_match(base_pcts, m.get('_pcts', frozenset())):
            return False
        if base_variants is not None and base_variants != m.get('_variants', base_variants):
            return False
        # Kødtype af samme grund som varianterne ovenfor: funktionen validerede
        # vægt, stk, procent og variant mod ALLE medlemmer, men ikke kød. En
        # kødløs base ("Frikadeller") kunne derfor samle både "Frikadeller m.
        # svinekød" og "Kyllingefrikadeller" i samme kort, fordi gaten i fase 2
        # kun ser basen og _meats_match er tavs-lempelig når den ene side
        # mangler kødtype.
        if base_meats is not None and not _meats_match(base_meats, m.get('_meats', frozenset())):
            return False
    return True


def _drop_cross_conflicting_matches(matches: dict, rema_w, rema_pcts: frozenset) -> dict:
    """Fjern butiks-matches der modsiger HINANDEN på vægt eller procent.

    Gates i _find_generic_match sammenligner kun kandidaten mod Rema-varen,
    og udeladelse er bevidst ensidigt lempet - men når Rema-teksten selv
    hverken angiver vægt eller procent, kan to butikkers matches være
    indbyrdes uforenelige varianter (Netto "Grillpølser 81 % kød" og Bilka
    "Grillpølser 62% kød" på samme Rema-kort for "GRILLPØLSER"). Højst én
    kan være Rema-varen, og uden arbiter droppes alle i konflikt. Par med
    samme EAN springes over - de er autoritativt samme vare trods
    label-drift (fx Matilde Kakaomælk "1,5%" hos mk vs "1,6%" hos Salling)."""
    if len(matches) < 2:
        return matches
    # Vægt/procent-armen er kun aktiv, når Rema-teksten selv tier om netop det
    # felt - ellers har gaten mod Rema allerede afgjort sagen.
    check_physical = not (rema_w and rema_pcts)
    items = list(matches.items())
    conflicted = set()
    for i, (k1, m1) in enumerate(items):
        for k2, m2 in items[i + 1:]:
            e1 = str(m1.get('ean') or '')
            if e1 and e1 == str(m2.get('ean') or ''):
                continue
            # Mærke-armen kører ALTID. To kandidater med hvert sit ægte
            # nationale mærke kan højst være den samme vare som Rema for den
            # enes vedkommende - uanset hvad Rema selv oplyser. Og Rema kan
            # ikke være dommer: begge bestod jo mærke-gaten mod Rema-teksten,
            # så den identificerer ikke nogen af dem.
            #
            # Målt på en fuld kørsel: 161 par som "Romkugler"/Dan Cake og
            # "Romkugler"/Fintons sad på samme Rema-kort, fordi gaten kun
            # sammenligner hver kandidat mod Rema - aldrig mod hinanden.
            if brands_conflict(str(m1.get('name') or ''), str(m1.get('brand') or ''),
                               str(m2.get('name') or ''), str(m2.get('brand') or '')):
                conflicted.update((k1, k2))
            elif not check_physical:
                continue
            elif not rema_w and not weights_compatible(m1.get('_weight_g'), m2.get('_weight_g')):
                conflicted.update((k1, k2))
            elif not rema_pcts and not _percents_match(m1.get('_pcts', frozenset()),
                                                       m2.get('_pcts', frozenset())):
                conflicted.update((k1, k2))
    if not conflicted:
        return matches
    return {k: m for k, m in matches.items() if k not in conflicted}


_NO_VARIANT_FLAGS = (False, False, False, False)


def _drop_variant_conflicting_matches(matches: dict, rema_variants: tuple) -> dict:
    """Kryds-medlems-arbitrage på variant-flag (øko/laktosefri/sukkerfri/glutenfri).

    _variants_compatible er bevidst ensidig: en kandidat der UDELADER et flag,
    Rema-varen har (fx "Saltet smør" mod Remas "SMØR, ØKOLOGISK"), afvises
    ikke - korte butiksnavne udelader ofte "øko". Men når en ANDEN butiks
    match eksplicit bekræfter Rema-flaget ("Arla Smør Øko"), er den tavse
    kandidat ikke længere en plausibel forkortelse: de to matches modsiger
    hinanden, og kandidaten der modsiger Rema-teksten droppes (fanget ved
    audit af eksisterende matches 2026-07-12: øko-smør-kort med Lurpak
    "Saltet smør" som medlem). Kun aktiv pr. flag når Rema-varen selv bærer
    flaget OG mindst ét medlem bekræfter det - er ALLE medlemmer tavse, er
    udeladelsen systematisk (Salling-generiske navne), og ingen droppes.
    Tavse medlemmer med samme EAN som en bekræfter fredes (autoritativt
    samme vare trods label-drift, jf. _drop_cross_conflicting_matches)."""
    if len(matches) < 2:
        return matches
    drop = set()
    for dim, base_flag in enumerate(rema_variants):
        if not base_flag:
            continue
        claimers = {k for k, m in matches.items() if m.get('_variants', _NO_VARIANT_FLAGS)[dim]}
        silent = set(matches) - claimers
        if not claimers or not silent:
            continue
        claimer_eans = {str(matches[k].get('ean') or '') for k in claimers} - {''}
        for k in silent:
            if str(matches[k].get('ean') or '') in claimer_eans:
                continue
            drop.add(k)
    if not drop:
        return matches
    return {k: m for k, m in matches.items() if k not in drop}


# Produkt-form (drik/budding/mousse osv.) - adskilt fra smag, da "chokolade" alene
# ikke skelner mellem fx en Arla Protein-drik og en Arla Protein-budding. Uden
# denne gate kan navnescoren (som deler "arla"/"protein"/"choko" på tværs af hele
# produktserien) fejlagtigt matche på tværs af produktformer.
_FORM_KEYWORDS = ('pudding', 'budding', 'mousse', 'skyr', 'kefir', 'yoghurt', 'yogurt', 'drik', 'shake')
_FORM_PATTERNS = _compile_keyword_patterns((kw, kw) for kw in _FORM_KEYWORDS)


def get_product_form(text: str) -> set:
    """Udtræk produktform (drik/budding/mousse osv.) fra produkttekst."""
    return _extract_keywords(text.lower(), _FORM_PATTERNS)


def _flavors_match(base_flavors: set, cand_flavors: set) -> bool:
    """Smags-gate: kun hård afvisning hvis KANDIDATEN nævner en smag, basen ikke har.

    Basen (Rema, eller den initierende butiksvare i cross-store-matching)
    nævner ofte en smag (fx "chokolade") som en kandidats kortfattede navn
    ikke gentager ("Choko") - det er ikke en modsigelse. Men hvis kandidaten
    eksplicit nævner en anden/ekstra smag end basen, er det en reel forskel."""
    return cand_flavors <= base_flavors


def _forms_match(base_forms: set, cand_forms: set) -> bool:
    """Form-gate (drik/budding/mousse osv.): samme asymmetri som _flavors_match.

    Forhindrer at fx en Arla Protein-DRIK matcher en Arla Protein-BUDDING,
    som ellers ville dele nok fælles ord ("arla", "protein", "choko") til at
    score højt på navnelighed alene."""
    return cand_forms <= base_forms


# Indeks i _variant_flags-tuplen der skal vurderes SYMMETRISK - se
# _variants_compatible. Alkoholfri er den eneste: (øko, laktosefri, sukkerfri,
# glutenfri, alkoholfri) -> indeks 4.
_SYMMETRIC_VARIANT_DIMS = frozenset({4})


def _variants_compatible(rema_variants: tuple, cand_variants: tuple) -> bool:
    """Variant-gate (øko, laktosefri, sukkerfri, glutenfri, alkoholfri).

    For de fire første er gaten bevidst ENSIDIG: Rema-produktets beskrivelse
    nævner ofte en attribut (fx "laktosefri") som en sammenligningsbutiks
    kortfattede varenavn ikke gentager - det er ikke en modsigelse, blot et
    kortere navn. Men hvis SAMMENLIGNINGSBUTIKKEN eksplicit påstår en attribut
    Rema-produktet ikke nævner, er det en reel forskel.

    Alkoholfri er undtagelsen og vurderes SYMMETRISK. Den ensidige regel lod
    Rema "Chenin Blanc 0,0%" matche en butiks almindelige "Chardonnay/Chenin",
    fordi kandidaten ikke påstod noget - fundet ved A/B-måling 10-08-2026.
    Alkoholfri og almindelig er to forskellige varer, der står side om side på
    hylden, og et manglende ord i det korte navn gør dem ikke ens."""
    for dim, (rema_flag, cand_flag) in enumerate(zip(rema_variants, cand_variants)):
        if dim in _SYMMETRIC_VARIANT_DIMS:
            if bool(rema_flag) != bool(cand_flag):
                return False
        elif cand_flag and not rema_flag:
            return False
    return True


def annotate_match_signals(product: dict) -> dict | None:
    """Sæt alle precomputede matchsignaler på en butiksvare, in-place.

    Kilden er varens ``name``, ``brand``, ``weight`` og ``Kategori``. Returnerer
    varen, eller None hvis den er ikke-mad/tobak (``unify_category`` -> None) og
    dermed hverken må matches eller vises.

    Ligger som selvstændig funktion, fordi tre steder skal udlede PRÆCIS de
    samme signaler: butiks-loaderen (load_store_comparison_data), fase 2/2b's
    kandidat-indeks og måle-harnesset (scripts/eval-matching.py). Da signalerne
    er gates, ville selv en lille afvigelse mellem to kopier gøre en måling
    misvisende frem for forkert-og-tydelig.

    Bemærk hvilke felter der indgår hvor - forskellene er bevidste og hver især
    fundet ved en reel fejlmatch:
      * brandfeltet indgår i smag, form, procent, variant og kødtype. Lidl
        lægger fedtprocenten i producent-feltet ("MADVÆRKET Hakket oksekød" /
        "14-18 % fedt."), og Rema lægger smag dér ("ARLA, SMAG AF CHOKOLADE").
      * stk-antal læses af vægtfeltet med fallback til navnet ("Avocado 3 Stk.").
    """
    name_str = str(product.get('name') or '')
    brand_str = str(product.get('brand') or '')
    weight_str = str(product.get('weight') or '')
    kategori_str = str(product.get('Kategori') or '')

    p_type = unify_category(kategori_str, name_str, brand_str)
    if p_type is None:
        return None

    text_with_brand = f"{name_str} {brand_str}"
    product['_norm_name'] = normalize_name(name_str)
    product['_weight_g'] = parse_weight_to_grams(weight_str)
    product['_stk_count'] = _stk_count_of(weight_str, name_str)
    product['_type'] = p_type
    product['_flavors'] = get_product_flavors(text_with_brand)
    # Kødtype læste tidligere KUN navnet, mens smag/form/procent/variant alle
    # læste navn+brand. Den inkonsistens gjorde kød-gaten blind for de feeds,
    # der lægger varetypen i producent-feltet.
    product['_meats'] = get_meat_types(text_with_brand)
    product['_forms'] = get_product_form(text_with_brand)
    product['_pcts'] = get_product_percents(text_with_brand)
    product['_variants'] = _variant_flags(name_str, '', brand_str)
    product['_is_pl'] = is_private_label(brand_str, name_str)
    return product


def cross_store_tokens(norm_name: str) -> set:
    """Blokerings-tokens for fase 2/2b (ord på mindst 3 tegn)."""
    return {t for t in (norm_name or '').split() if len(t) >= 3}


# Produktfoto: to forskellige Hamming-grænser, fordi billedsignalet gør to
# forskellige ting.
#
# _PHOTO_SAME_MAX_DIST (4) = "samme pakning fotograferet to gange". Bruges til
# at LEMPE andre gates og til at åbne token-blokeringen. Her SKAL grænsen være
# stram: den overtrumfer tekstbeviset, så den må kun fyre på nær-identitet.
#
# _PHOTO_REJECT_MAX_DIST (20) = "så forskellige at det ikke er samme vare".
# Bruges til at AFVISE. Her var 4 en alvorlig fejlkalibrering.
#
# Den oprindelige kalibrering (25.442 EAN-verificerede positive) gav
# "afstand <=4: 99,5 % korrekte" og konkluderede at klippet lå ved 4. Kommentaren
# noterede at Dagrofa navngiver billedfiler efter EAN, og kontrollerede for
# Dagrofa↔Dagrofa. Men Salling↔Salling har præcis samme egenskab og er den
# STØRRE gruppe - og den blev ikke kontrolleret. Målt på produktionscachen
# 29-08-2026 deler 12.464 af 15.168 samme-familie-par bogstavelig talt samme
# billed-URL, så deres afstand er 0 pr. konstruktion.
#
# Deler man på feed-familie (salling/dagrofa/coop deler feed internt), falder
# billedet fra hinanden som "samme vare"-mål:
#
#   samme feed      : median afstand  0   -   99,4 % under 4
#   på tværs af feed: median afstand 22   -    4,3 % under 4
#
# Afvisning ved 4 lukkede altså 95,7 % af de beviseligt korrekte kryds-feed-par
# ude - og det er præcis dem fuzzy-matchingen findes for, da samme-feed-parrene
# allerede er grupperet af stage 1 på EAN. Målt effekt af at flytte
# afvisningen til 20 (kryds-feed, 8.799 positive / 1.942 hårde negative):
#
#   klip  4:  recall  1,2 %   precision 56,2 %      <- før
#   klip 12:  recall  3,9 %   precision 80,2 %
#   klip 16:  recall  6,2 %   precision 84,7 %
#   klip 20:  recall 10,6 %   precision 89,3 %      <- nu
#   klip 24:  recall 14,8 %   precision 88,0 %
#
# Bemærk at BEGGE tal forbedres. Det er ikke et sædvanligt precision/recall-
# bytte: den gamle konfiguration afviste overvejende korrekte par og accepterede
# overvejende par UDEN foto på begge sider, hvor precision kun er 33,9 %.
# Samme-feed-segmentet berøres knap (recall 96,0 -> 96,4 %, precision
# 99,6 -> 98,9 %). Efterprøv med scripts/eval-matching.py.
_PHOTO_SAME_MAX_DIST = 4
_PHOTO_REJECT_MAX_DIST = 20

# Kg-pris: hvor meget må enhedsprisen afvige, før det ikke er samme vare?
# Kalibreret mod EAN-verificerede par: forholdet overstiger 3 for kun 0,2 % af
# de samme varer, men for 7,8 % af de forskellige.
_KG_PRICE_MAX_RATIO = 3.0


def photo_distance(base_p: dict, cand_p: dict) -> int | None:
    """Hamming-afstand mellem to produktfotos, eller None hvis en mangler."""
    a = base_p.get('_hash_int')
    b = cand_p.get('_hash_int')
    if a is None or b is None:
        return None
    return (a ^ b).bit_count()


# Mærketokens kortere end dette er i praksis ikke mærker: scrapernes
# extract_producer tager første ord i varenavnet for 6 af 14 butikker, hvilket
# giver stumper som 'Dg', 'Lb', 'Kk' og 'A B'. De kan ikke bære en afvisning.
# Grænsen er en midlertidig stedfortræder for at markere afledte mærker
# eksplicit ved kilden (se planens Etape 1.7 / 3).
_MIN_REAL_BRAND_LEN = 4

# Ord der optræder i producent-feltet, men aldrig er et mærke.
_BRAND_NOISE: frozenset = frozenset({
    'dansk', 'danske', 'rod', 'gron', 'frisk', 'ny', 'nye', 'stor', 'lille',
    'hel', 'hele', 'klasse', 'str', 'flere', 'pak', 'pakke', 'stk', 'ost',
    'okologisk', 'italiensk', 'spansk', 'graesk', 'poke', 'med', 'uden',
})


def _initial_runs(words: list) -> set:
    """Initialer for enhver sammenhængende ordsekvens på mindst to ord.

    "arla karolines køkken" giver bl.a. 'ak', 'akk' og 'kk', så Dagrofas
    forkortelse "Kk" kan genkendes som samme mærke.
    """
    out = set()
    for i in range(len(words)):
        for j in range(i + 2, len(words) + 1):
            out.add(''.join(w[0] for w in words[i:j]))
    return out


def brands_conflict(name_a: str, brand_a: str, name_b: str, brand_b: str) -> bool:
    """True kun når begge sider bærer et ÆGTE nationalt mærke, der modsiger hinanden.

    Fase 2/2b brugte hidtil kun brandet via PL-klassen, så to forskellige
    nationale mærker hverken blev belønnet eller straffet. Målt i
    produktionscachen gav det 1.333 accepterede par mellem klart forskellige
    mærker ("Jordbærmarmelade"/Den Gamle Fabrik sammen med St. Dalfour og
    Easis på ét kort; "Sorte oliven"/Barral med "Sorte sesamfrø"/Kilic).

    Gaten er bevidst KONSERVATIV, fordi producent-feltet er upålideligt:
    Dagrofa og Coop forkorter ("D.G.F", "Steff-H", "Kk"), og for 6 af 14
    butikker er feltet slet ikke et mærke, men første ord i varenavnet. Derfor
    afvises kun, når ingen af følgende holder:

      * en side er et eget mærke (PL↔PL på tværs af kæder er tilsigtede match)
      * mærket kan genfindes i modpartens navn+brand
      * navnene ligner hinanden (fuzzy >= 0,70)
      * fælles første ord (>=4 tegn) - "Steff-H" / "Steff Houlberg"
      * fælles præfiks (>=5 tegn) - "Danonino" / "Danone"
      * forkortelsen svarer til initialerne af en ordsekvens - "Kk" /
        "Arla Karolines Køkken", "D.G.F" / "Den Gamle Fabrik"

    Målt effekt (guldsæt, oven på pHash-gaten): falsk-positive 2.142 -> 1.628,
    mod 61 tabte sande match.
    """
    if is_private_label(brand_a, name_a) or is_private_label(brand_b, name_b):
        return False

    ba, bb = normalize_name(brand_a), normalize_name(brand_b)
    if not ba or not bb or ba == bb:
        return False
    # Er "mærket" identisk med varenavnet, er producent-feltet bare navnet igen.
    if ba == normalize_name(name_a) or bb == normalize_name(name_b):
        return False
    if ba in _BRAND_NOISE or bb in _BRAND_NOISE:
        return False

    flat_a = ba.replace(' ', '').replace('-', '')
    flat_b = bb.replace(' ', '').replace('-', '')
    if len(flat_a) < _MIN_REAL_BRAND_LEN or len(flat_b) < _MIN_REAL_BRAND_LEN:
        return False

    text_a = normalize_name(f'{name_a} {brand_a}')
    text_b = normalize_name(f'{name_b} {brand_b}')
    if re.search(r'\b' + re.escape(ba), text_b) or re.search(r'\b' + re.escape(bb), text_a):
        return False
    if fuzzy_score(ba, bb) >= 0.70:
        return False

    words_a, words_b = ba.split(), bb.split()
    if words_a and words_b and len(words_a[0]) >= 4 and words_a[0] == words_b[0]:
        return False
    # Et betydningsbærende ord af det ene mærke genfundet i modpartens tekst.
    # Flerords-mærker matcher ellers ikke som helhed: Rema-titlen "KLOVBORG 45+"
    # mødte kandidatens mærke "Arla Klovborg", og hele strengen 'arla klovborg'
    # findes ikke i Rema-teksten - men 'klovborg' gør. Kravet om mindst 5 tegn
    # holder generiske ord ('food', 'dansk') ude.
    for words, other_text in ((words_a, text_b), (words_b, text_a)):
        for w in words:
            if len(w) >= 5 and w not in _BRAND_NOISE and re.search(r'\b' + re.escape(w), other_text):
                return False
    short, long_ = (flat_a, flat_b) if len(flat_a) <= len(flat_b) else (flat_b, flat_a)
    common = 0
    for x, y in zip(short, long_):
        if x != y:
            break
        common += 1
    if common >= 5:
        return False
    for flat, other_words in ((flat_a, words_b), (flat_b, words_a)):
        for initials in _initial_runs(other_words):
            if len(initials) >= 3 and flat.startswith(initials):
                return False
    if flat_a in _initial_runs(words_b) or flat_b in _initial_runs(words_a):
        return False
    return True


# ---------------------------------------------------------------------------
# Match-telemetri
# ---------------------------------------------------------------------------
# Hidtil gemte motoren intet om SINE EGNE beslutninger: ingen score, ingen fase,
# ingen gate-årsag. Spørgsmålet "hvorfor blev X matchet med Y?" kunne kun
# besvares ved at genkøre koden i en debugger med de rigtige to varer i hånden.
#
# Sporet er bevidst tændt som standard. CLAUDE.md dokumenterer den modsatte fejl
# fra staging: logning man skal tænde bevidst, findes ikke når man har brug for
# den. Advarslen mod logning dér gælder EDGE-workeren, hvor mængden skalerer med
# trafikken - updateren er et natligt batch-job, der kører én gang, og filen
# ender på nogle få MB. Sæt MATCH_TRACE=0 for at slå fra.
_MATCH_TRACE_ENABLED = os.getenv('MATCH_TRACE', '1') not in ('0', 'false', 'False')
_match_trace: list = []
_gate_stats: dict = {}


def record_gate_outcome(reason: str) -> None:
    """Tæl hvilken gate der afviste et par (tom streng = accepteret)."""
    if _MATCH_TRACE_ENABLED:
        _gate_stats[reason] = _gate_stats.get(reason, 0) + 1


def record_match(phase: str, base_key: str, base_p: dict,
                 target_key: str, target_p: dict, score: float) -> None:
    """Gem beviset bag ét accepteret match."""
    if not _MATCH_TRACE_ENABLED:
        return
    _match_trace.append({
        'fase': phase,
        'base': f"{base_key}:{base_p.get('name', '')}",
        'match': f"{target_key}:{target_p.get('name', '')}",
        'navnescore': round(float(score), 3),
        'foto_afstand': photo_distance(base_p, target_p),
        'vaegt': [base_p.get('_weight_g'), target_p.get('_weight_g')],
        'ean': [base_p.get('ean') or '', target_p.get('ean') or ''],
        'pris': [base_p.get('price'), target_p.get('price')],
    })


def flush_match_trace(path: str = 'data/match_trace.jsonl') -> None:
    """Skriv sporet til disk og log gate-statistikken.

    Filen er gitignoreret - den er et diagnoseværktøj, ikke en artefakt.
    """
    if not _MATCH_TRACE_ENABLED:
        return
    if _gate_stats:
        total = sum(_gate_stats.values())
        top = sorted(_gate_stats.items(), key=lambda x: -x[1])[:10]
        logger.info("Gate-statistik (%d vurderede par): %s", total,
                    ', '.join(f"{k or 'accepteret'}={v}" for k, v in top))
    if not _match_trace:
        return
    try:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            for row in _match_trace:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
        logger.info("Match-telemetri: skrev %d match til %s", len(_match_trace), path)
    except OSError as e:
        # Et manglende diagnosespor må aldrig vælte nattens kørsel.
        logger.warning("Kunne ikke skrive match-telemetri: %s", e)


# Token-IDF over hele katalogets varenavne. Bygges én gang pr. kørsel af
# build_token_idf() og bruges af _distinctive_token_shared nedenfor. Tom dict
# = gaten er inaktiv, hvilket er det sikre udgangspunkt (fx i enhedstests og
# hvis indlæsningen fejler).
_TOKEN_IDF: dict = {}
_TOKEN_IDF_DEFAULT = 0.0


def build_token_idf(store_data: dict) -> None:
    """Beregn hvor sjældent hvert ord er i katalogets varenavne.

    fuzzy_score er tegnbaseret og vægter alle ord ens, så "Økologiske" tæller
    lige så meget som "Bananer". IDF gør forskellen eksplicit: 'fedt' optræder i
    822 varenavne, mens et mærke- eller sortsnavn typisk optræder i en håndfuld.
    """
    global _TOKEN_IDF, _TOKEN_IDF_DEFAULT
    doc_freq: dict = {}
    docs = 0
    for products, _t, _h, _e in store_data.values():
        for p in products:
            docs += 1
            for token in set(p.get('_norm_name', '').split()):
                if len(token) >= 3:
                    doc_freq[token] = doc_freq.get(token, 0) + 1
    if not docs:
        return
    _TOKEN_IDF = {t: math.log((docs + 1) / (n + 1)) for t, n in doc_freq.items()}
    # Ord vi aldrig har set er maksimalt distinktive.
    _TOKEN_IDF_DEFAULT = math.log(docs + 1)
    logger.info("Token-IDF bygget over %d varenavne (%d unikke ord)", docs, len(_TOKEN_IDF))


def _token_idf(token: str) -> float:
    return _TOKEN_IDF.get(token, _TOKEN_IDF_DEFAULT)


def distinctive_token_shared(tokens_a: set, tokens_b: set) -> bool:
    """Kan hver sides MEST distinktive ord genfindes hos modparten?

    Bruges kun når billed-signalet er utilgængeligt (se cross_store_pair_verdict).
    Begrundelsen er målt: med foto på begge sider koster gaten 515 sande match
    for kun 31 færre fejl - billedet gør allerede arbejdet. Uden foto fjerner den
    455 fejl for 48 tabte match, og løfter precision i det segment fra 27,6 % til
    35,6 %.

    Delvise træf tæller med, så danske sammensætninger ikke straffes
    ("jordbærmarmelade" indeholder "jordbær").

    Er den ene ordmængde en DELMÆNGDE af den anden, er gaten inaktiv. Det er
    den tersere-navn-situation, og hele motoren hviler på princippet om, at
    tavshed ikke er en modsigelse ("FRIKADELLER" må stadig kunne møde
    "Frikadeller m. svinekød", jf. README § Product matching). En symmetrisk
    udgave uden denne undtagelse måler 0,6 point højere i precision, men den
    afviser de par af den forkerte grund - og de reelle forskelle i
    delmængde-tilfældene fanges allerede af variant-, smags- og procentgaten.
    """
    if not _TOKEN_IDF or not tokens_a or not tokens_b:
        return True
    if tokens_a <= tokens_b or tokens_b <= tokens_a:
        return True
    # Kædernes egne mærkeord må ikke vælges som "det mest distinktive ord".
    # De ER sjældne i katalogets navne og vinder derfor IDF-konkurrencen - men
    # de er netop det ord der SKAL være forskelligt, når to kæders private
    # label-udgave af samme vare sammenlignes. Målt: "Xtra Havregryn" mod
    # "Budget Havregryn" blev afvist på 'distinktivt ord', fordi 'xtra' og
    # 'budget' er de mest distinktive ord - stik imod at brands_conflict
    # udtrykkeligt dokumenterer PL↔PL på tværs af kæder som TILSIGTEDE match.
    tokens_a = tokens_a - _PL_BRAND_TOKENS or tokens_a
    tokens_b = tokens_b - _PL_BRAND_TOKENS or tokens_b
    if tokens_a <= tokens_b or tokens_b <= tokens_a:
        return True
    for src, dst in ((tokens_a, tokens_b), (tokens_b, tokens_a)):
        # Tie-break på selve ordet. Uden det vælger max() vilkårligt blandt
        # ord med samme IDF, og da kilden er en mængde, kan valget skifte
        # mellem kørsler - så ville nattens matchresultat ikke være
        # reproducerbart for de par, hvor to ord er lige sjældne.
        top = max(src, key=lambda t: (_token_idf(t), t))
        if top in dst:
            continue
        if any(top in t or t in top for t in dst):
            continue
        return False
    return True


def stk_validates_pack_size(base_stk, cand_stk) -> bool:
    """Har stk-antallet reelt bekræftet, at de to varer har samme pakkestørrelse?

    Bruges KUN af vægtløs-undtagelsen, ikke af selve stk-gaten: "1 stk" mod
    "6 stk" er stadig en gyldig afvisning, og den regel er uændret.

    Men "1 stk" mod "1 stk" bekræfter ingenting - stort set alt er 1 stk, så
    lighed er default og ikke bevis. Alligevel talte det som "pakkestørrelsen
    er valideret" og sænkede navnegulvet fra 0,75 til 0,65 for et vægtløst par,
    hvor navnet ellers skulle bære matchet alene. Målt i produktionscachen:
    102 af de 214 stk-validerede par var netop 1 mod 1.

    Kræver derfor at mindst én side angiver et rigtigt flertal.
    """
    if base_stk is None or cand_stk is None:
        return False
    return max(base_stk, cand_stk) > 1


# Afvisningsårsager fra cross_store_pair_verdict. Bruges af måle-harnesset og
# af match-telemetrien til at vise HVILKEN gate der afviste et par - uden det
# kan spørgsmålet "hvorfor matchede X ikke Y?" ikke besvares uden at genkøre
# koden i en debugger.
VERDICT_ACCEPT = ''


def cross_store_pair_verdict(base_p: dict, target_p: dict, base_norm: str,
                             base_tokens: set) -> tuple[bool, float, str]:
    """Hele fase 2/2b's gate-kæde for ét (base, target)-par.

    Returnerer ``(accepteret, navnescore, afvisningsårsag)``. Navnescoren er 0.0
    når parret blev afvist, før scoren overhovedet blev beregnet.

    Fase 2 og fase 2b havde hver sin ordrette kopi af denne kæde. Kopierne var
    allerede drevet fra hinanden én gang (længde-forfilteret manglede i 2b indtil
    matchmotor-revisionen 2026-08-16, fund H7), og en gate der kun findes ét af
    de to steder er præcis den slags fejl ingen opdager. Rækkefølgen er bevaret
    nøjagtigt: de to billigste og mest afvisende filtre først, derefter de rene
    attribut-gates, og først til sidst navnescoren og det der afhænger af den.

    ``base_norm``/``base_tokens`` gives med udefra, fordi kalderen hejser dem ud
    af sin inderloop - de er de samme værdier som base_p bærer.
    """
    target_norm = target_p.get('_norm_name', '')

    # Produktfoto beregnes FØRST, fordi et nær-identisk billede også skal kunne
    # åbne selve blokeringen. Det er billigt (to opslag, XOR, popcount) - på
    # niveau med mængde-snittet nedenfor.
    #
    # Uden dette var billed-signalet magtesløst netop dér hvor det er stærkest:
    # butikkerne skriver samme vare vidt forskelligt ("Paradiso Kongeasp.Grøn11c"
    # ↔ "Hele grønne asparges"), og deler navnene ikke ét ord på 3+ tegn, blev
    # parret afvist før nogen gate så det. Målt på guldsættet var "ingen fælles
    # ord" den STØRSTE enkeltårsag til tabte sande match (33,8 %).
    dist = photo_distance(base_p, target_p)

    # Billedsignalet har TRE forskellige roller, og de tåler ikke samme grænse.
    # At bruge én konstant til alle tre var kernen i fejlkalibreringen:
    #
    #   near_identical_photo (<=4)  - LEMPER hårde gates og navnegulvet. Skal
    #       være stram: den overtrumfer tekstbeviset og må kun fyre på
    #       nær-identitet.
    #   photo_compatible (<=20)     - "fotoerne modsiger ikke hinanden". Åbner
    #       blokeringen (kandidatfindingen) og lemper TYPE-gaten. Begge dele er
    #       sikre ved den brede grænse, fordi alle rigtige gates stadig kører
    #       bagefter - blokeringen er ren kandidatudvælgelse, og typen er selv
    #       kun et gæt (unify_category udleder kategorien af navnet, når
    #       butikken ingen leverer; Salling hardkoder feltet til 'Katalog').
    #
    # Målt på kryds-feed-segmentet, oven på de øvrige rettelser:
    #   åbner 4  / lemper 4 / type 4 :  recall 11,9 %   precision 89,1 %
    #   åbner 20 / lemper 4 / type 4 :  recall 13,2 %   precision 90,2 %
    #   åbner 20 / lemper 4 / type 20:  recall 17,6 %   precision 91,5 %   <- nu
    # Samme-feed-segmentet er uændret i alle tre (97,9 % / 98,8 %).
    near_identical_photo = dist is not None and dist <= _PHOTO_SAME_MAX_DIST
    photo_compatible = dist is not None and dist <= _PHOTO_REJECT_MAX_DIST

    if not photo_compatible:
        if _cross_store_length_prefilter(len(base_norm), len(target_norm)):
            return False, 0.0, 'længde'
        if not base_tokens.intersection(target_p.get('_cross_match_tokens', ())):
            return False, 0.0, 'ingen fælles ord'

    base_weight = base_p.get('_weight_g')
    target_weight = target_p.get('_weight_g')
    if not weights_compatible(base_weight, target_weight):
        return False, 0.0, 'vægt'

    base_stk = base_p.get('_stk_count')
    target_stk = target_p.get('_stk_count')
    if base_stk is not None and target_stk is not None and base_stk != target_stk:
        return False, 0.0, 'stk'

    if base_p['_variants'] != target_p['_variants']:
        return False, 0.0, 'variant'
    # Procent-gate (fedt-/alkohol-%): kun aktiv når begge sider angiver
    # procenter, jf. _percents_match.
    if not _percents_match(base_p['_pcts'], target_p['_pcts']):
        return False, 0.0, 'procent'
    # Smag og form vurderes SYMMETRISK her (modsat Rema-sporets ensidige
    # cand<=base): begge sider er korte butiksnavne uden rig beskrivelse, så en
    # smag nævnt af kun én side er en reel forskel ("Cherry blommetomater" ≠
    # "Blommetomater"), uanset hvem der tilfældigvis er base. Den ensidige
    # udgave lod resultatet afhænge af behandlingsrækkefølgen - fund H3.
    if base_p['_flavors'] != target_p['_flavors']:
        return False, 0.0, 'smag'
    if base_p['_forms'] != target_p['_forms']:
        return False, 0.0, 'form'
    if not _meats_match(base_p['_meats'], target_p['_meats']):
        return False, 0.0, 'kødtype'

    # Brand-gate: kun aktiv når begge sider bærer et ægte nationalt mærke, der
    # modsiger hinanden (se brands_conflict for hvorfor den er konservativ).
    if brands_conflict(base_p.get('name', ''), base_p.get('brand', ''),
                       target_p.get('name', ''), target_p.get('brand', '')):
        return False, 0.0, 'mærke'

    name_score = fuzzy_score(base_norm, target_norm)

    # Type-gate med eskalering: butikskategorier er støjede (samme marmelade
    # ligger under "Kolonial" hos én butik og "Frost" hos en anden), så et
    # mismatch afviser kun, når navnet ikke er stærkt nok til at bære matchet.
    #
    # Et foto der ikke modsiger parret lemper også her - og her bruges den
    # BREDE grænse (photo_compatible, <=20), ikke nær-identitet. Salling-
    # butikkerne leverer ingen rigtig kategori (feltet er hardkodet 'Katalog'),
    # så unify_category gætter den ud fra varenavnet - og gættet skifter med
    # brandordet: "Tørsleffs Kondenseret Mælk" blev Kolonial, mens "Kondenseret
    # mælk" blev Køl. Typen er altså det svageste signal i hele kæden, og et
    # foto der overhovedet er foreneligt vejer tungere end et navnebaseret gæt.
    #
    # Målt (kryds-feed): lempelse ved <=4 gav recall 13,2 % / precision 90,2 %;
    # ved <=20 gav den recall 17,6 % / precision 91,5 %. BEGGE tal forbedres,
    # fordi de par gaten fjernede overvejende var korrekte. Samme-feed uændret.
    base_type = base_p['_type']
    target_type = target_p['_type']
    if (not types_compatible(base_type, target_type)
            and name_score < 0.80 and not photo_compatible):
        return False, name_score, 'type'

    if base_p['_is_pl'] != target_p['_is_pl'] and name_score < 0.70:
        return False, name_score, 'brand-klasse'

    # Navnegulv - lempes af et nær-identisk produktfoto. Butikkerne skriver
    # samme vare vidt forskelligt ("Paradiso Kongeasp.Grøn11c" ↔ "Hele grønne
    # asparges"), og dér er billedet stærkere bevis end teksten. Målt på de par
    # der i dag afvises PRÆCIS her: ved afstand <=4 er 94,7 % af dem korrekte
    # (126 sande mod 7 falske).
    if name_score < _CROSS_STORE_NAME_FLOOR and not near_identical_photo:
        return False, name_score, 'navnegulv'

    # Vægtløst par (typisk Dagrofa): mangler bare én side vægt, kan vægt-gaten
    # intet validere. Gaten krævede før at navnet alene bar matchet med
    # name_score >= 0.75. Den antagelse holder ikke, og målingen er entydig:
    # på kryds-feed-par er mediannavnescoren for KORREKTE par 0,609 og for
    # FORKERTE par 0,720. Navnescoren er altså svagt ANTI-korreleret med
    # korrekthed i netop det segment, gaten skulle beskytte - to butikker
    # beskriver samme vare vidt forskelligt ("Gestus Hvidløg I Kryd.Olie" mod
    # "Hvidløg i olie m. krydderier"), mens to FORSKELLIGE private label-varer
    # får næsten identiske generiske navne ("Fp Jordbærmarmelade" mod "Gestus
    # Jordbærmarmelade").
    #
    # Kravet er derfor byttet fra tekstlighed til POSITIVT BEVIS: parret skal
    # bære mindst ét uafhængigt signal, der faktisk korrelerer med at være
    # samme vare. Ingen af de fem nedenfor er anti-korreleret, som navnet er.
    # Målt (kryds-feed, oven på billed-klip 20): recall 10,6 -> 30,3 %,
    # precision 89,3 -> 93,5 %. Samme-feed: recall 96,4 -> 97,9 %, precision
    # 98,9 -> 98,7 %. Begge segmenter forbedres på recall.
    if not base_weight or not target_weight:
        evidence = (
            # 1. Fotoet siger samme pakning.
            near_identical_photo
            # 2. Stk-antallet har reelt valideret pakkestørrelsen ("1 stk" mod
            #    "1 stk" gør ikke, se stk_validates_pack_size).
            or stk_validates_pack_size(base_stk, target_stk)
            # 3. Enhedsprisen passer. Uafhængig af pakkestørrelse og derfor
            #    netop brugbar når vægten mangler.
            or (base_p.get('kg_price') and target_p.get('kg_price'))
            # 4. Navnenes mest distinktive ord genfindes hos modparten.
            or distinctive_token_shared(base_tokens,
                                        target_p.get('_cross_match_tokens') or set())
            # 5. Løsvarer er vægtløse i ALLE butikker, og de korte navne
            #    ("BANANER" mod "Økologiske bananer") scorer lavt uden at være
            #    tvivlsomme.
            or (base_type == CAT_FRUGT_GROENT and target_type == CAT_FRUGT_GROENT)
        )
        # Uden ét eneste bekræftende signal må navnet stadig bære matchet alene,
        # og så gælder det gamle, høje gulv.
        if not evidence and name_score < _WEIGHTLESS_NAME_FLOOR:
            return False, name_score, 'vægtløst par'

    # Pris-sanity: samme vare koster ikke 5× mere i en anden butik.
    try:
        base_price = float(base_p['price'])
        target_price = float(target_p['price'])
        if target_price > 5.0 * base_price or target_price * 5.0 < base_price:
            return False, name_score, 'pris'
    except (TypeError, ValueError, KeyError):
        pass

    # Kg-pris: enhedsnormaliseret pris. Modsat pris-sanityen ovenfor er den
    # uafhængig af pakkestørrelse, så den fanger par hvor navnene passer, men
    # varen er en helt anden kvalitet/type. Kalibreret mod guldsættet:
    # forholdet overstiger 3 for kun 0,2 % af de EAN-verificerede samme-varer,
    # men for 7,8 % af de forskellige.
    #
    # Gevinsten er lille i det store billede (41 færre falsk-positive), men den
    # ligger PRÆCIS dér hvor billed-gaten er tavs - i par uden foto på begge
    # sider, som rummer 95 % af de resterende fejl - og den koster nul sande
    # match i det segment.
    base_ppk, cand_ppk = base_p.get('kg_price'), target_p.get('kg_price')
    if base_ppk and cand_ppk:
        lo, hi = sorted((float(base_ppk), float(cand_ppk)))
        if lo > 0 and hi / lo > _KG_PRICE_MAX_RATIO:
            return False, name_score, 'kg-pris'

    # Uden foto på begge sider er der ingen billed-gate til at fange fejlen, og
    # netop dér ligger 95 % af de resterende falsk-positive. Kræv derfor, at
    # navnets mest distinktive ord kan genfindes hos modparten. Gaten er
    # bevidst IKKE aktiv når fotos findes: dér koster den 515 sande match for
    # kun 31 færre fejl, fordi billedet allerede har afgjort sagen.
    if dist is None and not distinctive_token_shared(
            base_tokens, target_p.get('_cross_match_tokens') or set()):
        return False, name_score, 'distinktivt ord'

    # Billed-gate til sidst: har BEGGE sider et foto, og er de tydeligt
    # forskellige, er det ikke samme vare - uanset hvor godt navnet passer.
    #
    # Bevidst placeret sidst, ikke som blokering: et manglende foto må aldrig
    # afvise noget (17 % af de negative har ikke hash på begge sider), og de
    # billige tekst-gates skal stadig have lov at afvise først, så
    # afvisningsårsagen forbliver den mest informative.
    #
    # Grænsen er _PHOTO_REJECT_MAX_DIST (20), IKKE _PHOTO_SAME_MAX_DIST (4).
    # De to spørgsmål er forskellige: "er det bevisligt samme pakning?" (4) og
    # "er de så forskellige at det udelukker samme vare?" (20). At bruge 4 til
    # begge afviste 95,7 % af de korrekte par på tværs af feeds - se
    # konstanternes definition ovenfor.
    if dist is not None and dist > _PHOTO_REJECT_MAX_DIST:
        return False, name_score, 'billede'

    return True, name_score, VERDICT_ACCEPT


def _find_generic_match(rema_title, rema_description, products, token_idx, hash_list, rema_brand='', rema_weight_g=None, threshold=0.60, rema_image_hash='', rema_price=0.0, rema_ean='', rema_stk_count=None, ean_index=None, rema_category='', claimed_ids=None, rema_price_per_kg=None):
    """Token-indexed fuzzy match used by all store comparisons.

    Product stages (EAN status - see README «Product matching»):
    Stage 1 - EAN match across stores (EAN lookup only, no fuzzy).
    Stage 2 - EAN but no match (passive fuzzy target only).
    Stage 3 - No EAN (may initiate fuzzy matching).

    Rema products have no EAN, so this function always acts as a stage-3 initiator
    against comparison-store candidates (stages 1–3).

    Scoring components (all additive, evaluated after every gate below passes):
    1. Name fuzzy score          - basis 0..1 via fuzzy_score (rapidfuzz)
    2. Brand similarity boost    - up to +0.30 when brands match (e.g. Arla↔Arla)
    3. Image perceptual hash     - up to +0.40 when pHash distance is low

    Gates, in ACTUAL execution order (kept accurate - a stale gate list here
    once caused real confusion about safe reordering; see matchmotor-
    revisionen 2026-08-16, fund M9):
    0. Stage-1 EAN short-circuit: if rema_ean is set, only an exact EAN hit
       is returned (or None) - never falls through to fuzzy below.
    1. Candidate discovery: token index (>=3-char tokens) plus pHash
       neighbours (hash_list) when Rema has an image_hash.
    2. Claimed: candidates already matched by an earlier Rema product in
       this run are skipped (claimed_ids), so two distinct Rema SKUs can't
       both claim the same comparison-store listing.
    3. Percent (fedt-/alkohol-/kakao-%): symmetric when both sides state a
       percentage (see _percents_match). Never relaxed by photo - alcohol-
       free and regular beer share near-identical packaging.
    4. Meat type (okse/gris/kylling/...): symmetric like percent - when
       BOTH sides name meat types, the sets must be identical (see
       _meats_match). No photo relaxation either.
    5. Alcohol-free (the one variant dimension checked symmetrically even
       here): flag must agree regardless of silence. Never relaxed by photo.
    6. Variant (øko/laktosefri/sukkerfri/glutenfri): only rejects when the
       CANDIDATE explicitly claims an attribute the Rema product doesn't
       have (see _variants_compatible) - relaxed when the product photos
       are near-identical (dist<=4).
    7. Flavor (jordbær ≠ pære/banan osv.): same cand<=base asymmetry and
       photo relaxation as variant (see _flavors_match).
    8. Form (drik/budding/mousse/skyr/kefir/yoghurt/shake): same asymmetry
       and photo relaxation as flavor (see _forms_match). Prevents e.g. an
       Arla Protein DRINK from matching an Arla Protein PUDDING just
       because both share generic tokens like "arla"/"protein"/"choko".
    9. Weight: candidates whose unit weight differs beyond weights_compatible's
       tolerance (20g floor / 8% relative / 25%-scaled for small items) are
       skipped. Moved here (before name score) since it doesn't need it.
    10. Quantity: skip when both sides have _stk_count and they differ.
    11. Price sanity: reject if store price is >5x or <1/5x the Rema price.
    -- name_score computed here --
    12. Type: product category must match unless name_score >= 0.80 (store
        categories are noisy, e.g. the same jam under "Kolonial"/"Frost").
    13. Brand-pairing: private-label vs. national-brand mismatch rejects
        unless name_score >= 0.70.
    14. Dairy-subtype + first-token: dairy fat-type mismatch (mini/let/
        skummet/...) rejects unless photos are close; the first significant
        (>=4-char) title token must appear in the candidate name unless
        relaxed by a strong photo match (national brands) or a solid
        name_score (private-label, which has no reliable photo signal).
    15. Weightless-candidate floor: when the candidate has neither weight,
        EAN nor a comparable stk-count (typical Dagrofa/Løvbjerg), require
        name_score >= 0.75 instead of the usual floor (exempt for Frugt &
        Grønt, where loose produce is weight-less everywhere).
    16. Minimum name floor: name_score must reach >=0.50 (relaxed to >=0.30
        for a strong photo match on national brands; no such relaxation for
        private-label pairs, which lack a reliable photo signal).
    17. Final composite score (name + brand boost + image boost) must reach
        `threshold` (default 0.60).
    """
    # Stage 1: EAN lookup only - never fall through to fuzzy when EAN is set but unmatched.
    # Rema has no EAN; comparison stores use EAN cross-fill in fetch_and_parse_xml.
    if rema_ean and rema_ean not in ('', 'nan', 'None'):
        if ean_index:
            hit = ean_index.get(rema_ean)
            if hit:
                return hit
        else:
            for p in products:
                if p.get('ean') == rema_ean:
                    return p
        return None  # EAN sat men ingen match fundet → ikke fuzzy

    rema_title_norm = normalize_name(rema_title)
    rema_norms = [n for n in (rema_title_norm, normalize_name(rema_description)) if n]
    if not rema_norms:
        return None

    norm_rema_brand = normalize_name(rema_brand)
    rema_type = unify_category(str(rema_category), str(rema_title), str(rema_brand))
    base_is_pl = is_private_label(rema_brand, rema_title)
    rema_variants = _variant_flags(rema_title, rema_description, rema_brand)
    # Rema-brandfeltet bærer ofte smags-/form-info som titel+beskrivelse udelader
    # (fx brand "ARLA, SMAG AF CHOKOLADE KARAMEL" på en vare med titel "PROTEIN
    # TO GO") - uden brand her fejlvurderede smags-gaten Rema-siden som "ingen
    # smag", og afviste dermed korrekte matches mod butikker med fyldigere navne.
    rema_flavors = get_product_flavors(f"{rema_title} {rema_description} {rema_brand}")
    rema_forms = get_product_form(f"{rema_title} {rema_description} {rema_brand}")
    # Brandfeltet SKAL med, præcis som på kandidatsiden (se '_pcts' ovenfor).
    # Rema lægger ofte procenten dér og kun dér ("ALKOHOLFRI 0,0%",
    # "CARLSBERG 0,0%"), så uden brand var rema_pcts tom, procent-gaten
    # inaktiv og kryds-medlems-arbitragen blind - netop den gate README siger
    # aldrig må lempes, fordi alkoholfri og almindelig øl deler emballage.
    rema_pcts = get_product_percents(f"{rema_title} {rema_description} {rema_brand}")
    # Brandfeltet SKAL med, præcis som på kandidatsiden. annotate_match_signals
    # tilføjede det eksplicit dér ("kødtype læste tidligere KUN navnet, mens
    # smag/form/procent/variant alle læste navn+brand"), men Rema-siden blev
    # ikke rettet med - så gaten sammenlignede to forskelligt udledte mængder.
    rema_meats = get_meat_types(f"{rema_title} {rema_description} {rema_brand}")

    r_hash_int = phash_hex_to_int(rema_image_hash)

    # Token-baserede kandidater. >=3 for at matche fase 2/2b's kandidat-
    # indeks (var >=4 her, udokumenteret inkonsistens der lod korte Rema-
    # navne som "Gær"/"Løg"/"Gin"/"Ale" aldrig få en kandidatchance via
    # tekst - se matchmotor-revisionen 2026-08-16, fund M1).
    candidate_indices = set()
    primary_norm = rema_title_norm if rema_title_norm else rema_norms[0]
    for token in primary_norm.split():
        if len(token) >= 3 and token in token_idx:
            candidate_indices |= token_idx[token]

    # Fallback: include description tokens if title gave nothing
    if not candidate_indices:
        for norm in rema_norms[1:]:
            for token in norm.split():
                if len(token) >= 3 and token in token_idx:
                    candidate_indices |= token_idx[token]

    # pHash-kandidater: ekstra vej ind når navn ikke overlapper (eller som supplement)
    if r_hash_int is not None and hash_list:
        candidate_indices |= hash_candidate_indices(r_hash_int, hash_list, _HASH_CANDIDATE_MAX_DIST)

    if not candidate_indices:
        return None

    best, best_score = None, 0.0

    for i in candidate_indices:
        p = products[i]

        # Gate: allerede matchet til en tidligere Rema-vare i dette scrape -
        # forhindrer at to forskellige Rema-varer stjæler samme butiksvare.
        if claimed_ids is not None and id(p) in claimed_ids:
            continue

        dist = None
        if r_hash_int is not None:
            p_hash_int = p.get('_hash_int')
            if p_hash_int is not None:
                dist = (r_hash_int ^ p_hash_int).bit_count()

        # Næsten-identisk produktfoto (samme pakning fotograferet af begge butikker)
        # er stærkt bevis for samme vare - så variant/smag/form-gates (som kun
        # kigger på tekst) lempes her. Fanger fx Rema "Arla choko protein to go"
        # (ingen "laktosefri" nævnt noget sted) mod Bilkas fyldigere "Proteindrik
        # m. chokolade- og karamelsmag ... laktosefri" - identisk flaske, men
        # Rema-teksten er terser end kandidatens, ikke omvendt som gates'ene ellers
        # antager. En reel anden smag/variant ville give synligt anderledes emballage
        # og dermed en langt større pHash-afstand.
        near_identical_photo = dist is not None and dist <= 4

        # Gate: Procent-konflikt (fedt-%, alkohol-%, kakao-%). Bevidst UDEN
        # foto-lempelse: alkoholfri og almindelig øl deler næsten identisk
        # emballage (Tuborg Classic 4,6% ↔ 0,0%), så et godt billedmatch er
        # netop ikke bevis her.
        if not _percents_match(rema_pcts, p['_pcts']):
            continue

        # Gate: Kødtype (okse ≠ gris ≠ kylling ...). Også bevidst UDEN
        # foto-lempelse: hakket-kød-varianter deler næsten identisk
        # emballage på tværs af kødtyper.
        if not _meats_match(rema_meats, p['_meats']):
            continue

        # Gate: Mærke. Manglede helt i dette spor, mens fase 2/2b fik den -
        # og en fuld pipelinekørsel viste, at det er HER problemet sidder:
        # 1.303 af 1.322 tilbageværende par mellem to klart forskellige
        # nationale mærker lå på Rema-kort ("Jordbærmarmelade"/Den Gamle
        # Fabrik sammen med St. Dalfour OG Easis på ét kort; "Sorte oliven"/
        # Barral med "Sorte sesamfrø"/Kilic).
        #
        # brands_conflict er symmetrisk og konservativ, hvilket netop er det
        # der gør den sikker her: Rema lægger ofte smagsinfo i brandfeltet
        # ("ARLA, SMAG AF CHOKOLADE KARAMEL"), men kandidatens "Arla" kan
        # genfindes i den tekst, så parret fredes.
        if brands_conflict(rema_title, rema_brand, p.get('name', ''), p.get('brand', '')):
            continue

        # Gate: Kg-pris. Også fraværende i dette spor. Enhedsprisen er
        # uafhængig af pakkestørrelse og fanger par, hvor navnet passer, men
        # varen er en anden kvalitet.
        if rema_price_per_kg and p.get('kg_price'):
            try:
                lo, hi = sorted((float(rema_price_per_kg), float(p['kg_price'])))
                if lo > 0 and hi / lo > _KG_PRICE_MAX_RATIO:
                    continue
            except (TypeError, ValueError):
                pass

        # Gate: Variant-linjer (øko, lacto/laktosefri, sukkerfri, glutenfri).
        # Et næsten identisk foto lemper de fire første - men ALDRIG alkohol:
        # alkoholfri og almindelig deler netop emballage (README § Product
        # matching), så billedet er intet bevis dér.
        if bool(rema_variants[4]) != bool(p['_variants'][4]):
            continue
        if not near_identical_photo and not _variants_compatible(rema_variants, p['_variants']):
            continue

        # Gate: Smagsvariant (jordbær ≠ pære/banan, naturel ≠ jordbær osv.)
        if not near_identical_photo and not _flavors_match(rema_flavors, p['_flavors']):
            continue

        # Gate: Produktform (drik ≠ budding ≠ mousse osv.)
        if not near_identical_photo and not _forms_match(rema_forms, p['_forms']):
            continue

        # Gate B/B2/C flyttet hertil, FØR den dyre navne-score beregnes
        # nedenfor: ingen af de tre afhænger af name_score (i modsætning
        # til Type-gaten og Gate A, som forbliver efter), så de billige,
        # uafhængige afvisninger bør ske først - samme princip filen
        # allerede anvender eksplicit i fase 2/2b. Ændrer intet i
        # resultatet, kun arbejdsmængden. Se matchmotor-revisionen
        # 2026-08-16, fund M4.
        # Gate B: Weight
        if not weights_compatible(rema_weight_g, p.get('_weight_g')):
            continue

        # Gate B2: Stk-count - skip if both have a known stk count that differs
        if rema_stk_count is not None and p.get('_stk_count') is not None and rema_stk_count != p.get('_stk_count'):
            continue

        # Gate C: Price sanity - tosidet. En kandidat >5× dyrere ELLER >5× billigere
        # er ikke samme vare (fx Rema 6-pak øl 48 kr mod Menys enkeltdåse 7,95 kr -
        # Dagrofa-varer mangler ofte vægt, så vægt-gaten fanger det ikke).
        if rema_price and rema_price > 0:
            try:
                p_price = float(p.get('price', 0))
                if p_price > 5.0 * float(rema_price):
                    continue
                if p_price > 0 and p_price * 5.0 < float(rema_price):
                    continue
            except (TypeError, ValueError):
                pass

        # 1. Name similarity - bedste af titel og beskrivelse. Rema-titlen er ofte
        # generisk (fx "PROTEIN DRIK"), mens smag/variant kun står i beskrivelsen
        # ("Arla protein drik vanilje laktosefri") - kun titlen giver falske afvisninger.
        name_score = max(fuzzy_score(rn, p['_norm_name']) for rn in rema_norms)

        # Gate: Product type - butikkernes kategorier er støjede (samme marmelade
        # ligger under "Kolonial" hos Rema og "Frost" hos Salling), så mismatch
        # afviser kun når navnescoren ikke er høj nok til at bære matchet alene.
        if not types_compatible(rema_type, p['_type']) and name_score < 0.80:
            continue

        # Gate A: Brand-pairing
        p_is_pl = p['_is_pl']
        both_pl = base_is_pl and p_is_pl
        if base_is_pl != p_is_pl and name_score < 0.70:
            continue
        # Egne mærker på tværs af kæder (Rema ↔ Salling/First Price/…) er
        # "samme brand-klasse" selvom brandteksten ikke ligner - bruges nedenfor
        # i stedet for pHash, fordi PL-emballager aldrig er nær-identiske.
        brands_align = both_pl or (
            fuzzy_score(norm_rema_brand, normalize_name(p.get('brand', ''))) >= 0.75
        )

        # Gate D: Dairy variant + first-token checks
        if rema_title_norm:
            dairy_types = ['mini', 'let', 'skummet', 'sod', 'piske', 'kærne', 'kær']
            rema_dairy = next((d for d in dairy_types if d in rema_title_norm), None)
            p_dairy    = next((d for d in dairy_types if d in p['_norm_name']), None)
            if rema_dairy and p_dairy and rema_dairy != p_dairy:
                # Tillad at overskrive, hvis billedet er næsten identisk
                # (gælder nationale mærker - PL-pakker ligner ikke hinanden).
                if both_pl or dist is None or dist > 5:
                    continue

            title_tokens_ordered = [t for t in rema_title_norm.split() if len(t) >= 4]
            if title_tokens_ordered and title_tokens_ordered[0] not in p['_norm_name']:
                if both_pl:
                    # PL ↔ PL: ingen pHash-genvej. name_score dækker allerede
                    # Rema-titel + beskrivelse - kræv solid tekstlighed.
                    if name_score < 0.60:
                        continue
                else:
                    # Nationale mærker: slæk første-token hvis billederne matcher.
                    # dist <= 12 kræver samme reelle brand (BUKO "Rejeost" ↔ Buko
                    # "Smøreost m. rejer" er ok) - uden brand-belæg kræves dist <= 8,
                    # da svag billedlighed alene bar urelaterede navne over tærsklen
                    # (PL-boost 0.30 + billede matchede fx lagkagebunde mod kylling).
                    if dist is None or dist > 12:
                        continue
                    if dist > 8 and not brands_align:
                        continue

        # Gate: vægt- og EAN-løs kandidat (typisk Dagrofa/Løvbjerg) - hverken
        # vægt-, stk- eller EAN-retro-gates kan validere matchet, så navnet må
        # bære det næsten alene: kræv markant højere navnescore. Lempes kun
        # ved nær-identisk produktfoto (nationale mærker) eller når stk-antallet
        # reelt har valideret pakkestørrelsen - "1 stk" mod "1 stk" gør ikke,
        # se stk_validates_pack_size. Frugt & grønt er undtaget: løsvarer er
        # vægtløse i ALLE butikker, og de korte navne ("BANANER" ↔ "Økologiske
        # bananer") scorer lavt uden at være tvivlsomme.
        if (name_score < 0.75 and not near_identical_photo
                and not p.get('_weight_g') and not p.get('ean')
                and not stk_validates_pack_size(rema_stk_count, p.get('_stk_count'))
                and not (rema_type == CAT_FRUGT_GROENT and p['_type'] == CAT_FRUGT_GROENT)):
            continue

        # Minimum name gate: boosts alone must not trigger a match.
        # Nationale mærker: brand-betinget billed-lempelse. PL ↔ PL: ingen
        # billed-genvej - emballagerne ligner ikke hinanden på tværs af kæder.
        if name_score < 0.50:
            if both_pl:
                continue
            if dist is None or dist > 12:
                continue
            if dist > 8 and not brands_align:
                continue
            if name_score < 0.30:
                # Men en meget lille tekst-score afvises stadig, trods godt billede
                continue

        # 2. Brand similarity boost (up to +0.30)
        # Begge sider normaliseres. Før mødte et normaliseret Rema-brand et
        # RÅT kandidat-brand, så 'arla' vs 'Arla' gav 0,75 i stedet for 1,0 -
        # boostet blev 0,225 frem for 0,30, og par lige omkring tærsklen faldt
        # igennem uden grund. Værre for æ/ø/å ('Änglamark').
        brand_sim   = 1.0 if both_pl else fuzzy_score(norm_rema_brand, normalize_name(p.get('brand', '')))
        brand_boost = 0.30 * brand_sim

        # 3. Image perceptual hash boost
        image_boost = 0.0
        if dist is not None:
            # Båndene mødtes ikke: dist 7 gav 0,05, dist 8 gav 0,00 og dist 9
            # gav 0,08 - et DÅRLIGERE billedmatch scorede altså højere end et
            # bedre. Intentionen med to bånd er "meget tæt = stort boost,
            # rimeligt tæt = lille boost", så de er nu gjort sammenhængende:
            # 0,40 ved afstand 0, 0,20 ved 8 (hvor båndene mødes) og 0 ved 15.
            # Boostet falder dermed monotont hele vejen.
            if dist <= 8:
                image_boost = 0.20 + 0.20 * (8 - dist) / 8.0
            elif dist <= 15:
                image_boost = 0.20 * (15 - dist) / 7.0

        score = name_score + brand_boost + image_boost
        if score > best_score:
            best_score = score
            best = p

    return best if best_score >= threshold else None






# Product name substrings that should never appear on the site
def _apply_cheapest_display(target: dict, store_key: str, match: dict) -> None:
    """Mutate *target* in-place to show *match* from *store_key* as the card front.

    Used when a comparison-store product is cheaper than the current display.
    Updates title, price, image, brand, weight, kg-price, multi-deal, and category.
    """
    target['/product/title'] = match['name']
    target['/product/store'] = _STORE_CONFIGS[store_key]['label']
    if match.get('is_sale'):
        normal_price = match.get('normal_price')
        if not normal_price:
            pid = str(target.get('/product/id', '')).strip()
            normal_price = _get_normal_price_history().get((pid, store_key))
        if normal_price and normal_price > match['price']:
            target['/product/price'] = normal_price
            target['/product/sale_price'] = match['price']
        else:
            # Ingen troværdig førpris fra hverken scraper eller 30-dages
            # historik - vis uden tilbudsmærkning frem for at gætte en
            # falsk førpris identisk med tilbudsprisen.
            target['/product/price'] = match['price']
            target['/product/sale_price'] = None
    else:
        target['/product/price'] = match['price']
        target['/product/sale_price'] = None
    if match.get('image') and str(match['image']).lower() != 'nan':
        target['/product/imageLink'] = match['image']
    target['/product/brand'] = match.get('brand') or target.get('/product/brand')
    target['/product/unit_pricing_measure'] = match.get('weight') or target.get('/product/unit_pricing_measure')
    target['/product/price_per_kg'] = match.get('kg_price')
    target['/product/multi_deal'] = match.get('multi_deal', '')
    new_type = unify_category(match.get('Kategori', ''), match['name'], match.get('brand', ''))
    if new_type and new_type != CAT_ANDET:
        target['/product/product_type'] = new_type


def parse_kg_price(kg_price_str):
    """Extract numeric kr/kg value from a string like '84,62 kr/Kg'."""
    if not kg_price_str or str(kg_price_str).strip() in ('nan', '', 'None'):
        return None
    try:
        cleaned = str(kg_price_str).replace(',', '.').replace('kr', '').replace('/kg', '').replace('/Kg', '').replace('/KG', '').strip()
        m = re.search(r'[\d.]+', cleaned)
        if m:
            val = float(m.group())
            return None if math.isnan(val) else val
    except (ValueError, TypeError):
        pass
    return None



def build_store_display_products(products: list, store_key: str) -> list:
    """Convert a comparison store's product list into display dicts for templates."""
    cfg = _STORE_CONFIGS[store_key]
    display = []
    for p in products:
        try:
            price = float(p['price'])
            if price <= 0:
                continue
            ppk = parse_kg_price(p.get('kg_price', ''))
            # ID-generering: den gamle udgave hashede RÅ, uvaskede scraper-
            # tekst (navn/mærke/vægt) - en kosmetisk omformulering i en
            # butiks feed (mellemrum, kampagnepræfiks, enhedsformat) mintede
            # dermed et helt nyt ID for samme fysiske vare, hvilket tavst
            # nulstillede prishistorik og lod prisalarmer dø permanent uden
            # fejl nogen steder. Se matchmotor-revisionen 2026-08-16, fund
            # C1/C2/C3.
            ean_val = str(p.get('ean') or '').strip()
            if ean_val and ean_val not in ('nan', 'None'):
                # EAN-baseret ID er UDEN butiksprefix med vilje: et EAN-
                # bærende kort skal have samme ID uanset hvilken butiks data
                # der tilfældigvis byggede kortet (fase 1's "main_key" er
                # blot første butik i DB_STORE_KEYS-rækkefølge der fører
                # EAN'en lige nu, og kan skifte nat til nat uden at varen
                # ændrer sig) - EAN'en er den autoritativt stabile nøgle.
                pid = f"ean_{hashlib.md5(ean_val.encode('utf-8')).hexdigest()[:10]}"
            else:
                # Normaliseret navn (samme normalize_name som resten af
                # matchingen bruger) + en afrundet vægt i stedet for de rå
                # navn/mærke/vægt-strenge - fjerner den mest almindelige
                # kilde til kosmetisk ID-drift for EAN-løse varer.
                norm_name = normalize_name(p.get('name', ''))
                weight_bucket = round(p.get('_weight_g') or 0)
                # Variantflagene SKAL med i nøglen. normalize_name fjerner
                # "økologisk" fra navnet - med vilje, for det er et VARIANT-flag
                # som _variant_flags håndterer på den rå tekst, og at lade ordet
                # blive giver kun støj i fuzzy_score. Men den normaliserede
                # streng blev genbrugt som IDENTITET, hvor informationen er
                # nødvendig: "Agurker" og "Økologiske Agurker" hos Meny gav
                # begge meny_0a96b695.
                #
                # Målt i produktionscachen 29-08-2026: 289 ID'er blev brugt af
                # mere end ét kort, i alt 312 kort. Konsekvenserne var tre:
                #   1. seed-d1.py har `id TEXT PRIMARY KEY` og en seen_ids-vagt,
                #      så de 312 kort blev tavst droppet ved hvert edge-seed -
                #      de lå i Supabase, men ikke på det live site.
                #   2. Prishistorik er nøglet på (pid, store), så "30 dages
                #      laveste" og førpris-fallback blandede øko og konventionel.
                #   3. En prisalarm på den ene vare blev udløst af den anden.
                #
                # Suffikset tilføjes KUN når varen faktisk bærer et flag.
                # Første udgave tilføjede det ubetinget, og en tørkørsel af hele
                # pipelinen viste hvad det kostede: 2.562 kort fik nyt id og
                # mistede dermed prishistorik og prisalarmer - 13 % af kataloget,
                # for at adskille en håndfuld øko-par. Den almindelige vare
                # beholder nu sit id uændret; kun varianten minter et nyt.
                # Kollisionen er lige så effektivt brudt, for det er netop
                # varianten der skal væk fra den almindelige vares nøgle.
                variants = p.get('_variants') or ()
                variant_key = ''.join('1' if flag else '0' for flag in variants)
                unique_str = f"{norm_name}_{weight_bucket}"
                if '1' in variant_key:
                    unique_str = f"{unique_str}_{variant_key}"
                pid = f"{store_key}_{hashlib.md5(unique_str.encode('utf-8')).hexdigest()[:8]}"
            img = p['image'] if p.get('image') and str(p['image']).lower() != 'nan' else cfg['logo']
            
            if p.get('is_sale'):
                normal_price = p.get('normal_price')
                if not normal_price:
                    normal_price = _get_normal_price_history().get((pid, store_key))
                if normal_price and normal_price > price:
                    display_price = normal_price
                    sale_price = price
                else:
                    # Ingen troværdig førpris fra hverken scraper eller
                    # 30-dages historik - vis uden tilbudsmærkning frem for
                    # at gætte en falsk førpris identisk med tilbudsprisen.
                    display_price = price
                    sale_price = None
            else:
                display_price = price
                sale_price = None

            p_type = unify_category(p.get('Kategori'), p['name'], p.get('brand', ''))
            if p_type is None:
                continue  # ikke-mad eller tobak
            display.append({
                '/product/id':                        pid,
                '/product/title':                     p['name'],
                '/product/price':                     display_price,
                '/product/sale_price':                sale_price,
                '/product/description':               p.get('weight', ''),
                '/product/brand':                     p.get('brand', ''),
                '/product/imageLink':                 img,
                '/product/product_type':              p_type,
                '/product/sale_price_effective_date': '',
                '/product/unit_pricing_measure':      p.get('weight', ''),
                '/product/weight_g':                  p.get('_weight_g'),
                '/product/price_per_kg':              ppk,
                '/product/store':                     cfg['label'],
                # Bevar varens EAN på solokortet (tabtes før) - så nærings-
                # opslag (Open Food Facts/Salling) virker for solokort uden
                # gruppe, og EAN'et følger med i store_matches ved gruppering
                # (_display_item_to_match). Lidl-ean er allerede '' (intern SKU).
                '/product/ean':                       p.get('ean', ''),
                '/product/store_matches':             {},
                '/product/cheapest_at':               None,
                '/product/cheaper_at':                None,
                '/product/multi_deal':                p.get('multi_deal', ''),
            })
        except Exception:
            continue
    return display


def _display_item_to_match(p: dict) -> dict:
    """Byg en store_matches 'match'-dict ud fra et display-produkt.

    Et display-produkts forside (titel/pris/billede) ER dets egen butiks tilbud,
    så vi kan konvertere det til samme format som de øvrige store_matches-poster.
    """
    sale = p.get('/product/sale_price')
    is_sale = sale is not None
    try:
        price = float(sale) if is_sale else float(p.get('/product/price', 0) or 0)
    except (TypeError, ValueError):
        price = 0.0
    normal_price = None
    if is_sale:
        try:
            normal_price = float(p.get('/product/price', 0) or 0)
        except (TypeError, ValueError):
            normal_price = None
    return {
        'name':         p.get('/product/title', ''),
        'price':        price,
        'normal_price': normal_price,
        'is_sale':      bool(is_sale),
        'image':        p.get('/product/imageLink', ''),
        'brand':        p.get('/product/brand', ''),
        'description':  p.get('/product/description', ''),
        'weight':       p.get('/product/unit_pricing_measure', ''),
        'kg_price':     p.get('/product/price_per_kg'),
        'multi_deal':   p.get('/product/multi_deal', ''),
        'ean':          str(p.get('/product/ean', '') or ''),
        'Kategori':     p.get('/product/product_type', ''),
    }


def _card_weight_g(card: dict) -> float | None:
    """Vægt i gram for et display-kort - feltet eller parset fra enhedsteksten."""
    w = card.get('/product/weight_g')
    if w:
        try:
            return float(w)
        except (TypeError, ValueError):
            pass
    return parse_weight_to_grams(str(card.get('/product/unit_pricing_measure', '')))


def _dedup_same_product(kept: dict, dup: dict) -> bool:
    """Sanity-check før billede-dedup fletter to kort: er det samme vare?

    Butikkerne genbruger produktfotos på tværs af pakkestørrelser (Royal Export
    0.33 l og 24-pakken deler billed-URL i Salling-feedet) og til tider på tværs
    af helt forskellige varer (generiske frugtfotos). Uforenelig vægt eller helt
    uens navne betyder, at kortene skal forblive adskilte."""
    w_kept, w_dup = _card_weight_g(kept), _card_weight_g(dup)
    if w_kept and w_dup and not weights_compatible(w_kept, w_dup):
        return False
    # Stk-antal: æg/te/frugt mangler ofte vægt, men bærer antallet i vægtfelt
    # eller navn - en 6-pk æg og en 10-pk æg deler foto, men er ikke samme vare.
    s_kept = _stk_count_of(kept.get('/product/unit_pricing_measure', ''), kept.get('/product/title', ''))
    s_dup = _stk_count_of(dup.get('/product/unit_pricing_measure', ''), dup.get('/product/title', ''))
    if s_kept is not None and s_dup is not None and s_kept != s_dup:
        return False
    # Procent-konflikt (fedt-/alkohol-%): alkoholfri og almindelig øl deler
    # ofte netop det produktfoto, som dedup'en grupperer på.
    if not _percents_match(get_product_percents(str(kept.get('/product/title', ''))),
                           get_product_percents(str(dup.get('/product/title', '')))):
        return False
    # Variant-konflikt (øko/laktosefri/sukkerfri/glutenfri): generiske stock-
    # fotos (kartofler, mozzarella, mælk) genbruges ofte identisk mellem den
    # økologiske og almindelige udgave af samme vare - uden dette tjek fletter
    # dedup'en dem stille sammen (fanget ved audit af eksisterende matches
    # 2026-07-12, fx "Kartofler" ↔ "Kartofler øko" endt i samme kort).
    kept_title = str(kept.get('/product/title', ''))
    dup_title = str(dup.get('/product/title', ''))
    kept_brand = str(kept.get('/product/brand', ''))
    dup_brand = str(dup.get('/product/brand', ''))
    if _variant_flags(kept_title, '', kept_brand) != _variant_flags(dup_title, '', dup_brand):
        return False
    # Kødtype-konflikt: hakket-kød-varianter (okse/gris/kylling) deler
    # pakkelayout og næsten hele navnet - må ikke flettes til ét kort.
    if not _meats_match(get_meat_types(kept_title), get_meat_types(dup_title)):
        return False
    n_kept = normalize_name(kept_title)
    n_dup = normalize_name(dup_title)
    if n_kept and n_dup and fuzzy_score(n_kept, n_dup) < 0.35:
        return False
    return True


def _merge_duplicate_into_kept(kept: dict, dup: dict) -> None:
    """Fold *dup*'s butiksdata ind i *kept*.store_matches.

    Salling-kæderne (Netto, Føtex, Bilka) deler samme produkt-feed og dermed
    samme billed-URL. Billede-dedup'en beholder kun ét kort, så varen kun vises
    én gang i listerne/på forsiden - men uden denne fletning ville vi tabe viden
    om, at varen også findes i dublettens butik(ker). Ved at bevare dataene i
    store_matches viser overlayet og indkøbskurven fortsat varen i ALLE butikker.
    """
    matches = kept.setdefault('/product/store_matches', {})
    kept_key = _LABEL_TO_KEY.get(kept.get('/product/store', ''))

    # 1) Dublettens egen forside-butik (dens synlige pris = butikkens tilbud)
    dup_key = _LABEL_TO_KEY.get(dup.get('/product/store', ''))
    if (dup_key and dup_key != REMA_KEY and dup_key != kept_key
            and dup_key not in matches):
        m = _display_item_to_match(dup)
        if m['price'] > 0:
            matches[dup_key] = m

    # 2) Dublettens egne store_matches (andre butikker varen allerede kendtes i)
    for k, m in (dup.get('/product/store_matches') or {}).items():
        if k == REMA_KEY or k == kept_key or k in matches:
            continue
        try:
            if m and float(m.get('price', 0) or 0) > 0:
                matches[k] = m
        except (TypeError, ValueError):
            continue

    # 3) Bevar Rema-tilgængelighed, så butiksfilteret stadig finder varen dér
    if not kept.get('/product/rema_price') and dup.get('/product/rema_price'):
        kept['/product/rema_price'] = dup.get('/product/rema_price')
        kept['/product/rema_is_sale'] = dup.get('/product/rema_is_sale', False)


def _card_eans(card: dict) -> set:
    """Alle gyldige EAN-numre kortet repræsenterer (kortets eget + medlemmernes)."""
    eans = set()
    own = str(card.get('/product/ean') or '')
    if ean_looks_valid(own):
        eans.add(own)
    for m in (card.get('/product/store_matches') or {}).values():
        if not isinstance(m, dict):
            continue
        e = str(m.get('ean') or '')
        if ean_looks_valid(e):
            eans.add(e)
    return eans


def _merge_cards_sharing_ean(cards: list) -> list:
    """Sikkerhedsnet: samme stregkode må ikke ende på to forskellige kort.

    EAN er en autoritativ produktidentitet, og hele motoren behandler den
    sådan - stage 1 grupperer på den, retro-valideringen lader den underkende
    et fuzzy-match, og kryds-medlems-arbitragen fritager par der deler den.
    Alligevel lå 437 EAN-numre på MERE end ét færdigt kort i produktionscachen
    (330 af dem på tværs af butikker), fx "Havredrik cremet" med Netto på ét
    kort og Bilka + Føtex på et andet.

    Årsagen er rækkefølgen: Rema-annoteringen kører før EAN-grupperingen og
    hiver EAN-bærende varer ud af de grupper, de hører til. Den rigtige
    løsning er at flytte grupperingen først (se planens Etape 2.5), men det
    er en større omlægning af fetch_and_parse_xml. Denne funktion lukker
    invarianten bagfra, hvor den er billig og let at efterprøve.

    Samme sanity-tjek som billede-dedup'en bruges (_dedup_same_product), så en
    EAN-kollision i rådata ikke kan flette to reelt forskellige varer sammen.
    """
    by_ean: dict = {}
    merged_into: dict = {}
    out: list = []
    merges = 0

    for card in cards:
        target = None
        for ean in _card_eans(card):
            candidate = by_ean.get(ean)
            # Følg kæden, hvis kandidaten selv er blevet flettet ind i et andet
            while candidate is not None and id(candidate) in merged_into:
                candidate = merged_into[id(candidate)]
            if candidate is not None and candidate is not card:
                target = candidate
                break

        if target is not None and _dedup_same_product(target, card):
            _merge_duplicate_into_kept(target, card)
            merged_into[id(card)] = target
            merges += 1
            # Kortets EAN-numre peger nu på det beholdte kort
            for ean in _card_eans(card):
                by_ean[ean] = target
            continue

        out.append(card)
        for ean in _card_eans(card):
            by_ean.setdefault(ean, card)

    if merges:
        logger.info("EAN-invariant: flettede %d kort der delte stregkode med et andet "
                    "(%d -> %d kort)", merges, len(cards), len(out))
    return out


def validate_xml_structure(xml_dict):
    """Validate the XML data structure"""
    if not isinstance(xml_dict, dict):
        logger.error("Error: XML data is not a dictionary")
        return False
        
    if 'products' not in xml_dict:
        logger.error("Error: No 'products' element in XML")
        return False
        
    if not isinstance(xml_dict['products'], dict):
        logger.error("Error: 'products' is not a dictionary")
        return False
        
    if 'product' not in xml_dict['products']:
        logger.error("Error: No 'product' element in products")
        return False
        
    if not isinstance(xml_dict['products']['product'], list):
        logger.error("Error: 'product' is not a list")
        return False
        
    return True

def _rema_hashes_path() -> str:
    return os.path.join(os.path.dirname(__file__), 'data', 'rema_hashes.json')


def _load_rema_hashes() -> dict:
    path = _rema_hashes_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error("Fejl ved indlæsning af rema_hashes.json: %s", e)
        return {}


def _persist_rema_hashes(rema_hashes: dict) -> None:
    path = _rema_hashes_path()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(rema_hashes, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("Kunne ikke gemme rema_hashes.json: %s", e)


def _fill_missing_rema_hashes(raw_products: list, rema_hashes: dict) -> dict:
    """Beregn pHash for Rema-varer der mangler i rema_hashes.json (parallel)."""
    jobs: list[tuple[str, str]] = []
    for product in raw_products:
        pid = str(product.get('id', '')).strip()
        if not pid or rema_hashes.get(pid):
            continue
        img_url = str(product.get('imageLink', '') or '').strip()
        if not img_url or img_url in _PLACEHOLDER_IMGS:
            continue
        jobs.append((pid, img_url))

    if not jobs:
        return rema_hashes

    logger.info("Beregner pHash for %d nye Rema-varer...", len(jobs))
    new_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(compute_image_hash, url): pid for pid, url in jobs}
        for future in concurrent.futures.as_completed(futures):
            pid = futures[future]
            try:
                h = future.result()
            except Exception:
                h = ''
            if h:
                rema_hashes[pid] = h
                new_count += 1

    if new_count:
        logger.info("Gemte %d nye Rema pHash i rema_hashes.json", new_count)
        _persist_rema_hashes(rema_hashes)
    return rema_hashes


def _fetch_rema_products_only():
    """Hent og parse Rema 1000 XML - uden sammenligning med andre butikker."""
    rema_products = []
    logger.info("Fetching XML data from: %s", XML_URL)
    try:
        rema_hashes = _load_rema_hashes()

        xml_text = None
        for attempt in range(3):
            try:
                response = requests.get(
                    XML_URL,
                    timeout=(10, 120),
                    headers=DEFAULT_HTTP_HEADERS,
                    stream=True,
                )
                response.raise_for_status()
                xml_text = response.content.decode(response.encoding or 'utf-8', errors='replace')
                logger.info(f"Response status: {response.status_code}")
                break
            except requests.exceptions.Timeout:
                logger.info(f"  Timeout på forsøg {attempt + 1}/3 - prøver igen...")
            except requests.exceptions.RequestException as e:
                logger.info(f"  Netværksfejl på forsøg {attempt + 1}/3: {e}")
        if xml_text is None:
            raise RuntimeError("Kunne ikke hente Rema XML efter 3 forsøg")

        xml_dict = xmltodict.parse(xml_text)
        if not validate_xml_structure(xml_dict):
            logger.info("XML validation failed")
            return []

        raw_products = xml_dict['products']['product']
        if isinstance(raw_products, dict):
            raw_products = [raw_products]
        rema_hashes = _fill_missing_rema_hashes(raw_products, rema_hashes)

        for i, product in enumerate(raw_products):
            try:
                price = format_price(product.get('price', '0 DKK'))
                sale_price = format_price(product.get('sale_price', '')) or None
                if price <= 0:
                    continue

                mapped_type = unify_category(
                    product.get('product_type', ''),
                    product.get('title', ''),
                    product.get('brand', ''),
                )
                if mapped_type is None:
                    continue  # ikke-mad eller tobak - frasorteres centralt i unify_category
                if is_age_restricted(
                    product.get('title', ''),
                    product.get('brand', ''),
                    product.get('product_type', ''),
                    product.get('id', ''),
                ):
                    continue

                unit_measure = product.get('unit_pricing_measure', '')
                weight_g = parse_weight_to_grams(unit_measure)
                price_per_kg = None
                if weight_g and weight_g > 0:
                    effective_price = sale_price if sale_price is not None else price
                    price_per_kg = (effective_price / (weight_g / 1000.0))

                rema_products.append({
                    '/product/id': product.get('id', ''),
                    '/product/ean': product.get('ean', ''),
                    '/product/title': product.get('title', ''),
                    '/product/price': price,
                    '/product/sale_price': sale_price,
                    '/product/description': product.get('description', ''),
                    '/product/brand': product.get('brand', ''),
                    '/product/imageLink': product.get('imageLink', ''),
                    '/product/product_type': mapped_type,
                    '/product/sale_price_effective_date': product.get('sale_price_effective_date', ''),
                    '/product/store': 'Rema 1000',
                    '/product/unit_pricing_measure': unit_measure,
                    '/product/weight_g': weight_g,
                    '/product/stk_count': _stk_count_of(unit_measure, product.get('title', '')),
                    '/product/price_per_kg': price_per_kg,
                    '/product/image_hash': rema_hashes.get(str(product.get('id', '')), ''),
                })
            except Exception as e:
                logger.error(f"Error processing Rema 1000 product {i}: {str(e)}")
                continue

        logger.info(f"Total Rema 1000 products parsed: {len(rema_products)}")
    except Exception as e:
        logger.error(f"Error fetching Rema 1000 data: {str(e)}")
        traceback.print_exc()
    return rema_products


def _rema_effective_price(product):
    sale = product.get('/product/sale_price')
    if sale is not None:
        try:
            val = float(sale)
            if not math.isnan(val):
                return val
        except (TypeError, ValueError):
            pass
    return float(product.get('/product/price') or 0)


def merge_rema_into_cache(cached, fresh_rema):
    """Opdater kun Rema-priser i eksisterende cache - andre butikker bevares."""
    fresh_by_id = {str(p['/product/id']): p for p in fresh_rema}
    seen_rema_ids = set()
    merged = []

    for product in cached:
        pid = str(product.get('/product/id', ''))
        if pid in fresh_by_id:
            fresh = fresh_by_id[pid]
            updated = dict(product)
            updated['/product/rema_price'] = _rema_effective_price(fresh)
            updated['/product/rema_is_sale'] = fresh.get('/product/sale_price') is not None
            updated['/product/rema_image'] = fresh.get('/product/imageLink', '')
            if product.get('/product/store') == 'Rema 1000':
                for key in (
                    '/product/price', '/product/sale_price', '/product/title',
                    '/product/imageLink', '/product/brand', '/product/description',
                    '/product/unit_pricing_measure', '/product/weight_g',
                    '/product/stk_count', '/product/price_per_kg', '/product/product_type',
                    '/product/sale_price_effective_date', '/product/ean',
                ):
                    if key in fresh:
                        updated[key] = fresh[key]
            merged.append(updated)
            seen_rema_ids.add(pid)
        elif product.get('/product/store') == 'Rema 1000':
            continue
        else:
            merged.append(product)

    for pid, fresh in fresh_by_id.items():
        if pid in seen_rema_ids:
            continue
        item = dict(fresh)
        item['/product/store_matches'] = {}
        item['/product/rema_price'] = _rema_effective_price(fresh)
        item['/product/rema_is_sale'] = fresh.get('/product/sale_price') is not None
        item['/product/cheapest_at'] = REMA_KEY
        item['/product/cheaper_at'] = REMA_KEY
        merged.append(item)

    logger.info(
        "Rema merge: %d produkter opdateret, %d nye, %d i alt",
        len(seen_rema_ids),
        len(fresh_by_id) - len(seen_rema_ids),
        len(merged),
    )
    return merged


def _load_app_cache():
    """Hent nuværende produkt-cache fra Supabase."""
    if not db_available():
        return [], {}
    try:
        import httpx
        # Samme env-fallbacks som _get_supabase_client/_save_app_cache - ellers
        # kan en kørsel med kun DEPLOY_KEY sat fejle stille her, se cachen som
        # tom og overskrive den med Rema-only produkter.
        base = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
        key = (
            os.getenv('DEPLOY_KEY')
            or os.getenv('SUPABASE_KEY')
            or os.getenv('NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY')
        )
        if not base or not key:
            logger.error("Supabase URL/nøgle mangler - kan ikke hente app_cache")
            return [], {}
        url = f"{base}/rest/v1/app_cache?select=*&id=gte.0&order=id.asc"
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        with httpx.Client(timeout=60.0) as client:
            res = client.get(url, headers=headers)
            if res.status_code != 200 or not res.json():
                return [], {}
            products = []
            search_index = {}
            for row in res.json():
                if row.get('id') == 0:
                    search_index = row.get('search_index', {})
                else:
                    chunk = row.get('data', [])
                    if isinstance(chunk, list):
                        products.extend(chunk)
            return products, search_index
    except Exception as e:
        logger.error(f"Kunne ikke hente app_cache: {e}")
        return [], {}


_LOCAL_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'app_cache_local.json')


def _save_local_cache(products, search_index):
    """Gem produkt-cache som lokal JSON-fil (fallback til udvikling)."""
    try:
        os.makedirs(os.path.dirname(_LOCAL_CACHE_FILE), exist_ok=True)
        payload = json.dumps(
            {"products": products, "search_index": search_index},
            default=lambda o: list(o) if isinstance(o, (set, frozenset)) else str(o),
            ensure_ascii=False,
        )
        with open(_LOCAL_CACHE_FILE, 'w', encoding='utf-8') as f:
            f.write(payload)
        logger.info(f"Lokal cache gemt: {len(products)} produkter → {_LOCAL_CACHE_FILE}")
        return True
    except Exception as e:
        logger.error(f"Fejl ved gemning af lokal cache: {e}")
        return False


_APP_CACHE_STAGING_OFFSET = 1_000_000


def _upload_app_cache_rows(client, url: str, headers: dict, products: list,
                           search_index: dict, id_offset: int) -> None:
    """Uploader index- og data-chunks med id'er forskudt af id_offset."""
    idx_payload = {"id": id_offset, "data": [], "search_index": search_index}
    res_idx = client.post(url, headers=headers, content=json.dumps(idx_payload, default=lambda o: list(o) if isinstance(o, (set, frozenset)) else str(o)))
    res_idx.raise_for_status()

    chunk_size = 1000
    for chunk_id, i in enumerate(range(0, len(products), chunk_size), start=1):
        chunk = products[i:i + chunk_size]
        chunk_payload = {"id": id_offset + chunk_id, "data": chunk, "search_index": {}}
        res_chunk = client.post(url, headers=headers, content=json.dumps(chunk_payload, default=lambda o: list(o) if isinstance(o, (set, frozenset)) else str(o)))
        res_chunk.raise_for_status()
        logger.info(f"Uploadet data chunk {chunk_id} med {len(chunk)} produkter (offset {id_offset})")


def _save_app_cache(products, search_index):
    """Upload produkt-cache til Supabase og gem altid lokalt som fallback.

    Uploader til et staging-id-space (id >= _APP_CACHE_STAGING_OFFSET) og
    swapper først ind som de rigtige id'er via swap_app_cache() - en Postgres-
    funktion der sletter gamle rækker og flytter staging-rækkerne ned i én
    transaktion. Fejler en upload midtvejs, rører vi aldrig den nuværende
    (fortsat fuldt fungerende) cache. Kør scripts/supabase-app-cache-swap.sql
    for at aktivere denne beskyttelse - indtil da bruges den gamle metode."""
    _save_local_cache(products, search_index)

    if not db_available():
        return False
    import httpx
    # Samme env-fallbacks som _load_app_cache og _get_supabase_client - ellers
    # vil en kørsel med kun NEXT_PUBLIC_*-varianterne sat uploade til
    # "None/rest/v1/app_cache" og fejle stille til lokal fallback.
    base = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    key = (
        os.getenv('DEPLOY_KEY')
        or os.getenv('SUPABASE_KEY')
        or os.getenv('NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY')
        or ""
    )
    if not base or not key:
        logger.error("_save_app_cache: Supabase URL/nøgle mangler - kan ikke uploade cache")
        return False
    url = f"{base}/rest/v1/app_cache"
    rpc_url = f"{base}/rest/v1/rpc/swap_app_cache"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal,resolution=merge-duplicates",
    }
    offset = _APP_CACHE_STAGING_OFFSET
    try:
        with httpx.Client(timeout=120.0) as client:
            # Ryd rester fra en evt. tidligere fejlet staging-upload
            try:
                client.delete(url + f"?id=gte.{offset}", headers={"apikey": key, "Authorization": f"Bearer {key}"})
            except Exception:
                pass

            _upload_app_cache_rows(client, url, headers, products, search_index, offset)

            res_swap = client.post(
                rpc_url, headers=headers,
                content=json.dumps({"staging_offset": offset}),
            )
            if res_swap.status_code == 404:
                # swap_app_cache findes endnu ikke - kør scripts/supabase-app-cache-swap.sql.
                # Falder tilbage til den gamle (ikke-atomiske) metode, så cachen
                # fortsat opdateres uden den ekstra beskyttelse.
                logger.warning(
                    "swap_app_cache-funktion mangler (404) - bruger gammel upload-metode. "
                    "Kør scripts/supabase-app-cache-swap.sql for atomisk swap."
                )
                client.delete(url + "?id=gte.0", headers={"apikey": key, "Authorization": f"Bearer {key}"})
                _upload_app_cache_rows(client, url, headers, products, search_index, 0)
                client.delete(url + f"?id=gte.{offset}", headers={"apikey": key, "Authorization": f"Bearer {key}"})
            else:
                res_swap.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"Kunne ikke uploade til Supabase app_cache (lokal fallback bruges): {e}")
        try:
            with httpx.Client(timeout=30.0) as client:
                client.delete(url + f"?id=gte.{offset}", headers={"apikey": key, "Authorization": f"Bearer {key}"})
        except Exception:
            pass
        return False


# Kør scripts/supabase-price-history.sql i Supabase hvis upsert fejler (manglende unique index).
_last_price_record_date = None


def collect_store_prices(products: list) -> list:
    """Udtræk (product_id, store_key, price) fra cache til daglig prishistorik."""
    entries = []
    for p in products:
        pid = str(p.get('/product/id', '')).strip()
        if not pid or pid in ('None', ''):
            continue

        rema_price = p.get('/product/rema_price')
        if rema_price and float(rema_price) > 0:
            entries.append((pid, 'rema', float(rema_price)))

        for store_key, match in (p.get('/product/store_matches') or {}).items():
            # 'price' er den aktuelle pris (tilbudspris når varen er på tilbud);
            # 'normal_price' er kun førprisen. Historikken skal vise prisfald,
            # så den aktuelle pris gemmes.
            match_price = match.get('price') or match.get('normal_price')
            if match_price:
                try:
                    mp = float(match_price)
                    if mp > 0:
                        entries.append((pid, store_key, mp))
                except (TypeError, ValueError):
                    pass

        if not p.get('/product/rema_price') and not p.get('/product/store_matches'):
            store_label = str(p.get('/product/store', ''))
            store_key = _LABEL_TO_KEY.get(store_label, '')
            if store_key:
                # '/product/price' er normalprisen når varen er på tilbud -
                # foretræk tilbudsprisen, så historikken viser prisfald.
                for raw_price in (p.get('/product/sale_price'), p.get('/product/price')):
                    if not raw_price:
                        continue
                    try:
                        sp = float(raw_price)
                    except (TypeError, ValueError):
                        continue
                    if sp > 0:
                        entries.append((pid, store_key, sp))
                        break
    return entries


def record_prices_batch(entries: list):
    """Gem dagens priser i Supabase og slet data ældre end 30 dage."""
    if not db_available():
        return
    global _last_price_record_date
    today = datetime.now().strftime('%Y-%m-%d')
    if _last_price_record_date == today:
        return
    try:
        if not entries:
            return

        # Én post pr. (produkt, butik) - duplikater i samme batch gav Supabase 500.
        by_key: dict[tuple[str, str], dict] = {}
        for row in entries:
            if len(row) == 3:
                product_id, store, price = row
            else:
                product_id, price = row
                store = 'rema'
            try:
                price_f = float(price)
            except (TypeError, ValueError):
                continue
            if price_f <= 0:
                continue
            pid = str(product_id).strip()
            store_key = str(store).strip()
            if not pid or pid == 'None' or not store_key:
                continue
            by_key[(pid, store_key)] = {
                "product_id": pid,
                "store": store_key,
                "price": price_f,
                "date": today,
            }
        records = list(by_key.values())
        if not records:
            return

        import httpx
        import time as _time

        base_url = (
            f"{os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')}"
            f"/rest/v1/price_history"
        )
        upsert_url = f"{base_url}?on_conflict=product_id,store,date"
        key = os.getenv("DEPLOY_KEY") or os.getenv("SUPABASE_KEY") or ""
        if not key:
            logger.warning("Prishistorik: DEPLOY_KEY/SUPABASE_KEY mangler - springer over")
            return
        auth = {"apikey": key, "Authorization": f"Bearer {key}"}
        upsert_headers = {
            **auth,
            "Content-Type": "application/json",
            "Prefer": "return=minimal,resolution=merge-duplicates",
        }

        chunk_size = 500
        posted = 0
        with httpx.Client(timeout=120.0) as client:
            for i in range(0, len(records), chunk_size):
                chunk = records[i:i + chunk_size]
                last_resp = None
                for attempt in range(3):
                    last_resp = client.post(
                        upsert_url,
                        headers=upsert_headers,
                        content=json.dumps(chunk),
                    )
                    if last_resp.is_success:
                        posted += len(chunk)
                        break
                    if attempt < 2:
                        _time.sleep(1.5 * (attempt + 1))
                else:
                    body = (last_resp.text[:500] if last_resp is not None else "")
                    code = last_resp.status_code if last_resp is not None else "?"
                    raise RuntimeError(f"Prishistorik POST fejlede: HTTP {code} {body}")

            thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
            try:
                resp = client.delete(
                    f"{base_url}?date=lt.{thirty_days_ago}",
                    headers=auth,
                )
                resp.raise_for_status()
            except Exception as del_err:
                # Indsatte dagens priser - gamle rækker kan ryddes ved næste kørsel.
                logger.warning("Prishistorik: kunne ikke slette data ældre end 30 dage: %s", del_err)

        _last_price_record_date = today
        logger.info(
            "Prishistorik: gemte %s posteringer for %s i Supabase (%s unikke produkt/butik-par)",
            posted, today, len(records),
        )
    except Exception as e:
        logger.error("Fejl ved gemning af prishistorik: %s", e)


def prune_cart_events(days: int = 30):
    """Slet kurv-aktivitet ældre end `days` dage fra cart_events.

    Tabellen er time-aggregeret (én række pr. produkt/time/signaltype), så den
    vokser langsomt - men uden en grænse vokser den ubegrænset. 30 dage matcher
    prishistorikken og holder forbruget langt under Supabase-gratisplanens 500 MB.
    Aggregatet er anonymt, så oprydningen handler om plads, ikke om slettepligt.

    Cutoff beregnes i maskinens lokaltid mod en kolonne i dansk tid - i GitHub
    Actions (UTC) giver det et par timers skævhed, hvilket er uden betydning
    ved 30 dages horisont."""
    if not db_available():
        return
    base = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    key = os.getenv("DEPLOY_KEY") or os.getenv("SUPABASE_KEY") or ""
    if not base or not key:
        logger.warning("cart_events: DEPLOY_KEY/SUPABASE_KEY mangler - springer oprydning over")
        return
    # _updater_table_suffix(): tom i CI (produktion), '_dev' hvis kørt lokalt
    # uden TABLE_SUFFIX sat - se funktionens docstring.
    table = f"cart_events{_updater_table_suffix()}"
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S')
    try:
        import httpx

        with httpx.Client(timeout=60.0) as client:
            resp = client.delete(
                f"{base}/rest/v1/{table}?hour=lt.{cutoff}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
            )
            if resp.status_code == 404:
                logger.info(
                    "cart_events findes ikke endnu - kør scripts/supabase-cart-increment.sql"
                )
                return
            resp.raise_for_status()
        logger.info("cart_events: ryddede rækker ældre end %s dage (cutoff %s)", days, cutoff)
    except Exception as e:
        # Ikke kritisk: tabellen er lille, og næste kørsel forsøger igen
        logger.warning("cart_events: kunne ikke rydde gamle rækker: %s", e)


def _cheapest_prices_by_id(products: list) -> dict:
    """product_id -> laveste kendte pris på tværs af butikker.

    Genbruger collect_store_prices' udtræk (samme kilde som prishistorikken),
    så prisalarmer udløses af nøjagtig den samme pris brugeren ser i overlayet.
    """
    cheapest: dict[str, float] = {}
    for pid, _store, price in collect_store_prices(products):
        if price > 0 and (pid not in cheapest or price < cheapest[pid]):
            cheapest[pid] = price
    return cheapest


def _send_price_alert_email(to_email: str, product_name: str, target_price: float, current_price: float) -> bool:
    """Send "prisen er nået"-mail via Resends HTTP API. True ved success."""
    api_key = os.getenv('RESEND_API_KEY')
    if not api_key:
        logger.info("RESEND_API_KEY ikke sat - springer prisalarm-mail over")
        return False
    raw_name = product_name or 'varen du overvåger'
    # product_name kommer fra KLIENTEN (create_price_alert's pname-argument), og
    # SQL'en gemmer den kun afkortet - ikke escaped. Uden html.escape kunne en
    # bruger lægge vilkårlig markup ind i en mail afsendt fra
    # alarm@madshopper.dk og videresende den: en autentisk-udseende
    # MadShopper-mail med fremmed indhold (brand-misbrug/phishing). Modtageren
    # er altid brugeren selv (emailen kommer fra auth.jwt()), så det rammer
    # ikke andre - men afsenderadressen er vores.
    name = _html.escape(raw_name)
    # Emnelinjen tåler ikke CR/LF: et linjeskift dér er header-injection i
    # SMTP-verdenen. Resend sender via HTTP-API'et, men vanen er billig.
    subject_name = raw_name.replace('\r', ' ').replace('\n', ' ')[:120]
    try:
        import httpx
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "MadShopper <alarm@madshopper.dk>",
                "to": [to_email],
                "subject": f"Prisalarm: {subject_name} er nu {current_price:.2f} kr",
                "html": (
                    f"<p>Hej!</p>"
                    f"<p><strong>{name}</strong> er faldet til <strong>{current_price:.2f} kr</strong> "
                    f"– din grænse var {target_price:.2f} kr.</p>"
                    f"<p><a href=\"https://madshopper.dk\">Se den på MadShopper</a></p>"
                    f"<p style=\"color:#888;font-size:12px;margin-top:24px;\">"
                    f"Du får denne mail, fordi du selv har oprettet en prisalarm på MadShopper. "
                    f"Alarmen er nu brugt op og sender ikke igen. "
                    f"Du kan se og slette dine alarmer under "
                    f"<a href=\"https://madshopper.dk/?alarmer=1\">Mine prisalarmer</a>.<br>"
                    f"Denne mail kan ikke besvares.</p>"
                ),
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        # Log ALDRIG modtageradressen: jobbet kører i GitHub Actions, hvis logs
        # er læsbare for alle med read-adgang og gemmes i 90 dage. Brugernes
        # mailadresser hører ikke hjemme i en build-log. Kalderen samler
        # alligevel de fejlede alarm-id'er og logger dem aggregeret.
        logger.warning("Kunne ikke sende prisalarm-mail: %s", e)
        return False


def check_price_alerts(products: list) -> None:
    """Tjek aktive prisalarmer mod nattens friske priser, mail ved match.

    Del af run_updater() - se docs/prisovervaagning.md. Alarmer kræver login
    (scripts/supabase-price-alerts-v2.sql), og emailen ligger allerede på
    rækken (hentet fra JWT'et ved oprettelse), så der er intet ekstra opslag
    mod auth.users nødvendigt her. Én alarm sender højst én mail - notified_at
    sættes bagefter, og en ny alarm på samme vare nulstiller den (RPC'en).
    """
    if not db_available():
        return
    base = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    key = os.getenv("DEPLOY_KEY") or os.getenv("SUPABASE_KEY") or ""
    if not base or not key:
        logger.warning("Prisalarmer: DEPLOY_KEY/SUPABASE_KEY mangler - springer over")
        return
    table = f"price_alerts{_updater_table_suffix()}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    try:
        import httpx
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                f"{base}/rest/v1/{table}",
                headers=headers,
                params={
                    "select": "id,product_id,product_name,target_price,email",
                    "notified_at": "is.null",
                    "email": "not.is.null",
                },
            )
            if resp.status_code == 404:
                logger.info("price_alerts findes ikke endnu - kør scripts/supabase-price-alerts-v2.sql")
                return
            resp.raise_for_status()
            alerts = resp.json()
    except Exception as e:
        logger.warning("Prisalarmer: kunne ikke hente aktive alarmer: %s", e)
        return

    if not alerts:
        return

    cheapest = _cheapest_prices_by_id(products)
    triggered_ids = []
    unresolved = []  # alarmer hvis product_id ikke findes i nattens friske priser
    unsent = []      # alarmer der udløste, men hvor selve mail-afsendelsen fejlede
    for alert in alerts:
        pid = str(alert.get('product_id') or '')
        target = alert.get('target_price')
        email = alert.get('email')
        if not pid or not email or target is None:
            continue
        price_now = cheapest.get(pid)
        if price_now is None:
            # Var tidligere en STILLE, PERMANENT fejl: en alarm hvis
            # product_id ikke længere findes (fx fordi kortets ID skiftede
            # ved en cache-genopbygning, se fund C1-C3) fyrede aldrig igen,
            # uden fejl nogen steder. Logges nu aggregeret (én linje pr.
            # kørsel, ikke pr. alarm) så det i det mindste er synligt.
            unresolved.append(pid)
            continue
        if price_now > float(target):
            continue
        if _send_price_alert_email(email, alert.get('product_name') or '', float(target), price_now):
            triggered_ids.append(alert['id'])
        else:
            unsent.append(alert['id'])

    if unresolved:
        logger.warning(
            "Prisalarmer: %d/%d aktive alarmer matcher intet product_id i "
            "nattens priser (kortet kan have skiftet ID) - eksempler: %s",
            len(unresolved), len(alerts), unresolved[:10])

    if unsent:
        # Var tidligere HELT stille (kun en info-linje inde i
        # _send_price_alert_email, én gang PR forsøgt mail - drukner i loggen
        # og ses aldrig, fordi jobbet uanset udfald afslutter grønt). En
        # manglende/ugyldig RESEND_API_KEY betyder brugere venter FOREVER på
        # en mail der aldrig sendes, uden at nogen opdager det - alarmerne
        # forbliver notified_at=NULL og forsøges forgæves igen hver nat.
        # Aggregeret advarsel (produktionsrevision 18-08-2026, blokerer #6).
        logger.warning(
            "Prisalarmer: %d udløst(e) alarm(er) kunne IKKE sendes (Resend-kald "
            "fejlede eller RESEND_API_KEY mangler) - de forsøges igen næste nat: %s",
            len(unsent), unsent[:10])

    if not triggered_ids:
        return

    try:
        import httpx
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        ids_filter = ",".join(str(i) for i in triggered_ids)
        with httpx.Client(timeout=30.0) as client:
            resp = client.patch(
                f"{base}/rest/v1/{table}",
                headers={**headers, "Content-Type": "application/json", "Prefer": "return=minimal"},
                params={"id": f"in.({ids_filter})"},
                content=json.dumps({"notified_at": now_iso}),
            )
            resp.raise_for_status()
        logger.info(
            "Prisalarmer: sendte %s mail(s) af %s aktive alarmer", len(triggered_ids), len(alerts)
        )
    except Exception as e:
        logger.warning("Prisalarmer: kunne ikke markere alarmer som udløst: %s", e)


def _fetch_lowest_prices_30d() -> dict:
    """Hent laveste pris pr. produkt (30 dage) fra price_history_low30-viewet.

    Kræver at scripts/supabase-lowest-price.sql er kørt i Supabase - ellers
    returneres tom dict, og badget udelades blot på hjemmesiden."""
    if not db_available():
        return {}
    base = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    key = os.getenv("DEPLOY_KEY") or os.getenv("SUPABASE_KEY") or ""
    if not base or not key:
        return {}
    lowest: dict = {}
    try:
        import httpx
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        page_size = 1000
        offset = 0
        with httpx.Client(timeout=60.0) as client:
            while True:
                res = client.get(
                    f"{base}/rest/v1/price_history_low30",
                    params={"select": "product_id,min_price",
                            "limit": page_size, "offset": offset},
                    headers=headers,
                )
                if res.status_code != 200:
                    logger.warning(
                        "price_history_low30 utilgængelig (status %s) - kør scripts/supabase-lowest-price.sql",
                        res.status_code,
                    )
                    return {}
                rows = res.json()
                for r in rows:
                    pid = str(r.get('product_id') or '')
                    mp = r.get('min_price')
                    if pid and mp is not None:
                        lowest[pid] = float(mp)
                if len(rows) < page_size:
                    break
                offset += page_size
    except Exception as e:
        logger.warning("Kunne ikke hente 30-dages laveste priser: %s", e)
        return {}
    logger.info("Hentede 30-dages laveste pris for %d produkter", len(lowest))
    return lowest


_normal_price_history_cache: dict | None = None


def _fetch_normal_prices_30d() -> dict:
    """Hent typisk pris pr. (produkt, butik) fra price_history_normal30-viewet.

    Bruges som førpris-fallback når en butiks-scraper flager en vare som
    'tilbud' uden selv at levere en førpris (fx Bilkas multikøbs-kampagner
    uden beforePrice - se scraper/bilka_katalog.py). Kræver at
    scripts/supabase-normal-price.sql er kørt i Supabase - ellers returneres
    tom dict, og den slags varer vises uden tilbudsmærkning i stedet for en
    falsk førpris (se build_store_display_products/_apply_cheapest_display)."""
    if not db_available():
        return {}
    base = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    key = os.getenv("DEPLOY_KEY") or os.getenv("SUPABASE_KEY") or ""
    if not base or not key:
        return {}
    normal: dict = {}
    try:
        import httpx
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        page_size = 1000
        offset = 0
        with httpx.Client(timeout=60.0) as client:
            while True:
                res = client.get(
                    f"{base}/rest/v1/price_history_normal30",
                    params={"select": "product_id,store,normal_price",
                            "limit": page_size, "offset": offset},
                    headers=headers,
                )
                if res.status_code != 200:
                    logger.warning(
                        "price_history_normal30 utilgængelig (status %s) - kør scripts/supabase-normal-price.sql",
                        res.status_code,
                    )
                    return {}
                rows = res.json()
                for r in rows:
                    pid = str(r.get('product_id') or '')
                    store = str(r.get('store') or '')
                    np = r.get('normal_price')
                    if pid and store and np is not None:
                        normal[(pid, store)] = float(np)
                if len(rows) < page_size:
                    break
                offset += page_size
    except Exception as e:
        logger.warning("Kunne ikke hente 30-dages normalpriser: %s", e)
        return {}
    logger.info("Hentede 30-dages normalpris for %d produkt/butik-par", len(normal))
    return normal


def _get_normal_price_history() -> dict:
    """Cacher _fetch_normal_prices_30d() for hele kørslen - kaldes fra flere
    steder under matching, og historikken ændrer sig ikke midt i en kørsel."""
    global _normal_price_history_cache
    if _normal_price_history_cache is None:
        _normal_price_history_cache = _fetch_normal_prices_30d()
    return _normal_price_history_cache


def annotate_lowest_prices(products: list) -> None:
    """Stemp '/product/lowest_price_30d' på produkter med prishistorik."""
    lowest = _fetch_lowest_prices_30d()
    if not lowest:
        return
    annotated = 0
    for p in products:
        lp = lowest.get(str(p.get('/product/id', '')).strip())
        if lp is not None:
            p['/product/lowest_price_30d'] = lp
            annotated += 1
    logger.info("Annoterede %d produkter med 30-dages laveste pris", annotated)


def fetch_and_parse_xml():
    """Fetch and parse data from both XML and Excel sources"""
    try:
        logger.info("\n=== Starting data fetch and parse ===")

        rema_products = _fetch_rema_products_only()
        if not rema_products:
            return []
        
        # Annotate each Rema product with comparison data from all secondary stores.
        # Rema has no EAN → _find_generic_match acts as a stage-3 fuzzy initiator.
        logger.info("\nAnnotating Rema products with comparison data")
        store_data   = load_all_comparison_data()
        # store_data = {'bilka': (products, token_idx), 'mk': (...), ...}

        final_products = []
        matched_ids  = {key: set() for key in DB_STORE_KEYS}
        match_counts = {key: 0     for key in DB_STORE_KEYS}

        for product in rema_products:
            rema_effective = (
                float(product['/product/sale_price'])
                if product['/product/sale_price'] is not None
                and not math.isnan(float(product['/product/sale_price']))
                else float(product['/product/price'])
            )

            # Match against every secondary store
            matches = {}
            for key in DB_STORE_KEYS:
                products_list, token_idx, hash_list, ean_index = store_data[key]
                m = _find_generic_match(
                    str(product['/product/title']),
                    str(product['/product/description']),
                    products_list,
                    token_idx,
                    hash_list,
                    rema_brand=str(product.get('/product/brand', '')),
                    rema_weight_g=product.get('/product/weight_g'),
                    rema_image_hash=product.get('/product/image_hash', ''),
                    rema_price=float(product['/product/price']),
                    rema_ean=product.get('/product/ean', ''),
                    rema_stk_count=product.get('/product/stk_count'),
                    ean_index=ean_index,
                    rema_category=product.get('/product/product_type', ''),
                    claimed_ids=matched_ids[key],
                    rema_price_per_kg=product.get('/product/price_per_kg'),
                )
                if m:
                    matches[key] = m

            # EAN retro-validering: et fuzzy-match mod en vare UDEN vægt (typisk
            # Dagrofa) kan være forkert uden at vægt-gaten kunne fange det. Men
            # samme EAN findes ofte i en Salling-butik MED vægt - er dén vægt
            # uforenelig med Rema-varens, er hele EAN'et et andet produkt, og
            # alle matches med det EAN droppes (fx Rema "TOMATSUPPE 400 g" der
            # matchede Spars vægtløse "Tomatsuppe" = Karolines Køkken 1 l).
            #
            # Samme mønster for procent: Dagrofa-navne udelader ofte selve
            # '%'-tegnet ("Tuborg Classic 0,0 6-Pk Ds"), så procent-gaten i
            # _find_generic_match læser det som "intet tal angivet" og lader
            # matchet igennem på navnescore alene. Findes samme EAN i en
            # butik, hvis navn HAR en '%'-angivelse der modsiger Rema, er hele
            # EAN'et forkert - ellers spredes fejlen videre til alle butikker
            # via EAN cross-fill nedenfor, uanset at deres egne kandidatnavne
            # (med korrekt '%') ville være blevet afvist enkeltvis.
            rema_w = product.get('/product/weight_g')
            # Brandfeltet SKAL med. _find_generic_match bruger den brand-
            # inkluderende udgave med en eksplicit kommentar om hvorfor (Rema
            # lægger ofte procenten dér og kun dér: "CARLSBERG 0,0%"), men
            # DENNE beregning - som styrer EAN-retro-validering, kryds-medlems-
            # arbitrage og EAN-cross-fill - udelod den. Samme variabelnavn,
            # to definitioner i samme funktion, og det var den svageste der
            # bestemte om en fejl blev spredt til alle butikker via cross-fill.
            rema_pcts = get_product_percents(
                f"{product['/product/title']} {product['/product/description']} "
                f"{product.get('/product/brand', '')}")
            # Samme felter som _find_generic_match bruger på Rema-siden (brandet
            # bærer ofte variant-info, fx "ARLA, ØKOLOGISK").
            rema_variants = _variant_flags(
                str(product['/product/title']),
                str(product['/product/description']),
                str(product.get('/product/brand', '')),
            )
            if matches:
                bad_eans = set()
                for m in matches.values():
                    ean = m.get('ean')
                    if not ean or ean in bad_eans:
                        continue
                    for key in DB_STORE_KEYS:
                        hit = store_data[key][3].get(ean)
                        if hit is None:
                            continue
                        hit_w = hit.get('_weight_g')
                        if hit_w and rema_w and not weights_compatible(rema_w, hit_w):
                            bad_eans.add(ean)
                            break
                        if not _percents_match(rema_pcts, hit.get('_pcts', frozenset())):
                            bad_eans.add(ean)
                            break
                        # Variant-retro: bærer samme EAN i en anden butik et
                        # eksplicit flag (øko/laktosefri/...), Rema-varen ikke
                        # har, er hele EAN'et en anden variant af varen - fx
                        # Rema "KARTOFLER" fuzzy-matchet til Dagrofas vægtløse
                        # "Kartofler", hvis EAN hos Salling hedder "Kartofler
                        # øko" (fanget ved audit 2026-07-12).
                        if not _variants_compatible(rema_variants, hit.get('_variants', _NO_VARIANT_FLAGS)):
                            bad_eans.add(ean)
                            break
                if bad_eans:
                    matches = {k: m for k, m in matches.items() if m.get('ean') not in bad_eans}

            # Kryds-medlems-validering: matches der modsiger HINANDEN på
            # vægt/procent (muligt når Rema-teksten selv udelader dem, så
            # gaten er ensidig pr. butik). Før cross-fill, så et droppet
            # EAN ikke spredes videre.
            matches = _drop_cross_conflicting_matches(matches, rema_w, rema_pcts)
            # ... og på variant-flag: en tavs kandidat droppes, når et andet
            # medlem eksplicit bekræfter et Rema-flag, kandidaten mangler.
            matches = _drop_variant_conflicting_matches(matches, rema_variants)

            # EAN cross-fill: if any match has EAN, try to find it in stores that missed.
            # Vægt-gate også her - cross-fill må ikke genindføre en vare, som
            # fuzzy-matchingens egne gates ville have afvist.
            found_ean = next(
                (m['ean'] for m in matches.values() if m.get('ean')),
                None
            )
            if found_ean:
                rema_stk = product.get('/product/stk_count')
                for key in DB_STORE_KEYS:
                    if key not in matches:
                        _, _, _, ean_index = store_data[key]
                        hit = ean_index.get(found_ean)
                        if (hit and id(hit) not in matched_ids[key]
                                and weights_compatible(rema_w, hit.get('_weight_g'))
                                and (rema_stk is None or hit.get('_stk_count') is None
                                     or rema_stk == hit.get('_stk_count'))
                                and _percents_match(rema_pcts, hit.get('_pcts', frozenset()))
                                and _variants_compatible(rema_variants, hit.get('_variants', _NO_VARIANT_FLAGS))):
                            matches[key] = hit

            # Store matches and track IDs
            product['/product/store_matches'] = {}
            for key, match in matches.items():
                product['/product/store_matches'][key] = match
                matched_ids[key].add(id(match))
                match_counts[key] += 1

            # Cheapest-store logic
            cheapest_price  = rema_effective
            cheapest_stores = [REMA_KEY]

            for key, match in matches.items():
                p = match['price']
                if is_price_cheaper(p, cheapest_price):
                    cheapest_price  = p
                    cheapest_stores = [key]
                elif is_price_equal(p, cheapest_price):
                    cheapest_stores.append(key)

            display_store = min(cheapest_stores, key=_cheapest_tie_break_key)
            product['/product/cheapest_at'] = display_store

            if display_store != REMA_KEY:
                _apply_cheapest_display(product, display_store, matches[display_store])
            else:
                product['/product/store'] = _STORE_CONFIGS[REMA_KEY]['label']
                product['/product/multi_deal'] = ''

            # Always record the Rema origin price so the store filter can find this
            # product even when it's promoted to display another store's badge
            product['/product/rema_price'] = rema_effective
            product['/product/rema_is_sale'] = product.get('/product/sale_price') is not None

            final_products.append(product)

        # Collect unmatched products from every secondary store
        unmatched = {
            key: [p for p in store_data[key][0] if id(p) not in matched_ids[key]]
            for key in DB_STORE_KEYS
        }

        # ===================================================================
        # Cross-store matching for comparison-store orphans (not linked to Rema).
        #
        # Product stages (by EAN status):
        #   Stage 1 - shared EAN across ≥2 stores → grouped here (EAN only, no fuzzy).
        #   Stage 2 - EAN but no cross-store match → solokort; passive fuzzy target.
        #   Stage 3 - no EAN → only stage that initiates fuzzy matching.
        #
        # Pipeline phases (do not confuse with product stages):
        #   Phase 1   - stage-1 EAN grouping
        #   Phase 2   - stage 3 initiates fuzzy vs unmatched (incl. stage-2 targets)
        #   Phase 2b  - stage 3 initiates fuzzy vs existing stage-1 groups
        #   Solokort  - remaining stage 2 + unmatched stage 3 as standalone cards
        # ===================================================================
        # Phase 1 - Stage 1: EAN grouping (always before fuzzy)
        # ===================================================================
        # stage1_components: {store_key: [(product, display_item), ...]}
        # Used in phase 2b so stage-3 products can fuzzy-match stage-1 groups.
        stage1_components: dict[str, list] = {key: [] for key in DB_STORE_KEYS}
        ean_to_group: dict[str, dict] = {}
        _poisoned_ean_group_keys: set = set()  # (ean, key) med >1 forskellig vare
        for key in DB_STORE_KEYS:
            for p in unmatched[key]:
                ean = p.get('ean', '').strip()
                if not ean or ean in ('nan', 'None', ''):
                    continue
                pair = (ean, key)
                if pair in _poisoned_ean_group_keys:
                    continue
                existing = ean_to_group.setdefault(ean, {}).get(key)
                if existing is not None and existing is not p:
                    # Samme kollisionsmønster som ean_index (fund C6): to
                    # forskellige varer i samme butik deler denne EAN.
                    # Udelukker butikkens bidrag til gruppen i stedet for
                    # at lade sidst-indlæste stille vinde.
                    _poisoned_ean_group_keys.add(pair)
                    del ean_to_group[ean][key]
                    continue
                ean_to_group[ean][key] = p
        if _poisoned_ean_group_keys:
            logger.warning(
                "EAN-kollision (fase 1): %d (ean, butik)-par udelukket fra "
                "EAN-gruppering: %s",
                len(_poisoned_ean_group_keys), sorted(_poisoned_ean_group_keys)[:20])

        for ean, group in ean_to_group.items():
            if len(group) < 2:
                continue  # stage 2: EAN but no cross-store match → solokort later
            for key, p in group.items():
                if p in unmatched[key]:
                    unmatched[key].remove(p)
            main_key = next(k for k in DB_STORE_KEYS if k in group)
            built = build_store_display_products([group[main_key]], main_key)
            if not built:
                continue
            display_item = built[0]
            cheapest_key   = main_key
            cheapest_price = group[main_key]['price']
            for key in DB_STORE_KEYS:
                if key in group:
                    display_item['/product/store_matches'][key] = group[key]
                    if group[key]['price'] < cheapest_price:
                        cheapest_price = group[key]['price']
                        cheapest_key   = key
            display_item['/product/cheapest_at']  = cheapest_key
            display_item['/product/cheaper_at']   = cheapest_key
            display_item['/product/is_any_sale']  = any(p.get('is_sale') for p in group.values())
            display_item['/product/rema_price']   = group[REMA_KEY]['price']               if REMA_KEY in group else 0
            display_item['/product/rema_image']   = group[REMA_KEY].get('image', '')       if REMA_KEY in group else display_item.get('/product/imageLink', '')
            display_item['/product/rema_is_sale'] = group[REMA_KEY].get('is_sale', False)  if REMA_KEY in group else False
            display_item['/product/multi_deal']   = group[main_key].get('multi_deal', '')
            if cheapest_key != main_key:
                _apply_cheapest_display(display_item, cheapest_key, group[cheapest_key])
            final_products.append(display_item)
            # Register stage-1 groups as passive targets for phase 2b
            for key, p in group.items():
                stage1_components[key].append((p, display_item))

        # ===================================================================
        # Phase 2 - Stage 3 initiates fuzzy matching (stages 1–2 are passive targets)
        # Stage-1 products already removed from unmatched; stage-2 EAN solokort remain.
        # ===================================================================
        logger.info("Cross-matching unmatched products across stores...")
        for key in DB_STORE_KEYS:
            for p in unmatched[key]:
                p['_cross_match_tokens'] = cross_store_tokens(p.get('_norm_name', ''))

        # Medlemskab spores på IDENTITET, ikke på værdi. `base_p not in
        # unmatched[base_key]` og `list.remove(p)` sammenligner dicts med DYB
        # værdi-lighed: to varer med samme navn, mærke, vægt og pris i samme
        # butik - hvilket sker i feeds med dublet-rækker - er == hinanden, så
        # remove() fjernede den FØRSTE lige dict, ikke nødvendigvis den rigtige
        # vare. Resten af pipelinen (matched_ids, claimed_ids) bruger allerede
        # id(); det gør fase 2/2b nu også. Bonus: O(1) i stedet for O(n) med en
        # dyb dict-sammenligning pr. element.
        still_unmatched = {key: {id(p) for p in unmatched[key]} for key in DB_STORE_KEYS}

        for base_key in DB_STORE_KEYS:
            for base_p in unmatched[base_key][:]:
                if id(base_p) not in still_unmatched[base_key]:
                    continue
                # Only stage 3 may initiate fuzzy - stages 1 and 2 never do
                if str(base_p.get('ean') or '').strip() not in ('', 'nan', 'None'):
                    continue

                base_title = str(base_p.get('name', ''))
                # Basen SKAL normaliseres nøjagtig som targeten. Før brugte
                # den en egen regex, der beholdt å og smed alle tal væk, mens
                # targeten kommer fra normalize_name (NFKD), hvor å bliver til
                # a. Token-snittet kunne derfor aldrig ramme for ord med å:
                # basens 'blåbær' mødte targetens 'blabær'. Tal-forskellen trak
                # desuden navnescoren ned på alle par. _norm_name er allerede
                # precomputed - den blev bare ikke brugt her.
                base_title_norm = base_p.get('_norm_name', '') or base_title.lower()
                base_tokens = base_p.get('_cross_match_tokens') or cross_store_tokens(base_title_norm)
                if not base_tokens:
                    continue

                cluster = {base_key: base_p}
                cluster_scores: dict = {}  # target_key -> name_score, til konflikt-oprydning nedenfor

                # ALLE andre butikker, ikke kun de efterfølgende. Kun stage 3
                # initierer fuzzy, så trekant-iterationen var ikke symmetrisk:
                # en stage-2-vare (med EAN) i en TIDLIG butik - og Salling-
                # butikkerne har næsten altid EAN - kunne aldrig nås af en
                # stage-3-initiator i en senere butik, fordi parret kun blev
                # vurderet med den tidlige butik som base. Resultatet var
                # solokort side om side med identiske titler (Pitabrød
                # Netto/Føtex, Kinakål Bilka/Meny).
                # Dobbelt-gruppering er ikke mulig: alle klyngemedlemmer
                # fjernes fra unmatched, og løkken springer over en base der
                # allerede er hentet ind i en anden klynge.
                for target_key in DB_STORE_KEYS:
                    if target_key == base_key:
                        continue
                    target_list = unmatched[target_key]
                    if not target_list:
                        continue
                    available = still_unmatched[target_key]

                    best_match = None
                    best_score = 0.0

                    for target_p in target_list:
                        if id(target_p) not in available:
                            continue  # allerede hentet ind i en anden klynge
                        # Stage 2 (EAN, no cross-store match) is a passive target here.
                        # Hele gate-kæden ligger i cross_store_pair_verdict, som
                        # fase 2b og måle-harnesset deler med denne løkke - se
                        # funktionens docstring for hvorfor kopierne blev slået
                        # sammen. Klynge-konsistens tjekkes IKKE her, men i
                        # konflikt-oprydningen efter target_key-løkken (fund H8).
                        accepted, name_score, reason = cross_store_pair_verdict(
                            base_p, target_p, base_title_norm, base_tokens)
                        record_gate_outcome(reason)
                        if not accepted:
                            continue

                        if name_score > best_score:
                            best_score = name_score
                            best_match = target_p

                    if best_match:
                        cluster[target_key] = best_match
                        cluster_scores[target_key] = best_score
                        record_match('fase2', base_key, base_p, target_key,
                                     best_match, best_score)

                # Konflikt-oprydning: hvert medlem er kun valideret mod
                # base_p ovenfor, ikke mod hinanden, så klyngen kan indeholde
                # indbyrdes modstridende medlemmer (samme ensidigheds-hul som
                # _drop_cross_conflicting_matches lukker i Rema-annoteringen -
                # her genbruges _group_compatible til det parvise tjek). Den
                # gamle udgave tjekkede dette INDE i target_key-løkken, mod
                # klyngen som den så ud der og da, hvilket gjorde den
                # VINDENDE kandidat blandt gensidigt uforenelige match
                # afhængig af DB_STORE_KEYS-rækkefølgen fra 3. medlem og
                # frem. Fjerner i stedet iterativt det lavest scorende
                # medlem i en konflikt, uafhængigt af hvilken butik der blev
                # behandlet først. Se matchmotor-revisionen 2026-08-16,
                # fund H8.
                while True:
                    loser_key = None
                    member_keys = [k for k in cluster if k != base_key]
                    for i, k1 in enumerate(member_keys):
                        p1 = cluster[k1]
                        for k2 in member_keys[i + 1:]:
                            p2 = cluster[k2]
                            if _group_compatible(
                                    p1.get('_weight_g'), p1.get('_stk_count'), p1['_pcts'],
                                    [p2], p1['_variants'], p1['_meats']):
                                continue
                            loser_key = k1 if cluster_scores.get(k1, 0.0) <= cluster_scores.get(k2, 0.0) else k2
                            break
                        if loser_key is not None:
                            break
                    if loser_key is None:
                        break
                    del cluster[loser_key]
                    del cluster_scores[loser_key]

                if len(cluster) > 1:
                    for k, p in cluster.items():
                        still_unmatched[k].discard(id(p))

                    main_key = base_key
                    built = build_store_display_products([cluster[main_key]], main_key)
                    if built:
                        display_item = built[0]
                        cheapest_key = main_key
                        cheapest_price = cluster[main_key]['price']

                        for k, matched_p in cluster.items():
                            display_item['/product/store_matches'][k] = matched_p
                            if matched_p['price'] < cheapest_price:
                                cheapest_price = matched_p['price']
                                cheapest_key = k

                        display_item['/product/cheapest_at'] = cheapest_key
                        display_item['/product/cheaper_at'] = cheapest_key
                        display_item['/product/is_any_sale'] = any(p.get('is_sale') for p in cluster.values())

                        if cheapest_key != main_key:
                            _apply_cheapest_display(display_item, cheapest_key, cluster[cheapest_key])

                        final_products.append(display_item)

        # ===================================================================
        # Phase 2b - Stage 3 initiates fuzzy against stage-1 EAN groups (passive targets)
        # ===================================================================
        # _cross_match_tokens er precomputed for unmatched[key] i fase 2
        # (se løkken lige ovenfor "Cross-matching unmatched products across
        # stores"), men stage1_components' targets kommer fra stage-1-
        # grupperingen og fik den ALDRIG sat - uden dette pre-pass blev token-
        # sættet genberegnet for hvert eneste (base, target)-par i inderloopet
        # nedenfor, selvom det er det samme for et givet target hver gang.
        # Målt (bench_fase2b.py, 9,3 mio. rigtige par): 0,417 -> 0,145 µs/par,
        # identiske matches.
        for _key in DB_STORE_KEYS:
            for _p, _display_item in stage1_components[_key]:
                if '_cross_match_tokens' not in _p:
                    _p['_cross_match_tokens'] = cross_store_tokens(_p.get('_norm_name', ''))

        for base_key in DB_STORE_KEYS:
            for base_p in unmatched[base_key][:]:
                if id(base_p) not in still_unmatched[base_key]:
                    continue
                if str(base_p.get('ean') or '').strip() not in ('', 'nan', 'None'):
                    continue  # only stage 3 initiates fuzzy

                base_title = str(base_p.get('name', ''))
                # Bruges kun af gruppe-valideringen nedenfor; resten af gate-
                # kæden læser felterne direkte i cross_store_pair_verdict.
                base_weight = base_p.get('_weight_g')
                base_stk = base_p.get('_stk_count')
                base_variants = base_p['_variants']
                base_pcts = base_p['_pcts']
                # Basen SKAL normaliseres nøjagtig som targeten. Før brugte
                # den en egen regex, der beholdt å og smed alle tal væk, mens
                # targeten kommer fra normalize_name (NFKD), hvor å bliver til
                # a. Token-snittet kunne derfor aldrig ramme for ord med å:
                # basens 'blåbær' mødte targetens 'blabær'. Tal-forskellen trak
                # desuden navnescoren ned på alle par. _norm_name er allerede
                # precomputed - den blev bare ikke brugt her.
                base_title_norm = base_p.get('_norm_name', '') or base_title.lower()
                base_tokens = base_p.get('_cross_match_tokens') or cross_store_tokens(base_title_norm)
                if not base_tokens:
                    continue

                best_display_item = None
                best_score = 0.0

                for target_key in DB_STORE_KEYS:
                    if target_key == base_key:
                        continue
                    for target_p, display_item in stage1_components[target_key]:
                        if base_key in display_item['/product/store_matches']:
                            continue  # base_key allerede repræsenteret i denne gruppe

                        # Præcis samme gate-kæde som fase 2 - delt via
                        # cross_store_pair_verdict, så de to faser ikke kan
                        # drive fra hinanden igen (længde-forfilteret manglede
                        # her indtil fund H7).
                        accepted, name_score, reason = cross_store_pair_verdict(
                            base_p, target_p, base_title_norm, base_tokens)
                        record_gate_outcome(reason)
                        if not accepted:
                            continue

                        # Gruppe-validering: gates ovenfor tjekker kun target_p
                        # (repræsentanten) - et vægtløst medlem må ikke være
                        # bagdør ind i en gruppe, hvis øvrige medlemmer
                        # modsiger basen på vægt/stk/procent.
                        if not _group_compatible(base_weight, base_stk, base_pcts,
                                                 display_item['/product/store_matches'].values(),
                                                 base_variants, base_p['_meats']):
                            continue

                        if name_score > best_score:
                            best_score = name_score
                            best_display_item = display_item

                if best_display_item is not None:
                    still_unmatched[base_key].discard(id(base_p))
                    record_match('fase2b', base_key, base_p, 'gruppe',
                                 {'name': best_display_item.get('/product/title', '')},
                                 best_score)
                    best_display_item['/product/store_matches'][base_key] = base_p
                    if is_price_cheaper(base_p['price'], effective_display_price(best_display_item)):
                        best_display_item['/product/cheapest_at'] = base_key
                        best_display_item['/product/cheaper_at'] = base_key
                        _apply_cheapest_display(best_display_item, base_key, base_p)

        # ===================================================================
        # Solokort - stage 2 (EAN, unmatched) + unmatched stage 3 (no EAN)
        # ===================================================================
        # Fase 2/2b mutérer kun still_unmatched (identitets-mængderne), ikke
        # listerne - se kommentaren ved still_unmatched. Listerne bringes derfor
        # i overensstemmelse her, før de læses.
        for key in DB_STORE_KEYS:
            unmatched[key] = [p for p in unmatched[key] if id(p) in still_unmatched[key]]

        for key in DB_STORE_KEYS:
            for p in unmatched[key]:
                final_products.extend(build_store_display_products([p], key))

        # Fjern interne precompute-felter fra store_matches, så de ikke fylder
        # i app_cache/D1 (sets kan desuden ikke serialiseres pænt til JSON).
        _transient_keys = ('_type', '_flavors', '_forms', '_variants', '_is_pl', '_pcts', '_meats', '_cross_match_tokens')
        for _p in final_products:
            for _m in (_p.get('/product/store_matches') or {}).values():
                if isinstance(_m, dict):
                    for _k in _transient_keys:
                        _m.pop(_k, None)

        counts_str = ', '.join(f"{match_counts[k]} matched to {_STORE_CONFIGS[k]['label']}" for k in DB_STORE_KEYS)
        logger.info(
            f"\nFinal product list: {len(final_products)} products "
            f"({len(rema_products)} Rema + {len(final_products) - len(rema_products)} unmatched comparison cards), "
            f"{counts_str}"
        )
        # Deduplicer final_products på billedeURL - samme billede = samme produkt.
        # Salling-kæderne (Netto/Føtex/Bilka) deler samme feed, så samme vare kan
        # optræde som flere kort med identisk billede. Vi beholder ét kort (så varen
        # kun vises én gang på siden), men fletter dublettens butiksdata ind i det
        # beholdte korts store_matches, så overlay + kurv fortsat viser varen i ALLE
        # butikker, hvor den findes. Placeholder/logo-billeder tæller ikke som unikke.
        seen_imgs: dict = {}
        deduped: list = []
        for _p in final_products:
            _img = str(_p.get('/product/imageLink', '')).strip()
            if not _img or _img in ('nan', 'None') or _img in _PLACEHOLDER_IMGS:
                deduped.append(_p)  # ingen unik billedeURL → inkluder altid
            elif _img not in seen_imgs:
                seen_imgs[_img] = _p
                deduped.append(_p)
            elif _dedup_same_product(seen_imgs[_img], _p):
                # Duplikat-billede + sanity-check ok → skjul kortet, men bevar butiksdata
                _merge_duplicate_into_kept(seen_imgs[_img], _p)
            else:
                # Samme billede men uforenelig vægt/navn (Salling genbruger produkt-
                # foto på tværs af pakkestørrelser, fx 0.33 l og 24-pak) → behold begge
                deduped.append(_p)
        logger.info(f"Dedupliceret: {len(final_products)} -> {len(deduped)} produkter (fjernede {len(final_products)-len(deduped)} dubletter)")
        final_products = deduped

        final_products = _merge_cards_sharing_ean(final_products)

        flush_match_trace()

        return final_products
        
    except Exception as e:
        logger.error(f"Error in fetch_and_parse_xml: {str(e)}")
        traceback.print_exc()
        return []


def _notify_website_refresh():
    """Push fresh cache to the live site right after Supabase upload."""
    app_url = (os.getenv('APP_URL') or '').rstrip('/')
    secret = os.getenv('CACHE_REFRESH_SECRET') or ''
    if not app_url or not secret:
        logger.info(
            "APP_URL/CACHE_REFRESH_SECRET ikke sat - genstart hjemmesiden eller sæt secrets for øjeblikkelig opdatering"
        )
        return
    try:
        import httpx
        with httpx.Client(timeout=30.0) as client:
            res = client.post(
                f"{app_url}/api/refresh-cache",
                headers={"X-Cache-Secret": secret},
            )
            res.raise_for_status()
            logger.info("Hjemmesidens cache opdateret med det samme (%s produkter)", res.json().get('products'))
    except Exception as e:
        logger.error("Kunne ikke opdatere hjemmesidens cache: %s", e)


def run_rema_updater():
    """Hent kun Rema XML og opdater Rema-priser i eksisterende cache."""
    logger.info("Starter Rema-opdatering...")
    fresh_rema = _fetch_rema_products_only()
    if not fresh_rema:
        return

    cached, _old_idx = _load_app_cache()
    if cached:
        products = merge_rema_into_cache(cached, fresh_rema)
    else:
        logger.info("Ingen eksisterende cache - uploader kun Rema-produkter")
        products = []
        for p in fresh_rema:
            item = dict(p)
            item['/product/store_matches'] = {}
            item['/product/rema_price'] = _rema_effective_price(p)
            item['/product/rema_is_sale'] = p.get('/product/sale_price') is not None
            item['/product/cheapest_at'] = REMA_KEY
            products.append(item)

    annotate_lowest_prices(products)
    search_index = {k: list(v) for k, v in build_search_index(products, normalize_name, flavor_fn=get_search_flavor_keywords).items()}
    if _save_app_cache(products, search_index):
        record_prices_batch(collect_store_prices(products))
        _notify_website_refresh()


def run_updater():
    logger.info("Starter opdatering af produkt-cache...")
    fresh = fetch_and_parse_xml()
    if not fresh:
        return
    # JSON-serialisering: sæt → liste for alle mængder (fx matched_variants)
    for p in fresh:
        if 'matched_variants' in p and isinstance(p['matched_variants'], set):
            p['matched_variants'] = list(p['matched_variants'])

    # Dæknings-værn: load_all_comparison_data fanger fejl PR. BUTIK og
    # fortsætter med en tom liste for den butik (se except-grenen dér), så en
    # butik der fejler under indlæsning gør IKKE 'fresh' tom - kun uden
    # prissammenligninger for den butik. Fejler ALLE andre butikker end Rema
    # samtidig (fx et kort Supabase-udfald kl. 01:00), henter Rema-XML'en
    # stadig fint (anden host, egen retry), og "if not fresh" ovenfor lukker
    # derfor intet: 1 Rema-produkt er nok til at passere. Uden dette værn
    # ville den cache blive gemt atomisk, seedet til D1 og cache_version
    # bumpet - hele sitet ville vise Rema-only uden en eneste
    # prissammenligning, med grønt CI hele vejen.
    matched = sum(1 for p in fresh if p.get('/product/store_matches'))
    coverage = matched / len(fresh) if fresh else 0
    if len(fresh) >= 1000 and coverage < 0.20:
        logger.error(
            "Kun %.1f%% af %d produkter har en butiksmatch (sund baseline er "
            "~50%%+) - gemmer IKKE. Sandsynlig årsag: én eller flere butikker "
            "fejlede stille under indlæsning (load_all_comparison_data).",
            coverage * 100, len(fresh),
        )
        return

    # Størrelsesværn: samme fejlklasse, men fanger også et generelt kollaps
    # der ikke nødvendigvis rammer matchingen (fx selve Rema-hentningen
    # leverede færre varer end normalt).
    gammel, _ = _load_app_cache()
    if len(gammel) > 1000 and len(fresh) < len(gammel) * 0.7:
        logger.error(
            "Kun %d produkter mod %d i nuværende cache (under 70%%) - gemmer "
            "IKKE",
            len(fresh), len(gammel),
        )
        return

    annotate_lowest_prices(fresh)
    search_index = {k: list(v) for k, v in build_search_index(fresh, normalize_name, flavor_fn=get_search_flavor_keywords).items()}
    if _save_app_cache(fresh, search_index):
        record_prices_batch(collect_store_prices(fresh))
        prune_cart_events()
        check_price_alerts(fresh)
        _notify_website_refresh()
    elif not db_available():
        logger.info("Supabase ikke tilgængelig - lokal cache gemt som fallback")

def push_local_cache_to_supabase():
    """Læs app_cache_local.json og push direkte til Supabase uden at scrape."""
    if not os.path.exists(_LOCAL_CACHE_FILE):
        logger.error(f"Lokal cache-fil ikke fundet: {_LOCAL_CACHE_FILE}")
        return False
    try:
        with open(_LOCAL_CACHE_FILE, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        products = payload.get('products', [])
        search_index = payload.get('search_index', {})
        logger.info(f"Pusher {len(products)} produkter fra lokal cache til Supabase...")
        success = _save_app_cache(products, search_index)
        if success:
            logger.info("Push til Supabase app_cache lykkedes.")
            record_prices_batch(collect_store_prices(products))
            _notify_website_refresh()
        else:
            logger.error("Push til Supabase app_cache fejlede.")
        return success
    except Exception as e:
        logger.error(f"Fejl ved push af lokal cache: {e}")
        return False


if __name__ == '__main__':
    import sys
    if '--rema-only' in sys.argv:
        run_rema_updater()
    elif '--push-local' in sys.argv:
        push_local_cache_to_supabase()
    else:
        run_updater()
