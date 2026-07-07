"""
Føtex komplet produktkatalog scraper.
- Algolia prod_FOETEX_PRODUCTS: navn, EAN, kategori, vægt, billede OG pris.
- Priser ligger direkte i Algolia-indekset (storeData[butik].price i øre) - ligesom
  Bilka. Vi bruger derfor IKKE længere Salling Group /v2/products API'et, som var
  rate-limitet (~60 kald/min + daglig kvote) og kun nåede at prissætte en brøkdel
  af kataloget. Nu får ~alle fødevarer en pris i ét træk.

Føtex, Netto og Bilka har samme moderfirma (Salling Group), men er separate kæder
med hver deres priser - derfor læses Føtex-prisen fra Føtex' eget indeks.
"""
import os, sys, time, requests
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client, enrich_billede_hashes
from keywords import is_non_food

# Top-level kategorier fra Føtex's Algolia-hierarki der er fødevarer.
# Whitelist er mere robust end blacklist - ukendte kategorier springes over.
_FOOD_CATEGORIES = {
    'frugt', 'grønt', 'grøntsager', 'frugt og grønt',
    'kød', 'fisk', 'fjerkræ', 'pålæg',
    'mejeri', 'ost', 'æg', 'plantebaseret',
    'brød', 'bageri', 'bagværk',
    'drikkevarer', 'øl', 'vin', 'spiritus', 'vand', 'juice', 'kaffe', 'te',
    'kolonial', 'konserves', 'tørvarer',
    'frost', 'dybfrost',
    'slik', 'snacks', 'konfekture', 'chokolade',
    'morgenmad', 'gryn', 'cerealier',
    'pasta', 'ris',
    'sauce', 'krydderier', 'olier',
    'færdigretter', 'convenience',
    'baby', 'babyernæring', 'babymad',
    'sundhed', 'naturlig', 'økologisk',
    'international', 'verden',
    'mad', 'fødevarer', 'dagligvarer',
}

# Top-level kategorier der er 100% ikke-mad - bruges som hurtig blacklist
_NON_FOOD_CATEGORIES = {
    'non-food', 'personlig pleje', 'helse', 'husholdning',
    'tøj', 'sko', 'sport', 'fritid', 'elektronik',
    'blomster', 'planter', 'have', 'kæledyr',
    'legetøj', 'hobby', 'bøger', 'magasiner',
    'rengøring', 'vask',
    'dyremad',  # "dyremad" rammer 'mad' i FOOD_CATEGORIES uden denne
    'bolig & køkken',  # persienner, tæpper, balloner mv.
    'leg',             # legetårne, Jungle Gym mv.
    'kiosk',           # magasiner, tobak, medicin
}


def _is_food_hit(hit: dict) -> bool:
    """Returnerer True hvis produktet er en fødevare."""
    cat = _cat(hit).lower()

    # 1. Hurtig blacklist på kategori
    if any(nf in cat for nf in _NON_FOOD_CATEGORIES):
        return False

    # 2. Keyword-filter altid - fanger ikke-mad inden for fx "Baby & børn"
    if is_non_food(hit.get('name', '')):
        return False

    # 3. Whitelist på kategori - kun kendte madkategorier slipper igennem
    if cat and any(fc in cat for fc in _FOOD_CATEGORIES):
        return True

    # 4. Ukendt/tom kategori - ingen kategori, ingen keyword-match = godkendt
    if not cat:
        return True

    # 5. Kategori er ukendt og ikke på whitelist - filtrer fra for en sikkerheds skyld
    return False

# ── Algolia ──────────────────────────────────────────────────────────────────
ALGOLIA_APP_ID = 'F9VBJLR1BK'
ALGOLIA_KEY    = 'd4f161f51f749bdd5baf699175d5f956'
ALGOLIA_INDEX  = 'prod_FOETEX_PRODUCTS'
ALGOLIA_URL    = f'https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query'
ALGOLIA_ATTRS  = ['name', 'gtin', 'objectID', 'brand', 'subBrand', 'manufacturer',
                  'units', 'unitsOfMeasure', 'netcontent',
                  'consumerFacingHierarchy', 'categories', 'images',
                  'productType', 'properties',
                  'storeData', 'sales_price', 'multibuy_offer_description']
ALGOLIA_HEADERS = {'X-Algolia-Application-Id': ALGOLIA_APP_ID, 'X-Algolia-API-Key': ALGOLIA_KEY}

# Reference-butik (fysisk Føtex) til pris/tilbud. Falder tilbage til enhver butik
# med pris, hvis referencebutikken ikke fører varen.
REF_STORES = ['1373']

# ── Supabase ──────────────────────────────────────────────────────────────────
BUTIK    = 'Foetex'
KATEGORI = 'Katalog'


