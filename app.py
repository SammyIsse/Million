from flask import Flask, render_template, send_from_directory, jsonify, request, redirect, url_for, render_template_string
import requests
import re
import xmltodict
from datetime import datetime, timedelta
import os
import json
import pandas as pd
import math
import hashlib
import traceback
import random
from difflib import SequenceMatcher
import unicodedata
import sqlite3
import imagehash

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

# ---------------------------------------------------------------------------
# Store comparison data — generic loader
# ---------------------------------------------------------------------------

_STORE_CONFIGS = {
    'rema': {
        'file':       None,
        'label':      'Rema 1000',
        'logo':       '/static/images/Rema1000-logo.png',
    },
    'bilka': {
        'file':       'Bilka_produkter.xlsx',
        'name_col':   'Navn',
        'brand_col':  'Type',
        'weight_col': 'Vægt',
        'ean_col':    'EAN',
        'label':      'Bilka',
        'logo':       '/static/images/bilka-logo.png',
    },
    'mk': {
        'file':       'minkobmand_produkter.xlsx',
        'name_col':   'Navn',
        'brand_col':  'Producent',
        'weight_col': 'Netto Vægt',
        'ean_col':    'Varenummer',
        'label':      'Min Købmand',
        'logo':       '/static/images/Min_kobmand_logo.png',
    },
    'meny': {
        'file':       'Meny_produkter.xlsx',
        'name_col':   'Navn',
        'brand_col':  'Producent',
        'weight_col': 'Netto Vægt',
        'ean_col':    'Varenummer',
        'label':      'Meny',
        'logo':       '/static/images/meny-logo.png',
    },
    'spar': {
        'file':       'Spar_produkter.xlsx',
        'name_col':   'Navn',
        'brand_col':  'Producent',
        'weight_col': 'Netto Vægt',
        'ean_col':    'Varenummer',
        'label':      'Spar',
        'logo':       '/static/images/spar-logo.png',
    },
    'sb': {
        'file':       'SuperBrugsen_produkter.xlsx',
        'name_col':   'Navn',
        'brand_col':  'Producent',
        'weight_col': 'Netto Vægt',
        'ean_col':    'Varenummer',
        'label':      'SuperBrugsen',
        'logo':       '/static/images/superbrugsen-logo.png',
    },
    'brugsen': {
        'file':       'Brugsen_produkter.xlsx',
        'name_col':   'Navn',
        'brand_col':  'Producent',
        'weight_col': 'Netto Vægt',
        'ean_col':    'Varenummer',
        'label':      'Brugsen',
        'logo':       '/static/images/brugsen-logo.png',
    },
    'kvickly': {
        'file':       'Kvickly_produkter.xlsx',
        'name_col':   'Navn',
        'brand_col':  'Producent',
        'weight_col': 'Netto Vægt',
        'ean_col':    'Varenummer',
        'label':      'Kvickly',
        'logo':       '/static/images/kvickly-logo.png',
    },
    'discount365': {
        'file':       '365Discount_produkter.xlsx',
        'name_col':   'Navn',
        'brand_col':  'Producent',
        'weight_col': 'Netto Vægt',
        'ean_col':    'Varenummer',
        'label':      '365 Discount',
        'logo':       '/static/images/365discount-logo.png',
    },
}

# Rema is the XML data source — not "primary", just the feed format we parse
REMA_KEY       = 'rema'
EXCEL_STORE_KEYS = [k for k, v in _STORE_CONFIGS.items() if v.get('file')]

# Single unified cache: store_key -> (products_list, token_index_dict)
_store_caches: dict = {}


def load_store_comparison_data(store_key: str) -> tuple:
    """Generic loader: reads an Excel file and builds a token inverted index."""
    if store_key in _store_caches:
        return _store_caches[store_key]
    cfg = _STORE_CONFIGS[store_key]
    try:
        filepath = os.path.join(os.path.dirname(__file__), 'Xlsx filer', cfg['file'])
        df = pd.read_excel(filepath)
        products = []
        for _, row in df.iterrows():
            try:
                raw = row['Pris']
                price = float(str(raw).replace(',', '.').replace('kr', '').strip()) if isinstance(raw, str) else float(raw)
                if math.isnan(price) or price <= 0:
                    continue
                weight_str = str(row[cfg['weight_col']])
                weight_g = parse_weight_to_grams(weight_str)
                ppk = parse_kg_price(row.get('Kg-pris', ''))
                price = sanitize_price(price, ppk, weight_g)
                is_sale_raw = str(row.get('Tilbud', 'Nej')).lower()
                is_sale = is_sale_raw in ('ja', 'true', 'yes', '1')
                ean_raw = str(row.get(cfg['ean_col'], '')).strip()
                ean = ean_raw.split('.')[0].strip() if ean_raw not in ('nan', 'None', '') else ''
                p_hash_hex = str(row.get('Billede Hash', ''))
                try:
                    p_hash_int = int(p_hash_hex, 16) if p_hash_hex and p_hash_hex not in ('nan', 'None', '') else None
                except Exception:
                    p_hash_int = None
                    
                normal_price = None
                if 'Normalpris' in row and row['Normalpris'] not in ('nan', 'None', '', None):
                    try:
                        raw_np = row['Normalpris']
                        np = float(str(raw_np).replace(',', '.').replace('kr', '').strip()) if isinstance(raw_np, str) else float(raw_np)
                        if not math.isnan(np) and np > 0:
                            normal_price = np
                    except Exception:
                        pass

                multi_deal_raw = str(row.get('Multikøb', '')).strip()
                multi_deal = '' if multi_deal_raw in ('nan', 'None') else multi_deal_raw

                products.append({
                    'name':        str(row[cfg['name_col']]),
                    'brand':       str(row[cfg['brand_col']]),
                    'weight':      weight_str,
                    'kg_price':    ppk,
                    'price':       price,
                    'normal_price': normal_price,
                    'is_sale':     is_sale,
                    'multi_deal':  multi_deal,
                    '_norm_name':  normalize_name(str(row[cfg['name_col']])),
                    '_weight_g':   weight_g,
                    'image':       str(row.get('Billede URL', '')),
                    '_image_hash': p_hash_hex,
                    '_hash_int':   p_hash_int,
                    'ean':         ean,
                })
            except Exception as e:
                print(f"Skipping {cfg['label']} comparison row: {e}")
                continue
        token_idx = {}
        hash_list = []
        for i, p in enumerate(products):
            for token in p['_norm_name'].split():
                if len(token) >= 4:
                    token_idx.setdefault(token, set()).add(i)
            p_hash_int = p.get('_hash_int')
            if p_hash_int is not None:
                hash_list.append((i, p_hash_int))
        _store_caches[store_key] = (products, token_idx, hash_list)
        print(f"Loaded {len(products)} {cfg['label']} products, {len(token_idx)} index tokens")
        return products, token_idx, hash_list
    except Exception as e:
        print(f"Error loading {cfg['file']}: {e}")
        _store_caches[store_key] = ([], {}, [])
        return [], {}, []


