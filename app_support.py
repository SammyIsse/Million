"""Shared utilities: logging, rate limiting, search index, optional DB flag."""

from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from collections import defaultdict, deque
from datetime import datetime
from functools import wraps
from typing import Callable

from rapidfuzz.fuzz import ratio as rapid_ratio, token_sort_ratio as rapid_token_sort

logger = logging.getLogger('million')

_db_available: bool | None = None


def configure_logging() -> None:
    level = logging.DEBUG if os.environ.get('FLASK_DEBUG', '0') == '1' else logging.INFO
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        )


def is_price_db_enabled() -> bool:
    flag = os.environ.get('ENABLE_PRICE_DB', 'auto').lower()
    if flag in ('0', 'false', 'no', 'off'):
        return False
    if flag in ('1', 'true', 'yes', 'on'):
        return True
    return True


def set_db_available(ok: bool) -> None:
    global _db_available
    _db_available = ok


def db_available() -> bool:
    if _db_available is None:
        return is_price_db_enabled()
    return _db_available


class RateLimiter:
    """In-memory per-IP rate limit (no database)."""

    def __init__(self, max_calls: int = 60, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=max_calls))

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window_seconds:
            hits.popleft()
        if len(hits) >= self.max_calls:
            return False
        hits.append(now)
        return True


api_limiter = RateLimiter(max_calls=60, window_seconds=60)


def rate_limit(limiter: RateLimiter) -> Callable:
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            from flask import jsonify, request
            ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown')
            if ',' in ip:
                ip = ip.split(',')[0].strip()
            key = f'{ip}:{f.__name__}'
            if not limiter.allow(key):
                logger.warning('Rate limit exceeded for %s', key)
                return jsonify(success=False, error='For mange forespørgsler. Prøv igen om lidt.'), 429
            return f(*args, **kwargs)
        return wrapped
    return decorator


def build_search_index(products: list, normalize_fn) -> dict[str, set[str]]:
    """token -> set of product ids for fast AND-search."""
    index: dict[str, set[str]] = {}
    for product in products:
        pid = str(product.get('/product/id', '')).strip()
        if not pid or pid in ('None', ''):
            continue
        text = ' '.join([
            str(product.get('/product/title', '')),
            str(product.get('/product/brand', '')),
            str(product.get('/product/description', '')),
        ])
        norm = normalize_fn(text)
        seen_tokens: set[str] = set()
        for token in norm.split():
            if len(token) >= 3 and token not in seen_tokens:
                seen_tokens.add(token)
                index.setdefault(token, set()).add(pid)
    return index


def search_product_ids(index: dict[str, set[str]], query: str) -> set[str] | None:
    terms = [t for t in query.lower().split() if len(t) >= 2]
    if not terms or not index:
        return None
    result: set[str] | None = None
    for term in terms:
        term_ids: set[str] = set()
        for token, pids in index.items():
            if term in token:
                term_ids.update(pids)
        if not term_ids:
            return set()
        result = term_ids if result is None else result & term_ids
    return result or set()


def product_matches_query(product: dict, query: str) -> bool:
    """Fallback substring search when index is unavailable."""
    terms = query.lower().split()
    if not terms:
        return False
    name = str(product.get('name', '')).lower()
    brand = str(product.get('brand', '')).lower()
    desc = str(product.get('description', '')).lower()
    fields = (name, brand, desc)
    return all(any(term in field for field in fields) for term in terms)


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

DEFAULT_HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'da,da-DK;q=0.9,en;q=0.8',
}

_STORE_CONFIGS = {
    'rema':       {'db_key': None,           'label': 'Rema 1000',    'logo': '/static/images/Rema1000-logo.png'},
    'bilka':      {'db_key': 'Bilka',        'label': 'Bilka',        'logo': '/static/images/bilka-logo.png'},
    'netto':      {'db_key': 'Netto',        'label': 'Netto',        'logo': '/static/images/netto-logo.png'},
    'foetex':     {'db_key': 'Foetex',      'label': 'Føtex',        'logo': '/static/images/foetex-logo.png'},
    'mk':         {'db_key': 'minkøbmand',   'label': 'Min Købmand',  'logo': '/static/images/Min_kobmand_logo.png'},
    'meny':       {'db_key': 'Meny',         'label': 'Meny',         'logo': '/static/images/meny-logo.png'},
    'spar':       {'db_key': 'Spar',         'label': 'Spar',         'logo': '/static/images/spar-logo.png'},
    'sb':         {'db_key': 'SuperBrugsen', 'label': 'SuperBrugsen', 'logo': '/static/images/superbrugsen-logo.png'},
    'brugsen':    {'db_key': 'Brugsen',      'label': 'Brugsen',      'logo': '/static/images/brugsen-logo.png'},
    'kvickly':    {'db_key': 'Kvickly',      'label': 'Kvickly',      'logo': '/static/images/kvickly-logo.png'},
    'discount365':{'db_key': '365discount',  'label': '365 Discount', 'logo': '/static/images/365discount-logo.png'},
}


def format_price(price_str):
    if not price_str:
        return 0.0
    try:
        cleaned = str(price_str).replace('DKK', '').replace('kr', '').replace(',', '.').strip()
        return float(cleaned)
    except (ValueError, TypeError):
        logger.error(f"Error converting price: {price_str}")
        return 0.0


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------

_ABBREV_COMPILED: list[tuple] = [
    (re.compile(r'\bsr\b'),      'sour'),
    (re.compile(r'\bsc\b'),      'sour cream'),
    (re.compile(r'\bonion\b'),   'onion'),
    (re.compile(r'\bo\b'),       'onion'),
    (re.compile(r'\bhk\b'),      'hakket'),
    (re.compile(r'\bmin\b'),     'mini'),
    (re.compile(r'\bøko\b'),     'okologisk'),
    (re.compile(r'\borg\b'),     'okologisk'),
    # vanilla stavet på dansk/fr/en → fælles form
    (re.compile(r'\bvanille\b'), 'vanilje'),
    (re.compile(r'\bvanilla\b'), 'vanilje'),
    # normalisering af smørbar-varianter (inkl. bilka scrape fejl)
    (re.compile(r'\bsmørbart\b'), 'smørbar'),
    (re.compile(r'\bsmrbar\b'), 'smørbar'),
]
_OKOLOGISK_RE = re.compile(r'\bokologisk\b')