def _algolia_page(page: int) -> list[dict]:
    r = requests.post(ALGOLIA_URL, json={
        'query': '', 'hitsPerPage': 100, 'page': page,
        'attributesToRetrieve': ALGOLIA_ATTRS,
    }, headers=ALGOLIA_HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()['hits']


def fetch_all_algolia() -> list[dict]:
    r = requests.post(ALGOLIA_URL, json={
        'query': '', 'hitsPerPage': 100, 'page': 0,
        'attributesToRetrieve': ALGOLIA_ATTRS,
    }, headers=ALGOLIA_HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    nb_pages = data['nbPages']
    print(f'  Algolia: {data["nbHits"]} produkter, {nb_pages} sider')

    all_hits = list(data['hits'])
    for page in range(1, nb_pages):
        hits = _algolia_page(page)
        all_hits.extend(hits)
        if page % 10 == 0:
            print(f'    Side {page}/{nb_pages} ({len(all_hits)} produkter)...')
        time.sleep(0.15)

    print(f'  Algolia done: {len(all_hits)} produkter hentet')
    return all_hits


def _cat(hit: dict) -> str:
    # Foetex bruger consumerFacingHierarchy (dybere hierarki end categories)
    hier = hit.get('consumerFacingHierarchy', {})
    lvl0 = hier.get('lvl0') or []
    if lvl0:
        return lvl0[0] if isinstance(lvl0, list) else str(lvl0)
    cats = hit.get('categories', {})
    return (cats.get('lvl0') or [''])[0]


def _norm_unit(unit: str) -> str:
    return (unit or '').strip().rstrip('.').lower()


def _ref_store(hit: dict) -> dict | None:
    """Vælg prisdata fra referencebutik, ellers enhver butik med pris."""
    sd = hit.get('storeData') or {}
    for sid in REF_STORES:
        v = sd.get(sid)
        if v and v.get('price'):
            return v
    for v in sd.values():
        if v and v.get('price'):
            return v
    return None


def _kg_price(hit: dict, ref: dict | None) -> str | None:
    val  = (ref or {}).get('unitsOfMeasurePrice')
    unit = (ref or {}).get('unitsOfMeasurePriceUnit')
    if val and unit:
        return f'{val / 100:.2f} kr/{_norm_unit(unit)}'
    return None


def _weight(hit: dict) -> str | None:
    nc = (hit.get('netcontent') or '').strip()
    if nc:
        return nc
    vol = hit.get('units')
    unit = hit.get('unitsOfMeasure')
    if vol and unit:
        return f'{vol} {unit}'
    return None


def build_rows(hits: list[dict]) -> list[dict]:
    rows = []
    for hit in hits:
        naam = (hit.get('name') or '').strip()
        ean  = str(hit.get('gtin') or '').strip()
        if not naam or not ean:
            continue

        ref = _ref_store(hit)
        price_ore = (ref or {}).get('price') or hit.get('sales_price')
        if not price_ore:
            continue

        before = (ref or {}).get('beforePrice') or 0
        on_offer = bool(before and before > price_ore)
        pris       = round(price_ore / 100, 2)
        normalpris = round(before / 100, 2) if on_offer else None

        multikob = ''
        if ref:
            mp  = str(ref.get('multipromo') or '').strip()
            mpp = str(ref.get('multiPromoPrice') or '').strip()
            if mp:
                multikob = f'{mp} {mpp}'.strip()
        if not multikob:
            multikob = (hit.get('multibuy_offer_description') or '').strip()

        tilbud = 'Ja' if (on_offer or multikob) else 'Nej'
        producent = (hit.get('brand') or hit.get('manufacturer') or 'Salling').strip() or 'Salling'
        images = hit.get('images') or []
        billede = images[0] if images else ''

        rows.append({
            'butik':        BUTIK,
            'kategori':     KATEGORI,
            'navn':         naam,
            'producent':    producent,
            'netto_vaegt':  _weight(hit),
            'kg_price':     _kg_price(hit, ref),
            'pris':         pris,
            'normalpris':   normalpris,
            'varenummer':   ean,
            'billede_url':  billede,
            'billede_hash': None,
            'tilbud':       tilbud,
            'multikob':     multikob or None,
        })
    enrich_billede_hashes(rows)
    return rows


def save_to_supabase(rows: list[dict]):
    # Sikkerhed: en tom scraping må aldrig slette eksisterende data.
    if not rows:
        print('  Ingen rækker - beholder eksisterende Føtex-data (intet slettet).')
        return
    client = get_client()
    client.table('produkter').delete().eq('butik', BUTIK).eq('kategori', KATEGORI).execute()
    for i in range(0, len(rows), 500):
        client.table('produkter').insert(rows[i:i+500]).execute()
    print(f'  Gemt {len(rows)} rækker i Supabase')


def print_category_report(hits: list[dict]):
    """Printer unikke lvl0-kategorier og antal produkter - til at tune whitelisten."""
    from collections import Counter
    cats = Counter(_cat(h) or '(ingen)' for h in hits)
    print('\n  === Kategorioversigt ===')
    for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
        flag = '✓' if any(fc in cat.lower() for fc in _FOOD_CATEGORIES) else \
               '✗' if any(nf in cat.lower() for nf in _NON_FOOD_CATEGORIES) else '?'
        print(f'  {flag} {cat:45s} {n:5d} produkter')
    print()


def main():
    print('Starter Føtex katalog scraper (Algolia)...')

    hits = fetch_all_algolia()
    print_category_report(hits)
    food_hits = [h for h in hits if h.get('gtin') and _is_food_hit(h)]
    skipped_early = len(hits) - len(food_hits)
    print(f'  {len(hits)} produkter hentet → {skipped_early} ikke-mad fjernet → {len(food_hits)} fødevarer')

    rows = build_rows(food_hits)
    print(f'  {len(rows)} rækker med pris bygget')

    print('\nEksempel (første 5):')
    for r in rows[:5]:
        print(f"  {r['navn']:35.35s} {r['pris']:>7} kr  {r['producent']:15.15s} "
              f"{r['netto_vaegt'] or '':>8}  tilbud={r['tilbud']}")

    save_to_supabase(rows)
    print(f'\nFærdig! {len(rows)} Føtex-produkter gemt.')


if __name__ == '__main__':
    main()
