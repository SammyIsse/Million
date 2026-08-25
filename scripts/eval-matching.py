#!/usr/bin/env python3
"""Mål matchmotorens præcision mod guldsættet - og sammenlign mod en baseline.

Kører updater.py's EGEN gate-kæde (cross_store_pair_verdict) mod hvert par i
data/match_goldset.jsonl. Der findes bevidst ingen kopi af logikken her: en
kopi ville drive fra produktionen og gøre målingen misvisende i stedet for
tydeligt forkert.

Rapporten har tre dele:

1. **Præcision/recall** på gate-kæden. Et positivt par er to varer med samme
   EAN i to butikker (skal accepteres); et negativt par er to varer, hvor
   modbutikken beviseligt fører den rigtige vare selv (må afvises).

2. **Hvilken gate gør arbejdet.** Histogram over afvisningsårsager, både for de
   negative (godt arbejde) og de positive (tabte matches). Det er den tabel,
   der viser om en gate rent faktisk bidrager, eller bare aldrig fyrer.

3. **Hvad fuzzy-sporet aldrig overvejer.** Fase 2/2b lader kun stage 3 (varer
   UDEN EAN) initiere fuzzy, så to varer der begge har EAN mødes aldrig dér.

VIGTIG afgrænsning - hvad tallene IKKE er
-----------------------------------------
Guldsættet er bygget på EAN, og derfor har begge sider af hvert par en EAN.
Det betyder to ting, som skal holdes adskilt:

  * De POSITIVE par håndteres i produktionen af stage-1 EAN-gruppering, ikke af
    fuzzy. At de ligger uden for fuzzy-sporets rækkevidde er altså ikke et tabt
    match - stage 1 fanger dem. Målingen er her et PROXY: hvor godt skelner
    gate-kæden varer, vi uafhængigt VED er de samme?

  * De NEGATIVE par ville i dag blive stage 2 (EAN, men uden match) og ende som
    solokort, fordi ingen af dem må initiere fuzzy. De er altså beskyttet af
    stage-reglen, ikke af gate-kæden.

Precision-tallet er derfor IKKE produktionens fejlrate. Det er gate-kædens
skelneevne i sig selv - præcis det tal der afgør, hvor sikkert det er at give
stage 2 lov til at initiere, eller at flytte EAN-grupperingen tidligere.

Anden afgrænsning: guldsættet UNDERVURDERER foto-dækningen. Solokort bærer ikke
pHash videre i app_cache (build_store_display_products dropper feltet), så en
del par står uden billede her, selvom produktionen læser billede_hash direkte
fra produkter-tabellen for ALLE varer (93-100 % dækning pr. butik). Rapporten
opdeler derfor på foto-dækning: segmentet "med foto på begge sider" er det, der
ligner produktionen mest.

Brug
----
    python scripts/eval-matching.py --baseline     # gem som referencepunkt
    python scripts/eval-matching.py                # mål og diff mod baseline
    python scripts/eval-matching.py --examples 20  # vis flere eksempler
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_support import parse_weight_to_grams, weights_compatible  # noqa: E402
from updater import (  # noqa: E402
    annotate_match_signals, build_token_idf, cross_store_pair_verdict,
    cross_store_tokens,
)

DEFAULT_GOLDSET = 'data/match_goldset.jsonl'
DEFAULT_BASELINE = 'data/match_eval_baseline.json'


def prepare(raw: dict, backfill: dict | None = None) -> dict | None:
    """Guldsæt-vare -> updater'ens interne produktform, med alle signaler sat."""
    product = dict(raw)
    if annotate_match_signals(product) is None:
        return None  # ikke-mad/tobak: udelukkes af loaderen, hører ikke til her
    # Samme EAN-backfill som load_all_comparison_data laver i produktionen -
    # uden den ville harnesset måle en vægtløs verden, som ikke findes mere.
    if backfill:
        extra = backfill.get(product.get('ean') or '')
        if extra:
            if not product.get('_weight_g') and extra[0]:
                product['_weight_g'] = extra[0]
            if product.get('_stk_count') is None and extra[1]:
                product['_stk_count'] = extra[1]
    product['_cross_match_tokens'] = cross_store_tokens(product['_norm_name'])
    return product