def normalize_name(name):
    if not name or str(name) == 'nan':
        return ''
    name = str(name).lower().strip()
    name = unicodedata.normalize('NFKD', name)
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
    name = name.replace('&', 'and').replace('+', 'and').replace(',', ' ')
    for pattern, replacement in _ABBREV_COMPILED:
        name = pattern.sub(replacement, name)
    for noise in ['%', ' eko', ' bio', ' a/s', ' i/s']:
        name = name.replace(noise, '')
    name = _OKOLOGISK_RE.sub('', name)
    return ' '.join(name.split())


def fuzzy_score(a, b):
    if not a or not b: return 0.0
    if a == b: return 1.0
    la, lb = len(a), len(b)
    if (2.0 * min(la, lb) / (la + lb)) < 0.35:
        return 0.0
    # max af ratio (følsom for ordstilling) og token_sort (ufølsom for ordstilling),
    # så fx "Rød peberfrugt" ≈ "Peberfrugt rød" matcher. token_set bruges bevidst IKKE,
    # da den over-matcher delmængder (fx "Kaffe" ≈ "Kaffe Filter").
    return max(rapid_ratio(a, b), rapid_token_sort(a, b)) / 100.0


# ---------------------------------------------------------------------------
# Weight / unit parsing
# ---------------------------------------------------------------------------

_WEIGHT_TOLERANCE_G = 50  # grams / ml

_WEIGHT_RE = re.compile(r'^([\d.]+)\s*([a-zæøå]+)$')
_STK_RE = re.compile(r'^([\d.]+)\s*st[k]?$')


def parse_weight_to_grams(weight_str) -> float | None:
    if not weight_str or str(weight_str).strip().lower() in ('nan', '', 'none'):
        return None
    s = str(weight_str).strip().lower().replace(',', '.')
    m = _WEIGHT_RE.match(s)
    if not m:
        return None
    try:
        value = float(m.group(1))
        unit = m.group(2)
    except ValueError:
        return None
    if unit in ('g', 'gr', 'gram'):     return value
    if unit in ('kg',):                  return value * 1000
    if unit in ('l', 'ltr', 'liter', 'litre'): return value * 1000
    if unit in ('ml',):                  return value
    if unit in ('cl',):                  return value * 10
    if unit in ('dl',):                  return value * 100
    return None


def parse_stk_count(weight_str) -> int | None:
    if not weight_str or str(weight_str).strip().lower() in ('nan', '', 'none'):
        return None
    s = str(weight_str).strip().lower().replace(',', '.')
    m = _STK_RE.match(s)
    if not m:
        return None
    try:
        return int(float(m.group(1)))
    except ValueError:
        return None


def weights_compatible(w_a: float | None, w_b: float | None, tolerance: float = _WEIGHT_TOLERANCE_G) -> bool:
    if w_a is None or w_b is None:
        return True
    return abs(w_a - w_b) <= tolerance


# ---------------------------------------------------------------------------
# Product filtering constants
# ---------------------------------------------------------------------------

_BLOCKED_NAME_FRAGMENTS = {
    # Personlig pleje
    'indlæg', 'batteri', 'shampoo', 'balsam', 'creme', 'lotion', 'bleer',
    'bleposer', 'vaskeserviet', 'vådserviet', 'skumvaskeklud', 'sutteflaske',
    'tandpasta', 'tandbørste', 'håndsæbe', 'shower gel', 'deodorant',
    'deospray', 'bind', 'tampon', 'hudpleje', 'parfume', 'solcreme',
    'sollotion', 'mascara', 'neglelak', 'makeupfjerner', 'brusegel',
    # Kæledyr
    'hundemad', 'kattefoder', 'kattemad', 'hundesnack', 'kattegrus',
    'pedigree', 'whiskas', 'felix', 'royal canin', 'purina', 'dreamies',
    # Rengøring & husholdning
    'opvaskemiddel', 'vaskemiddel', 'skyllemiddel', 'opvasketabs',
    'vaskekapsler', 'toiletrengøring', 'bref', 'domestos', 'harpic',
    'toiletpapir', 'køkkenrulle', 'køkken rulle',
    # Tobak
    'tobak', 'cigaret', 'cigarillo', 'cigar', 'snus', 'nikotin',
    'tændstik', 'lighter', 'fyrstikker', 'marlboro', 'winston', 'camel',
    'skjold rød', 'skjold blå', 'skjold grå', "king's", 'prince filter', 'prince røg',
    # Blade & magasiner
    'hjemmet', 'søndag', 'hendes verden', 'her og nu', 'billed bladet',
    'billedbladet', 'se og hør', 'ude og hjemme', 'ude & hjemme',
    '7-tv-dage', 'alt for damerne', 'anders and', 'zapp elektron',
    'piberensere', 'ekstra bladet',
    # Planter & blomster
    'plante', 'planter', 'potte', 'potteskjuler', 'blomst', 'blomster',
    'buket', 'roser', 'tulipaner', 'orkidé', 'krysantemum', 'gødning',
    'pottejord', 'plantejord', 'havejord', 'blomsterjord', 'pottemuld', 'spagnum',
    # Tøj & tekstil
    'sneakers', 't-shirt', 'solbriller', 'badeklæde', 'leggings',
    'sengetøj', 'sengetæppe', 'pude', 'dyne', 'slipper', 'hjemmesko', 'kasket',
    # Møbler & have
    'havestol', 'spisebordsstol', 'lænestol', 'liggestol', 'klapstol',
    'gyngestol', 'havebord', 'sofabord', 'spisebord', 'havemøbel', 'havemøbler',
    'krukke', 'parasol', 'trolley',
    # Sæson & fritid
    'telt', 'nissehave', 'kridt', 'uneflex',
    # Maskiner & køkkengrej
    'kaffemaskine', 'espressomaskine', 'elkedel', 'airfryer', 'stegepande',
    'støvsuger', 'støvsugerpose',
    # Lys
    'stearinlys', 'fyrfadslys', 'kronelys', 'bloklys',
    # Kosttilskud
    'vitaminer', 'kosttilskud', 'proteinpulver', 'whey protein',
}

