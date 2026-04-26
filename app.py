from flask import Flask, render_template, send_from_directory, jsonify, request, redirect, url_for, render_template_string
import requests
import re
import xmltodict
from datetime import datetime, timedelta
import os
import json
import pandas as pd
import math  # Added for math.isnan
from difflib import SequenceMatcher
import unicodedata

app = Flask(__name__)

# HTTP headers to improve compatibility with sites that gate content by user-agent
DEFAULT_HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'da,da-DK;q=0.9,en;q=0.8',
}

# Cache configuration
CACHE_DURATION = timedelta(hours=6)
XML_URL = "https://cphapp.rema1000.dk/api/v1/products.xml"
cached_data = {
    'timestamp': None,
    'data': None
}

# Add at the top with other app config
app.cached_products = None
app.last_cache_update = None

def format_price(price_str):
    """Format price string to float"""
    if not price_str:
        return 0.0
    try:
        # Remove currency and whitespace
        cleaned = price_str.replace('DKK', '').replace('kr', '').replace(',', '.').strip()
        return float(cleaned)
    except (ValueError, TypeError):
        print(f"Error converting price: {price_str}")
        return 0.0

# ---------------------------------------------------------------------------
# Bilka fuzzy-matching helpers
# ---------------------------------------------------------------------------

bilka_comparison_cache = None
bilka_token_index = None

mk_comparison_cache = None
mk_token_index = None

def normalize_name(name):
    """Lowercase, strip diacritics and noise for fuzzy comparison."""
    if not name or str(name) == 'nan':
        return ''
    name = str(name).lower().strip()
    name = unicodedata.normalize('NFKD', name)
    name = ''.join(c for c in name if not unicodedata.combining(c))
    for noise in ['%', ' eko', ' bio', ' a/s', ' i/s', ' øko']:
        name = name.replace(noise, '')
    return ' '.join(name.split())


def fuzzy_score(a, b):
    return SequenceMatcher(None, a, b).ratio()


def brand_similarity(brand_a: str, brand_b: str) -> float:
    """Return [0, 1] similarity between two brand strings.
    Uses normalised fuzzy ratio so 'Arla' ↔ 'Arla ØKO' still scores high.
    Returns 0 when either brand is empty/unknown."""
    a = normalize_name(brand_a)
    b = normalize_name(brand_b)
    if not a or not b:
        return 0.0
    # Exact substring: e.g. 'arla' in 'arla lact ofree'
    if a in b or b in a:
        return 1.0
    return fuzzy_score(a, b)


# Weight tolerance used when deciding if two products are comparable
_WEIGHT_TOLERANCE_G = 50  # grams / ml


def parse_weight_to_grams(weight_str) -> float | None:
    """Parse a weight/volume string to a common unit (grams or ml, treated equally).

    Supported formats: '1 l', '0.5 kg', '650 g', '20 cl', '200 ml',
    '1.5L', '500G', '0.33 l', etc.
    Returns None when the string cannot be parsed or contains no useful data.
    """
    if not weight_str or str(weight_str).strip().lower() in ('nan', '', 'none'):
        return None
    s = str(weight_str).strip().lower().replace(',', '.')
    # Extract leading number and trailing unit
    m = re.match(r'^([\d.]+)\s*([a-zæøå]+)$', s)
    if not m:
        # Try number-only (assume grams)
        m2 = re.match(r'^([\d.]+)$', s)
        if m2:
            try:
                return float(m2.group(1))
            except ValueError:
                return None
        return None
    try:
        value = float(m.group(1))
        unit = m.group(2)
    except ValueError:
        return None
    if unit in ('g', 'gr', 'gram'):
        return value
    if unit in ('kg',):
        return value * 1000
    if unit in ('l', 'ltr', 'liter', 'litre'):
        return value * 1000
    if unit in ('ml',):
        return value
    if unit in ('cl',):
        return value * 10
    if unit in ('dl',):
        return value * 100
    return None

def is_organic(name: str, desc: str = '', brand: str = '') -> bool:
    """Return True if the product is explicitly marked as organic."""
    text = f"{name} {desc} {brand}".lower()
    return 'økolog' in text or 'øko ' in text or ' øko' in text or text.startswith('øko') or text.endswith('øko') or 'organic' in text


def weights_compatible(w_a: float | None, w_b: float | None, tolerance: float = _WEIGHT_TOLERANCE_G) -> bool:
    """Return True when both weights are known and within *tolerance* of each other,
    OR when either weight is unknown (we cannot rule out a match)."""
    if w_a is None or w_b is None:
        return True  # unknown weight → do not discard the candidate
    return abs(w_a - w_b) <= tolerance


def load_bilka_comparison_data():
    """Load Bilka products and build a token inverted index for fast matching."""
    global bilka_comparison_cache, bilka_token_index
    if bilka_comparison_cache is not None:
        return bilka_comparison_cache, bilka_token_index
    try:
        import os
        filepath = os.path.join(os.path.dirname(__file__), 'Bilka_produkter.xlsx')
        df = pd.read_excel(filepath)
        # Expected columns: Navn, Type, Vægt, Kg-pris, Pris
        products = []
        for _, row in df.iterrows():
            try:
                raw = row['Pris']
                price = float(str(raw).replace(',', '.').replace('kr', '').strip()) if isinstance(raw, str) else float(raw)
                if math.isnan(price) or price <= 0:
                    continue
                weight_str = str(row['Vægt'])
                ppk = parse_kg_price(row.get('Kg-pris', ''))
                is_sale_raw = str(row.get('Tilbud', 'Nej')).lower()
                is_sale = True if is_sale_raw in ('ja', 'true', 'yes', '1') else False
                
                products.append({
                    'name':     str(row['Navn']),
                    'brand':    str(row['Type']),
                    'weight':   weight_str,
                    'kg_price': ppk,
                    'price':    price,
                    'is_sale':  is_sale,
                    '_norm_name': normalize_name(str(row['Navn'])),
                    '_weight_g':  parse_weight_to_grams(weight_str),
                    'image':      str(row.get('Billede URL', '')),
                    '_image_hash': str(row.get('Billede Hash', '')),
                    'ean':        str(row.get('EAN', '')).split('.')[0].strip() if str(row.get('EAN', '')).strip() not in ('nan', 'None', '') else '',
                })
            except Exception as e:
                print(f"Skipping bilka comparison row: {e}")
                continue

        # Build inverted index: token (≥4 chars) → set of product indices
        token_idx = {}
        for i, bp in enumerate(products):
            for token in bp['_norm_name'].split():
                if len(token) >= 4:
                    token_idx.setdefault(token, set()).add(i)

        bilka_comparison_cache = products
        bilka_token_index = token_idx
        print(f"Loaded {len(products)} Bilka products, {len(token_idx)} index tokens")
        return bilka_comparison_cache, bilka_token_index
    except Exception as e:
        print(f"Error loading Bilka_produkter.xlsx: {e}")
        bilka_comparison_cache, bilka_token_index = [], {}
        return [], {}


def load_mk_comparison_data():
    """Load Min Købmand products and build a token inverted index for fast matching."""
    global mk_comparison_cache, mk_token_index
    if mk_comparison_cache is not None:
        return mk_comparison_cache, mk_token_index
    try:
        import os
        filepath = os.path.join(os.path.dirname(__file__), 'minkobmand_produkter.xlsx')
        df = pd.read_excel(filepath)
        # Expected columns: Kategori, Navn, Producent, Netto Vægt, Kg-pris, Pris, Billede URL, Billede Hash
        products = []
        for _, row in df.iterrows():
            try:
                raw = row['Pris']
                price = float(str(raw).replace(',', '.').replace('kr', '').strip()) if isinstance(raw, str) else float(raw)
                if math.isnan(price) or price <= 0:
                    continue
                weight_str = str(row['Netto Vægt'])
                ppk = parse_kg_price(row.get('Kg-pris', ''))
                is_sale_raw = str(row.get('Tilbud', 'Nej')).lower()
                is_sale = True if is_sale_raw in ('ja', 'true', 'yes', '1') else False
                products.append({
                    'name':     str(row['Navn']),
                    'brand':    str(row['Producent']),
                    'weight':   weight_str,
                    'kg_price': ppk,
                    'price':    price,
                    'is_sale':  is_sale,
                    '_norm_name': normalize_name(str(row['Navn'])),
                    '_weight_g':  parse_weight_to_grams(weight_str),
                    'image':      str(row.get('Billede URL', '')),
                    '_image_hash': str(row.get('Billede Hash', '')),
                    'ean':        str(row.get('Varenummer', '')).split('.')[0].strip() if str(row.get('Varenummer', '')).strip() not in ('nan', 'None', '') else '',
                })
            except Exception as e:
                print(f"Skipping mk comparison row: {e}")
                continue

        # Build inverted index: token (≥4 chars) → set of product indices
        token_idx = {}
        for i, p in enumerate(products):
            for token in p['_norm_name'].split():
                if len(token) >= 4:
                    token_idx.setdefault(token, set()).add(i)

        mk_comparison_cache = products
        mk_token_index = token_idx
        print(f"Loaded {len(products)} MK products, {len(token_idx)} index tokens")
        return mk_comparison_cache, mk_token_index
    except Exception as e:
        print(f"Error loading minkobmand_produkter.xlsx: {e}")
        mk_comparison_cache, mk_token_index = [], {}
        return [], {}


def load_meny_comparison_data():
    """Load Meny products and build a token inverted index for fast matching."""
    global meny_comparison_cache, meny_token_index
    if 'meny_comparison_cache' in globals() and meny_comparison_cache is not None:
        return meny_comparison_cache, meny_token_index
    try:
        import os
        filepath = os.path.join(os.path.dirname(__file__), 'Meny_produkter.xlsx')
        df = pd.read_excel(filepath)
        products = []
        for _, row in df.iterrows():
            try:
                raw = row['Pris']
                price = float(str(raw).replace(',', '.').replace('kr', '').strip()) if isinstance(raw, str) else float(raw)
                if math.isnan(price) or price <= 0:
                    continue
                weight_str = str(row['Netto Vægt'])
                ppk = parse_kg_price(row.get('Kg-pris', ''))
                is_sale_raw = str(row.get('Tilbud', 'Nej')).lower()
                is_sale = True if is_sale_raw in ('ja', 'true', 'yes', '1') else False
                products.append({
                    'name':     str(row['Navn']),
                    'brand':    str(row['Producent']),
                    'weight':   weight_str,
                    'kg_price': ppk,
                    'price':    price,
                    'is_sale':  is_sale,
                    '_norm_name': normalize_name(str(row['Navn'])),
                    '_weight_g':  parse_weight_to_grams(weight_str),
                    'image':      str(row.get('Billede URL', '')),
                    '_image_hash': str(row.get('Billede Hash', '')),
                    'ean':        str(row.get('Varenummer', '')).split('.')[0].strip() if str(row.get('Varenummer', '')).strip() not in ('nan', 'None', '') else '',
                })
            except Exception as e:
                print(f"Skipping meny comparison row: {e}")
                continue

        token_idx = {}
        for i, p in enumerate(products):
            for token in p['_norm_name'].split():
                if len(token) >= 4:
                    token_idx.setdefault(token, set()).add(i)

        globals()['meny_comparison_cache'] = products
        globals()['meny_token_index'] = token_idx
        print(f"Loaded {len(products)} Meny products, {len(token_idx)} index tokens")
        return products, token_idx
    except Exception as e:
        print(f"Error loading Meny_produkter.xlsx: {e}")
        globals()['meny_comparison_cache'], globals()['meny_token_index'] = [], {}
        return [], {}


