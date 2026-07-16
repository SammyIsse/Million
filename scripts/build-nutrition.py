#!/usr/bin/env python3
"""Bygger næringsdata for alle varekort i app_cache.

Kilder i prioriteret rækkefølge:
  1. Rema 1000 produkt-API (nutrition_info + declaration) - Rema-forankrede kort
  2. Salling Algolia-indeks (infos -> nutritional_100/ingredients) - alle EAN'er Salling fører
  3. Open Food Facts (opslag pr. EAN, ODbL - fri brug m. kildeangivelse) - alle resterende EAN'er,
     uanset butik (Coop, Lidl, Løvbjerg, ABC Lavpris m.fl.)

Output: data/nutrition_data.json
  {"built": <iso>, "sources": {"rema:<id>": payload, "ean:<ean>": payload}, "misses": [nøgler]}
"misses" er nøgler, der definitivt ikke gav data (404/tom deklaration) - de
genprøves ikke ved genoptagelse. Netværksfejl markeres ikke som miss.
Payload: {"per": "100 g", "rows": [{"label", "value"}], "ingredients": str|None, "source": "rema"|"salling"|"off"}

Kort-til-kilde-mapping udledes ved læsning via kandidatnøgler (se card_candidates)
- den gemmes ikke, da kort-id'er skifter ved hver cache-genopbygning.

Scriptet er genoptageligt: allerede hentede nøgler i output-filen springes over.
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))
sys.path.insert(0, ROOT)
from app_support import salling_sname_key, sname_key  # noqa: E402  (delt nøglelogik med runtime)

OUT_FILE = os.path.join(ROOT, 'data', 'nutrition_data.json')

# ── Algolia (samme offentlige nøgler som katalog-scraperne) ───────────────────
ALGOLIA_APP_ID = 'F9VBJLR1BK'
ALGOLIA_KEY = 'd4f161f51f749bdd5baf699175d5f956'
ALGOLIA_HEADERS = {'X-Algolia-Application-Id': ALGOLIA_APP_ID, 'X-Algolia-API-Key': ALGOLIA_KEY}
ALGOLIA_INDEXES = {
    'prod_BILKATOGO_PRODUCTS': ['Kolonial', 'Drikke', 'Kiosk', 'Køl', 'Brød og kager',
                                'Mejeri', 'Frost', 'Frugt og grønt', 'Kød og fisk'],
    'prod_FOETEX_PRODUCTS': ['Kolonial', 'Drikke', 'Kiosk', 'Køl', 'Brød og kager',
                             'Mejeri', 'Frost', 'Frugt og grønt', 'Kød og fisk'],
    'prod_NETTO_PRODUCTS': ['Mejeri & køl', 'Kolonial', 'Drikkevarer', 'Slik & snacks',
                            'Frost', 'Frugt & grønt', 'Brød & kager', 'Kød & fisk',
                            'Mad fra hele verden', 'Kiosk'],
}
# Algolia-indeks -> vores butiks-key. Bruges til navne-baseret genforbindelse
# af Salling-solokort (som taber deres EAN i cache-opbygningen) til varens egen
# infos-næring, jf. sname:-nøgler.
ALGOLIA_STORE_KEY = {
    'prod_BILKATOGO_PRODUCTS': 'bilka',
    'prod_FOETEX_PRODUCTS': 'foetex',
    'prod_NETTO_PRODUCTS': 'netto',
}

REMA_API = 'https://api.digital.rema1000.dk/api/v3/products/{pid}?include=declaration,nutrition_info'
OFF_UA = 'MadShopper/1.0 (kontakt@madshopper.dk)'

SALLING_STORE_KEYS = ('netto', 'bilka', 'foetex')
DAGROFA_STORE_KEYS = ('meny', 'spar', 'mk')


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# Reserveret nøgle: samler alle 'misses' (EAN'er uden næring nogen steder) i én
# JSON-række, så OFF-crawlen forbliver inkrementel i CI uden at genprøve kendte
# tomme koder hver nat. Appen slår aldrig denne nøgle op (kort danner kun
# rema:/ean:/sname:), så den er usynlig for runtime.
MISSES_KEY = '__misses__'


def _supabase_creds():
    base = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    key = os.getenv('DEPLOY_KEY') or os.getenv('SUPABASE_KEY')
    return base, key


def load_state_from_supabase() -> tuple[dict, set]:
    """Genoptag fra Supabase nutrition_data når der ingen lokal fil er (CI)."""
    base, key = _supabase_creds()
    sources, misses = {}, set()
    if not base or not key:
        return sources, misses
    headers = {'apikey': key, 'Authorization': f'Bearer {key}'}
    step, offset = 1000, 0
    with requests.Session() as sess:
        while True:
            r = sess.get(f"{base}/rest/v1/nutrition_data",
                         params={'select': 'key,payload', 'order': 'key.asc',
                                 'limit': step, 'offset': offset},
                         headers=headers, timeout=60)
            r.raise_for_status()
            rows = r.json()
            for row in rows:
                if row['key'] == MISSES_KEY:
                    misses = set((row.get('payload') or {}).get('keys', []))
                else:
                    sources[row['key']] = row['payload']
            if len(rows) < step:
                break
            offset += step
    return sources, misses


def push_to_supabase(sources: dict, misses: set | None = None) -> None:
    """Upsert alle kilder til Supabase-tabellen nutrition_data (kør
    scripts/supabase-nutrition.sql først). Misses gemmes samlet i én reserveret
    række (MISSES_KEY). Samme batch-mønster som updater.py bruger til price_history."""
    base, key = _supabase_creds()
    if not base or not key:
        log('Supabase-push sprunget over: SUPABASE_URL/DEPLOY_KEY mangler i .env')
        return

    records = [{'key': k, 'payload': v} for k, v in sources.items()]
    if misses is not None:
        records.append({'key': MISSES_KEY, 'payload': {'keys': sorted(misses)}})
    upsert_url = f"{base}/rest/v1/nutrition_data?on_conflict=key"
    headers = {
        'apikey': key, 'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal,resolution=merge-duplicates',
    }

    chunk_size = 500
    posted = 0
    with requests.Session() as sess:
        for i in range(0, len(records), chunk_size):
            chunk = records[i:i + chunk_size]
            for attempt in range(3):
                r = sess.post(upsert_url, headers=headers, data=json.dumps(chunk), timeout=60)
                if r.ok:
                    posted += len(chunk)
                    break
                time.sleep(1.5 * (attempt + 1))
            else:
                log(f'Supabase-push fejlede permanent ved batch {i}: HTTP {r.status_code} {r.text[:200]}')
                continue
            if (i // chunk_size + 1) % 5 == 0:
                log(f'Supabase-push: {posted}/{len(records)}')
    log(f'Supabase-push færdig: {posted}/{len(records)} rækker upsertet')


def valid_ean(e):
    e = str(e or '').strip()
    return e if e.isdigit() and len(e) in (8, 12, 13, 14) else None


# ── app_cache ─────────────────────────────────────────────────────────────────
def load_app_cache():
    base = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    key = os.getenv('SUPABASE_KEY') or os.getenv('NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY')
    if not base or not key:
        raise RuntimeError('SUPABASE_URL/KEY mangler i .env')
    url = f"{base}/rest/v1/app_cache?select=*&id=gte.0&order=id.asc"
    res = requests.get(url, headers={'apikey': key, 'Authorization': f'Bearer {key}'}, timeout=60)
    res.raise_for_status()
    products = []
    for row in res.json():
        if row.get('id') != 0 and isinstance(row.get('data'), list):
            products.extend(row['data'])
    return products


def card_candidates(card):
    """Prioriterede kilde-nøgler for et varekort: Rema først, så EAN fra en
    hvilken som helst butik i den matchede gruppe (Salling/Dagrofa først, da de
    har højest hit-rate, derefter alle øvrige butikker)."""
    keys = []
    try:
        if float(card.get('/product/rema_price') or 0) > 0:
            keys.append(f"rema:{card['/product/id']}")
    except (TypeError, ValueError):
        pass
    # Solokortets eget EAN (samme som nutrition_candidate_keys i runtime) + gruppens.
    own = valid_ean(card.get('/product/ean'))
    if own:
        keys.append(f"ean:{own}")
    sm = card.get('/product/store_matches') or {}
    ordered = list(SALLING_STORE_KEYS + DAGROFA_STORE_KEYS)
    ordered += [s for s in sm if s not in ordered]
    for store in ordered:
        ean = valid_ean((sm.get(store) or {}).get('ean'))
        if ean:
            k = f"ean:{ean}"
            if k not in keys:
                keys.append(k)
    sname = salling_sname_key(card)
    if sname:
        keys.append(sname)
    return keys


# ── Salling Algolia ───────────────────────────────────────────────────────────
def parse_infos(infos):
    """Uddrag næringstabel + ingredienser af Sallings infos-sektioner."""
    rows, per, ingredients = [], '100 g', None
    for sec in infos or []:
        if not isinstance(sec, dict):
            continue  # enkelte varer har sektioner som rene strenge
        code = sec.get('code', '')
        if code == 'nutritional_100':
            for item in sec.get('items', []):
                if not isinstance(item, dict):
                    continue
                title = (item.get('title') or '').strip()
                value = (item.get('value') or '').strip()
                if not title or not value:
                    continue
                if title.lower().startswith('næringsindhold pr'):
                    per = value
                else:
                    rows.append({'label': title, 'value': value})
        elif code == 'ingredients':
            items = [i for i in sec.get('items', []) if isinstance(i, dict)]
            if items:
                ingredients = (items[0].get('value') or '').strip() or None
    if not rows:
        return None
    return {'per': per, 'rows': rows, 'ingredients': ingredients, 'source': 'salling'}


def _rows_signature(payload):
    """Sammenlignelig signatur af en næringstabel (til at opdage navne-kollisioner)."""
    return tuple((r['label'], r['value']) for r in payload.get('rows', []))


def fetch_algolia_map(wanted_eans):
    """Dump fødevarekategorierne i alle tre indekser. Returnerer to slags nøgler:
      - ean:<ean>            for ønskede EAN'er (matchede kort)
      - sname:<butik>:<navn> for ALLE varer, så Salling-solokort der har tabt
                             deres EAN i cache-opbygningen kan genforbindes på
                             normaliseret navn til varens egen infos-næring.
    Et sname-navn der peger på to varer med FORSKELLIG næringstabel droppes
    (tvetydigt) - så viser vi hellere ingenting end en forkert vares værdier."""
    found = {}
    # sname-nøgle -> payload, eller AMBIG hvis flere varer med samme navn har
    # forskellig næring i samme butik.
    AMBIG = object()
    sname = {}
    sess = requests.Session()
    for index, cats in ALGOLIA_INDEXES.items():
        store_key = ALGOLIA_STORE_KEY[index]
        url = f'https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{index}/query'
        idx_hits = 0
        for cat in cats:
            page, nb_pages = 0, 1
            while page < nb_pages:
                r = sess.post(url, json={
                    'query': '', 'hitsPerPage': 1000, 'page': page,
                    'facetFilters': [f'categories.lvl0:{cat}'],
                    'attributesToRetrieve': ['gtin', 'infos', 'name'],
                    'attributesToHighlight': [],
                }, headers=ALGOLIA_HEADERS, timeout=30)
                r.raise_for_status()
                data = r.json()
                nb_pages = data.get('nbPages', 1)
                for hit in data.get('hits', []):
                    idx_hits += 1
                    payload = parse_infos(hit.get('infos'))
                    if not payload:
                        continue
                    ean = valid_ean(hit.get('gtin'))
                    if ean and ean in wanted_eans and f'ean:{ean}' not in found:
                        found[f'ean:{ean}'] = payload
                    # Navne-indeks til genforbindelse af solokort
                    key = sname_key(store_key, hit.get('name'))
                    if key:
                        prev = sname.get(key)
                        if prev is None:
                            sname[key] = payload
                        elif prev is not AMBIG and _rows_signature(prev) != _rows_signature(payload):
                            sname[key] = AMBIG
                page += 1
                time.sleep(0.05)
        log(f'Algolia {index}: {idx_hits} varer gennemgået, {len(found)} EAN-match indtil videre')
    kept = {k: v for k, v in sname.items() if v is not AMBIG}
    dropped = len(sname) - len(kept)
    found.update(kept)
    log(f'Algolia navne-indeks: {len(kept)} sname-nøgler ({dropped} tvetydige droppet)')
    return found


# ── Rema 1000 ─────────────────────────────────────────────────────────────────
def fetch_rema_one(pid, sess):
    """Returnerer (pid, payload|None, status) hvor status er 'ok', 'miss' eller 'err'."""
    for attempt in range(2):
        try:
            r = sess.get(REMA_API.format(pid=pid), timeout=15)
            if r.status_code == 404:
                return pid, None, 'miss'
            if r.status_code == 429:
                time.sleep(2.0)
                continue
            r.raise_for_status()
            d = r.json().get('data', {})
            rows = []
            for item in d.get('nutrition_info') or []:
                label = (item.get('name') or '').strip()
                value = (item.get('value') or '').strip()
                if not label or not value:
                    continue
                # Rema angiver gram-værdier uden enhed - tilføj for ensartet visning
                if not re.search(r'[a-zA-Z]', value):
                    value = f'{value} g'
                rows.append({'label': label, 'value': value})
            if not rows:
                return pid, None, 'miss'
            ingredients = (d.get('declaration') or '').strip() or None
            return pid, {'per': '100 g', 'rows': rows, 'ingredients': ingredients, 'source': 'rema'}, 'ok'
        except requests.RequestException:
            time.sleep(1.0)
    return pid, None, 'err'


def fetch_rema_map(pids, done_keys, misses):
    todo = [p for p in pids if f'rema:{p}' not in done_keys and f'rema:{p}' not in misses]
    log(f'Rema: {len(todo)} opslag ({len(pids) - len(todo)} allerede afklaret)')
    found = {}
    sess = requests.Session()
    sess.headers['User-Agent'] = 'Mozilla/5.0'
    with ThreadPoolExecutor(3) as ex:
        for i, (pid, payload, status) in enumerate(ex.map(lambda p: fetch_rema_one(p, sess), todo), 1):
            if payload:
                found[f'rema:{pid}'] = payload
            elif status == 'miss':
                misses.add(f'rema:{pid}')
            if i % 250 == 0:
                log(f'Rema: {i}/{len(todo)} ({len(found)} med næring)')
    return found


# ── Open Food Facts ───────────────────────────────────────────────────────────
def _fmt_num(v):
    s = f'{float(v):.1f}'.rstrip('0').rstrip('.')
    return s.replace('.', ',')


def off_payload(nutriments, ingredients):
    rows = []
    kj, kcal = nutriments.get('energy-kj_100g') or nutriments.get('energy_100g'), nutriments.get('energy-kcal_100g')
    if kj or kcal:
        parts = []
        if kj:
            parts.append(f'{_fmt_num(kj)} kJ')
        if kcal:
            parts.append(f'{_fmt_num(kcal)} kcal')
        rows.append({'label': 'Energi', 'value': ' / '.join(parts)})
    for key, label in [('fat', 'Fedt'), ('saturated-fat', 'Heraf mættede fedtsyrer'),
                       ('carbohydrates', 'Kulhydrat'), ('sugars', 'Heraf sukkerarter'),
                       ('fiber', 'Kostfibre'), ('proteins', 'Protein'), ('salt', 'Salt')]:
        v = nutriments.get(f'{key}_100g')
        if v is not None:
            rows.append({'label': label, 'value': f'{_fmt_num(v)} g'})
    if not rows:
        return None
    return {'per': '100 g', 'rows': rows, 'ingredients': ingredients or None, 'source': 'off'}


OFF_FIELDS = 'code,nutriments,ingredients_text_da,ingredients_text'


def fetch_off_map(eans, done_keys, misses, flush_cb=None):
    """Enkeltopslag mod produkt-endpointet (max 100 kald/min).
    Search-endpointet 503'er konsekvent ved batches over få koder, så det bruges ikke."""
    todo = sorted(e for e in eans if f'ean:{e}' not in done_keys and f'ean:{e}' not in misses)
    log(f'Open Food Facts: {len(todo)} EAN-enkeltopslag')
    found = {}
    sess = requests.Session()
    sess.headers['User-Agent'] = OFF_UA

    for i, ean in enumerate(todo, 1):
        try:
            r = sess.get(f'https://world.openfoodfacts.org/api/v2/product/{ean}',
                         params={'fields': OFF_FIELDS}, timeout=15)
            if r.status_code == 200:
                prod = r.json().get('product') or {}
                payload = off_payload(prod.get('nutriments') or {},
                                      prod.get('ingredients_text_da') or prod.get('ingredients_text'))
                if payload:
                    found[f'ean:{ean}'] = payload
                else:
                    misses.add(f'ean:{ean}')
            elif r.status_code == 404:
                misses.add(f'ean:{ean}')
            elif r.status_code == 429:
                time.sleep(30)
        except requests.RequestException:
            pass
        time.sleep(0.65)  # hold os under 100 kald/min
        if i % 250 == 0:
            log(f'OFF: {i}/{len(todo)} ({len(found)} med næring)')
            if flush_cb:
                flush_cb(found)
    return found


