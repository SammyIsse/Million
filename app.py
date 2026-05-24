from flask import Flask, render_template, send_from_directory, jsonify, request, redirect, url_for
import requests
import re
from datetime import datetime, timedelta
import os
import json
from dotenv import load_dotenv
load_dotenv()
import random
import time
from rapidfuzz.fuzz import ratio as rapid_ratio
import unicodedata
import threading
import urllib.parse

from app_support import (
    configure_logging,
    is_price_db_enabled,
    set_db_available,
    db_available,
    rate_limit,
    api_limiter,
    build_search_index,
    search_product_ids,
    product_matches_query,
    logger,
)

configure_logging()

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# HTTP headers to improve compatibility with sites that gate content by user-agent
DEFAULT_HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'da,da-DK;q=0.9,en;q=0.8',
}

# Cache configuration
CACHE_DURATION = timedelta(hours=6)
cached_data = {
    'timestamp': None,
    'data': None,
    'search_index': None,
}
_cache_refresh_started = False
_cache_refresh_lock = threading.Lock()


def format_price(price_str):
    """Format price string to float"""
    if not price_str:
        return 0.0
    try:
        # Remove currency and whitespace
        cleaned = price_str.replace('DKK', '').replace('kr', '').replace(',', '.').strip()
        return float(cleaned)
    except (ValueError, TypeError):
        logger.error(f"Error converting price: {price_str}")
        return 0.0

_STORE_CONFIGS = {
    'rema': {
        'db_key':     None,
        'label':      'Rema 1000',
        'logo':       '/static/images/Rema1000-logo.png',
    },
    'bilka': {
        'db_key':     'Bilka',
        'label':      'Bilka',
        'logo':       '/static/images/bilka-logo.png',
    },
    'mk': {
        'db_key':     'minkøbmand',
        'label':      'Min Købmand',
        'logo':       '/static/images/Min_kobmand_logo.png',
    },
    'meny': {
        'db_key':     'Meny',
        'label':      'Meny',
        'logo':       '/static/images/meny-logo.png',
    },
    'spar': {
        'db_key':     'Spar',
        'label':      'Spar',
        'logo':       '/static/images/spar-logo.png',
    },
    'sb': {
        'db_key':     'SuperBrugsen',
        'label':      'SuperBrugsen',
        'logo':       '/static/images/superbrugsen-logo.png',
    },
    'brugsen': {
        'db_key':     'Brugsen',
        'label':      'Brugsen',
        'logo':       '/static/images/brugsen-logo.png',
    },
    'kvickly': {
        'db_key':     'Kvickly',
        'label':      'Kvickly',
        'logo':       '/static/images/kvickly-logo.png',
    },
    'discount365': {
        'db_key':     '365discount',
        'label':      '365 Discount',
        'logo':       '/static/images/365discount-logo.png',
    },
}

_xml_cache_lock = threading.Lock()

# Pre-kompilerede regex til normalize_name — bygges én gang ved opstart
_ABBREV_COMPILED: list[tuple] = [
    (re.compile(r'\bsr\b'),    'sour'),
    (re.compile(r'\bsc\b'),    'sour cream'),
    (re.compile(r'\bonion\b'), 'onion'),
    (re.compile(r'\bo\b'),     'onion'),
    (re.compile(r'\bhk\b'),    'hakket'),
    (re.compile(r'\bmin\b'),   'mini'),
    (re.compile(r'\bøko\b'),   'okologisk'),
    (re.compile(r'\borg\b'),   'okologisk'),
]
_OKOLOGISK_RE = re.compile(r'\bokologisk\b')


def normalize_name(name):
    """Lowercase, strip diacritics and noise for fuzzy comparison."""
    if not name or str(name) == 'nan':
        return ''
    name = str(name).lower().strip()
    name = unicodedata.normalize('NFKD', name)
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
    # Normalise separators before stripping noise
    name = name.replace('&', 'and').replace('+', 'and')
    # Expand common Danish grocery abbreviations (pre-compiled at module level)
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
    # Max possible ratio is 2 * min / sum. Skip rapidfuzz for impossible pairs.
    if (2.0 * min(la, lb) / (la + lb)) < 0.35:
        return 0.0
        
    return rapid_ratio(a, b) / 100.0


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

def parse_stk_count(weight_str) -> int | None:
    """Return the piece count if weight_str denotes a stk/st unit, else None.
    E.g. '4 stk' → 4, '1 ST' → 1, '500 g' → None.
    """
    if not weight_str or str(weight_str).strip().lower() in ('nan', '', 'none'):
        return None
    s = str(weight_str).strip().lower().replace(',', '.')
    m = re.match(r'^([\d.]+)\s*st[k]?$', s)
    if not m:
        return None
    try:
        return int(float(m.group(1)))
    except ValueError:
        return None

def weights_compatible(w_a: float | None, w_b: float | None, tolerance: float = _WEIGHT_TOLERANCE_G) -> bool:
    """Return True when both weights are known and within *tolerance* of each other,
    OR when either weight is unknown (we cannot rule out a match)."""
    if w_a is None or w_b is None:
        return True  # unknown weight → do not discard the candidate
    return abs(w_a - w_b) <= tolerance






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
    'cigar',
    'snus',
    'nikotin',
    'tændstik',
    'lighter',
    'fyrstikker',
    'marlboro',
    'winston',
    'camel',
    'skjold rød',
    'skjold blå',
    'skjold grå',
    "king's",
    'prince filter',
    'prince røg',
    # Blade, aviser og ikke-madrelaterede kiosk-varer
    'hjemmet',
    'søndag',
    'hendes verden',
    'her og nu',
    'billed bladet',
    'billedbladet',
    'se og hør',
    'ude og hjemme',
    'ude & hjemme',
    '7-tv-dage',
    'alt for damerne',
    'anders and',
    'zapp elektron',
    'piberensere',
    'ekstra bladet',
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
    'https://rema-product-images.digital.rema1000.dk/521363/1-large-rDq68WajPb.webp',
    'https://rema-product-images.digital.rema1000.dk/521374/1-large-869DBK5MoM.webp',
}