def load_spar_comparison_data():
    """Load Spar products and build a token inverted index for fast matching."""
    global spar_comparison_cache, spar_token_index
    if 'spar_comparison_cache' in globals() and spar_comparison_cache is not None:
        return spar_comparison_cache, spar_token_index
    try:
        import os
        filepath = os.path.join(os.path.dirname(__file__), 'Spar_produkter.xlsx')
        df = pd.read_excel(filepath)
        products = []
        for _, row in df.iterrows():
            try:
                raw = row['Pris']
                price = float(str(raw).replace(',', '.').replace('kr', '').strip()) if isinstance(raw, str) else float(raw)
                if math.isnan(price) or price <= 0:
                    continue
                weight_str = str(row['Netto Vægt'])
                ppk = parse_kg_price(row.get('Kg-pris', ''))
                is_sale_raw = str(row.get('Tilbud', 'Nej')).lower()
                is_sale = True if is_sale_raw in ('ja', 'true', 'yes', '1') else False
                products.append({
                    'name':     str(row['Navn']),
                    'brand':    str(row['Producent']),
                    'weight':   weight_str,
                    'kg_price': ppk,
                    'price':    price,
                    'is_sale':  is_sale,
                    '_norm_name': normalize_name(str(row['Navn'])),
                    '_weight_g':  parse_weight_to_grams(weight_str),
                    'image':      str(row.get('Billede URL', '')),
                    '_image_hash': str(row.get('Billede Hash', '')),
                    'ean':        str(row.get('Varenummer', '')).split('.')[0].strip() if str(row.get('Varenummer', '')).strip() not in ('nan', 'None', '') else '',
                })
            except Exception as e:
                print(f"Skipping spar comparison row: {e}")
                continue

        token_idx = {}
        for i, p in enumerate(products):
            for token in p['_norm_name'].split():
                if len(token) >= 4:
                    token_idx.setdefault(token, set()).add(i)

        globals()['spar_comparison_cache'] = products
        globals()['spar_token_index'] = token_idx
        print(f"Loaded {len(products)} Spar products, {len(token_idx)} index tokens")
        return products, token_idx
    except Exception as e:
        print(f"Error loading Spar_produkter.xlsx: {e}")
        globals()['spar_comparison_cache'], globals()['spar_token_index'] = [], {}
        return [], {}


def is_private_label(brand: str, title: str = '') -> bool:
    """Return True if the product is a private label from any store."""
    b = brand.lower().strip()
    t = title.lower().strip()
    
    if b.startswith('rema 1000') or b.startswith('rema '):
        return True
    if b.startswith('salling'):
        return True
    if b.startswith('first price') or b == 'fp':
        return True
        
    if t.startswith('salling ') or t.startswith('rema 1000 ') or t.startswith('rema ') or t.startswith('first price ') or t.startswith('fp '):
        return True
        
    return False


def find_bilka_match(rema_title, rema_description, bilka_products, token_idx, rema_brand='', rema_weight_g=None, threshold=0.60, rema_image_hash='', rema_price=0.0):
    """Token-indexed fuzzy match — only runs SequenceMatcher on candidates
    that share at least one 4-character token with the Rema product.

    Scoring components (all additive):
    1. Name fuzzy score          — basis 0..1 via SequenceMatcher
    2. Brand similarity boost    — up to +0.30 when brands match (e.g. Arla↔Arla)
    3. Image perceptual hash     — up to +0.25 when pHash distance ≤ 10

    Gates (hard reject before scoring):
    A. Brand-pairing: Rema 1000 private-label ↔ Salling private-label only.
    B. Weight: candidates whose weight differs > _WEIGHT_TOLERANCE_G are skipped.

    The threshold applies to the *combined* score so matching improves greatly
    when brand + name both agree, or when the image is visually identical.
    """
    rema_norms = [n for n in [normalize_name(rema_title), normalize_name(rema_description)] if n]
    if not rema_norms:
        return None

    # Use only the *title* norm as primary; description is a weaker secondary signal
    rema_title_norm = normalize_name(rema_title)

    base_is_pl = is_private_label(rema_brand, rema_title)
    norm_rema_brand = normalize_name(rema_brand)

    # Collect candidate indices via token index
    # Only index tokens from the TITLE (not description/brand) to avoid
    # spurious matches where e.g. 'vanilje' in the brand field pulls
    # in completely unrelated Bilka products.
    candidate_indices = set()
    primary_norm = rema_title_norm if rema_title_norm else rema_norms[0]
    for token in primary_norm.split():
        if len(token) >= 4 and token in token_idx:
            candidate_indices |= token_idx[token]

    # Fallback: if title alone gave no candidates, include description tokens too
    if not candidate_indices:
        for norm in rema_norms[1:]:
            for token in norm.split():
                if len(token) >= 4 and token in token_idx:
                    candidate_indices |= token_idx[token]

    if not candidate_indices:
        return None

    r_hash_obj = None
    if rema_image_hash and rema_image_hash not in ('None', 'nan', ''):
        try:
            r_hash_obj = imagehash.hex_to_hash(rema_image_hash)
        except Exception:
            pass

    best, best_score = None, 0.0
    rema_is_org = is_organic(rema_title, rema_description, rema_brand)
    for i in candidate_indices:
        bp = bilka_products[i]
        
        # Gate: Organic matching
        bp_is_org = is_organic(bp.get('name', ''), bp.get('description', ''), bp.get('brand', ''))
        if rema_is_org != bp_is_org:
            continue

        bp_is_pl = is_private_label(bp.get('brand', ''), bp.get('name', ''))

        # Gate A: Brand-pairing
        if base_is_pl and not bp_is_pl:
            continue
        if not base_is_pl and bp_is_pl:
            continue

        # Gate B: Weight
        if not weights_compatible(rema_weight_g, bp.get('_weight_g')):
            continue

        # Gate C: Price sanity — reject if Bilka price is > 5× the Rema price.
        # Protects against e.g. spirits/liqueur matching a dairy product because
        # they share a single flavour word like 'vanilje' or 'hindbær'.
        if rema_price and rema_price > 0:
            bp_price = bp.get('price', 0)
            try:
                if float(bp_price) > 5.0 * float(rema_price):
                    continue
            except (TypeError, ValueError):
                pass

        # --- Scoring ---
        # 1. Name similarity — scored against TITLE only (primary signal)
        name_score = fuzzy_score(rema_title_norm, bp['_norm_name']) if rema_title_norm else 0.0

        # Gate D: Token-overlap — the FIRST product word (first token ≥4 chars
        # in the Rema title, i.e. the product type noun) MUST appear somewhere
        # in the Bilka product name.
        # This prevents "SKYR VANILJE → Mælkeis m. vanilje" because 'skyr'
        # (the first token) is not in "mælkeis m vanilje".
        # We use the first token because it is typically the product category noun,
        # not the flavour/variant descriptor which the longest token often is.
        if rema_title_norm:
            rema_tokens = sorted([t for t in rema_title_norm.split() if len(t) >= 3], key=len, reverse=True)
            # Find a token that is likely the product type NOUN (usually the first or longest)
            if rema_tokens:
                # Variant check: If titles share a brand but have different dairy types
                dairy_types = ['mini', 'let', 'skummet', 'sod', 'piske', 'kærne', 'kær']
                rema_dairy = next((d for d in dairy_types if d in rema_title_norm), None)
                bp_dairy = next((d for d in dairy_types if d in bp['_norm_name']), None)
                if rema_dairy and bp_dairy and rema_dairy != bp_dairy:
                    continue

                # Gate D: First 4-char token check
                title_tokens_ordered = [t for t in rema_title_norm.split() if len(t) >= 4]
                if title_tokens_ordered:
                    first_token = title_tokens_ordered[0]
                    if first_token not in bp['_norm_name']:
                        continue

        # Minimum name_score gate: brand/image boosts alone must never trigger a match.
        # A candidate needs at least 0.50 raw name similarity before boosts are counted.
        if name_score < 0.50:
            continue

        # 2. Brand similarity boost (up to +0.30)
        if base_is_pl and bp_is_pl:
            brand_sim = 1.0
        else:
            brand_sim = brand_similarity(norm_rema_brand, bp.get('brand', ''))
        brand_boost = 0.30 * brand_sim

        # 3. Image perceptual hash boost (up to +0.25)
        image_boost = 0.0
        bp_hash = bp.get('_image_hash', '')
        if r_hash_obj and bp_hash and bp_hash not in ('None', 'nan', '') and len(bp_hash) >= 16:
            try:
                b_hash_obj = imagehash.hex_to_hash(bp_hash)
                dist = r_hash_obj - b_hash_obj
                if dist <= 10:  # tight threshold: visually near-identical
                    image_boost = 0.25 * (10 - dist) / 10.0
                elif dist <= 20:  # looser: same product, different resolution
                    image_boost = 0.10 * (20 - dist) / 20.0
            except Exception:
                pass

        score = name_score + brand_boost + image_boost
        if score > best_score:
            best_score = score
            best = bp

    return best if best_score >= threshold else None

def find_mk_match(rema_title, rema_description, mk_products, token_idx, rema_brand='', rema_weight_g=None, threshold=0.60, rema_image_hash='', rema_price=0.0):
    """Token-indexed fuzzy matching for Min Købmand with private-label brand gates."""
    rema_norms = [n for n in [normalize_name(rema_title), normalize_name(rema_description)] if n]
    if not rema_norms:
        return None

    rema_title_norm = normalize_name(rema_title)
    norm_rema_brand = normalize_name(rema_brand)
    base_is_pl = is_private_label(rema_brand, rema_title)

    candidate_indices = set()
    primary_norm = rema_title_norm if rema_title_norm else rema_norms[0]
    for token in primary_norm.split():
        if len(token) >= 4 and token in token_idx:
            candidate_indices |= token_idx[token]

    if not candidate_indices:
        for norm in rema_norms[1:]:
            for token in norm.split():
                if len(token) >= 4 and token in token_idx:
                    candidate_indices |= token_idx[token]

    if not candidate_indices:
        return None

    r_hash_obj = None
    if rema_image_hash and rema_image_hash not in ('None', 'nan', ''):
        try:
            r_hash_obj = imagehash.hex_to_hash(rema_image_hash)
        except Exception:
            pass

    best, best_score = None, 0.0
    rema_is_org = is_organic(rema_title, rema_description, rema_brand)
    for i in candidate_indices:
        mp = mk_products[i]

        # Gate: Organic matching
        mp_is_org = is_organic(mp.get('name', ''), mp.get('description', ''), mp.get('brand', ''))
        if rema_is_org != mp_is_org:
            continue

        # Gate: Brand-pairing (Private Label matching)
        mp_is_pl = is_private_label(mp.get('brand', ''), mp.get('name', ''))
        if base_is_pl and not mp_is_pl:
            continue
        if not base_is_pl and mp_is_pl:
            continue

        if not weights_compatible(rema_weight_g, mp.get('_weight_g')):
            continue

        if rema_price and rema_price > 0:
            mp_price = mp.get('price', 0)
            try:
                if float(mp_price) > 5.0 * float(rema_price):
                    continue
            except (TypeError, ValueError):
                pass

        name_score = fuzzy_score(rema_title_norm, mp['_norm_name']) if rema_title_norm else 0.0

        if rema_title_norm:
            # Variant check: Important for milk/dairy
            dairy_types = ['mini', 'let', 'skummet', 'sod', 'piske', 'kærne', 'kær']
            rema_dairy = next((d for d in dairy_types if d in rema_title_norm), None)
            mp_dairy = next((d for d in dairy_types if d in mp['_norm_name']), None)
            if rema_dairy and mp_dairy and rema_dairy != mp_dairy:
                continue

            title_tokens_ordered = [t for t in rema_title_norm.split() if len(t) >= 4]
            if title_tokens_ordered:
                first_token = title_tokens_ordered[0]
                if first_token not in mp['_norm_name']:
                    continue

        if name_score < 0.50:
            continue

        # 2. Brand similarity boost (up to +0.30)
        if base_is_pl and mp_is_pl:
            brand_sim = 1.0
        else:
            brand_sim = brand_similarity(norm_rema_brand, mp.get('brand', ''))
        brand_boost = 0.30 * brand_sim

        image_boost = 0.0
        mp_hash = mp.get('_image_hash', '')
        if r_hash_obj and mp_hash and mp_hash not in ('None', 'nan', '') and len(mp_hash) >= 16:
            try:
                m_hash_obj = imagehash.hex_to_hash(mp_hash)
                dist = r_hash_obj - m_hash_obj
                if dist <= 10:
                    image_boost = 0.25 * (10 - dist) / 10.0
                elif dist <= 20:
                    image_boost = 0.10 * (20 - dist) / 20.0
            except Exception:
                pass

        score = name_score + brand_boost + image_boost

        if score > best_score:
            best_score = score
            best = mp

    return best if best_score >= threshold else None


