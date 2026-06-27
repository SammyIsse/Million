from flask import Flask, render_template, send_from_directory, jsonify, request, redirect, url_for
import re
from datetime import datetime
import os
import json
from dotenv import load_dotenv
load_dotenv()
import random
import time
import threading
import urllib.parse

from app_support import (
    configure_logging, is_price_db_enabled, set_db_available, db_available,
    rate_limit, api_limiter, search_product_ids,
    product_matches_query, logger,
    _STORE_CONFIGS,
    normalize_name, fuzzy_score,
    parse_weight_to_grams, weights_compatible,
    _BLOCKED_NAME_FRAGMENTS, _PLACEHOLDER_IMGS,
    CAT_MEJERI, CAT_KOED_FISK, CAT_FRUGT_GROENT, CAT_BROED_KAGER,
    CAT_FROST, CAT_KOLONIAL, CAT_DRIKKEVARER, CAT_SLIK,
    _SUBCATEGORY_RULES, _get_subcategory,
    _product_type_words,
    parse_sale_end_date, product_to_display_dict,
    product_available_at_active_stores,
    product_for_active_stores,
)

configure_logging()

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Produkt-cache: alle butikker opdateres én gang dagligt (se cache-updater.yml)
cached_data = {
    'timestamp': None,
    'data': None,
    'search_index': None,
}
_cache_refresh_started = False
_cache_refresh_lock = threading.Lock()

_xml_cache_lock = threading.Lock()

def _filter_products_for_search(
    products: list, query: str, active_stores: set | None = None,
) -> list:
    """Use search index when available, else linear scan. Respects store selection."""
    def _to_display(raw: dict) -> dict | None:
        adjusted = product_for_active_stores(raw, active_stores)
        if not adjusted:
            return None
        return product_to_display_dict(adjusted, default_category='Andre varer')

    index = cached_data.get('search_index')
    if index:
        ids = search_product_ids(index, query)
        if ids is not None:
            id_set = ids
            results = []
            for p in products:
                if str(p.get('/product/id', '')) not in id_set:
                    continue
                if not p.get('/product/title') or not p.get('/product/id'):
                    continue
                d = _to_display(p)
                if d:
                    results.append(d)
            return results
    results = []
    for product in products:
        if not product.get('/product/title') or not product.get('/product/id'):
            continue
        d = _to_display(product)
        if d and product_matches_query(d, query):
            results.append(d)
    return results


def _supabase_rest_config():
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL") or ""
    key = (os.environ.get("DEPLOY_KEY") or
           os.environ.get("SUPABASE_KEY") or
           os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") or "")
    return url.rstrip("/"), key


def _should_refresh_product_cache(now=None):
    """Hent nye data én gang pr. dag — butikskataloger ændrer sig ikke i løbet af dagen."""
    now = now or datetime.now()
    if not cached_data.get('data'):
        return True
    ts = cached_data.get('timestamp')
    if not ts:
        return True
    return ts.date() < now.date()


_LOCAL_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'app_cache_local.json')