# Standard categories used across the site
CAT_MEJERI = 'Køl'
CAT_KOED_FISK = 'Kød & Fisk'
CAT_FRUGT_GROENT = 'Frugt & Grønt'
CAT_BROED_KAGER = 'Brød & Kager'
CAT_FROST = 'Frost'
CAT_KOLONIAL = 'Kolonial'
CAT_DRIKKEVARER = 'Drikkevarer'
CAT_SLIK = 'Slik'
CAT_ANDET = 'Andre varer'

# ---------------------------------------------------------------------------
# Subcategory keyword rules — ordered, first match wins
# ---------------------------------------------------------------------------
_SUBCATEGORY_RULES: dict[str, list[tuple[str, tuple]]] = {
    CAT_DRIKKEVARER: [
        ('Øl & Cider',        (' øl', 'øl ', 'pilsner', 'lager', ' ale ', 'ipa', 'stout', 'porter',
                                'cider', 'radler', 'breezer', 'pils ')),
        ('Vin & Spiritus',    ('hvidvin', 'rødvin', 'rosé', 'prosecco', 'champagne', 'cava', 'sangria',
                                'whisky', 'whiskey', 'vodka', ' gin ', ' rom ', 'tequila', 'likør',
                                'akvavit', 'spiritus', 'cognac', 'brandy', 'cointreau', 'baileys',
                                ' vin ', 'vin,')),
        ('Kaffe & Te',        ('kaffe', 'espresso', 'cappuccino', 'kaffekapsler', 'nespresso',
                                ' te ', 'te,', 'tebreve', 'chai', 'urtete', 'grøn te', 'matcha')),
        ('Juice & Smoothie',  ('juice', 'smoothie', 'nektar', 'frugtdrik', 'kokosvand')),
        ('Saft & Sirup',      ('saft', 'sirup', 'squash', 'koncentrat')),
        ('Vand',              ('mineralvand', 'kildevand', 'danskvand', ' vand', 'vand ')),
        ('Sodavand & Energi', ('cola', 'sodavand', 'energidrik', 'energy drink', 'sportsdrik',
                                'red bull', 'redbull', 'monster ', 'iste', 'ice tea',
                                'lemonade', 'tonic', 'kombucha')),
    ],
    CAT_MEJERI: [
        ('Mælk & Fløde',      ('mælk', 'fløde', 'halvfløde', 'kærnemælk', 'kefir', 'havremælk',
                                'mandelmælk', 'sojamælk', 'rismælk')),
        ('Yoghurt & Kvark',   ('yoghurt', 'skyr', 'kvark', 'ymer', 'fromage', 'fraiche', 'creme fraiche')),
        ('Ost',               ('ost', 'brie', 'camembert', 'gouda', 'cheddar', 'parmesan', 'fetaost',
                                'feta', 'mozzarella', 'ricotta', 'hytteost', 'danbo', 'esrom', 'castello')),
        ('Smør & Fedtstof',   ('smør', 'margarine', 'plantesmør', 'bregott', 'lurpak')),
        ('Æg',                ('æg',)),
        ('Pålæg & Kølvarer',  ('pålæg', 'leverpostej', 'postej', 'skinke', 'salami', 'rullepølse',
                                'spegepølse', 'mortadella', 'roastbeef', 'paté', 'pølse', 'hummus')),
    ],
    CAT_KOED_FISK: [
        ('Oksekød & Kalv',    ('okse', 'kalv', 'oksekød', 'entrecôte', 'ribeye', 'mørbrad',
                                'cuvette', 'oksesteg', 'tyksteg')),
        ('Svinekød',          ('svin', 'svinekød', 'nakkefilet', 'koteletter', 'flæsk', 'bacon',
                                'ribbensteg', 'svinesteg', 'svinemørbrad')),
        ('Fjerkræ',           ('kylling', 'kalkun', 'and ', 'ande', 'poussin')),
        ('Lam & Vildt',       ('lam', 'lammekød', 'vildt', 'hjort', 'rådyr', 'kanin')),
        ('Fisk & Skaldyr',    ('fisk', 'laks', 'torsk', 'tun', 'makrel', 'sild', 'rejer', 'muslinger',
                                'krabbe', 'blæksprutte', 'rødspætte', 'tilapia', 'pangasius', 'sei',
                                'kuller', 'ørred', 'aborre', 'helleflynder', 'hornfisk')),
        ('Pølser',            ('pølse', 'medister', 'grillpølse', 'hotdog', 'chorizo', 'pepperoni')),
    ],
    CAT_FRUGT_GROENT: [
        ('Frugt',             ('æble', 'pære', 'banan', 'appelsin', 'citron', 'lime', 'grape', 'melon',
                                'jordbær', 'hindbær', 'blåbær', 'mango', 'ananas', 'kiwi', 'fersken',
                                'nektarin', 'blomme', 'kirsebær', 'druer', 'avocado', 'kokos', 'papaya',
                                'klementin', 'mandarin', 'granatæble')),
        ('Grøntsager',        ('salat', 'spinat', 'grønkål', 'hvidkål', 'rødkål', 'broccoli', 'blomkål',
                                'gulerod', 'løg', 'kartofler', 'tomat', 'agurk', 'peberfrugt', 'zucchini',
                                'aubergine', 'selleri', 'fennikel', 'porrer', 'asparges', 'roer',
                                'radiser', 'majs', 'ærter', 'bønner', 'pastinak', 'rucola')),
        ('Svampe',            ('champignon', 'svampe', 'shiitake', 'portobello', 'østershat')),
        ('Krydderurter',      ('basilikum', 'persille', 'koriander', 'rosmarin', 'timian', 'mynte',
                                'estragon', 'oregano', 'dild', 'purløg', 'salvie')),
    ],
    CAT_BROED_KAGER: [
        ('Rugbrød & Knækbrød',('rugbrød', 'knækbrød', 'rugmel')),
        ('Brød',              ('franskbrød', 'toastbrød', 'sandwichbrød', 'ciabatta', 'surdejsbrød',
                                'fuldkornsbrød', 'baguette', 'flutes', 'pita', 'focaccia', 'brød')),
        ('Boller',            ('boller', 'rundstykker', 'burgerboller', 'miniboller')),
        ('Kager & Wienerbrød',('kage', 'wienerbrød', 'croissant', 'kanelsneglen', 'tebirkes', 'spandauer',
                                'muffin', 'tærte', 'lagkage', 'brownie', 'cheesecake', 'romkugle')),
        ('Kiks & Vafler',     ('kiks', 'crackers', 'vafler', 'riskager', 'digestive')),
        ('Bagning',           ('mel', 'hvedemel', 'gær', 'bagepulver', 'natron', 'majsstivelse')),
    ],
    CAT_FROST: [
        ('Is & Desserter',    ('is', 'flødeis', 'mælkeis', 'sorbetis', 'ispinde', 'islagkage',
                                'dessert', 'tiramisu', 'macarons', 'fondant', 'æbleskiver')),
        ('Frossen Fisk',      ('fisk', 'rejer', 'laks', 'torsk', 'rødspætte', 'sei', 'pangasius',
                                'tilapia', 'fiskepinde', 'panerede', 'tempura')),
        ('Frossen Kød',       ('kød', 'kylling', 'burger', 'bøf', 'frikadeller', 'kødboller',
                                'karbonader', 'hakket', 'pølse', 'medister')),
        ('Frossen Grønt & Frugt', ('ærter', 'majs', 'broccoli', 'spinat', 'bønner', 'grøntsags',
                                    'edamame', 'mukimame', 'blåbær', 'jordbær', 'hindbær', 'brombær')),
        ('Frost Brød',        ('brød', 'boller', 'baguette', 'croissant', 'tebirkes', 'bagels', 'focaccia')),
        ('Færdigretter',      ('lasagne', 'pizza', 'tikka masala', 'butter chicken', 'boller i karry',
                                'spaghetti bolognese', 'karbonade', 'risotto', 'wok', 'gratin')),
    ],
    CAT_KOLONIAL: [
        ('Pasta & Ris',       ('pasta', 'spaghetti', 'penne', 'fusilli', 'rigatoni', 'lasagne plader',
                                'tagliatelle', 'fettuccine', 'nudler', 'macaroni', 'couscous', 'quinoa',
                                'bulgur', 'polenta', 'basmati', 'jasminris', 'risotto', ' ris ')),
        ('Konserves & Dåse',  ('dåse', 'konserves', 'kikærter', 'linser', 'kidneybønner', 'hvidebønner',
                                'flåede tomater', 'tomatpuré', 'rødbeder', 'sylte', 'syltede',
                                'majs', 'asparges', 'champignon', 'artiskok', 'oliven',
                                'sardiner', 'tun', 'makrel', 'ansjoser')),
        ('Morgenmad',         ('havregryn', 'müsli', 'granola', 'cornflakes', 'morgenmad', 'grød',
                                'chiafrø', 'hørfrø', 'fiberhusk')),
        ('Krydderier & Sauce',('krydderi', ' salt ', 'peber', 'chili', 'paprika', 'karry', 'sauce',
                                'ketchup', 'sennep', 'mayonnaise', 'dressing', 'bouillon', 'fond',
                                'soyasauce', 'pesto', 'sambal', 'tabasco', 'teriyaki')),
        ('Olie & Eddike',     ('olie', 'olivenolie', 'rapsolie', 'solsikkeolie', 'eddike', 'balsamico')),
        ('Nødder & Tørret Frugt', ('nødder', 'mandler', 'cashew', 'valnødder', 'hasselnødder',
                                    'pistacier', 'jordnødder', 'rosiner', 'dadler', 'tørrede')),
        ('Bagning & Sødning', ('mel ', 'sukker', 'melis', 'bagepulver', 'vanilje', 'honning',
                                'marmelade', 'syltetøj', 'nutella', 'peanutbutter', 'kakao',
                                'sødetabl', 'sødemiddel', 'stevia', 'sukrinol', 'canderel')),
        ('Supper & Snacks',   ('suppe', 'suppefond', 'popcorn', 'chips', 'nachos', 'kiks', 'cracker')),
    ],
    CAT_SLIK: [
        ('Chokolade',         ('chokolade', 'praliner', 'trøfler', 'bounty', 'snickers', 'twix',
                                'kit kat', 'mars', 'milka', 'toblerone', 'ferrero')),
        ('Slik & Vingummi',   ('vingummi', 'lakrids', 'skumfiduser', 'bolsjer', 'karameller',
                                'gummi', 'haribo', 'pastiller', 'tyggegummi', 'guf', 'skum')),
        ('Chips & Snacks',    ('chips', 'popcorn', 'nachos', 'majschips', 'tortillachips',
                                'linsechips', 'jordnøddesnack')),
        ('Proteinbarer',      ('proteinbar', 'energibar', 'müslibar', 'snackbar', 'protein')),
    ],
}