def find_meny_match(rema_title, rema_description, meny_products, token_idx, rema_brand='', rema_weight_g=None, threshold=0.60, rema_image_hash='', rema_price=0.0):
    """Token-indexed fuzzy matching for Meny."""
    return find_mk_match(rema_title, rema_description, meny_products, token_idx, rema_brand, rema_weight_g, threshold, rema_image_hash, rema_price)


def find_spar_match(rema_title, rema_description, spar_products, token_idx, rema_brand='', rema_weight_g=None, threshold=0.60, rema_image_hash='', rema_price=0.0):
    """Token-indexed fuzzy matching for Spar."""
    return find_mk_match(rema_title, rema_description, spar_products, token_idx, rema_brand, rema_weight_g, threshold, rema_image_hash, rema_price)


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Bilka display helpers
# ---------------------------------------------------------------------------

_BILKA_CATEGORY_RULES = [
    # (kategori, tuple af nøgleord der skal matche i produktnavnet)
    ('Drikkevarer',        ('cola', 'sodavand', 'juice', 'energidrik', 'øl', 'vin', 'spiritus',
                            'smoothie', 'vand', 'saft', 'cider', 'whisky', 'vodka', 'gin',
                            'rom', 'tequila', 'likør', 'akvavit', 'champagne', 'prosecco',
                            'cava', 'iste', 'sportsdrik', 'ingefærshot', 'kombucha',
                            'kokosvand', 'shots', 'frugtdrik', 'blanding', 'sirup',
                            'drik', 'lemonade', 'breezer', 'smirnoff', 'sangria',
                            'hvidvin', 'rødvin', 'rosévin', 'pilsner', 'bitter', 'tonic')),
    ('Frost',              ('pommes frites', 'kyllingenuggets', 'frikadeller', 'flødeis',
                            'mælkeis', 'sorbetis', 'ispinde', 'isvafler', 'pizza m.',
                            'fuldkornsboller', 'håndværkere', 'miniflutes', 'croissanter',
                            'pain au chocolat', 'kanelsnegle', 'tebirkes', 'surdejsstykker',
                            'baguettes', 'focaccia m.', 'boller m.', 'bagels',
                            'grøntsagsblanding', 'bærblanding', 'blåbær', 'jordbær', 'hindbær',
                            'brombær', 'frys-selv', 'frossen', 'mukimame', 'edamame',
                            'kartoffelriste', 'kartoffelkroketter', 'løgringe',
                            'fiskepinde', 'panerede', 'rejenuggets', 'tempurarejer',
                            'butterfly rejer', 'vannamei rejer', 'grønlandske rejer',
                            'dumplings', 'gyoza', 'forårsruller', 'samosa', 'falafler',
                            'kødboller', 'melboller', 'karbonader', 'burgerbøffer',
                            'tikka masala m.', 'butter chicken m.', 'lasagne bolognese',
                            'spaghetti bolognese', 'karbonade m.', 'boller i karry m. ris',
                            'kylling i', 'flødeisvafler', 'mælkeis sandwich',
                            'limonadeis', 'islagkage', 'chokoladefondant', 'tiramisu',
                            'æbleskiver', 'æbleskiver m.', 'æblekage', 'skovbærtærte',
                            'citrontærte', 'cheesecake 2 stk', 'sacher 2 stk',
                            'tærte', 'macarons', 'pølsehorn', 'møllehjul',
                            'astronautis', "carte d'or")),
    ('Slik',               ('chips m.', 'majschips', 'linsechips', 'rodfrugtchips',
                            'popcorn', 'skumfiduser', 'vingummi', 'lakrids', 'chokoladebar',
                            'mælkechokolade', 'mørk chokolade', 'hvid chokolade',
                            'karameller', 'bolcher', 'pastiller', 'tyggegummi',
                            'müslibar', 'frugtsnacks', 'frugtstænger', 'rosiner',
                            'nøddeblanding', 'peanuts', 'flæskesvær', 'saltsnacks',
                            'saltstænger', 'marcipanbrød', 'vingummibamser',
                            'skumbananer', 'ostepops', 'dipmix', 'click mix',
                            'matador mix', 'stjerne mix', 'favorit mix', 'beef jerky',
                            'tørret mango', 'tørrede', 'rawbar', 'daddelbar',
                            'müslibarer', 'chokoladekugler', 'lakridsstænger',
                            'chips', 'osterejer', 'blandede chokolader')),
    ('Brød & Bavinchi',    ('rugbrød', 'toastbrød', 'sandwichbrød', 'burgerboller',
                            'hotdogbrød', 'pølsebrød', 'baguette', 'pitabrød',
                            'naanbrød', 'knækbrød', 'digestive kiks', 'mariekiks',
                            'havrekiks', 'kiks m.', 'cookies m.', 'kiks',
                            'fuldkornsboller', 'solsikkeboller', 'rugboller',
                            'sandwichboller', 'hvedeboller', 'yoghurtboller',
                            'krydderboller', 'surdejsbrød', 'focaccia', 'ciabatta',
                            'grissini', 'knækbrød', 'rasp', 'tarteletter',
                            'lagkagebunde', 'tærtebund', 'vafler', 'isvafler',
                            'bondebrød', 'schwarzbrot', 'fladbrød', 'tortillas',
                            'tortillachips', 'pitabrød', 'fastelavnsbolle',
                            'boller', 'brød', 'bagels', 'citronmåne', 'romkugler',
                            'drømmekage', 'kanelstang', 'daim mini', 'mazarinkager',
                            'kammerjunkere', 'brownie', 'muffins', 'chokoladekage',
                            'citronkage', 'marmorkage', 'sandkage', 'gulerodskage',
                            'hindbærroulade', 'roulade', 'vaniljekranse', 'honningsnitter',
                            'småkager', 'tvebakker', 'pumpernickel', 'grovboller',
                            'proteinboller', 'proteinbrød', 'gulerodsboller',
                            'fuldkornssandwichbrød', 'skagensbrød', 'brioche',
                            'pølsehornsdej', 'pizzadej', 'butterdej', 'croissantdej',
                            'tærtedej', 'fuldkornspizzabunde', 'surdejspizzadej',
                            'surdejsboller', 'surdejsbrød')),
    ('Ost m.v.',           ('danbo', 'havarti', 'cheddar', 'mozzarella', 'brie',
                            'camembert', 'feta', 'gorgonzola', 'emmentaler', 'gouda',
                            'ricotta', 'mascarpone', 'burrata', 'parmesan', 'parmigiano',
                            'grana padano', 'pecorino', 'manchego', 'jarlsberg',
                            'samsø ost', 'danablu', 'blåskimmelost', 'rygeost',
                            'smøreost', 'flødeost', 'ostehaps', 'ostetern',
                            'salatost', 'hytteost', 'halloumi', 'gruyere',
                            'comté', 'port salut', 'præst', 'rødkitost')),
    ('Mejeri',             ('mælk', 'smør', 'piskefløde', 'skyr', 'yoghurt',
                            'kefir', 'fraiche', 'creme fraiche', 'kærnemælk', 'ymer',
                            'bagegær', 'æg', 'havredrik', 'sojadrik', 'mandeldrik',
                            'risdrik', 'oatly', 'flydende til madlavning',
                            'stegemargarine', 'plantemargarine', 'smørbar')),
    ('Frugt & grønt',      ('agurk', 'bananer', 'banan', 'peberfrugt', 'tomat',
                            'gulerødder', 'gulerod', 'salat', 'broccoli', 'blomkål',
                            'æbler', 'æble', 'pærer', 'pære', 'appelsin', 'citron',
                            'jordbær', 'hindbær', 'kål', 'rødkål', 'hvidkål',
                            'spidskål', 'løg', 'rødløg', 'forårsløg', 'kartofler',
                            'kartoffel', 'squash', 'avocado', 'spinat', 'svampe',
                            'champignon', 'melon', 'druer', 'mango', 'ananas',
                            'blåbær', 'brombær', 'solbær', 'tranebær', 'klementiner',
                            'kiwi', 'lime', 'citrongræs', 'ingefær', 'hvidløg',
                            'purløg', 'persille', 'dild', 'basilikum', 'rosmarin',
                            'timian', 'asparges', 'artiskok', 'selleri', 'pastinak',
                            'persillerod', 'rødbeder', 'jordskokkerne', 'aubergine',
                            'courgette', 'rosenkål', 'grønkål', 'rucola', 'feldsalat',
                            'icebergsalat', 'romainesalat', 'pak choi', 'sugarsnaps',
                            'ærter', 'bobbybønner', 'sukkerærter', 'vandmelon',
                            'papaya', 'dadler', 'figner', 'granatæble', 'coconut',
                            'passionsfrugt', 'mandariner', 'klementiner', 'nektariner',
                            'abrikoser', 'blomme', 'kirsebær', 'vindruer',
                            'hokkaido', 'butternut')),
    ('Nemt & hurtigt',     ('boller i karry', 'lasagne', 'spaghetti bolognese',
                            'pasta carbonara', 'burger', 'frokostplatte',
                            'kylling tikka masala', 'tikka masala', 'butter chicken',
                            'tarteletfyld', 'biksemad', 'millionbøf', 'flæskestegsburger',
                            'schnitzel m. tilbehør', 'karbonader m.', 'frikadeller m.',
                            'hakkebøffer m.', 'kartoffelmos m.', 'boller i karry m.',
                            'kylling i karry', 'kylling i rød', 'kylling m. ris',
                            'pasta m. kylling', 'pasta bolognese', 'mørbradgryde',
                            'paprikagryde', 'goulash', 'boller i karry',
                            'forloren hare', 'wienergryde', 'jægergryde',
                            'gyros m.', 'kyllingewok', 'ris m. kylling',
                            'risotto m.')),
    ('Kolonial',           ('pasta', 'ris', 'mel', 'sukker', 'olie', 'sauce',
                            'ketchup', 'marmelade', 'konserves', 'havregryn',
                            'müsli', 'musli', 'granola', 'bouillon', 'krydderi',
                            'sennep', 'mayonnaise', 'remoulade', 'dressing',
                            'tun i', 'makrel i', 'sardiner', 'oliven', 'kapers',
                            'pesto', 'tomatsauce', 'passata', 'hakkede tomater',
                            'tomatpuré', 'pizzasauce', 'bechamelsauce', 'hollandaise',
                            'bearnaisesauce', 'honning', 'sirup', 'eddike',
                            'cornflakes', 'frosties', 'coco pops', 'cheerios',
                            'havrefras', 'fiberknas', 'guldkorn', 'risottoris',
                            'basmatiris', 'jasminris', 'parboiled', 'fusilli',
                            'spaghetti', 'penne', 'lasagneplader', 'tagliatelle',
                            'gnocchi', 'instant kaffe', 'formalet kaffe', 'hele bønner',
                            'kaffekapsler', 'te', 'bagepulver', 'vaniljesukker',
                            'chiafrø', 'hørfrø', 'solsikkekerner', 'valnødder',
                            'cashewnødder', 'mandler', 'pinjekerner', 'pistaciekerner',
                            'kokosmel', 'kokosmælk', 'sojasauce', 'woksauce',
                            'tortillas', 'tacosauce', 'tortillachips',
                            'nudler', 'risnudler', 'hvedenudler', 'glasnudler',
                            'chilisauce', 'teriyaki')),
    ('Køl',                ()),   # catch-all for everything else from Køl
]