def load_goldset(goldset_path: str) -> list:
    """Læs guldsættet én gang. Filen er ~62 MB, så gentagne gennemløb koster."""
    with open(goldset_path, encoding='utf-8') as fh:
        return [json.loads(line) for line in fh]


def build_backfill_map(rows: list) -> dict:
    """EAN -> (vægt, stk) samlet på tværs af alle butikker i guldsættet.

    Spejler backfill_attributes_by_ean i updater.py: EAN'er hvor to butikker
    er uenige om vægten udelades, da et gæt dér ville være værre end intet.
    """
    weights: dict = {}
    stks: dict = {}
    conflicting: set = set()
    for row in rows:
        for side in ('a', 'b'):
            raw = row[side]
            ean = raw.get('ean') or ''
            if not ean:
                continue
            w = parse_weight_to_grams(raw.get('weight'))
            if w:
                known = weights.get(ean)
                if known is None:
                    weights[ean] = w
                elif not weights_compatible(known, w):
                    conflicting.add(ean)
    return {e: (weights.get(e), stks.get(e))
            for e in set(weights) | set(stks) if e not in conflicting}


def verdict_both_ways(a: dict, b: dict) -> tuple[bool, float, str, bool]:
    """Kør gate-kæden begge veje.

    Returnerer (accepteret, navnescore, årsag, asymmetrisk). Fase 2 vælger base
    efter butiksrækkefølge, så et par der kun accepteres den ene vej, giver et
    resultat der afhænger af DB_STORE_KEYS - det tælles med som et selvstændigt
    fund.
    """
    ok_ab, score_ab, why_ab = cross_store_pair_verdict(
        a, b, a['_norm_name'], a['_cross_match_tokens'])
    ok_ba, score_ba, why_ba = cross_store_pair_verdict(
        b, a, b['_norm_name'], b['_cross_match_tokens'])
    asymmetric = ok_ab != ok_ba
    # Pipelinen accepterer parret, hvis mindst én retning gør det (begge
    # retninger prøves, fordi fase 2 itererer over alle butikker som base).
    if ok_ab or ok_ba:
        return True, max(score_ab, score_ba), '', asymmetric
    # Rapportér den årsag der nåede længst i kæden (højeste score betyder at
    # flere gates blev passeret, før noget afviste).
    why = why_ab if score_ab >= score_ba else why_ba
    return False, max(score_ab, score_ba), why, asymmetric


def evaluate(goldset_path: str, examples: int) -> dict:
    stats = collections.Counter()
    reasons = {'positive': collections.Counter(), 'negative': collections.Counter()}
    unreachable = collections.Counter()
    photo_seg = {'foto': collections.Counter(), 'uden foto': collections.Counter()}
    fp_examples: list = []
    fn_examples: list = []

    goldset = load_goldset(goldset_path)
    backfill = build_backfill_map(goldset)

    # Token-IDF bygges i produktionen af load_all_comparison_data over hele
    # katalogets varenavne. Uden den her ville gaten "distinktivt ord" være
    # inaktiv, og målingen ville vise en anden motor end den der kører.
    catalogue: dict = collections.defaultdict(list)
    seen_products: set = set()
    for row in goldset:
        for side in ('a', 'b'):
            raw = row[side]
            key = (raw['store'], raw['name'])
            if key in seen_products:
                continue
            seen_products.add(key)
            p = prepare(raw, backfill)
            if p is not None:
                catalogue[raw['store']].append(p)
    build_token_idf({k: (v, {}, [], {}) for k, v in catalogue.items()})

    for row in goldset:
        a, b = prepare(row['a'], backfill), prepare(row['b'], backfill)
        if a is None or b is None:
            stats['sprunget over (ikke-mad)'] += 1
            continue

        label = row['label']
        kind = 'positive' if label == 1 else 'negative'
        accepted, score, why, asymmetric = verdict_both_ways(a, b)

        stats['par'] += 1
        if asymmetric:
            stats['retningsafhængige'] += 1

        # Opdeling på foto-dækning: guldsættet undervurderer den (se
        # docstring), så segmentet med foto på begge sider ligner
        # produktionen mest.
        seg = 'foto' if (row['a']['_hash_int'] is not None
                         and row['b']['_hash_int'] is not None) else 'uden foto'
        if label == 1:
            photo_seg[seg]['TP' if accepted else 'FN'] += 1
        else:
            photo_seg[seg]['FP' if accepted else 'TN'] += 1

        # Kan pipelinen overhovedet nå parret? Kun stage 3 (ingen EAN) må
        # initiere, så to varer der begge har EAN, mødes aldrig i fase 2/2b.
        reachable = not (row['a']['ean'] and row['b']['ean'])
        if not reachable:
            unreachable[kind] += 1

        if label == 1:
            if accepted:
                stats['TP'] += 1
            else:
                stats['FN'] += 1
                reasons['positive'][why] += 1
                if len(fn_examples) < examples:
                    fn_examples.append((why, round(score, 2), row['a'], row['b']))
        else:
            if accepted:
                stats['FP'] += 1
                if len(fp_examples) < examples:
                    fp_examples.append((round(score, 2), row['a'], row['b']))
            else:
                stats['TN'] += 1
                reasons['negative'][why] += 1

    tp, fp, fn, tn = stats['TP'], stats['FP'], stats['FN'], stats['TN']
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        'counts': dict(stats),
        'precision': precision,
        'recall': recall,
        'f1': f1,
        'reasons_positive': dict(reasons['positive']),
        'reasons_negative': dict(reasons['negative']),
        'unreachable': dict(unreachable),
        'photo_segments': {k: dict(v) for k, v in photo_seg.items()},
        'fp_examples': fp_examples,
        'fn_examples': fn_examples,
    }