def _load_local_cache():
    """Læs lokal cache-fil som fallback når Supabase app_cache ikke er tilgængelig."""
    try:
        if not os.path.exists(_LOCAL_CACHE_FILE):
            return None
        with open(_LOCAL_CACHE_FILE, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        products = payload.get('products', [])
        search_index = payload.get('search_index', {})
        if products:
            logger.info(f"Lokal cache indlæst: {len(products)} produkter fra {_LOCAL_CACHE_FILE}")
            return products, search_index
    except Exception as e:
        logger.error(f"Fejl ved læsning af lokal cache: {e}")
    return None


def _refresh_product_cache():
    """Load pre-computed product data and search index from Supabase app_cache."""
    global cached_data
    try:
        import httpx
        base_url, supabase_key = _supabase_rest_config()
        if not base_url or not supabase_key:
            logger.error("Supabase URL eller key mangler — kan ikke hente app_cache")
            return
        headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
        url = f"{base_url}/rest/v1/app_cache?select=*&id=gte.0&order=id.asc"

        with httpx.Client(timeout=30.0) as client:
            res = client.get(url, headers=headers)
            if res.status_code == 200 and res.json():
                rows = res.json()

                _c_data = []
                _c_idx = {}

                for row in rows:
                    if row.get('id') == 0:
                        _c_idx = row.get('search_index', {})
                    else:
                        chunk_data = row.get('data', [])
                        if isinstance(chunk_data, list):
                            _c_data.extend(chunk_data)

                if _c_data or _c_idx:
                    cached_data = {
                        'timestamp': datetime.now(),
                        'data': _c_data,
                        'search_index': _c_idx,
                    }
                    logger.info(f"Product cache refreshed from Supabase app_cache ({len(_c_data)} produkter i {len(rows)-1} chunks)")
                    return
                else:
                    logger.warning("app_cache var tom")
            else:
                logger.warning(f"Supabase app_cache utilgængelig (status {res.status_code}) — prøver lokal cache")
    except Exception as e:
        logger.error(f"Error loading app_cache: {e}")

    local = _load_local_cache()
    if local:
        products, search_index = local
        cached_data = {
            'timestamp': datetime.now(),
            'data': products,
            'search_index': search_index,
        }


def _start_background_cache_refresh():
    """Refresh cache once per day when the calendar date changes."""
    global _cache_refresh_started
    with _cache_refresh_lock:
        if _cache_refresh_started:
            return
        _cache_refresh_started = True

    def _worker():
        while True:
            try:
                time.sleep(3600)
                if not _should_refresh_product_cache():
                    continue
                logger.info("Daily cache refresh starting")
                with _xml_cache_lock:
                    if not _should_refresh_product_cache():
                        continue
                    _refresh_product_cache()
            except Exception:
                logger.exception("Background cache refresh failed")

    threading.Thread(target=_worker, daemon=True, name='cache-refresh').start()


def get_product_data():
    """Get product data with caching"""
    global cached_data
    _start_background_cache_refresh()
    if _should_refresh_product_cache():
        with _xml_cache_lock:
            if _should_refresh_product_cache():
                _refresh_product_cache()
    else:
        logger.debug("Using cached product data")
    return cached_data['data'] or []

def get_active_stores():
    """Selected store labels from ?stores= or madshopper_stores cookie. None = all stores."""
    stores_param = request.args.get('stores')
    if stores_param is not None:
        labels = {s.strip() for s in stores_param.split(',') if s.strip()}
        return labels

    stores_cookie = request.cookies.get('madshopper_stores')
    if stores_cookie:
        try:
            unquoted = urllib.parse.unquote(stores_cookie)
            stores_list = json.loads(unquoted)
            if isinstance(stores_list, list) and len(stores_list) > 0:
                return {str(s).strip() for s in stores_list if str(s).strip()}
        except Exception:
            pass

    return None

_TOBACCO_IMG_RE = re.compile(
    r'rema-product-images\.digital\.rema1000\.dk/'
    r'(5213[4-9]\d|52[14]\d{3}|5218[0-2]\d|5618[2-7]\d)/'
)

def _is_tobacco_image(url: str) -> bool:
    m = _TOBACCO_IMG_RE.search(url)
    if not m:
        return False
    pid = int(m.group(1))
    return (521340 <= pid <= 521825) or (561828 <= pid <= 561875)

def filter_products_by_stores(products, active_stores):
    """Helper to filter products by store names, blocked images, and blocked product names."""
    def _is_allowed(p):
        img = str(p.get('/product/imageLink', '')).strip()
        if img in _PLACEHOLDER_IMGS or _is_tobacco_image(img):
            return False
        rema_img = str(p.get('/product/rema_image', '')).strip()
        if rema_img in _PLACEHOLDER_IMGS or _is_tobacco_image(rema_img):
            return False
        name = str(p.get('/product/title', '')).lower()
        if any(fragment in name for fragment in _BLOCKED_NAME_FRAGMENTS):
            return False
        bilka_brand = str((p.get('/product/store_matches') or {}).get('bilka', {}).get('brand', '')).lower().strip()
        if bilka_brand.startswith('deli'):
            return False
        if str(p.get('/product/store', '')).lower() == 'bilka' and str(p.get('/product/brand', '')).lower().strip().startswith('deli'):
            return False
        return True

    filtered = [p for p in products if _is_allowed(p)]
    if active_stores is None:
        return filtered
    return [p for p in filtered if product_available_at_active_stores(p, active_stores)]

def apply_product_filters(products, args):
    """Helper to apply price, sale, organic, weight, subcategory filters and sorting to a list of products"""
    min_price = args.get('min_price', type=float)
    max_price = args.get('max_price', type=float)
    sale_only = args.get('sale', type=str) == 'true'
    organic_only = args.get('organic', type=str) == 'true'
    lactose_only = args.get('lactose', type=str) == 'true'
    min_weight = args.get('min_weight', type=float)
    max_weight = args.get('max_weight', type=float)
    sort_type = args.get('sort', 'relevance')
    subcategory = args.get('subcategory', type=str) or ''

    filtered = []
    for p in products:
        # Use the effective price (sale price if active)
        price = p.get('sale_price') if p.get('is_sale') else p.get('price')
        if price is None: price = 0
        
        if min_price is not None and price < min_price: continue
        if max_price is not None and price > max_price: continue
        if sale_only and not p.get('is_sale') and not p.get('is_any_sale'): continue
        if subcategory and p.get('subcategory', '') != subcategory: continue
        
        # Organic check
        if organic_only:
            name_lower = p.get('name', '').lower()
            desc_lower = p.get('description', '').lower()
            brand_lower = p.get('brand', '').lower()
            combined = f"{name_lower} {desc_lower} {brand_lower}"
            if not any(x in combined for x in ['økolog', 'øko ', ' øko', 'organic']):
                continue
        
        # Lactose check
        if lactose_only:
            name_lower = p.get('name', '').lower()
            desc_lower = p.get('description', '').lower()
            combined = f"{name_lower} {desc_lower}"
            if not any(x in combined for x in ['laktosefri', 'lactose free']):
                continue

        # Weight check
        weight_g = p.get('weight_g')
        if weight_g is not None:
            if min_weight is not None and weight_g < min_weight: continue
            if max_weight is not None and weight_g > max_weight: continue
        elif min_weight is not None and min_weight > 0:
            continue

        filtered.append(p)

    # Sorting
    if sort_type == 'price-asc':
        filtered.sort(key=lambda x: (x.get('sale_price') if x.get('is_sale') else x.get('price')) or 0)
    elif sort_type == 'price-desc':
        filtered.sort(key=lambda x: (x.get('sale_price') if x.get('is_sale') else x.get('price')) or 0, reverse=True)
    elif sort_type == 'kg-price-asc':
        filtered.sort(key=lambda x: x.get('price_per_kg') or 999999)
    elif sort_type == 'name-asc':
        filtered.sort(key=lambda x: x.get('name', '').lower())
        
    return filtered

# --- PRICE HISTORY DATABASE ---
supabase = None

def init_db():
    if not is_price_db_enabled():
        set_db_available(False)
        logger.info("Price database disabled (ENABLE_PRICE_DB=0)")
        return
    try:
        url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
        key = os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_KEY")
        if key and (key.startswith("http://") or key.startswith("https://")):
            # Fall back to NEXT_PUBLIC key if SUPABASE_KEY is a URL placeholder
            key = os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
            
        if not url or not key:
            set_db_available(False)
            logger.warning("Supabase URL or Key not set. App runs without database.")
            return

        global supabase
        from supabase import create_client
        supabase = create_client(url, key)
        set_db_available(True)
        logger.info("Supabase connection initialized successfully.")
    except Exception as e:
        set_db_available(False)
        logger.warning("Supabase connection unavailable (%s). App runs without database.", e)

def get_popular_product_ids(limit=20):
    if not db_available() or not supabase:
        return []
    try:
        res = supabase.table("cart_popularity").select("product_id").order("count", desc=True).limit(limit).execute()
        return [row["product_id"] for row in res.data]
    except Exception as e:
        logger.error("Error fetching popular product ids: %s", e)
        return []

@app.route('/api/cart-event', methods=['POST'])
@rate_limit(api_limiter)
def cart_event():
    try:
        data = request.get_json(force=True)
        product_id = str(data.get('product_id', '')).strip()[:64]
        if not product_id:
            return jsonify({'ok': False}), 400
        if not db_available() or not supabase:
            return jsonify({'ok': True, 'persisted': False})
            
        # Increment popularity: select, then update or insert
        res = supabase.table("cart_popularity").select("count").eq("product_id", product_id).execute()
        if res.data:
            new_count = (res.data[0].get("count") or 0) + 1
            supabase.table("cart_popularity").update({"count": new_count}).eq("product_id", product_id).execute()
        else:
            supabase.table("cart_popularity").insert({"product_id": product_id, "count": 1}).execute()
            
        return jsonify({'ok': True, 'persisted': True})
    except Exception as e:
        logger.error("cart-event error: %s", e)
        return jsonify({'ok': False}), 500

@app.route('/api/price-history/<product_id>')
def get_price_history(product_id):
    if not db_available() or not supabase:
        return jsonify(success=True, history=[], history_by_store={})
    try:
        res = supabase.table("price_history").select("store, price, date").eq("product_id", str(product_id)[:64]).order("store").order("date").execute()
        
        by_store = {}
        for row in res.data:
            store = row.get("store")
            price = row.get("price")
            date = row.get("date")
            by_store.setdefault(store, []).append({'price': price, 'date': date})
            
        flat = by_store.get('rema') or next(iter(by_store.values()), [])
        return jsonify(success=True, history=flat, history_by_store=by_store)
    except Exception as e:
        logger.error("price-history error: %s", e)
        return jsonify(success=False, error='Kunne ikke hente prishistorik.')

@app.route('/api/create-alert', methods=['POST'])
@rate_limit(api_limiter)
def create_alert():
    try:
        data = request.get_json(silent=True) or {}
        p_id = str(data.get('product_id', '')).strip()[:64]
        p_name = str(data.get('product_name', '')).strip()[:200]
        if not p_id:
            return jsonify(success=False, error='Manglende produkt-id.'), 400
        try:
            target_val = data.get('target_price')
            current_val = data.get('current_price')
            if target_val is None or current_val is None:
                raise ValueError("Missing price")
            target = float(target_val)
            current = float(current_val)
        except (TypeError, ValueError):
            return jsonify(success=False, error='Ugyldig pris.'), 400
        if target <= 0 or current <= 0 or target > 99999:
            return jsonify(success=False, error='Ugyldig pris.'), 400

        if not db_available() or not supabase:
            return jsonify(success=True, persisted=False)

        supabase.table("price_alerts").insert({
            "product_id": p_id,
            "product_name": p_name,
            "target_price": target,
            "current_price": current
        }).execute()
        return jsonify(success=True, persisted=True)
    except Exception as e:
        logger.error("create-alert error: %s", e)
        return jsonify(success=False, error='Kunne ikke oprette alarm.')

init_db()

@app.route('/')
@app.route('/index.html')
def home():
    # Get active stores and filter data
    active_stores = get_active_stores()
    product_data = get_product_data()
    filtered_data = filter_products_by_stores(product_data, active_stores)
    
    # Shuffle for the "tilfældige varer" experience on the front page
    display_data = list(filtered_data)
    random.shuffle(display_data)

    products_by_category = {
        'Ugens Tilbud': [],
        'Brugernes Favoritter': [],
        CAT_MEJERI: [],
    }

    seen_tilbud_imgs = set()
    seen_cat_imgs = {cat: set() for cat in products_by_category}

    _STAPLES = {
        'mælk', 'brød', 'æg', 'smør', 'yoghurt', 'ost', 'juice',
        'havregryn', 'pasta', 'ris', 'rugbrød', 'fløde', 'kefir',
        'skyr', 'tomat', 'kartofler', 'løg', 'gulerødder', 'kylling',
        'hakket', 'leverpostej', 'syltetøj', 'marmelade', 'kaffe',
        'te', 'vand', 'cola', 'spaghetti', 'mel', 'sukker', 'salt',
    }

    def _staple_score(name):
        n = name.lower()
        return sum(1 for kw in _STAPLES if kw in n)

    seen_fav_imgs = set()
    used_fav_ids = set()

    def _try_add_fav(product):
        try:
            if float(product.get('/product/price', 0)) <= 0:
                return False
            pid = str(product.get('/product/id', ''))
            if pid in used_fav_ids:
                return False
            _img = str(product.get('/product/imageLink', '')).strip()
            if _img and _img not in ('nan', 'None') and _img not in _PLACEHOLDER_IMGS:
                if _img in seen_fav_imgs:
                    return False
                seen_fav_imgs.add(_img)
            products_by_category['Brugernes Favoritter'].append(
                product_to_display_dict(
                    product,
                    category=product.get('/product/product_type', CAT_KOLONIAL),
                )
            )
            used_fav_ids.add(pid)
            return True
        except (ValueError, TypeError):
            return False

    _cat_keys = {CAT_MEJERI}
    staple_scored = []

    # Single pass: populate Ugens Tilbud, all categories, and collect staple scores
    for product in display_data:
        ptype = product.get('/product/product_type')
        if ptype is None:
            continue
        try:
            price = float(product.get('/product/price', 0))
            _img = str(product.get('/product/imageLink', '')).strip()
            _img_valid = _img and _img not in ('nan', 'None') and _img not in _PLACEHOLDER_IMGS

            # Ugens Tilbud
            if product.get('/product/sale_price') or product.get('/product/is_any_sale'):
                sale_end_date = parse_sale_end_date(product)
                if not _img_valid or _img not in seen_tilbud_imgs:
                    if _img_valid:
                        seen_tilbud_imgs.add(_img)
                    products_by_category['Ugens Tilbud'].append(
                        product_to_display_dict(product, category=ptype, sale_end_date=sale_end_date)
                    )

            # Regular categories
            if ptype in _cat_keys and price > 0:
                if not _img_valid or _img not in seen_cat_imgs[ptype]:
                    if _img_valid:
                        seen_cat_imgs[ptype].add(_img)
                    products_by_category[ptype].append(
                        product_to_display_dict(product, category=ptype)
                    )

            # Staple scoring for Brugernes Favoritter fallback
            score = _staple_score(str(product.get('/product/title', '')))
            if score > 0:
                staple_scored.append((score, product))

        except (ValueError, TypeError, KeyError):
            continue

    # Brugernes Favoritter — Step 1: popularity data
    popular_ids = get_popular_product_ids(limit=20)
    if popular_ids:
        id_to_product = {str(p.get('/product/id', '')): p for p in display_data}
        leftover_ids = []
        for pid in popular_ids:
            product = id_to_product.get(pid)
            if not product:
                continue
            if product.get('/product/store_matches'):
                _try_add_fav(product)
            else:
                leftover_ids.append(pid)
        for pid in leftover_ids:
            product = id_to_product.get(pid)
            if product:
                _try_add_fav(product)

    # Brugernes Favoritter — Step 2: staple fallback
    if len(products_by_category['Brugernes Favoritter']) < 10:
        staple_scored.sort(key=lambda x: x[0], reverse=True)
        for _, product in staple_scored:
            if len(products_by_category['Brugernes Favoritter']) >= 20:
                break
            _try_add_fav(product)

    # Apply advanced filters to each category
    filtered_categories = {}
    for cat, products in products_by_category.items():
        if products:
            filtered = apply_product_filters(products, request.args)
            if filtered:
                filtered_categories[cat] = filtered

    trimmed_categories = {k: v[:60] for k, v in filtered_categories.items() if v}
    template_mapping = {
        'Ugens Tilbud':         '/ugens_tilbud',
        'Brugernes Favoritter': None,
        CAT_MEJERI:             '/Mejeri',
    }

    # Handle AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render_template(
            'partials/index_products.html',
            categories=trimmed_categories,
            template_mapping=template_mapping
        )

    # Prices are recorded centrally in get_product_data() — no duplicate call here

    return render_template(
        'index.html',
        categories=trimmed_categories,
        template_mapping=template_mapping,
    )

