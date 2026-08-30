#!/usr/bin/env python3
"""Måleharness for matchmotorens gate-kæde - SEGMENTERET.

Kør: python3 scripts/eval-matching.py [--baseline] [--cache STI]

BAGGRUNDEN: updater.py refererede flere steder til "scripts/eval-matching.py"
og til kalibreringstal målt med den, men filen fandtes ikke i repoet. Tallene i
kommentarerne kunne derfor hverken efterprøves eller genkøres, og da en revision
25-08-2026 alligevel genskabte målingen, viste det sig at det gamle, ikke-
segmenterede tal (precision 94,4 % / recall 62,0 % i data/match_eval_baseline
.json) skjulte det væsentlige.

HVORFOR SEGMENTERING ER HELE POINTEN
------------------------------------
Butikkerne deler datafeed i familier:

    salling = bilka + netto + foetex
    dagrofa = meny + spar + mk
    coop    = sb + brugsen + kvickly + discount365

To butikker i SAMME familie deler varenavn og billed-URL. Et par derfra er
trivielt at matche - hashene er identiske fordi filen er den samme - og de er
i forvejen grupperet af stage 1 på EAN, så fuzzy-matchingen bliver aldrig
spurgt. Målt på produktionscachen: 12.464 af 15.168 samme-familie-par har
bogstavelig talt identisk billed-URL.

Et samlet tal over begge populationer måler derfor mest af alt, hvor mange
par der kommer fra samme feed. Den rigtige måling er kryds-familie-segmentet,
og de to tal ligger meget langt fra hinanden:

    samme feed:   recall 96,0 %   precision 99,6 %
    kryds-feed:   recall  1,2 %   precision 56,2 %

SANDHEDSGRUNDLAG
----------------
Guldsættet bygges af data/app_cache_local.json (motorens eget output). Hvert
par af butiksvarer på samme kort, hvor BEGGE sider bærer en gyldig GTIN:

    samme EAN      -> positiv (autoritativt samme vare)
    forskellig EAN -> negativ, og en HÅRD en af slagsen: motoren har allerede
                      accepteret parret, så det er ikke en tilfældig negativ

Forbehold der skal med hver gang tallene citeres: grundlaget er motorens
output, ikke de rå butiksdata. Par som motoren aldrig fandt, indgår ikke i
nævneren, så recall er et MINIMUM. Retning og størrelsesorden er robuste; de
præcise procenter er det ikke.

MARGINAL EFFEKT, IKKE FØRSTE-AFVISNING
--------------------------------------
Gate-kæden kortslutter, så afvisnings-histogrammet rangerer efter rækkefølge i
kæden - ikke efter betydning. I kryds-feed-segmentet ser "ingen fælles ord" ud
til at koste 30,9 % af de sande par, men slår man blokeringen fra efter at have
rettet billed-gaten, stiger recall kun 0,2 point: parrene faldt alligevel.
--marginal måler det rigtige ved faktisk at slå hver gate fra og genmåle.
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import updater as U  # noqa: E402
from updater import (  # noqa: E402
    annotate_match_signals, build_token_idf, cross_store_pair_verdict,
    cross_store_tokens,
)

# Butikker der deler datafeed - og dermed varenavn og billedfil.
FEED_FAMILY = {
    'bilka': 'salling', 'netto': 'salling', 'foetex': 'salling',
    'meny': 'dagrofa', 'spar': 'dagrofa', 'mk': 'dagrofa',
    'sb': 'coop', 'brugsen': 'coop', 'kvickly': 'coop', 'discount365': 'coop',
    'lidl': 'lidl', 'loevbjerg': 'loevbjerg', 'abclavpris': 'abclavpris',
    'rema': 'rema',
}

DEFAULT_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'app_cache_local.json')
BASELINE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'match_eval_baseline.json')


def _prepare(match: dict) -> dict | None:
    """Genskab et butiksvare-dict med alle matchsignaler fra en cache-post.

    De transiente felter (_type, _flavors, ...) strippes før cachen gemmes, så
    de skal beregnes igen - gennem annotate_match_signals, præcis som butiks-
    loaderen gør, så målingen ikke kan afvige fra produktionen.
    """
    p = dict(match)
    p.setdefault('Kategori', match.get('Kategori') or 'Kolonial')
    if annotate_match_signals(p) is None:
        return None  # ikke-mad/tobak - hverken vist eller matchet
    p['_hash_int'] = match.get('_hash_int')
    p['_cross_match_tokens'] = cross_store_tokens(p['_norm_name'])
    return p


def build_gold_set(cache_path: str) -> tuple[dict, list]:
    """-> ({segment: (positive, negative)}, alle_varer_til_IDF)."""
    with open(cache_path, encoding='utf-8') as fh:
        products = json.load(fh)['products']

    segments: dict = {'samme-feed': ([], []), 'kryds-feed': ([], [])}
    corpus: list = []

    for card in products:
        members = [(k, m) for k, m in (card.get('/product/store_matches') or {}).items()
                   if isinstance(m, dict)]
        prepped = {}
        for key, m in members:
            q = _prepare(m)
            if q is not None:
                prepped[key] = q
                corpus.append(q)

        for (k1, m1), (k2, m2) in itertools.combinations(members, 2):
            if k1 not in prepped or k2 not in prepped:
                continue
            e1 = str(m1.get('ean') or '')
            e2 = str(m2.get('ean') or '')
            if len(e1) < 8 or len(e2) < 8:
                continue
            seg = ('samme-feed' if FEED_FAMILY.get(k1, k1) == FEED_FAMILY.get(k2, k2)
                   else 'kryds-feed')
            bucket = segments[seg][0 if e1 == e2 else 1]
            bucket.append((prepped[k1], prepped[k2]))

    return segments, corpus


def verdict(a: dict, b: dict) -> tuple[bool, str]:
    """Accepterer kæden parret i mindst én retning?

    Begge retninger prøves, fordi fase 2 itererer over alle butikker som base -
    hvilken vare der er "base", afhænger af butiksrækkefølgen, ikke af parret.
    """
    ok_ab, _, r_ab = cross_store_pair_verdict(a, b, a['_norm_name'], a['_cross_match_tokens'])
    if ok_ab:
        return True, ''
    ok_ba, _, r_ba = cross_store_pair_verdict(b, a, b['_norm_name'], b['_cross_match_tokens'])
    if ok_ba:
        return True, ''
    return False, (r_ab or r_ba)


def score(positives: list, negatives: list) -> dict:
    tp = fn = 0
    reasons: dict = collections.Counter()
    for a, b in positives:
        ok, why = verdict(a, b)
        if ok:
            tp += 1
        else:
            fn += 1
            reasons[why] += 1
    fp = sum(1 for a, b in negatives if verdict(a, b)[0])
    tn = len(negatives) - fp
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {'TP': tp, 'FP': fp, 'FN': fn, 'TN': tn, 'precision': precision,
            'recall': recall, 'f1': f1, 'tabt_paa': dict(reasons.most_common())}


def report(segments: dict) -> dict:
    out = {}
    print('=' * 78)
    print('MATCHMOTOR - SEGMENTERET MAALING')
    print('=' * 78)
    for seg in ('samme-feed', 'kryds-feed'):
        pos, neg = segments[seg]
        res = score(pos, neg)
        out[seg] = res
        print("\n%s  (%d positive / %d haarde negative)" % (seg.upper(), len(pos), len(neg)))
        print("   recall    %6.1f %%   (%d af %d sande par fundet)"
              % (100 * res['recall'], res['TP'], len(pos)))
        print("   precision %6.1f %%   (%d falske accepter)" % (100 * res['precision'], res['FP']))
        print("   F1        %6.3f" % res['f1'])
        if res['tabt_paa']:
            print("   tabte sande par, foerste afvisningsaarsag:")
            for why, n in list(res['tabt_paa'].items())[:8]:
                print("      %-20s %5d  (%4.1f %%)" % (why or '(ukendt)', n, 100 * n / max(len(pos), 1)))
    return out


def marginal(segments: dict) -> None:
    """Slå hver gate fra én ad gangen og mål den FAKTISKE effekt.

    Uden dette rangeres gates efter hvor tidligt de sidder i kæden, ikke efter
    hvad de koster. Se modulets docstring.
    """
    pos, neg = segments['kryds-feed']
    corpus = {id(p): p for pair in pos + neg for p in pair}.values()

    saved = {
        'photo_reject': U._PHOTO_REJECT_MAX_DIST,
        'floor': U._CROSS_STORE_NAME_FLOOR,
        'prefilter': U._cross_store_length_prefilter,
        'types': U.types_compatible,
        'weightless': U._WEIGHTLESS_NAME_FLOOR,
    }
    saved_tokens = {id(p): set(p['_cross_match_tokens']) for p in corpus}

    def restore():
        U._PHOTO_REJECT_MAX_DIST = saved['photo_reject']
        U._CROSS_STORE_NAME_FLOOR = saved['floor']
        U._cross_store_length_prefilter = saved['prefilter']
        U.types_compatible = saved['types']
        U._WEIGHTLESS_NAME_FLOOR = saved['weightless']
        for p in corpus:
            p['_cross_match_tokens'] = set(saved_tokens[id(p)])

    def measure(label):
        res = score(pos, neg)
        print("   %-34s recall %5.1f %%   precision %5.1f %%   F1 %.3f"
              % (label, 100 * res['recall'], 100 * res['precision'], res['f1']))

    print("\n" + "=" * 78)
    print("MARGINAL EFFEKT - kryds-feed-segmentet, hver gate slaaet fra for sig")
    print("=" * 78)
    measure('uaendret')

    U._PHOTO_REJECT_MAX_DIST = 64
    measure('uden billed-afvisning')
    restore()

    U._WEIGHTLESS_NAME_FLOOR = 0.0
    measure('uden vaegtloest-gulv')
    restore()

    U._cross_store_length_prefilter = lambda a, b: False
    for p in corpus:
        p['_cross_match_tokens'] = p['_cross_match_tokens'] | {'§alle'}
    measure('uden blokering (laengde + token)')
    restore()

    U.types_compatible = lambda a, b: True
    measure('uden type-gate')
    restore()

    U._CROSS_STORE_NAME_FLOOR = 0.0
    measure('uden navnegulv')
    restore()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', default=DEFAULT_CACHE, help='sti til app_cache_local.json')
    ap.add_argument('--marginal', action='store_true',
                    help='maal hver gates faktiske bidrag ved at slaa den fra')
    ap.add_argument('--baseline', action='store_true',
                    help='skriv resultatet til data/match_eval_baseline.json')
    args = ap.parse_args()

    if not os.path.exists(args.cache):
        print("Cachen findes ikke: %s" % args.cache)
        print("Hent den med `python updater.py` eller peg paa en kopi med --cache.")
        return 1

    segments, corpus = build_gold_set(args.cache)
    # IDF skal bygges over hele katalogets navne, praecis som i produktionen -
    # ellers er "distinktivt ord"-gaten kunstigt streng (ukendte ord taeller
    # som maksimalt distinktive).
    build_token_idf({'alle': (corpus, {}, [], {})})

    results = report(segments)

    if args.marginal:
        marginal(segments)

    if args.baseline:
        payload = {
            'segmenter': {k: {kk: vv for kk, vv in v.items() if kk != 'tabt_paa'}
                          for k, v in results.items()},
            'tabt_paa': {k: v['tabt_paa'] for k, v in results.items()},
            'note': ('Guldsaettet er bygget af app_cache_local.json (motorens output). '
                     'Recall er et minimum - par motoren aldrig fandt, er ikke i naevneren. '
                     'Negative er HAARDE: par motoren allerede har accepteret.'),
        }
        with open(BASELINE_PATH, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print("\nSkrev baseline til %s" % BASELINE_PATH)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
