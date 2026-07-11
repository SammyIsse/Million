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
import traceback
import random
import threading

from supabase import create_client

from app_support import (
    configure_logging, db_available,
    build_search_index, logger,
    DEFAULT_HTTP_HEADERS, _STORE_CONFIGS, format_price,
    normalize_name, fuzzy_score,
    parse_weight_to_grams, parse_stk_count, weights_compatible,
    _PLACEHOLDER_IMGS,
    CAT_ANDET, CAT_FRUGT_GROENT, unify_category,
    compute_image_hash, phash_hex_to_int, hash_candidate_indices,
    _HASH_CANDIDATE_MAX_DIST,
    is_organic, is_lactose_free, is_sugar_free, is_gluten_free,
)


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
                    price = sanitize_price(price, ppk, weight_g)
                    
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
                            
                    multi_deal = str(row.get('multikob') or '').strip()
                    if multi_deal in ('nan', 'None'):
                        multi_deal = ''

                    name_str = str(row.get('navn') or '')
                    brand_str = str(row.get('producent') or '')
                    kategori_str = str(row.get('kategori') or '')

                    products.append({
                        'name':        name_str,
                        'brand':       brand_str,
                        'weight':      weight_str,
                        'kg_price':    ppk,
                        'price':       price,
                        'normal_price': normal_price,
                        'is_sale':     is_sale,
                        'multi_deal':  multi_deal,
                        '_norm_name':  normalize_name(name_str),
                        '_weight_g':   weight_g,
                        '_stk_count':  _stk_count_of(weight_str, name_str),
                        'image':       str(row.get('billede_url') or ''),
                        '_image_hash': p_hash_hex,
                        '_hash_int':   p_hash_int,
                        'ean':         ean,
                        'Kategori':    kategori_str,
                        # Precompute (fix: matchingens inderloops genberegnede
                        # disse pr. kandidat-par - nu én gang pr. produkt)
                        '_type':       unify_category(kategori_str, name_str),
                        '_flavors':    get_product_flavors(name_str),
                        '_forms':      get_product_form(name_str),
                        '_pcts':       get_product_percents(name_str),
                        '_variants':   _variant_flags(name_str, '', brand_str),
                        '_is_pl':      is_private_label(brand_str, name_str),
                    })
                    
            except Exception as e:
                logger.warning("Error fetching %s from Supabase: %s", cfg['label'], e)
                
        # Building indexes
        token_idx: dict = {}
        hash_list = []
        ean_index: dict = {}
        for i, p in enumerate(products):
            for token in p['_norm_name'].split():
                if len(token) >= 4:
                    token_idx.setdefault(token, set()).add(i)
            p_hash_int = p.get('_hash_int')
            if p_hash_int is not None:
                hash_list.append((i, p_hash_int))
            ean = p.get('ean')
            if ean:
                ean_index[ean] = p
        
        result = (products, token_idx, hash_list, ean_index)
        _store_caches[store_key] = result
        logger.info("Loaded %s products from Supabase for %s", len(products), cfg['label'])
        return result


import concurrent.futures

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


def sanitize_price(price, ppk, weight_g):
    """Fallback validation to fix scraped prices that incorrectly concatenated weight and kg-price."""
    if price > 0 and ppk is not None and weight_g is not None and weight_g > 0:
        expected_price = ppk * (weight_g / 1000.0)
        if expected_price > 0 and (price > expected_price * 2.5 or price < expected_price * 0.3):
            # If the price is extremely off, trust the kg-price and weight
            return round(expected_price, 2)
    return price


def is_price_cheaper(new_p, current_p):
    """Returns True if new_p is strictly cheaper than current_p."""
    if new_p is None: return False
    return new_p < current_p - 0.001


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


