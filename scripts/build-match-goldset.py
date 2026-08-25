#!/usr/bin/env python3
"""Byg et guldsæt af verificerede produktpar til måling af matchmotoren.

Baggrund
--------
Matchmotoren i updater.py har hverken tests eller telemetri, så enhver ændring
af en tærskel er hidtil sket i blinde. Dette script bygger facit ud af data vi
allerede har: EAN er en autoritativ produktidentitet, så EAN'en kan bruges til
at udlede både hvilke par der SKAL matche og hvilke der IKKE må.

Positive par (skal matche)
    Samme gyldige EAN i to forskellige butikker. Ren og ubestridelig.

Negative par (må ikke matche)
    Her duer "forskellig EAN" IKKE som facit: egne mærker på tværs af kæder
    (Rema-marmelade ↔ Coop-marmelade) har altid forskellig EAN og er alligevel
    tilsigtede sammenligninger - motoren behandler dem eksplicit som "samme
    brand-klasse" (se _find_generic_match, both_pl).

    I stedet bruges en katalog-baseret konstruktion, som er logisk vandtæt:

        Har butik X varen med EAN A, og fører butik Y OGSÅ EAN A som sin egen
        vare, så er enhver ANDEN vare i Y (med gyldig, anden EAN) beviseligt
        ikke den samme vare som X's - Y's rigtige svar ligger allerede i Y's
        eget katalog.

    Der udtrækkes kun de SVÆRE negativer (højest navnelighed), da tilfældige
    negativer afvises trivielt og ville pynte på tallene uden at måle noget.

Kilde
-----
data/app_cache_local.json (skrives af hver updater-kørsel). produkter-tabellen
er RLS-låst lokalt, så cachen er den tilgængelige sandhed.

Brug
----
    python scripts/build-match-goldset.py
    python scripts/build-match-goldset.py --hard-negatives 5 --out data/x.jsonl
"""

from __future__ import annotations

import argparse
import collections
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_support import (  # noqa: E402
    ean_looks_valid, fuzzy_score, normalize_name, parse_weight_to_grams,
)

DEFAULT_CACHE = 'data/app_cache_local.json'
DEFAULT_OUT = 'data/match_goldset.jsonl'

# Butikker hvis "EAN" er et butiks-internt ID og ikke en stregkode. De ryger
# alligevel på ean_looks_valid, men holdes ude eksplicit, så guldsættet ikke
# stilltiende afhænger af at den gate bliver ved med at virke.
_FAKE_EAN_STORES = frozenset({'sb', 'kvickly', 'brugsen'})

# Felter der udgør ét produkt i guldsættet. Gemmes i PRÆCIS den form
# updater.py's butiks-loader bygger, så måle-harnesset kan køre varen gennem
# annotate_match_signals og få nøjagtig de samme signaler som i produktion.
# Derfor de RÅ felter ('weight' som tekst, 'Kategori' som butikkens egen
# streng) - ikke de afledte. Gemmer vi det afledte, måler vi vores egen
# udledning i stedet for motorens.
#
# 'store' og 'ean' er metadata: 'ean' er facit og må ALDRIG gives til den
# funktion der evalueres.
_PRODUCT_FIELDS = (
    'store', 'name', 'brand', 'weight', 'Kategori', 'price', 'kg_price',
    'ean', '_hash_int',
)


def _num(value):
    """Tal eller None - cachen blander tal, tal-som-tekst og tomme strenge."""
    if value is None or value == '':
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _from_store_match(store_key: str, m: dict) -> dict | None:
    """Butiksvare fra en store_matches-post (den rige form)."""
    name = str(m.get('name') or '').strip()
    if not name:
        return None
    ean = str(m.get('ean') or '').strip()
    return {
        'store': store_key,
        'name': name,
        'brand': str(m.get('brand') or '').strip(),
        'weight': str(m.get('weight') or ''),
        'Kategori': str(m.get('Kategori') or '').strip(),
        'price': _num(m.get('price')),
        'kg_price': _num(m.get('kg_price')),
        'ean': ean if ean_looks_valid(ean) else '',
        '_hash_int': m.get('_hash_int'),
    }


