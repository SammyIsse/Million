#!/usr/bin/env python3
"""Kontrakt-test for native listing-API'er (docs/native-app.md Fase 0).

Kør: uv run python scripts/test-listing-api.py

Tester:
  1. product_to_api_dict-schema (alle card-felter)
  2. Route-registrering + JSON-svarform via Flask test client
  3. Filtre/pagination-query-params accepteres uden 500

Data-afhængige asserts (produkter > 0) skips blødt hvis lokal cache mangler.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault('ENABLE_PRICE_DB', '0')

from app_support import product_to_api_dict, product_to_display_dict  # noqa: E402
import app as app_module  # noqa: E402

flask_app = app_module.app

_PRODUCT_REQUIRED = {
    'id', 'name', 'brand', 'description', 'image', 'main_image', 'rema_image',
    'category', 'subcategory', 'store', 'price', 'normal_price', 'is_sale',
    'is_any_sale', 'sale_end_date', 'unit_measure', 'weight_g', 'stk_count',
    'kg_price', 'multi_deal', 'is_organic', 'is_lactose_free', 'has_match',
    'has_match_rema', 'cheapest_at', 'cheaper_at', 'rema_price', 'rema_is_sale',
    'lowest_price_30d', 'store_matches',
}

_MATCH_REQUIRED = {
    'name', 'price', 'normal_price', 'is_sale', 'image', 'brand',
    'description', 'weight', 'kg_price', 'multi_deal', 'ean', 'Kategori',
}

_failures = 0


def ok(msg: str) -> None:
    print(f'  OK  {msg}')


def fail(msg: str) -> None:
    global _failures
    _failures += 1
    print(f' FAIL {msg}')


def warn(msg: str) -> None:
    print(f' WARN {msg}')


def _sample_raw() -> dict:
    return {
        '/product/id': 'abc123',
        '/product/title': 'Øko Letmælk 1 L',
        '/product/price': 14.95,
        '/product/sale_price': 11.5,
        '/product/description': '1 L',
        '/product/brand': 'Arla',
        '/product/imageLink': 'https://rema-product-images.digital.rema1000.dk/1.jpg',
        '/product/rema_image': 'https://rema-product-images.digital.rema1000.dk/1.jpg',
        '/product/store': 'Rema 1000',
        '/product/unit_pricing_measure': '1000 g',
        '/product/stk_count': None,
        '/product/price_per_kg': 11.5,
        '/product/rema_price': 11.5,
        '/product/rema_is_sale': True,
        '/product/multi_deal': '2 for 20',
        '/product/cheapest_at': 'rema',
        '/product/product_type': 'Køl',
        '/product/is_any_sale': True,
        '/product/lowest_price_30d': 10.0,
        '/product/store_matches': {
            'bilka': {
                'name': 'Øko Letmælk',
                'price': 12.0,
                'normal_price': 15.0,
                'is_sale': True,
                'image': 'https://digitalassets.sallinggroup.com/x.jpg',
                'brand': 'Arla',
                'description': '1 L',
                'weight': '1 L',
                'kg_price': 12.0,
                'multi_deal': '',
                'ean': '5701234567890',
                'Kategori': 'Mejeri',
            },
        },
    }


def test_serializer() -> None:
    print('\n== product_to_api_dict ==')
    display = product_to_display_dict(_sample_raw(), category='Køl')
    api = product_to_api_dict(display)
    missing = _PRODUCT_REQUIRED - set(api)
    if missing:
        fail(f'manglende felter: {sorted(missing)}')
    else:
        ok(f'alle {len(_PRODUCT_REQUIRED)} kernfelter til stede')

    if api['price'] != 11.5 or api['normal_price'] != 14.95:
        fail(f'pris-mapping forkert: price={api["price"]} normal={api["normal_price"]}')
    else:
        ok('price=sale, normal_price=listepris')

    if not api['is_organic']:
        fail('is_organic skulle være True for Øko-titel')
    else:
        ok('is_organic True')

    if not api['has_match'] or not api['has_match_rema']:
        fail('has_match / has_match_rema')
    else:
        ok('has_match + has_match_rema')

    match = api['store_matches'].get('bilka') or {}
    miss_m = _MATCH_REQUIRED - set(match)
    if miss_m:
        fail(f'store_matches.bilka mangler: {sorted(miss_m)}')
    else:
        ok('store_matches-entry har alle felter')


def _assert_product_list(products: list, label: str) -> None:
    if not isinstance(products, list):
        fail(f'{label}: products er ikke en liste')
        return
    if not products:
        warn(f'{label}: tom produktliste (ingen lokal cache?)')
        return
    sample = products[0]
    missing = _PRODUCT_REQUIRED - set(sample)
    if missing:
        fail(f'{label}: produkt mangler {sorted(missing)}')
    else:
        ok(f'{label}: {len(products)} produkter, schema OK')


def test_routes() -> None:
    print('\n== Flask listing-routes ==')
    client = flask_app.test_client()

    expected = {
        '/api/home': 'api_home',
        '/api/sale': 'api_sale',
        '/api/category/Mejeri': 'api_category',
        '/api/search': 'api_search',
    }
    rules = {r.endpoint: r.rule for r in flask_app.url_map.iter_rules()}
    for ep in ('api_home', 'api_sale', 'api_category', 'api_search'):
        if ep not in rules:
            fail(f'endpoint {ep} ikke registreret')
        else:
            ok(f'endpoint {ep} → {rules[ep]}')

    # /api/home
    r = client.get('/api/home')
    if r.status_code != 200:
        fail(f'GET /api/home → {r.status_code}')
    else:
        data = r.get_json()
        if not data or not data.get('success'):
            fail('/api/home: success=false')
        elif 'sections' not in data or 'personal_savings' not in data:
            fail('/api/home: mangler sections/personal_savings')
        else:
            ok(f'/api/home: {len(data["sections"])} sektioner')
            for sec in data['sections']:
                _assert_product_list(sec.get('products') or [], f'home/{sec.get("key")}')

    # /api/sale
    r = client.get('/api/sale?page=1')
    if r.status_code != 200:
        fail(f'GET /api/sale → {r.status_code}')
    else:
        data = r.get_json()
        if not data or not data.get('success'):
            fail('/api/sale: success=false')
        elif 'page' not in data or 'total_pages' not in data or 'per_page' not in data:
            fail('/api/sale: mangler pagination-felter')
        elif data.get('per_page') != 60:
            fail(f'/api/sale: per_page={data.get("per_page")} (forventet 60)')
        else:
            ok(f'/api/sale: page={data["page"]} total_pages={data["total_pages"]}')
            _assert_product_list(data.get('products') or [], 'sale')

    # /api/category
    for slug in ('Mejeri', 'Koed_og_fisk', 'Slik'):
        r = client.get(f'/api/category/{slug}?page=1')
        if r.status_code != 200:
            fail(f'GET /api/category/{slug} → {r.status_code}')
            continue
        data = r.get_json()
        if not data or not data.get('success'):
            fail(f'/api/category/{slug}: success=false')
        elif 'available_subcategories' not in data:
            fail(f'/api/category/{slug}: mangler available_subcategories')
        else:
            ok(f'/api/category/{slug}: {len(data.get("products") or [])} produkter')

    r = client.get('/api/category/UkendtKategori')
    if r.status_code != 404:
        fail(f'ukendt kategori skulle give 404, fik {r.status_code}')
    else:
        ok('ukendt kategori → 404')

    # /api/search
    r = client.get('/api/search')
    data = r.get_json() or {}
    if r.status_code != 200 or not data.get('success') or data.get('products') != []:
        fail(f'tom /api/search: {r.status_code} {data}')
    else:
        ok('tom q → tom produktliste')

    r = client.get('/api/search?q=mælk&page=1&sort=relevance')
    if r.status_code != 200:
        fail(f'GET /api/search?q=mælk → {r.status_code}')
    else:
        data = r.get_json()
        if not data or not data.get('success'):
            fail('/api/search: success=false')
        else:
            ok(f'/api/search?q=mælk: total={data.get("total")} page={data.get("page")}')
            _assert_product_list(data.get('products') or [], 'search')

    # Filter-params må ikke 500'e
    for path in (
        '/api/home?sale=true&organic=true&sort=price-asc',
        '/api/sale?min_price=5&max_price=50&sort=kg-price-asc',
        '/api/category/Mejeri?subcategory=Ost&lactose=true',
        '/api/search?q=ost&stores=Rema%201000,Bilka&sort=name-asc',
    ):
        r = client.get(path)
        if r.status_code >= 500:
            fail(f'{path} → {r.status_code}')
        else:
            ok(f'filtre OK: {path.split("?")[0]} → {r.status_code}')


def main() -> int:
    print('Listing-API kontrakt-test (native-app Fase 0)')
    test_serializer()
    test_routes()
    print()
    if _failures:
        print(f'{_failures} fejl')
        return 1
    print('Alle tests bestået')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
