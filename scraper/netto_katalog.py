"""
Netto komplet produktkatalog scraper.
- Algolia prod_NETTO_PRODUCTS: navn, EAN, kategori, vægt, billede (4000+ produkter)
- Salling Group /v2/products/{ean}: normalpriser (kun nye fødevare-EANs — eksisterende priser genbruges)
"""
import os, sys, re, time, requests, threading, concurrent.futures
from typing import cast
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client
from keywords import NON_FOOD_KEYWORDS

# ── Madfilter ────────────────────────────────────────────────────────────────
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
    'mad', 'fødevarer', 'dagligvarer',
}

_NON_FOOD_CATEGORIES = {
    'non-food', 'personlig pleje', 'helse', 'husholdning',
    'tøj', 'sko', 'sport', 'fritid', 'elektronik',
    'blomster', 'planter', 'have', 'kæledyr',
    'legetøj', 'hobby', 'bøger', 'magasiner',
    'rengøring', 'vask', 'skønhed', 'beauty',
}


def _is_food_hit(hit: dict) -> bool:
    cat = _cat(hit).lower()
    if any(nf in cat for nf in _NON_FOOD_CATEGORIES):
        return False
    if cat and any(fc in cat for fc in _FOOD_CATEGORIES):
        return True
    if not cat:
        name = hit.get('name', '').lower()
        return not any(kw in name for kw in NON_FOOD_KEYWORDS)
    return False


# ── Algolia ──────────────────────────────────────────────────────────────────
ALGOLIA_APP_ID = 'F9VBJLR1BK'
ALGOLIA_KEY    = 'd4f161f51f749bdd5baf699175d5f956'
ALGOLIA_INDEX  = 'prod_NETTO_PRODUCTS'
ALGOLIA_URL    = f'https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query'
ALGOLIA_ATTRS  = ['name', 'gtin', 'objectID', 'units', 'unitsOfMeasure',
                  'categories', 'images', 'manufacturer', 'productType', 'properties']
ALGOLIA_HEADERS = {'X-Algolia-Application-Id': ALGOLIA_APP_ID, 'X-Algolia-API-Key': ALGOLIA_KEY}

# ── Salling ───────────────────────────────────────────────────────────────────
SALLING_KEY   = os.getenv('SALLING_API_KEY', '')
SALLING_BASE  = 'https://api.sallinggroup.com'
SALLING_STORE = '2da2b92a-25c8-48cf-a4a1-7c56b4469a02'  # Netto Aalborg (reference)
SALLING_DELAY = 1.1   # sekunder mellem Salling-kald (rate limit ~60/min)

# ── Supabase ──────────────────────────────────────────────────────────────────
BUTIK   = 'Netto'
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


_DAILY_LIMIT_RETRY_THRESHOLD = 300  # sekunder — over denne = dagslimit ramt


def fetch_salling_price(ean: str, stop_flag: threading.Event, retries: int = 3) -> dict | None:
    if not SALLING_KEY or stop_flag.is_set():
        return None
    for attempt in range(retries):
        if stop_flag.is_set():
            return None
        try:
            r = requests.get(f'{SALLING_BASE}/v2/products/{ean}',
                headers={'Authorization': f'Bearer {SALLING_KEY}'},
                params={'storeId': SALLING_STORE},
                timeout=10)
            if r.status_code == 200:
                return r.json().get('instore')
            if r.status_code == 429:
                wait = int(r.headers.get('Retry-After', 60))
                if wait > _DAILY_LIMIT_RETRY_THRESHOLD:
                    print(f'  Daglig API-grænse nået (Retry-After: {wait}s) — stopper og gemmer akkumulerede priser')
                    stop_flag.set()
                    return None
                print(f'  429 rate limit — venter {wait}s...')
                time.sleep(wait)
                continue
            return None
        except Exception:
            if attempt < retries - 1:
                time.sleep(5)
    return None


class _RateLimit:
    """Global rate limiter: maks. ét Salling-kald per interval på tværs af tråde."""
    def __init__(self, interval: float):
        self._lock = threading.Lock()
        self._next = 0.0
        self._interval = interval

    def wait(self):
        with self._lock:
            delay = self._next - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._next = time.monotonic() + self._interval


def fetch_prices_parallel(eans: list[str]) -> dict[str, dict]:
    """Henter Salling-priser med rate-limit og graceful stop ved daglig kvote."""
    results: dict[str, dict] = {}
    lock = threading.Lock()
    counter = [0]
    total = len(eans)
    rate = _RateLimit(SALLING_DELAY)
    stop_flag = threading.Event()

    def _worker(ean: str):
        if stop_flag.is_set():
            return
        rate.wait()
        instore = fetch_salling_price(ean, stop_flag)
        with lock:
            if instore:
                results[ean] = instore
            counter[0] += 1
            if counter[0] % 100 == 0:
                print(f'    {counter[0]}/{total} kald ({len(results)} priser i alt)...')

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(_worker, eans))

    if stop_flag.is_set():
        print(f'  Kvote opbrugt efter {len(results)} nye priser — fortsætter i morgen')
    return results


