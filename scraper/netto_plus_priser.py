"""
Netto+ personlige tilbud (+Priser) via p-club API.
Erstatter webscrape_netto2.py (netto.dk scraping, forbudt).
Endpoint: /api/cp/personalizedOffer
"""
import os, sys, re, json, requests
from dotenv import load_dotenv

load_dotenv()
import io
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from supabase_utils import get_client, enrich_billede_hashes

TOKEN_FILE = os.path.join(os.path.dirname(__file__), '_netto_token.json')
CLUB       = 'https://p-club.dsgapps.dk'
BUTIK      = 'Netto'
KATEGORI   = 'Netto+ +Priser'


def _load_token() -> str:
    env_token = os.getenv('NETTO_ID_TOKEN')
    if env_token:
        return env_token
    with open(TOKEN_FILE) as f:
        return json.load(f)['id_token']


def _extract_price(text: str) -> float | None:
    m = re.search(r'(\d+(?:[.,]\d+)?)', text.strip())
    if m:
        return float(m.group(1).replace(',', '.'))
    return None


def fetch_offers(token: str) -> list[dict]:
    r = requests.get(
        f'{CLUB}/api/cp/personalizedOffer',
        headers={'Authorization': f'Bearer {token}'},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def parse_offers(offers: list[dict]) -> list[dict]:
    rows = []
    for o in offers:
        navn = (o.get('title') or '').strip().rstrip('.')
        if not navn:
            continue

        pris     = float(o['price'])  if o.get('price')     is not None else None
        normalpris = _extract_price(o.get('nonMemberPriceLabelTxt') or '')

        kg_raw = o.get('unitPriceText') or ''
        kg_pris = kg_raw.strip() if kg_raw.strip() else None

        udlob = (o.get('expiryTxtDetailed') or '').strip()

        # Varenummer: articleNumber (Netto internt, ikke standard EAN)
        varenummer = str(o.get('articleNumber') or '') or None

        rows.append({
            'butik':        BUTIK,
            'kategori':     KATEGORI,
            'navn':         navn,
            'producent':    None,
            'netto_vaegt':  None,
            'kg_price':     kg_pris,
            'pris':         pris,
            'normalpris':   str(normalpris) if normalpris is not None else None,
            'varenummer':   varenummer,
            'billede_url':  o.get('imageUrl') or '',
            'billede_hash': None,
            'tilbud':       udlob or 'Ja',
            'multikob':     None,
        })
    enrich_billede_hashes(rows)
    return rows


def save_to_supabase(rows: list[dict]):
    if not rows:
        print('  Ingen rækker at gemme.')
        return
    client = get_client()
    client.table('produkter').delete().eq('butik', BUTIK).eq('kategori', KATEGORI).execute()
    for i in range(0, len(rows), 500):
        client.table('produkter').insert(rows[i:i+500]).execute()
    print(f'  Gemt {len(rows)} rækker i Supabase')


def main():
    print('Starter Netto+ +Priser scraper (p-club API)...')
    token = _load_token()

    offers = fetch_offers(token)
    print(f'  {len(offers)} tilbud hentet fra p-club')

    rows = parse_offers(offers)

    print('\nEksempel (første 5):')
    for r in rows[:5]:
        print(f"  {r['navn']:40s}  {r['pris']:5.0f} kr  (norm: {r['normalpris']} kr)  {r['tilbud']}")

    save_to_supabase(rows)
    print(f'\nFærdig! {len(rows)} Netto+ +Priser gemt.')


if __name__ == '__main__':
    main()