_FLAVOR_MAP = {
    # Sodavand / juice
    'cola': 'cola',
    'vindrue': 'grape', 'grape': 'grape',
    'hyldeblomst': 'elderflower', 'elderflower': 'elderflower',
    'mango': 'mango',
    'ananas': 'pineapple', 'pineapple': 'pineapple',
    'appelsin': 'orange', 'orange': 'orange',
    'citron': 'lemon', 'lemon': 'lemon',
    'lime': 'lime',
    # 'sour' og 'sour cream' deler kanonisk navn: "Kims Sour & Onion" er en
    # forkortelse af "sour cream & onion", så et skel ville afvise korrekte
    # chips-matches. Slik-siden ("Katjes Sour") rammes ikke - begge sider af
    # et korrekt slik-match nævner 'sour'.
    'sour': 'sour', 'sour cream': 'sour', 'sourcream': 'sour',
    'granatæble': 'pomegranate', 'pomegranate': 'pomegranate',
    'tranebær': 'cranberry', 'cranberry': 'cranberry',
    # Frugt / bær (yoghurt, skyr, marmelade osv.)
    'hindbær': 'raspberry', 'raspberry': 'raspberry',
    'jordbær': 'strawberry', 'strawberry': 'strawberry',
    'blåbær': 'blueberry', 'blueberry': 'blueberry',
    'solbær': 'blackcurrant', 'blackcurrant': 'blackcurrant',
    'stikkelsbær': 'gooseberry',
    'kirsebær': 'cherry', 'cherry': 'cherry',
    'pære': 'pear', 'pear': 'pear',
    'banan': 'banana', 'banana': 'banana',
    'æble': 'apple', 'apple': 'apple',
    'fersken': 'peach', 'peach': 'peach',
    'abrikos': 'apricot', 'apricot': 'apricot',
    'guava': 'guava',
    'passionsfrugt': 'passionfruit', 'passion': 'passionfruit',
    'kokos': 'coconut', 'coconut': 'coconut',
    'rabarber': 'rhubarb', 'rhubarb': 'rhubarb',
    'melon': 'melon',
    # Vandmelon giver BÅDE watermelon og melon: butikker forkorter til "Melon"
    # ("Extra Refresh Melon"), som ellers ville afvises asymmetrisk, mens
    # honning-/galiamelon stadig adskilles fra vandmelon på watermelon-smagen.
    'watermelon': ('watermelon', 'melon'), 'vandmelon': ('watermelon', 'melon'),
    'drue': 'grape',  # dækker også "druer" (ordstart); "vindruer" fanges af 'vindrue'
    'skovbær': 'forestberry',
    # Krydderurter/krydderier som varianter ("Tomatsuppe m. timian" ≠ "Tomatsuppe")
    'timian': 'thyme',
    'basilikum': 'basil',
    'oregano': 'oregano',
    'hvidløg': 'garlic',
    'h.løg': 'garlic',  # Dagrofa-forkortelse ("Flødeost H.Løg") - konsumeres før 'løg' (onion)
    'chili': 'chili',
    'karry': 'curry',
    # Smagsvarianter
    'naturel': 'natural', 'natural': 'natural', 'naturlig': 'natural',
    'vanilje': 'vanilla', 'vanilla': 'vanilla',
    'kakao': 'cocoa', 'cocoa': 'cocoa',
    'chokolade': 'chocolate', 'chocolate': 'chocolate',
    'honning': 'honey', 'honey': 'honey',
    'karamel': 'caramel', 'caramel': 'caramel',
    'karameller': 'caramel',  # flertalsform: 'karamel' står internt i "lakridskarameller" uden ordgrænse
    'mint': 'mint', 'mynte': 'mint',
    'spearmint': 'mint',  # ordgrænse-matcheren fanger ikke 'mint' inde i "spearmintsmag"
    'kaffe': 'coffee', 'coffee': 'coffee',
    'choko': 'chocolate',  # Rema-forkortelse ("choko" i titel/desc, ikke "chokolade")
    'choco': 'chocolate',  # engelsk forkortelse ("Cruesli Dark Choco", "Choco Treats")
    'chokol': 'chocolate',  # trunkeret feed-navn ("...m. mælkechokol")
    # Salte snack-smage (chips/tortilla, syltevarer osv.) - fanger fejlmatch som
    # "Røget torskelever" ↔ "Røget bacon" og "Syltede agurker" ↔ "Syltede rødløg".
    # Bemærk: 'salt' (også "m. salt"/"havsalt") og 'creme fraiche' er bevidst
    # udeladt. Salling beskriver mærkevarer generisk ("Chips m. salt" = Taffel/
    # Kettle/Danske Franske, hvis egne navne ikke nævner salt), og creme fraiche
    # er oftest selve MEJERIVAREN med afkortede navne ("CREME F.", "Fraiche 9%")
    # - begge ville afvise langt flere korrekte matches end de fanger fejl.
    'paprika': 'paprika',
    'bacon': 'bacon',
    'løg': 'onion', 'onion': 'onion',  # 'hvidløg' (garlic) konsumeres først, se _extract_keywords
    # Fisketyper: fisken ER produktnavnet ("Tun i tomat" ≠ "Makrel i tomat"),
    # så udeladelses-risikoen fra kød (frikadeller nævner ikke 'svin') findes
    # ikke her. Fanger fx tun↔makrel, ørred↔makrel og mørksej↔laks.
    'laks': 'laks', 'tun': 'tun', 'torsk': 'torsk', 'makrel': 'makrel',
    'sild': 'sild', 'ørred': 'ørred', 'rødspætte': 'rødspætte',
    'reje': 'reje', 'rejer': 'reje',
    'musling': 'musling', 'blåmusling': 'musling',
    'mørksej': 'sej', 'sejfilet': 'sej',  # bart 'sej' er for kollisionsudsat
}