def _get_subcategory(name: str, category: str) -> str:
    """Return subcategory label for a product based on name keywords."""
    rules = _SUBCATEGORY_RULES.get(category)
    if not rules:
        return ''
    name_lower = name.lower()
    for sub_name, keywords in rules:
        if any(kw in name_lower for kw in keywords):
            return sub_name
    return 'Øvrige'


def _get_subcategory_keywords(name: str, category: str) -> set[str]:
    """Return the subcategory-defining keywords that appear in a product name."""
    rules = _SUBCATEGORY_RULES.get(category)
    if not rules:
        return set()
    name_lower = name.lower()
    for _, keywords in rules:
        matched = {kw.strip() for kw in keywords if kw in name_lower}
        if matched:
            return matched
    return set()


def parse_sale_end_date(product: dict) -> str | None:
    """Parse sale end date from raw product dict → dd/mm or None."""
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
    """Single canonical mapping from internal /product/* dict to template dict."""
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
    """True if the product can be bought at at least one selected store."""
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
    """Show a comparison-store match on the product card instead of Rema."""
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
    out['/product/brand'] = match.get('brand') or out.get('/product/brand')
    out['/product/unit_pricing_measure'] = match.get('weight') or out.get('/product/unit_pricing_measure')
    out['/product/price_per_kg'] = match.get('kg_price')
    out['/product/multi_deal'] = match.get('multi_deal', '')
    out['/product/cheapest_at'] = store_key
    new_type = unify_category(match.get('Kategori', ''), match['name'])
    if new_type and new_type != CAT_ANDET:
        out['/product/product_type'] = new_type
    return out


