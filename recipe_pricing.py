"""Forudberegn opskrift-priser og tilbudsdækning (recipe_price_snapshot).

Ren prisberegning over allerede matchede ingredienser
(recipe_ingredients.matched_product_id) - ingen AI/Ollama involveret, så det
kan køre i samme workflow som updater.py (.github/workflows/cache-updater.yml)
lige efter app_cache er bygget frisk, i stedet for i recipe-import.yml.

Læses ved sidevisning som et opslag (app.py /api/recipes) frem for en live
join mod aktuelle priser pr. request - samme grund som home_data_v1 (KV): tung
per-request-beregning på edge var årsagen til nedbruddet 2026-07-19, se
CLAUDE.md § Miljøer & deploy.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

from app_support import logger
from recipe_matching import load_current_products


def _get_supabase_client():
    """Samme env-fallback-mønster som updater.py's _get_supabase_client."""
    url = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    key = (
        os.getenv('DEPLOY_KEY')
        or os.getenv('SUPABASE_KEY')
        or os.getenv('NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY')
    )
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


def _product_price_points(product: dict) -> list[dict]:
    """Alle nuværende (butik, pris, is_sale)-punkter for ét produkt-kort:
    forsidens egen butik + alle store_matches - samme effektiv-pris-logik som
    updater.py._display_item_to_match (sale_price hvis is_sale, ellers price)."""
    points = []
    sale = product.get('/product/sale_price')
    is_sale = sale is not None
    try:
        price = float(sale) if is_sale else float(product.get('/product/price', 0) or 0)
    except (TypeError, ValueError):
        price = 0.0
    if price > 0:
        points.append({
            'store': product.get('/product/store', ''),
            'price': price,
            'is_sale': bool(is_sale),
        })

    for store_name, match in (product.get('/product/store_matches') or {}).items():
        if not isinstance(match, dict):
            continue
        try:
            m_price = float(match.get('price') or 0)
        except (TypeError, ValueError):
            m_price = 0.0
        if m_price > 0:
            points.append({
                'store': store_name,
                'price': m_price,
                'is_sale': bool(match.get('is_sale')),
            })
    return points


def compute_recipe_price_snapshots() -> None:
    """Genberegn recipe_price_snapshot for alle godkendte opskrifter."""
    client = _get_supabase_client()
    if client is None:
        logger.error('Supabase-forbindelse mangler - kan ikke beregne opskrift-priser')
        return

    products = load_current_products(client)
    product_by_id = {}
    for p in products:
        pid = str(p.get('/product/id', '')).strip()
        if pid:
            product_by_id[pid] = p

    recipes = (
        client.table('recipes').select('id').eq('status', 'approved').execute()
    ).data or []
    if not recipes:
        logger.info('Ingen godkendte opskrifter at prisberegne')
        return

    computed = 0
    for recipe in recipes:
        recipe_id = recipe['id']
        ingredients = (
            client.table('recipe_ingredients')
            .select('matched_product_id')
            .eq('recipe_id', recipe_id)
            .execute()
        ).data or []
        total_count = len(ingredients)
        if total_count == 0:
            continue

        matched_count = 0
        on_sale_count = 0
        cheapest_total = 0.0
        breakdown = {}

        for ing in ingredients:
            pid = ing.get('matched_product_id')
            product = product_by_id.get(pid) if pid else None
            if not product:
                continue
            points = _product_price_points(product)
            if not points:
                continue
            matched_count += 1
            cheapest = min(points, key=lambda pt: pt['price'])
            cheapest_total += cheapest['price']
            if any(pt['is_sale'] for pt in points):
                on_sale_count += 1
            breakdown[pid] = {'store': cheapest['store'], 'price': cheapest['price']}

        client.table('recipe_price_snapshot').upsert({
            'recipe_id': recipe_id,
            'computed_at': 'now()',
            'cheapest_total_price': round(cheapest_total, 2) if matched_count else None,
            'matched_ingredient_count': matched_count,
            'total_ingredient_count': total_count,
            'ingredients_on_sale_count': on_sale_count,
            'cheapest_store_breakdown': breakdown,
        }).execute()
        computed += 1

    logger.info(f'Prisberegnet {computed}/{len(recipes)} godkendte opskrifter')


if __name__ == '__main__':
    compute_recipe_price_snapshots()