# Kontekster hvor et smagsnøgleord IKKE er en smag: "druesukker" (glukose, ikke
# drue-smag), "colada" (piña colada indeholder 'cola'), brandet "Løgismose"
# (indeholder 'løg'), "tunge" (okse-/røget tunge, ikke 'tun') og fiskebrandet
# "Neptun" (ender på 'tun'). Fjernes fra teksten før nøgleords-scanning.
# "chocolat"/"chocolatier" (indeholder 'cola') klares af ordgrænse-kravet i
# _extract_keywords.
_FLAVOR_BLOCKERS_RE = re.compile(r'druesukker|colada|løgismose|tunge|neptun')


def _compile_keyword_patterns(keyword_map) -> list:
    """(mønster, kanoniske navne)-liste, længste nøgleord først.

    Mønstret kræver ordgrænse i mindst én ende af forekomsten: rene
    substring-hits inde i et andet ord ('cola' i "chocolat") afvises, mens
    danske sammensætninger stadig fanges i begge ender ("jordbærsmag",
    "mælkechokolade"). Kanonisk navn kan være en tuple, når ét nøgleord skal
    give flere smage (fx vandmelon → watermelon + melon)."""
    patterns = []
    for kw, canonical in sorted(keyword_map, key=lambda x: -len(x[0])):
        esc = re.escape(kw)
        patterns.append((
            re.compile(rf'(?<![a-zæøå]){esc}|{esc}(?![a-zæøå])'),
            (canonical,) if isinstance(canonical, str) else tuple(canonical),
        ))
    return patterns


# Sammensætnings-suffikser skjuler smagen midt i ordet ("saltkaramelSMAG",
# "pebermynteFYLD", "mælkechokoladeOVERTRÆK") for ordgrænse-kravet - de
# strippes, så smagsordet ender ved ordgrænsen igen. Lookbehind sikrer, at
# fritstående ord ("smag", "fyld") ikke røres. Strip kan kun EKSPONERE
# smagsord, aldrig fjerne dem.
_SMAG_SUFFIX_RE = re.compile(r'(?<=[a-zæøå])(?:smag(?:s|en)?|fyld|overtræk|stang|stænger)\b')


def _extract_keywords(text_lower: str, patterns: list) -> set:
    """Scan med længste nøgleord først og konsumér hver forekomst, så et kortere
    nøgleord ikke gen-matcher inde i et længere ("sour cream" skal ikke også
    give 'sour'; "hvidløg" (garlic) skal ikke også give 'løg' (onion)).

    Kører til fixpoint: en konsumering kan eksponere en ordgrænse for et
    nøgleord, der allerede var afprøvet ("chokokaramel" → 'choko' konsumeres
    og frigør 'karamel', som er længere og derfor blev scannet først)."""
    found = set()
    changed = True
    while changed:
        changed = False
        for pattern, canonicals in patterns:
            m = pattern.search(text_lower)
            if m:
                found.update(canonicals)
                text_lower = f"{text_lower[:m.start()]} {text_lower[m.end():]}"
                changed = True
    return found


# Kompileret én gang ved opstart - get_product_flavors kaldes i matchingens
# inderloops, så sortering/kompilering må ikke ske pr. kald.
_FLAVOR_PATTERNS = _compile_keyword_patterns(_FLAVOR_MAP.items())


def get_product_flavors(text: str) -> set:
    """Udtræk kanoniske smagsnavne fra produkttekst (længste nøgleord først)."""
    cleaned = _SMAG_SUFFIX_RE.sub(' ', _FLAVOR_BLOCKERS_RE.sub(' ', text.lower()))
    return _extract_keywords(cleaned, _FLAVOR_PATTERNS)


# Procent-angivelser i produktnavne (fedt-%, alkohol-%, kakao-%) er reelle
# produktegenskaber: "Tuborg Classic 4,6%" og "Tuborg Classic 0,0% alkoholfri"
# er IKKE samme vare, og det samme gælder "Piskefløde 38%" ↔ "36%". Gaten er
# symmetrisk og kun aktiv, når BEGGE sider angiver procenter - en side, der
# blot udelader tallet ("Piskefløde"), er ikke en modsigelse.
_PCT_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*%')


def get_product_percents(text: str) -> frozenset:
    """Alle procenttal nævnt i teksten, afrundet til 1 decimal."""
    return frozenset(round(float(m.replace(',', '.')), 1) for m in _PCT_RE.findall(text))


def _percents_match(base_pcts: frozenset, cand_pcts: frozenset) -> bool:
    """Falsk kun når begge sider angiver procenter uden én fælles værdi."""
    return not base_pcts or not cand_pcts or bool(base_pcts & cand_pcts)


