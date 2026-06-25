"""
Føtex komplet produktkatalog scraper.
- Algolia prod_FOETEX_PRODUCTS: navn, EAN, kategori, vægt, billede (~14.500 produkter)
- Salling Group /v2/products/{ean}: normalpriser (kræver FOETEX_SALLING_STORE)
"""
import os, sys, time, requests
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client
from keywords import NON_FOOD_KEYWORDS

# Top-level kategorier fra Føtex's Algolia-hierarki der er fødevarer.
# Whitelist er mere robust end blacklist — ukendte kategorier springes over.
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

# Top-level kategorier der er 100% ikke-mad — bruges som hurtig blacklist
_NON_FOOD_CATEGORIES = {
    'non-food', 'personlig pleje', 'helse', 'husholdning',
    'tøj', 'sko', 'sport', 'fritid', 'elektronik',
    'blomster', 'planter', 'have', 'kæledyr',
    'legetøj', 'hobby', 'bøger', 'magasiner',
    'rengøring', 'vask',
}


def _is_food_hit(hit: dict) -> bool:
    """Returnerer True hvis produktet er en fødevare."""
    cat = _cat(hit).lower()

    # 1. Hurtig blacklist på kategori
    if any(nf in cat for nf in _NON_FOOD_CATEGORIES):
        return False

    # 2. Whitelist på kategori — kun kendte madkategorier slipper igennem
    if cat and any(fc in cat for fc in _FOOD_CATEGORIES):
        return True

    # 3. Ukendt/tom kategori: tjek produktnavn mod non-food keywords
    if not cat:
        name = hit.get('name', '').lower()
        return not any(kw in name for kw in NON_FOOD_KEYWORDS)

    # 4. Kategori er ukendt og ikke på whitelist — filtrer fra for en sikkerheds skyld
    return False

# ── Algolia ──────────────────────────────────────────────────────────────────
ALGOLIA_APP_ID = 'F9VBJLR1BK'
ALGOLIA_KEY    = 'd4f161f51f749bdd5baf699175d5f956'
ALGOLIA_INDEX  = 'prod_FOETEX_PRODUCTS'
ALGOLIA_URL    = f'https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query'
ALGOLIA_ATTRS  = ['name', 'gtin', 'objectID', 'units', 'unitsOfMeasure',
                  'consumerFacingHierarchy', 'categories', 'images',
                  'manufacturer', 'productType', 'properties']
ALGOLIA_HEADERS = {'X-Algolia-Application-Id': ALGOLIA_APP_ID, 'X-Algolia-API-Key': ALGOLIA_KEY}

# ── Salling ───────────────────────────────────────────────────────────────────
SALLING_KEY   = os.getenv('SALLING_API_KEY', '')
SALLING_BASE  = 'https://api.sallinggroup.com'
SALLING_STORE = os.getenv('FOETEX_SALLING_STORE', '15a4b863-66fb-4bba-9978-cfd1af0dd70c')  # føtex Herning (reference)
SALLING_DELAY = 1.1   # sekunder mellem Salling-kald (rate limit ~60/min)

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


def fetch_salling_price(ean: str, retries: int = 3) -> dict | None:
    if not SALLING_KEY or not SALLING_STORE:
        return None
    for attempt in range(retries):
        try:
            r = requests.get(f'{SALLING_BASE}/v2/products/{ean}',
                headers={'Authorization': f'Bearer {SALLING_KEY}'},
                params={'storeId': SALLING_STORE},
                timeout=10)
            if r.status_code == 200:
                return r.json().get('instore')
            if r.status_code == 429:
                wait = int(r.headers.get('Retry-After', 60))
                print(f'  429 rate limit — venter {wait}s...')
                time.sleep(wait)
                continue
            return None
        except Exception:
            if attempt < retries - 1:
                time.sleep(5)
    return None


def _cat(hit: dict) -> str:
    # Foetex bruger consumerFacingHierarchy (dybere hierarki end categories)
    hier = hit.get('consumerFacingHierarchy', {})
    lvl0 = hier.get('lvl0') or []
    if lvl0:
        return lvl0[0] if isinstance(lvl0, list) else str(lvl0)
    cats = hit.get('categories', {})
    return (cats.get('lvl0') or [''])[0]