def bilka_display_category(name):
    """Map a Bilka product name to the closest website category."""
    n = name.lower()
    for category, keywords in _BILKA_CATEGORY_RULES:
        if any(kw in n for kw in keywords):
            return category
    # Default: if nothing matched, put in Kolonial
    return 'Kolonial'


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


def build_bilka_display_products(bilka_comparison):
    """Convert the comparison product list into raw product dicts for templates."""
    display = []
    for i, bp in enumerate(bilka_comparison):
        try:
            price = float(bp['price'])
            if price <= 0:
                continue  # Bug 3: skip zero/negative-price Bilka products
            ppk = parse_kg_price(bp.get('kg_price', ''))
            display.append({
                '/product/id':                    f'bilka_{i}',
                '/product/title':                 bp['name'],
                '/product/price':                 price,
                '/product/sale_price':            price if bp.get('is_sale') else None,
                '/product/description':           bp.get('weight', ''),
                '/product/brand':                 bp.get('brand', ''),
                '/product/imageLink':             bp['image'] if bp.get('image') and str(bp['image']).lower() != 'nan' else '/static/images/bilka-logo.png',
                '/product/product_type':          bilka_display_category(bp['name']),
                '/product/sale_price_effective_date': '',
                '/product/unit_pricing_measure':  bp.get('weight', ''),
                '/product/weight_grams':          bp.get('_weight_g'),
                '/product/price_per_kg':          ppk,
                '/product/store':                 'Bilka',
                '/product/bilka_match':           None,
                '/product/mk_match':              None,
                '/product/cheapest_at':           None,
                '/product/cheaper_at':            None,
            })
        except Exception:
            continue
    print(f"Built {len(display)} Bilka display products")
    return display

def build_mk_display_products(mk_comparison):
    """Convert the Min Købmand product list into raw product dicts for templates."""
    display = []
    for i, mp in enumerate(mk_comparison):
        try:
            price = float(mp['price'])
            if price <= 0:
                continue
            ppk = parse_kg_price(mp.get('kg_price', ''))
            display.append({
                '/product/id':                    f'mk_{i}',
                '/product/title':                 mp['name'],
                '/product/price':                 price,
                '/product/sale_price':            price if mp.get('is_sale') else None,
                '/product/description':           mp.get('weight', ''),
                '/product/brand':                 mp.get('brand', ''),
                '/product/imageLink':             mp['image'] if mp.get('image') and str(mp['image']).lower() != 'nan' else '/static/images/Min_kobmand_logo.png',
                '/product/product_type':          bilka_display_category(mp['name']),
                '/product/sale_price_effective_date': '',
                '/product/unit_pricing_measure':  mp.get('weight', ''),
                '/product/weight_grams':          mp.get('_weight_g'),
                '/product/price_per_kg':          ppk,
                '/product/store':                 'Min Købmand',
                '/product/bilka_match':           None,
                '/product/mk_match':              None,
                '/product/cheaper_at':            None,
            })
        except Exception:
            continue
    print(f"Built {len(display)} Min Købmand display products")
    return display

def build_meny_display_products(meny_comparison):
    """Convert the Meny product list into raw product dicts for templates."""
    display = []
    for i, mp in enumerate(meny_comparison):
        try:
            price = float(mp['price'])
            if price <= 0:
                continue
            ppk = parse_kg_price(mp.get('kg_price', ''))
            display.append({
                '/product/id':                    f'meny_{i}',
                '/product/title':                 mp['name'],
                '/product/price':                 price,
                '/product/sale_price':            price if mp.get('is_sale') else None,
                '/product/description':           mp.get('weight', ''),
                '/product/brand':                 mp.get('brand', ''),
                '/product/imageLink':             mp['image'] if mp.get('image') and str(mp['image']).lower() != 'nan' else '/static/images/meny-logo.png',
                '/product/product_type':          bilka_display_category(mp['name']),
                '/product/sale_price_effective_date': '',
                '/product/unit_pricing_measure':  mp.get('weight', ''),
                '/product/weight_grams':          mp.get('_weight_g'),
                '/product/price_per_kg':          ppk,
                '/product/store':                 'Meny',
                '/product/bilka_match':           None,
                '/product/mk_match':              None,
                '/product/meny_match':            None,
                '/product/spar_match':            None,
                '/product/cheaper_at':            None,
            })
        except Exception:
            continue
    print(f"Built {len(display)} Meny display products")
    return display

def build_spar_display_products(spar_comparison):
    """Convert the Spar product list into raw product dicts for templates."""
    display = []
    for i, sp in enumerate(spar_comparison):
        try:
            price = float(sp['price'])
            if price <= 0:
                continue
            ppk = parse_kg_price(sp.get('kg_price', ''))
            display.append({
                '/product/id':                    f'spar_{i}',
                '/product/title':                 sp['name'],
                '/product/price':                 price,
                '/product/sale_price':            price if sp.get('is_sale') else None,
                '/product/description':           sp.get('weight', ''),
                '/product/brand':                 sp.get('brand', ''),
                '/product/imageLink':             sp['image'] if sp.get('image') and str(sp['image']).lower() != 'nan' else '/static/images/spar-logo.png',
                '/product/product_type':          bilka_display_category(sp['name']),
                '/product/sale_price_effective_date': '',
                '/product/unit_pricing_measure':  sp.get('weight', ''),
                '/product/weight_grams':          sp.get('_weight_g'),
                '/product/price_per_kg':          ppk,
                '/product/store':                 'Spar',
                '/product/bilka_match':           None,
                '/product/mk_match':              None,
                '/product/meny_match':            None,
                '/product/spar_match':            None,
                '/product/cheaper_at':            None,
            })
        except Exception:
            continue
    print(f"Built {len(display)} Spar display products")
    return display

def validate_xml_structure(xml_dict):
    """Validate the XML data structure"""
    if not isinstance(xml_dict, dict):
        print("Error: XML data is not a dictionary")
        return False
        
    if 'products' not in xml_dict:
        print("Error: No 'products' element in XML")
        return False
        
    if not isinstance(xml_dict['products'], dict):
        print("Error: 'products' is not a dictionary")
        return False
        
    if 'product' not in xml_dict['products']:
        print("Error: No 'product' element in products")
        return False
        
    if not isinstance(xml_dict['products']['product'], list):
        print("Error: 'product' is not a list")
        return False
        
    return True

