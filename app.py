from flask import Flask, render_template, send_from_directory, jsonify, request, redirect, url_for, Response
import hmac
import re
from datetime import datetime, timedelta
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
    rate_limit, api_limiter, cart_event_limiter, _client_ip, search_product_ids,
    product_matches_query, product_matches_query_fuzzy, logger,
    _STORE_CONFIGS,
    normalize_name, fuzzy_score,
    parse_weight_to_grams, weights_compatible,
    is_non_food_name, is_organic, is_lactose_free, _PLACEHOLDER_IMGS,
    CAT_MEJERI, CAT_KOED_FISK, CAT_FRUGT_GROENT, CAT_BROED_KAGER,
    CAT_FROST, CAT_KOLONIAL, CAT_DRIKKEVARER, CAT_SLIK,
    _SUBCATEGORY_RULES, _get_subcategory,
    _product_type_words,
    parse_sale_end_date, product_to_display_dict,
    product_available_at_active_stores,
    product_for_active_stores,
    STORE_CATALOG_VERSION,
    stores_auto_enable_since,
    STORES_ADDED_IN_VERSION,
    nutrition_candidate_keys,
)

configure_logging()

_IS_EDGE = os.environ.get('CLOUDFLARE_WORKERS') == '1'
SITE_URL = os.environ.get('SITE_URL', 'https://madshopper.dk').rstrip('/')
_PUBLIC_CATEGORY_PATHS = (
    'Mejeri', 'Koed_og_fisk', 'Frugt_og_groent', 'Broed_og_kager',
    'Kolonial', 'Frost', 'Drikkevarer', 'Slik',
)
_APP_ROOT = os.path.dirname(os.path.abspath(__file__))

# Ingen produktnavn/brand er i nærheden af så langt - en ekstremt lang
# søgestreng koster kun performance (flere AND/LIKE-led i D1, flere ord
# gennem rapidfuzz-fallback) uden at kunne finde noget. Klippes ved kilden,
# så al nedstrøms søgekode (D1, index, fuzzy) altid arbejder på et lille input.
_MAX_SEARCH_QUERY_LEN = 100


def _clean_search_query(raw: str) -> str:
    return (raw or '').strip().lower()[:_MAX_SEARCH_QUERY_LEN]


app = Flask(
    __name__,
    template_folder=os.path.join(_APP_ROOT, 'templates'),
    static_folder=os.path.join(_APP_ROOT, 'static'),
)
app.config['JSON_SORT_KEYS'] = False

# Produkt-cache: alle butikker opdateres én gang dagligt (se cache-updater.yml)
cached_data = {
    'timestamp': None,
    'data': None,
    'search_index': None,
}
_category_index: dict[str, list] | None = None
_cache_refresh_started = False
_cache_refresh_lock = threading.Lock()

_xml_cache_lock = threading.Lock()

_KV_CACHE_KEY = 'app_cache_v1'
_HOME_KV_KEY = 'home_data_v1'


def _edge_kv():
    """Cloudflare KV-binding når appen kører som Worker."""
    if not _IS_EDGE:
        return None
    try:
        from edgekit.runtime import current_env
        return getattr(current_env(), 'CACHE_KV', None)
    except Exception:
        return None


# Cloudflare Python Workers giver ikke vars/secrets via os.environ - de ligger
# på env-objektet. Kopiér dem ind i os.environ ved første request, så resten af
# appen (som bruger os.environ) fungerer uændret.
_edge_env_synced = False
_EDGE_ENV_VARS = (
    'SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_URL',
    'SUPABASE_KEY', 'NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY',
    'CACHE_REFRESH_SECRET', 'ENABLE_PRICE_DB',
    'GOOGLE_SHEET_WEBHOOK_URL', 'TABLE_SUFFIX',
)


@app.before_request
def _canonical_host_redirect():
    host = (request.host or '').split(':')[0].lower()
    if host == 'www.madshopper.dk':
        return redirect(request.url.replace('://www.madshopper.dk', '://madshopper.dk', 1), 301)


@app.before_request
def _sync_edge_env():
    global _edge_env_synced
    if not _IS_EDGE or _edge_env_synced:
        return
    try:
        from edgekit.runtime import current_env
        env = current_env()
    except Exception:
        return
    for name in _EDGE_ENV_VARS:
        try:
            value = getattr(env, name)
        except Exception:
            continue
        if value is not None:
            os.environ[name] = str(value)
    _edge_env_synced = True


# Sider hvor edge/browser-cache må betjene gentagne visninger, så worker'en
# spares (afgørende for kapacitet på Cloudflare free-plan). Butiksfiltrering
# ligger i ?stores= (cache-nøgle) + klient-side, så standardvisningen er sikker.
_CACHEABLE_ENDPOINTS = {
    'home', 'category', 'ugens_tilbud', 'search_page', 'search',
    'autocomplete', 'get_stores', 'get_separate_products', 'get_product_info',
    'terms_of_service', 'about', 'feedback_page',
    # Prishistorik og ernæring: data ændrer sig højst én gang i døgnet og er
    # GET uden rate-limit - edge-cache (s-maxage=600) sparer Supabase-kald
    # og gør produkt-overlay hurtigere. Dæmper samtidig misbrug.
    'get_price_history', 'get_nutrition',
}
# Endpoints hvis svar afhænger af get_active_stores() - dvs. af ?stores= ELLER
# af madshopper_stores-cookien. Query-parameteren indgår i cache-nøglen, men
# cookien gør IKKE, og der sættes intet Vary. Uden særbehandling havner et
# personligt butiksfiltreret svar derfor i den delte edge/CDN-cache og bliver
# serveret videre til andre besøgende paa samme URL (verificeret paa
# produktion 2026-07-19).
_STORE_DEPENDENT_ENDPOINTS = {
    'home', 'category', 'ugens_tilbud', 'search_page', 'search', 'autocomplete',
}
# INGEN browser-cache: browseren skal altid revalidere mod edge/CDN, så en
# deploy er synlig for alle brugere med det samme - uanset hvad den enkelte
# browser måtte have liggende lokalt. CDN/edge-cachen (s-maxage) bærer i
# stedet lasten og purges automatisk ved hver deploy (se deploy-worker.sh +
# cache_version i src/worker.py), så det koster ikke ekstra load på originen.
_EDGE_CACHE_SECONDS = 600


# Billed-CDN'er varekortene henter fra. Enumereret frem for et bredt "https:",
# saa en XSS ikke kan bruge et <img>-beacon til at sende data ud af sitet.
# Listen er verificeret mod HELE produktkataloget (35.010 billed-URL'er ->
# praecis disse hosts) og mod de renderede sider. Tilfoejer en scraper en ny
# butiks-CDN, skal den ind her - ellers vises den butiks billeder ikke
# (varekortet fungerer stadig; img-onerror skjuler det tomme billede).
_IMG_HOSTS = (
    'https://rema-product-images.digital.rema1000.dk '
    'https://digitalassets.sallinggroup.com '
    'https://dagrofa-dam.s3.eu-central-1.amazonaws.com '
    'https://image-transformer-api.tjek.com '
    'https://imgproxy-retcat.assets.schwarz '
    'https://image.prod.iposeninfra.com '
    'https://nxtumbraco.azurewebsites.net'
)