@app.route('/vilkaar.html')
@app.route('/terms-of-service')
def terms_of_service():
    return render_template('terms.html')


@app.route('/om-os.html')
@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/feedback.html')
@app.route('/feedback')
def feedback_page():
    return render_template('feedback.html')


@app.route('/api/feedback', methods=['POST'])
@rate_limit(api_limiter)
def submit_feedback():
    data = request.get_json(silent=True) or {}
    feedback_type = str(data.get('type', 'feedback')).strip()[:50]
    message = str(data.get('message', '')).strip()
    name = str(data.get('name', '')).strip()[:120] or None
    email = str(data.get('email', '')).strip()[:200] or None
    subject = str(data.get('subject', '')).strip()[:200] or None
    page_url = str(data.get('page_url', '')).strip()[:500] or None

    allowed_types = {'feedback', 'bug', 'feature', 'other'}
    if feedback_type not in allowed_types:
        feedback_type = 'feedback'

    if len(message) < 10:
        return jsonify(success=False, error='Beskeden skal være mindst 10 tegn.'), 400
    if len(message) > 5000:
        return jsonify(success=False, error='Beskeden er for lang (maks. 5000 tegn).'), 400

    if not db_available() or not supabase:
        logger.info("Feedback received (DB off): %s", feedback_type)
        return jsonify(success=True, persisted=False)

    try:
        supabase.table("feedback").insert({
            "feedback_type": feedback_type,
            "name": name,
            "email": email,
            "subject": subject,
            "message": message,
            "page_url": page_url,
            "created_at": datetime.now().isoformat(timespec='seconds')
        }).execute()
        return jsonify(success=True, persisted=True)
    except Exception as e:
        logger.error('Feedback save error: %s', e)
        return jsonify(success=False, error='Kunne ikke gemme din besked. Prøv igen senere.'), 500