def fetch_and_parse_xml():
    """Fetch and parse data from both XML and Excel sources"""
    try:
        print("\n=== Starting data fetch and parse ===")
        
        # Initialize empty list for Rema XML
        rema_products = []
        
        # 1. Fetch and parse XML data (Rema 1000)
        print("Fetching XML data from:", XML_URL)
        try:
            rema_hashes = {}
            hash_path = os.path.join(os.path.dirname(__file__), 'data', 'rema_hashes.json')
            if os.path.exists(hash_path):
                try:
                    with open(hash_path, 'r', encoding='utf-8') as f:
                        rema_hashes = json.load(f)
                except Exception as e:
                    print(f"Fejl ved indlæsning af rema_hashes.json: {e}")
            
            response = requests.get(XML_URL, timeout=30, headers=DEFAULT_HTTP_HEADERS)
            response.raise_for_status()
            
            print(f"Response status: {response.status_code}")
            print(f"Response content type: {response.headers.get('content-type', 'unknown')}")
            
            # Parse XML to dict
            xml_dict = xmltodict.parse(response.text)
            
            if validate_xml_structure(xml_dict):
                print(f"XML structure validated successfully")
                
                # Bug 2: Map Rema category names to internal category keys
                REMA_CATEGORY_MAP = {
                    'Mejeriprodukter & kølvarer': 'Mejeri',
                    'Mejeri': 'Mejeri',
                    'Kolonialvarer': 'Kolonial',
                    'Kolonial': 'Kolonial',
                    'Drikkevarer': 'Drikkevarer',
                    'Frugt & grønt': 'Frugt & grønt',
                    'Frugt og grønt': 'Frugt & grønt',
                    'Brød & kager': 'Brød & Bavinchi',
                    'Brød og kager': 'Brød & Bavinchi',
                    'Frost': 'Frost',
                    'Slik & snacks': 'Slik',
                    'Slik og snacks': 'Slik',
                    'Kød, fisk & fjerkræ': 'Kød, fisk & fjerkræ',
                    'Kød fisk fjerkræ': 'Kød, fisk & fjerkræ',
                    'Ost': 'Ost m.v.',
                    'Ost m.v.': 'Ost m.v.',
                    'Nemt & hurtigt': 'Nemt & hurtigt',
                    'Nemt og hurtigt': 'Nemt & hurtigt',
                    'Køl': 'Køl',
                    'Baby og småbørn': 'Baby og småbørn',
                    'Personlig pleje': 'Personlig pleje',
                    'Husholdning': 'Husholdning',
                    'Kiosk': 'Kiosk',
                }

                for i, product in enumerate(xml_dict['products']['product']):
                    try:
                        # Extract price and clean it
                        price = format_price(product.get('price', '0 DKK'))
                        sale_price = format_price(product.get('sale_price', '')) or None

                        # Bug 3: Skip products with price 0
                        if price <= 0:
                            continue

                        # Bug 2: Map Rema product_type to internal category
                        raw_type = product.get('product_type', '')
                        mapped_type = REMA_CATEGORY_MAP.get(raw_type, raw_type)

                        unit_measure = product.get('unit_pricing_measure', '')
                        product_dict = {
                            '/product/id': product.get('id', ''),
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
                            '/product/weight_g': parse_weight_to_grams(unit_measure),
                            '/product/image_hash': rema_hashes.get(str(product.get('id', '')), '')
                        }

                        rema_products.append(product_dict)

                    except Exception as e:
                        print(f"Error processing Rema 1000 product {i}: {str(e)}")
                        print("Product data:", json.dumps(product, indent=2))
                        continue
                
                print(f"\nTotal Rema 1000 products parsed: {len(rema_products)}")
            else:
                print("XML validation failed")
                
        except Exception as e:
            print(f"Error fetching Rema 1000 data: {str(e)}")
            import traceback
            traceback.print_exc()
        
        # 3. Annotate each Rema product with Bilka & MK comparison data
        print("\nAnnotating Rema products with comparison data")
        bilka_comparison, token_idx_bilka = load_bilka_comparison_data()
        mk_comparison, token_idx_mk = load_mk_comparison_data()
        meny_comparison, token_idx_meny = load_meny_comparison_data()
        spar_comparison, token_idx_spar = load_spar_comparison_data()

        final_products = []
        matched_bilka_ids = set()
        matched_mk_ids = set()
        matched_meny_ids = set()
        matched_spar_ids = set()
        match_count_bilka = 0
        match_count_mk = 0
        match_count_meny = 0
        match_count_spar = 0
        
        for product in rema_products:
            rema_effective = (
                float(product['/product/sale_price'])
                if product['/product/sale_price'] is not None
                and not math.isnan(float(product['/product/sale_price']))
                else float(product['/product/price'])
            )
            
            bilka_match = find_bilka_match(
                str(product['/product/title']),
                str(product['/product/description']),
                bilka_comparison,
                token_idx_bilka,
                rema_brand=str(product.get('/product/brand', '')),
                rema_weight_g=product.get('/product/weight_g'),
                rema_image_hash=product.get('/product/image_hash', ''),
                rema_price=float(product['/product/price'])
            )
            
            mk_match = find_mk_match(
                str(product['/product/title']),
                str(product['/product/description']),
                mk_comparison,
                token_idx_mk,
                rema_brand=str(product.get('/product/brand', '')),
                rema_weight_g=product.get('/product/weight_g'),
                rema_image_hash=product.get('/product/image_hash', ''),
                rema_price=float(product['/product/price'])
            )

            meny_match = find_meny_match(
                str(product['/product/title']),
                str(product['/product/description']),
                meny_comparison,
                token_idx_meny,
                rema_brand=str(product.get('/product/brand', '')),
                rema_weight_g=product.get('/product/weight_g'),
                rema_image_hash=product.get('/product/image_hash', ''),
                rema_price=float(product['/product/price'])
            )

            spar_match = find_spar_match(
                str(product['/product/title']),
                str(product['/product/description']),
                spar_comparison,
                token_idx_spar,
                rema_brand=str(product.get('/product/brand', '')),
                rema_weight_g=product.get('/product/weight_g'),
                rema_image_hash=product.get('/product/image_hash', ''),
                rema_price=float(product['/product/price'])
            )
            
            cheapest_at = 'rema'
            cheapest_price = rema_effective
            
            # Hvis Rema matcher den ene (f.eks. Bilka) men ikke den anden (f.eks. Min Købmand),
            # og den fundne har et EAN nummer, prøv at finde den manglende via eksakt EAN match.
            if bilka_match and not mk_match and bilka_match.get('ean'):
                for mp in mk_comparison:
                    if mp.get('ean') and mp['ean'] == bilka_match['ean']:
                        mk_match = mp
                        break
            elif mk_match and not bilka_match and mk_match.get('ean'):
                for bp in bilka_comparison:
                    if bp.get('ean') and bp['ean'] == mk_match['ean']:
                        bilka_match = bp
                        break

            # Try EAN match for Meny and Spar too
            if (bilka_match or mk_match):
                match_to_use = bilka_match or mk_match
                if match_to_use.get('ean'):
                    if not meny_match:
                        for mp in meny_comparison:
                            if mp.get('ean') and mp['ean'] == match_to_use['ean']:
                                meny_match = mp
                                break
                    if not spar_match:
                        for sp in spar_comparison:
                            if sp.get('ean') and sp['ean'] == match_to_use['ean']:
                                spar_match = sp
                                break
            
            if bilka_match:
                product['/product/bilka_match'] = bilka_match
                matched_bilka_ids.add(id(bilka_match))
                match_count_bilka += 1
                if bilka_match['price'] < cheapest_price:
                    cheapest_at = 'bilka'
                    cheapest_price = bilka_match['price']
            else:
                product['/product/bilka_match'] = None
                
            if mk_match:
                product['/product/mk_match'] = mk_match
                matched_mk_ids.add(id(mk_match))
                match_count_mk += 1
                if mk_match['price'] < cheapest_price:
                    cheapest_at = 'minkobmand'
                    cheapest_price = mk_match['price']
            else:
                product['/product/mk_match'] = None

            if meny_match:
                product['/product/meny_match'] = meny_match
                matched_meny_ids.add(id(meny_match))
                match_count_meny += 1
                if meny_match['price'] < cheapest_price:
                    cheapest_at = 'meny'
                    cheapest_price = meny_match['price']
            else:
                product['/product/meny_match'] = None

            if spar_match:
                product['/product/spar_match'] = spar_match
                matched_spar_ids.add(id(spar_match))
                match_count_spar += 1
                if spar_match['price'] < cheapest_price:
                    cheapest_at = 'spar'
                    cheapest_price = spar_match['price']
            else:
                product['/product/spar_match'] = None
                
            product['/product/cheapest_at'] = cheapest_at
            # Kept for backwards compatibility just in case
            product['/product/cheaper_at'] = cheapest_at
            
            # Check if any store has a sale
            is_any_sale = False
            if product.get('/product/sale_price'):
                is_any_sale = True
            elif (bilka_match and bilka_match.get('is_sale')) or \
                 (mk_match and mk_match.get('is_sale')) or \
                 (meny_match and meny_match.get('is_sale')) or \
                 (spar_match and spar_match.get('is_sale')):
                is_any_sale = True
            product['/product/is_any_sale'] = is_any_sale
            
            final_products.append(product)

        # Saml alle u-matchede varer fra de 4 butikker
        unmatched_bilka = [p for p in bilka_comparison if id(p) not in matched_bilka_ids]
        unmatched_mk = [p for p in mk_comparison if id(p) not in matched_mk_ids]
        unmatched_meny = [p for p in meny_comparison if id(p) not in matched_meny_ids]
        unmatched_spar = [p for p in spar_comparison if id(p) not in matched_spar_ids]

        # Gruppér u-matchede varer efter EAN
        ean_groups = {}
        
        def add_to_groups(products, store_name):
            for p in products:
                ean = p.get('ean')
                if ean:
                    if ean not in ean_groups:
                        ean_groups[ean] = {}
                    ean_groups[ean][store_name] = p
                else:
                    # Varer uden EAN tilføjes som solo-kort med det samme
                    solo_display = None
                    if store_name == 'Bilka': solo_display = build_bilka_display_products([p])
                    elif store_name == 'Min Købmand': solo_display = build_mk_display_products([p])
                    elif store_name == 'Meny': solo_display = build_meny_display_products([p])
                    elif store_name == 'Spar': solo_display = build_spar_display_products([p])
                    
                    if solo_display:
                        final_products.extend(solo_display)

        add_to_groups(unmatched_bilka, 'Bilka')
        add_to_groups(unmatched_mk, 'Min Købmand')
        add_to_groups(unmatched_meny, 'Meny')
        add_to_groups(unmatched_spar, 'Spar')

        # Opret kombinerede kort for varer med samme EAN
        for ean, group in ean_groups.items():
            # Find en "hovedvare" at bygge kortet ud fra (prioritér Bilka, så MK, osv.)
            main_store = None
            for s in ['Bilka', 'Min Købmand', 'Meny', 'Spar']:
                if s in group:
                    main_store = s
                    break
            
            if not main_store: continue
            main_p = group[main_store]
            
            # Byg display-objektet
            display_item = None
            if main_store == 'Bilka': display_item = build_bilka_display_products([main_p])[0]
            elif main_store == 'Min Købmand': display_item = build_mk_display_products([main_p])[0]
            elif main_store == 'Meny': display_item = build_meny_display_products([main_p])[0]
            elif main_store == 'Spar': display_item = build_spar_display_products([main_p])[0]

            if not display_item: continue

            # Tilføj matches fra de andre butikker i gruppen
            cheapest_store = main_store.lower().replace(' ', '')
            cheapest_price = main_p['price']

            if 'Bilka' in group:
                display_item['/product/bilka_match'] = group['Bilka']
                if group['Bilka']['price'] < cheapest_price:
                    cheapest_price = group['Bilka']['price']
                    cheapest_store = 'bilka'
            
            if 'Min Købmand' in group:
                display_item['/product/mk_match'] = group['Min Købmand']
                if group['Min Købmand']['price'] < cheapest_price:
                    cheapest_price = group['Min Købmand']['price']
                    cheapest_store = 'minkobmand'

            if 'Meny' in group:
                display_item['/product/meny_match'] = group['Meny']
                if group['Meny']['price'] < cheapest_price:
                    cheapest_price = group['Meny']['price']
                    cheapest_store = 'meny'

            if 'Spar' in group:
                display_item['/product/spar_match'] = group['Spar']
                if group['Spar']['price'] < cheapest_price:
                    cheapest_price = group['Spar']['price']
                    cheapest_store = 'spar'

            display_item['/product/cheapest_at'] = cheapest_store
            display_item['/product/cheaper_at'] = cheapest_store
            
            # Check if any store in this group is on sale
            is_any_sale = False
            for s_p in group.values():
                if s_p.get('is_sale'):
                    is_any_sale = True
                    break
            display_item['/product/is_any_sale'] = is_any_sale
            
            final_products.append(display_item)

        print(
            f"\nFinal product list: {len(final_products)} products "
            f"({len(rema_products)} Rema + {len(final_products) - len(rema_products)} unmatched comparison cards), "
            f"{match_count_bilka} matched to Bilka, {match_count_mk} matched to MK, {match_count_meny} matched to Meny, {match_count_spar} matched to Spar"
        )
        return final_products
        
    except Exception as e:
        print(f"Error in fetch_and_parse_xml: {str(e)}")
        import traceback
        traceback.print_exc()
        return []

def get_product_data():
    """Get product data with caching"""
    global cached_data
    current_time = datetime.now()
    
    # Check if cache is valid
    if (cached_data['timestamp'] is None or 
        cached_data['data'] is None or 
        current_time - cached_data['timestamp'] >= CACHE_DURATION):
        
        # Fetch new data
        products = fetch_and_parse_xml()
        
        # Update cache
        cached_data = {
            'timestamp': current_time,
            'data': products
        }
        
    else:
        print("Using cached data")
    
    return cached_data['data']