# Krav: kun mad — ingen undtagelser. Ekstra ikke-mad-termer ud over dem ovenfor.
# Bemærk: 'creme' er bevidst IKKE med (rammer fødevarer som "cremefraiche"/"is creme").
_EXTRA_NON_FOOD_TERMS = {
    # Kæledyr
    'hundefoder', 'kæledyrsfoder', 'dyrefoder', 'dyremad', 'kattesand',
    'kattebakke', 'hundelegetøj', 'kattemøbel', 'friskies', 'iams', 'sheba',
    # Rengøring & husholdning
    'sæbe', 'rengøringsmiddel', 'afkalker', 'afspændingsmiddel', 'wc-rens',
    'toiletrens', 'pletfjerner', 'tøjvask', 'skuresvamp', 'karklud', 'karklude',
    'viskestykke', 'affaldsposer', 'skraldeposer', 'fryseposer', 'husholdningsfilm',
    'alufolie', 'bagepapir', 'servietter', 'lommetørklæder', 'tørrestativ',
    # Personlig pleje
    'bodylotion', 'barberskum', 'barberblade', 'vatpinde', 'vatrondeller',
    'tandtråd', 'mundskyl', 'intimsæbe', 'sololie', 'solspray', 'solstift',
    'sæbespåner', 'deo',
    # Tøj, sko & tekstil
    'sokker', 'undertøj', 'strømper', 'badehåndklæde', 'håndklæde', 'viskestykker',
    # Elektronik, husgeråd, legetøj m.m.
    'lyspære', 'glødepære', 'batterier', 'opladelige', 'legetøj', 'spil',
    'puslespil', 'engangsservice', 'plastikkrus', 'paptallerken',
    # Forbrugerelektronik (fx Føtex sælger tv, telefoner og tilbehør).
    # Bemærk: bare 'tv' undgås bevidst — kolliderer med snacks som "TV-Mix".
    'smart tv', 'fjernsyn', 'oled', 'qled',
    'soundbar', 'høretelefon', 'høretelefoner', 'hovedtelefoner',
    'øretelefoner', 'earbuds', 'mobiltelefon', 'smartphone', 'telefon',
    'bærbar', 'laptop', 'oplader', 'powerbank', 'router', 'printer',
    'playstation', 'xbox', 'nintendo', 'smartwatch', 'højttaler',
    'kamera', 'overvågningskamera', 'videokamera', 'webcam',
    # Elektronik-mærker uden fødevarer (entydige i dagligvarekontekst)
    'samsung', 'iphone', 'ipad', 'ipod', 'macbook', 'airpods',
    'huawei', 'xiaomi', 'oneplus', 'hisense', 'prosonic', 'tp-link',
    'tcl', 'zte', 'doro', 'lg',
    # Kosttilskud & helse
    'fiskeolie', 'magnesium', 'd-vitamin', 'c-vitamin', 'multivitamin',
    'vitamintilskud', 'kreatin', 'collagen',
}

# Ordgrænse-baseret regex: matcher kun hele ord, så fødevare-sammensætninger
# (fx "jordbær", "cremefraiche", "balsamico") ikke rammes ved et uheld.
_NON_FOOD_NAME_TERMS = (_BLOCKED_NAME_FRAGMENTS - {'creme'}) | _EXTRA_NON_FOOD_TERMS
_NON_FOOD_NAME_RE = re.compile(
    r'(?<![0-9a-zæøåäöü])(?:'
    + '|'.join(re.escape(t) for t in sorted(_NON_FOOD_NAME_TERMS, key=len, reverse=True))
    + r')(?![0-9a-zæøåäöü])',
    re.IGNORECASE,
)


def is_non_food_name(name: str) -> bool:
    """True hvis produktnavnet klart er en ikke-mad-vare (ordgrænse-match)."""
    return bool(name) and _NON_FOOD_NAME_RE.search(str(name).lower()) is not None


_PLACEHOLDER_IMGS = {
    '/static/images/bilka-logo.png',
    '/static/images/Min_kobmand_logo.png',
    '/static/images/meny-logo.png',
    '/static/images/spar-logo.png',
    '/static/images/Rema1000-logo.png',
    'https://rema-product-images.digital.rema1000.dk/521365/1-large-bJ9YdpX0qL.webp',
    'https://rema-product-images.digital.rema1000.dk/521363/1-large-rDq68WajPb.webp',
    'https://rema-product-images.digital.rema1000.dk/521374/1-large-869DBK5MoM.webp',
}

# ---------------------------------------------------------------------------
# Standard site categories
# ---------------------------------------------------------------------------

CAT_MEJERI       = 'Køl'
CAT_KOED_FISK    = 'Kød & Fisk'
CAT_FRUGT_GROENT = 'Frugt & Grønt'
CAT_BROED_KAGER  = 'Brød & Kager'
CAT_FROST        = 'Frost'
CAT_KOLONIAL     = 'Kolonial'
CAT_DRIKKEVARER  = 'Drikkevarer'
CAT_SLIK         = 'Slik'
CAT_ANDET        = 'Andre varer'

# ---------------------------------------------------------------------------
# Subcategory keyword rules — ordered, first match wins
# ---------------------------------------------------------------------------