def product_for_active_stores(product: dict, active_stores: set | None) -> dict | None:
    """
    Adjust product for display when Rema is off: show Bilka/Meny/etc. instead of Rema badge.
    Returns None if the product is only available at Rema (or other deselected stores).
    """
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


def _filter_products_for_search(
    products: list, query: str, active_stores: set | None = None,
) -> list:
    """Use search index when available, else linear scan. Respects store selection."""
    def _to_display(raw: dict) -> dict | None:
        adjusted = product_for_active_stores(raw, active_stores)
        if not adjusted:
            return None
        return product_to_display_dict(adjusted, default_category='Andre varer')

    index = cached_data.get('search_index')
    if index:
        ids = search_product_ids(index, query)
        if ids is not None:
            id_set = ids
            results = []
            for p in products:
                if str(p.get('/product/id', '')) not in id_set:
                    continue
                if not p.get('/product/title') or not p.get('/product/id'):
                    continue
                d = _to_display(p)
                if d:
                    results.append(d)
            return results
    results = []
    for product in products:
        if not product.get('/product/title') or not product.get('/product/id'):
            continue
        d = _to_display(product)
        if d and product_matches_query(d, query):
            results.append(d)
    return results


def unify_category(raw_cat, product_name=''):
    """Maps any store category or product name to a standard website category."""
    raw = str(raw_cat or '').lower().strip()
    name = str(product_name or '').lower().strip()

    # Special overrides
    if 'prince' in name:
        return CAT_BROED_KAGER

    # Is-produkter der fejlkategoriseres af butikkernes egne kategorier
    if 'lolly' in name or 'frys-selv' in name or 'ispind' in name:
        return CAT_FROST

    if 'kiosk' in raw and name:
        _kiosk_drink_kws = (
            'cola', 'sodavand', 'juice', 'energidrik', 'energy drink',
            'øl', 'vin', 'cider', 'vand', 'saft', 'iste', 'ice tea',
            'sportsdrik', 'kombucha', 'drik', 'lemonade', 'shots',
            'smoothie', 'frugtdrik', 'breezer', 'kokosvand',
        )
        _kiosk_slik_kws = (
            'chips', 'popcorn', 'nachos', 'majschips', 'tortillachips',
            'chokolade', 'slik', 'vingummi', 'lakrids', 'skumfiduser',
            'bolsjer', 'karameller', 'nødder', 'jordnødder',
            'guf', 'tyggegummi', ' gum', 'gum ', 'skum',
            'orbit', 'stimorol', 'dirol', 'mentos', 'hubba bubba', 'wrigley',
        )
        _kiosk_mejeri_kws = (
            'coleslaw', 'waldorf', 'hummussalat', 'pastasalat',
            'kartoffelsalat', 'grøn salat', 'salat ',
        )
        if any(kw in name for kw in _kiosk_drink_kws):
            return CAT_DRIKKEVARER
        if any(kw in name for kw in _kiosk_slik_kws):
            return CAT_SLIK
        if any(kw in name for kw in _kiosk_mejeri_kws):
            return CAT_MEJERI
    
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
        'baby og småbørn': None,
        
        'kiosk': CAT_DRIKKEVARER,
        'kiosk - slik og snack - chips og snacks': CAT_SLIK,
        
        'slik': CAT_SLIK,
        'slik & snacks': CAT_SLIK,
        'slik og snacks': CAT_SLIK,
        'kiosk - slik og snack - chokolade': CAT_SLIK,
        'kiosk - slik og snack - slik': CAT_SLIK,

        # Bilka URL-slug varianter
        'frugt-og-groent': CAT_FRUGT_GROENT,
        'mejeri-og-koel': CAT_MEJERI,
        'slik-og-snacks': CAT_SLIK,
        'broed-og-kager': CAT_BROED_KAGER,
        'koed-og-fisk': CAT_KOED_FISK,
        'mad-fra-hele-verden': CAT_KOLONIAL,
        # Bilka Frost-underkategorier
        'ispinde-og-sodavandsis': CAT_FROST,
        'is-i-baeger': CAT_FROST,
        'frys-selv-is': CAT_FROST,
        'isvafler': CAT_FROST,
        'desserter-og-islagkager': CAT_FROST,
        'groentsager': CAT_FROST,
        'faerdigretter-paa-frost': CAT_FROST,
        'frugt-og-baer': CAT_FROST,
        'kartofler-og-pommes-frites': CAT_FROST,

        # COOP avis-scraper: ugentlige tilbudsaviser har ingen individuel kategori
        'avis': CAT_ANDET,
    }
    
    if raw in mapping:
        return mapping[raw]
        
    # 2. Fallback to keyword rules in name
    for cat_const, keywords in _BILKA_CATEGORY_RULES:
        if any(kw in name for kw in keywords):
            return cat_const
            
    return CAT_KOLONIAL if raw else CAT_ANDET

# ---------------------------------------------------------------------------
# Bilka display helpers
# ---------------------------------------------------------------------------