@app.route('/newsletters')
def newsletters():
    try:
        data_path = os.path.join(os.path.dirname(__file__), 'data', 'newsletters.json')
        newsletters_list = []
        if os.path.exists(data_path):
            with open(data_path, 'r', encoding='utf-8') as f:
                newsletters_list = json.load(f)

        # Build Bilka (Food) entries dynamically by probing availability
        try:
            today = datetime.now()
            current_year, current_week, _ = today.isocalendar()
            next_week_date_for_url = today + timedelta(days=7)
            next_year, next_week, _ = next_week_date_for_url.isocalendar()

            def bilka_url(year_val, week_val):
                return f"https://avis.bilka.dk/bilka/aviser/bilka-{year_val}/uge-{week_val}-food/?page=1"

            def url_exists(url):
                try:
                    # Try HEAD first, fall back to GET
                    r = requests.head(url, timeout=5, allow_redirects=True)
                    if r.status_code == 200:
                        return True
                    # Some origins may not support HEAD reliably
                    r = requests.get(url, timeout=7, allow_redirects=True)
                    return r.status_code == 200
                except Exception:
                    return False

            candidates = [
                (current_year, current_week, 'current'),
                (next_year, next_week, 'next')
            ]

            # Remove any existing Bilka items from JSON to avoid duplicates
            filtered = []
            for it in newsletters_list:
                title = str(it.get('title', ''))
                viewer_url = str(it.get('viewer_url', ''))
                source_url = str(it.get('url', ''))
                if ('bilka' in title.lower()) or ('avis.bilka.dk' in viewer_url.lower()) or ('bilkaavisen' in source_url.lower()):
                    continue
                filtered.append(it)
            newsletters_list = filtered

            bilka_dynamic = []
            for y, w, tag in candidates:
                u = bilka_url(y, w)
                if url_exists(u):
                    bilka_dynamic.append({
                        'title': f"Bilka Uge {w}",
                        'date': '',
                        'url': 'https://www.bilka.dk/bilkaavisen/',
                        'pdf': '',
                        'image': '/static/images/bilka-logo.png',
                        'viewer': 'link',
                        'viewer_url': u,
                        'bilka_week': w,
                        'bilka_year': y,
                        'bilka_tag': tag
                    })
        except Exception:
            bilka_dynamic = []

        # Build REMA 1000 entries dynamically by scraping the avis overview (current and upcoming if present)
        try:
            REMA_OVERVIEW_URL = 'https://shop.rema1000.dk/avis/'
            rema_dynamic = []

            def scrape_rema_weeks():
                try:
                    r = requests.get(REMA_OVERVIEW_URL, timeout=10, headers=DEFAULT_HTTP_HEADERS)
                    r.raise_for_status()
                    html = r.text
                    # Find pairs of "Uge/UGE XX" near an avis link (allow larger window; site may insert wrappers)
                    matches = re.findall(r'(Uge|UGE)\s*(\d{1,2}).{0,1200}?href\s*=\s*"(/avis/[A-Za-z0-9_-]+(?:\?page=1)?)"', html, flags=re.IGNORECASE|re.DOTALL)
                    # Also detect tiles marked "Kommende" with a link nearby (treat as next week if week label missing)
                    kommende = re.findall(r'Kommende.{0,1200}?href\s*=\s*"(/avis/[A-Za-z0-9_-]+(?:\?page=1)?)"', html, flags=re.IGNORECASE|re.DOTALL)
                    week_to_url = {}
                    for _, wk, href in matches:
                        try:
                            week_num = int(wk)
                            viewer_url = href if href.endswith('?page=1') else href + '?page=1'
                            if viewer_url.startswith('/'):
                                viewer_url = 'https://shop.rema1000.dk' + viewer_url
                            if week_num not in week_to_url:
                                week_to_url[week_num] = viewer_url
                        except Exception:
                            continue
                    # Also collect all /avis/... links as fallback
                    all_links = re.findall(r'href\s*=\s*"(/avis/[A-Za-z0-9_-]+(?:\?page=1)?)"', html)
                    normalized_links = []
                    for href in all_links:
                        url = href if href.endswith('?page=1') else href + '?page=1'
                        if url.startswith('/'):
                            url = 'https://shop.rema1000.dk' + url
                        if url not in normalized_links:
                            normalized_links.append(url)
                    # Merge kommende candidates at end for fallback order
                    for href in kommende:
                        url = href if href.endswith('?page=1') else href + '?page=1'
                        if url.startswith('/'):
                            url = 'https://shop.rema1000.dk' + url
                        if url not in normalized_links:
                            normalized_links.append(url)
                    return week_to_url, normalized_links
                except Exception:
                    return {}, []

            week_to_url, rema_all_links = scrape_rema_weeks()
            # Remove any existing REMA items to avoid duplicates
            newsletters_list = [it for it in newsletters_list if 'rema' not in str(it.get('title','')).lower() and 'shop.rema1000.dk' not in str(it.get('viewer_url','')).lower()]

            # Determine current and next ISO week numbers
            today = datetime.now()
            current_iso_week = today.isocalendar()[1]
            next_iso_week = (today + timedelta(days=7)).isocalendar()[1]

            # Strategy: Always show both links when available
            # 1. Find current week link (or next week if current missing)
            # 2. Find the other available link for upcoming
            active_week = None
            active_url = None
            other_week = None
            other_url = None

            # Determine active (current week preferred, next week if current missing)
            if current_iso_week in week_to_url:
                active_week = current_iso_week
                active_url = week_to_url[current_iso_week]
            elif next_iso_week in week_to_url:
                active_week = next_iso_week
                active_url = week_to_url[next_iso_week]
            elif week_to_url:
                # Fallback to any available week
                active_week = max(week_to_url.keys())
                active_url = week_to_url[active_week]
            elif rema_all_links:
                # Final fallback: use the first available /avis/ link (e.g., MFX0bDHL)
                active_week = current_iso_week
                active_url = rema_all_links[0]

            # Final hard fallback to known active URL if nothing resolved
            if (active_week is None or not active_url):
                known_active_url = 'https://shop.rema1000.dk/avis/MFX0bDHL?page=1'
                active_week = current_iso_week
                active_url = known_active_url

            # Find the other link (different from active)
            if week_to_url:
                for week_num, url in week_to_url.items():
                    if week_num != active_week:
                        other_week = week_num
                        other_url = url
                        break

            # If no other week found, try from all_links
            if not other_url and rema_all_links:
                for link in rema_all_links:
                    if link != active_url:
                        other_week = next_iso_week  # Use next week number for display
                        other_url = link
                        break

            # Add active card
            if active_week is not None and active_url:
                rema_dynamic.append({
                    'title': f'REMA 1000 Uge {active_week}',
                    'date': '',
                    'url': REMA_OVERVIEW_URL,
                    'pdf': '',
                    'image': '/static/images/Rema1000-logo.png',
                    'viewer': 'link',
                    'viewer_url': active_url,
                    'rema_tag': 'current'
                })

            # Add other card (upcoming)
            if other_week is not None and other_url:
                rema_dynamic.append({
                    'title': f'REMA 1000 Uge {other_week}',
                    'date': '',
                    'url': REMA_OVERVIEW_URL,
                    'pdf': '',
                    'image': '/static/images/Rema1000-logo.png',
                    'viewer': 'link',
                    'viewer_url': other_url,
                    'rema_tag': 'next'
                })
            else:
                # Placeholder for upcoming if no second link
                rema_dynamic.append({
                    'title': f'REMA 1000 Uge {next_iso_week}',
                    'date': '',
                    'url': REMA_OVERVIEW_URL,
                    'pdf': '',
                    'image': '/static/images/Rema1000-logo.png',
                    'viewer': '',
                    'viewer_url': '',
                    'rema_tag': 'next'
                })
        except Exception:
            rema_dynamic = []

        # Split Bilka (Food) newsletters into current vs upcoming week and others
        bilka_current = []
        bilka_upcoming = []
        others = newsletters_list
        # Classify dynamic bilka items; choose active per availability rule
        try:
            has_current = any(it.get('bilka_tag') == 'current' for it in bilka_dynamic)
            if has_current:
                bilka_current = [it for it in bilka_dynamic if it.get('bilka_tag') == 'current']
                bilka_upcoming = [it for it in bilka_dynamic if it.get('bilka_tag') == 'next']
            else:
                # Current disappeared → promote next to current
                bilka_current = [it for it in bilka_dynamic if it.get('bilka_tag') == 'next']
                bilka_upcoming = []
        except Exception:
            pass

        # Classify REMA (do not mix into others so sections are clear)
        rema_current = []
        rema_upcoming = []
        try:
            if 'rema_dynamic' in locals() and rema_dynamic:
                rema_current = [it for it in rema_dynamic if it.get('rema_tag') == 'current']
                rema_upcoming = [it for it in rema_dynamic if it.get('rema_tag') == 'next']
        except Exception:
            rema_current = []
            rema_upcoming = []

        # Sort others by date if available
        try:
            others.sort(key=lambda x: x.get('date', ''), reverse=True)
        except Exception:
            pass

        return render_template(
            'newsletters.html',
            newsletters=others,
            bilka_current=bilka_current,
            bilka_upcoming=bilka_upcoming,
            rema_current=rema_current,
            rema_upcoming=rema_upcoming
        )
    except Exception as e:
        print(f"Error loading newsletters: {str(e)}")
        return render_template('newsletters.html', newsletters=[], bilka_current=[], bilka_upcoming=[], rema_current=[], rema_upcoming=[])

@app.route('/')
@app.route('/index.html')
def home():
    # Get product data (either from cache or fresh)
    product_data = get_product_data()
    
    # Create a dictionary to store products by category
    products_by_category = {
        'Ugens Tilbud': [],
        'Kolonial': [],
        'Drikkevarer': [],
        'Mejeri': [],
        'Baby og småbørn': [],
        'Personlig pleje': [],
        'Husholdning': [],
        'Frugt & grønt': [],
        'Nemt & hurtigt': [],
        'Køl': [],
        'Frost': [],
        'Ost m.v.': [],
        'Brød & Bavinchi': [],
        'Kød, fisk & fjerkræ': [],
        'Kiosk': [],
        'Slik': []
    }
    
    # Populate sale products først
    for product in product_data:
        if product.get('/product/sale_price') or product.get('/product/is_any_sale'):
            try:
                # Get the sale end date
                sale_dates = str(product.get('/product/sale_price_effective_date', '')).split('/')
                sale_end_date = None
                if len(sale_dates) > 1:
                    try:
                        # Parse the date and reformat to dd/mm
                        date_str = sale_dates[1].strip()
                        date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S%z')
                        sale_end_date = date_obj.strftime('%d/%m')
                    except ValueError:
                        sale_end_date = None
                
                sale_price = product.get('/product/sale_price')
                product_dict = {
                    'id': str(product.get('/product/id', '')),
                    'name': str(product.get('/product/title', 'Ukendt vare')),
                    'price': float(product.get('/product/price', 0)),
                    'sale_price': float(sale_price) if sale_price is not None else None,
                    'description': str(product.get('/product/description', '')),
                    'category': str(product.get('/product/product_type') or 'Andre varer'),
                    'brand': str(product.get('/product/brand', '')),
                    'image_url': str(product.get('/product/imageLink', '')),
                    'is_sale': True if sale_price is not None else False,
                    'is_any_sale': product.get('/product/is_any_sale', False),
                    'sale_end_date': sale_end_date,
                    'store': str(product.get('/product/store', 'Rema 1000')),
                    'unit_measure': str(product.get('/product/unit_pricing_measure', '') or ''),
                    'price_per_kg': (product.get('/product/price_per_kg') if product.get('/product/price_per_kg') is not None else None),
                    'bilka_match': product.get('/product/bilka_match'),
                    'mk_match': product.get('/product/mk_match'),
                    'meny_match': product.get('/product/meny_match'),
                    'spar_match': product.get('/product/spar_match'),
                    'cheaper_at':  product.get('/product/cheaper_at'),
                    'cheapest_at': product.get('/product/cheapest_at'),
                }
                products_by_category['Ugens Tilbud'].append(product_dict)
            except (ValueError, TypeError, KeyError):
                continue

    # Populate regular categories (Bug 1: skip sale products — already in Ugens Tilbud)
    for product in product_data:
        if product.get('/product/sale_price') or product.get('/product/is_any_sale'):  # Bug 1: already added to Ugens Tilbud above
            continue
        category = str(product['/product/product_type'])
        if category in products_by_category:
            try:
                price = float(product['/product/price'])
                if price <= 0:  # Bug 3: skip zero-price products
                    continue
                product_dict = {
                    'id': str(product['/product/id']),
                    'name': str(product['/product/title']),
                    'price': price,
                    'description': str(product['/product/description']),
                    'category': str(product.get('/product/product_type') or 'Andre varer'),
                    'brand': str(product['/product/brand']),
                    'image_url': str(product['/product/imageLink']),
                    'is_sale': False,
                    'store': str(product.get('/product/store', 'Rema 1000')),
                    'unit_measure': str(product.get('/product/unit_pricing_measure', '') or ''),
                    'price_per_kg': (product.get('/product/price_per_kg') if product.get('/product/price_per_kg') is not None else None),
                    'bilka_match': product.get('/product/bilka_match'),
                    'mk_match': product.get('/product/mk_match'),
                    'meny_match': product.get('/product/meny_match'),
                    'spar_match': product.get('/product/spar_match'),
                    'cheaper_at':  product.get('/product/cheaper_at'),
                    'cheapest_at': product.get('/product/cheapest_at'),
                }
                products_by_category[category].append(product_dict)
            except (ValueError, TypeError):
                continue

    # Begræns til 3 kategorier
    trimmed_categories = {k: v[:6] for k, v in products_by_category.items() if v}

    # Create a mapping for template filenames
    template_mapping = {
        'Ugens Tilbud': 'sale.html',
        'Kolonial': 'Kolonial.html',
        'Drikkevarer': 'Drikkevarer.html',

    }

    return render_template(
        'index.html',
        categories=trimmed_categories,
        template_mapping=template_mapping,
        debug=True  # Add debug flag
    )

