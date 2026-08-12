"""Match opskrift-ingredienser (fritekst) mod produkter i app_cache.

Samme problem som butik<->butik-matchingen i updater.py, bare fritekst->produkt
i stedet for produkt->produkt (docs/Features.md "Opskrift-import og
-matching"). Genbruger byggeklodserne derfra (normalize_name/fuzzy_score/
get_meat_types) i stedet for at bygge en ny normaliseringsmotor - se README §
Product matching for hvorfor de gates findes.

Input er app_cache-formatede produkt-dicts (samme '/product/...'-nøgler som
updater.py/app_support.py bruger), IKKE den raa produkter-tabel (RLS-lukket,
foer-matching data).
"""

from __future__ import annotations

import json
import os
import re
import time

from app_support import (
    fuzzy_score, get_meat_types, get_product_flavors, logger, meats_match, normalize_name,
)

# Samme fil og friskheds-grænse som scripts/seed-d1.py::fetch_products().
# cache-updater.yml kører "python recipe_pricing.py" som næste step i SAMME
# job, lige efter "python updater.py" har skrevet denne fil FØR den uploader
# til Supabase (updater.py::_save_local_cache) - at hente de samme ~30 MB
# igen over netværket dér var ren Supabase-egress der aldrig gav noget nyt
# (samme fund som seed-d1.py's tilsvarende genbrug). recipe_importer.py kører
# i et andet workflow/runner uden filen til stede og falder derfor uændret
# tilbage til Supabase-hentningen nedenfor.
_LOCAL_APP_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'app_cache_local.json')
_LOCAL_APP_CACHE_MAX_AGE_S = 1800  # 30 min