def _cat(hit: dict) -> str:
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

        instore = prices.get(ean)
        pris     = float(instore['price'])     if instore and instore.get('price')       else None
        volumen  = instore.get('contents')     if instore else hit.get('units')
        vol_unit = instore.get('contentsUnit') if instore else hit.get('unitsOfMeasure')
        vaegt_str = f'{volumen} {vol_unit}' if volumen and vol_unit else None

        billede = (hit.get('images') or [''])[0]
        cat     = _cat(hit)
        mfr     = hit.get('manufacturer') or 'Salling'

        rows.append({
            'butik':       BUTIK,
            'kategori':    KATEGORI,
            'navn':        naam,
            'producent':   mfr,
            'netto_vaegt': vaegt_str,
            'kg_price':    _kg_price(instore),
            'pris':        pris,
            'normalpris':  None,
            'varenummer':  ean,
            'billede_url': billede,
            'billede_hash': None,
            'tilbud':      cat or 'Netto katalog',
            'multikob':    None,
        })
    return rows


def load_existing_prices() -> dict[str, dict]:
    """Henter gemte priser fra Supabase og rekonstruerer instore-dict per EAN."""
    client = get_client()
    existing: dict[str, dict] = {}
    last_id = -1
    while True:
        res = (client.table('produkter')
               .select('id, varenummer, pris, kg_price, netto_vaegt')
               .eq('butik', BUTIK)
               .eq('kategori', KATEGORI)
               .gt('id', last_id)
               .order('id')
               .limit(1000)
               .execute())
        data = cast(list[dict], list(res.data or []))
        if not data:
            break
        for row in data:
            ean = str(row.get('varenummer') or '').strip()
            if not ean or row.get('pris') is None:
                continue
            contents = contents_unit = unit_price = unit = None
            m = re.match(r'([\d.,]+)\s*(\S+)', str(row.get('netto_vaegt') or ''))
            if m:
                try:
                    contents = float(m.group(1).replace(',', '.'))
                    contents_unit = m.group(2)
                except ValueError:
                    pass
            km = re.match(r'([\d.,]+)\s*kr/(\S+)', str(row.get('kg_price') or ''), re.IGNORECASE)
            if km:
                try:
                    unit_price = float(km.group(1).replace(',', '.'))
                    unit = km.group(2)
                except ValueError:
                    pass
            existing[ean] = {
                'price': row['pris'],
                'contents': contents,
                'contentsUnit': contents_unit,
                'unitPrice': unit_price,
                'unit': unit,
            }
        if len(data) < 1000:
            break
        last_id = data[-1]['id']
    print(f'  Eksisterende priser indlæst: {len(existing)} EANs')
    return existing


def save_to_supabase(rows: list[dict]):
    if not rows:
        print('  Ingen rækker.')
        return
    client = get_client()
    client.table('produkter').delete().eq('butik', BUTIK).eq('kategori', KATEGORI).execute()
    for i in range(0, len(rows), 500):
        client.table('produkter').insert(rows[i:i+500]).execute()
    print(f'  Gemt {len(rows)} rækker i Supabase')


def main():
    print('Starter Netto katalog scraper...')

    # 1) Algolia – hent alle produkter og filtrer til fødevarer
    hits = fetch_all_algolia()
    food_hits = [h for h in hits if h.get('gtin') and _is_food_hit(h)]
    print(f'  {len(hits)} produkter → {len(hits) - len(food_hits)} ikke-mad fjernet → {len(food_hits)} fødevarer')
    eans = [h['gtin'] for h in food_hits]

    # 2) Indlæs eksisterende priser fra Supabase
    prices = load_existing_prices()

    # 3) Salling – hent kun priser for EANs der mangler
    missing = [ean for ean in eans if ean not in prices]
    if SALLING_KEY and missing:
        print(f'  Henter priser fra Salling API for {len(missing)} nye EANs (~{len(missing)*SALLING_DELAY/60:.0f} min)...')
        new_prices = fetch_prices_parallel(missing)
        prices.update(new_prices)
        print(f'  Salling done: {len(prices)} priser i alt')
    elif not SALLING_KEY:
        print('  SALLING_API_KEY mangler — bruger kun eksisterende priser')
    else:
        print(f'  Alle EANs har allerede priser — springer Salling API over')

    # 4) Byg rækker og gem
    rows = build_rows(food_hits, prices)
    print(f'\nEksempel (første 3):')
    for r in rows[:3]:
        print(f"  {r['navn']:35s}  {r['pris'] or '?':>6} kr  {r['netto_vaegt'] or ''}  {r['tilbud']}")

    save_to_supabase(rows)
    print(f'\nFærdig! {len(rows)} Netto-produkter gemt.')


if __name__ == '__main__':
    main()