def _kg_price(instore: dict | None) -> str | None:
    if not instore:
        return None
    val  = instore.get('unitPrice')
    unit = instore.get('unit')
    if val and unit:
        return f'{val} kr/{unit}'
    return None


def build_rows(hits: list[dict], prices: dict[str, dict]) -> list[dict]:
    rows = []
    for hit in hits:
        ean  = hit.get('gtin', '')
        naam = hit.get('name', '').strip()
        if not naam or not ean:
            continue

        instore  = prices.get(ean)
        pris     = float(instore['price'])     if instore and instore.get('price')       else None
        volumen  = instore.get('contents')     if instore else hit.get('units')
        vol_unit = instore.get('contentsUnit') if instore else hit.get('unitsOfMeasure')
        vaegt_str = f'{volumen} {vol_unit}' if volumen and vol_unit else None

        billede = (hit.get('images') or [''])[0]
        cat     = _cat(hit)
        mfr     = hit.get('manufacturer') or 'Salling'

        rows.append({
            'butik':        BUTIK,
            'kategori':     KATEGORI,
            'navn':         naam,
            'producent':    mfr,
            'netto_vaegt':  vaegt_str,
            'kg_price':     _kg_price(instore),
            'pris':         pris,
            'normalpris':   None,
            'varenummer':   ean,
            'billede_url':  billede,
            'billede_hash': None,
            'tilbud':       cat or 'Føtex katalog',
            'multikob':     None,
        })
    return rows


def save_to_supabase(rows: list[dict]):
    if not rows:
        print('  Ingen rækker.')
        return
    client = get_client()
    client.table('produkter').delete().eq('butik', BUTIK).eq('kategori', KATEGORI).execute()
    for i in range(0, len(rows), 500):
        client.table('produkter').insert(rows[i:i+500]).execute()
    print(f'  Gemt {len(rows)} rækker i Supabase')


def print_category_report(hits: list[dict]):
    """Printer unikke lvl0-kategorier og antal produkter — til at tune whitelisten."""
    from collections import Counter
    cats = Counter(_cat(h) or '(ingen)' for h in hits)
    print('\n  === Kategorioversigt ===')
    for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
        flag = '✓' if any(fc in cat.lower() for fc in _FOOD_CATEGORIES) else \
               '✗' if any(nf in cat.lower() for nf in _NON_FOOD_CATEGORIES) else '?'
        print(f'  {flag} {cat:45s} {n:5d} produkter')
    print()


def main():
    print('Starter Føtex katalog scraper...')

    hits = fetch_all_algolia()
    print_category_report(hits)
    food_hits = [h for h in hits if h.get('gtin') and _is_food_hit(h)]
    skipped_early = len(hits) - len(food_hits)
    print(f'  {len(hits)} produkter hentet → {skipped_early} ikke-mad fjernet → {len(food_hits)} fødevarer')
    eans = [h['gtin'] for h in food_hits]
    print(f'  {len(eans)} unikke EANs til prisopslag')

    prices: dict[str, dict] = {}
    if SALLING_KEY and SALLING_STORE:
        print(f'  Henter priser fra Salling API ({len(eans)} EANs, ~{len(eans)*SALLING_DELAY/60:.0f} min)...')
        done = 0
        for ean in eans:
            instore = fetch_salling_price(ean)
            if instore:
                prices[ean] = instore
            done += 1
            if done % 100 == 0:
                print(f'    {done}/{len(eans)} priser hentet ({len(prices)} fundet)...')
            time.sleep(SALLING_DELAY)
        print(f'  Salling done: {len(prices)} priser hentet')
    else:
        print('  SALLING_API_KEY mangler — gemmer uden priser')

    rows = build_rows(food_hits, prices)
    print(f'\nEksempel (første 3):')
    for r in rows[:3]:
        print(f"  {r['navn']:35s}  {r['pris'] or '?':>6} kr  {r['netto_vaegt'] or ''}  {r['tilbud']}")

    save_to_supabase(rows)
    print(f'\nFærdig! {len(rows)} Føtex-produkter gemt.')


if __name__ == '__main__':
    main()
