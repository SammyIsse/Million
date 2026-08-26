#!/usr/bin/env python3
"""Regressionstest for Lidl-katalog-scraperens Nuxt-payload-parser.

Kør: python3 scripts/test-lidl-katalog.py

BAGGRUNDEN: scraper/lidl_katalog.py læser produktdata ud af en Nuxt SSR-JSON-
payload (devalue-lignende pool-indeks-serialisering). Nuxt omlagde skemaet
2026-08-18 - feltet 'items' blev til 'products', og selve item-listen blev
pakket ind i et Ref/Reactive-indpakningsskema i stedet for en almindelig
pool-indeks-reference. Scraperen fejlede DERFOR HVER ENESTE NAT i 8 dage i
træk (fandt 0 varer hver gang), fanget af shrink-værnet men aldrig undersøgt
og rettet - indtil matchmotor-analysen 26-08-2026 fandt det ved et tilfælde.

`scraper/testdata/lidl_search_sample.html` er et rigtigt JSON-payload hentet
fra lidl.dk 25-08-2026 (minimeret til kun <script>-blokken), så denne test
opdager en NY skema-ombygning uden at afhænge af live netværksadgang.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scraper'))

from lidl_katalog import _extract_products, _parse_nuxt_pool  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'scraper', 'testdata', 'lidl_search_sample.html')

fails: list[str] = []


def check(label: str, ok: bool) -> None:
    print(("  OK    " if ok else "  FEJL  ") + label)
    if not ok:
        fails.append(label)


def main() -> int:
    print("=" * 62)
    print("LIDL-KATALOG - REGRESSIONSTEST AF NUXT-PAYLOAD-PARSER")
    print("=" * 62)

    if not os.path.exists(FIXTURE):
        print(f"FEJL: fixture mangler: {FIXTURE}")
        return 1

    html = open(FIXTURE, encoding='utf-8').read()
    pool = _parse_nuxt_pool(html)
    check("Nuxt-payload parses uden fejl", isinstance(pool, list) and len(pool) > 0)

    num_found, products = _extract_products(pool)

    # Det reelle facit fra 25-08-2026: en frisk hentning gav numFound=616 og
    # 39 fødevarer på denne specifikke side. Et lille udsving accepteres
    # (lidl.dk's sortiment ændrer sig fra dag til dag), men falder tallet til
    # nul, er parseren igen brudt - præcis den fejl der stod på i 8 nætter.
    check(f"numFound er et realistisk tal ({num_found}, ikke 0)", num_found > 100)
    check(f"produkter udtrukket ({len(products)}, ikke 0)", len(products) > 5)

    if products:
        p = products[0]
        for field in ('navn', 'pris'):
            check(f"produkt har feltet '{field}'", bool(p.get(field)) or p.get(field) == 0)
        check("mindst ét produkt har et udfyldt producent-felt (brand er ikke helt væk)",
              any(pr.get('producent') for pr in products))

    print()
    if fails:
        print(f"{len(fails)} KONTROL(LER) FEJLEDE - Lidls Nuxt-skema er sandsynligvis ændret igen.")
        print("Se scraper/lidl_katalog.py's _resolve_items_list/_find_search-kommentarer")
        print("for hvordan skemaet saa ud sidst, og hent en frisk payload for at diffe.")
        return 1
    print("ALLE TESTS BESTAAET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