@app.route('/sale.html')
def sale():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 60  # 6x10 layout
        
        product_data = get_product_data()
        sale_products = []
        
        for product in product_data:
            if product.get('/product/sale_price') or product.get('/product/is_any_sale'):
                try:
                    # Get the sale end date
                    sale_dates = str(product.get('/product/sale_price_effective_date', '')).split('/')
                    sale_end_date = None
                    if len(sale_dates) > 1:
                        try:
                            # Parse the date and reformat to dd/mm
                            date_str = sale_dates[1].strip()
                            date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S%z')
                            sale_end_date = date_obj.strftime('%d/%m')
                        except ValueError:
                            sale_end_date = None
                    
                    sale_price = product.get('/product/sale_price')
                    product_dict = {
                        'id': str(product.get('/product/id', '')),
                        'name': str(product.get('/product/title', 'Ukendt vare')),
                        'price': float(product.get('/product/price', 0)),
                        'sale_price': float(sale_price) if sale_price is not None else None,
                        'description': str(product.get('/product/description', '')),
                        'category': str(product.get('/product/product_type') or 'Andre varer'),
                        'brand': str(product.get('/product/brand', '')),
                        'image_url': str(product.get('/product/imageLink', '')),
                        'is_sale': True if sale_price is not None else False,
                        'is_any_sale': product.get('/product/is_any_sale', False),
                        'sale_end_date': sale_end_date,
                        'unit_measure': str(product.get('/product/unit_pricing_measure', '') or ''),
                        'price_per_kg': (product.get('/product/price_per_kg') if product.get('/product/price_per_kg') is not None else None),
                        'bilka_match': product.get('/product/bilka_match'),
                        'mk_match': product.get('/product/mk_match'),
                        'meny_match': product.get('/product/meny_match'),
                        'spar_match': product.get('/product/spar_match'),
                        'cheaper_at':  product.get('/product/cheaper_at'),
                        'cheapest_at': product.get('/product/cheapest_at'),
                    }
                    sale_products.append(product_dict)
                except (ValueError, TypeError, KeyError) as e:
                    print(f"Error converting prices for sale product {product['/product/id']} - {product['/product/title']}: {str(e)}")
                    continue
        
        # Calculate pagination
        total_products = len(sale_products)
        total_pages = (total_products + per_page - 1) // per_page
        page = min(max(page, 1), total_pages)  # Ensure page is within valid range
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_products = sale_products[start_idx:end_idx]
        
        return render_template('category.html', 
                            category_name='Ugens Tilbud',
                            products=paginated_products,
                            current_page=page,
                            total_pages=total_pages)
                            
    except Exception as e:
        print(f"Error loading sale page: {str(e)}")
        return "Page not found", 404