_SUBCATEGORY_RULES: dict[str, list[tuple[str, tuple]]] = {
    CAT_DRIKKEVARER: [
        ('Øl & Cider',        (' øl', 'øl ', 'pilsner', 'lager', ' ale ', 'ipa', 'stout', 'porter', 'cider', 'radler', 'breezer', 'pils ')),
        ('Vin & Spiritus',    ('hvidvin', 'rødvin', 'rosé', 'prosecco', 'champagne', 'cava', 'sangria', 'whisky', 'whiskey', 'vodka', ' gin ', ' rom ', 'tequila', 'likør', 'akvavit', 'spiritus', 'cognac', 'brandy', 'cointreau', 'baileys', ' vin ', 'vin,')),
        ('Kaffe & Te',        ('kaffe', 'espresso', 'cappuccino', 'kaffekapsler', 'nespresso', ' te ', 'te,', 'tebreve', 'chai', 'urtete', 'grøn te', 'matcha')),
        ('Juice & Smoothie',  ('juice', 'smoothie', 'nektar', 'frugtdrik', 'kokosvand')),
        ('Saft & Sirup',      ('saft', 'sirup', 'squash', 'koncentrat')),
        ('Vand',              ('mineralvand', 'kildevand', 'danskvand', ' vand', 'vand ')),
        ('Sodavand & Energi', ('cola', 'sodavand', 'energidrik', 'energy drink', 'sportsdrik', 'red bull', 'redbull', 'monster ', 'iste', 'ice tea', 'lemonade', 'tonic', 'kombucha')),
    ],
    CAT_MEJERI: [
        ('Mælk & Fløde',      ('mælk', 'fløde', 'halvfløde', 'kærnemælk', 'kefir', 'havremælk', 'mandelmælk', 'sojamælk', 'rismælk')),
        ('Yoghurt & Kvark',   ('yoghurt', 'skyr', 'kvark', 'ymer', 'fromage', 'fraiche', 'creme fraiche')),
        ('Ost',               ('ost', 'brie', 'camembert', 'gouda', 'cheddar', 'parmesan', 'fetaost', 'feta', 'mozzarella', 'ricotta', 'hytteost', 'danbo', 'esrom', 'castello')),
        ('Smør & Fedtstof',   ('smør', 'margarine', 'plantesmør', 'bregott', 'lurpak')),
        ('Æg',                ('æg',)),
        ('Pålæg & Kølvarer',  ('pålæg', 'leverpostej', 'postej', 'skinke', 'salami', 'rullepølse', 'spegepølse', 'mortadella', 'roastbeef', 'paté', 'pølse', 'hummus')),
    ],
    CAT_KOED_FISK: [
        ('Oksekød & Kalv',    ('okse', 'kalv', 'oksekød', 'entrecôte', 'ribeye', 'mørbrad', 'cuvette', 'oksesteg', 'tyksteg')),
        ('Svinekød',          ('svin', 'svinekød', 'nakkefilet', 'koteletter', 'flæsk', 'bacon', 'ribbensteg', 'svinesteg', 'svinemørbrad')),
        ('Fjerkræ',           ('kylling', 'kalkun', 'and ', 'ande', 'poussin')),
        ('Lam & Vildt',       ('lam', 'lammekød', 'vildt', 'hjort', 'rådyr', 'kanin')),
        ('Fisk & Skaldyr',    ('fisk', 'laks', 'torsk', 'tun', 'makrel', 'sild', 'rejer', 'muslinger', 'krabbe', 'blæksprutte', 'rødspætte', 'tilapia', 'pangasius', 'sei', 'kuller', 'ørred', 'aborre', 'helleflynder', 'hornfisk')),
        ('Pølser',            ('pølse', 'medister', 'grillpølse', 'hotdog', 'chorizo', 'pepperoni')),
    ],
    CAT_FRUGT_GROENT: [
        ('Frugt',         ('æble', 'pære', 'banan', 'appelsin', 'citron', 'lime', 'grape', 'melon', 'jordbær', 'hindbær', 'blåbær', 'mango', 'ananas', 'kiwi', 'fersken', 'nektarin', 'blomme', 'kirsebær', 'druer', 'avocado', 'kokos', 'papaya', 'klementin', 'mandarin', 'granatæble')),
        ('Grøntsager',    ('salat', 'spinat', 'grønkål', 'hvidkål', 'rødkål', 'broccoli', 'blomkål', 'gulerod', 'løg', 'kartofler', 'tomat', 'agurk', 'peberfrugt', 'zucchini', 'aubergine', 'selleri', 'fennikel', 'porrer', 'asparges', 'roer', 'radiser', 'majs', 'ærter', 'bønner', 'pastinak', 'rucola')),
        ('Svampe',        ('champignon', 'svampe', 'shiitake', 'portobello', 'østershat')),
        ('Krydderurter',  ('basilikum', 'persille', 'koriander', 'rosmarin', 'timian', 'mynte', 'estragon', 'oregano', 'dild', 'purløg', 'salvie')),
    ],
    CAT_BROED_KAGER: [
        ('Rugbrød & Knækbrød', ('rugbrød', 'knækbrød', 'rugmel')),
        ('Brød',               ('franskbrød', 'toastbrød', 'sandwichbrød', 'ciabatta', 'surdejsbrød', 'fuldkornsbrød', 'baguette', 'flutes', 'pita', 'focaccia', 'brød')),
        ('Boller',             ('boller', 'rundstykker', 'burgerboller', 'miniboller')),
        ('Kager & Wienerbrød', ('kage', 'wienerbrød', 'croissant', 'kanelsneglen', 'tebirkes', 'spandauer', 'muffin', 'tærte', 'lagkage', 'brownie', 'cheesecake', 'romkugle')),
        ('Kiks & Vafler',      ('kiks', 'crackers', 'vafler', 'riskager', 'digestive')),
        ('Bagning',            ('mel', 'hvedemel', 'gær', 'bagepulver', 'natron', 'majsstivelse')),
    ],
    CAT_FROST: [
        ('Is & Desserter',        ('is', 'flødeis', 'mælkeis', 'sorbetis', 'ispinde', 'islagkage', 'dessert', 'tiramisu', 'macarons', 'fondant', 'æbleskiver')),
        ('Frossen Fisk',          ('fisk', 'rejer', 'laks', 'torsk', 'rødspætte', 'sei', 'pangasius', 'tilapia', 'fiskepinde', 'panerede', 'tempura')),
        ('Frossen Kød',           ('kød', 'kylling', 'burger', 'bøf', 'frikadeller', 'kødboller', 'karbonader', 'hakket', 'pølse', 'medister')),
        ('Frossen Grønt & Frugt', ('ærter', 'majs', 'broccoli', 'spinat', 'bønner', 'grøntsags', 'edamame', 'mukimame', 'blåbær', 'jordbær', 'hindbær', 'brombær')),
        ('Frost Brød',            ('brød', 'boller', 'baguette', 'croissant', 'tebirkes', 'bagels', 'focaccia')),
        ('Færdigretter',          ('lasagne', 'pizza', 'tikka masala', 'butter chicken', 'boller i karry', 'spaghetti bolognese', 'karbonade', 'risotto', 'wok', 'gratin')),
    ],
    CAT_KOLONIAL: [
        ('Pasta & Ris',           ('pasta', 'spaghetti', 'penne', 'fusilli', 'rigatoni', 'lasagne plader', 'tagliatelle', 'fettuccine', 'nudler', 'macaroni', 'couscous', 'quinoa', 'bulgur', 'polenta', 'basmati', 'jasminris', 'risotto', ' ris ')),
        ('Konserves & Dåse',      ('dåse', 'konserves', 'kikærter', 'linser', 'kidneybønner', 'hvidebønner', 'flåede tomater', 'tomatpuré', 'rødbeder', 'sylte', 'syltede', 'majs', 'asparges', 'champignon', 'artiskok', 'dåseoliven', ' oliven ', 'sardiner', 'tun i ', 'makrel i ', 'ansjoser')),
        ('Morgenmad',             ('havregryn', 'müsli', 'granola', 'cornflakes', 'morgenmad', 'grød', 'chiafrø', 'hørfrø', 'fiberhusk')),
        ('Krydderier & Sauce',    ('krydderi', ' salt ', 'peber', 'chili', 'paprika', 'karry', 'sauce', 'ketchup', 'sennep', 'mayonnaise', 'dressing', 'bouillon', 'fond', 'soyasauce', 'pesto', 'sambal', 'tabasco', 'teriyaki')),
        ('Olie & Eddike',         ('olie', 'olivenolie', 'rapsolie', 'solsikkeolie', 'eddike', 'balsamico')),
        ('Nødder & Tørret Frugt', ('nødder', 'mandler', 'cashew', 'valnødder', 'hasselnødder', 'pistacier', 'jordnødder', 'rosiner', 'dadler', 'tørrede')),
        ('Bagning & Sødning',     ('mel ', 'sukker', 'melis', 'bagepulver', 'vanilje', 'honning', 'marmelade', 'syltetøj', 'nutella', 'peanutbutter', 'kakao', 'sødetabl', 'sødemiddel', 'stevia', 'sukrinol', 'canderel')),
        ('Supper & Snacks',       ('suppe', 'suppefond', 'popcorn', 'chips', 'nachos', 'kiks', 'cracker')),
    ],
    CAT_SLIK: [
        ('Chokolade',      ('chokolade', 'praliner', 'trøfler', 'bounty', 'snickers', 'twix', 'kit kat', 'mars', 'milka', 'toblerone', 'ferrero')),
        ('Slik & Vingummi',('vingummi', 'lakrids', 'skumfiduser', 'bolsjer', 'karameller', 'gummi', 'haribo', 'pastiller', 'tyggegummi', 'guf', 'skum')),
        ('Chips & Snacks', ('chips', 'popcorn', 'nachos', 'majschips', 'tortillachips', 'linsechips', 'jordnøddesnack')),
        ('Proteinbarer',   ('proteinbar', 'energibar', 'müslibar', 'snackbar', 'protein')),
    ],
}