# Content-Security-Policy.
#
# script-src beholder 'unsafe-inline', fordi siden har 101 inline event-
# handlers (onclick/onchange/onerror m.fl.) i templates og i runtime-genereret
# HTML. En nonce er ikke en mulighed: svarene ligger i en DELT edge-cache, saa
# en nonce ville blive cachet og genbrugt paa tvaers af besoegende og dermed
# vaere vaerdiloes. At fjerne de 101 handlere er en reel refaktorering af
# script.js + 9 templates - den staar tilbage som naeste skridt, og foerst
# DEREFTER giver det mening at fjerne 'unsafe-inline'.
#
# Alt andet er laast. Selv med 'unsafe-inline' blokerer denne CSP det, der
# goer XSS farligt i praksis: indlaesning af fremmede scripts (script-src),
# exfiltrering af Supabase-sessionen fra localStorage (connect-src + img-src),
# kapring af relative URL'er (base-uri), afsendelse af formularer til en
# fremmed server (form-action), plugins (object-src) og clickjacking
# (frame-ancestors).
_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "form-action 'self'; "
    # cdn.jsdelivr.net: Chart.js lazy-loades derfra (loadChartJs i script.js).
    # accounts.google.com: Google Identity Services (login).
    "script-src 'self' 'unsafe-inline' https://accounts.google.com https://cdn.jsdelivr.net; "
    # accounts.google.com: GSI henter sit eget stylesheet (/gsi/style) til
    # login-knappen. Uden den her mister knappen sin styling - fanget af
    # browsertesten, ikke af header-inspektion.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://accounts.google.com; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    f"img-src 'self' data: {_IMG_HOSTS} https://accounts.google.com https://lh3.googleusercontent.com; "
    # Supabase: REST + auth (https) og realtime (wss). Intet andet maa
    # kontaktes - det er den linje der stopper tyveri af en session.
    "connect-src 'self' https://*.supabase.co wss://*.supabase.co "
    "https://accounts.google.com https://cdn.jsdelivr.net; "
    "frame-src https://accounts.google.com; "
    "manifest-src 'self'; "
    "upgrade-insecure-requests"
)

_SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'SAMEORIGIN',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'Permissions-Policy': 'geolocation=(), microphone=(), camera=(), interest-cohort=()',
    # 1 aar (var 180 dage). includeSubDomains daekker www. 'preload' er bevidst
    # udeladt: det er en svaer-reversibel tilmelding til browsernes indbyggede
    # liste og boer vaere et aktivt valg, ikke en sidegevinst ved et deploy.
    'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
    'Content-Security-Policy': _CSP,
    # same-origin-allow-popups (ikke same-origin): Google Identity Services
    # aabner sit login i et popup-vindue og skal kunne tale med sin opener.
    'Cross-Origin-Opener-Policy': 'same-origin-allow-popups',
    'X-Permitted-Cross-Domain-Policies': 'none',
}


@app.context_processor
def _inject_site_meta():
    path = request.path if request else '/'
    return {
        'site_url': SITE_URL,
        'canonical_url': f'{SITE_URL}{path}',
        # Offentlige Supabase-værdier til browser-siden (supabase-js/auth.js).
        # KUN den publishable nøgle - ALDRIG DEPLOY_KEY (service_role). Begge
        # værdier er globale/ens for alle besøgende, så de er sikre i den delte
        # edge-cache. carts_table følger TABLE_SUFFIX, så staging skriver til
        # carts_dev (client-side pendant til server-sidens _table_suffix).
        'supabase_url': (os.environ.get('NEXT_PUBLIC_SUPABASE_URL')
                         or os.environ.get('SUPABASE_URL') or ''),
        'supabase_anon_key': (os.environ.get('NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY')
                              or os.environ.get('SUPABASE_KEY') or ''),
        'carts_table': 'carts' + _table_suffix(),
    }


def _is_cookie_personalised() -> bool:
    """Sandt når butiksvalget stammer fra cookien i stedet for ?stores=.

    get_active_stores() foretrækker ?stores= og falder ellers tilbage til
    madshopper_stores-cookien. Kun i fallback-tilfældet er svaret personligt
    uden at være afspejlet i URL'en - og dermed uegnet til delt cache."""
    if request.args.get('stores') is not None:
        return False
    return bool(request.cookies.get('madshopper_stores'))


@app.after_request
def _set_response_headers(response):
    try:
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if (
            request.method == 'GET'
            and response.status_code == 200
            and request.endpoint in _CACHEABLE_ENDPOINTS
        ):
            if (
                request.endpoint in _STORE_DEPENDENT_ENDPOINTS
                and _is_cookie_personalised()
            ):
                # private: browseren maa gemme det (uaendret adfaerd der), men
                # hverken worker'ens cache.put (tjekker "public") eller
                # Cloudflares CDN maa dele det med andre besoegende.
                response.headers['Cache-Control'] = 'private, max-age=0, must-revalidate'
            else:
                response.headers['Cache-Control'] = (
                    f'public, max-age=0, must-revalidate, '
                    f's-maxage={_EDGE_CACHE_SECONDS}'
                )
    except Exception:
        pass
    return response


def _kv_get_json(key: str):
    kv = _edge_kv()
    if not kv:
        return None
    try:
        from edgekit.runtime import await_sync
        raw = await_sync(kv.get_text(key))
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning("KV get %s failed: %s", key, e)
        return None


def _kv_put_json(key: str, value) -> None:
    kv = _edge_kv()
    if not kv:
        return
    try:
        from edgekit.runtime import await_sync
        await_sync(kv.put(key, json.dumps(value, separators=(',', ':'))))
    except Exception as e:
        logger.warning("KV put %s failed: %s", key, e)


def _home_precomputed() -> dict | None:
    """Forudberegnet forside-data (Ugens Tilbud/Køl-kandidater + favorit-pulje)
    fra scripts/seed-d1.py, skrevet til KV ved hver nattens seed. Læses her i
    stedet for at ramme D1 (2x) + Supabase (2x) live pr. sidevisning - det var
    hovedbidraget til 1101/1102-nedbruddet under samtidig trafik 2026-07-19/20.
    Data ændrer sig alligevel kun ved nattens seed, så intet går tabt.
    None (fejler åbent) får home() til at falde tilbage til de gamle
    live-kald, så en manglende/gammel KV-nøgle aldrig kan bryde forsiden."""
    if not _IS_EDGE:
        return None
    return _kv_get_json(_HOME_KV_KEY)


def _edge_fetch(url: str, method: str = 'GET', headers: dict | None = None,
                body: str | None = None) -> tuple:
    """HTTP via Workers-runtime fetch (js.fetch). httpx/pyfetch virker ikke pålideligt
    i Cloudflares Pyodide-runtime - den native fetch gør. Samme await_sync-mønster som
    D1-kaldene (der virker på edge). Returnerer (parsed_json_eller_None, status)."""
    from edgekit.runtime import await_sync
    import js  # type: ignore  # runtime-only modul (Pyodide/Workers)
    from pyodide.ffi import to_js
    init: dict = {'method': method}
    if headers:
        init['headers'] = headers
    if body is not None:
        init['body'] = body
    resp = await_sync(js.fetch(url, to_js(init, dict_converter=js.Object.fromEntries)))  # type: ignore[attr-defined]
    status = int(resp.status)
    try:
        text = str(await_sync(resp.text()))
    except Exception:
        text = ''
    try:
        data = json.loads(text) if text else None
    except (TypeError, ValueError):
        data = None
    return data, status


def _edge_fetch_json(url: str, headers: dict):
    """HTTP GET via Workers-runtime fetch - kun status 200 giver data."""
    data, status = _edge_fetch(url, method='GET', headers=headers)
    return (data if status == 200 else None), status


_pending_feedback_ready = False


def _d1_run(sql: str, params: tuple = ()) -> bool:
    db = _d1()
    if not db:
        return False
    from edgekit.runtime import await_sync
    stmt = db.prepare(sql)
    if params:
        stmt = stmt.bind(*params)
    await_sync(stmt.run())
    return True


def _ensure_pending_feedback_table() -> None:
    global _pending_feedback_ready
    if _pending_feedback_ready:
        return
    _d1_run(
        "CREATE TABLE IF NOT EXISTS pending_feedback ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, feedback_type TEXT, name TEXT, "
        "email TEXT, subject TEXT, message TEXT, page_url TEXT, created_at TEXT)"
    )
    _pending_feedback_ready = True