@app.route('/sale.html')
def sale_html_redirect():
    return redirect(url_for('ugens_tilbud'), 301)

@app.route('/ugens_tilbud')
def ugens_tilbud():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 60  # 6x10 layout
        
        active_stores = get_active_stores()
        product_data = get_product_data()
        filtered_data = filter_products_by_stores(product_data, active_stores)
        
        sale_products = []
        for product in filtered_data:
            if product.get('/product/sale_price') or product.get('/product/is_any_sale'):
                try:
                    sale_products.append(
                        product_to_display_dict(
                            product,
                            default_category='Andre varer',
                            sale_end_date=parse_sale_end_date(product),
                            force_sale=bool(product.get('/product/sale_price')),
                        )
                    )
                except (ValueError, TypeError, KeyError) as e:
                    logger.warning(
                        "Error converting sale product %s: %s",
                        product.get('/product/id'),
                        e,
                    )
                    continue
        
        # Apply Filters
        sale_products = apply_product_filters(sale_products, request.args)

        # Calculate pagination
        total_products = len(sale_products)
        total_pages = (total_products + per_page - 1) // per_page
        page = min(max(page, 1), total_pages) if total_pages > 0 else 1
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_products = sale_products[start_idx:end_idx]
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render_template('partials/product_grid.html', 
                                 products=paginated_products,
                                 current_page=page,
                                 total_pages=total_pages)

        return render_template('category.html',
                            category_name='Ugens Tilbud',
                            products=paginated_products,
                            current_page=page,
                            total_pages=total_pages)

    except Exception as e:
        logger.error("Error loading sale page: %s", e)
        return "Page not found", 404

