import requests
import re
import xmltodict
import os
import json
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
    _BLOCKED_NAME_FRAGMENTS, _PLACEHOLDER_IMGS,
    CAT_ANDET, unify_category,
)


def _get_supabase_client():
    url = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    key = os.getenv('SUPABASE_KEY') or os.getenv('NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY')
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None

supabase = _get_supabase_client()

configure_logging()


XML_URL = "https://cphapp.rema1000.dk/api/v1/products.xml"

# Rema is the XML data source — not "primary", just the feed format we parse
REMA_KEY       = 'rema'
DB_STORE_KEYS = [k for k, v in _STORE_CONFIGS.items() if v.get('db_key')]

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
                    
                    p_hash_hex = str(row.get('billede_hash') or '')
                    try:
                        p_hash_int = int(p_hash_hex, 16) if p_hash_hex and p_hash_hex not in ('nan', 'None', '') else None
                    except Exception:
                        p_hash_int = None
                        
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
                        
                    products.append({
                        'name':        str(row.get('navn') or ''),
                        'brand':       str(row.get('producent') or ''),
                        'weight':      weight_str,
                        'kg_price':    ppk,
                        'price':       price,
                        'normal_price': normal_price,
                        'is_sale':     is_sale,
                        'multi_deal':  multi_deal,
                        '_norm_name':  normalize_name(str(row.get('navn') or '')),
                        '_weight_g':   weight_g,
                        '_stk_count':  parse_stk_count(weight_str),
                        'image':       str(row.get('billede_url') or ''),
                        '_image_hash': p_hash_hex,
                        '_hash_int':   p_hash_int,
                        'ean':         ean,
                        'Kategori':    str(row.get('kategori') or ''),
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

# Pre-kompilerede regex til normalize_name — bygges én gang ved opstart
def is_organic(name: str, desc: str = '', brand: str = '') -> bool:
    """Return True if the product is explicitly marked as organic."""
    text = f"{name} {desc} {brand}".lower()
    return 'økolog' in text or 'øko ' in text or ' øko' in text or text.startswith('øko') or text.endswith('øko') or 'organic' in text


def is_lactose_free(name: str, desc: str = '', brand: str = '') -> bool:
    """Return True if the product is explicitly marked as lactose-free."""
    text = f"{name} {desc} {brand}".lower()
    return 'laktosefri' in text or 'lactose free' in text or 'laktose fri' in text


def is_sugar_free(name: str, desc: str = '', brand: str = '') -> bool:
    """Return True if the product is explicitly marked as sugar-free."""
    text = f"{name} {desc} {brand}".lower()
    return ('sukkerfri' in text or 'sugar free' in text or 'sukker fri' in text
            or 'zero sugar' in text or ' zero' in text or text.endswith('zero')
            or 'no sugar' in text or 'uden sukker' in text)


def is_gluten_free(name: str, desc: str = '', brand: str = '') -> bool:
    """Return True if the product is explicitly marked as gluten-free."""
    text = f"{name} {desc} {brand}".lower()
    return ('glutenfri' in text or 'gluten free' in text or 'gluten fri' in text
            or 'uden gluten' in text or 'gluten-fri' in text)


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




_PRIVATE_LABEL_BRANDS: frozenset = frozenset({
    # Rema 1000 – basisbrand + øvrige egne mærker
    'rema 1000', 'rema',
    'gram slot', 'kolonihagen', 'solgryn', 'cleverdeli',
    'vigo', 'maximat', 'lev vel', 'ängens',
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
})

_PRIVATE_LABEL_PREFIXES: tuple = (
    'rema ', 'rema 1000 ', 'gram slot ', 'kolonihagen ', 'cleverdeli ',
    'salling ', 'slagteren ', 'budget ',
    'coop ', 'xtra ', 'x-tra ', 'änglamark ', 'irma ',
    'first price ', 'fp ', 'grøn balance ', 'gestus ', 'levevis ',
    'vores ', 'karma ', 'cirkel ',
    'omhu ', 'spicefield ', 'banderos ', 'praktisk ',
)

# Single-word brands that are first words of multi-word private label names.
# extract_producer() in the scrapers only takes the first word of the product name,
# so "First Price Havregryn" → brand="First" — we need this extra check.
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
    'cola': 'cola',
    'vindrue': 'grape', 'grape': 'grape',
    'hindbær': 'raspberry', 'raspberry': 'raspberry',
    'jordbær': 'strawberry', 'strawberry': 'strawberry',
    'hyldeblomst': 'elderflower', 'elderflower': 'elderflower',
    'mango': 'mango',
    'ananas': 'pineapple', 'pineapple': 'pineapple',
    'appelsin': 'orange', 'orange': 'orange',
    'citron': 'lemon', 'lemon': 'lemon',
    'sour': 'sour'
}

def get_lolly_flavors(text: str) -> set:
    text_lower = text.lower()
    flavors = set()
    for kw, canonical in _FLAVOR_MAP.items():
        if kw in text_lower:
            flavors.add(canonical)
    return flavors


def _find_generic_match(rema_title, rema_description, products, token_idx, hash_list, rema_brand='', rema_weight_g=None, threshold=0.60, rema_image_hash='', rema_price=0.0, rema_ean='', rema_stk_count=None, ean_index=None):
    """Token-indexed fuzzy match used by all store comparisons.

    Scoring components (all additive):
    1. Name fuzzy score          — basis 0..1 via SequenceMatcher
    2. Brand similarity boost    — up to +0.30 when brands match (e.g. Arla↔Arla)
    3. Image perceptual hash     — up to +0.40 when pHash distance is low

    Gates (hard reject before scoring):
    A. Brand-pairing: private-label ↔ private-label only.
    B. Weight: candidates whose weight differs > _WEIGHT_TOLERANCE_G are skipped.
    C. Price sanity: reject if store price > 5× the Rema price.
    D. Token-overlap: first 4-char title token must appear in candidate name (relaxed if images match).
    """
    # 1. EAN Match: Rema har ingen EAN — dette bruges kun hvis kilden skiftes.
    # Sammenligningsbutikkerne har EAN, men de slås op via cross-fill i fetch_and_parse_xml.
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

    rema_norms = [n for n in [normalize_name(rema_title), normalize_name(rema_description)] if n]
    if not rema_norms:
        return None

    rema_title_norm = normalize_name(rema_title)
    norm_rema_brand = normalize_name(rema_brand)
    base_is_pl = is_private_label(rema_brand, rema_title)

    # Collect candidate indices via token index (title tokens only)
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

    r_hash_int = None
    if rema_image_hash and rema_image_hash not in ('None', 'nan', ''):
        try:
            r_hash_int = int(rema_image_hash, 16)
        except Exception:
            pass

    # BEMÆRK: Vi har fjernet det tunge O(N^2) image hash loop (som lavede 48 mio. tjek) 
    # for at forhindre Render timeout. Token-overlap er mere end rigeligt nu hvor 'hk.' osv. oversættes.


    if not candidate_indices:
        return None

    best, best_score = None, 0.0
    rema_is_org = is_organic(rema_title, rema_description, rema_brand)
    rema_is_lf  = is_lactose_free(rema_title, rema_description, rema_brand)
    rema_is_sf  = is_sugar_free(rema_title, rema_description, rema_brand)
    rema_is_gf  = is_gluten_free(rema_title, rema_description, rema_brand)

    for i in candidate_indices:
        p = products[i]

        dist = None
        if r_hash_int is not None:
            p_hash_int = p.get('_hash_int')
            if p_hash_int is not None:
                dist = (r_hash_int ^ p_hash_int).bit_count()

        # Gate: Organic matching
        if rema_is_org != is_organic(p.get('name', ''), p.get('description', ''), p.get('brand', '')):
            continue

        # Gate: Lactose-free matching
        if rema_is_lf != is_lactose_free(p.get('name', ''), p.get('description', ''), p.get('brand', '')):
            continue

        # Gate: Sugar-free matching
        if rema_is_sf != is_sugar_free(p.get('name', ''), p.get('description', ''), p.get('brand', '')):
            continue

        # Gate: Gluten-free matching
        if rema_is_gf != is_gluten_free(p.get('name', ''), p.get('description', ''), p.get('brand', '')):
            continue

        # Gate: Lolly flavor matching to avoid matching different flavors or generic collage cards
        if 'lolly' in rema_title.lower() or 'lolly' in rema_description.lower() or 'lolly' in p.get('name', '').lower():
            rema_flavors = get_lolly_flavors(rema_title + " " + rema_description)
            p_flavors = get_lolly_flavors(p.get('name', '') + " " + p.get('description', ''))
            if rema_flavors != p_flavors:
                continue

        # 1. Name similarity
        name_score = fuzzy_score(rema_title_norm, p['_norm_name']) if rema_title_norm else 0.0

        # Gate A: Brand-pairing
        p_is_pl = is_private_label(p.get('brand', ''), p.get('name', ''))
        if base_is_pl != p_is_pl and name_score < 0.70:
            continue

        # Gate B: Weight
        if not weights_compatible(rema_weight_g, p.get('_weight_g')):
            continue

        # Gate B2: Stk-count — skip if both have a known stk count that differs
        if rema_stk_count is not None and p.get('_stk_count') is not None and rema_stk_count != p.get('_stk_count'):
            continue

        # Gate C: Price sanity
        if rema_price and rema_price > 0:
            try:
                if float(p.get('price', 0)) > 5.0 * float(rema_price):
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
                # Slæk kravet om første token, hvis billederne matcher godt
                if dist is None or dist > 12:
                    continue

        # Minimum name gate: boosts alone must not trigger a match
        if name_score < 0.50:
            if dist is None or dist > 12:
                continue
            elif name_score < 0.20:
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
                '/product/weight_grams':              p.get('_weight_g'),
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

def _fetch_rema_products_only():
    """Hent og parse Rema 1000 XML — uden sammenligning med andre butikker."""
    rema_products = []
    logger.info("Fetching XML data from: %s", XML_URL)
    try:
        rema_hashes = {}
        hash_path = os.path.join(os.path.dirname(__file__), 'data', 'rema_hashes.json')
        if os.path.exists(hash_path):
            try:
                with open(hash_path, 'r', encoding='utf-8') as f:
                    rema_hashes = json.load(f)
            except Exception as e:
                logger.error(f"Fejl ved indlæsning af rema_hashes.json: {e}")

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
                logger.info(f"  Timeout på forsøg {attempt + 1}/3 — prøver igen...")
            except requests.exceptions.RequestException as e:
                logger.info(f"  Netværksfejl på forsøg {attempt + 1}/3: {e}")
        if xml_text is None:
            raise RuntimeError("Kunne ikke hente Rema XML efter 3 forsøg")

        xml_dict = xmltodict.parse(xml_text)
        if not validate_xml_structure(xml_dict):
            logger.info("XML validation failed")
            return []

        for i, product in enumerate(xml_dict['products']['product']):
            try:
                price = format_price(product.get('price', '0 DKK'))
                sale_price = format_price(product.get('sale_price', '')) or None
                if price <= 0:
                    continue

                mapped_type = unify_category(product.get('product_type', ''), product.get('title', ''))
                if mapped_type is None:
                    continue

                title_lower = product.get('title', '').lower()
                if any(frag in title_lower for frag in _BLOCKED_NAME_FRAGMENTS):
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
                    '/product/stk_count': parse_stk_count(unit_measure),
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
    """Opdater kun Rema-priser i eksisterende cache — andre butikker bevares."""
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
        url = f"{os.getenv('SUPABASE_URL')}/rest/v1/app_cache?select=*&id=gte.0&order=id.asc"
        headers = {
            "apikey": os.getenv("SUPABASE_KEY"),
            "Authorization": f"Bearer {os.getenv('SUPABASE_KEY')}",
        }
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


def _save_app_cache(products, search_index):
    """Upload produkt-cache til Supabase og gem altid lokalt som fallback."""
    _save_local_cache(products, search_index)

    if not db_available():
        return False
    import httpx
    url = f"{os.getenv('SUPABASE_URL')}/rest/v1/app_cache"
    key = os.getenv("DEPLOY_KEY") or os.getenv("SUPABASE_KEY") or ""
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal,resolution=merge-duplicates",
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            try:
                client.delete(
                    url + "?id=gte.0",
                    headers={"apikey": key, "Authorization": f"Bearer {key}"},
                )
            except Exception:
                pass

            idx_payload = {"id": 0, "data": [], "search_index": search_index}
            res_idx = client.post(url, headers=headers, content=json.dumps(idx_payload, default=lambda o: list(o) if isinstance(o, (set, frozenset)) else str(o)))
            res_idx.raise_for_status()

            chunk_size = 1000
            for chunk_id, i in enumerate(range(0, len(products), chunk_size), start=1):
                chunk = products[i:i + chunk_size]
                chunk_payload = {"id": chunk_id, "data": chunk, "search_index": {}}
                res_chunk = client.post(url, headers=headers, content=json.dumps(chunk_payload, default=lambda o: list(o) if isinstance(o, (set, frozenset)) else str(o)))
                res_chunk.raise_for_status()
                logger.info(f"Uploadet data chunk {chunk_id} med {len(chunk)} produkter")
        return True
    except Exception as e:
        logger.warning(f"Kunne ikke uploade til Supabase app_cache (lokal fallback bruges): {e}")
        return False


def fetch_and_parse_xml():
    """Fetch and parse data from both XML and Excel sources"""
    try:
        logger.info("\n=== Starting data fetch and parse ===")

        rema_products = _fetch_rema_products_only()
        if not rema_products:
            return []
        
        # Annotate each Rema product with comparison data from all secondary stores
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
                )
                if m:
                    matches[key] = m

            # EAN cross-fill: if any match has EAN, try to find it in stores that missed
            found_ean = next(
                (m['ean'] for m in matches.values() if m.get('ean')),
                None
            )
            if found_ean:
                for key in DB_STORE_KEYS:
                    if key not in matches:
                        _, _, _, ean_index = store_data[key]
                        hit = ean_index.get(found_ean)
                        if hit:
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
        # FASE 1: EAN-baseret matching (altid prioriteret over fuzzy)
        # Gruppér alle umatchede produkter på tværs af butikker via EAN.
        # Kun grupper med ≥2 butikker slettes fra unmatched og bygges nu.
        # Ét-butiks EAN-produkter forbliver i unmatched og behandles i fase 3.
        # ===================================================================
        # stage1_components: {store_key: [(product, display_item), ...]}
        # Bruges i fase 2b så stage-3-produkter (ingen EAN) kan matche fase-1-grupper.
        stage1_components: dict[str, list] = {key: [] for key in DB_STORE_KEYS}
        ean_to_group: dict[str, dict] = {}
        for key in DB_STORE_KEYS:
            for p in unmatched[key]:
                ean = p.get('ean', '').strip()
                if ean and ean not in ('nan', 'None', ''):
                    ean_to_group.setdefault(ean, {})[key] = p

        for ean, group in ean_to_group.items():
            if len(group) < 2:
                continue  # ét-butiks EAN → solokort i fase 3
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
            # Registrér fase-1-produkter så stage-3 kan fuzzy-matche mod dem i fase 2b
            for key, p in group.items():
                stage1_components[key].append((p, display_item))

        # ===================================================================
        # FASE 2: Fuzzy-matching for resterende umatchede produkter
        # (Produkter matchet via EAN i fase 1 er allerede fjernet fra unmatched)
        # ===================================================================
        logger.info("Cross-matching unmatched products across stores...")
        for key in DB_STORE_KEYS:
            for p in unmatched[key]:
                p['_cross_match_tokens'] = set(t for t in p.get('_norm_name', '').split() if len(t) >= 3)

        for base_store_idx, base_key in enumerate(DB_STORE_KEYS):
            for base_p in unmatched[base_key][:]:
                if base_p not in unmatched[base_key]:
                    continue
                # Varer med EAN fuzzy-matches ikke — EAN er autoritativ identifikator
                if str(base_p.get('ean') or '').strip() not in ('', 'nan', 'None'):
                    continue

                base_title = str(base_p.get('name', ''))
                base_desc = str(base_p.get('description', ''))
                base_brand = str(base_p.get('brand', ''))
                base_weight = base_p.get('_weight_g')
                base_stk = base_p.get('_stk_count')
                base_title_norm = ' '.join(re.findall(r'\b[a-zæøå]+\b', base_title.lower()))
                base_tokens = set(t for t in base_title_norm.split() if len(t) >= 3)
                if not base_tokens:
                    continue
                base_is_pl = is_private_label(base_brand, base_title)
                base_is_org = is_organic(base_title, base_desc, base_brand)

                cluster = {base_key: base_p}

                for target_key in DB_STORE_KEYS[base_store_idx + 1:]:
                    target_list = unmatched[target_key]
                    if not target_list:
                        continue

                    best_match = None
                    best_score = 0.0

                    for target_p in target_list:
                        # Stage 2-produkter (har EAN, ingen cross-store match) er passive targets — de initierer ikke,
                        # men stage 3 (ingen EAN) må godt matche mod dem her.
                        if not weights_compatible(base_weight, target_p.get('_weight_g')):
                            continue
                        if base_stk is not None and target_p.get('_stk_count') is not None and base_stk != target_p.get('_stk_count'):
                            continue
                        if base_is_org != is_organic(target_p.get('name',''), target_p.get('description',''), target_p.get('brand','')):
                            continue

                        if 'lolly' in base_title.lower() or 'lolly' in target_p.get('name', '').lower():
                            base_flavors = get_lolly_flavors(base_title + " " + base_desc)
                            target_flavors = get_lolly_flavors(target_p.get('name', '') + " " + target_p.get('description', ''))
                            if base_flavors != target_flavors:
                                continue

                        target_name_norm = target_p.get('_norm_name', '')
                        if abs(len(base_title_norm) - len(target_name_norm)) > 20:
                            continue

                        target_tokens = target_p.get('_cross_match_tokens', set())
                        if not base_tokens.intersection(target_tokens):
                            continue

                        name_score = fuzzy_score(base_title_norm, target_name_norm)

                        target_is_pl = is_private_label(target_p.get('brand',''), target_p.get('name',''))
                        if base_is_pl != target_is_pl and name_score < 0.70:
                            continue

                        if name_score < 0.65:
                            continue

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
        # FASE 2b: Stage 3-produkter (ingen EAN) matcher mod fase 1-grupper
        # Stage 1-produkter er passive targets — de kan ikke selv initiere,
        # men en no-EAN vare kan blive tilknyttet en eksisterende EAN-gruppe.
        # ===================================================================
        for base_key in DB_STORE_KEYS:
            for base_p in unmatched[base_key][:]:
                if base_p not in unmatched[base_key]:
                    continue
                if str(base_p.get('ean') or '').strip() not in ('', 'nan', 'None'):
                    continue  # kun stage 3 initierer

                base_title = str(base_p.get('name', ''))
                base_desc = str(base_p.get('description', ''))
                base_brand = str(base_p.get('brand', ''))
                base_weight = base_p.get('_weight_g')
                base_stk = base_p.get('_stk_count')
                base_title_norm = ' '.join(re.findall(r'\b[a-zæøå]+\b', base_title.lower()))
                base_tokens = set(t for t in base_title_norm.split() if len(t) >= 3)
                if not base_tokens:
                    continue
                base_is_pl = is_private_label(base_brand, base_title)
                base_is_org = is_organic(base_title, base_desc, base_brand)

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
                        if base_is_org != is_organic(target_p.get('name', ''), target_p.get('description', ''), target_p.get('brand', '')):
                            continue

                        target_name_norm = target_p.get('_norm_name', '')
                        target_tokens = set(t for t in target_name_norm.split() if len(t) >= 3)
                        if not base_tokens.intersection(target_tokens):
                            continue

                        name_score = fuzzy_score(base_title_norm, target_name_norm)
                        target_is_pl = is_private_label(target_p.get('brand', ''), target_p.get('name', ''))
                        if base_is_pl != target_is_pl and name_score < 0.70:
                            continue
                        if name_score < 0.65:
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
        # FASE 3: Solokort for resterende umatchede produkter (EAN eller ej)
        # ===================================================================
        for key in DB_STORE_KEYS:
            for p in unmatched[key]:
                final_products.extend(build_store_display_products([p], key))

        counts_str = ', '.join(f"{match_counts[k]} matched to {_STORE_CONFIGS[k]['label']}" for k in DB_STORE_KEYS)
        logger.info(
            f"\nFinal product list: {len(final_products)} products "
            f"({len(rema_products)} Rema + {len(final_products) - len(rema_products)} unmatched comparison cards), "
            f"{counts_str}"
        )
        # Deduplicer final_products på billedeURL — samme billede = samme produkt
        # Placeholder/logo-billeder tæller ikke som unikke og dedupliceres ikke
        seen_imgs: set = set()
        deduped: list = []
        for _p in final_products:
            _img = str(_p.get('/product/imageLink', '')).strip()
            if not _img or _img in ('nan', 'None') or _img in _PLACEHOLDER_IMGS:
                deduped.append(_p)  # ingen unik billedeURL → inkluder altid
            elif _img not in seen_imgs:
                seen_imgs.add(_img)
                deduped.append(_p)
            # else: duplikat-billede → spring over
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
            "APP_URL/CACHE_REFRESH_SECRET ikke sat — genstart hjemmesiden eller sæt secrets for øjeblikkelig opdatering"
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
        logger.info("Ingen eksisterende cache — uploader kun Rema-produkter")
        products = []
        for p in fresh_rema:
            item = dict(p)
            item['/product/store_matches'] = {}
            item['/product/rema_price'] = _rema_effective_price(p)
            item['/product/rema_is_sale'] = p.get('/product/sale_price') is not None
            item['/product/cheapest_at'] = REMA_KEY
            products.append(item)

    search_index = {k: list(v) for k, v in build_search_index(products, normalize_name).items()}
    if _save_app_cache(products, search_index):
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

    search_index = {k: list(v) for k, v in build_search_index(fresh, normalize_name).items()}
    if _save_app_cache(fresh, search_index):
        _notify_website_refresh()
    elif not db_available():
        logger.info("Supabase ikke tilgængelig — lokal cache gemt som fallback")

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