def _queue_feedback_for_sheet(payload: dict) -> bool:
    """Feedback går kun til Google Sheet (ikke Supabase). På edge er der ingen
    ctx.waitUntil-adgang fra WSGI-laget, og et blokerende kald til den langsomme,
    eksterne Apps Script-webhook kan overskride Workers' CPU/wall-time-budget
    (set det give 503 på hele requesten). Derfor lægges rækken i D1 (hurtigt,
    internt kald - samme klasse som de øvrige D1-kald der virker på edge), og en
    periodisk GitHub Actions-relay (scripts/relay-feedback-to-sheet.py) sender
    videre til webhooken uden om Workers helt. Lokalt (ikke edge) er der ingen af
    disse begrænsninger, så vi sender direkte og synkront."""
    if _IS_EDGE:
        _ensure_pending_feedback_table()
        return _d1_run(
            "INSERT INTO pending_feedback "
            "(feedback_type, name, email, subject, message, page_url, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                payload['type'], payload['name'], payload['email'],
                payload['subject'], payload['message'], payload['page_url'],
                payload['created_at'],
            ),
        )

    webhook_url = os.environ.get('GOOGLE_SHEET_WEBHOOK_URL')
    if not webhook_url:
        return False
    try:
        import httpx
        httpx.post(
            webhook_url, headers={'Content-Type': 'application/json'},
            content=json.dumps(payload), timeout=5.0, follow_redirects=True,
        )
        return True
    except Exception as e:
        logger.error('Google Sheet-webhook fejlede: %s', e)
        return False


# ---------------------------------------------------------------------------
# D1 (SQL) dataadgang - på Cloudflare henter vi kun det datasæt en side skal
# bruge (per kategori/søgning), så en request aldrig loader hele kataloget.
# ---------------------------------------------------------------------------

def _d1():
    if not _IS_EDGE:
        return None
    try:
        from edgekit.runtime import current_env
        return getattr(current_env(), 'DB', None)
    except Exception:
        return None


def _use_d1() -> bool:
    return _d1() is not None


def _d1_rows(sql: str, params: tuple = ()):
    db = _d1()
    if not db:
        return []
    from edgekit.runtime import await_sync
    stmt = db.prepare(sql)
    if params:
        stmt = stmt.bind(*params)
    return await_sync(stmt.all())


def _d1_products(sql: str, params: tuple = ()):
    out = []
    for row in _d1_rows(sql, params):
        raw = row.get('data') if isinstance(row, dict) else None
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return out


def _d1_scalar(sql: str, params: tuple = ()):
    db = _d1()
    if not db:
        return None
    from edgekit.runtime import await_sync
    stmt = db.prepare(sql)
    if params:
        stmt = stmt.bind(*params)
    return await_sync(stmt.first())


def load_category_raw(category: str, limit: int | None = None) -> list:
    """Rå produkter i én kategori (D1 på edge, ellers in-memory index)."""
    if _use_d1():
        lim = f" LIMIT {int(limit)}" if limit else ""
        return _d1_products(
            f"SELECT data FROM products WHERE category = ?{lim}", (category,)
        )
    products = get_product_data()
    idx = _category_index if _category_index is not None else _rebuild_category_index(products)
    result = list(idx.get(category, []))
    return result[:limit] if limit else result


def load_sale_raw(limit: int | None = None) -> list:
    """Rå produkter på tilbud."""
    if _use_d1():
        lim = f" LIMIT {int(limit)}" if limit else ""
        return _d1_products(f"SELECT data FROM products WHERE is_sale = 1{lim}")
    products = get_product_data()
    result = [
        p for p in products
        if p.get('/product/sale_price') or p.get('/product/is_any_sale')
    ]
    return result[:limit] if limit else result


def _escape_like(t: str) -> str:
    return t.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def load_search_raw(query: str, limit: int = 800) -> list | None:
    """Rå produkter der matcher en søgning. None = brug in-memory index-vej.

    search_text (scripts/seed-d1.py) er bygget med normalize_name, så
    query'en normaliseres her på samme måde - ellers matcher fx "hakket
    svinekød" aldrig et Rema-kort hvis synlige/lagrede titel er "HK.
    SVINEKØD" (kun 'hk' -> 'hakket'-udvidelsen sker symmetrisk)."""
    if not _use_d1():
        return None
    norm_query = normalize_name(query)
    tokens = [t for t in norm_query.split() if len(t) >= 2]
    if not tokens:
        tokens = [norm_query.strip()]
    # Rigtige produktsøgninger er nogle få ord - et højere antal AND-led
    # giver kun en tungere D1-forespørgsel (og risikerer D1's grænse for
    # bundne parametre) uden at kunne finde noget ægte produkt.
    tokens = [_escape_like(t) for t in tokens[:8] if t]
    if not tokens:
        return []
    where = " AND ".join(["search_text LIKE ? ESCAPE '\\'"] * len(tokens))
    params = tuple(f"%{t}%" for t in tokens)
    rows = _d1_products(
        f"SELECT data FROM products WHERE {where} LIMIT {int(limit)}", params
    )
    if rows:
        return rows

    # Typo-tolerant widen: den strenge AND-substring-søgning fandt intet -
    # uden dette skridt får product_matches_query_fuzzy (rapidfuzz) aldrig
    # nogen kandidater at score, fordi `rows` allerede er tom (fx "minmælk"
    # -> "minimælk"). Løsere OR-søgning på et kort, typo-robust præfiks
    # (typoer rammer sjældent de første bogstaver) - stadig begrænset af
    # LIMIT; den præcise fuzzy-scoring sker efterfølgende i Python.
    prefixes = {t[:3] for t in tokens if len(t) >= 5}
    if not prefixes:
        return rows
    where2 = " OR ".join(["search_text LIKE ? ESCAPE '\\'"] * len(prefixes))
    params2 = tuple(f"%{p}%" for p in prefixes)
    return _d1_products(
        f"SELECT data FROM products WHERE {where2} LIMIT {int(limit)}", params2
    )


def load_product_raw(product_id: str):
    """Enkelt rå produkt via id."""
    if _use_d1():
        rows = _d1_products(
            "SELECT data FROM products WHERE id = ? LIMIT 1", (str(product_id),)
        )
        return rows[0] if rows else None
    return next(
        (p for p in get_product_data() if str(p.get('/product/id')) == str(product_id)),
        None,
    )


def load_products_by_ids(ids: list) -> list:
    """Rå produkter for en liste af id'er (D1 på edge, ellers in-memory scan)."""
    ids = [str(i) for i in ids if str(i).strip()]
    if not ids:
        return []
    if _use_d1():
        placeholders = ",".join("?" * len(ids))
        return _d1_products(
            f"SELECT data FROM products WHERE id IN ({placeholders})", tuple(ids)
        )
    id_set = set(ids)
    return [p for p in get_product_data() if str(p.get('/product/id')) in id_set]


def _popular_product_ids(limit: int = 60) -> list[str]:
    """Mest kurv-tilføjede produkt-id'er fra cart_popularity (mest populære først).

    Kræver mindst 2 klik, så et enkelt tilfældigt klik ikke definerer en 'favorit'.
    Tom liste ved fejl/for få data - forsiden falder tilbage til staple-varer."""
    if not _supabase_available():
        return []
    rows, status = _supabase_rest(
        "GET", "cart_popularity" + _table_suffix(),
        params={"select": "product_id,count", "count": "gte.2",
                "order": "count.desc", "limit": str(limit)},
    )
    if status != 200 or not isinstance(rows, list):
        return []
    return [str(r.get("product_id")) for r in rows if r.get("product_id")]