@app.route('/api/autocomplete')
def autocomplete():
    """Returns up to 8 slim product suggestions for the search autocomplete dropdown."""
    query = request.args.get('q', '').strip().lower()
    if len(query) < 2:
        return jsonify({'suggestions': []})

    try:
        active_stores = get_active_stores()
        product_data = get_product_data()
        filtered_data = filter_products_by_stores(product_data, active_stores)
        matched = _filter_products_for_search(filtered_data, query, active_stores)
        seen_names = set()
        suggestions = []

        for d in matched:
            if len(suggestions) >= 8:
                break
            key = normalize_name(d['name'])
            if key in seen_names:
                continue
            seen_names.add(key)
            price = float(d.get('sale_price') or d.get('price') or 0)
            suggestions.append({
                'name': d['name'],
                'brand': d.get('brand', ''),
                'price': round(price, 2),
                'is_sale': bool(d.get('is_sale')),
                'image': d.get('image_url', ''),
                'category': d.get('category', ''),
            })

        return jsonify({'suggestions': suggestions})

    except Exception as e:
        logger.error("Autocomplete error: %s", e)
        return jsonify({'suggestions': []})


@app.route('/search')
def search():
    """API endpoint for search suggestions as user types"""
    query = request.args.get('q', '').lower().strip()
    
    if not query:
        return jsonify(html='<div class="no-results">Indtast søgeord</div>')
    
    try:
        active_stores = get_active_stores()
        product_data = get_product_data()
        filtered_data = filter_products_by_stores(product_data, active_stores)
        all_products = _filter_products_for_search(filtered_data, query, active_stores)

        if len(all_products) == 0:
            return jsonify(html='<div class="no-results">Ingen resultater fundet</div>')
            
        products_html = render_template('partials/search_products.html', products=all_products)
        return jsonify(html=products_html)
        
    except Exception as e:
        logger.exception("Error in search route: %s", e)
        return jsonify(html='<div class="error">Der opstod en fejl under søgningen</div>')

