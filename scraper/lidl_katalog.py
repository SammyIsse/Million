"""
Lidl produktkatalog med hyldpriser via lidl.dk.

Kilde: lidl.dk intern søgning (ldt-searcher) - SSR Nuxt-payload på /q/search?q=*
Giver ~465 fødevarer med aktuel pris og normalpris (oldPrice) ved tilbud.

Status: KLAR - kører via GitHub Actions + manuelt.
  - Tilbudsavis: python scraper/webscrape_lidl.py
  - Katalog:     python scraper/lidl_katalog.py
  - Supabase-gem kræver SUPABASE_URL + SUPABASE_KEY (som andre scrapers)

Begrænsninger:
  - Kun varer listet på lidl.dk (~710 total, ~465 fødevarer efter filter)
  - Ingen EAN på nogen varer (erpNumber bruges som varenummer/pseudo-EAN,
    se ean_looks_valid() i updater.py for hvorfor det ikke bruges til EAN-match)
  - Lidl Plus-priser er ikke tilgængelige her (kræver login; se commit-historik
    for hvorfor det spor er droppet - erhvervsmæssig brug af Lidl Plus er
    udtrykkeligt forbudt i deres vilkår)
"""
import io
import json
import os
import re
import sys
import time

import requests

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from keywords import is_non_food as _is_non_food
from supabase_utils import get_client, enrich_billede_hashes, shrink_guard_ok, fetch_existing_products, save_product_dicts

SEARCH_URL = 'https://www.lidl.dk/q/search'
FETCH_SIZE = 48
PAGE_DELAY = 0.25
BUTIK = 'Lidl'
KATEGORI = 'Katalog'

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'da-DK,da;q=0.9',
}

# Gavekort, oplevelser og lign. der ligger i Food-kategorien på lidl.dk
_JUNK_EXACT = {
    'brunch', 'frokost', 'gavekort', 'cafépause', 'cafe pause',
    'middag for 2', 'bio for 2', 'ticketmaster', 'fashioncheque', 'magasin',
}
_JUNK_CONTAINS = ('gavekort', 'ticketmaster', 'fashioncheque', 'oplevelsesgavekort')

_NUXT_JSON_RE = re.compile(
    r'<script type="application/json"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_WEIGHT_RE = re.compile(
    r'(\d+[.,]?\d*)\s*(kg|g|l|ml|cl|dl|stk|liter)',
    re.IGNORECASE,
)


def _ref(pool: list, val):
    """Ét Nuxt-indeks-opslag (int → pool[i]). Ingen dyb rekursion."""
    if isinstance(val, int) and 0 <= val < len(pool):
        return pool[val]
    return val


def _ref_str(pool: list, val) -> str:
    v = _ref(pool, val)
    return v if isinstance(v, str) else ''


def _ref_num(pool: list, val) -> float | int | None:
    v = _ref(pool, val)
    if isinstance(v, (int, float)):
        return v
    return None


def _ref_dict(pool: list, val) -> dict:
    v = _ref(pool, val)
    return v if isinstance(v, dict) else {}


def _ref_list(pool: list, val) -> list:
    v = _ref(pool, val)
    return v if isinstance(v, list) else []


# Nuxt begyndte 2026-08-18 at pakke søgeresultatets item-liste ind i sit
# devalue-lignende Ref/Reactive-skema i stedet for en almindelig pool-indeks-
# reference. Kæden set i praksis:
#   node['products'] = N            (plain int, som _ref allerede følger)
#   pool[N]           = ['Ref', M]
#   pool[M]           = ['Reactive', K]
#   pool[K]           = {'0': L}    (encoding af et enkelt-element array)
#   pool[L]           = [...]       (den faktiske liste af item-indices)
# Scraperen stod stille i 8 nætter i træk (0 varer hver gang), fordi
# _find_search krævede nøglen 'items', som blev omdøbt til 'products', og
# selv med navnet rettet ville den flade _ref ikke have fulgt Ref/Reactive-
# indpakningen. Denne resolver følger kæden generisk, så en fremtidig
# ombygning af PRÆCIS denne indpakning (uden at ændre navnene igen) ikke
# kræver endnu en rettelse - depth-graensen er et sikkerhedsnet, ikke en
# forventet grænse.
def _resolve_ref_chain(pool: list, val, depth: int = 0):
    if depth > 6:
        return val
    v = _ref(pool, val)
    if isinstance(v, list) and len(v) == 2 and v[0] in ('Ref', 'Reactive') and isinstance(v[1], int):
        return _resolve_ref_chain(pool, v[1], depth + 1)
    return v


def _item_code(pool: list, item_ref) -> object:
    item = _ref_dict(pool, item_ref)
    return _ref(pool, item.get('code')) if item else None


