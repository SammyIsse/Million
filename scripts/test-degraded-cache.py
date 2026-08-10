#!/usr/bin/env python3
"""Håndhæver at degraderede svar ALDRIG havner i den delte cache.

Baggrunden: D1-hjælperne og Supabase-kaldene kan ikke skelne "ingen rækker"
fra "opslaget fejlede", så de returnerer tomt i begge tilfælde. Svaret får
status 200, og _set_response_headers cachede udelukkende på statuskoden - en
forbigående bro- eller D1-fejl blev derfor frosset fast som "der findes ingen
varer" for ALLE besøgende på den URL i op til 24 timer (edge-TTL).

app.py markerer nu sådanne svar via _mark_data_degraded(), og header-laget
nægter at sætte CDN-Cache-Control på dem. Testen her fejlinjicerer hver kendt
fejlvej og kræver at CDN-headeren udebliver. Uden den er regressionen usynlig:
alt ser fint ud lokalt, hvor fejlene aldrig indtræffer.

Kør: .venv/bin/python scripts/test-degraded-cache.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as A  # noqa: E402

CDN = 'CDN-Cache-Control'
failures = []
checks = 0


def check(label: str, resp, *, cacheable: bool):
    """cacheable=True: svaret SKAL kunne edge-caches. False: må det ikke."""
    global checks
    checks += 1
    has_cdn = resp.headers.get(CDN) is not None
    if has_cdn != cacheable:
        failures.append(
            f"{label}: forventede {'CDN-cache' if cacheable else 'INGEN CDN-cache'}, "
            f"fik {CDN}={resp.headers.get(CDN)!r} (status {resp.status_code})"
        )
        print(f"  FEJL  {label}")
    else:
        print(f"  ok    {label}")


def main() -> int:
    client = A.app.test_client()
    products = A.get_product_data()
    if not products:
        print("Ingen produktdata - kan ikke køre testen.")
        return 1
    pid = str(products[0].get('/product/id'))

    print("Gyldige svar skal stadig kunne caches:")
    check("kategoriside", client.get('/Mejeri'), cacheable=True)
    check("søgeside", client.get('/search/results?q=maelk'), cacheable=True)
    check("næring, produkt uden data",
          client.get('/api/nutrition/findes-ikke-xyz'), cacheable=True)

    print("\nAJAX-fragmenter deler URL med den fulde side og må aldrig caches:")
    check("kategori som fragment",
          client.get('/Mejeri', headers={'X-Requested-With': 'XMLHttpRequest'}),
          cacheable=False)
    check("søgeside som fragment",
          client.get('/search/results?q=maelk',
                     headers={'X-Requested-With': 'XMLHttpRequest'}),
          cacheable=False)

    print("\nSupabase nede (status 0 = netværksfejl):")
    orig_rest = A._supabase_rest
    A._supabase_rest = lambda *a, **k: (None, 0)
    try:
        check("prishistorik", client.get(f'/api/price-history/{pid}'), cacheable=False)
        check("næring", client.get(f'/api/nutrition/{pid}'), cacheable=False)
    finally:
        A._supabase_rest = orig_rest

    # search_display_products er fælles bund for alle tre søgeveje (både
    # _build_search_listing og autocomplete går igennem den), så et kast her
    # svarer til at D1-opslaget eller Pyodide-broen svigter.
    print("\nProduktopslaget kaster (simuleret D1-/bro-kollision):")
    orig_search = A.search_display_products

    def boom(*a, **k):
        raise RuntimeError("simuleret bro-kollision")

    A.search_display_products = boom
    try:
        check("søgeside", client.get('/search/results?q=maelk'), cacheable=False)
        check("søgepanel", client.get('/search?q=maelk'), cacheable=False)
        check("autocomplete", client.get('/api/autocomplete?q=mae'), cacheable=False)
    finally:
        A.search_display_products = orig_search

    print("\nEfter genoprettelse skal caching virke igen:")
    check("kategoriside", client.get('/Mejeri'), cacheable=True)

    print()
    if failures:
        print(f"{len(failures)} af {checks} kontroller fejlede:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"Alle {checks} kontroller bestået.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