def _get_subcategory(name: str, category: str) -> str:
    rules = _SUBCATEGORY_RULES.get(category)
    if not rules:
        return ''
    name_lower = name.lower()
    for sub_name, keywords in rules:
        if any(kw in name_lower for kw in keywords):
            return sub_name
    return 'Øvrige'


_UNIT_WORDS = {'g', 'kg', 'l', 'ml', 'cl', 'dl', 'stk', 'pak', 'ltr', 'pcs'}


def _product_type_words(name: str) -> set[str]:
    words = normalize_name(name).split()
    if not words:
        return set()
    if len(words) == 1:
        return {words[0]} if len(words[0]) >= 3 else set()
    return {w for w in words[1:] if len(w) >= 4 and not re.match(r'^\d', w) and w not in _UNIT_WORDS}


# ---------------------------------------------------------------------------
# Bilka category rules (keyword fallback)
# ---------------------------------------------------------------------------

_BILKA_CATEGORY_RULES = [
    (CAT_DRIKKEVARER,  ('cola', 'sodavand', 'juice', 'energidrik', 'øl', 'vin', 'spiritus', 'smoothie', 'vand', 'saft', 'cider', 'whisky', 'vodka', 'gin', 'rom', 'tequila', 'likør', 'akvavit', 'champagne', 'prosecco', 'cava', 'iste', 'sportsdrik', 'ingefærshot', 'kombucha', 'kokosvand', 'shots', 'frugtdrik', 'blanding', 'sirup', 'drik', 'lemonade', 'breezer', 'smirnoff', 'sangria', 'hvidvin', 'rødvin', 'rosévin', 'pilsner', 'bitter', 'tonic')),
    (CAT_FROST,        ('pommes frites', 'kyllingenuggets', 'frikadeller', 'flødeis', 'mælkeis', 'sorbetis', 'ispinde', 'isvafler', 'pizza m.', 'fuldkornsboller', 'håndværkere', 'miniflutes', 'croissanter', 'pain au chocolat', 'kanelsnegle', 'tebirkes', 'surdejsstykker', 'baguettes', 'focaccia m.', 'boller m.', 'bagels', 'grøntsagsblanding', 'bærblanding', 'blåbær', 'jordbær', 'hindbær', 'brombær', 'frys-selv', 'frossen', 'mukimame', 'edamame', 'kartoffelriste', 'kartoffelkroketter', 'løgringe', 'fiskepinde', 'panerede', 'rejenuggets', 'tempurarejer', 'butterfly rejer', 'vannamei rejer', 'grønlandske rejer', 'dumplings', 'gyoza', 'forårsruller', 'samosa', 'falafler', 'kødboller', 'melboller', 'karbonader', 'burgerbøffer', 'tikka masala m.', 'butter chicken m.', 'lasagne bolognese', 'spaghetti bolognese', 'karbonade m.', 'boller i karry m. ris', 'kylling i', 'flødeisvafler', 'mælkeis sandwich', 'limonadeis', 'islagkage', 'chokoladefondant', 'tiramisu', 'æbleskiver', 'æbleskiver m.', 'æblekage', 'skovbærtærte', 'citrontærte', 'cheesecake 2 stk', 'sacher 2 stk', 'tærte', 'macarons', 'pølsehorn', 'møllehjul', 'astronautis', "carte d'or")),
    (CAT_SLIK,         ('chips m.', 'majschips', 'linsechips', 'rodfrugtchips', 'popcorn', 'skumfiduser', 'vingummi', 'lakrids', 'chokoladebar', 'mælkechokolade', 'mørk chokolade', 'hvid chokolade', 'karameller', 'bolcher', 'pastiller', 'tyggegummi', 'müslibar', 'frugtsnacks', 'frugtstænger', 'rosiner', 'nøddeblanding', 'peanuts', 'flæskesvær', 'saltsnacks', 'saltstænger', 'marcipanbrød', 'vingummibamser', 'skumbananer', 'ostepops', 'dipmix', 'click mix', 'matador mix', 'stjerne mix', 'favorit mix', 'beef jerky', 'tørret mango', 'tørrede', 'rawbar', 'daddelbar', 'müslibarer', 'chokoladekugler', 'lakridsstænger', 'chips', 'osterejer', 'blandede chokolader')),
    (CAT_BROED_KAGER,  ('rugbrød', 'toastbrød', 'sandwichbrød', 'burgerboller', 'hotdogbrød', 'pølsebrød', 'baguette', 'pitabrød', 'naanbrød', 'knækbrød', 'digestive kiks', 'mariekiks', 'havrekiks', 'kiks m.', 'cookies m.', 'kiks', 'prince', 'fuldkornsboller', 'solsikkeboller', 'rugboller', 'sandwichboller', 'hvedeboller', 'yoghurtboller', 'krydderboller', 'surdejsbrød', 'focaccia', 'ciabatta', 'grissini', 'rasp', 'tarteletter', 'lagkagebunde', 'tærtebund', 'vafler', 'isvafler', 'bondebrød', 'schwarzbrot', 'fladbrød', 'tortillas', 'tortillachips', 'pitabrød', 'fastelavnsbolle', 'boller', 'brød', 'bagels', 'citronmåne', 'romkugler', 'drømmekage', 'kanelstang', 'daim mini', 'mazarinkager', 'kammerjunkere', 'brownie', 'muffins', 'chokoladekage', 'citronkage', 'marmorkage', 'sandkage', 'gulerodskage', 'hindbærroulade', 'roulade', 'vaniljekranse', 'honningsnitter', 'småkager', 'tvebakker', 'pumpernickel', 'grovboller', 'proteinboller', 'proteinbrød', 'gulerodsboller', 'fuldkornssandwichbrød', 'skagensbrød', 'brioche', 'pølsehornsdej', 'pizzadej', 'butterdej', 'croissantdej', 'tærtedej', 'fuldkornspizzabunde', 'surdejspizzadej', 'surdejsboller')),
    (CAT_MEJERI,       ('mælk', 'smør', 'piskefløde', 'skyr', 'yoghurt', 'kefir', 'fraiche', 'creme fraiche', 'kærnemælk', 'ymer', 'bagegær', 'æg', 'havredrik', 'sojadrik', 'mandeldrik', 'risdrik', 'oatly', 'flydende til madlavning', 'stegemargarine', 'plantemargarine', 'smørbar', 'danbo', 'havarti', 'cheddar', 'mozzarella', 'brie', 'camembert', 'feta', 'gorgonzola', 'emmentaler', 'gouda', 'ricotta', 'mascarpone', 'burrata', 'parmesan', 'parmigiano', 'grana padano', 'pecorino', 'manchego', 'jarlsberg', 'samsø ost', 'danablu', 'blåskimmelost', 'rygeost', 'smøreost', 'flødeost', 'ostehaps', 'ostetern', 'salatost', 'hytteost', 'halloumi', 'gruyere', 'comté', 'port salut', 'præst', 'rødkitost')),
    (CAT_KOLONIAL,     ('pasta', 'ris', 'mel', 'sukker', 'olie', 'sauce', 'ketchup', 'marmelade', 'konserves', 'havregryn', 'müsli', 'musli', 'granola', 'bouillon', 'krydderi', 'sennep', 'mayonnaise', 'remoulade', 'dressing', 'tun i', 'makrel i', 'sardiner', 'oliven', 'kapers', 'pesto', 'tomatsauce', 'passata', 'hakkede tomater', 'tomatpuré', 'pizzasauce', 'bechamelsauce', 'hollandaise', 'bearnaisesauce', 'honning', 'sirup', 'eddike', 'cornflakes', 'frosties', 'coco pops', 'cheerios', 'havrefras', 'fiberknas', 'guldkorn', 'risottoris', 'basmatiris', 'jasminris', 'parboiled', 'fusilli', 'spaghetti', 'penne', 'lasagneplader', 'tagliatelle', 'gnocchi', 'instant kaffe', 'formalet kaffe', 'hele bønner', 'kaffekapsler', 'te', 'bagepulver', 'vaniljesukker', 'chiafrø', 'hørfrø', 'solsikkekerner', 'valnødder', 'cashewnødder', 'mandler', 'pinjekerner', 'pistaciekerner', 'kokosmel', 'kokosmælk', 'sojasauce', 'woksauce', 'tortillas', 'tacosauce', 'tortillachips', 'nudler', 'risnudler', 'hvedenudler', 'glasnudler', 'chilisauce', 'teriyaki', 'boller i karry', 'lasagne', 'spaghetti bolognese', 'pasta carbonara', 'burger', 'frokostplatte', 'kylling tikka masala', 'tikka masala', 'butter chicken', 'tarteletfyld', 'biksemad', 'millionbøf', 'flæskestegsburger', 'schnitzel m. tilbehør', 'karbonader m.', 'frikadeller m.', 'hakkebøffer m.', 'kartoffelmos m.', 'boller i karry m.', 'kylling i karry', 'kylling i rød', 'kylling m. ris', 'pasta m. kylling', 'pasta bolognese', 'mørbradgryde', 'paprikagryde', 'goulash', 'forloren hare', 'wienergryde', 'jægergryde', 'gyros m.', 'kyllingewok', 'ris m. kylling', 'risotto m.')),
    (CAT_FRUGT_GROENT, ('agurk', 'bananer', 'banan', 'peberfrugt', 'tomat', 'gulerødder', 'gulerod', 'salat', 'broccoli', 'blomkål', 'æbler', 'æble', 'pærer', 'pære', 'appelsin', 'citron', 'jordbær', 'hindbær', 'kål', 'rødkål', 'hvidkål', 'spidskål', 'løg', 'rødløg', 'forårsløg', 'kartofler', 'kartoffel', 'squash', 'avocado', 'spinat', 'svampe', 'champignon', 'melon', 'druer', 'mango', 'ananas', 'blåbær', 'brombær', 'solbær', 'tranebær', 'klementiner', 'kiwi', 'lime', 'citrongræs', 'ingefær', 'hvidløg', 'purløg', 'persille', 'dild', 'basilikum', 'rosmarin', 'timian', 'asparges', 'artiskok', 'selleri', 'pastinak', 'persillerod', 'rødbeder', 'jordskokkerne', 'aubergine', 'courgette', 'rosenkål', 'grønkål', 'rucola', 'feldsalat', 'icebergsalat', 'romainesalat', 'pak choi', 'sugarsnaps', 'ærter', 'bobbybønner', 'sukkerærter', 'vandmelon', 'papaya', 'dadler', 'figner', 'granatæble', 'coconut', 'passionsfrugt', 'mandariner', 'klementiner', 'nektariner', 'abrikoser', 'blomme', 'kirsebær', 'vindruer', 'hokkaido', 'butternut')),
]