def load_all_comparison_data() -> dict:
    """Returns {store_key: (products, token_idx)} for all Excel-based stores."""
    return {key: load_store_comparison_data(key) for key in EXCEL_STORE_KEYS}

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


def is_lactose_free(name: str, desc: str = '', brand: str = '') -> bool:
    """Return True if the product is explicitly marked as lactose-free."""
    text = f"{name} {desc} {brand}".lower()
    return 'laktosefri' in text or 'lactose free' in text or 'laktose fri' in text


def weights_compatible(w_a: float | None, w_b: float | None, tolerance: float = _WEIGHT_TOLERANCE_G) -> bool:
    """Return True when both weights are known and within *tolerance* of each other,
    OR when either weight is unknown (we cannot rule out a match)."""
    if w_a is None or w_b is None:
        return True  # unknown weight → do not discard the candidate
    return abs(w_a - w_b) <= tolerance
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
    if b.startswith('coop'):
        return True

    if t.startswith('salling ') or t.startswith('rema 1000 ') or t.startswith('rema ') or t.startswith('first price ') or t.startswith('fp ') or t.startswith('coop '):
        return True
        
    return False


def _find_generic_match(rema_title, rema_description, products, token_idx, hash_list, rema_brand='', rema_weight_g=None, threshold=0.60, rema_image_hash='', rema_price=0.0, rema_ean=''):
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
    # 1. EAN Match: Varenummer match trumfer alt og returneres straks
    if rema_ean and rema_ean not in ('', 'nan', 'None'):
        for p in products:
            if p.get('ean') == rema_ean:
                return p

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

    # Fuzzy Image Match: Inkludér også produkter med meget lignende billeder som kandidater
    # Det hjælper fx. når navne er forkortede (hakket oksekød vs hk. oksekød)
    if r_hash_int is not None:
        for i, p_hash_int in hash_list:
            if i not in candidate_indices:
                if bin(r_hash_int ^ p_hash_int).count('1') <= 12:
                    candidate_indices.add(i)

    if not candidate_indices:
        return None

    best, best_score = None, 0.0
    rema_is_org = is_organic(rema_title, rema_description, rema_brand)
    rema_is_lf  = is_lactose_free(rema_title, rema_description, rema_brand)

    for i in candidate_indices:
        p = products[i]

        dist = None
        if r_hash_int is not None:
            p_hash_int = p.get('_hash_int')
            if p_hash_int is not None:
                dist = bin(r_hash_int ^ p_hash_int).count('1')

        # Gate: Organic matching
        if rema_is_org != is_organic(p.get('name', ''), p.get('description', ''), p.get('brand', '')):
            continue

        # Gate: Lactose-free matching
        if rema_is_lf != is_lactose_free(p.get('name', ''), p.get('description', ''), p.get('brand', '')):
            continue

        # Gate A: Brand-pairing
        p_is_pl = is_private_label(p.get('brand', ''), p.get('name', ''))
        if base_is_pl != p_is_pl:
            continue

        # Gate B: Weight
        if not weights_compatible(rema_weight_g, p.get('_weight_g')):
            continue

        # Gate C: Price sanity
        if rema_price and rema_price > 0:
            try:
                if float(p.get('price', 0)) > 5.0 * float(rema_price):
                    continue
            except (TypeError, ValueError):
                pass

        # 1. Name similarity
        name_score = fuzzy_score(rema_title_norm, p['_norm_name']) if rema_title_norm else 0.0

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
        brand_sim   = 1.0 if (base_is_pl and p_is_pl) else brand_similarity(norm_rema_brand, p.get('brand', ''))
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
_BLOCKED_NAME_FRAGMENTS = {
    'indlæg',
    'batteri',
    'shampoo',
    'balsam',
    'creme',
    'lotion',
    'bleer',
    'bleposer',
    'vaskeserviet',
    'vådserviet',
    'skumvaskeklud',
    'sutteflaske',
    'hundemad',
    'kattefoder',
    'kattemad',
    'hundesnack',
    'kattegrus',
    'tandpasta',
    'tandbørste',
    'håndsæbe',
    'shower gel',
    'deodorant',
    'bind',
    'tampon',
    'opvaskemiddel',
    'vaskemiddel',
    'skyllemiddel',
    # Tobak og nikotinprodukter
    'tobak',
    'cigaret',
    'cigarillo',
    'snus',
    'nikotin',
    'tændstik',
    'lighter',
    'fyrstikker',
    # Planter og blomster
    'plante',
    'planter',
    'potte',
    'potteskjuler',
    'blomst',
    'blomster',
    'buket',
    'roser',
    'tulipaner',
    'orkidé',
    'krysantemum',
    'jord',
    'gødning',
}

# Images that are store logos or known placeholders — products using these are excluded
_PLACEHOLDER_IMGS = {
    '/static/images/bilka-logo.png',
    '/static/images/Min_kobmand_logo.png',
    '/static/images/meny-logo.png',
    '/static/images/spar-logo.png',
    '/static/images/Rema1000-logo.png',
    'https://rema-product-images.digital.rema1000.dk/521365/1-large-bJ9YdpX0qL.webp',
}

# Standard categories used across the site
CAT_MEJERI = 'Mejeri'
CAT_KOED_FISK = 'Kød & Fisk'
CAT_FRUGT_GROENT = 'Frugt & Grønt'
CAT_BROED_KAGER = 'Brød & Kager'
CAT_FROST = 'Frost'
CAT_KOLONIAL = 'Kolonial'
CAT_DRIKKEVARER = 'Drikkevarer'
CAT_KIOSK = 'Kiosk'
CAT_SLIK = 'Slik'
CAT_ANDET = 'Andre varer'