@app.route('/search/results')
def search_page():
    """Full page search results"""
    query = ""
    try:
        page = request.args.get('page', 1, type=int)
        query = request.args.get('q', '').lower().strip()
        per_page = 60  # 6x10 layout
        
        if not query:
            return redirect(url_for('home'))
        
        active_stores = get_active_stores()
        product_data = get_product_data()
        filtered_data = filter_products_by_stores(product_data, active_stores)
        all_products = _filter_products_for_search(filtered_data, query, active_stores)

        # Apply Filters
        all_products = apply_product_filters(all_products, request.args)

        # Calculate pagination
        total_products = len(all_products)
        if total_products == 0:
            return render_template('search_results.html', 
                                query=query,
                                products=[],
                                total_products=0,
                                current_page=1,
                                total_pages=1)
            
        total_pages = (total_products + per_page - 1) // per_page
        page = min(max(page, 1), total_pages)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_products = all_products[start_idx:end_idx]

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # For AJAX filtering
            return render_template('partials/product_grid.html', 
                                 products=paginated_products,
                                 current_page=page,
                                 total_pages=total_pages)

        return render_template('search_results.html',
                            query=query,
                            products=paginated_products,
                            total_products=total_products,
                            current_page=page,
                            total_pages=total_pages)
    
    except Exception as e:
        logger.exception("Error in search: %s", e)
        return render_template('search_results.html',
                            query=query,
                            products=[],
                            total_products=0,
                            current_page=1,
                            total_pages=1,
                            error="Der opstod en fejl under søgningen")