def _group_compatible(base_weight, base_stk, base_pcts: frozenset, members) -> bool:
    """Valider en EAN-løs base mod ALLE medlemmer af en stage-1 EAN-gruppe.

    Fase 2b's gates sammenligner kun med ét gruppemedlem ad gangen, og et
    medlem uden vægtdata (typisk Dagrofa) kan derfor fungere som bagdør ind
    i en gruppe, hvis ØVRIGE medlemmer beviseligt modsiger basen - fx Lidl
    "BELBAKE Fødselsdagsboller 350 g" der kom ind via mk's vægtløse "Amo
    Fødselsdagsboller", selvom Bilka/Føtex i samme gruppe angiver 500 g.
    EAN-gruppens medlemmer er autoritativt samme vare, så ét medlem med
    uforenelig vægt, stk-antal eller procent afviser hele gruppen (samme
    princip som EAN retro-valideringen i Rema-annoteringen)."""
    for m in members:
        if not isinstance(m, dict):
            continue
        if not weights_compatible(base_weight, m.get('_weight_g')):
            return False
        if base_stk is not None and m.get('_stk_count') is not None and base_stk != m.get('_stk_count'):
            return False
        if not _percents_match(base_pcts, m.get('_pcts', frozenset())):
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
    if len(matches) < 2 or (rema_w and rema_pcts):
        return matches
    items = list(matches.items())
    conflicted = set()
    for i, (k1, m1) in enumerate(items):
        for k2, m2 in items[i + 1:]:
            e1 = str(m1.get('ean') or '')
            if e1 and e1 == str(m2.get('ean') or ''):
                continue
            if not rema_w and not weights_compatible(m1.get('_weight_g'), m2.get('_weight_g')):
                conflicted.update((k1, k2))
            elif not rema_pcts and not _percents_match(m1.get('_pcts', frozenset()),
                                                       m2.get('_pcts', frozenset())):
                conflicted.update((k1, k2))
    if not conflicted:
        return matches
    return {k: m for k, m in matches.items() if k not in conflicted}


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


def _variants_compatible(rema_variants: tuple, cand_variants: tuple) -> bool:
    """Variant-gate (øko, laktosefri, sukkerfri, glutenfri): kun hård afvisning ved reel modsigelse.

    Rema-produktets beskrivelse nævner ofte en attribut (fx "laktosefri")
    som en sammenligningsbutiks kortfattede varenavn ikke gentager - det er
    ikke en modsigelse, blot et kortere navn. Men hvis SAMMENLIGNINGSBUTIKKEN
    eksplicit påstår en attribut Rema-produktet ikke nævner, er det derimod
    en reel forskel (fx match mod en tydeligt økologisk vare)."""
    for rema_flag, cand_flag in zip(rema_variants, cand_variants):
        if cand_flag and not rema_flag:
            return False
    return True