def _resolve_items_list(pool: list, val, depth: int = 0) -> list:
    """Følger Ref/Reactive til selve listen af item-pool-indices.

    To former set i praksis efter Nuxts omlægning 2026-08-18:
      * en ren liste af item-indices (det almindelige tilfælde)
      * et objekt med cifferstrenge som nøgler ('0', '1', ...), hvor hver
        værdi er endnu en kandidatliste - kun ÉN af dem er de rigtige
        produkter, resten er tomme skeleton-/loading-tiles. De kendes fra
        hinanden på item['code']: -1 for en skeleton-tile, et rigtigt
        Lidl-varenummer for et ægte produkt.
    """
    if depth > 6:
        return []
    container = _ref(pool, val)
    if isinstance(container, list) and len(container) == 2 and container[0] in ('Ref', 'Reactive') and isinstance(container[1], int):
        return _resolve_items_list(pool, container[1], depth + 1)
    if isinstance(container, dict) and container and all(str(k).isdigit() for k in container):
        candidates = [container[k] for k in sorted(container, key=lambda k: int(k))]
        real = [c for c in candidates if _ref_list(pool, c) and _item_code(pool, _ref_list(pool, c)[0]) not in (None, -1)]
        for cand in reversed(real or candidates):
            items = _ref_list(pool, cand)
            if items:
                return items
        return []
    if isinstance(container, list):
        return container
    return []


def _parse_nuxt_pool(html: str) -> list:
    m = _NUXT_JSON_RE.search(html)
    if not m:
        raise RuntimeError('Ingen Nuxt JSON-payload fundet i Lidl-svar')
    return json.loads(m.group(1))


# Feltet der bar item-listen hed 'items' indtil 2026-08-18, derefter
# 'products' - se _resolve_ref_chain. Både 'type' og 'resultType' er tomme
# strenge i det nye skema, så det tjek er droppet; der findes i praksis kun
# ét numFound-bærende objekt i hele pool'et, så nøglenavnet alene er nok til
# at identificere det entydigt.
_SEARCH_ITEMS_KEYS = ('items', 'products')


def _find_search(pool: list) -> dict | None:
    for val in pool:
        if not isinstance(val, dict) or 'numFound' not in val:
            continue
        if any(k in val for k in _SEARCH_ITEMS_KEYS):
            return val
    return None


def _is_food_product(data: dict, title: str) -> bool:
    if data.get('category') != 'Food':
        return False
    if data.get('isLidlGiftCard'):
        return False
    t = (title or '').lower().strip()
    if t in _JUNK_EXACT:
        return False
    if any(j in t for j in _JUNK_CONTAINS):
        return False
    return not _is_non_food(title or '')


def _parse_weight(title: str, base_price_text: str | None) -> str:
    for src in (title or '', base_price_text or ''):
        m = _WEIGHT_RE.search(src)
        if m:
            return f"{m.group(1).replace(',', '.')} {m.group(2).lower()}"
    return ''


def _parse_kg_price(base_price_text: str | None) -> str:
    if not base_price_text:
        return ''
    m = re.search(r'Pr\.\s*(kg|g|l|ml|cl|dl|stk|liter)\s*([\d.,]+)', base_price_text, re.I)
    if m:
        return f"{m.group(2).replace(',', '.')} kr/{m.group(1).lower()}"
    return ''


def _extract_products(pool: list) -> tuple[int, list[dict]]:
    search = _find_search(pool)
    if not search:
        return 0, []

    num_found_raw = _resolve_ref_chain(pool, search.get('numFound'))
    num_found = int(num_found_raw) if isinstance(num_found_raw, (int, float)) else 0
    items_key = 'items' if 'items' in search else 'products'
    raw_items = _resolve_items_list(pool, search.get(items_key))
    products: list[dict] = []

    for raw_item in raw_items:
        item = _ref_dict(pool, raw_item)

        # 'resultClass' fandtes kun i det gamle skema (indtil 2026-08-18) til
        # at skelne produkt-tiles fra bannere o.l. Det nye 'items'/'products'-
        # array indeholder kun produkt-tiles, og nøglen er væk - så tjekket er
        # nu betinget: skip KUN hvis nøglen findes og siger noget andet end
        # 'product', ellers antag at tilstedeværelsen i listen er nok.
        result_class = item.get('resultClass')
        if result_class is not None and _ref_str(pool, result_class) != 'product':
            continue

        # gridbox/data (til 2026-08-18) -> gridBox/gridBoxData (derefter).
        # Begge forsøges, så et skema der stadig findes i produktion ikke
        # kræver en fremtidig omskrivning, kun en udvidelse af denne liste.
        grid = _ref_dict(pool, item.get('gridBox')) or _ref_dict(pool, item.get('gridbox'))
        data = _ref_dict(pool, grid.get('gridBoxData')) or _ref_dict(pool, grid.get('data'))

        title = _ref_str(pool, data.get('fullTitle')).strip()
        category = _ref_str(pool, data.get('category'))
        is_gift = _ref(pool, data.get('isLidlGiftCard'))
        if not title or category != 'Food' or is_gift is True:
            continue
        if not _is_food_product({'category': category, 'isLidlGiftCard': is_gift}, title):
            continue

        # Brandfeltet mistede 'name' i samme omlægning - kun 'showBrand'
        # (bool) er tilbage. Mærket står i praksis stadig som første ord i
        # titlen ("K-Salat remoulade...", "Combino Fusilli"), så en manglende
        # producent her er en dækningsforringelse, ikke en fejl: is_private_label
        # og de øvrige matchgates behandler allerede et tomt brandfelt som
        # neutralt, aldrig som en modsigelse.
        brand = _ref_dict(pool, data.get('brand'))
        brand_name = _ref_str(pool, brand.get('name')) or None

        price_block = _ref_dict(pool, data.get('price'))
        aktuel = _ref_num(pool, price_block.get('price'))
        if aktuel is None:
            continue

        discount = _ref_dict(pool, price_block.get('discount'))
        on_offer = bool(_ref(pool, discount.get('showDiscount')))
        normal = _ref_num(pool, price_block.get('oldPrice'))
        if normal is None:
            normal = _ref_num(pool, discount.get('deletedPrice'))

        base_price = _ref_dict(pool, price_block.get('basePrice'))
        base_price_text = _ref_str(pool, base_price.get('text')) or None

        image = _ref_str(pool, data.get('image'))
        erp = _ref_str(pool, data.get('erpNumber')) or None

        products.append({
            'navn': title,
            'producent': brand_name,
            'erp': erp,
            'pris': float(aktuel),
            'normalpris': float(normal) if on_offer and normal is not None else None,
            'tilbud': 'Ja' if on_offer else 'Nej',
            'netto_vaegt': _parse_weight(title, base_price_text) or None,
            'kg_price': _parse_kg_price(base_price_text) or None,
            'billede_url': image,
        })

    return num_found, products