def _d1_listing(base_where: list, base_params: list, args, page: int,
                per_page: int, active_stores: set | None):
    """SQL-pagineret produktliste - henter kun én side ad gangen fra D1."""
    where = list(base_where)
    params = list(base_params)

    if active_stores is not None:
        if len(active_stores) == 0:
            return [], 0, 1
        ors = " OR ".join(["stores LIKE ?"] * len(active_stores))
        where.append(f"({ors})")
        params.extend(f"%|{s}|%" for s in active_stores)

    sub = args.get('subcategory', type=str) or ''
    if sub:
        where.append("subcategory = ?")
        params.append(sub)

    if args.get('sale', type=str) == 'true':
        where.append("is_sale = 1")

    min_price = args.get('min_price', type=float)
    max_price = args.get('max_price', type=float)
    if min_price is not None:
        where.append("eff_price >= ?")
        params.append(min_price)
    if max_price is not None:
        where.append("eff_price <= ?")
        params.append(max_price)

    # Øko/laktose/vægt afgøres i SQL, så COUNT og paginering tæller de samme
    # produkter som siden viser (kolonnerne sættes af scripts/seed-d1.py).
    if args.get('organic', type=str) == 'true':
        where.append("organic = 1")
    if args.get('lactose', type=str) == 'true':
        where.append("lactose = 1")

    min_weight = args.get('min_weight', type=float)
    max_weight = args.get('max_weight', type=float)
    if min_weight is not None and min_weight > 0:
        where.append("weight_g >= ?")
        params.append(min_weight)
    if max_weight is not None:
        # Ukendt vægt beholdes - samme semantik som apply_product_filters
        where.append("(weight_g IS NULL OR weight_g <= ?)")
        params.append(max_weight)

    where_sql = " AND ".join(where)

    sort_type = args.get('sort', 'relevance')
    order = ""
    if sort_type == 'price-asc':
        order = " ORDER BY eff_price ASC"
    elif sort_type == 'price-desc':
        order = " ORDER BY eff_price DESC"
    elif sort_type == 'name-asc':
        order = " ORDER BY title ASC"
    elif sort_type == 'kg-price-asc':
        # Uden denne gren fik kg-pris ingen ORDER BY, saa SQL returnerede en
        # vilkaarlig side, som Python bagefter kun sorterede internt - side 2
        # kunne indeholde billigere kg-priser end side 1 (maalt paa produktion).
        # price_per_kg findes kun inde i JSON-blobben, men eff_price/weight_g
        # giver samme rangordning ud fra kolonner der allerede er seedet.
        # Varer uden kendt vaegt sidst, som i Python-sorteringens 999999-fallback.
        order = (
            " ORDER BY CASE WHEN weight_g IS NULL OR weight_g <= 0 THEN 1 ELSE 0 END ASC,"
            " eff_price / weight_g ASC"
        )

    row = _d1_scalar(
        f"SELECT COUNT(*) AS c FROM products WHERE {where_sql}", tuple(params)
    )
    total = int((row or {}).get('c', 0)) if isinstance(row, dict) else 0
    total_pages = (total + per_page - 1) // per_page
    page = min(max(page, 1), total_pages) if total_pages > 0 else 1
    offset = (page - 1) * per_page

    products = _d1_products(
        f"SELECT data FROM products WHERE {where_sql}{order} LIMIT {per_page} OFFSET {offset}",
        tuple(params),
    )
    return products, total_pages, page


def _d1_subcategories(category: str) -> set:
    rows = _d1_rows(
        "SELECT DISTINCT subcategory FROM products WHERE category = ?", (category,)
    )
    return {r.get('subcategory', '') for r in rows if isinstance(r, dict)}


def _apply_cache_payload(products, search_index, ts=None):
    global cached_data, _category_index
    ts = ts or datetime.now()
    cached_data = {
        'timestamp': ts,
        'data': products,
        'search_index': search_index or {},
    }
    _category_index = _rebuild_category_index(products)


def _rebuild_category_index(products: list) -> dict[str, list]:
    idx: dict[str, list] = {}
    for product in products:
        ptype = product.get('/product/product_type')
        if ptype:
            key = str(ptype)
            idx.setdefault(key, []).append(product)
    return idx

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
        if ids:
            results = []
            for p in products:
                if str(p.get('/product/id', '')) not in ids:
                    continue
                if not p.get('/product/title') or not p.get('/product/id'):
                    continue
                d = _to_display(p)
                if d:
                    results.append(d)
            if results:
                return results
    results = []
    for product in products:
        if not product.get('/product/title') or not product.get('/product/id'):
            continue
        d = _to_display(product)
        if d and product_matches_query(d, query):
            results.append(d)
    if results:
        return results
    # Typo-tolerant fallback - kun når streng søgning ikke gav nogen hits
    for product in products:
        if not product.get('/product/title') or not product.get('/product/id'):
            continue
        d = _to_display(product)
        if d and product_matches_query_fuzzy(d, query):
            results.append(d)
    return results


def search_display_products(query: str, active_stores: set | None,
                            limit: int = 800) -> list:
    """Søgeresultater som display-dicts (D1-kandidater på edge, ellers index).

    `limit` begrænser hvor mange rå kandidater der hentes/parses fra D1.
    Autocomplete bruger en lille pulje for at holde sig under free-planens
    CPU-grænse; søgeresultatsiden bruger den fulde pulje.
    """
    query = (query or '')[:60]  # beskyt mod urimeligt lange søgestrenge
    raw = load_search_raw(query, limit=limit)
    if raw is None:
        filtered = filter_products_by_stores(get_product_data(), active_stores)
        return _filter_products_for_search(filtered, query, active_stores)
    displayed = []
    for p in filter_products_by_stores(raw, active_stores):
        if not p.get('/product/title') or not p.get('/product/id'):
            continue
        adjusted = product_for_active_stores(p, active_stores)
        if not adjusted:
            continue
        d = product_to_display_dict(adjusted, default_category='Andre varer')
        if d:
            displayed.append(d)

    results = [d for d in displayed if product_matches_query(d, query)]
    if results:
        return results
    # Typo-tolerant fallback - kun når streng søgning ikke gav nogen hits (fx "minmælk")
    return [d for d in displayed if product_matches_query_fuzzy(d, query)]


