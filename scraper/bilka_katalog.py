"""
Bilka komplet produktkatalog scraper.
- Algolia prod_BILKATOGO_PRODUCTS: navn, EAN, kategori, vægt, billede OG pris (i øre)
- Priser ligger direkte i Algolia-indekset (storeData[butik].price) — ingen Salling Group
  API nødvendig, så vi rammer ikke den daglige API-kvote.

Erstatter den tidligere Selenium-baserede Webscrape_Bilka.py, der kun nåede
landingssiderne pr. topkategori og derfor manglede hele underkategorier (fx fersk
mælk under Mejeri > Mælk og fløde > Mælk).
"""
import os, sys, time, requests
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client
from keywords import NON_FOOD_KEYWORDS

# ── Algolia ──────────────────────────────────────────────────────────────────
ALGOLIA_APP_ID = 'F9VBJLR1BK'
ALGOLIA_KEY    = 'd4f161f51f749bdd5baf699175d5f956'
ALGOLIA_INDEX  = 'prod_BILKATOGO_PRODUCTS'
ALGOLIA_URL    = f'https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query'
ALGOLIA_ATTRS  = ['name', 'gtin', 'objectID', 'manufacturer', 'brand', 'subBrand',
                  'categories', 'images', 'netcontent', 'units', 'unitsOfMeasure',
                  'unitOfMeasurePrice', 'unitOfMeasurePriceUnits',
                  'price', 'sales_price', 'storeData', 'multibuy_offer_description']
ALGOLIA_HEADERS = {'X-Algolia-Application-Id': ALGOLIA_APP_ID, 'X-Algolia-API-Key': ALGOLIA_KEY}

# Mad-kategorier (lvl0) i Bilkas Algolia-hierarki. Vi henter pr. facet for at holde
# os under Algolias paginerings-grænse og for at filtrere non-food fra med det samme.
FOOD_LVL0 = ['Kolonial', 'Drikke', 'Kiosk', 'Køl', 'Brød og kager',
             'Mejeri', 'Frost', 'Frugt og grønt', 'Kød og fisk']

# Reference-varehuse (fysiske Bilka-butikker) i prioriteret rækkefølge — bruges til
# pris og tilbud. Falder tilbage til enhver butik med pris, ellers top-level pris.
REF_STORES = ['1651', '1661', '1653', '1658', '1659', '1662', '1663', '1664']

# ── Supabase ──────────────────────────────────────────────────────────────────
BUTIK    = 'Bilka'
KATEGORI = 'Katalog'


def fetch_category(cat: str) -> list[dict]:
    hits: list[dict] = []
    page, nb_pages = 0, 1
    while page < nb_pages:
        r = requests.post(ALGOLIA_URL, json={
            'query': '', 'hitsPerPage': 100, 'page': page,
            'facetFilters': [f'categories.lvl0:{cat}'],
            'attributesToRetrieve': ALGOLIA_ATTRS,
        }, headers=ALGOLIA_HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        nb_pages = data['nbPages']
        hits.extend(data['hits'])
        page += 1
        time.sleep(0.1)
    return hits


def fetch_all_algolia() -> list[dict]:
    all_hits: list[dict] = []
    seen: set = set()
    for cat in FOOD_LVL0:
        hits = fetch_category(cat)
        new = 0
        for h in hits:
            oid = h.get('objectID')
            if oid in seen:
                continue
            seen.add(oid)
            all_hits.append(h)
            new += 1
        print(f'  {cat:18s} {len(hits):5d} hits ({new} nye)')
    print(f'  Algolia done: {len(all_hits)} unikke produkter hentet')
    return all_hits


def _lvl0(hit: dict) -> str:
    return (hit.get('categories', {}).get('lvl0') or [''])[0]


def _is_food(hit: dict) -> bool:
    """Kategorierne er allerede mad (facet), men frasortér fx tobak via produktnavn."""
    name = (hit.get('name') or '').lower()
    return not any(kw in name for kw in NON_FOOD_KEYWORDS)


def _norm_unit(unit: str) -> str:
    return (unit or '').strip().rstrip('.').lower()


def _ref_store(hit: dict) -> dict | None:
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
    val  = (ref or {}).get('unitsOfMeasurePrice') or hit.get('unitOfMeasurePrice')
    unit = (ref or {}).get('unitsOfMeasurePriceUnit') or hit.get('unitOfMeasurePriceUnits')
    if val and unit:
        return f'{val / 100:.2f} kr/{_norm_unit(unit)}'
    return None


def build_rows(hits: list[dict]) -> list[dict]:
    rows = []
    for hit in hits:
        navn = (hit.get('name') or '').strip()
        if not navn:
            continue

        ref = _ref_store(hit)
        price_ore = (ref or {}).get('price') or hit.get('price') or hit.get('sales_price')
        if not price_ore:
            continue

        before = (ref or {}).get('beforePrice') or 0
        on_offer = bool(before and before > price_ore)
        pris       = round(price_ore / 100, 2)
        normalpris = round(before / 100, 2) if on_offer else None

        multikob = ''
        if ref:
            mp  = (ref.get('multipromo') or '').strip()
            mpp = str(ref.get('multiPromoPrice') or '').strip()
            if mp:
                multikob = f'{mp} {mpp}'.strip()
        if not multikob:
            multikob = (hit.get('multibuy_offer_description') or '').strip()

        tilbud = 'Ja' if (on_offer or multikob) else 'Nej'

        producent = (hit.get('brand') or hit.get('manufacturer') or 'Salling').strip() or 'Salling'
        images = hit.get('images') or []
        billede = images[0] if images else ''
        vaegt = (hit.get('netcontent') or '').strip() or None

        rows.append({
            'butik':        BUTIK,
            'kategori':     KATEGORI,
            'navn':         navn,
            'producent':    producent,
            'netto_vaegt':  vaegt,
            'kg_price':     _kg_price(hit, ref),
            'pris':         pris,
            'normalpris':   normalpris,
            'varenummer':   str(hit.get('gtin') or '').strip(),
            'billede_url':  billede,
            'billede_hash': None,
            'tilbud':       tilbud,
            'multikob':     multikob or None,
        })
    return rows


def save_to_supabase(rows: list[dict]):
    # Sikkerhed: en tom scraping må aldrig slette eksisterende data.
    if not rows:
        print('  Ingen rækker — beholder eksisterende Bilka-data (intet slettet).')
        return
    client = get_client()
    client.table('produkter').delete().eq('butik', BUTIK).execute()
    for i in range(0, len(rows), 500):
        client.table('produkter').insert(rows[i:i + 500]).execute()
    print(f'  Gemt {len(rows)} rækker i Supabase')


def main():
    print('Starter Bilka katalog scraper (Algolia)...')

    hits = fetch_all_algolia()
    food = [h for h in hits if _is_food(h)]
    print(f'  {len(hits)} produkter → {len(hits) - len(food)} ikke-mad (navn) fjernet → {len(food)} fødevarer')

    rows = build_rows(food)
    print(f'  {len(rows)} rækker med pris bygget')

    print('\nEksempel (første 5):')
    for r in rows[:5]:
        print(f"  {r['navn']:35.35s} {r['pris']:>7} kr  {r['producent']:15.15s} "
              f"{r['netto_vaegt'] or '':>8}  tilbud={r['tilbud']}")

    save_to_supabase(rows)
    print(f'\nFærdig! {len(rows)} Bilka-produkter gemt.')


if __name__ == '__main__':
    main()
