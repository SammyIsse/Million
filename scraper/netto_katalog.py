"""
Netto komplet produktkatalog scraper.
- Algolia prod_NETTO_PRODUCTS: navn, EAN, kategori, vægt, billede OG pris.
- Priser ligger direkte i Algolia-indekset (storeData[butik].price i øre) - ligesom
  Bilka. Vi bruger derfor IKKE længere Salling Group /v2/products API'et, som var
  rate-limitet (~60 kald/min + daglig kvote) og kun nåede at prissætte en brøkdel
  af kataloget. Nu får ~alle fødevarer en pris i ét træk.

Netto, Føtex og Bilka har samme moderfirma (Salling Group), men er separate kæder
med hver deres priser - derfor læses Netto-prisen fra Netto' eget indeks.
"""
import os, sys, requests, time
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client
from keywords import is_non_food

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
    'dyremad',  # "dyremad" rammer 'mad' i FOOD_CATEGORIES uden denne
    'bolig & køkken',  # gavepapir, LED-pærer, solcelle-items mv.
    'øvrig nonfood',   # penalhuse, bolde, tights mv.
    'leg',             # legetøj/bolde
    'kiosk',           # medicin (allergikapsler, smertestillende mv.)
    'byggemarked',     # brænde mv.
}


def _is_food_hit(hit: dict) -> bool:
    cat = _cat(hit).lower()

    # 1. Kategori-blacklist
    if any(nf in cat for nf in _NON_FOOD_CATEGORIES):
        return False

    # 2. Keyword-filter altid - fanger ikke-mad inden for fx "Baby & børn"
    if is_non_food(hit.get('name', '')):
        return False

    # 3. Kategori-whitelist
    if cat and any(fc in cat for fc in _FOOD_CATEGORIES):
        return True

    # 4. Ingen kategori = godkendt (keyword-tjek er allerede sket ovenfor)
    if not cat:
        return True

    return False


# ── Algolia ──────────────────────────────────────────────────────────────────
ALGOLIA_APP_ID = 'F9VBJLR1BK'
ALGOLIA_KEY    = 'd4f161f51f749bdd5baf699175d5f956'
ALGOLIA_INDEX  = 'prod_NETTO_PRODUCTS'
ALGOLIA_URL    = f'https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query'
ALGOLIA_ATTRS  = ['name', 'gtin', 'objectID', 'brand', 'manufacturer',
                  'units', 'unitsOfMeasure',
                  'categories', 'images', 'productType', 'properties',
                  'storeData']
ALGOLIA_HEADERS = {'X-Algolia-Application-Id': ALGOLIA_APP_ID, 'X-Algolia-API-Key': ALGOLIA_KEY}

# Reference-butik (fysisk Netto) til pris/tilbud. Falder tilbage til enhver butik
# med pris, hvis referencebutikken ikke fører varen.
REF_STORES = ['7701']

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


def _cat(hit: dict) -> str:
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
    # Nettos unitsOfMeasurePrice er upålidelig (matcher kun hyldeprisen ~76% af
    # tiden); unitsOfMeasureOfferPrice er den faktiske effektive per-enheds-pris
    # (verificeret 100% match mod pris for 1 l/1 kg-varer).
    r = ref or {}
    val  = r.get('unitsOfMeasureOfferPrice') or r.get('unitsOfMeasurePrice')
    unit = r.get('unitsOfMeasurePriceUnit')
    if val and unit:
        return f'{val / 100:.2f} kr/{_norm_unit(unit)}'
    return None


def _weight(hit: dict) -> str | None:
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
        price_ore = (ref or {}).get('price')
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
            # Netto sætter multipromo=0/'0' når der ikke er multikøb
            if mp and mp not in ('0', '0.0'):
                multikob = f'{mp} {mpp}'.strip()

        tilbud = 'Ja' if (on_offer or multikob) else 'Nej'
        producent = (hit.get('brand') or hit.get('manufacturer') or 'Salling').strip() or 'Salling'
        images = hit.get('images') or []
        billede = images[0] if images else ''

        rows.append({
            'butik':       BUTIK,
            'kategori':    KATEGORI,
            'navn':        naam,
            'producent':   producent,
            'netto_vaegt': _weight(hit),
            'kg_price':    _kg_price(hit, ref),
            'pris':        pris,
            'normalpris':  normalpris,
            'varenummer':  ean,
            'billede_url': billede,
            'billede_hash': None,
            'tilbud':      tilbud,
            'multikob':    multikob or None,
        })
    return rows


def save_to_supabase(rows: list[dict]):
    # Sikkerhed: en tom scraping må aldrig slette eksisterende data.
    if not rows:
        print('  Ingen rækker - beholder eksisterende Netto-data (intet slettet).')
        return
    client = get_client()
    client.table('produkter').delete().eq('butik', BUTIK).eq('kategori', KATEGORI).execute()
    for i in range(0, len(rows), 500):
        client.table('produkter').insert(rows[i:i+500]).execute()
    print(f'  Gemt {len(rows)} rækker i Supabase')


def main():
    print('Starter Netto katalog scraper (Algolia)...')

    hits = fetch_all_algolia()
    food_hits = [h for h in hits if h.get('gtin') and _is_food_hit(h)]
    print(f'  {len(hits)} produkter → {len(hits) - len(food_hits)} ikke-mad fjernet → {len(food_hits)} fødevarer')

    rows = build_rows(food_hits)
    print(f'  {len(rows)} rækker med pris bygget')

    print('\nEksempel (første 5):')
    for r in rows[:5]:
        print(f"  {r['navn']:35.35s} {r['pris']:>7} kr  {r['producent']:15.15s} "
              f"{r['netto_vaegt'] or '':>8}  tilbud={r['tilbud']}")

    save_to_supabase(rows)
    print(f'\nFærdig! {len(rows)} Netto-produkter gemt.')


if __name__ == '__main__':
    main()