def unify_category(raw_cat, product_name=''):
    """Maps any store category or product name to a standard website category.

    Returnerer None hvis varen ikke er mad — så filtreres den fra på hjemmesiden.
    """
    raw = str(raw_cat or '').lower().strip()
    name = str(product_name or '').lower().strip()

    # Krav: kun mad — ingen undtagelser. Klart ikke-mad (navn) frasorteres straks.
    if name and _NON_FOOD_NAME_RE.search(name):
        return None

    if 'prince' in name:
        return CAT_BROED_KAGER
    if 'lolly' in name or 'frys-selv' in name or 'ispind' in name:
        return CAT_FROST

    if 'kiosk' in raw and name:
        _kiosk_drink = ('cola', 'sodavand', 'juice', 'energidrik', 'energy drink', 'øl', 'vin', 'cider', 'vand', 'saft', 'iste', 'ice tea', 'sportsdrik', 'kombucha', 'drik', 'lemonade', 'shots', 'smoothie', 'frugtdrik', 'breezer', 'kokosvand')
        _kiosk_slik  = ('chips', 'popcorn', 'nachos', 'majschips', 'tortillachips', 'chokolade', 'slik', 'vingummi', 'lakrids', 'skumfiduser', 'bolsjer', 'karameller', 'nødder', 'jordnødder', 'guf', 'tyggegummi', ' gum', 'gum ', 'skum', 'orbit', 'stimorol', 'dirol', 'mentos', 'hubba bubba', 'wrigley')
        _kiosk_mejeri= ('coleslaw', 'waldorf', 'hummussalat', 'pastasalat', 'kartoffelsalat', 'grøn salat', 'salat ')
        if any(kw in name for kw in _kiosk_drink):  return CAT_DRIKKEVARER
        if any(kw in name for kw in _kiosk_slik):   return CAT_SLIK
        if any(kw in name for kw in _kiosk_mejeri): return CAT_MEJERI

    mapping = {
        'mejeri': CAT_MEJERI, 'mejeriprodukter & kølvarer': CAT_MEJERI,
        'pålæg og kølede middagsretter': CAT_MEJERI, 'køl': CAT_MEJERI,
        'ost': CAT_MEJERI, 'ost m.v.': CAT_MEJERI,
        'kød': CAT_KOED_FISK, 'fisk og skaldyr': CAT_KOED_FISK,
        'kød, fisk & fjerkræ': CAT_KOED_FISK, 'kød fisk fjerkræ': CAT_KOED_FISK,
        'frugt & grønt': CAT_FRUGT_GROENT, 'frugt og grønt': CAT_FRUGT_GROENT,
        'brød & kager': CAT_BROED_KAGER, 'brød og kager': CAT_BROED_KAGER,
        'brød & bavinchi': CAT_BROED_KAGER,
        'frost': CAT_FROST,
        'kolonial': CAT_KOLONIAL, 'kolonialvarer': CAT_KOLONIAL,
        'drikkevarer': CAT_DRIKKEVARER, 'vin og spiritus': CAT_DRIKKEVARER,
        'personlig pleje': None, 'pleje': None, 'husholdning': None,
        'rengøring': None, 'baby og småbørn': None,
        'kiosk': CAT_DRIKKEVARER, 'kiosk - slik og snack - chips og snacks': CAT_SLIK,
        'slik': CAT_SLIK, 'slik & snacks': CAT_SLIK, 'slik og snacks': CAT_SLIK,
        'kiosk - slik og snack - chokolade': CAT_SLIK, 'kiosk - slik og snack - slik': CAT_SLIK,
        'frugt-og-groent': CAT_FRUGT_GROENT, 'mejeri-og-koel': CAT_MEJERI,
        'slik-og-snacks': CAT_SLIK, 'broed-og-kager': CAT_BROED_KAGER,
        'koed-og-fisk': CAT_KOED_FISK, 'mad-fra-hele-verden': CAT_KOLONIAL,
        'ispinde-og-sodavandsis': CAT_FROST, 'is-i-baeger': CAT_FROST,
        'frys-selv-is': CAT_FROST, 'isvafler': CAT_FROST,
        'desserter-og-islagkager': CAT_FROST, 'groentsager': CAT_FROST,
        'faerdigretter-paa-frost': CAT_FROST, 'frugt-og-baer': CAT_FROST,
        'kartofler-og-pommes-frites': CAT_FROST,
        'avis': CAT_ANDET,
    }
    if raw in mapping:
        return mapping[raw]
    for cat_const, keywords in _BILKA_CATEGORY_RULES:
        if any(kw in name for kw in keywords):
            return cat_const
    return CAT_KOLONIAL if raw else CAT_ANDET