def _pct(x: float) -> str:
    return f'{100 * x:.2f} %'


def _delta(now: float, before: float | None) -> str:
    if before is None:
        return ''
    diff = 100 * (now - before)
    if abs(diff) < 0.005:
        return '   (uændret)'
    return f'   ({diff:+.2f} pp)'


def report(result: dict, baseline: dict | None, examples: int) -> None:
    c = result['counts']
    base_c = (baseline or {}).get('counts', {})

    print('=' * 66)
    print('MATCHMOTOR - MÅLING MOD GULDSÆT')
    print('=' * 66)
    if c.get('sprunget over (ikke-mad)'):
        print(f"Sprunget over (ikke-mad/tobak): {c['sprunget over (ikke-mad)']}")
    print(f"Par vurderet: {c.get('par', 0)}")
    print()
    was_fp = f"   (baseline {base_c['FP']})" if 'FP' in base_c else ''
    was_fn = f"   (baseline {base_c['FN']})" if 'FN' in base_c else ''
    print(f"  Rigtigt accepteret    (TP): {c.get('TP', 0):7}")
    print(f"  Rigtigt afvist        (TN): {c.get('TN', 0):7}")
    print(f"  FEJLAGTIGT accepteret (FP): {c.get('FP', 0):7}{was_fp}")
    print(f"  Tabt match            (FN): {c.get('FN', 0):7}{was_fn}")
    print()
    print(f"  Precision : {_pct(result['precision'])}{_delta(result['precision'], (baseline or {}).get('precision'))}")
    print(f"  Recall    : {_pct(result['recall'])}{_delta(result['recall'], (baseline or {}).get('recall'))}")
    print(f"  F1        : {_pct(result['f1'])}{_delta(result['f1'], (baseline or {}).get('f1'))}")

    if c.get('retningsafhængige'):
        print(f"\n  Retningsafhængige par: {c['retningsafhængige']}"
              "  (accepteres kun med den ene vare som base -> resultatet"
              " afhænger af butiksrækkefølgen)")

    segments = result.get('photo_segments') or {}
    if segments:
        print('\n' + '-' * 66)
        print('OPDELT PÅ FOTO-DÆKNING')
        print('-' * 66)
        base_seg = (baseline or {}).get('photo_segments') or {}
        for seg in ('foto', 'uden foto'):
            s = segments.get(seg) or {}
            tp_s, fp_s = s.get('TP', 0), s.get('FP', 0)
            if tp_s + fp_s == 0:
                continue
            prec = tp_s / (tp_s + fp_s)
            was = ''
            b = base_seg.get(seg) or {}
            if b.get('TP', 0) + b.get('FP', 0):
                was = f"   (baseline {100 * b['TP'] / (b['TP'] + b['FP']):.2f} %)"
            label = 'foto på begge sider' if seg == 'foto' else 'mindst én uden foto'
            print(f"  {label:22} TP={tp_s:6} FP={fp_s:6}  precision {100 * prec:6.2f} %{was}")
        print('  => Guldsættet undervurderer foto-dækningen (solokort mister pHash i'
              '\n     cachen). Øverste linje ligner produktionen mest.')

    unreachable = result['unreachable']
    if unreachable:
        print('\n' + '-' * 66)
        print('UDEN FOR FUZZY-SPORET (begge sider har EAN -> ingen kan initiere)')
        print('-' * 66)
        notes = {
            'positive': 'håndteres af stage-1 EAN-gruppering - ikke tabt',
            'negative': 'ender som solokort - beskyttet af stage-reglen, ikke af gates',
        }
        for kind in ('positive', 'negative'):
            n = unreachable.get(kind, 0)
            total = (c.get('TP', 0) + c.get('FN', 0)) if kind == 'positive' \
                else (c.get('TN', 0) + c.get('FP', 0))
            if not total:
                continue
            print(f"  {kind:9}: {n:6} af {total:6} ({100 * n / total:5.1f} %)  - {notes[kind]}")
        print('  => Tallene ovenfor måler gate-kædens skelneevne, ikke produktionens'
              '\n     fejlrate. Se scriptets docstring.')

    for kind, title in (('negative', 'HVILKEN GATE AFVISER DE NEGATIVE (godt arbejde)'),
                        ('positive', 'HVILKEN GATE TABER DE POSITIVE (recall-tab)')):
        data = result[f'reasons_{kind}']
        if not data:
            continue
        print('\n' + '-' * 66)
        print(title)
        print('-' * 66)
        total = sum(data.values())
        for why, n in sorted(data.items(), key=lambda x: -x[1]):
            print(f"  {why or '(accepteret)':18} {n:7}  {100 * n / total:5.1f} %")

    if result['fp_examples']:
        print('\n' + '-' * 66)
        print(f'FEJLAGTIGT ACCEPTEREDE PAR (op til {examples})')
        print('-' * 66)
        for score, a, b in result['fp_examples']:
            print(f"  score {score:.2f}  {a['store']}:{a['name']!r} ({a['ean']})")
            print(f"              {b['store']}:{b['name']!r} ({b['ean']})")

    if result['fn_examples']:
        print('\n' + '-' * 66)
        print(f'TABTE MATCHES (op til {examples})')
        print('-' * 66)
        for why, score, a, b in result['fn_examples']:
            print(f"  {why:16} score {score:.2f}  {a['store']}:{a['name']!r}")
            print(f"  {'':16}              {b['store']}:{b['name']!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--goldset', default=DEFAULT_GOLDSET)
    ap.add_argument('--baseline-file', default=DEFAULT_BASELINE)
    ap.add_argument('--baseline', action='store_true',
                    help='gem denne kørsel som baseline i stedet for at sammenligne')
    ap.add_argument('--examples', type=int, default=10)
    args = ap.parse_args()

    if not os.path.exists(args.goldset):
        print(f"FEJL: {args.goldset} findes ikke. Kør scripts/build-match-goldset.py først.",
              file=sys.stderr)
        return 1

    result = evaluate(args.goldset, args.examples)

    baseline = None
    if not args.baseline and os.path.exists(args.baseline_file):
        with open(args.baseline_file, encoding='utf-8') as fh:
            baseline = json.load(fh)

    report(result, baseline, args.examples)

    if args.baseline:
        # Eksempler gemmes ikke - de er store og siger intet om en regression.
        slim = {k: v for k, v in result.items() if not k.endswith('_examples')}
        with open(args.baseline_file, 'w', encoding='utf-8') as fh:
            json.dump(slim, fh, ensure_ascii=False, indent=1)
        print(f"\nGemt som baseline: {args.baseline_file}")
    elif baseline is None:
        print(f"\n(Ingen baseline fundet. Kør med --baseline for at gemme denne som referencepunkt.)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