def _find_generic_match(rema_title, rema_description, products, token_idx, hash_list, rema_brand='', rema_weight_g=None, threshold=0.60, rema_image_hash='', rema_price=0.0, rema_ean='', rema_stk_count=None, ean_index=None, rema_category='', claimed_ids=None):
    """Token-indexed fuzzy match used by all store comparisons.

  Product stages (EAN status - see README «Product matching»):
    Stage 1 - EAN match across stores (EAN lookup only, no fuzzy).
    Stage 2 - EAN but no match (passive fuzzy target only).
    Stage 3 - No EAN (may initiate fuzzy matching).

    Rema products have no EAN, so this function always acts as a stage-3 initiator
    against comparison-store candidates (stages 1–3).

    Fuzzy attributes (stage 3 initiator):
    - Name   - primary similarity score
    - Type   - category gate (types_compatible)
    - Weight - unit weight/volume gate (weights_compatible)
    - Quantity - package unit count gate (_stk_count); separate from weight

    Scoring components (all additive):
    1. Name fuzzy score          - basis 0..1 via SequenceMatcher
    2. Brand similarity boost    - up to +0.30 when brands match (e.g. Arla↔Arla)
    3. Image perceptual hash     - up to +0.40 when pHash distance is low

    Gates (hard reject before scoring):
    A. Brand-pairing: private-label ↔ private-label only.
    B. Type: product category must match when both sides are known.
    C. Weight: candidates whose unit weight differs > max(_WEIGHT_TOLERANCE_G, 8%) are skipped.
    D. Quantity: skip when both sides have _stk_count and they differ.
    E. Price sanity: reject if store price > 5× the Rema price.
    F. Token-overlap: first 4-char title token must appear in candidate name (relaxed if images match).
    G. Variant (øko/laktosefri/sukkerfri/glutenfri): only rejects when the CANDIDATE
       explicitly claims an attribute the Rema product doesn't have - a Rema
       product mentioning e.g. "laktosefri" that a terser candidate name omits
       is not treated as a contradiction (see _variants_compatible).
    H. Claimed: candidates already matched by an earlier Rema product in this
       run are skipped (claimed_ids), so two distinct Rema SKUs can't both
       claim the same comparison-store listing.
    I. Form (drik/budding/mousse/skyr/kefir/yoghurt/shake): same asymmetry as
       flavor - rejects when the candidate claims a product form the Rema
       product's own text doesn't mention (see _forms_match). Prevents e.g.
       an Arla Protein DRINK from matching an Arla Protein PUDDING just
       because both share generic tokens like "arla"/"protein"/"choko".

    Candidate discovery: token index plus pHash neighbours (hash_list) when Rema has image_hash.
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
    rema_type = unify_category(str(rema_category), str(rema_title))
    base_is_pl = is_private_label(rema_brand, rema_title)
    rema_variants = _variant_flags(rema_title, rema_description, rema_brand)
    # Rema-brandfeltet bærer ofte smags-/form-info som titel+beskrivelse udelader
    # (fx brand "ARLA, SMAG AF CHOKOLADE KARAMEL" på en vare med titel "PROTEIN
    # TO GO") - uden brand her fejlvurderede smags-gaten Rema-siden som "ingen
    # smag", og afviste dermed korrekte matches mod butikker med fyldigere navne.
    rema_flavors = get_product_flavors(f"{rema_title} {rema_description} {rema_brand}")
    rema_forms = get_product_form(f"{rema_title} {rema_description} {rema_brand}")
    rema_pcts = get_product_percents(f"{rema_title} {rema_description}")

    r_hash_int = phash_hex_to_int(rema_image_hash)

    # Token-baserede kandidater
    candidate_indices = set()
    primary_norm = rema_title_norm if rema_title_norm else rema_norms[0]
    for token in primary_norm.split():
        if len(token) >= 4 and token in token_idx:
            candidate_indices |= token_idx[token]

    # Fallback: include description tokens if title gave nothing
    if not candidate_indices:
        for norm in rema_norms[1:]:
            for token in norm.split():
                if len(token) >= 4 and token in token_idx:
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

        # Gate: Variant-linjer (øko, lacto/laktosefri, sukkerfri, glutenfri)
        if not near_identical_photo and not _variants_compatible(rema_variants, p['_variants']):
            continue

        # Gate: Smagsvariant (jordbær ≠ pære/banan, naturel ≠ jordbær osv.)
        if not near_identical_photo and not _flavors_match(rema_flavors, p['_flavors']):
            continue

        # Gate: Produktform (drik ≠ budding ≠ mousse osv.)
        if not near_identical_photo and not _forms_match(rema_forms, p['_forms']):
            continue

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
        if base_is_pl != p_is_pl and name_score < 0.70:
            continue

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

        # Gate D: Dairy variant + first-token checks
        if rema_title_norm:
            dairy_types = ['mini', 'let', 'skummet', 'sod', 'piske', 'kærne', 'kær']
            rema_dairy = next((d for d in dairy_types if d in rema_title_norm), None)
            p_dairy    = next((d for d in dairy_types if d in p['_norm_name']), None)
            if rema_dairy and p_dairy and rema_dairy != p_dairy:
                # Tillad at overskrive, hvis billedet er næsten identisk
                if dist is None or dist > 5:
                    continue

            title_tokens_ordered = [t for t in rema_title_norm.split() if len(t) >= 4]
            if title_tokens_ordered and title_tokens_ordered[0] not in p['_norm_name']:
                # Slæk kravet om første token, hvis billederne matcher godt.
                # dist <= 12 kræver samme reelle brand (BUKO "Rejeost" ↔ Buko
                # "Smøreost m. rejer" er ok) - uden brand-belæg kræves dist <= 8,
                # da svag billedlighed alene bar urelaterede navne over tærsklen
                # (PL-boost 0.30 + billede matchede fx lagkagebunde mod kylling).
                if dist is None or dist > 12:
                    continue
                if dist > 8 and fuzzy_score(norm_rema_brand, normalize_name(p.get('brand', ''))) < 0.75:
                    continue

        # Gate: vægt- og EAN-løs kandidat (typisk Dagrofa/Løvbjerg) - hverken
        # vægt-, stk- eller EAN-retro-gates kan validere matchet, så navnet må
        # bære det næsten alene: kræv markant højere navnescore. Lempes kun
        # ved nær-identisk produktfoto eller når stk-antal findes på begge
        # sider (så har stk-gaten allerede valideret pakkestørrelsen).
        # Frugt & grønt er undtaget: løsvarer er vægtløse i ALLE butikker, og
        # de korte navne ("BANANER" ↔ "Økologiske bananer") scorer lavt uden
        # at være tvivlsomme.
        if (name_score < 0.75 and not near_identical_photo
                and not p.get('_weight_g') and not p.get('ean')
                and (rema_stk_count is None or p.get('_stk_count') is None)
                and not (rema_type == CAT_FRUGT_GROENT and p['_type'] == CAT_FRUGT_GROENT)):
            continue

        # Minimum name gate: boosts alone must not trigger a match.
        # Samme brand-betingede billed-lempelse som ovenfor.
        if name_score < 0.50:
            if dist is None or dist > 12:
                continue
            if dist > 8 and fuzzy_score(norm_rema_brand, normalize_name(p.get('brand', ''))) < 0.75:
                continue
            if name_score < 0.30:
                # Men en meget lille tekst-score afvises stadig, trods godt billede
                continue

        # 2. Brand similarity boost (up to +0.30)
        brand_sim   = 1.0 if (base_is_pl and p_is_pl) else fuzzy_score(norm_rema_brand, p.get('brand', ''))
        brand_boost = 0.30 * brand_sim

        # 3. Image perceptual hash boost
        image_boost = 0.0
        if dist is not None:
            if dist <= 8:
                image_boost = 0.40 * (8 - dist) / 8.0
            elif dist <= 15:
                image_boost = 0.20 * (15 - dist) / 15.0

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
        target['/product/price'] = match.get('normal_price') or match['price']
        target['/product/sale_price'] = match['price']
    else:
        target['/product/price'] = match['price']
        target['/product/sale_price'] = None
    if match.get('image') and str(match['image']).lower() != 'nan':
        target['/product/imageLink'] = match['image']
    target['/product/brand'] = match.get('brand') or target.get('/product/brand')
    target['/product/unit_pricing_measure'] = match.get('weight') or target.get('/product/unit_pricing_measure')
    target['/product/price_per_kg'] = match.get('kg_price')
    target['/product/multi_deal'] = match.get('multi_deal', '')
    new_type = unify_category(match.get('Kategori', ''), match['name'])
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
            unique_str = f"{p.get('name','')}_{p.get('brand','')}_{p.get('weight','')}_{p.get('ean','')}"
            pid = f"{store_key}_{hashlib.md5(unique_str.encode('utf-8')).hexdigest()[:8]}"
            img = p['image'] if p.get('image') and str(p['image']).lower() != 'nan' else cfg['logo']
            
            if p.get('is_sale'):
                display_price = p.get('normal_price') or price
                sale_price = price
            else:
                display_price = price
                sale_price = None

            p_type = unify_category(p.get('Kategori'), p['name'])
            if p_type is None:
                continue  # ikke-mad kategori
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
    n_kept = normalize_name(str(kept.get('/product/title', '')))
    n_dup = normalize_name(str(dup.get('/product/title', '')))
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

                mapped_type = unify_category(product.get('product_type', ''), product.get('title', ''))
                if mapped_type is None:
                    continue  # ikke-mad (kategori eller navn) - frasorteres centralt i unify_category

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
    url = f"{os.getenv('SUPABASE_URL')}/rest/v1/app_cache"
    rpc_url = f"{os.getenv('SUPABASE_URL')}/rest/v1/rpc/swap_app_cache"
    key = os.getenv("DEPLOY_KEY") or os.getenv("SUPABASE_KEY") or ""
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


# Dagrofa-butikker henter priser fra ugentlig tilbudsavis - gemmes ikke i 30-dages historik.
# Kør scripts/supabase-price-history.sql i Supabase hvis upsert fejler (manglende unique index).
DAGROFA_STORE_KEYS = frozenset({'meny', 'spar', 'mk'})
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
            if store_key in DAGROFA_STORE_KEYS:
                continue
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
            if store_key and store_key not in DAGROFA_STORE_KEYS:
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
            rema_pcts = get_product_percents(f"{product['/product/title']} {product['/product/description']}")
            if (rema_w or rema_pcts) and matches:
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
                if bad_eans:
                    matches = {k: m for k, m in matches.items() if m.get('ean') not in bad_eans}

            # Kryds-medlems-validering: matches der modsiger HINANDEN på
            # vægt/procent (muligt når Rema-teksten selv udelader dem, så
            # gaten er ensidig pr. butik). Før cross-fill, så et droppet
            # EAN ikke spredes videre.
            matches = _drop_cross_conflicting_matches(matches, rema_w, rema_pcts)

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
                                and _percents_match(rema_pcts, hit.get('_pcts', frozenset()))):
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

            display_store = random.choice(cheapest_stores)
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
        for key in DB_STORE_KEYS:
            for p in unmatched[key]:
                ean = p.get('ean', '').strip()
                if ean and ean not in ('nan', 'None', ''):
                    ean_to_group.setdefault(ean, {})[key] = p

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
                p['_cross_match_tokens'] = set(t for t in p.get('_norm_name', '').split() if len(t) >= 3)

        for base_store_idx, base_key in enumerate(DB_STORE_KEYS):
            for base_p in unmatched[base_key][:]:
                if base_p not in unmatched[base_key]:
                    continue
                # Only stage 3 may initiate fuzzy - stages 1 and 2 never do
                if str(base_p.get('ean') or '').strip() not in ('', 'nan', 'None'):
                    continue

                base_title = str(base_p.get('name', ''))
                base_weight = base_p.get('_weight_g')
                base_stk = base_p.get('_stk_count')
                base_type = base_p['_type']
                base_variants = base_p['_variants']
                base_flavors = base_p['_flavors']
                base_forms = base_p['_forms']
                base_pcts = base_p['_pcts']
                base_title_norm = ' '.join(re.findall(r'\b[a-zæøå]+\b', base_title.lower()))
                base_tokens = set(t for t in base_title_norm.split() if len(t) >= 3)
                if not base_tokens:
                    continue
                base_is_pl = base_p['_is_pl']

                cluster = {base_key: base_p}

                for target_key in DB_STORE_KEYS[base_store_idx + 1:]:
                    target_list = unmatched[target_key]
                    if not target_list:
                        continue

                    best_match = None
                    best_score = 0.0

                    for target_p in target_list:
                        # Stage 2 (EAN, no cross-store match) is a passive target here.
                        # Fuzzy gates: weight (unit), quantity (stk), name score, type.
                        if not weights_compatible(base_weight, target_p.get('_weight_g')):
                            continue
                        if base_stk is not None and target_p.get('_stk_count') is not None and base_stk != target_p.get('_stk_count'):
                            continue
                        if base_variants != target_p['_variants']:
                            continue
                        # Procent-gate (fedt-/alkohol-%): kun aktiv når begge
                        # sider angiver procenter, jf. _percents_match
                        if not _percents_match(base_pcts, target_p['_pcts']):
                            continue
                        # Symmetrisk smags-gate: begge sider er korte butiksnavne
                        # (ingen rig beskrivelse som hos Rema), så en smag nævnt
                        # af kun én side er en reel forskel ("Cherry blommetomater"
                        # ≠ "Blommetomater") uanset hvem der initierer.
                        if base_flavors != target_p['_flavors']:
                            continue
                        if not _forms_match(base_forms, target_p['_forms']):
                            continue

                        # Kluster-konsistens: kandidaten skal også være
                        # forenelig med allerede accepterede medlemmer, ikke
                        # kun basen - en base uden vægt/procent kan ellers
                        # samle indbyrdes modstridende varianter (samme
                        # ensidigheds-hul som _drop_cross_conflicting_matches
                        # lukker i Rema-annoteringen).
                        if len(cluster) > 1 and not _group_compatible(
                                target_p.get('_weight_g'), target_p.get('_stk_count'),
                                target_p['_pcts'], cluster.values()):
                            continue

                        target_name_norm = target_p.get('_norm_name', '')
                        if abs(len(base_title_norm) - len(target_name_norm)) > 20:
                            continue

                        target_tokens = target_p.get('_cross_match_tokens', set())
                        if not base_tokens.intersection(target_tokens):
                            continue

                        name_score = fuzzy_score(base_title_norm, target_name_norm)

                        # Type-gate med eskalering: butikskategorier er støjede,
                        # så mismatch kræver blot næsten-identisk navn (jf.
                        # _find_generic_match).
                        if not types_compatible(base_type, target_p['_type']) and name_score < 0.80:
                            continue

                        target_is_pl = is_private_label(target_p.get('brand',''), target_p.get('name',''))
                        if base_is_pl != target_is_pl and name_score < 0.70:
                            continue

                        if name_score < 0.65:
                            continue

                        # Vægtløst par (typisk Dagrofa): mangler bare én side
                        # vægt, kan vægt-gaten intet validere, og navnet bærer
                        # matchet alene - kræv markant højere navnescore,
                        # medmindre stk-antal findes på begge sider (så har
                        # stk-gaten valideret pakkestørrelsen). Frugt & grønt
                        # er undtaget: løsvarer er vægtløse overalt, og korte
                        # navne scorer lavt uden at være tvivlsomme.
                        if (name_score < 0.75
                                and (not base_weight or not target_p.get('_weight_g'))
                                and (base_stk is None or target_p.get('_stk_count') is None)
                                and not (base_type == CAT_FRUGT_GROENT and target_p['_type'] == CAT_FRUGT_GROENT)):
                            continue

                        # Pris-sanity: samme vare koster ikke 5× mere i en anden butik
                        try:
                            if float(target_p['price']) > 5.0 * float(base_p['price']) or \
                               float(target_p['price']) * 5.0 < float(base_p['price']):
                                continue
                        except (TypeError, ValueError, KeyError):
                            pass

                        if name_score > best_score:
                            best_score = name_score
                            best_match = target_p

                    if best_match:
                        cluster[target_key] = best_match

                if len(cluster) > 1:
                    for k, p in cluster.items():
                        unmatched[k].remove(p)

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
        for base_key in DB_STORE_KEYS:
            for base_p in unmatched[base_key][:]:
                if base_p not in unmatched[base_key]:
                    continue
                if str(base_p.get('ean') or '').strip() not in ('', 'nan', 'None'):
                    continue  # only stage 3 initiates fuzzy

                base_title = str(base_p.get('name', ''))
                base_weight = base_p.get('_weight_g')
                base_stk = base_p.get('_stk_count')
                base_type = base_p['_type']
                base_variants = base_p['_variants']
                base_flavors = base_p['_flavors']
                base_forms = base_p['_forms']
                base_pcts = base_p['_pcts']
                base_title_norm = ' '.join(re.findall(r'\b[a-zæøå]+\b', base_title.lower()))
                base_tokens = set(t for t in base_title_norm.split() if len(t) >= 3)
                if not base_tokens:
                    continue
                base_is_pl = base_p['_is_pl']

                best_display_item = None
                best_score = 0.0

                for target_key in DB_STORE_KEYS:
                    if target_key == base_key:
                        continue
                    for target_p, display_item in stage1_components[target_key]:
                        if base_key in display_item['/product/store_matches']:
                            continue  # base_key allerede repræsenteret i denne gruppe
                        if not weights_compatible(base_weight, target_p.get('_weight_g')):
                            continue
                        if base_stk is not None and target_p.get('_stk_count') is not None and base_stk != target_p.get('_stk_count'):
                            continue
                        if base_variants != target_p['_variants']:
                            continue
                        # Procent-gate (jf. fase 2)
                        if not _percents_match(base_pcts, target_p['_pcts']):
                            continue
                        # Symmetrisk smags-gate - samme begrundelse som i fase 2
                        if base_flavors != target_p['_flavors']:
                            continue
                        if not _forms_match(base_forms, target_p['_forms']):
                            continue

                        target_name_norm = target_p.get('_norm_name', '')
                        target_tokens = set(t for t in target_name_norm.split() if len(t) >= 3)
                        if not base_tokens.intersection(target_tokens):
                            continue

                        name_score = fuzzy_score(base_title_norm, target_name_norm)
                        # Type-gate med eskalering (jf. fase 2)
                        if not types_compatible(base_type, target_p['_type']) and name_score < 0.80:
                            continue
                        target_is_pl = is_private_label(target_p.get('brand', ''), target_p.get('name', ''))
                        if base_is_pl != target_is_pl and name_score < 0.70:
                            continue
                        if name_score < 0.65:
                            continue

                        # Vægtløst par: kræv højere navnescore (jf. fase 2)
                        if (name_score < 0.75
                                and (not base_weight or not target_p.get('_weight_g'))
                                and (base_stk is None or target_p.get('_stk_count') is None)
                                and not (base_type == CAT_FRUGT_GROENT and target_p['_type'] == CAT_FRUGT_GROENT)):
                            continue

                        # Pris-sanity (jf. fase 2)
                        try:
                            if float(target_p['price']) > 5.0 * float(base_p['price']) or \
                               float(target_p['price']) * 5.0 < float(base_p['price']):
                                continue
                        except (TypeError, ValueError, KeyError):
                            pass

                        # Gruppe-validering: gates ovenfor tjekker kun target_p
                        # (repræsentanten) - et vægtløst medlem må ikke være
                        # bagdør ind i en gruppe, hvis øvrige medlemmer
                        # modsiger basen på vægt/stk/procent.
                        if not _group_compatible(base_weight, base_stk, base_pcts,
                                                 display_item['/product/store_matches'].values()):
                            continue

                        if name_score > best_score:
                            best_score = name_score
                            best_display_item = display_item

                if best_display_item is not None:
                    unmatched[base_key].remove(base_p)
                    best_display_item['/product/store_matches'][base_key] = base_p
                    if is_price_cheaper(base_p['price'], best_display_item['/product/price']):
                        best_display_item['/product/cheapest_at'] = base_key
                        best_display_item['/product/cheaper_at'] = base_key
                        _apply_cheapest_display(best_display_item, base_key, base_p)

        # ===================================================================
        # Solokort - stage 2 (EAN, unmatched) + unmatched stage 3 (no EAN)
        # ===================================================================
        for key in DB_STORE_KEYS:
            for p in unmatched[key]:
                final_products.extend(build_store_display_products([p], key))

        # Fjern interne precompute-felter fra store_matches, så de ikke fylder
        # i app_cache/D1 (sets kan desuden ikke serialiseres pænt til JSON).
        _transient_keys = ('_type', '_flavors', '_forms', '_variants', '_is_pl', '_pcts', '_cross_match_tokens')
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
    search_index = {k: list(v) for k, v in build_search_index(products, normalize_name).items()}
    if _save_app_cache(products, search_index):
        record_prices_batch(collect_store_prices(products))
        _notify_website_refresh()


def run_updater():
    logger.info("Starter opdatering af produkt-cache...")
    fresh = fetch_and_parse_xml()
    if not fresh:
        return
    # Convert sets to lists for JSON serialization
    
    # Convert sets to lists in fresh
    for p in fresh:
        if 'matched_variants' in p and isinstance(p['matched_variants'], set):
            p['matched_variants'] = list(p['matched_variants'])

    annotate_lowest_prices(fresh)
    search_index = {k: list(v) for k, v in build_search_index(fresh, normalize_name).items()}
    if _save_app_cache(fresh, search_index):
        record_prices_batch(collect_store_prices(fresh))
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