# ---------------------------------------------------------------------------
# Product display helpers
# ---------------------------------------------------------------------------

def parse_sale_end_date(product: dict) -> str | None:
    sale_dates = str(product.get('/product/sale_price_effective_date', '')).split('/')
    if len(sale_dates) <= 1:
        return None
    try:
        date_obj = datetime.strptime(sale_dates[1].strip(), '%Y-%m-%dT%H:%M:%S%z')
        return date_obj.strftime('%d/%m')
    except (ValueError, TypeError):
        return None


def product_to_display_dict(
    product: dict,
    *,
    category: str | None = None,
    sale_end_date: str | None = None,
    default_category: str = 'Andre varer',
    force_sale: bool = False,
) -> dict:
    sale_price = product.get('/product/sale_price')
    ptype = category or product.get('/product/product_type') or default_category
    name_str = str(product.get('/product/title', 'Ukendt vare'))
    unit_measure = str(product.get('/product/unit_pricing_measure', '') or '')
    is_sale = force_sale or sale_price is not None
    result = {
        'id': str(product.get('/product/id', '')),
        'name': name_str,
        'price': float(product.get('/product/price', 0)),
        'sale_price': float(sale_price) if sale_price is not None else None,
        'description': str(product.get('/product/description', '')),
        'category': str(ptype),
        'brand': str(product.get('/product/brand', '')),
        'image_url': str(product.get('/product/imageLink', '')),
        'rema_image': product.get('/product/rema_image', ''),
        'is_sale': is_sale,
        'is_any_sale': product.get('/product/is_any_sale', False),
        'sale_end_date': sale_end_date if sale_end_date is not None else parse_sale_end_date(product),
        'store': str(product.get('/product/store', 'Rema 1000')),
        'unit_measure': unit_measure,
        'weight_g': parse_weight_to_grams(unit_measure),
        'stk_count': product.get('/product/stk_count') or parse_stk_count(unit_measure),
        'price_per_kg': product.get('/product/price_per_kg'),
        'store_matches': product.get('/product/store_matches', {}),
        'cheaper_at': product.get('/product/cheaper_at'),
        'cheapest_at': product.get('/product/cheapest_at'),
        'rema_price': product.get('/product/rema_price'),
        'rema_is_sale': product.get('/product/rema_is_sale'),
        'multi_deal': product.get('/product/multi_deal', ''),
        'subcategory': _get_subcategory(name_str, str(ptype)),
    }
    if not is_sale:
        result['sale_end_date'] = sale_end_date
    return result


