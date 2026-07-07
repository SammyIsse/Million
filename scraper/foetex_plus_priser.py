"""
Føtex+ personlige tilbud (+Priser) via p-club API.
Endpoint: /api/cp/personalizedOffer (samme platform som Netto+, men med Føtex-token)
"""
import os, sys, re, json, requests
from dotenv import load_dotenv

load_dotenv()
import io
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from supabase_utils import get_client, enrich_billede_hashes

# Genbrug den centrale, ordgrænse-baserede madfilter (creme-sikker, dækker elektronik).
try:
    from app_support import is_non_food_name as _is_non_food
except Exception:  # pragma: no cover - fallback hvis app_support ikke kan importeres
    import re
    from keywords import NON_FOOD_KEYWORDS
    _NF_RE = re.compile(
        r'(?<![0-9a-zæøåäöü])(?:'
        + '|'.join(re.escape(t) for t in sorted(NON_FOOD_KEYWORDS - {'creme'}, key=len, reverse=True))
        + r')(?![0-9a-zæøåäöü])',
        re.IGNORECASE,
    )

    def _is_non_food(navn: str) -> bool:
        return bool(navn) and _NF_RE.search(navn.lower()) is not None

TOKEN_FILE = os.path.join(os.path.dirname(__file__), '_foetex_token.json')
CLUB       = 'https://p-club.dsgapps.dk'
BUTIK      = 'Foetex'
KATEGORI   = 'Foetex+ +Priser'


def _load_token() -> str:
    env_token = os.getenv('FOETEX_ID_TOKEN')
    if env_token:
        return env_token
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f).get('id_token', '')
    return ''


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
        if _is_non_food(navn):
            continue  # fx elektronik (Samsung-tv, telefoner) - kun fødevarer ønskes

        pris       = float(o['price'])  if o.get('price')     is not None else None
        normalpris = _extract_price(o.get('nonMemberPriceLabelTxt') or '')

        kg_raw  = o.get('unitPriceText') or ''
        kg_pris = kg_raw.strip() if kg_raw.strip() else None

        udlob = (o.get('expiryTxtDetailed') or '').strip()

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
    print('Starter Føtex+ +Priser scraper (p-club API)...')
    token = _load_token()
    if not token:
        print('  Ingen FOETEX_ID_TOKEN eller _foetex_token.json - springer scraper over uden fejl.')
        return

    offers = fetch_offers(token)
    print(f'  {len(offers)} tilbud hentet fra p-club')

    rows = parse_offers(offers)

    print('\nEksempel (første 5):')
    for r in rows[:5]:
        print(f"  {r['navn']:40s}  {r['pris']:5.0f} kr  (norm: {r['normalpris']} kr)  {r['tilbud']}")

    save_to_supabase(rows)
    print(f'\nFærdig! {len(rows)} Føtex+ +Priser gemt.')


if __name__ == '__main__':
    main()