@app.route('/<category_name>.html')
def category_html_redirect(category_name):
    return redirect(f'/{category_name}', 301)

@app.route('/<category_name>')
def category(category_name):
    # Reverse mapping for filenames to category names
    category_mapping = {
        'Kolonial': CAT_KOLONIAL,
        'Drikkevarer': CAT_DRIKKEVARER,
        'Mejeri': CAT_MEJERI,
        'Køl': CAT_MEJERI,
        'Frugt_og_groent': CAT_FRUGT_GROENT,
        'Frost': CAT_FROST,
        'Broed_og_kager': CAT_BROED_KAGER,
        'Koed_og_fisk': CAT_KOED_FISK,
        'Slik': CAT_SLIK,
    }
    
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 60  # 6x10 layout
        
        actual_category = category_mapping.get(category_name)
        if not actual_category:
            return "Category not found", 404
            
        # Get products for this category
        active_stores = get_active_stores()
        product_data = get_product_data()
        filtered_data = filter_products_by_stores(product_data, active_stores)
        
        category_products = []
        
        for product in filtered_data:
            p_type = product.get('/product/product_type')
            if p_type and str(p_type) == actual_category:
                try:
                    category_products.append(
                        product_to_display_dict(product, category=str(p_type))
                    )
                except Exception as e:
                    logger.warning("Error processing product in category: %s", e)
                    continue

        # Prices are recorded centrally in get_product_data() — no duplicate call here

        # Compute ordered subcategory list from unfiltered products
        rules = _SUBCATEGORY_RULES.get(actual_category, [])
        _seen_subs = {p.get('subcategory', '') for p in category_products}
        available_subcategories = [sub for sub, _ in rules if sub in _seen_subs]
        if 'Øvrige' in _seen_subs:
            available_subcategories.append('Øvrige')
        current_subcategory = request.args.get('subcategory', '')

        # Apply Filters
        category_products = apply_product_filters(category_products, request.args)

        # Calculate pagination
        total_products = len(category_products)
        total_pages = (total_products + per_page - 1) // per_page
        page = min(max(page, 1), total_pages) if total_pages > 0 else 1
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_products = category_products[start_idx:end_idx]

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render_template('partials/product_grid.html',
                                 products=paginated_products,
                                 current_page=page,
                                 total_pages=total_pages)

        return render_template('category.html',
                            category_name=actual_category,
                            products=paginated_products,
                            current_page=page,
                            total_pages=total_pages,
                            available_subcategories=available_subcategories,
                            current_subcategory=current_subcategory)
                            
    except Exception as e:
        logger.exception("Error loading category %s: %s", category_name, e)
        return "Internal Server Error", 500

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

@app.route('/product/<product_id>')
def get_product_info(product_id):
    """Get product information and print debug info"""
    try:
        product_data = get_product_data()
        
        # Find the product with the matching ID
        product = next((p for p in product_data if str(p['/product/id']) == str(product_id)), None)
        
        if product:
            logger.debug("Product info requested for %s: %s", product_id, product.get('/product/title'))
            
            return jsonify({
                'success': True,
                'product': {
                    'rema_price': product['/product/price'],
                    'bilka_price': product.get('/product/store_matches', {}).get('bilka', {}).get('price')
                }
            })
        else:
            logger.info(f"Product not found with ID: {product_id}")
            return jsonify(success=False, error="Product not found"), 404
            
    except Exception as e:
        logger.error(f"Error getting product info: {str(e)}")
        return jsonify(success=False, error=str(e)), 500

@app.route('/api/stores')
def get_stores():
    stores = [{'key': k, 'label': v['label'], 'logo': v['logo']} for k, v in _STORE_CONFIGS.items()]
    return jsonify({'stores': stores})


@app.route('/api/products', methods=['GET'])
def get_separate_products():
    """Returns slim price data from the existing cache for cart store comparison."""
    try:
        products = get_product_data()
        rema = [
            {
                '/product/id': p.get('/product/id', ''),
                '/product/price': p.get('/product/price'),
                '/product/sale_price': p.get('/product/sale_price'),
                '/product/store_matches': {
                    k: {'price': v.get('price')}
                    for k, v in (p.get('/product/store_matches') or {}).items()
                },
            }
            for p in products
            if p.get('/product/store') == 'Rema 1000'
        ]
        return jsonify({'success': True, 'rema_products': rema, 'bilka_products': []})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/alternatives', methods=['POST'])