def product_available_at_active_stores(product: dict, active_stores: set | None) -> bool:
    if active_stores is None:
        return True
    if len(active_stores) == 0:
        return False
    display_store = product.get('/product/store', 'Rema 1000')
    if display_store in active_stores:
        return True
    if 'Rema 1000' in active_stores and product.get('/product/rema_price'):
        return True
    for key in (product.get('/product/store_matches') or {}):
        label = _STORE_CONFIGS.get(key, {}).get('label')
        if label in active_stores:
            return True
    return False


def _promote_match_to_product(product: dict, store_key: str, match: dict) -> dict:
    out = dict(product)
    out['/product/title'] = match['name']
    out['/product/store'] = _STORE_CONFIGS[store_key]['label']
    if match.get('is_sale'):
        out['/product/price'] = match.get('normal_price') or match['price']
        out['/product/sale_price'] = match['price']
    else:
        out['/product/price'] = match['price']
        out['/product/sale_price'] = None
    if match.get('image') and str(match['image']).lower() != 'nan':
        out['/product/imageLink'] = match['image']
    out['/product/brand'] = match.get('brand') or ''
    out['/product/description'] = match.get('description') or ''
    out['/product/unit_pricing_measure'] = match.get('weight') or out.get('/product/unit_pricing_measure')
    out['/product/price_per_kg'] = match.get('kg_price')
    out['/product/multi_deal'] = match.get('multi_deal', '')
    out['/product/cheapest_at'] = store_key
    new_type = unify_category(match.get('Kategori', ''), match['name'])
    if new_type and new_type != CAT_ANDET:
        out['/product/product_type'] = new_type
    return out


def product_for_active_stores(product: dict, active_stores: set | None) -> dict | None:
    if not product_available_at_active_stores(product, active_stores):
        return None
    if active_stores is None or 'Rema 1000' in active_stores:
        return product
    display_store = product.get('/product/store', 'Rema 1000')
    if display_store in active_stores:
        return product
    matches = product.get('/product/store_matches') or {}
    best_key = None
    best_price = None
    for key, match in matches.items():
        label = _STORE_CONFIGS.get(key, {}).get('label')
        if label not in active_stores:
            continue
        try:
            price = float(match.get('price', 0))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        if best_price is None or price < best_price:
            best_price = price
            best_key = key
    if best_key:
        return _promote_match_to_product(product, best_key, matches[best_key])
    return None