def unify_category(raw_cat, product_name=''):
    """Maps any store category or product name to a standard website category."""
    raw = str(raw_cat or '').lower().strip()
    name = str(product_name or '').lower().strip()

    # Special overrides
    if 'prince' in name:
        return CAT_BROED_KAGER

    # Kiosk-kategorien fra Rema indeholder både drikkevarer og tobak.
    # Tobak fanges allerede af _BLOCKED_NAME_FRAGMENTS inden dette punkt.
    # Drikkevarer omdirigeres til CAT_DRIKKEVARER.
    if 'kiosk' in raw and name:
        _kiosk_drink_kws = (
            'cola', 'sodavand', 'juice', 'energidrik', 'energy drink',
            'øl', 'vin', 'cider', 'vand', 'saft', 'iste', 'ice tea',
            'sportsdrik', 'kombucha', 'drik', 'lemonade', 'shots',
            'smoothie', 'frugtdrik', 'breezer', 'kokosvand',
        )
        if any(kw in name for kw in _kiosk_drink_kws):
            return CAT_DRIKKEVARER
    
    # 1. Map known store category strings
    mapping = {
        'mejeri': CAT_MEJERI,
        'mejeriprodukter & kølvarer': CAT_MEJERI,
        'pålæg og kølede middagsretter': CAT_MEJERI,
        'køl': CAT_MEJERI,
        'ost': CAT_MEJERI,
        'ost m.v.': CAT_MEJERI,
        
        'kød': CAT_KOED_FISK,
        'fisk og skaldyr': CAT_KOED_FISK,
        'kød, fisk & fjerkræ': CAT_KOED_FISK,
        'kød fisk fjerkræ': CAT_KOED_FISK,
        
        'frugt & grønt': CAT_FRUGT_GROENT,
        'frugt og grønt': CAT_FRUGT_GROENT,
        
        'brød & kager': CAT_BROED_KAGER,
        'brød og kager': CAT_BROED_KAGER,
        'brød & bavinchi': CAT_BROED_KAGER,
        
        'frost': CAT_FROST,
        
        'kolonial': CAT_KOLONIAL,
        'kolonialvarer': CAT_KOLONIAL,
        
        'drikkevarer': CAT_DRIKKEVARER,
        'vin og spiritus': CAT_DRIKKEVARER,
        
        'personlig pleje': None,
        'pleje': None,
        'husholdning': None,
        'rengøring': None,
        
        'kiosk': CAT_KIOSK,
        'kiosk - slik og snack - chips og snacks': CAT_KIOSK,
        
        'slik': CAT_SLIK,
        'slik & snacks': CAT_SLIK,
        'slik og snacks': CAT_SLIK,
        'kiosk - slik og snack - chokolade': CAT_SLIK,
        'kiosk - slik og snack - slik': CAT_SLIK,
    }
    
    if raw in mapping:
        return mapping[raw]
        
    # 2. Fallback to keyword rules in name
    for category, keywords in _BILKA_CATEGORY_RULES:
        if any(kw in name for kw in keywords):
            # Map keyword-rule category to standard name if needed
            internal_map = {
                'Kød, fisk & fjerkræ': CAT_KOED_FISK,
                'Frugt & grønt': CAT_FRUGT_GROENT,
                'Brød & Kager': CAT_BROED_KAGER,
                'Slik': CAT_SLIK,
                'Drikkevarer': CAT_DRIKKEVARER,
                'Frost': CAT_FROST,
                'Kolonial': CAT_KOLONIAL,
            }
            return internal_map.get(category, category)
            
    return CAT_KOLONIAL if raw else CAT_ANDET

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
    ('Brød & Kager',       ('rugbrød', 'toastbrød', 'sandwichbrød', 'burgerboller',
                            'hotdogbrød', 'pølsebrød', 'baguette', 'pitabrød',
                            'naanbrød', 'knækbrød', 'digestive kiks', 'mariekiks',
                            'havrekiks', 'kiks m.', 'cookies m.', 'kiks',
                            'prince', 'fuldkornsboller', 'solsikkeboller', 'rugboller',
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
    ('Mejeri',             ('mælk', 'smør', 'piskefløde', 'skyr', 'yoghurt',
                            'kefir', 'fraiche', 'creme fraiche', 'kærnemælk', 'ymer',
                            'bagegær', 'æg', 'havredrik', 'sojadrik', 'mandeldrik',
                            'risdrik', 'oatly', 'flydende til madlavning',
                            'stegemargarine', 'plantemargarine', 'smørbar',
                            'danbo', 'havarti', 'cheddar', 'mozzarella', 'brie',
                            'camembert', 'feta', 'gorgonzola', 'emmentaler', 'gouda',
                            'ricotta', 'mascarpone', 'burrata', 'parmesan', 'parmigiano',
                            'grana padano', 'pecorino', 'manchego', 'jarlsberg',
                            'samsø ost', 'danablu', 'blåskimmelost', 'rygeost',
                            'smøreost', 'flødeost', 'ostehaps', 'ostetern',
                            'salatost', 'hytteost', 'halloumi', 'gruyere',
                            'comté', 'port salut', 'præst', 'rødkitost')),
    ('Frugt & Grønt',      ('agurk', 'bananer', 'banan', 'peberfrugt', 'tomat',
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
                            'chilisauce', 'teriyaki',
                            'boller i karry', 'lasagne', 'spaghetti bolognese',
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
]


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

            display.append({
                '/product/id':                        pid,
                '/product/title':                     p['name'],
                '/product/price':                     display_price,
                '/product/sale_price':                sale_price,
                '/product/description':               p.get('weight', ''),
                '/product/brand':                     p.get('brand', ''),
                '/product/imageLink':                 img,
                '/product/product_type':              unify_category(p.get('Kategori'), p['name']),
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
    # print(f"Built {len(display)} {cfg['label']} display products")
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
            
            xml_text = None
            for attempt in range(3):
                try:
                    response = requests.get(
                        XML_URL,
                        timeout=(10, 120),  # (connect, read) — XML-filen er stor
                        headers=DEFAULT_HTTP_HEADERS,
                        stream=True,
                    )
                    response.raise_for_status()
                    xml_text = response.content.decode(response.encoding or 'utf-8', errors='replace')
                    print(f"Response status: {response.status_code}")
                    print(f"Response content type: {response.headers.get('content-type', 'unknown')}")
                    break
                except requests.exceptions.Timeout:
                    print(f"  Timeout på forsøg {attempt + 1}/3 — prøver igen...")
                except requests.exceptions.RequestException as e:
                    print(f"  Netværksfejl på forsøg {attempt + 1}/3: {e}")
            if xml_text is None:
                raise RuntimeError("Kunne ikke hente Rema XML efter 3 forsøg")

            # Parse XML to dict
            xml_dict = xmltodict.parse(xml_text)
            
            if validate_xml_structure(xml_dict):
                print(f"XML structure validated successfully")
                
                for i, product in enumerate(xml_dict['products']['product']):
                    try:
                        # Extract price and clean it
                        price = format_price(product.get('price', '0 DKK'))
                        sale_price = format_price(product.get('sale_price', '')) or None

                        # Bug 3: Skip products with price 0
                        if price <= 0:
                            continue

                        # Map Rema product_type to internal category
                        raw_type = product.get('product_type', '')
                        mapped_type = unify_category(raw_type, product.get('title', ''))

                        if mapped_type is None:
                            continue

                        # Filter by blocked name fragments
                        title_lower = product.get('title', '').lower()
                        if any(frag in title_lower for frag in _BLOCKED_NAME_FRAGMENTS):
                            continue

                        unit_measure = product.get('unit_pricing_measure', '')
                        weight_g = parse_weight_to_grams(unit_measure)
                        
                        # Calculate price per kg for Rema products
                        price_per_kg = None
                        if weight_g and weight_g > 0:
                            # Use sale_price if available, otherwise price
                            effective_price = sale_price if sale_price is not None else price
                            price_per_kg = (effective_price / (weight_g / 1000.0))

                        product_dict = {
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
                            '/product/price_per_kg': price_per_kg,
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
        
        # 3. Annotate each Rema product with comparison data from all secondary stores
        print("\nAnnotating Rema products with comparison data")
        store_data   = load_all_comparison_data()
        # store_data = {'bilka': (products, token_idx), 'mk': (...), ...}

        final_products = []
        matched_ids  = {key: set() for key in EXCEL_STORE_KEYS}
        match_counts = {key: 0     for key in EXCEL_STORE_KEYS}

        for product in rema_products:
            rema_effective = (
                float(product['/product/sale_price'])
                if product['/product/sale_price'] is not None
                and not math.isnan(float(product['/product/sale_price']))
                else float(product['/product/price'])
            )

            # Match against every secondary store
            matches = {}
            for key in EXCEL_STORE_KEYS:
                products_list, token_idx, hash_list = store_data[key]
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
                    rema_ean=product.get('/product/ean', '')
                )
                if m:
                    matches[key] = m

            # EAN cross-fill: if any match has EAN, try to find it in stores that missed
            found_ean = next(
                (m['ean'] for m in matches.values() if m.get('ean')),
                None
            )
            if found_ean:
                for key in EXCEL_STORE_KEYS:
                    if key not in matches:
                        products_list, _, _ = store_data[key]
                        for p in products_list:
                            if p.get('ean') == found_ean:
                                matches[key] = p
                                break

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
                best_match = matches[display_store]
                product['/product/title'] = best_match['name']

                if best_match.get('is_sale'):
                    product['/product/price'] = best_match.get('normal_price') or best_match['price']
                    product['/product/sale_price'] = best_match['price']
                else:
                    product['/product/price'] = best_match['price']
                    product['/product/sale_price'] = None

                product['/product/store'] = _STORE_CONFIGS[display_store]['label']
                if best_match.get('image') and str(best_match['image']).lower() != 'nan':
                    product['/product/imageLink'] = best_match['image']
                product['/product/brand'] = best_match.get('brand') or product['/product/brand']
                product['/product/unit_pricing_measure'] = best_match.get('weight') or product['/product/unit_pricing_measure']
                product['/product/price_per_kg'] = best_match.get('kg_price')
                product['/product/multi_deal'] = best_match.get('multi_deal', '')
            else:
                product['/product/store'] = _STORE_CONFIGS[REMA_KEY]['label']
                product['/product/multi_deal'] = ''

            final_products.append(product)

        # Collect unmatched products from every secondary store
        unmatched = {
            key: [p for p in store_data[key][0] if id(p) not in matched_ids[key]]
            for key in EXCEL_STORE_KEYS
        }

        # Group unmatched products by EAN; those without EAN become solo cards immediately
        ean_groups: dict = {}

        def add_to_groups(products_list, store_key):
            for p in products_list:
                ean = p.get('ean')
                if ean:
                    ean_groups.setdefault(ean, {})[store_key] = p
                else:
                    final_products.extend(build_store_display_products([p], store_key))

        for key in EXCEL_STORE_KEYS:
            add_to_groups(unmatched[key], key)

        # Build combined cards for products sharing an EAN
        for ean, group in ean_groups.items():
            main_key = next((k for k in EXCEL_STORE_KEYS if k in group), None)
            if not main_key:
                continue
            built = build_store_display_products([group[main_key]], main_key)
            if not built:
                continue
            display_item = built[0]

            cheapest_key   = main_key
            cheapest_price = group[main_key]['price']

            for key in EXCEL_STORE_KEYS:
                if key in group:
                    display_item['/product/store_matches'][key] = group[key]
                    if group[key]['price'] < cheapest_price:
                        cheapest_price = group[key]['price']
                        cheapest_key   = key

            display_item['/product/cheapest_at'] = cheapest_key
            display_item['/product/cheaper_at']  = cheapest_key
            display_item['/product/is_any_sale']  = any(p.get('is_sale') for p in group.values())

            display_item['/product/rema_price']   = group[REMA_KEY]['price']   if REMA_KEY in group else 0
            display_item['/product/rema_image']   = group[REMA_KEY].get('image', '') if REMA_KEY in group else display_item.get('/product/imageLink', '')
            display_item['/product/rema_is_sale'] = group[REMA_KEY].get('is_sale', False) if REMA_KEY in group else False

            # Promote cheapest store to card front
            display_item['/product/multi_deal'] = group[main_key].get('multi_deal', '')
            if cheapest_key != main_key:
                promote = group[cheapest_key]
                display_item['/product/title'] = promote['name']

                if promote.get('is_sale'):
                    display_item['/product/price'] = promote.get('normal_price') or promote['price']
                    display_item['/product/sale_price'] = promote['price']
                else:
                    display_item['/product/price'] = promote['price']
                    display_item['/product/sale_price'] = None
                display_item['/product/store'] = _STORE_CONFIGS[cheapest_key]['label']
                if promote.get('image') and str(promote['image']).lower() != 'nan':
                    display_item['/product/imageLink'] = promote['image']
                display_item['/product/brand'] = promote.get('brand') or display_item['/product/brand']
                display_item['/product/unit_pricing_measure'] = promote.get('weight') or display_item['/product/unit_pricing_measure']
                display_item['/product/price_per_kg'] = promote.get('kg_price')
                display_item['/product/multi_deal'] = promote.get('multi_deal', '')

            final_products.append(display_item)

        counts_str = ', '.join(f"{match_counts[k]} matched to {_STORE_CONFIGS[k]['label']}" for k in EXCEL_STORE_KEYS)
        print(
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
        print(f"Dedupliceret: {len(final_products)} -> {len(deduped)} produkter (fjernede {len(final_products)-len(deduped)} dubletter)")
        final_products = deduped

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

def get_active_stores():
    """Helper to get selected stores from query params"""
    stores_param = request.args.get('stores')
    if stores_param:
        return set(stores_param.split(','))
    return None

def filter_products_by_stores(products, active_stores):
    """Helper to filter products by store names, blocked images, and blocked product names."""
    def _is_allowed(p):
        if str(p.get('/product/imageLink', '')).strip() in _PLACEHOLDER_IMGS:
            return False
        if str(p.get('/product/rema_image', '')).strip() in _PLACEHOLDER_IMGS:
            return False
        name = str(p.get('/product/title', '')).lower()
        if any(fragment in name for fragment in _BLOCKED_NAME_FRAGMENTS):
            return False
        return True

    filtered = [p for p in products if _is_allowed(p)]
    if active_stores is None:
        return filtered

    def _matches_active(p):
        if p.get('/product/store', 'Rema 1000') in active_stores:
            return True
        for key in (p.get('/product/store_matches') or {}):
            cfg = _STORE_CONFIGS.get(key, {})
            if cfg.get('label') in active_stores:
                return True
        return False

    return [p for p in filtered if _matches_active(p)]

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

def apply_product_filters(products, args):
    """Helper to apply price, sale, organic, weight filters and sorting to a list of products"""
    min_price = args.get('min_price', type=float)
    max_price = args.get('max_price', type=float)
    sale_only = args.get('sale', type=str) == 'true'
    organic_only = args.get('organic', type=str) == 'true'
    lactose_only = args.get('lactose', type=str) == 'true'
    min_weight = args.get('min_weight', type=float)
    max_weight = args.get('max_weight', type=float)
    sort_type = args.get('sort', 'relevance')

    filtered = []
    for p in products:
        # Use the effective price (sale price if active)
        price = p.get('sale_price') if p.get('is_sale') else p.get('price')
        if price is None: price = 0
        
        if min_price is not None and price < min_price: continue
        if max_price is not None and price > max_price: continue
        if sale_only and not p.get('is_sale') and not p.get('is_any_sale'): continue
        
        # Organic check
        if organic_only:
            name_lower = p.get('name', '').lower()
            desc_lower = p.get('description', '').lower()
            brand_lower = p.get('brand', '').lower()
            combined = f"{name_lower} {desc_lower} {brand_lower}"
            if not any(x in combined for x in ['økolog', 'øko ', ' øko', 'organic']):
                continue
        
        # Lactose check
        if lactose_only:
            name_lower = p.get('name', '').lower()
            desc_lower = p.get('description', '').lower()
            combined = f"{name_lower} {desc_lower}"
            if not any(x in combined for x in ['laktosefri', 'lactose free']):
                continue

        # Weight check
        weight_g = p.get('weight_g')
        if weight_g is not None:
            if min_weight is not None and weight_g < min_weight: continue
            if max_weight is not None and weight_g > max_weight: continue
        elif min_weight is not None and min_weight > 0:
            continue

        filtered.append(p)

    # Sorting
    if sort_type == 'price-asc':
        filtered.sort(key=lambda x: (x.get('sale_price') if x.get('is_sale') else x.get('price')) or 0)
    elif sort_type == 'price-desc':
        filtered.sort(key=lambda x: (x.get('sale_price') if x.get('is_sale') else x.get('price')) or 0, reverse=True)
    elif sort_type == 'kg-price-asc':
        filtered.sort(key=lambda x: x.get('price_per_kg') or 999999)
    elif sort_type == 'name-asc':
        filtered.sort(key=lambda x: x.get('name', '').lower())
        
    return filtered

# --- PRICE HISTORY DATABASE ---
DB_PATH = 'price_history.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS price_history
                 (product_id TEXT, price REAL, date TEXT,
                  PRIMARY KEY (product_id, date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS price_alerts
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  product_id TEXT,
                  product_name TEXT,
                  target_price REAL,
                  current_price REAL,
                  is_active INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS cart_popularity
                 (product_id TEXT PRIMARY KEY, count INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

_last_price_record_date = None

def record_prices_batch(product_price_list):
    """Saves multiple prices in a single transaction. Skips if already run today."""
    global _last_price_record_date
    today = datetime.now().strftime('%Y-%m-%d')
    if _last_price_record_date == today:
        return
    try:
        if not product_price_list: return
        conn = sqlite3.connect(DB_PATH, timeout=20)
        c = conn.cursor()
        
        # Start transaction
        c.execute("BEGIN TRANSACTION")
        for product_id, price in product_price_list:
            if price is not None and price > 0:
                c.execute("INSERT OR REPLACE INTO price_history (product_id, price, date) VALUES (?, ?, ?)",
                          (str(product_id), float(price), today))
        
        # Cleanup old data (only once per batch)
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        c.execute("DELETE FROM price_history WHERE date < ?", (thirty_days_ago,))
        
        conn.commit()
        conn.close()
        _last_price_record_date = today
    except Exception as e:
        print(f"Error in batch recording: {e}")

def get_popular_product_ids(limit=20):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute('SELECT product_id FROM cart_popularity ORDER BY count DESC LIMIT ?', (limit,))
        ids = [row[0] for row in c.fetchall()]
        conn.close()
        return ids
    except Exception:
        return []

@app.route('/api/cart-event', methods=['POST'])
def cart_event():
    try:
        data = request.get_json(force=True)
        product_id = str(data.get('product_id', '')).strip()
        if not product_id:
            return jsonify({'ok': False}), 400
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute(
            'INSERT INTO cart_popularity (product_id, count) VALUES (?, 1) '
            'ON CONFLICT(product_id) DO UPDATE SET count = count + 1',
            (product_id,)
        )
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        print(f"cart-event error: {e}")
        return jsonify({'ok': False}), 500

@app.route('/api/price-history/<product_id>')
def get_price_history(product_id):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        c = conn.cursor()
        # Only fetch the last 30 records for this product
        c.execute("SELECT price, date FROM price_history WHERE product_id = ? ORDER BY date DESC LIMIT 30", (str(product_id),))
        rows = c.fetchall()
        conn.close()
        
        # Reverse to get chronological order (oldest to newest)
        rows.reverse()
        
        history = [{'price': r[0], 'date': r[1]} for r in rows]
        return jsonify(success=True, history=history)
    except Exception as e:
        return jsonify(success=False, error=str(e))

@app.route('/api/create-alert', methods=['POST'])
def create_alert():
    try:
        data = request.json
        p_id = str(data.get('product_id'))
        p_name = data.get('product_name')
        target = float(data.get('target_price'))
        current = float(data.get('current_price'))

        conn = sqlite3.connect(DB_PATH, timeout=20)
        c = conn.cursor()
        c.execute("INSERT INTO price_alerts (product_id, product_name, target_price, current_price) VALUES (?, ?, ?, ?)",
                  (p_id, p_name, target, current))
        conn.commit()
        conn.close()
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e))

init_db()

@app.route('/')
@app.route('/index.html')
def home():
    # Get active stores and filter data
    active_stores = get_active_stores()
    product_data = get_product_data()
    filtered_data = filter_products_by_stores(product_data, active_stores)
    
    # Shuffle for the "tilfældige varer" experience on the front page
    display_data = list(filtered_data)
    random.shuffle(display_data)

    products_by_category = {
        'Ugens Tilbud': [],
        'Brugernes Favoritter': [],
        CAT_MEJERI: [],
        CAT_KOED_FISK: [],
        CAT_FRUGT_GROENT: [],
        CAT_BROED_KAGER: [],
        CAT_FROST: [],
        CAT_KOLONIAL: [],
        CAT_DRIKKEVARER: [],
        CAT_SLIK: [],
        CAT_KIOSK: [],
    }

    seen_tilbud_imgs = set()
    seen_cat_imgs = {cat: set() for cat in products_by_category}

    def _build_product_dict(product, category, sale_end_date=None):
        sale_price = product.get('/product/sale_price')
        return {
            'id': str(product.get('/product/id', '')),
            'name': str(product.get('/product/title', 'Ukendt vare')),
            'price': float(product.get('/product/price', 0)),
            'sale_price': float(sale_price) if sale_price is not None else None,
            'description': str(product.get('/product/description', '')),
            'category': category,
            'brand': str(product.get('/product/brand', '')),
            'image_url': str(product.get('/product/imageLink', '')),
            'rema_image': product.get('/product/rema_image', ''),
            'is_sale': sale_price is not None,
            'is_any_sale': product.get('/product/is_any_sale', False),
            'sale_end_date': sale_end_date,
            'store': str(product.get('/product/store', 'Rema 1000')),
            'unit_measure': str(product.get('/product/unit_pricing_measure', '') or ''),
            'weight_g': parse_weight_to_grams(str(product.get('/product/unit_pricing_measure', '') or '')),
            'price_per_kg': product.get('/product/price_per_kg'),
            'store_matches': product.get('/product/store_matches', {}),
            'cheapest_at': product.get('/product/cheapest_at'),
            'rema_price': product.get('/product/rema_price'),
            'rema_is_sale': product.get('/product/rema_is_sale'),
            'multi_deal': product.get('/product/multi_deal', ''),
        }

    _STAPLES = {
        'mælk', 'brød', 'æg', 'smør', 'yoghurt', 'ost', 'juice',
        'havregryn', 'pasta', 'ris', 'rugbrød', 'fløde', 'kefir',
        'skyr', 'tomat', 'kartofler', 'løg', 'gulerødder', 'kylling',
        'hakket', 'leverpostej', 'syltetøj', 'marmelade', 'kaffe',
        'te', 'vand', 'cola', 'spaghetti', 'mel', 'sukker', 'salt',
    }

    def _staple_score(name):
        n = name.lower()
        return sum(1 for kw in _STAPLES if kw in n)

    seen_fav_imgs = set()
    used_fav_ids = set()

    def _try_add_fav(product):
        try:
            if float(product.get('/product/price', 0)) <= 0:
                return False
            pid = str(product.get('/product/id', ''))
            if pid in used_fav_ids:
                return False
            _img = str(product.get('/product/imageLink', '')).strip()
            if _img and _img not in ('nan', 'None') and _img not in _PLACEHOLDER_IMGS:
                if _img in seen_fav_imgs:
                    return False
                seen_fav_imgs.add(_img)
            products_by_category['Brugernes Favoritter'].append(
                _build_product_dict(product, product.get('/product/product_type', CAT_KOLONIAL))
            )
            used_fav_ids.add(pid)
            return True
        except (ValueError, TypeError):
            return False

    _cat_keys = {CAT_MEJERI, CAT_KOED_FISK, CAT_FRUGT_GROENT, CAT_BROED_KAGER,
                 CAT_FROST, CAT_KOLONIAL, CAT_DRIKKEVARER, CAT_SLIK, CAT_KIOSK}
    staple_scored = []

    # Single pass: populate Ugens Tilbud, all categories, and collect staple scores
    for product in display_data:
        ptype = product.get('/product/product_type')
        if ptype is None:
            continue
        try:
            price = float(product.get('/product/price', 0))
            _img = str(product.get('/product/imageLink', '')).strip()
            _img_valid = _img and _img not in ('nan', 'None') and _img not in _PLACEHOLDER_IMGS

            # Ugens Tilbud
            if product.get('/product/sale_price') or product.get('/product/is_any_sale'):
                sale_end_date = None
                try:
                    sale_dates = str(product.get('/product/sale_price_effective_date', '')).split('/')
                    if len(sale_dates) > 1:
                        date_obj = datetime.strptime(sale_dates[1].strip(), '%Y-%m-%dT%H:%M:%S%z')
                        sale_end_date = date_obj.strftime('%d/%m')
                except (ValueError, TypeError):
                    pass
                if not _img_valid or _img not in seen_tilbud_imgs:
                    if _img_valid:
                        seen_tilbud_imgs.add(_img)
                    products_by_category['Ugens Tilbud'].append(
                        _build_product_dict(product, ptype, sale_end_date)
                    )

            # Regular categories
            if ptype in _cat_keys and price > 0:
                if not _img_valid or _img not in seen_cat_imgs[ptype]:
                    if _img_valid:
                        seen_cat_imgs[ptype].add(_img)
                    products_by_category[ptype].append(_build_product_dict(product, ptype))

            # Staple scoring for Brugernes Favoritter fallback
            score = _staple_score(str(product.get('/product/title', '')))
            if score > 0:
                staple_scored.append((score, product))

        except (ValueError, TypeError, KeyError):
            continue

    # Brugernes Favoritter — Step 1: popularity data
    popular_ids = get_popular_product_ids(limit=20)
    if popular_ids:
        id_to_product = {str(p.get('/product/id', '')): p for p in display_data}
        leftover_ids = []
        for pid in popular_ids:
            product = id_to_product.get(pid)
            if not product:
                continue
            if product.get('/product/store_matches'):
                _try_add_fav(product)
            else:
                leftover_ids.append(pid)
        for pid in leftover_ids:
            product = id_to_product.get(pid)
            if product:
                _try_add_fav(product)

    # Brugernes Favoritter — Step 2: staple fallback
    if len(products_by_category['Brugernes Favoritter']) < 10:
        staple_scored.sort(key=lambda x: x[0], reverse=True)
        for _, product in staple_scored:
            if len(products_by_category['Brugernes Favoritter']) >= 20:
                break
            _try_add_fav(product)

    trimmed_categories = {k: v[:20] for k, v in products_by_category.items() if v}
    template_mapping = {
        'Ugens Tilbud':     'sale.html',
        'Brugernes Favoritter': None,
        CAT_MEJERI:         'Mejeri.html',
        CAT_KOED_FISK:      'Koed_og_fisk.html',
        CAT_FRUGT_GROENT:   'Frugt_og_groent.html',
        CAT_BROED_KAGER:    'Broed_og_kager.html',
        CAT_FROST:          'Frost.html',
        CAT_KOLONIAL:       'Kolonial.html',
        CAT_DRIKKEVARER:    'Drikkevarer.html',
        CAT_SLIK:           'Slik.html',
        CAT_KIOSK:          'Kiosk.html',
    }

    # Handle AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render_template(
            'partials/index_products.html',
            categories=trimmed_categories,
            template_mapping=template_mapping
        )

    # Track prices for history (batch)
    price_batch = []
    for cat_products in products_by_category.values():
        for p in cat_products:
            price_batch.append((p.get('id'), p.get('sale_price') if p.get('is_sale') else p.get('price')))
    record_prices_batch(price_batch)

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
        
        active_stores = get_active_stores()
        product_data = get_product_data()
        filtered_data = filter_products_by_stores(product_data, active_stores)
        
        sale_products = []
        for product in filtered_data:
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
                        'rema_image': product.get('/product/rema_image', ''),
                        'is_sale': True if sale_price is not None else False,
                        'is_any_sale': product.get('/product/is_any_sale', False),
                        'sale_end_date': sale_end_date,
                        'unit_measure': str(product.get('/product/unit_pricing_measure', '') or ''),
                        'weight_g': parse_weight_to_grams(str(product.get('/product/unit_pricing_measure', '') or '')),
                        'price_per_kg': (product.get('/product/price_per_kg') if product.get('/product/price_per_kg') is not None else None),
                        'store': str(product.get('/product/store', 'Rema 1000')),
                        'store_matches': product.get('/product/store_matches', {}),
                        'cheaper_at':  product.get('/product/cheaper_at'),
                        'cheapest_at': product.get('/product/cheapest_at'),
                        'rema_price': product.get('/product/rema_price'),
                        'rema_is_sale': product.get('/product/rema_is_sale'),
                        'multi_deal': product.get('/product/multi_deal', ''),
                    }
                    sale_products.append(product_dict)
                except (ValueError, TypeError, KeyError) as e:
                    print(f"Error converting prices for sale product {product['/product/id']} - {product['/product/title']}: {str(e)}")
                    continue
        
        # Apply Filters
        sale_products = apply_product_filters(sale_products, request.args)

        # Calculate pagination
        total_products = len(sale_products)
        total_pages = (total_products + per_page - 1) // per_page
        page = min(max(page, 1), total_pages) if total_pages > 0 else 1
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_products = sale_products[start_idx:end_idx]
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render_template('partials/product_grid.html', 
                                 products=paginated_products,
                                 current_page=page,
                                 total_pages=total_pages)

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
        active_stores = get_active_stores()
        product_data = get_product_data()
        filtered_data = filter_products_by_stores(product_data, active_stores)
        
        all_products = []
        match_count = 0
        
        for product in filtered_data:
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
                    'rema_image': product.get('/product/rema_image', ''),
                    'is_sale': False,
                    'unit_measure': str(product.get('/product/unit_pricing_measure', '') or ''),
                    'weight_g': parse_weight_to_grams(str(product.get('/product/unit_pricing_measure', '') or '')),
                    'price_per_kg': (product.get('/product/price_per_kg') if product.get('/product/price_per_kg') is not None else None),
                    'store': str(product.get('/product/store', 'Rema 1000')),
                    'store_matches': product.get('/product/store_matches', {}),
                    'cheaper_at':  product.get('/product/cheaper_at'),
                    'cheapest_at': product.get('/product/cheapest_at'),
                    'rema_price': product.get('/product/rema_price'),
                    'rema_is_sale': product.get('/product/rema_is_sale'),
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
                
                # Split query into words and check if ALL words match
                search_terms = query.split()
                if all(any(term in field for field in (product_name, product_brand, product_description)) for term in search_terms):
                    all_products.append(product_dict)
                    match_count += 1
                    
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
              else ('spar' if 'spar' in store_lower
              else ('sb' if 'superbrugsen' in store_lower else 'rema')))) -%}
            {%- set badge_label = 'Bilka' if 'bilka' in store_lower
              else ('Min Købmand' if ('min' in store_lower or 'kobmand' in store_lower)
              else ('Meny' if 'meny' in store_lower
              else ('Spar' if 'spar' in store_lower
              else ('SuperBrugsen' if 'superbrugsen' in store_lower else 'Rema 1000')))) -%}
            <div id="product{{ product.id }}" class="product"
                 onclick="openOverlay(this)"
                 data-cheapest-at="{{ product.cheapest_at or '' }}"
                 {% for key, match in product.store_matches.items() %}data-{{ key }}-price="{{ match.price }}" data-{{ key }}-name="{{ match.name }}" data-{{ key }}-kg-price="{% if match.kg_price is not none %}{{ '%.2f'|format(match.kg_price) }}{% endif %}" data-{{ key }}-is-sale="{{ 'true' if match.is_sale else 'false' }}" {% endfor %}
                 data-rema-price="{{ product.rema_price if product.rema_price is defined else '' }}"
                 data-rema-is-sale="{{ 'true' if product.rema_is_sale else 'false' }}"
                 data-rema-weight="{{ product.unit_measure if product.unit_measure else '' }}"
                 data-weight-g="{{ product.weight_g if product.weight_g else '' }}"
                 data-rema-kg-price="{% if product.price_per_kg is not none %}{{ '%.2f'|format(product.price_per_kg) }}{% endif %}"
                 data-store="{{ product.store or 'Rema 1000' }}"
                 data-has-match="{{ 'true' if (product.store_matches or (product.rema_price and product.rema_price > 0)) else 'false' }}"
                 data-has-match-rema="{{ 'true' if product.rema_price and product.rema_price > 0 else 'false' }}"
                 data-category="{{ product.category|default('Andre varer') }}"
                 data-main-image="{{ product.image_url }}"
                 data-rema-image="{{ product.rema_image }}"
                 data-is-organic="{{ 'true' if ('økolog' in (product.name|lower + ' ' + (product.description|lower if product.description else '') + ' ' + (product.brand|lower if product.brand else '')) or 'øko ' in (product.name|lower + ' ' + (product.description|lower if product.description else '') + ' ' + (product.brand|lower if product.brand else '')) or ' øko' in (product.name|lower + ' ' + (product.description|lower if product.description else '') + ' ' + (product.brand|lower if product.brand else '')) or 'organic' in (product.name|lower + ' ' + (product.description|lower if product.description else '') + ' ' + (product.brand|lower if product.brand else ''))) else 'false' }}"
                 data-is-lactose-free="{{ 'true' if ('laktosefri' in (product.name|lower + ' ' + (product.description|lower if product.description else '') + ' ' + (product.brand|lower if product.brand else '')) or 'lactose free' in (product.name|lower + ' ' + (product.description|lower if product.description else '') + ' ' + (product.brand|lower if product.brand else ''))) else 'false' }}">
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
                {% if not product.store_matches %}
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
                <div class="corner-box" onclick="event.stopPropagation(); addToCart(event, this.closest('.product'))">
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
                    
                # Filter out products from removed categories
                category = product.get('/product/product_type')
                if category is None:
                    continue
                    
                product_dict = {
                    'id': str(product['/product/id']),
                    'name': str(product['/product/title']),
                    'price': float(product['/product/price']),
                    'description': str(product['/product/description']),
                    'category': category,
                    'brand': str(product['/product/brand']),
                    'image_url': str(product['/product/imageLink']),
                    'rema_image': product.get('/product/rema_image', ''),
                    'is_sale': False,
                    'unit_measure': str(product.get('/product/unit_pricing_measure', '') or ''),
                    'price_per_kg': (product.get('/product/price_per_kg') if product.get('/product/price_per_kg') is not None else None),
                    'store_matches': product.get('/product/store_matches', {}),
                    'cheaper_at':  product.get('/product/cheaper_at'),
                    'cheapest_at': product.get('/product/cheapest_at'),
                    'rema_price': product.get('/product/rema_price'),
                    'rema_is_sale': product.get('/product/rema_is_sale'),
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
                
                # Split query into words and check if ALL words match
                search_terms = query.split()
                if all(any(term in field for field in (product_name, product_brand, product_description)) for term in search_terms):
                    all_products.append(product_dict)
                    
            except (ValueError, TypeError, KeyError) as e:
                print(f"Error processing product: {str(e)}")
                continue
        
        # Track prices for history (batch)
        price_batch = [(p.get('id'), p.get('sale_price') if p.get('is_sale') else p.get('price')) for p in all_products]
        record_prices_batch(price_batch)

        # Apply Filters
        all_products = apply_product_filters(all_products, request.args)

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

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # For AJAX filtering
            return render_template('partials/product_grid.html', 
                                 products=paginated_products,
                                 current_page=page,
                                 total_pages=total_pages)

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
        'Kolonial': CAT_KOLONIAL,
        'Drikkevarer': CAT_DRIKKEVARER,
        'Mejeri': CAT_MEJERI,
        'Frugt_og_groent': CAT_FRUGT_GROENT,
        'Frost': CAT_FROST,
        'Broed_og_kager': CAT_BROED_KAGER,
        'Koed_og_fisk': CAT_KOED_FISK,
        'Slik': CAT_SLIK,
        'Kiosk': CAT_KIOSK
    }
    
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 60  # 6x10 layout
        
        # Get the actual category name from the filename
        actual_category = category_mapping.get(category_name.replace('.html', ''))
        if not actual_category:
            return "Category not found", 404
            
        # Get products for this category
        active_stores = get_active_stores()
        product_data = get_product_data()
        filtered_data = filter_products_by_stores(product_data, active_stores)
        
        category_products = []
        
        for product in filtered_data:
            p_type = product.get('/product/product_type')
            if p_type and str(p_type) == actual_category:
                try:
                    # Get the sale end date if it's a sale product
                    sale_end_date = None
                    sale_price = product.get('/product/sale_price')
                    
                    if sale_price:
                        sale_dates = str(product.get('/product/sale_price_effective_date', '')).split('/')
                        if len(sale_dates) > 1:
                            try:
                                # Parse the date and reformat to dd/mm
                                date_str = sale_dates[1].strip()
                                date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S%z')
                                sale_end_date = date_obj.strftime('%d/%m')
                            except Exception:
                                sale_end_date = None

                    product_dict = {
                        'id': str(product.get('/product/id', '')),
                        'name': str(product.get('/product/title', 'Ukendt vare')),
                        'price': float(product.get('/product/price', 0)),
                        'description': str(product.get('/product/description', '')),
                        'category': str(p_type),
                        'brand': str(product.get('/product/brand', '')),
                        'image_url': str(product.get('/product/imageLink', '')),
                        'rema_image': product.get('/product/rema_image', ''),
                        'is_sale': False,
                        'sale_end_date': sale_end_date,
                        'store': str(product.get('/product/store', 'Rema 1000')),
                        'unit_measure': str(product.get('/product/unit_pricing_measure', '') or ''),
                        'weight_g': parse_weight_to_grams(str(product.get('/product/unit_pricing_measure', '') or '')),
                        'price_per_kg': (product.get('/product/price_per_kg') if product.get('/product/price_per_kg') is not None else None),
                        'store_matches': product.get('/product/store_matches', {}),
                        'cheaper_at':  product.get('/product/cheaper_at'),
                        'cheapest_at': product.get('/product/cheapest_at'),
                        'rema_price': product.get('/product/rema_price'),
                        'rema_is_sale': product.get('/product/rema_is_sale'),
                    }

                    # Check if it's a sale product
                    product_dict['is_any_sale'] = product.get('/product/is_any_sale', False)
                    if sale_price:
                        product_dict['is_sale'] = True
                        product_dict['sale_price'] = float(sale_price)
                    
                    category_products.append(product_dict)
                except Exception as e:
                    print(f"Error processing product in category: {str(e)}")
                    continue

        # Track prices for history (batch)
        price_batch = [(p.get('id'), p.get('sale_price') if p.get('is_sale') else p.get('price')) for p in category_products]
        record_prices_batch(price_batch)

        # Apply Filters
        category_products = apply_product_filters(category_products, request.args)

        # Calculate pagination
        total_products = len(category_products)
        total_pages = (total_products + per_page - 1) // per_page
        page = min(max(page, 1), total_pages) if total_pages > 0 else 1
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_products = category_products[start_idx:end_idx]
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render_template('partials/product_grid.html', 
                                 products=paginated_products,
                                 current_page=page,
                                 total_pages=total_pages)

        return render_template('category.html', 
                            category_name=actual_category,
                            products=paginated_products,
                            current_page=page,
                            total_pages=total_pages)
                            
    except Exception as e:
        print(f"Error loading category {category_name}: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Internal Server Error: {str(e)}", 500

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

@app.route('/api/stores')
def get_stores():
    stores = [{'key': k, 'label': v['label'], 'logo': v['logo']} for k, v in _STORE_CONFIGS.items()]
    return jsonify({'stores': stores})


@app.route('/api/products', methods=['GET'])
def get_separate_products():
    """Returns slim price data from the existing cache for cart store comparison."""
    try:
        products = get_product_data()
        rema = [
            {
                '/product/id': p.get('/product/id', ''),
                '/product/price': p.get('/product/price'),
                '/product/sale_price': p.get('/product/sale_price'),
                '/product/store_matches': {
                    k: {'price': v.get('price')}
                    for k, v in (p.get('/product/store_matches') or {}).items()
                },
            }
            for p in products
            if p.get('/product/store') == 'Rema 1000'
        ]
        return jsonify({'success': True, 'rema_products': rema, 'bilka_products': []})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)