@app.route('/search')
def search():
    """API endpoint for search suggestions as user types"""
    query = request.args.get('q', '').lower().strip()
    
    if not query:
        return jsonify(html='<div class="no-results">Indtast søgeord</div>')
    
    try:
        product_data = get_product_data()
        
        all_products = []
        match_count = 0
        
        for product in product_data:
            try:
                if not product.get('/product/title') or not product.get('/product/id'):
                    continue
                    
                product_dict = {
                    'id': str(product['/product/id']),
                    'name': str(product['/product/title']),
                    'price': float(product['/product/price']),
                    'description': str(product['/product/description']),
                    'category': str(product.get('/product/product_type') or 'Andre varer'),
                    'brand': str(product['/product/brand']),
                    'image_url': str(product['/product/imageLink']),
                    'is_sale': False,
                    'unit_measure': str(product.get('/product/unit_pricing_measure', '') or ''),
                    'price_per_kg': (product.get('/product/price_per_kg') if product.get('/product/price_per_kg') is not None else None),
                    'store': str(product.get('/product/store', 'Rema 1000')),
                    'bilka_match': product.get('/product/bilka_match'),
                    'mk_match': product.get('/product/mk_match'),
                    'meny_match': product.get('/product/meny_match'),
                    'spar_match': product.get('/product/spar_match'),
                    'cheaper_at':  product.get('/product/cheaper_at'),
                    'cheapest_at': product.get('/product/cheapest_at'),
                }
                
                is_any_sale = product.get('/product/is_any_sale', False)
                product_dict['is_any_sale'] = is_any_sale

                sale_price = product.get('/product/sale_price')
                if sale_price:
                    product_dict['is_sale'] = True
                    product_dict['sale_price'] = float(sale_price)
                    # Add sale end date processing
                    sale_dates = str(product.get('/product/sale_price_effective_date', '')).split('/')
                    sale_end_date = None
                    if len(sale_dates) > 1:
                        try:
                            date_str = sale_dates[1].strip()
                            date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S%z')
                            sale_end_date = date_obj.strftime('%d/%m')
                        except ValueError:
                            sale_end_date = None
                    product_dict['sale_end_date'] = sale_end_date
                else:
                    product_dict['is_sale'] = False
                    product_dict['sale_end_date'] = None
                
                # Search in product fields
                product_name = product_dict['name'].lower()
                product_brand = product_dict['brand'].lower()
                product_description = product_dict['description'].lower()
                
                # Split query into words and check if any word matches
                search_terms = query.split()
                for term in search_terms:
                    if term in product_name or term in product_brand or term in product_description:
                        all_products.append(product_dict)
                        match_count += 1
                        break
                    
            except (ValueError, TypeError, KeyError) as e:
                print(f"Error processing product: {str(e)}")
                continue
        
        if len(all_products) == 0:
            return jsonify(html='<div class="no-results">Ingen resultater fundet</div>')
            
        # Generate HTML for matched products
        products_html = render_template_string('''
            {% for product in products %}
            {%- set store_lower = (product.store or 'rema').lower() -%}
            {%- set badge_class = 'bilka' if 'bilka' in store_lower 
              else ('mk' if ('min' in store_lower or 'kobmand' in store_lower) 
              else ('meny' if 'meny' in store_lower 
              else ('spar' if 'spar' in store_lower else 'rema'))) -%}
            {%- set badge_label = 'Bilka' if 'bilka' in store_lower 
              else ('Min Købmand' if ('min' in store_lower or 'kobmand' in store_lower) 
              else ('Meny' if 'meny' in store_lower 
              else ('Spar' if 'spar' in store_lower else 'Rema 1000'))) -%}
            <div id="product{{ product.id }}" class="product"
                 onclick="openOverlay('product{{ product.id }}')"
                 data-cheapest-at="{{ product.cheapest_at or '' }}"
                 data-bilka-price="{{ product.bilka_match.price if product.bilka_match else '' }}"
                 data-bilka-name="{{ product.bilka_match.name if product.bilka_match else '' }}"
                 data-bilka-kg-price="{% if product.bilka_match and product.bilka_match.kg_price is not none %}{{ '%.2f'|format(product.bilka_match.kg_price) }}{% endif %}"
                 data-mk-price="{{ product.mk_match.price if product.mk_match else '' }}"
                 data-mk-name="{{ product.mk_match.name if product.mk_match else '' }}"
                 data-mk-kg-price="{% if product.mk_match and product.mk_match.kg_price is not none %}{{ '%.2f'|format(product.mk_match.kg_price) }}{% endif %}"
                 data-meny-price="{{ product.meny_match.price if product.meny_match else '' }}"
                 data-meny-name="{{ product.meny_match.name if product.meny_match else '' }}"
                 data-meny-kg-price="{% if product.meny_match and product.meny_match.kg_price is not none %}{{ '%.2f'|format(product.meny_match.kg_price) }}{% endif %}"
                 data-spar-price="{{ product.spar_match.price if product.spar_match else '' }}"
                 data-spar-name="{{ product.spar_match.name if product.spar_match else '' }}"
                 data-spar-kg-price="{% if product.spar_match and product.spar_match.kg_price is not none %}{{ '%.2f'|format(product.spar_match.kg_price) }}{% endif %}"
                 data-bilka-is-sale="{{ 'true' if product.bilka_match and product.bilka_match.is_sale else 'false' }}"
                 data-mk-is-sale="{{ 'true' if product.mk_match and product.mk_match.is_sale else 'false' }}"
                 data-meny-is-sale="{{ 'true' if product.meny_match and product.meny_match.is_sale else 'false' }}"
                 data-spar-is-sale="{{ 'true' if product.spar_match and product.spar_match.is_sale else 'false' }}"
                 data-rema-weight="{{ product.unit_measure if product.unit_measure else '' }}"
                 data-rema-kg-price="{% if product.price_per_kg is not none %}{{ '%.2f'|format(product.price_per_kg) }}{% endif %}"
                 data-store="{{ product.store or 'Rema 1000' }}"
                 data-has-match="{{ 'true' if (product.bilka_match or product.mk_match or product.meny_match or product.spar_match) else 'false' }}"
                 data-has-match-bil="{{ 'true' if product.bilka_match else 'false' }}"
                 data-has-match-mk="{{ 'true' if product.mk_match else 'false' }}"
                 data-has-match-meny="{{ 'true' if product.meny_match else 'false' }}"
                 data-has-match-spar="{{ 'true' if product.spar_match else 'false' }}"
                 data-category="{{ product.category|default('Andre varer') }}">
              <div class="product-image-container">
                {% if product.is_sale or product.is_any_sale %}
                <span class="sale-badge"><svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M21.41 11.58l-9-9A2 2 0 0011 2H4a2 2 0 00-2 2v7a2 2 0 00.59 1.42l9 9A2 2 0 0013 22a2 2 0 001.41-.59l7-7A2 2 0 0022 13a2 2 0 00-.59-1.42zM6.5 8A1.5 1.5 0 115 6.5 1.5 1.5 0 016.5 8z"/></svg> Tilbud</span>
                {% endif %}
                <span class="store-badge {{ badge_class }}">{{ badge_label }}</span>
                <img src="{{ product.image_url }}" alt="{{ product.name }}" class="product-image" loading="lazy">
              </div>
              <div class="product-content">
                <div class="product-brand">{{ product.brand }}</div>
                <h3>{{ product.name }}</h3>
                {% if product.description %}<div class="product-weight">{{ product.description }}</div>{% endif %}
                {% if not product.bilka_match and not product.mk_match and not product.meny_match and not product.spar_match %}
                <div class="compare-badge only">Kun hos {{ product.store or "Rema 1000" }}</div>
                {% endif %}
                {% if product.is_sale and product.sale_end_date %}<p class="sale-end-date" style="display:none;">Tilbud frem til: {{ product.sale_end_date }}</p>{% endif %}
              </div>
              <div class="product-footer">
                <div class="product-price">
                  {% if product.is_sale %}
                  <div class="price-original price original">{{ "%.2f"|format(product.price) }} kr</div>
                  <div class="price-sale price sale">{{ "%.2f"|format(product.sale_price) }} kr</div>
                  {% else %}
                  <div class="price-main price">{{ "%.2f"|format(product.price) }} kr</div>
                  {% endif %}
                </div>
                <div class="corner-box" onclick="event.stopPropagation(); addToCart(event, 'product{{ product.id }}')">
                  &#128722;
                </div>
              </div>
              <span class="brand" style="display:none;">{{ product.brand }}</span>
              <span class="product-description" style="display:none;">{{ product.description }}</span>
            </div>
            {% endfor %}
        ''', products=all_products)
        
        return jsonify(html=products_html)
        
    except Exception as e:
        print(f"Error in search route: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify(html='<div class="error">Der opstod en fejl under søgningen</div>')

@app.route('/search/results')
def search_page():
    """Full page search results"""
    try:
        page = request.args.get('page', 1, type=int)
        query = request.args.get('q', '').lower().strip()
        per_page = 60  # 6x10 layout
        
        if not query:
            return redirect(url_for('home'))
        
        product_data = get_product_data()
        all_products = []
        
        for product in product_data:
            try:
                if not product.get('/product/title') or not product.get('/product/id'):
                    continue
                    
                product_dict = {
                    'id': str(product['/product/id']),
                    'name': str(product['/product/title']),
                    'price': float(product['/product/price']),
                    'description': str(product['/product/description']),
                    'category': str(product.get('/product/product_type') or 'Andre varer'),
                    'brand': str(product['/product/brand']),
                    'image_url': str(product['/product/imageLink']),
                    'is_sale': False,
                    'unit_measure': str(product.get('/product/unit_pricing_measure', '') or ''),
                    'price_per_kg': (product.get('/product/price_per_kg') if product.get('/product/price_per_kg') is not None else None),
                    'bilka_match': product.get('/product/bilka_match'),
                    'mk_match': product.get('/product/mk_match'),
                    'meny_match': product.get('/product/meny_match'),
                    'spar_match': product.get('/product/spar_match'),
                    'cheaper_at':  product.get('/product/cheaper_at'),
                    'cheapest_at': product.get('/product/cheapest_at'),
                }
                
                is_any_sale = product.get('/product/is_any_sale', False)
                product_dict['is_any_sale'] = is_any_sale

                sale_price = product.get('/product/sale_price')
                if sale_price:
                    product_dict['is_sale'] = True
                    product_dict['sale_price'] = float(sale_price)
                    # Add sale end date processing
                    sale_dates = str(product.get('/product/sale_price_effective_date', '')).split('/')
                    sale_end_date = None
                    if len(sale_dates) > 1:
                        try:
                            date_str = sale_dates[1].strip()
                            date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S%z')
                            sale_end_date = date_obj.strftime('%d/%m')
                        except ValueError:
                            sale_end_date = None
                    product_dict['sale_end_date'] = sale_end_date
                else:
                    product_dict['is_sale'] = False
                    product_dict['sale_end_date'] = None
                
                # Search in product fields
                product_name = product_dict['name'].lower()
                product_brand = product_dict['brand'].lower()
                product_description = product_dict['description'].lower()
                
                # Split query into words and check if any word matches
                search_terms = query.split()
                for term in search_terms:
                    if term in product_name or term in product_brand or term in product_description:
                        all_products.append(product_dict)
                        break
                    
            except (ValueError, TypeError, KeyError) as e:
                print(f"Error processing product: {str(e)}")
                continue
        
        # Calculate pagination
        total_products = len(all_products)
        if total_products == 0:
            return render_template('search_results.html', 
                                query=query,
                                products=[],
                                total_products=0,
                                current_page=1,
                                total_pages=1)
            
        total_pages = (total_products + per_page - 1) // per_page
        page = min(max(page, 1), total_pages)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_products = all_products[start_idx:end_idx]

        return render_template('search_results.html',
                            query=query,
                            products=paginated_products,
                            total_products=total_products,
                            current_page=page,
                            total_pages=total_pages)
    
    except Exception as e:
        print(f"Error in search: {str(e)}")
        return render_template('search_results.html',
                            query=query,
                            products=[],
                            total_products=0,
                            current_page=1,
                            total_pages=1,
                            error="Der opstod en fejl under søgningen")

@app.route('/<category_name>.html')
def category(category_name):
    # Reverse mapping for filenames to category names
    category_mapping = {
        'Kolonial': 'Kolonial',
        'Drikkevarer': 'Drikkevarer',
        'Mejeri': 'Mejeri',
        'Frugt_og_groent': 'Frugt & grønt',
        'Nemt_og_hurtigt': 'Nemt & hurtigt',
        'Koel': 'Køl',
        'Frost': 'Frost',
        'Ost_mv': 'Ost m.v.',
        'Broed_og_Bavinchi': 'Brød & Bavinchi',
        'Koed_fisk_og_fjerkrae': 'Kød, fisk & fjerkræ',
        'Slik': 'Slik'
    }
    
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 60  # 6x10 layout
        
        # Get the actual category name from the filename
        actual_category = category_mapping.get(category_name.replace('.html', ''))
        if not actual_category:
            return "Category not found", 404
            
        # Get products for this category
        product_data = get_product_data()
        category_products = []
        
        print("\n=== Processing products for category:", actual_category, "===")
        
        for product in product_data:
            if str(product['/product/product_type']) == actual_category:
                try:
                    # Log raw sale price effective date
                    if product['/product/sale_price']:
                        print(f"\nProcessing sale product:")
                        print(f"Product ID: {product['/product/id']}")
                        print(f"Product Name: {product['/product/title']}")
                        print(f"Raw sale_price_effective_date: {product['/product/sale_price_effective_date']}")
                    
                    # Get the sale end date if it's a sale product
                    sale_end_date = None
                    if product['/product/sale_price']:
                        sale_dates = str(product['/product/sale_price_effective_date']).split('/')
                        
                        if len(sale_dates) > 1:
                            try:
                                # Parse the date and reformat to dd/mm
                                date_str = sale_dates[1].strip()
                                date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S%z')
                                sale_end_date = date_obj.strftime('%d/%m')
                            except ValueError:
                                sale_end_date = None

                    product_dict = {
                        'id': str(product['/product/id']),
                        'name': str(product['/product/title']),
                        'price': float(product['/product/price']),
                        'description': str(product['/product/description']),
                        'category': str(product.get('/product/product_type') or 'Andre varer'),
                        'brand': str(product['/product/brand']),
                        'image_url': str(product['/product/imageLink']),
                        'is_sale': False,
                        'sale_end_date': sale_end_date,
                        'store': str(product.get('/product/store', 'Rema 1000')),
                        'unit_measure': str(product.get('/product/unit_pricing_measure', '') or ''),
                        'price_per_kg': (product.get('/product/price_per_kg') if product.get('/product/price_per_kg') is not None else None),
                        'bilka_match': product.get('/product/bilka_match'),
                        'mk_match': product.get('/product/mk_match'),
                        'meny_match': product.get('/product/meny_match'),
                        'spar_match': product.get('/product/spar_match'),
                        'cheaper_at':  product.get('/product/cheaper_at'),
                        'cheapest_at': product.get('/product/cheapest_at'),
                    }

                    # Check if it's a sale product
                    if product['/product/sale_price']:
                        product_dict['is_sale'] = True
                        product_dict['sale_price'] = float(product['/product/sale_price'])
                    
                    category_products.append(product_dict)
                except (ValueError, TypeError) as e:
                    print(f"Error converting price for product {product['/product/id']} - {product['/product/title']}: {str(e)}")
                    continue

        # Calculate pagination
        total_products = len(category_products)
        total_pages = (total_products + per_page - 1) // per_page
        page = min(max(page, 1), total_pages)  # Ensure page is within valid range
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_products = category_products[start_idx:end_idx]
        
        return render_template('category.html', 
                            category_name=actual_category,
                            products=paginated_products,
                            current_page=page,
                            total_pages=total_pages)
                            
    except Exception as e:
        print(f"Error loading category page: {str(e)}")
        return "Page not found", 404

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

@app.route('/static/images/<path:filename>')
def serve_static_images(filename):
    return send_from_directory('static/images', filename)

@app.route('/product/<product_id>')
def get_product_info(product_id):
    """Get product information and print debug info"""
    try:
        product_data = get_product_data()
        
        # Find the product with the matching ID
        product = next((p for p in product_data if str(p['/product/id']) == str(product_id)), None)
        
        if product:
            # Print debug information
            print("\n=== Product Information Debug ===")
            print("Product ID:", product['/product/id'])
            print("Title:", product['/product/title'])
            print("Price:", product['/product/price'])
            print("Sale Price:", product['/product/sale_price'])
            print("Description:", product['/product/description'])
            print("Brand:", product['/product/brand'])
            print("Product Type:", product['/product/product_type'])
            print("Store:", product['/product/store'])
            print("Image Link:", product['/product/imageLink'])
            if product['/product/sale_price']:
                print("Sale Price Effective Date:", product['/product/sale_price_effective_date'])
            print("================================\n")
            
            return jsonify({
                'success': True,
                'product': {
                    'rema_price': product['/product/price'],
                    'bilka_price': product['/product/price']
                }
            })
        else:
            print(f"Product not found with ID: {product_id}")
            return jsonify(success=False, error="Product not found"), 404
            
    except Exception as e:
        print(f"Error getting product info: {str(e)}")
        return jsonify(success=False, error=str(e)), 500

@app.route('/api/products', methods=['GET'])
def get_separate_products():
    try:
        # Add debug logging
        print("\n=== /api/products endpoint called ===")
        
        if app.cached_products and app.last_cache_update:
            if datetime.now() - app.last_cache_update < timedelta(hours=1):
                print("Returning cached products:")
                print(f"Rema products: {len(app.cached_products['rema'])}")
                print(f"Bilka products: {len(app.cached_products['bilka'])}")
                return jsonify({
                    'success': True,
                    'rema_products': app.cached_products['rema'],
                    'bilka_products': app.cached_products['bilka']
                })
        
        print("Cache miss - fetching fresh data")
        rema = parse_rema_xml()
        bilka = parse_bilka_excel()
        
        print(f"Fresh data fetched:")
        print(f"Rema products: {len(rema)}")
        print(f"Bilka products: {len(bilka)}")
        
        app.cached_products = {
            'rema': rema,
            'bilka': bilka
        }
        app.last_cache_update = datetime.now()
        
        return jsonify({
            'success': True,
            'rema_products': rema,
            'bilka_products': bilka
        })
        
    except Exception as e:
        print(f"Error in /api/products endpoint: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

def parse_rema_xml():
    try:
        print("\n=== Starting data fetch and parse ===")
        
        # Initialize empty lists for both sources
        rema_products = []

        
        # 1. Fetch and parse XML data (Rema 1000)
        print("Fetching XML data from:", XML_URL)
        try:
            response = requests.get(XML_URL, timeout=10)
            response.raise_for_status()
            
            print(f"Response status: {response.status_code}")
            print(f"Response content type: {response.headers.get('content-type', 'unknown')}")
            
            # Parse XML to dict
            xml_dict = xmltodict.parse(response.text)
            
            if validate_xml_structure(xml_dict):
                print(f"XML structure validated successfully")
                
                for i, product in enumerate(xml_dict['products']['product']):
                    try:
                        # Extract price and clean it
                        price = format_price(product.get('price', '0 DKK'))
                        sale_price = format_price(product.get('sale_price', '')) or None
                        
                        product_dict = {
                            '/product/id': product.get('id', ''),
                            '/product/title': product.get('title', ''),
                            '/product/price': price,
                            '/product/sale_price': sale_price,
                            '/product/description': product.get('description', ''),
                            '/product/brand': product.get('brand', ''),
                            '/product/imageLink': product.get('imageLink', ''),
                            '/product/product_type': product.get('product_type', ''),
                            '/product/sale_price_effective_date': product.get('sale_price_effective_date', ''),
                            '/product/store': 'Rema 1000'  # Add store field
                        }
                        
                        rema_products.append(product_dict)
                        
                    except Exception as e:
                        print(f"Error processing Rema 1000 product {i}: {str(e)}")
                        print("Product data:", json.dumps(product, indent=2))
                        continue
                
                print(f"\nTotal Rema 1000 products parsed: {len(rema_products)}")
            else:
                print("XML validation failed")
                
        except Exception as e:
            print(f"Error fetching Rema 1000 data: {str(e)}")
            import traceback
            traceback.print_exc()

        return rema_products
    except Exception as e:
        print(f"Error parsing Rema XML: {str(e)}")
        return []

def parse_bilka_excel():
    try:
        bilka_products = []
        # Skip the first row (index 0) and use second row (index 1) as headers
        df = pd.read_excel('Products-bilka.xlsx', header=1)
        
        for i, row in df.iterrows():
            try:
                # Extract price and clean it - using correct column names with /product/ prefix
                raw_price = str(row['/product/price']) if '/product/price' in df.columns else '0'
                raw_sale_price = str(row['/product/sale_price']) if '/product/sale_price' in df.columns else ''
                raw_id = str(row['/product/id']) if '/product/id' in df.columns else '0'

                price = format_price(raw_price) or 0.0
                sale_price = format_price(raw_sale_price) or None
                # Add NaN check for sale_price
                if sale_price is not None and math.isnan(sale_price):
                    sale_price = None
                
                product_dict = {
                    '/product/id': str(row['/product/id']),
                    '/product/title': str(row['/product/title']),
                    '/product/price': price,
                    '/product/sale_price': sale_price,
                    '/product/description': str(row['/product/description']),
                    '/product/brand': str(row['/product/brand']),
                    '/product/imageLink': str(row['/product/imageLink']),
                    '/product/product_type': str(row['/product/product_type']),
                    '/product/sale_price_effective_date': str(row['/product/sale_price_effective_date']),
                    '/product/store': 'Bilka'
                }
                
                # Skip products with missing or invalid ID
                if not product_dict['/product/id'] or product_dict['/product/id'] == 'nan':
                    continue
                
                bilka_products.append(product_dict)
                
            except Exception as e:
                continue
                
        return bilka_products
    except Exception as e:
        return []

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
    