def load_current_products(client) -> list[dict]:
    """Hent app_cache (samme tabel/format som updater.py._load_app_cache),
    kopieret i stedet for importeret for at undgå at trække hele updater.py's
    tunge scraper-afhængigheder ind i scripts der kun skal læse cachen
    (recipe_importer.py, recipe_pricing.py)."""
    if os.path.exists(_LOCAL_APP_CACHE):
        age_s = time.time() - os.path.getmtime(_LOCAL_APP_CACHE)
        if age_s < _LOCAL_APP_CACHE_MAX_AGE_S:
            try:
                with open(_LOCAL_APP_CACHE, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
                products = payload.get('products') or []
                if products:
                    logger.info(
                        f"Genbruger frisk data/app_cache_local.json ({len(products)} "
                        f"produkter, {age_s:.0f}s gammel) til opskrift-matching - "
                        f"springer Supabase-hentning over"
                    )
                    return products
            except Exception as e:
                logger.error(f"Kunne ikke læse lokal cache ({e}) - henter fra Supabase i stedet")

    try:
        rows = (
            client.table('app_cache')
            .select('id,data')
            .gte('id', 0)
            .order('id')
            .execute()
        ).data or []
    except Exception as e:
        logger.error(f"Kunne ikke hente app_cache til opskrift-matching: {e}")
        return []

    products = []
    for row in rows:
        if row.get('id') == 0:
            continue
        chunk = row.get('data', [])
        if isinstance(chunk, list):
            products.extend(chunk)
    return products

# Danske maaleenheder i opskrifter - et helt andet ordforraad end produkt-
# vaegtfeltets g/kg/l/ml/cl/dl (app_support._unit_to_grams), derfor egen alias-tabel.
_UNIT_ALIASES = {
    'g': 'g', 'gram': 'g', 'gr': 'g',
    'kg': 'kg', 'kilo': 'kg',
    'l': 'l', 'liter': 'l',
    'dl': 'dl',
    'cl': 'cl',
    'ml': 'ml',
    'spsk': 'spsk', 'spiseske': 'spsk', 'spiseskeer': 'spsk', 'ss': 'spsk',
    'tsk': 'tsk', 'teske': 'tsk', 'teskeer': 'tsk', 'ts': 'tsk',
    'stk': 'stk', 'styk': 'stk', 'stykker': 'stk',
    'fed': 'fed',
    'dåse': 'dåse', 'dåser': 'dåse', 'ds': 'dåse',
    'pakke': 'pakke', 'pk': 'pakke', 'pakker': 'pakke',
    'bundt': 'bundt',
    'knivspids': 'knivspids',
    'håndfuld': 'håndfuld',
    'fl': 'flaske', 'flaske': 'flaske', 'flasker': 'flaske',
    'glas': 'glas',
}

_UNIT_PATTERN = '|'.join(sorted((re.escape(u) for u in _UNIT_ALIASES), key=len, reverse=True))

# "2 dl mælk", "1/2 dl fløde", "3-4 æg", "1 fed hvidløg", "2 spsk. olie"
_QTY_RE = re.compile(
    rf'^\s*(?P<qty>\d+[.,]?\d*(?:\s*[-/]\s*\d+[.,]?\d*)?)\s*(?P<unit>{_UNIT_PATTERN})?\.?\s+(?P<rest>.+)$',
    re.IGNORECASE,
)


def _parse_quantity_token(token: str) -> float | None:
    token = token.strip().replace(',', '.')
    if '/' in token and '-' not in token:
        try:
            num, den = token.split('/')
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    if '-' in token:
        try:
            parts = [float(p) for p in token.split('-')]
            return sum(parts) / len(parts)
        except ValueError:
            return None
    try:
        return float(token)
    except ValueError:
        return None


def parse_ingredient_line(raw_text: str) -> dict:
    """Split '2 dl mælk' op i {quantity, unit, ingredient_name}.

    Best-effort regex - ustrukturerede linjer ('en smule salt', 'salt efter
    smag') matcher ikke _QTY_RE og falder tilbage til quantity=None/unit='' med
    hele linjen som ingredient_name, hvilket stadig er fint input til
    match_ingredient_to_product nedenfor.
    """
    text = (raw_text or '').strip()
    if not text:
        return {'quantity': None, 'unit': '', 'ingredient_name': ''}

    m = _QTY_RE.match(text)
    if m:
        unit_raw = (m.group('unit') or '').lower().rstrip('.')
        return {
            'quantity': _parse_quantity_token(m.group('qty')),
            'unit': _UNIT_ALIASES.get(unit_raw, ''),
            'ingredient_name': m.group('rest').strip(),
        }

    return {'quantity': None, 'unit': '', 'ingredient_name': text}


# Under denne fuzzy-score forsøges intet match - navnene er for forskellige
# til at det er meningsfuldt, selv som 'ai'-kandidat.
_MATCH_FLOOR = 0.55
# Over denne er matchet trygt nok til automatisk brug uden AI-fallback.
_MATCH_CONFIDENT = 0.78


def _score_candidates(ingredient_name: str, products: list[dict]) -> list[tuple]:
    """(score, product)-par for alle produkter der består kødtype-gaten,
    sorteret højeste score først. Delt af match_ingredient_to_product
    (top-1) og find_candidate_products (top-K, til mængde-baseret
    ompricing ved personer-skalering, se opskrift.html).

    Kødtype-gaten genbruges fra updater.py's produkt<->produkt-matching:
    hakket-kød-varianter deler næsten hele navnet på tværs af kødtyper
    ("hakket oksekød" vs "hakket kyllingekød"), så uden gaten ville en
    opskrift der beder om oksekød lige så let matche en kyllingevare.

    Smags-gaten (samme asymmetri som updater.py::_flavors_match: kandidaten
    må ikke nævne en smag basen ikke gør) er nødvendig HER specifikt for
    kandidat-listen (find_candidate_products) - fandt i praksis "kakaomælk"
    som billigere "kandidat" til "mælk" ved mængde-ompricing (2026-08-01),
    fordi ren navne-fuzzy ikke fanger at det er en anden vare.
    """
    query = normalize_name(ingredient_name)
    if not query:
        return []
    query_meats = get_meat_types(ingredient_name)
    query_flavors = get_product_flavors(ingredient_name)

    scored = []
    for product in products:
        title = product.get('/product/title', '')
        cand_name = normalize_name(title)
        if not cand_name:
            continue
        cand_meats = get_meat_types(title)
        if query_meats and cand_meats and not meats_match(query_meats, cand_meats):
            continue
        cand_flavors = get_product_flavors(title)
        if not (cand_flavors <= query_flavors):
            continue
        score = fuzzy_score(query, cand_name)
        if score >= _MATCH_FLOOR:
            scored.append((score, product))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def match_ingredient_to_product(ingredient_name: str, products: list[dict]) -> dict | None:
    """Find det bedste produkt-match til en fritekst-ingrediens.

    Returnerer {'product_id', 'confidence', 'method'} eller None hvis intet
    kandidat kommer over _MATCH_FLOOR. 'method' er 'exact' ved (næsten)
    identisk navn, ellers 'fuzzy' - kald med resultatet fra denne funktion når
    du beslutter om en AI-fallback skal forsøges for lav-tillid/None-resultater
    (se recipe_importer.py).
    """
    scored = _score_candidates(ingredient_name, products)
    if not scored:
        return None
    best_score, best_product = scored[0]
    return {
        'product_id': str(best_product.get('/product/id', '')),
        'confidence': round(best_score, 3),
        'method': 'exact' if best_score >= 0.97 else 'fuzzy',
    }


_CANDIDATE_TOP_K = 5


def find_candidate_products(ingredient_name: str, products: list[dict], top_k: int = _CANDIDATE_TOP_K) -> list[str]:
    """Top-K produkt-id'er (samme navne-/kødtype-gates som match_ingredient_to_product)
    til mængde-baseret ompricing: når personer-antallet ændres på opskrift-siden,
    kan den billigste pakkestørrelse for den nu krævede mængde skifte - fx en
    stor pakke der er billigere pr. kg end den lille standard-match. Beregnes
    én gang ved import/moderation (recipe_importer.py) og gemmes i
    recipe_ingredients.candidate_product_ids - pris/vægt for hvert id slås
    derimod op LIVE ved hver sidevisning (_fetch_recipe_detail i app.py)."""
    scored = _score_candidates(ingredient_name, products)
    return [str(p.get('/product/id', '')) for _, p in scored[:top_k]]


def match_recipe_ingredients(ingredients: list[dict], products: list[dict]) -> list[dict]:
    """Match en liste af {'raw_text', ...}-ingrediens-dicts mod produkt-cachen.

    Returnerer nye dicts (kopi af input beriget med quantity/unit/
    ingredient_name/matched_product_id/match_confidence/match_method) klar til
    at gemmes i recipe_ingredients - ingredienser uden godt match får
    match_method='unmatched' og matched_product_id=None fremfor at blive
    droppet, så opskriften stadig viser den fulde ingrediensliste.
    """
    results = []
    for ing in ingredients:
        raw_text = ing.get('raw_text', '')
        parsed = parse_ingredient_line(raw_text)
        name_for_matching = parsed['ingredient_name'] or raw_text
        # Ét scan af produkt-cachen pr. ingrediens, delt mellem top-1-matchet
        # og top-K-kandidaterne, i stedet for at scanne to gange.
        scored = _score_candidates(name_for_matching, products)
        best_score, best_product = scored[0] if scored else (None, None)
        results.append({
            **ing,
            'quantity': parsed['quantity'],
            'unit': parsed['unit'],
            'ingredient_name': parsed['ingredient_name'],
            'matched_product_id': str(best_product.get('/product/id', '')) if best_product else None,
            'match_confidence': round(best_score, 3) if best_score is not None else None,
            'match_method': ('exact' if best_score >= 0.97 else 'fuzzy') if best_score is not None else 'unmatched',
            'candidate_product_ids': [str(p.get('/product/id', '')) for _, p in scored[:_CANDIDATE_TOP_K]],
        })
    return results