# ── Hovedforløb ───────────────────────────────────────────────────────────────
def main():
    sources, misses = {}, set()
    if os.path.exists(OUT_FILE):
        with open(OUT_FILE, encoding='utf-8') as f:
            existing = json.load(f)
        sources = existing.get('sources', {})
        misses = set(existing.get('misses', [])) - set(sources)
        log(f'Genoptager: {len(sources)} kilder og {len(misses)} kendte misses i {OUT_FILE}')
    else:
        # CI: ingen lokal fil - genoptag fra Supabase, så kørslen er inkrementel
        sources, misses = load_state_from_supabase()
        misses -= set(sources)
        log(f'Genoptager fra Supabase: {len(sources)} kilder og {len(misses)} misses')

    def flush():
        with open(OUT_FILE, 'w', encoding='utf-8') as f:
            json.dump({'built': datetime.now(timezone.utc).isoformat(),
                       'sources': sources, 'misses': sorted(misses)},
                      f, ensure_ascii=False)

    log('Henter app_cache fra Supabase...')
    cards = load_app_cache()
    log(f'{len(cards)} varekort indlæst')

    rema_pids, salling_eans, dagrofa_eans, other_eans = set(), set(), set(), set()
    for card in cards:
        for key in card_candidates(card):
            kind, _, val = key.partition(':')
            if kind == 'rema':
                rema_pids.add(val)
        sm = card.get('/product/store_matches') or {}
        for store, match in sm.items():
            ean = valid_ean((match or {}).get('ean'))
            if not ean:
                continue
            if store in SALLING_STORE_KEYS:
                salling_eans.add(ean)
            elif store in DAGROFA_STORE_KEYS:
                dagrofa_eans.add(ean)
            else:
                other_eans.add(ean)
        # Solokortets eget EAN (bevaret af updater.py's build_store_display_products).
        # Uden dette ville de genskabte solokort-EAN'er aldrig blive slået op.
        own = valid_ean(card.get('/product/ean'))
        if own:
            label = card.get('/product/store')
            if label in ('Bilka', 'Netto', 'Føtex'):
                salling_eans.add(own)
            elif label in ('Meny', 'Spar', 'Min Købmand'):
                dagrofa_eans.add(own)
            else:
                other_eans.add(own)
    log(f'Nøgler: {len(rema_pids)} Rema-id, {len(salling_eans)} Salling-EAN, '
        f'{len(dagrofa_eans)} Dagrofa-EAN, {len(other_eans)} øvrige EAN')

    all_eans = salling_eans | dagrofa_eans | other_eans

    # 1) Salling Algolia - køres ALTID (billigt, ~2 min). Genopbygger sname-
    #    navneindekset og fanger nye varers Salling-næring hver nat. Læser kun
    #    Sallings eget offentlige indeks.
    sources.update(fetch_algolia_map(all_eans))
    flush()
    log(f'Efter Algolia: {len(sources)} kilder')

    # 2) Rema
    sources.update(fetch_rema_map(sorted(rema_pids), sources, misses))
    flush()
    log(f'Efter Rema: {len(sources)} kilder')

    # 3) Open Food Facts for alle EAN'er der stadig mangler
    def off_flush(found):
        sources.update(found)
        flush()

    sources.update(fetch_off_map(all_eans, sources, misses, flush_cb=off_flush))
    flush()
    log(f'Efter OFF: {len(sources)} kilder')

    # ── Dækningsrapport ──────────────────────────────────────────────────────
    total = covered = 0
    per_store = {}
    for card in cards:
        total += 1
        hit = next((k for k in card_candidates(card) if k in sources), None)
        if hit:
            covered += 1
        label = card.get('/product/store', '?')
        t, c = per_store.get(label, (0, 0))
        per_store[label] = (t + 1, c + (1 if hit else 0))
    log(f'DÆKNING: {covered}/{total} varekort = {100 * covered / total:.1f}%')
    for label, (t, c) in sorted(per_store.items(), key=lambda kv: -kv[1][0]):
        log(f'  {label:15} {c:5}/{t:5} = {100 * c / t:5.1f}%')

    push_to_supabase(sources, misses)


if __name__ == '__main__':
    sys.exit(main())