def fetch_search_page(offset: int = 0) -> tuple[int, list[dict]]:
    r = requests.get(
        SEARCH_URL,
        params={'q': '*', 'fetchsize': FETCH_SIZE, 'offset': offset},
        headers=_HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    pool = _parse_nuxt_pool(r.text)
    return _extract_products(pool)


def fetch_all_products() -> list[dict]:
    num_found, first = fetch_search_page(0)
    all_products = list(first)
    print(f'  Side 0: {len(first)} fødevarer (katalog: {num_found} varer i alt på lidl.dk)')

    offset = FETCH_SIZE
    while offset < num_found:
        time.sleep(PAGE_DELAY)
        _, batch = fetch_search_page(offset)
        print(f'  Side {offset // FETCH_SIZE}: {len(batch)} fødevarer')
        all_products.extend(batch)
        offset += FETCH_SIZE

    # Dedup på erp - samme vare kan teoretisk optræde to gange
    seen: set[str] = set()
    unique: list[dict] = []
    for p in all_products:
        key = p.get('erp') or p['navn']
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    if len(unique) < len(all_products):
        print(f'  Dedup: {len(all_products)} → {len(unique)} unikke fødevarer')
    return unique


def build_rows(products: list[dict]) -> list[dict]:
    # Genbrug gårsdagens billede_hash ved uændret billed-URL - se
    # bilka_katalog.py's build_rows for begrundelsen (fund M3). Nøglen her
    # (erp/varenummer) er kun et internt Lidl-SKU til cache-opslag, ikke en
    # offentlig EAN - påvirker ikke updater.py's bevidste nulstilling af
    # Lidls varenummer til EAN-baseret cross-store matching.
    _hash_cache = fetch_existing_products(BUTIK)
    rows: list[dict] = []
    for p in products:
        billede_url = p.get('billede_url') or ''
        cached = _hash_cache.get(str(p.get('erp') or '')) or _hash_cache.get(p['navn'].lower())
        reused_hash = (cached['billede_hash']
                       if cached and cached.get('billede_url') == billede_url and cached.get('billede_hash')
                       else None)
        rows.append({
            'butik':        BUTIK,
            'kategori':     KATEGORI,
            'navn':         p['navn'],
            'producent':    p.get('producent'),
            'netto_vaegt':  p.get('netto_vaegt'),
            'kg_price':     p.get('kg_price'),
            'pris':         p['pris'],
            'normalpris':   str(p['normalpris']) if p.get('normalpris') is not None else None,
            'varenummer':   p.get('erp'),
            'billede_url':  billede_url,
            'billede_hash': reused_hash,
            'tilbud':       p.get('tilbud') or 'Nej',
            'multikob':     None,
        })
    enrich_billede_hashes(rows)
    return rows


def main():
    print('Starter Lidl katalog scraper (lidl.dk hyldpriser)...')

    products = fetch_all_products()
    rows = build_rows(products)

    on_offer = sum(1 for r in rows if r['tilbud'] == 'Ja')
    normal_only = len(rows) - on_offer
    print(f'\n  OK: {len(rows)} fødevarer ({normal_only} normalpris, {on_offer} med tilbud)')

    print('\nEksempel (første 5):')
    for r in rows[:5]:
        norm = f" (norm: {r['normalpris']} kr)" if r['normalpris'] else ''
        print(f"  {r['navn']:40s}  {r['pris']:>6} kr{norm}  tilbud={r['tilbud']}")

    save_product_dicts(BUTIK, rows, delete_eq_kategori=KATEGORI)
    print(f'\nFærdig! {len(rows)} Lidl-produkter gemt.')


if __name__ == '__main__':
    main()