_BILKA_CATEGORY_RULES = [
    # (kategori-konstant, tuple af nøgleord der skal matche i produktnavnet)
    (CAT_DRIKKEVARER,      ('cola', 'sodavand', 'juice', 'energidrik', 'øl', 'vin', 'spiritus',
                            'smoothie', 'vand', 'saft', 'cider', 'whisky', 'vodka', 'gin',
                            'rom', 'tequila', 'likør', 'akvavit', 'champagne', 'prosecco',
                            'cava', 'iste', 'sportsdrik', 'ingefærshot', 'kombucha',
                            'kokosvand', 'shots', 'frugtdrik', 'blanding', 'sirup',
                            'drik', 'lemonade', 'breezer', 'smirnoff', 'sangria',
                            'hvidvin', 'rødvin', 'rosévin', 'pilsner', 'bitter', 'tonic')),
    (CAT_FROST,            ('pommes frites', 'kyllingenuggets', 'frikadeller', 'flødeis',
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
    (CAT_SLIK,             ('chips m.', 'majschips', 'linsechips', 'rodfrugtchips',
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
    (CAT_BROED_KAGER,      ('rugbrød', 'toastbrød', 'sandwichbrød', 'burgerboller',
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
    (CAT_MEJERI,           ('mælk', 'smør', 'piskefløde', 'skyr', 'yoghurt',
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
    (CAT_KOLONIAL,         ('pasta', 'ris', 'mel', 'sukker', 'olie', 'sauce',
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
    (CAT_FRUGT_GROENT,     ('agurk', 'bananer', 'banan', 'peberfrugt', 'tomat',
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
]


def _refresh_product_cache():
    """Load pre-computed product data and search index from Supabase app_cache."""
    global cached_data
    try:
        import httpx, os
        supabase_key = os.getenv("SUPABASE_KEY") or ""
        headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
        url = f"{os.getenv('SUPABASE_URL')}/rest/v1/app_cache?select=*&id=gte.0&order=id.asc"

        with httpx.Client(timeout=30.0) as client:
            res = client.get(url, headers=headers)
            if res.status_code == 200 and res.json():
                rows = res.json()
                
                _c_data = []
                _c_idx = {}
                
                for row in rows:
                    if row.get('id') == 0:
                        _c_idx = row.get('search_index', {})
                    else:
                        chunk_data = row.get('data', [])
                        if isinstance(chunk_data, list):
                            _c_data.extend(chunk_data)

                if _c_data or _c_idx:
                    cached_data = {
                        'timestamp': datetime.now(),
                        'data': _c_data,
                        'search_index': _c_idx
                    }
                    logger.info(f"Product cache refreshed instantly from Supabase app_cache ({len(_c_data)} produkter i {len(rows)-1} chunks)")
                else:
                    logger.warning("app_cache var tom")
            else:
                logger.error(f"Could not load app_cache. Status: {res.status_code}")
    except Exception as e:
        logger.error(f"Error loading app_cache: {e}")


def _start_background_cache_refresh():
    """Refresh cache ~10 min before expiry so users avoid cold-start waits."""
    global _cache_refresh_started
    with _cache_refresh_lock:
        if _cache_refresh_started:
            return
        _cache_refresh_started = True

    def _worker():
        lead = timedelta(minutes=10)
        while True:
            try:
                sleep_s = max(120, CACHE_DURATION.total_seconds() - lead.total_seconds())
                time.sleep(sleep_s)
                ts = cached_data.get('timestamp')
                if ts is None:
                    continue
                if datetime.now() - ts < CACHE_DURATION - lead:
                    continue
                logger.info("Background cache refresh starting")
                with _xml_cache_lock:
                    ts = cached_data.get('timestamp')
                    if ts is None or datetime.now() - ts < CACHE_DURATION - lead:
                        continue
                    _refresh_product_cache()
            except Exception:
                logger.exception("Background cache refresh failed")

    threading.Thread(target=_worker, daemon=True, name='cache-refresh').start()


def get_product_data():
    """Get product data with caching"""
    global cached_data
    _start_background_cache_refresh()
    current_time = datetime.now()
    if (cached_data['timestamp'] is None or
            cached_data['data'] is None or
            current_time - cached_data['timestamp'] >= CACHE_DURATION):
        with _xml_cache_lock:
            if (cached_data['timestamp'] is None or
                    cached_data['data'] is None or
                    current_time - cached_data['timestamp'] >= CACHE_DURATION):
                _refresh_product_cache()
    else:
        logger.debug("Using cached product data")
    return cached_data['data'] or []

def get_active_stores():
    """Selected store labels from ?stores= or cartspotter_stores cookie. None = all stores."""
    stores_param = request.args.get('stores')
    if stores_param is not None:
        labels = {s.strip() for s in stores_param.split(',') if s.strip()}
        return labels

    stores_cookie = request.cookies.get('cartspotter_stores')
    if stores_cookie:
        try:
            unquoted = urllib.parse.unquote(stores_cookie)
            stores_list = json.loads(unquoted)
            if isinstance(stores_list, list) and len(stores_list) > 0:
                return {str(s).strip() for s in stores_list if str(s).strip()}
        except Exception:
            pass

    return None

_TOBACCO_IMG_RE = re.compile(
    r'rema-product-images\.digital\.rema1000\.dk/'
    r'(5213[4-9]\d|52[14]\d{3}|5218[0-2]\d|5618[2-7]\d)/'
)

def _is_tobacco_image(url: str) -> bool:
    m = _TOBACCO_IMG_RE.search(url)
    if not m:
        return False
    pid = int(m.group(1))
    return (521340 <= pid <= 521825) or (561828 <= pid <= 561875)

def filter_products_by_stores(products, active_stores):
    """Helper to filter products by store names, blocked images, and blocked product names."""
    def _is_allowed(p):
        img = str(p.get('/product/imageLink', '')).strip()
        if img in _PLACEHOLDER_IMGS or _is_tobacco_image(img):
            return False
        rema_img = str(p.get('/product/rema_image', '')).strip()
        if rema_img in _PLACEHOLDER_IMGS or _is_tobacco_image(rema_img):
            return False
        name = str(p.get('/product/title', '')).lower()
        if any(fragment in name for fragment in _BLOCKED_NAME_FRAGMENTS):
            return False
        return True

    filtered = [p for p in products if _is_allowed(p)]
    if active_stores is None:
        return filtered
    return [p for p in filtered if product_available_at_active_stores(p, active_stores)]

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
        logger.error(f"Error loading newsletters: {str(e)}")
        return render_template('newsletters.html', newsletters=[], bilka_current=[], bilka_upcoming=[], rema_current=[], rema_upcoming=[])

def apply_product_filters(products, args):
    """Helper to apply price, sale, organic, weight, subcategory filters and sorting to a list of products"""
    min_price = args.get('min_price', type=float)
    max_price = args.get('max_price', type=float)
    sale_only = args.get('sale', type=str) == 'true'
    organic_only = args.get('organic', type=str) == 'true'
    lactose_only = args.get('lactose', type=str) == 'true'
    min_weight = args.get('min_weight', type=float)
    max_weight = args.get('max_weight', type=float)
    sort_type = args.get('sort', 'relevance')
    subcategory = args.get('subcategory', type=str) or ''

    filtered = []
    for p in products:
        # Use the effective price (sale price if active)
        price = p.get('sale_price') if p.get('is_sale') else p.get('price')
        if price is None: price = 0
        
        if min_price is not None and price < min_price: continue
        if max_price is not None and price > max_price: continue
        if sale_only and not p.get('is_sale') and not p.get('is_any_sale'): continue
        if subcategory and p.get('subcategory', '') != subcategory: continue
        
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
supabase = None

def init_db():
    if not is_price_db_enabled():
        set_db_available(False)
        logger.info("Price database disabled (ENABLE_PRICE_DB=0)")
        return
    try:
        url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
        key = os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_KEY")
        if key and (key.startswith("http://") or key.startswith("https://")):
            # Fall back to NEXT_PUBLIC key if SUPABASE_KEY is a URL placeholder
            key = os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
            
        if not url or not key:
            set_db_available(False)
            logger.warning("Supabase URL or Key not set. App runs without database.")
            return

        global supabase
        from supabase import create_client
        supabase = create_client(url, key)
        set_db_available(True)
        logger.info("Supabase connection initialized successfully.")
    except Exception as e:
        set_db_available(False)
        logger.warning("Supabase connection unavailable (%s). App runs without database.", e)

def get_popular_product_ids(limit=20):
    if not db_available() or not supabase:
        return []
    try:
        res = supabase.table("cart_popularity").select("product_id").order("count", desc=True).limit(limit).execute()
        return [row["product_id"] for row in res.data]
    except Exception as e:
        logger.error("Error fetching popular product ids: %s", e)
        return []

@app.route('/api/cart-event', methods=['POST'])
@rate_limit(api_limiter)
def cart_event():
    try:
        data = request.get_json(force=True)
        product_id = str(data.get('product_id', '')).strip()[:64]
        if not product_id:
            return jsonify({'ok': False}), 400
        if not db_available() or not supabase:
            return jsonify({'ok': True, 'persisted': False})
            
        # Increment popularity: select, then update or insert
        res = supabase.table("cart_popularity").select("count").eq("product_id", product_id).execute()
        if res.data:
            new_count = (res.data[0].get("count") or 0) + 1
            supabase.table("cart_popularity").update({"count": new_count}).eq("product_id", product_id).execute()
        else:
            supabase.table("cart_popularity").insert({"product_id": product_id, "count": 1}).execute()
            
        return jsonify({'ok': True, 'persisted': True})
    except Exception as e:
        logger.error("cart-event error: %s", e)
        return jsonify({'ok': False}), 500

@app.route('/api/price-history/<product_id>')
def get_price_history(product_id):
    if not db_available() or not supabase:
        return jsonify(success=True, history=[], history_by_store={})
    try:
        res = supabase.table("price_history").select("store, price, date").eq("product_id", str(product_id)[:64]).order("store").order("date").execute()
        
        by_store = {}
        for row in res.data:
            store = row.get("store")
            price = row.get("price")
            date = row.get("date")
            by_store.setdefault(store, []).append({'price': price, 'date': date})
            
        flat = by_store.get('rema') or next(iter(by_store.values()), [])
        return jsonify(success=True, history=flat, history_by_store=by_store)
    except Exception as e:
        logger.error("price-history error: %s", e)
        return jsonify(success=False, error='Kunne ikke hente prishistorik.')

@app.route('/api/create-alert', methods=['POST'])
@rate_limit(api_limiter)
def create_alert():
    try:
        data = request.get_json(silent=True) or {}
        p_id = str(data.get('product_id', '')).strip()[:64]
        p_name = str(data.get('product_name', '')).strip()[:200]
        if not p_id:
            return jsonify(success=False, error='Manglende produkt-id.'), 400
        try:
            target_val = data.get('target_price')
            current_val = data.get('current_price')
            if target_val is None or current_val is None:
                raise ValueError("Missing price")
            target = float(target_val)
            current = float(current_val)
        except (TypeError, ValueError):
            return jsonify(success=False, error='Ugyldig pris.'), 400
        if target <= 0 or current <= 0 or target > 99999:
            return jsonify(success=False, error='Ugyldig pris.'), 400

        if not db_available() or not supabase:
            return jsonify(success=True, persisted=False)

        supabase.table("price_alerts").insert({
            "product_id": p_id,
            "product_name": p_name,
            "target_price": target,
            "current_price": current
        }).execute()
        return jsonify(success=True, persisted=True)
    except Exception as e:
        logger.error("create-alert error: %s", e)
        return jsonify(success=False, error='Kunne ikke oprette alarm.')

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
    }

    seen_tilbud_imgs = set()
    seen_cat_imgs = {cat: set() for cat in products_by_category}

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
                product_to_display_dict(
                    product,
                    category=product.get('/product/product_type', CAT_KOLONIAL),
                )
            )
            used_fav_ids.add(pid)
            return True
        except (ValueError, TypeError):
            return False

    _cat_keys = {CAT_MEJERI}
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
                sale_end_date = parse_sale_end_date(product)
                if not _img_valid or _img not in seen_tilbud_imgs:
                    if _img_valid:
                        seen_tilbud_imgs.add(_img)
                    products_by_category['Ugens Tilbud'].append(
                        product_to_display_dict(product, category=ptype, sale_end_date=sale_end_date)
                    )

            # Regular categories
            if ptype in _cat_keys and price > 0:
                if not _img_valid or _img not in seen_cat_imgs[ptype]:
                    if _img_valid:
                        seen_cat_imgs[ptype].add(_img)
                    products_by_category[ptype].append(
                        product_to_display_dict(product, category=ptype)
                    )

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

    # Apply advanced filters to each category
    filtered_categories = {}
    for cat, products in products_by_category.items():
        if products:
            filtered = apply_product_filters(products, request.args)
            if filtered:
                filtered_categories[cat] = filtered

    trimmed_categories = {k: v[:60] for k, v in filtered_categories.items() if v}
    template_mapping = {
        'Ugens Tilbud':         '/ugens_tilbud',
        'Brugernes Favoritter': None,
        CAT_MEJERI:             '/Mejeri',
    }

    # Handle AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render_template(
            'partials/index_products.html',
            categories=trimmed_categories,
            template_mapping=template_mapping
        )

    # Prices are recorded centrally in get_product_data() — no duplicate call here

    return render_template(
        'index.html',
        categories=trimmed_categories,
        template_mapping=template_mapping,
    )

@app.route('/vilkaar.html')
@app.route('/terms-of-service')
def terms_of_service():
    return render_template('terms.html')


@app.route('/om-os.html')
@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/feedback.html')
@app.route('/feedback')
def feedback_page():
    return render_template('feedback.html')


@app.route('/api/feedback', methods=['POST'])
@rate_limit(api_limiter)
def submit_feedback():
    data = request.get_json(silent=True) or {}
    feedback_type = str(data.get('type', 'feedback')).strip()[:50]
    message = str(data.get('message', '')).strip()
    name = str(data.get('name', '')).strip()[:120] or None
    email = str(data.get('email', '')).strip()[:200] or None
    subject = str(data.get('subject', '')).strip()[:200] or None
    page_url = str(data.get('page_url', '')).strip()[:500] or None

    allowed_types = {'feedback', 'bug', 'feature', 'other'}
    if feedback_type not in allowed_types:
        feedback_type = 'feedback'

    if len(message) < 10:
        return jsonify(success=False, error='Beskeden skal være mindst 10 tegn.'), 400
    if len(message) > 5000:
        return jsonify(success=False, error='Beskeden er for lang (maks. 5000 tegn).'), 400

    if not db_available() or not supabase:
        logger.info("Feedback received (DB off): %s", feedback_type)
        return jsonify(success=True, persisted=False)

    try:
        supabase.table("feedback").insert({
            "feedback_type": feedback_type,
            "name": name,
            "email": email,
            "subject": subject,
            "message": message,
            "page_url": page_url,
            "created_at": datetime.now().isoformat(timespec='seconds')
        }).execute()
        return jsonify(success=True, persisted=True)
    except Exception as e:
        logger.error('Feedback save error: %s', e)
        return jsonify(success=False, error='Kunne ikke gemme din besked. Prøv igen senere.'), 500


@app.route('/sale.html')
def sale_html_redirect():
    return redirect(url_for('ugens_tilbud'), 301)

@app.route('/ugens_tilbud')
def ugens_tilbud():
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
                    sale_products.append(
                        product_to_display_dict(
                            product,
                            default_category='Andre varer',
                            sale_end_date=parse_sale_end_date(product),
                            force_sale=bool(product.get('/product/sale_price')),
                        )
                    )
                except (ValueError, TypeError, KeyError) as e:
                    logger.warning(
                        "Error converting sale product %s: %s",
                        product.get('/product/id'),
                        e,
                    )
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
        logger.error("Error loading sale page: %s", e)
        return "Page not found", 404

@app.route('/api/autocomplete')
def autocomplete():
    """Returns up to 8 slim product suggestions for the search autocomplete dropdown."""
    query = request.args.get('q', '').strip().lower()
    if len(query) < 2:
        return jsonify({'suggestions': []})

    try:
        active_stores = get_active_stores()
        product_data = get_product_data()
        filtered_data = filter_products_by_stores(product_data, active_stores)
        matched = _filter_products_for_search(filtered_data, query, active_stores)
        seen_names = set()
        suggestions = []

        for d in matched:
            if len(suggestions) >= 8:
                break
            key = normalize_name(d['name'])
            if key in seen_names:
                continue
            seen_names.add(key)
            price = float(d.get('sale_price') or d.get('price') or 0)
            suggestions.append({
                'name': d['name'],
                'brand': d.get('brand', ''),
                'price': round(price, 2),
                'is_sale': bool(d.get('is_sale')),
                'image': d.get('image_url', ''),
                'category': d.get('category', ''),
            })

        return jsonify({'suggestions': suggestions})

    except Exception as e:
        logger.error("Autocomplete error: %s", e)
        return jsonify({'suggestions': []})


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
        all_products = _filter_products_for_search(filtered_data, query, active_stores)

        if len(all_products) == 0:
            return jsonify(html='<div class="no-results">Ingen resultater fundet</div>')
            
        products_html = render_template('partials/search_products.html', products=all_products)
        return jsonify(html=products_html)
        
    except Exception as e:
        logger.exception("Error in search route: %s", e)
        return jsonify(html='<div class="error">Der opstod en fejl under søgningen</div>')

@app.route('/search/results')
def search_page():
    """Full page search results"""
    query = ""
    try:
        page = request.args.get('page', 1, type=int)
        query = request.args.get('q', '').lower().strip()
        per_page = 60  # 6x10 layout
        
        if not query:
            return redirect(url_for('home'))
        
        active_stores = get_active_stores()
        product_data = get_product_data()
        filtered_data = filter_products_by_stores(product_data, active_stores)
        all_products = _filter_products_for_search(filtered_data, query, active_stores)

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
        logger.exception("Error in search: %s", e)
        return render_template('search_results.html',
                            query=query,
                            products=[],
                            total_products=0,
                            current_page=1,
                            total_pages=1,
                            error="Der opstod en fejl under søgningen")

@app.route('/<category_name>.html')
def category_html_redirect(category_name):
    return redirect(f'/{category_name}', 301)

@app.route('/<category_name>')
def category(category_name):
    # Reverse mapping for filenames to category names
    category_mapping = {
        'Kolonial': CAT_KOLONIAL,
        'Drikkevarer': CAT_DRIKKEVARER,
        'Mejeri': CAT_MEJERI,
        'Køl': CAT_MEJERI,
        'Frugt_og_groent': CAT_FRUGT_GROENT,
        'Frost': CAT_FROST,
        'Broed_og_kager': CAT_BROED_KAGER,
        'Koed_og_fisk': CAT_KOED_FISK,
        'Slik': CAT_SLIK,
    }
    
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 60  # 6x10 layout
        
        actual_category = category_mapping.get(category_name)
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
                    category_products.append(
                        product_to_display_dict(product, category=str(p_type))
                    )
                except Exception as e:
                    logger.warning("Error processing product in category: %s", e)
                    continue

        # Prices are recorded centrally in get_product_data() — no duplicate call here

        # Compute ordered subcategory list from unfiltered products
        rules = _SUBCATEGORY_RULES.get(actual_category, [])
        _seen_subs = {p.get('subcategory', '') for p in category_products}
        available_subcategories = [sub for sub, _ in rules if sub in _seen_subs]
        if 'Øvrige' in _seen_subs:
            available_subcategories.append('Øvrige')
        current_subcategory = request.args.get('subcategory', '')

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
                            total_pages=total_pages,
                            available_subcategories=available_subcategories,
                            current_subcategory=current_subcategory)
                            
    except Exception as e:
        logger.exception("Error loading category %s: %s", category_name, e)
        return "Internal Server Error", 500

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

@app.route('/product/<product_id>')
def get_product_info(product_id):
    """Get product information and print debug info"""
    try:
        product_data = get_product_data()
        
        # Find the product with the matching ID
        product = next((p for p in product_data if str(p['/product/id']) == str(product_id)), None)
        
        if product:
            logger.debug("Product info requested for %s: %s", product_id, product.get('/product/title'))
            
            return jsonify({
                'success': True,
                'product': {
                    'rema_price': product['/product/price'],
                    'bilka_price': product['/product/price']
                }
            })
        else:
            logger.info(f"Product not found with ID: {product_id}")
            return jsonify(success=False, error="Product not found"), 404
            
    except Exception as e:
        logger.error(f"Error getting product info: {str(e)}")
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

@app.route('/api/alternatives', methods=['POST'])
def find_alternatives():
    try:
        data = request.json
        missing_items = data.get('missing_items', [])
        if not missing_items:
            return jsonify({'success': True, 'alternatives': []})
            
        product_data = get_product_data()
        
        alternatives = []
        for req_item in missing_items:
            cart_id = req_item.get('cart_id')
            store_label = req_item.get('store')
            category = req_item.get('category')
            name = req_item.get('name', '')
            weight_str = req_item.get('weight_str', '')
            weight_g = parse_weight_to_grams(weight_str) if weight_str else None
            
            subcategory = _get_subcategory(name, category)

            # Keywords from subcategory rules that appear in the original name (e.g. "energidrik")
            orig_subcat_kws = _get_subcategory_keywords(name, category)
            
            best_alt = None
            best_score = -1.0
            best_price = float('inf')
            norm_orig = normalize_name(name)
            
            for p in product_data:
                p_store = p.get('/product/store', 'Rema 1000')
                p_matches = p.get('/product/store_matches', {})
                
                target_price = None
                p_name_store = p.get('/product/title', '')
                p_image_store = p.get('/product/imageLink', '')
                
                if p_store == store_label:
                    target_price = p.get('/product/sale_price') or p.get('/product/price')
                else:
                    for match_key, match_data in p_matches.items():
                        store_cfg = _STORE_CONFIGS.get(match_key)
                        if store_cfg and store_cfg['label'] == store_label:
                            target_price = match_data.get('normal_price') or match_data.get('price')
                            if match_data.get('name'):
                                p_name_store = match_data.get('name')
                            if match_data.get('image') and str(match_data.get('image')).lower() not in ('nan', 'none'):
                                p_image_store = match_data.get('image')
                            break
                            
                if target_price is None or float(target_price) <= 0:
                    continue
                    
                target_price = float(target_price)
                
                p_category = p.get('/product/product_type', '')
                if p_category != category:
                    continue
                    
                p_name_base = p.get('/product/title', '')
                p_subcat = _get_subcategory(p_name_base, p_category)
                
                if p_subcat != subcategory:
                    continue
                    
                # Weight check
                p_weight_g = p.get('/product/weight_g')
                if weight_g is not None and p_weight_g is not None:
                    # Allow up to 100g difference for alternatives
                    if not weights_compatible(weight_g, p_weight_g, 100):
                        continue
                
                # Check for same item - if it's the same, skip; also skip completely unrelated names
                sim = fuzzy_score(norm_orig, normalize_name(p_name_base))
                if sim > 0.9 or sim < 0.25:
                    continue

                # For 'Øvrige' products the subcategory label is generic, so require a shared
                # keyword to avoid matching completely unrelated products (e.g. rødbeder ↔ sødetabletter).
                # Named subcategories already guarantee product similarity — no extra keyword check needed.
                if subcategory == 'Øvrige' and orig_subcat_kws:
                    alt_subcat_kws = _get_subcategory_keywords(p_name_base, category)
                    if alt_subcat_kws and not orig_subcat_kws & alt_subcat_kws:
                        continue

                # Pick by highest name similarity; use price as tiebreaker
                if sim > best_score or (sim == best_score and target_price < best_price):
                    best_score = sim
                    best_price = target_price

                    new_storePrices = {}
                    base_price = p.get('/product/sale_price') or p.get('/product/price')
                    if base_price:
                        new_storePrices[p_store] = float(base_price)

                    for match_key, match_data in p_matches.items():
                        store_cfg = _STORE_CONFIGS.get(match_key)
                        if store_cfg:
                            mp = match_data.get('normal_price') or match_data.get('price')
                            if mp:
                                new_storePrices[store_cfg['label']] = float(mp)

                    best_alt = {
                        'cart_id': cart_id,
                        'store': store_label,
                        'alt_id': str(p.get('/product/id', '')),
                        'alt_name': p_name_store,
                        'alt_price': best_price,
                        'alt_image': p_image_store,
                        'alt_storePrices': new_storePrices,
                        'alt_category': p_category,
                        'alt_unitMeasure': p.get('/product/unit_pricing_measure', ''),
                        'alt_kgPrice': p.get('/product/price_per_kg', ''),
                        'alt_store': p_store
                    }
            
            if best_alt:
                alternatives.append(best_alt)
                
        return jsonify({'success': True, 'alternatives': alternatives})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    port = int(os.environ.get('PORT', '5001'))
    logger.info(
        "Starting server debug=%s port=%s db=%s",
        debug,
        port,
        db_available(),
    )
    app.run(debug=debug, host='0.0.0.0', port=port)