def _from_solo_card(p: dict, label_to_key: dict) -> dict | None:
    """Butiksvare fra et solokort.

    Solokort har ingen store_matches - varen ER kortet. De mangler pHash og
    normaliseret navn (build_store_display_products bærer dem ikke videre), men
    de er rigtige varer i butikkens katalog og hører derfor med, når negativer
    konstrueres ud fra "hvad fører butikken ellers".
    """
    store_key = label_to_key.get(str(p.get('/product/store') or ''))
    if not store_key:
        return None
    name = str(p.get('/product/title') or '').strip()
    if not name:
        return None
    ean = str(p.get('/product/ean') or '').strip()
    return {
        'store': store_key,
        'name': name,
        'brand': str(p.get('/product/brand') or '').strip(),
        # Solokortets vægtstreng ligger i unit_pricing_measure - samme rå form
        # som butiksvarens 'weight', så parsingen bliver identisk.
        'weight': str(p.get('/product/unit_pricing_measure') or ''),
        # Kortets product_type er ALLEREDE kørt gennem unify_category, mens
        # 'Kategori' skal være butikkens rå streng. unify_category er
        # idempotent på sine egne outputs (de findes i kategori-mappet), så
        # den kan bruges her uden at flytte varen til en anden klasse.
        'Kategori': str(p.get('/product/product_type') or '').strip(),
        'price': _num(p.get('/product/price')),
        'kg_price': _num(p.get('/product/price_per_kg')),
        'ean': ean if ean_looks_valid(ean) else '',
        # Solokort bærer ikke pHash videre (build_store_display_products
        # dropper den), så billedsignalet mangler for denne halvdel.
        '_hash_int': None,
    }


def load_products(cache_path: str) -> list[dict]:
    """Alle butiksvarer i cachen, afduplikeret."""
    with open(cache_path, encoding='utf-8') as fh:
        cache = json.load(fh)
    cards = cache.get('products') or []

    # Butiks-label -> key. store_matches er nøglet på key, solokort bærer label.
    label_to_key: dict = {}
    for card in cards:
        for key, m in (card.get('/product/store_matches') or {}).items():
            if isinstance(m, dict) and m.get('name'):
                label_to_key.setdefault(str(card.get('/product/store') or ''), key)
    # Robust fallback: udled label->key fra updater'ens egen konfiguration.
    try:
        from updater import _STORE_CONFIGS
        label_to_key = {v['label']: k for k, v in _STORE_CONFIGS.items()}
    except Exception:
        pass

    products: list[dict] = []
    seen: set = set()
    for card in cards:
        matches = card.get('/product/store_matches') or {}
        if matches:
            for key, m in matches.items():
                if not isinstance(m, dict):
                    continue
                p = _from_store_match(key, m)
                if p is None:
                    continue
                fingerprint = (p['store'], p['name'], p['price'], p['ean'])
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                products.append(p)
        else:
            p = _from_solo_card(card, label_to_key)
            if p is None:
                continue
            fingerprint = (p['store'], p['name'], p['price'], p['ean'])
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            products.append(p)
    return products


def build_positives(products: list[dict]) -> list[tuple[dict, dict]]:
    """Par med samme gyldige EAN i to forskellige butikker."""
    by_ean: dict = collections.defaultdict(list)
    for p in products:
        if p['ean'] and p['store'] not in _FAKE_EAN_STORES:
            by_ean[p['ean']].append(p)

    pairs: list[tuple[dict, dict]] = []
    for ean, group in by_ean.items():
        # Én vare pr. butik pr. EAN. To varer med samme EAN i SAMME butik er en
        # datafejl (updater kalder det EAN-kollision og udelukker den helt), så
        # den skal heller ikke danne facit her.
        by_store: dict = collections.defaultdict(list)
        for p in group:
            by_store[p['store']].append(p)
        singles = [lst[0] for lst in by_store.values() if len(lst) == 1]
        for a, b in itertools.combinations(singles, 2):
            pairs.append((a, b))
    return pairs