def _supabase_rest_config():
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL") or ""
    key = (os.environ.get("DEPLOY_KEY") or
           os.environ.get("SUPABASE_KEY") or
           os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") or "")
    return url.rstrip("/"), key


def _table_suffix() -> str:
    """Suffiks på skrive-tabellerne (cart_popularity, price_alerts) og deres RPC'er.

    Staging-workeren og lokal kørsel bruger *_dev-kopierne, så test ikke
    forurener produktionens data (kør scripts/supabase-dev-tables.sql én gang).
    Deployede workers sætter TABLE_SUFFIX eksplicit via scripts/build-pages.sh;
    er varen fraværende (fx ældre deploy eller lokal kørsel uden .env-valg)
    falder edge tilbage til produktion og lokalt til _dev."""
    suffix = os.environ.get("TABLE_SUFFIX")
    if suffix is None:
        return "" if os.environ.get("CLOUDFLARE_WORKERS") else "_dev"
    return suffix


def _supabase_available() -> bool:
    """Sandt når vi har URL + nøgle til Supabase - virker både lokalt og på edge."""
    base, key = _supabase_rest_config()
    return bool(base and key)


def _supabase_rest(method: str, path: str, params: dict | None = None,
                   json_body=None, prefer: str | None = None, timeout: float = 15.0) -> tuple:
    """Kald Supabase PostgREST direkte - ÉN kodesti på edge (js.fetch) og lokalt (httpx).
    Erstatter supabase-py-klienten, som ikke kan køre i Cloudflares Pyodide-runtime, så
    interaktive features (feedback, prisalarm, kurv, prishistorik) også virker offentligt.
    Returnerer (data, status). status == 0 betyder netværks-/opsætningsfejl."""
    base, key = _supabase_rest_config()
    if not base or not key:
        return None, 0
    url = f"{base}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer
    body = json.dumps(json_body) if json_body is not None else None
    try:
        if _IS_EDGE:
            return _edge_fetch(url, method=method, headers=headers, body=body)
        import httpx
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(method, url, headers=headers, content=body)
            try:
                data = resp.json() if resp.content else None
            except Exception:
                data = None
            return data, resp.status_code
    except Exception as e:
        logger.warning("Supabase REST %s %s fejlede: %s", method, path, e)
        return None, 0


def _should_refresh_product_cache(now=None):
    """Hent nye data én gang pr. dag - butikskataloger ændrer sig ikke i løbet af dagen."""
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
    """Load pre-computed product data and search index (KV → Supabase → lokal fil)."""
    global cached_data

    kv_payload = _kv_get_json(_KV_CACHE_KEY)
    if isinstance(kv_payload, dict):
        products = kv_payload.get('products') or []
        search_index = kv_payload.get('search_index') or {}
        if products or search_index:
            ts_raw = kv_payload.get('timestamp')
            try:
                ts = datetime.fromisoformat(ts_raw) if ts_raw else datetime.now()
            except (TypeError, ValueError):
                ts = datetime.now()
            _apply_cache_payload(products, search_index, ts)
            logger.info("Product cache loaded from KV (%d produkter)", len(products))
            return

    try:
        base_url, supabase_key = _supabase_rest_config()
        if not base_url or not supabase_key:
            logger.error("Supabase URL eller key mangler - kan ikke hente app_cache")
            return
        headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
        url = f"{base_url}/rest/v1/app_cache?select=*&id=gte.0&order=id.asc"

        if _IS_EDGE:
            rows, status = _edge_fetch_json(url, headers)
        else:
            import httpx
            with httpx.Client(timeout=30.0) as client:
                res = client.get(url, headers=headers)
                status = res.status_code
                rows = res.json() if status == 200 else None

        if status == 200 and rows:
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
                now = datetime.now()
                _apply_cache_payload(_c_data, _c_idx, now)
                _kv_put_json(_KV_CACHE_KEY, {
                    'timestamp': now.isoformat(),
                    'products': _c_data,
                    'search_index': _c_idx,
                })
                logger.info(
                    "Product cache refreshed from Supabase app_cache (%d produkter i %d chunks)",
                    len(_c_data), len(rows) - 1,
                )
                return
            logger.warning("app_cache var tom")
        else:
            logger.warning(
                "Supabase app_cache utilgængelig (status %s) - prøver lokal cache",
                status,
            )
    except Exception as e:
        logger.error("Error loading app_cache: %s", e)

    local = _load_local_cache()
    if local:
        products, search_index = local
        _apply_cache_payload(products, search_index)


def _start_background_cache_refresh():
    """Refresh cache once per day when the calendar date changes."""
    if _IS_EDGE:
        return
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

    saved_version = 0
    try:
        saved_version = int(request.cookies.get('madshopper_store_version') or 0)
    except (TypeError, ValueError):
        saved_version = 0

    labels = None
    stores_cookie = request.cookies.get('madshopper_stores')
    if stores_cookie:
        try:
            unquoted = urllib.parse.unquote(stores_cookie)
            stores_list = json.loads(unquoted)
            if isinstance(stores_list, list) and len(stores_list) > 0:
                labels = {str(s).strip() for s in stores_list if str(s).strip()}
        except Exception:
            pass

    if labels and saved_version < STORE_CATALOG_VERSION:
        for label in stores_auto_enable_since(saved_version):
            labels.add(label)

    return labels

_TOBACCO_IMG_RE = re.compile(r'rema-product-images\.digital\.rema1000\.dk/(\d+)/')

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
        # Ordgrænse-match (is_non_food_name) - substring ramte fødevarer som
        # "hyldeblomst", "bindsalat" og "plantedrik".
        if is_non_food_name(str(p.get('/product/title', ''))):
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
        
        # Øko/laktose-tjek - samme heuristikker som updater'ens matching (app_support)
        if organic_only and not is_organic(
            p.get('name', ''), p.get('description', ''), p.get('brand', ''),
        ):
            continue
        if lactose_only and not is_lactose_free(
            p.get('name', ''), p.get('description', ''), p.get('brand', ''),
        ):
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
# Alle Supabase-kald i app.py går via _supabase_rest() (httpx/REST) -
# supabase-py-klienten bruges ikke her. db_available-flaget sættes af
# app_support.set_db_available() baseret på env-variabler ved opstart.

def init_db():
    """Sæt db_available-flaget baseret på tilgængelige env-variabler.

    Bemærk: opretter IKKE længere en supabase-py-klient - alle kald
    i app.py bruger _supabase_rest() (httpx direkte mod REST-API).
    Funktionen beholdes for kompatibilitet med __main__-blokken.
    """
    if _IS_EDGE:
        set_db_available(False)
        return
    if not is_price_db_enabled():
        set_db_available(False)
        logger.info("Price database disabled (ENABLE_PRICE_DB=0)")
        return
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_KEY")
    if key and (key.startswith("http://") or key.startswith("https://")):
        key = os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
    if not url or not key:
        set_db_available(False)
        logger.warning("Supabase URL or Key not set. App runs without database.")
        return
    set_db_available(True)
    logger.info("Supabase konfiguration OK (REST-lag aktiv).")

# Loft på hvor mange produkter ét cart-event må tælle op. Kurven er brugerens
# egen, så en reel kurv rammer aldrig loftet - det er der for at en forfalsket
# request ikke kan puste vilkårligt mange produkter op i Brugernes Favoritter.
_CART_EVENT_MAX_IDS = 50
# Fallback-stien laver ét Supabase-kald pr. produkt. Workers' gratis-plan giver
# 50 subrequests pr. invocation, så vi stopper i god tid under loftet.
_CART_EVENT_FALLBACK_MAX = 25
# Loft pr. vare, så en manipuleret kurv ikke kan sende qty=999999
_CART_EVENT_MAX_QTY = 99
# Bemærk: record_cart_activity håndhæver de samme tre lofter selv, fordi RPC'en
# også kan nås direkte via PostgREST. Caps her sparer båndbredde og holder
# fejlsvaret pænt; SQL'en er det egentlige forsvar.
#
# Vægtningen i cart_popularity (kurv-tilføjelse = 1, prissammenligning = 3)
# udledes af signaltypen inde i RPC'en - se scripts/supabase-cart-increment.sql.
# Den må ikke sendes fra klienten, som ellers selv kunne vælge sin vægt.


def _parse_cart_items(data: dict) -> tuple[list[dict], str]:
    """Normaliser payload til ([{'pid': str, 'qty': int}], event_type).

    Accepterer den nye form ({'event', 'items'}) og de to ældre former
    ({'product_id'} / {'product_ids'}), så JS-filer der stadig ligger i en
    browser-cache fra før v22 fortsætter med at tælle korrekt."""
    raw_items = data.get('items')
    if isinstance(raw_items, list):
        event_type = 'compare' if data.get('event') == 'compare' else 'add'
        source = raw_items
    elif isinstance(data.get('product_ids'), list):
        event_type = 'compare'          # kun sammenligning sendte lister før v22
        source = data['product_ids']
    else:
        event_type = 'add'
        source = [data.get('product_id', '')]

    items: list[dict] = []
    seen: set[str] = set()
    for raw in source[:_CART_EVENT_MAX_IDS]:
        if isinstance(raw, dict):
            pid = str(raw.get('id', '')).strip()[:64]
            try:
                qty = int(raw.get('qty', 1))
            except (TypeError, ValueError):
                qty = 1
        else:
            pid, qty = str(raw).strip()[:64], 1
        if not pid or pid in seen:
            continue
        seen.add(pid)
        items.append({'pid': pid, 'qty': max(1, min(qty, _CART_EVENT_MAX_QTY))})
    return items, event_type


@app.route('/api/cart-event', methods=['POST'])
@rate_limit(cart_event_limiter)
def cart_event():
    try:
        data = request.get_json(force=True)
        items, event_type = _parse_cart_items(data)
        if not items:
            return jsonify({'ok': False}), 400
        if not _supabase_available():
            return jsonify({'ok': True, 'persisted': False})

        product_ids = [it['pid'] for it in items]

        # Ét kald skriver både den vægtede popularitet og time-aggregatet.
        # Hvert Supabase-kald fra edge er en subrequest (50 pr. invocation på
        # gratis-planen), så to kald ville doble forbruget uden at give mere.
        # Vægten sendes IKKE med - den udledes af etype inde i RPC'en, så den
        # ikke kan sættes af en klient der kalder PostgREST uden om appen.
        _, st = _supabase_rest(
            "POST", "rpc/record_cart_activity" + _table_suffix(),
            json_body={"items": items, "etype": event_type},
            prefer="return=minimal",
        )
        if st in (200, 201, 204):
            return jsonify({'ok': True, 'persisted': True})

        # Herunder: fallbacks for et Supabase der endnu ikke har fået
        # scripts/supabase-cart-increment.sql kørt. De taber vægtning og
        # tidsdata, men holder rangeringen i gang frem for at fejle requesten.
        #
        # Alle fallbacks gaar gennem RPC'er. Den tidligere sidste udvej -
        # laes-saa-skriv direkte mod cart_popularity - er fjernet: den
        # offentlige noegle har ikke laengere INSERT/UPDATE paa tabellen
        # (scripts/supabase-hardening.sql), fordi den adgang tillod enhver at
        # saette taellerne frit udenom app'ens validering og rate limiting.
        if len(product_ids) > 1:
            _, st = _supabase_rest(
                "POST", "rpc/increment_cart_counts" + _table_suffix(),
                json_body={"pids": product_ids}, prefer="return=minimal",
            )
            if st in (200, 201, 204):
                return jsonify({'ok': True, 'persisted': True})
            # Sidste udvej: tæl enkeltvis, men kun så mange at subrequest-
            # loftet holder. At tabe en hale er bedre end at fejle requesten.
            ok = False
            for pid in product_ids[:_CART_EVENT_FALLBACK_MAX]:
                _, st_one = _supabase_rest(
                    "POST", "rpc/increment_cart_count" + _table_suffix(),
                    json_body={"pid": pid}, prefer="return=minimal",
                )
                ok = ok or st_one in (200, 201, 204)
            return jsonify({'ok': True, 'persisted': ok})

        # Atomisk tæller-increment via Postgres-funktion, så to samtidige klik
        # ikke taber det ene (kør scripts/supabase-cart-increment.sql én gang).
        _, st = _supabase_rest(
            "POST", "rpc/increment_cart_count" + _table_suffix(),
            json_body={"pid": product_ids[0]}, prefer="return=minimal",
        )
        return jsonify({'ok': True, 'persisted': st in (200, 201, 204)})
    except Exception as e:
        logger.error("cart-event error: %s", e)
        return jsonify({'ok': False}), 500

@app.route('/api/price-history/<product_id>')
def get_price_history(product_id):
    if not _supabase_available():
        return jsonify(success=True, history=[], history_by_store={})
    try:
        pid = str(product_id)[:64]
        cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        rows, status = _supabase_rest(
            "GET", "price_history",
            params={"select": "store,price,date", "product_id": f"eq.{pid}",
                    "date": f"gte.{cutoff}",
                    "order": "store.asc,date.asc"},
        )
        if status != 200 or not isinstance(rows, list):
            return jsonify(success=True, history=[], history_by_store={})

        by_store = {}
        for row in rows:
            store = row.get("store")
            by_store.setdefault(store, []).append(
                {'price': row.get("price"), 'date': row.get("date")}
            )

        flat = by_store.get('rema') or next(iter(by_store.values()), [])
        return jsonify(success=True, history=flat, history_by_store=by_store)
    except Exception as e:
        logger.error("price-history error: %s", e)
        return jsonify(success=False, error='Kunne ikke hente prishistorik.')

@app.route('/api/nutrition/<product_id>')
def get_nutrition(product_id):
    if not _supabase_available():
        return jsonify(success=True, nutrition=None)
    try:
        product = load_product_raw(str(product_id)[:64])
        if not product:
            return jsonify(success=True, nutrition=None)
        keys = nutrition_candidate_keys(product)
        if not keys:
            return jsonify(success=True, nutrition=None)

        rows, status = _supabase_rest(
            "GET", "nutrition_data",
            params={"select": "key,payload", "key": f"in.({','.join(keys)})"},
        )
        if status != 200 or not isinstance(rows, list) or not rows:
            return jsonify(success=True, nutrition=None)

        by_key = {row.get("key"): row.get("payload") for row in rows}
        for key in keys:  # respektér prioriteret rækkefølge (Rema-anker først)
            if by_key.get(key):
                return jsonify(success=True, nutrition=by_key[key])
        return jsonify(success=True, nutrition=None)
    except Exception as e:
        logger.error("nutrition error: %s", e)
        return jsonify(success=False, nutrition=None)

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

        if not _supabase_available():
            return jsonify(success=True, persisted=False)

        # Gaar gennem create_price_alert (SECURITY DEFINER), IKKE direkte mod
        # tabellen: den offentlige noegle har ikke laengere INSERT paa
        # price_alerts, jf. scripts/supabase-hardening.sql. RPC'en gentager
        # valideringen ovenfor i SQL, saa graenserne ogsaa gaelder for kald der
        # rammer PostgREST udenom denne rute.
        # Ingen return=minimal her: RPC'en returnerer en boolean, der siger om
        # raekken faktisk blev skrevet. Med return=minimal ville vi faa 204
        # uanset hvad og dermed rapportere persisted=true selv naar RPC'ens
        # egen validering afviste kaldet.
        body, st = _supabase_rest(
            "POST", "rpc/create_price_alert" + _table_suffix(),
            json_body={"pid": p_id, "pname": p_name,
                       "target": target, "current": current},
        )
        if st not in (200, 201, 204):
            logger.warning(
                "Prisalarm ikke gemt (status %s) - koer scripts/supabase-hardening.sql", st
            )
            return jsonify(success=True, persisted=False)
        return jsonify(success=True, persisted=body is not False)
    except Exception as e:
        logger.error("create-alert error: %s", e)
        return jsonify(success=False, error='Kunne ikke oprette alarm.')

init_db()

_STAPLES = {
    'mælk', 'brød', 'æg', 'smør', 'yoghurt', 'ost', 'juice',
    'havregryn', 'pasta', 'ris', 'rugbrød', 'fløde', 'kefir',
    'skyr', 'tomat', 'kartofler', 'løg', 'gulerødder', 'kylling',
    'hakket', 'leverpostej', 'syltetøj', 'marmelade', 'kaffe',
    'te', 'vand', 'cola', 'spaghetti', 'mel', 'sukker', 'salt',
}


@app.route('/index.html')
def home_index_html_redirect():
    return redirect('/', code=301)


@app.route('/')
def home():
    active_stores = get_active_stores()

    def _adjust_for_stores(products):
        # Promover kort til den aktive butiks pris/visning (samme som D1-stien),
        # så fx Rema-prisen ikke vises når Rema er fravalgt.
        out = []
        for p in products:
            adjusted = product_for_active_stores(p, active_stores)
            if adjusted:
                out.append(adjusted)
        return out

    # Hent kun de datasæt forsiden viser - ikke hele kataloget. Forudberegnet
    # KV-data foretrækkes på edge (se _home_precomputed) for at undgå D1-kald
    # pr. samtidig sidevisning; falder tilbage til live-kald hvis KV mangler.
    precomputed = _home_precomputed()
    if precomputed:
        sale_raw = _adjust_for_stores(
            filter_products_by_stores(precomputed.get('sale_raw') or [], active_stores))
        mejeri_raw = _adjust_for_stores(
            filter_products_by_stores(precomputed.get('mejeri_raw') or [], active_stores))
    else:
        sale_raw = _adjust_for_stores(
            filter_products_by_stores(load_sale_raw(limit=200), active_stores))
        mejeri_raw = _adjust_for_stores(
            filter_products_by_stores(load_category_raw(CAT_MEJERI, limit=200), active_stores))
    if not _IS_EDGE:
        random.shuffle(sale_raw)
        random.shuffle(mejeri_raw)

    products_by_category = {
        'Ugens Tilbud': [],
        'Brugernes Favoritter': [],
        CAT_MEJERI: [],
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

    # Ugens Tilbud
    seen_tilbud_imgs = set()
    for product in sale_raw:
        if len(products_by_category['Ugens Tilbud']) >= 60:
            break
        _img = str(product.get('/product/imageLink', '')).strip()
        _img_valid = _img and _img not in ('nan', 'None') and _img not in _PLACEHOLDER_IMGS
        if _img_valid and _img in seen_tilbud_imgs:
            continue
        if _img_valid:
            seen_tilbud_imgs.add(_img)
        products_by_category['Ugens Tilbud'].append(
            product_to_display_dict(
                product,
                category=product.get('/product/product_type') or CAT_MEJERI,
                sale_end_date=parse_sale_end_date(product),
            )
        )

    # Mejeri
    seen_cat_imgs = set()
    for product in mejeri_raw:
        if len(products_by_category[CAT_MEJERI]) >= 60:
            break
        try:
            if float(product.get('/product/price', 0)) <= 0:
                continue
        except (ValueError, TypeError):
            continue
        _img = str(product.get('/product/imageLink', '')).strip()
        _img_valid = _img and _img not in ('nan', 'None') and _img not in _PLACEHOLDER_IMGS
        if _img_valid and _img in seen_cat_imgs:
            continue
        if _img_valid:
            seen_cat_imgs.add(_img)
        products_by_category[CAT_MEJERI].append(
            product_to_display_dict(product, category=CAT_MEJERI)
        )

    # Brugernes Favoritter - kurv-klik-data fra cart_popularity (mest populære
    # først). På edge kommer puljen fra samme KV-forudberegning som ovenfor
    # (opdateres ved nattens seed) i stedet for et Supabase+D1-kald pr. request.
    # Falder tilbage til staple-varer, når der endnu ikke er nok data.
    if precomputed:
        pop_ids = precomputed.get('pop_ids') or []
        fav_pool = precomputed.get('fav_pool') or []
    else:
        pop_ids = _popular_product_ids(limit=60)
        fav_pool = load_products_by_ids(pop_ids) if pop_ids else []
    if pop_ids:
        by_id = {
            str(p.get('/product/id', '')): p
            for p in _adjust_for_stores(filter_products_by_stores(fav_pool, active_stores))
        }
        for pid in pop_ids:
            if len(products_by_category['Brugernes Favoritter']) >= 20:
                break
            if pid in by_id:
                _try_add_fav(by_id[pid])

    if len(products_by_category['Brugernes Favoritter']) < 20:
        staple_scored = []
        for product in (mejeri_raw + sale_raw):
            score = _staple_score(str(product.get('/product/title', '')))
            if score > 0:
                staple_scored.append((score, product))
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

    # Prices are recorded centrally in get_product_data() - no duplicate call here

    return render_template(
        'index.html',
        categories=trimmed_categories,
        template_mapping=template_mapping,
    )

@app.route('/robots.txt')
def robots_txt():
    host = (request.host or '').split(':')[0].lower()
    if host.endswith('.workers.dev'):
        body = 'User-agent: *\nDisallow: /\n'
    else:
        body = f'User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n'
    return Response(body, mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    paths = ['/', '/ugens_tilbud', *(
        f'/{slug}' for slug in _PUBLIC_CATEGORY_PATHS
    ), '/about', '/feedback', '/terms-of-service']
    urls = '\n'.join(
        f'  <url><loc>{SITE_URL}{path}</loc></url>' for path in paths
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'{urls}\n'
        '</urlset>\n'
    )
    return Response(body, mimetype='application/xml')


@app.route('/.well-known/security.txt')
@app.route('/security.txt')
def security_txt():
    """RFC 9116. Giver en sikkerhedsforsker et sted at sende et fund hen frem
    for at gaette - eller offentliggoere det. Expires er paakraevet af standarden;
    den rulles et aar frem, saa filen aldrig staar som udloebet."""
    expires = (datetime.now() + timedelta(days=365)).strftime('%Y-%m-%dT%H:%M:%SZ')
    body = (
        f'Contact: mailto:kontakt@madshopper.dk\n'
        f'Expires: {expires}\n'
        f'Preferred-Languages: da, en\n'
        f'Canonical: {SITE_URL}/.well-known/security.txt\n'
    )
    return Response(body, mimetype='text/plain; charset=utf-8')


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

    created_at = datetime.now().isoformat(timespec='seconds')

    # Feedback gemmes udelukkende i Google Sheet - ingen Supabase/DB-kopi.
    persisted = _queue_feedback_for_sheet({
        "type": feedback_type,
        "name": name or "",
        "email": email or "",
        "subject": subject or "",
        "message": message,
        "page_url": page_url or "",
        "created_at": created_at,
    })
    if not persisted:
        logger.error("Feedback kunne ikke lægges i kø til Google Sheet (type=%s)", feedback_type)

    return jsonify(success=True, persisted=persisted)


@app.route('/sale.html')
def sale_html_redirect():
    return redirect(url_for('ugens_tilbud'), 301)

@app.route('/ugens_tilbud')
def ugens_tilbud():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 60  # 6x10 layout
        total_pages = 1
        
        active_stores = get_active_stores()

        if _use_d1():
            raw_page, total_pages, page = _d1_listing(
                ["is_sale = 1"], [], request.args, page, per_page, active_stores,
            )
            source = filter_products_by_stores(raw_page, active_stores)
        else:
            source = filter_products_by_stores(load_sale_raw(), active_stores)

        sale_products = []
        for product in source:
            if product.get('/product/sale_price') or product.get('/product/is_any_sale'):
                try:
                    adjusted = product_for_active_stores(product, active_stores) if _use_d1() else product
                    if not adjusted:
                        continue
                    sale_products.append(
                        product_to_display_dict(
                            adjusted,
                            default_category='Andre varer',
                            sale_end_date=parse_sale_end_date(adjusted),
                            force_sale=bool(adjusted.get('/product/sale_price')),
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

        if not _use_d1():
            # Calculate pagination (in-memory path)
            total_products = len(sale_products)
            total_pages = (total_products + per_page - 1) // per_page
            page = min(max(page, 1), total_pages) if total_pages > 0 else 1
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            sale_products = sale_products[start_idx:end_idx]
        paginated_products = sale_products

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
@rate_limit(api_limiter)
def autocomplete():
    """Returns up to 8 slim product suggestions for the search autocomplete dropdown."""
    query = _clean_search_query(request.args.get('q', ''))
    if len(query) < 2:
        return jsonify({'suggestions': []})

    try:
        active_stores = get_active_stores()
        # Lille kandidatpulje: autocomplete viser kun 8 forslag, så vi undgår
        # at parse hundredvis af JSON-blobs (holder os under 10 ms CPU).
        matched = search_display_products(query, active_stores, limit=60)
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
@rate_limit(api_limiter)
def search():
    """API endpoint for search suggestions as user types"""
    query = _clean_search_query(request.args.get('q', ''))

    if not query:
        return jsonify(html='<div class="no-results">Indtast søgeord</div>')
    
    try:
        active_stores = get_active_stores()
        all_products = search_display_products(query, active_stores)

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
        query = _clean_search_query(request.args.get('q', ''))
        per_page = 60  # 6x10 layout

        if not query:
            return redirect(url_for('home'))

        # Samme in-memory limiter som resten af /api/*, men rendret som en
        # normal søgeside (ikke JSON) - denne route rammes af almindelig
        # sidenavigation, hvor et rå JSON-svar ville se ødelagt ud.
        if not api_limiter.allow(f'{_client_ip()}:search_page'):
            return render_template('search_results.html', query=query,
                                    products=[], total_products=0,
                                    current_page=1, total_pages=1,
                                    error="For mange forespørgsler. Prøv igen om lidt."), 429

        active_stores = get_active_stores()
        all_products = search_display_products(query, active_stores)

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

# Kun ét sikkert sti-segment (bogstaver/tal/_/-) må redirectes videre, så en
# sti som "\evil.com" ikke kan blive til en protokol-relativ open redirect.
_SAFE_SEGMENT_RE = re.compile(r'^[\w-]+$')


@app.route('/<category_name>.html')
def category_html_redirect(category_name):
    if not _SAFE_SEGMENT_RE.match(category_name):
        return "Category not found", 404
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
            
        active_stores = get_active_stores()

        if _use_d1():
            # Edge: hent kun én side fra D1 (aldrig hele kategorien).
            raw_page, total_pages, page = _d1_listing(
                ["category = ?"], [actual_category],
                request.args, page, per_page, active_stores,
            )
            paginated_products = []
            for product in filter_products_by_stores(raw_page, active_stores):
                adjusted = product_for_active_stores(product, active_stores)
                if not adjusted:
                    continue
                try:
                    paginated_products.append(
                        product_to_display_dict(adjusted, category=actual_category)
                    )
                except Exception as e:
                    logger.warning("Error processing product in category: %s", e)
            paginated_products = apply_product_filters(paginated_products, request.args)

            present_subs = _d1_subcategories(actual_category)
            rules = _SUBCATEGORY_RULES.get(actual_category, [])
            available_subcategories = [sub for sub, _ in rules if sub in present_subs]
            if 'Øvrige' in present_subs:
                available_subcategories.append('Øvrige')
            current_subcategory = request.args.get('subcategory', '')

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

        raw_category = filter_products_by_stores(
            load_category_raw(actual_category), active_stores,
        )

        category_products = []
        for product in raw_category:
            # Samme promovering som D1-stien: vis den aktive butiks pris,
            # ikke Rema-prisen, når Rema er fravalgt.
            adjusted = product_for_active_stores(product, active_stores)
            if not adjusted:
                continue
            try:
                category_products.append(
                    product_to_display_dict(adjusted, category=actual_category)
                )
            except Exception as e:
                logger.warning("Error processing product in category: %s", e)
                continue

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
    # Fallback hvis en fil ikke serveres af CDN-assets. Sæt lang cache, så
    # worker'en ikke rammes igen for samme fil (filerne har ?v= cache-busting).
    resp = send_from_directory(os.path.join(_APP_ROOT, 'static'), filename)
    max_age = 31536000 if filename.startswith('images/') else 86400
    resp.headers['Cache-Control'] = f'public, max-age={max_age}, immutable'
    if filename.endswith('.css'):
        resp.headers['Content-Type'] = 'text/css; charset=utf-8'
    elif filename.endswith('.js'):
        resp.headers['Content-Type'] = 'application/javascript; charset=utf-8'
    return resp

@app.route('/product/<product_id>')
def get_product_info(product_id):
    """Get product information and print debug info"""
    try:
        product = load_product_raw(product_id)

        if product:
            logger.debug("Product info requested for %s: %s", product_id, product.get('/product/title'))
            
            return jsonify({
                'success': True,
                'product': {
                    # .get som resten af filen - direkte indeksering gav KeyError
                    # (og dermed 500) paa kort uden pris.
                    'rema_price': product.get('/product/price'),
                    'bilka_price': product.get('/product/store_matches', {}).get('bilka', {}).get('price')
                }
            })
        else:
            logger.info(f"Product not found with ID: {product_id}")
            return jsonify(success=False, error="Product not found"), 404

    except Exception as e:
        logger.error(f"Error getting product info: {str(e)}")
        return jsonify(success=False, error="Kunne ikke hente produktinfo."), 500

@app.route('/api/stores')
def get_stores():
    stores = [{'key': k, 'label': v['label'], 'logo': v['logo']} for k, v in _STORE_CONFIGS.items()]
    return jsonify({
        'stores': stores,
        'version': STORE_CATALOG_VERSION,
        'stores_added': STORES_ADDED_IN_VERSION,
    })


@app.route('/api/products', methods=['GET'])
def get_separate_products():
    """Returns slim price data from the existing cache for cart store comparison."""
    try:
        # Alle kort med en Rema-pris - også kort promoveret til en anden butiks
        # visning (før: kun store == 'Rema 1000', så promoverede kort manglede).
        if _use_d1():
            products = _d1_products(
                "SELECT data FROM products WHERE stores LIKE '%|Rema 1000|%'"
            )
        else:
            products = get_product_data()
        rema = []
        for p in products:
            try:
                rema_price = float(p.get('/product/rema_price') or 0)
            except (TypeError, ValueError):
                continue
            if rema_price <= 0:
                continue
            rema.append({
                '/product/id': p.get('/product/id', ''),
                # JS'en læser price/sale_price som REMA-prisen. For promoverede
                # kort er '/product/price' den anden butiks pris, så vi sender
                # altid den effektive Rema-pris (tilbud indregnet) eksplicit.
                '/product/price': rema_price,
                '/product/sale_price': None,
                '/product/store_matches': {
                    k: {'price': v.get('price')}
                    for k, v in (p.get('/product/store_matches') or {}).items()
                },
            })
        return jsonify({'success': True, 'rema_products': rema, 'bilka_products': []})
    except Exception as e:
        # Selve undtagelsesteksten hoerer hjemme i loggen, ikke i svaret til klienten.
        logger.error("api/products error: %s", e)
        return jsonify({'success': False, 'error': 'Kunne ikke hente produktdata.'})

@app.route('/api/alternatives', methods=['POST'])
@rate_limit(api_limiter)
def find_alternatives():
    try:
        data = request.json or {}
        missing_items = data.get('missing_items', [])
        if not isinstance(missing_items, list):
            missing_items = []
        # Beskyt mod misbrug: hver vare udløser en kategori-scan, så begræns antal.
        missing_items = missing_items[:100]
        if not missing_items:
            return jsonify({'success': True, 'alternatives': []})

        alternatives = []
        for req_item in missing_items:
            cart_id = req_item.get('cart_id')
            store_label = req_item.get('store')
            category = req_item.get('category')
            name = req_item.get('name', '')
            weight_str = req_item.get('weight_str', '')
            weight_g = parse_weight_to_grams(weight_str) if weight_str else None

            # Kandidater begrænses til varens kategori, så vi ikke scanner alt.
            if category:
                product_pool = load_category_raw(category)
            elif not _use_d1():
                product_pool = get_product_data()
            else:
                product_pool = []

            subcategory = _get_subcategory(name, category)
            orig_type_words = _product_type_words(name)
            best_alt = None
            best_score = -1.0
            best_price = float('inf')
            norm_orig = normalize_name(name)

            for p in product_pool:
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
        logger.error("api/alternatives error: %s", e)
        return jsonify({'success': False, 'error': 'Kunne ikke finde alternativer.'})


@app.route('/api/refresh-cache', methods=['POST'])
def refresh_cache():
    """Invalidate local cache after updater.py - protected by CACHE_REFRESH_SECRET."""
    secret = os.environ.get('CACHE_REFRESH_SECRET', '')
    # compare_digest frem for != : konstant tid, så svartiden ikke røber
    # hvor mange tegn af secret'en et gæt ramte rigtigt.
    if not secret or not hmac.compare_digest(
        request.headers.get('X-Cache-Secret') or '', secret
    ):
        return jsonify({'ok': False}), 401

    global _category_index
    _category_index = None
    cached_data['timestamp'] = None

    kv = _edge_kv()
    if kv:
        try:
            from edgekit.runtime import await_sync
            await_sync(kv.delete(_KV_CACHE_KEY))
        except Exception as e:
            logger.warning("KV delete failed: %s", e)

    if _IS_EDGE:
        cached_data['data'] = None
        cached_data['search_index'] = None
        logger.info("Edge cache invalidated - reload sker ved næste request")
        return jsonify({'ok': True, 'invalidated': True})

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