def find_alternatives():
    try:
        data = request.json
        missing_items = data.get('missing_items', [])
        if not missing_items:
            return jsonify({'success': True, 'alternatives': []})
            
        product_data = get_product_data()
        
        alternatives = []
        for req_item in missing_items:
            cart_id = req_item.get('cart_id')
            store_label = req_item.get('store')
            category = req_item.get('category')
            name = req_item.get('name', '')
            weight_str = req_item.get('weight_str', '')
            weight_g = parse_weight_to_grams(weight_str) if weight_str else None

            subcategory = _get_subcategory(name, category)
            orig_type_words = _product_type_words(name)
            best_alt = None
            best_score = -1.0
            best_price = float('inf')
            norm_orig = normalize_name(name)

            for p in product_data:
                p_store = p.get('/product/store', 'Rema 1000')
                p_matches = p.get('/product/store_matches', {})

                target_price = None
                p_name_store = p.get('/product/title', '')
                p_image_store = p.get('/product/imageLink', '')

                if p_store == store_label:
                    target_price = p.get('/product/sale_price') or p.get('/product/price')
                else:
                    for match_key, match_data in p_matches.items():
                        store_cfg = _STORE_CONFIGS.get(match_key)
                        if store_cfg and store_cfg['label'] == store_label:
                            target_price = match_data.get('normal_price') or match_data.get('price')
                            if match_data.get('name'):
                                p_name_store = match_data.get('name')
                            if match_data.get('image') and str(match_data.get('image')).lower() not in ('nan', 'none'):
                                p_image_store = match_data.get('image')
                            break

                if target_price is None or float(target_price) <= 0:
                    continue

                target_price = float(target_price)

                p_category = p.get('/product/product_type', '')
                if p_category != category:
                    continue

                p_name_base = p.get('/product/title', '')
                p_subcat = _get_subcategory(p_name_base, p_category)

                # Named subcategories: must match exactly (handles "energidrik" ↔ "energy drink").
                # For 'Øvrige': subcategory label is too generic, so use word overlap instead.
                if p_subcat != subcategory:
                    continue
                if subcategory == 'Øvrige' and orig_type_words:
                    alt_type_words = _product_type_words(p_name_base)
                    if alt_type_words and not orig_type_words & alt_type_words:
                        continue

                # Weight check
                p_weight_g = p.get('/product/weight_g')
                if weight_g is not None and p_weight_g is not None:
                    # Allow up to 100g difference for alternatives
                    if not weights_compatible(weight_g, p_weight_g, 100):
                        continue

                # Skip same product or completely unrelated names
                sim = fuzzy_score(norm_orig, normalize_name(p_name_base))
                if sim > 0.9 or sim < 0.25:
                    continue

                # Pick by highest name similarity; use price as tiebreaker
                if sim > best_score or (sim == best_score and target_price < best_price):
                    best_score = sim
                    best_price = target_price

                    new_storePrices = {}
                    base_price = p.get('/product/sale_price') or p.get('/product/price')
                    if base_price:
                        new_storePrices[p_store] = float(base_price)

                    for match_key, match_data in p_matches.items():
                        store_cfg = _STORE_CONFIGS.get(match_key)
                        if store_cfg:
                            mp = match_data.get('normal_price') or match_data.get('price')
                            if mp:
                                new_storePrices[store_cfg['label']] = float(mp)

                    best_alt = {
                        'cart_id': cart_id,
                        'store': store_label,
                        'alt_id': str(p.get('/product/id', '')),
                        'alt_name': p_name_store,
                        'alt_price': best_price,
                        'alt_image': p_image_store,
                        'alt_storePrices': new_storePrices,
                        'alt_category': p_category,
                        'alt_unitMeasure': p.get('/product/unit_pricing_measure', ''),
                        'alt_kgPrice': p.get('/product/price_per_kg', ''),
                        'alt_store': p_store
                    }
            
            if best_alt:
                alternatives.append(best_alt)
                
        return jsonify({'success': True, 'alternatives': alternatives})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/refresh-cache', methods=['POST'])
def refresh_cache():
    """Invalidate local cache after updater.py — protected by CACHE_REFRESH_SECRET."""
    secret = os.environ.get('CACHE_REFRESH_SECRET', '')
    if not secret or request.headers.get('X-Cache-Secret') != secret:
        return jsonify({'ok': False}), 401
    with _xml_cache_lock:
        _refresh_product_cache()
    return jsonify({
        'ok': True,
        'products': len(cached_data.get('data') or []),
    })


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    port = int(os.environ.get('PORT', '5001'))
    logger.info(
        "Starting server debug=%s port=%s db=%s",
        debug,
        port,
        db_available(),
    )
    app.run(debug=debug, host='0.0.0.0', port=port)