def build_hard_negatives(products: list[dict], per_product: int) -> list[tuple[dict, dict]]:
    """Katalog-baserede negativer: Y fører selv EAN A, så Y's øvrige varer er ikke A.

    Kun de sværeste (højeste navnelighed) tages med - en tilfældig negativ er
    triviel at afvise og ville måle ingenting.
    """
    usable = [p for p in products if p['store'] not in _FAKE_EAN_STORES]

    # Normaliserede navne bruges KUN til blokering og til at rangere hvor svær
    # en negativ er - de gemmes ikke i guldsættet, hvor varen skal ligge i sin
    # rå form (se _PRODUCT_FIELDS).
    norms = {id(p): normalize_name(p['name']) for p in usable}

    # butik -> EAN -> vare, og butik -> token -> vare-indeks (blokering)
    store_eans: dict = collections.defaultdict(dict)
    store_tokens: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for p in usable:
        if p['ean']:
            store_eans[p['store']][p['ean']] = p
        for token in set(norms[id(p)].split()):
            if len(token) >= 3:
                store_tokens[p['store']][token].append(p)

    pairs: list[tuple[dict, dict]] = []
    seen: set = set()
    for p in usable:
        if not p['ean']:
            continue
        p_norm = norms[id(p)]
        p_tokens = {t for t in p_norm.split() if len(t) >= 3}
        if not p_tokens:
            continue
        for other_store, eans in store_eans.items():
            if other_store == p['store']:
                continue
            if p['ean'] not in eans:
                continue  # Y fører ikke EAN A - så kan vi intet konkludere

            # Kandidater i Y der deler mindst ét ord med varen, minus tvillingen.
            candidates: dict = {}
            for token in p_tokens:
                for q in store_tokens[other_store].get(token, ()):
                    if not q['ean']:
                        continue  # uden EAN kan vi ikke vide at den er forskellig
                    if q['ean'] == p['ean']:
                        continue  # det ER tvillingen (eller en dublet af den)
                    candidates[id(q)] = q

            scored = sorted(
                ((fuzzy_score(p_norm, norms[id(q)]), q) for q in candidates.values()),
                key=lambda x: -x[0],
            )
            for score, q in scored[:per_product]:
                if score <= 0.0:
                    break
                key = (p['store'], p['name'], p['ean'], q['store'], q['name'], q['ean'])
                mirror = (q['store'], q['name'], q['ean'], p['store'], p['name'], p['ean'])
                if key in seen or mirror in seen:
                    continue
                seen.add(key)
                pairs.append((p, q))
    return pairs


def _slim(p: dict) -> dict:
    return {k: p[k] for k in _PRODUCT_FIELDS}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cache', default=DEFAULT_CACHE, help=f'produktcache (default: {DEFAULT_CACHE})')
    ap.add_argument('--out', default=DEFAULT_OUT, help=f'output JSONL (default: {DEFAULT_OUT})')
    ap.add_argument('--hard-negatives', type=int, default=3,
                    help='antal svære negativer pr. vare pr. modbutik (default: 3)')
    args = ap.parse_args()

    if not os.path.exists(args.cache):
        print(f"FEJL: {args.cache} findes ikke. Kør updater.py først.", file=sys.stderr)
        return 1

    print(f"Læser {args.cache} ...")
    products = load_products(args.cache)
    with_ean = sum(1 for p in products if p['ean'])
    print(f"  {len(products)} butiksvarer, {with_ean} med gyldig EAN")

    positives = build_positives(products)
    print(f"  {len(positives)} positive par (samme EAN, forskellig butik)")

    negatives = build_hard_negatives(products, args.hard_negatives)
    print(f"  {len(negatives)} svære negative par (katalog-baseret)")

    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        for label, pairs, reason in ((1, positives, 'same-ean'), (0, negatives, 'cross-catalog')):
            for a, b in pairs:
                fh.write(json.dumps({'label': label, 'reason': reason,
                                     'a': _slim(a), 'b': _slim(b)},
                                    ensure_ascii=False) + '\n')

    total = len(positives) + len(negatives)
    print(f"\nSkrev {total} par til {args.out}")

    # Dækningsrapport: hvilke signaler er overhovedet til stede i facit? Uden
    # den er det umuligt at vide, om en gate blev målt eller bare aldrig fyrede.
    print("\nSignaldækning (andel par hvor BEGGE sider har feltet):")
    for label, pairs in ((1, positives), (0, negatives)):
        if not pairs:
            continue
        n = len(pairs)
        cover = {
            'vægt': sum(1 for a, b in pairs
                        if parse_weight_to_grams(a['weight']) and parse_weight_to_grams(b['weight'])),
            'kg-pris': sum(1 for a, b in pairs if a['kg_price'] and b['kg_price']),
            'pHash': sum(1 for a, b in pairs
                         if a['_hash_int'] is not None and b['_hash_int'] is not None),
            'brand': sum(1 for a, b in pairs if a['brand'] and b['brand']),
        }
        name = 'positive' if label == 1 else 'negative'
        print(f"  {name:9} (n={n:6}): " +
              '  '.join(f'{k}={100 * v // n:3}%' for k, v in cover.items()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
