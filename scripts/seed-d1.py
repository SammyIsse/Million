#!/usr/bin/env python3
"""Indlæser produkter fra Supabase app_cache ind i Cloudflare D1.

Kører lokalt (hvor der er netværk + wrangler-login). Bygger en tabel med
queryable kolonner, så Worker'en kun henter det en side skal bruge.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

# DEPLOY_ENV=staging seeder madshopper-dev i stedet for produktions-D1/KV
# (samme skelnen som scripts/build-pages.sh bruger til selve worker-deployet).
if os.environ.get("DEPLOY_ENV") == "staging":
    DB_NAME = "madshopper-dev"
    KV_NAMESPACE_ID = "b879e69c3a1f477c9c69bbc7e7b041df"
else:
    DB_NAME = "madshopper"
    KV_NAMESPACE_ID = "0e60bdf03ed4490cbfac5fa72c8adca5"

# D1's gratis-plan-budget (100k rows written / 5M rows read pr. dag) er
# KONTO-bredt - delt mellem madshopper og madshopper-dev, ikke pr. database
# (bekræftet 2026-07-19: 710k/100k skrivninger var de to tilsammen). Derfor
# tjekkes/opdateres reseed-spærren altid mod PRODUKTIONENS KV-namespace,
# uanset hvilket miljø der seedes - en guard pr. miljø ville ikke opdage at
# begge tilsammen sprænger den fælles kontogrænse.
GUARD_KV_NAMESPACE_ID = "0e60bdf03ed4490cbfac5fa72c8adca5"
GUARD_HOURS = 6

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app_support import (  # noqa: E402
    _get_subcategory, _STORE_CONFIGS, CAT_MEJERI,
    is_organic, is_lactose_free, parse_weight_to_grams,
    normalize_name, _PLACEHOLDER_IMGS,
    is_non_food_name, is_age_restricted, is_rema_tobacco_id,
)
from updater import get_search_flavor_keywords  # noqa: E402

_TOBACCO_IMG_RE = re.compile(r'rema-product-images\.digital\.rema1000\.dk/(\d+)/')


def _is_tobacco_image(url: str) -> bool:
    m = _TOBACCO_IMG_RE.search(url)
    if not m:
        return False
    return is_rema_tobacco_id(m.group(1))


def _home_is_allowed(p: dict) -> bool:
    """Samme regler som app.py::filter_products_by_stores' _is_allowed (minus
    butiksfiltrering, som forbliver pr.-request i app.py). Anvendes her ved
    seed-tid i stedet for ved hver forsidevisning - målt: kontrollen fjernede
    0 af 18.781 rækker (upstream matching/scraping udelukker allerede disse
    kategorier), men kørte alligevel igen på de samme ~400 forudberegnede
    varer ved HVER request og stod for 54% af forsidens CPU. HOLD DE TO
    FUNKTIONER I SYNC, hvis reglerne nogensinde ændres."""
    img = str(p.get('/product/imageLink', '')).strip()
    if img in _PLACEHOLDER_IMGS or _is_tobacco_image(img):
        return False
    rema_img = str(p.get('/product/rema_image', '')).strip()
    if rema_img in _PLACEHOLDER_IMGS or _is_tobacco_image(rema_img):
        return False
    title = str(p.get('/product/title', ''))
    brand = str(p.get('/product/brand', ''))
    if is_age_restricted(title, brand, product_id=p.get('/product/id', '')):
        return False
    if is_non_food_name(title) or is_non_food_name(brand):
        return False
    bilka_brand = str((p.get('/product/store_matches') or {}).get('bilka', {}).get('brand', '')).lower().strip()
    if bilka_brand.startswith('deli'):
        return False
    if str(p.get('/product/store', '')).lower() == 'bilka' and str(p.get('/product/brand', '')).lower().strip().startswith('deli'):
        return False
    return True

# Skrive-tabellen cart_popularity er miljø-adskilt ligesom i app.py::_table_suffix.
TABLE_SUFFIX = "_dev" if os.environ.get("DEPLOY_ENV") == "staging" else ""

SUPABASE_URL = (
    os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    or os.environ.get("SUPABASE_URL")
    or "https://oxzxingkbsnqzpmjtktr.supabase.co"
)
SUPABASE_KEY = (
    os.environ.get("DEPLOY_KEY")
    or os.environ.get("SUPABASE_KEY")
    or "sb_publishable_Jt8N0XezmzfZJSzzSwBBKQ_uGbNoq8f"
)

MAX_STMT_BYTES = 60_000      # hver INSERT skal være under D1's statement-grænse
BYTES_PER_FILE = 2_000_000   # færre, større filer = færre wrangler-kald = hurtigere

# Seed ind i en midlertidig tabel, mens den gamle 'products' fortsat betjener
# trafik. Til sidst byttes de om (næsten uden nedetid) i FINALIZE.
SCHEMA = """
DROP TABLE IF EXISTS products_new;
CREATE TABLE products_new (
  id TEXT PRIMARY KEY,
  category TEXT,
  subcategory TEXT,
  title TEXT,
  price REAL,
  eff_price REAL,
  is_sale INTEGER DEFAULT 0,
  organic INTEGER DEFAULT 0,
  lactose INTEGER DEFAULT 0,
  weight_g REAL,
  store TEXT,
  stores TEXT,
  search_text TEXT,
  data TEXT
);
"""

# Indekser oprettes EFTER indsættelse (hurtigere) på den færdige tabel.
# idx_products_category_price dækker "ORDER BY eff_price" inden for en
# kategori (_d1_listing i app.py, sort=price-asc/-desc) - uden den bruger
# planlæggeren idx_products_category til selve filtreringen og sorterer
# resultatet i en midlertidig B-træ bagefter (130x langsommere målt).
FINALIZE = """
DROP TABLE IF EXISTS products;
ALTER TABLE products_new RENAME TO products;
CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_category_price ON products(category, eff_price);
CREATE INDEX idx_products_subcat ON products(category, subcategory);
CREATE INDEX idx_products_sale ON products(is_sale);
CREATE INDEX idx_products_store ON products(store);
"""


def available_stores(p: dict) -> str:
    """'|'-omkranset liste af butiks-labels varen findes hos (til SQL-filter)."""
    labels = {str(p.get("/product/store", "Rema 1000"))}
    if p.get("/product/rema_price"):
        labels.add("Rema 1000")
    for key in (p.get("/product/store_matches") or {}):
        cfg = _STORE_CONFIGS.get(key)
        if cfg and cfg.get("label"):
            labels.add(cfg["label"])
    return "|" + "|".join(sorted(labels)) + "|"


_LOCAL_APP_CACHE = os.path.join(ROOT, "data", "app_cache_local.json")
_LOCAL_APP_CACHE_MAX_AGE_S = 1800  # 30 min


def fetch_products() -> list[dict]:
    # updater.py's _save_app_cache() skriver ALTID præcis samme produktliste
    # til data/app_cache_local.json FØR den uploader til Supabase (se
    # updater.py:1354-1363) - i cache-updater.yml kører seed-d1.py som næste
    # trin i SAMME job/runner lige efter, så filen er på det tidspunkt
    # identisk med det der netop blev skrevet til app_cache. At hente den
    # samme ~30 MB igen over netværket dér er ren Supabase-egress der aldrig
    # giver noget nyt (bekræftet 2026-08-05: gratis-planens 5 GB/måned-kvote).
    # Kun brugt hvis filen er frisk (< 30 min) - en standalone/manuel kørsel
    # af dette script uden en updater.py-kørsel lige før falder automatisk
    # tilbage til den gamle Supabase-hentning, så en gammel liggende fil
    # aldrig kan seede D1 med forældede data.
    if os.path.exists(_LOCAL_APP_CACHE):
        age_s = time.time() - os.path.getmtime(_LOCAL_APP_CACHE)
        if age_s < _LOCAL_APP_CACHE_MAX_AGE_S:
            try:
                with open(_LOCAL_APP_CACHE, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                products = payload.get("products") or []
                if products:
                    print(
                        f"Genbruger frisk data/app_cache_local.json ({len(products)} "
                        f"produkter, {age_s:.0f}s gammel) - springer Supabase-hentning over"
                    )
                    return products
            except Exception as e:
                print(f"Kunne ikke læse lokal cache ({e}) - henter fra Supabase i stedet")

    url = f"{SUPABASE_URL}/rest/v1/app_cache?select=*&order=id.asc"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    print("Henter app_cache fra Supabase ...")
    raw = urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=120
    ).read()
    rows = json.loads(raw)
    products: list[dict] = []
    for row in rows:
        if row.get("id") == 0:
            continue
        chunk = row.get("data")
        if isinstance(chunk, list):
            products.extend(chunk)
    print(f"  {len(products)} produkter ({len(raw) / 1024 / 1024:.1f} MB)")
    return products


# Interne felter som KUN bruges af updater.py/scrapers ved bygning - aldrig af
# runtime (app.py/app_support.py). Fjernes fra det gemte 'data' for at halvere
# blob-størrelsen (mindre JSON-parsing i worker'en + mindre D1).
# NB: /product/ean og store_matches 'ean' BEHOLDES nu - nutrition_candidate_keys
# (app_support.py) slår næring op via EAN, så de skal med ud til edge/D1.
_TOP_DROP = frozenset({"/product/image_hash", "/product/weight_grams"})
_MATCH_DROP = frozenset({"_hash_int", "_norm_name", "_image_hash", "_weight_g", "_stk_count"})


def slim_product(p: dict) -> dict:
    """Fjern build-only felter fra produkt-JSON før det gemmes i D1."""
    out = {}
    for k, v in p.items():
        if k in _TOP_DROP:
            continue
        if k == "/product/store_matches" and isinstance(v, dict):
            slim_matches = {}
            for sk, match in v.items():
                if isinstance(match, dict):
                    slim_matches[sk] = {
                        mk: mv for mk, mv in match.items() if mk not in _MATCH_DROP
                    }
                else:
                    slim_matches[sk] = match
            out[k] = slim_matches
        else:
            out[k] = v
    return out


def sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_row_values(p: dict) -> str | None:
    pid = str(p.get("/product/id", "")).strip()
    if not pid or pid in ("None", "nan"):
        return None
    category = str(p.get("/product/product_type") or "Andre varer")
    title = str(p.get("/product/title", ""))
    subcategory = _get_subcategory(title, category)
    try:
        price = float(p.get("/product/price", 0) or 0)
    except (TypeError, ValueError):
        price = 0.0
    sale_price = p.get("/product/sale_price")
    is_sale = 1 if (sale_price is not None or p.get("/product/is_any_sale")) else 0
    try:
        eff_price = float(sale_price) if sale_price is not None else price
    except (TypeError, ValueError):
        eff_price = price
    # Øko/laktose/vægt som kolonner, så edge-filtrene kan afgøres i SQL FØR
    # paginering (ellers bliver sideantal/total talt uden filtrene).
    desc = str(p.get("/product/description", "") or "")
    brand = str(p.get("/product/brand", "") or "")
    organic = 1 if is_organic(title, desc, brand) else 0
    lactose = 1 if is_lactose_free(title, desc, brand) else 0
    weight_g = parse_weight_to_grams(str(p.get("/product/unit_pricing_measure", "") or ""))
    if weight_g is None:
        try:
            weight_g = float(p.get("/product/weight_g"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            weight_g = None
    store = str(p.get("/product/store", "Rema 1000"))
    stores = available_stores(p)
    # normalize_name (ikke bare .lower()) så search_text bærer samme
    # kanoniske stavemåde som forespørgslen bliver normaliseret til i
    # app.py::load_search_raw - ellers matcher fx "hakket svinekød" aldrig
    # et Rema-kort med rå titel "HK. SVINEKØD".
    base_text = " ".join([
        str(p.get("/product/title", "")),
        str(p.get("/product/brand", "")),
        str(p.get("/product/description", "")),
    ])
    img_url = str(p.get("/product/imageLink", ""))
    flavor_kw = get_search_flavor_keywords(base_text, img_url)
    search_text = normalize_name(f"{base_text} {flavor_kw}".strip())
    # Samme regex-tunge opslag som lige er brugt til search_text ovenfor -
    # send resultatet med ind i data-JSON'en, så app_support._product_flavor_
    # search_field() kan slå det op i stedet for at genberegne det live pr.
    # søgning pr. kandidat (op til 800). Det var den anden af to bekræftede
    # CPU-budget-årsager til Error 1101/1102 (2026-08-05), ved siden af selve
    # D1/KV-bro-kollisionen.
    p["/product/flavor_kw"] = normalize_name(flavor_kw) if flavor_kw else ""
    # subcategory/organic/lactose er lige beregnet ovenfor til deres egne
    # D1-kolonner (bruges til SQL-filtrering FØR paginering) - samme mønster
    # som flavor_kw ramte: send dem også med i data-JSON'en, så
    # product_to_display_dict (app_support.py) kan slå dem op i stedet for at
    # genberegne (_get_subcategory scanner op til 100+ nøgleord pr. produkt,
    # og kører på ALLE sider - forside/kategori/tilbud/søgning - ikke kun
    # søgningens kandidatpulje).
    p["/product/subcategory"] = subcategory
    p["/product/is_organic"] = bool(organic)
    p["/product/is_lactose_free"] = bool(lactose)
    data = json.dumps(slim_product(p), separators=(",", ":"), ensure_ascii=False)
    return (
        "("
        + sql_str(pid) + ","
        + sql_str(category) + ","
        + sql_str(subcategory) + ","
        + sql_str(title) + ","
        + f"{price}" + ","
        + f"{eff_price}" + ","
        + f"{is_sale}" + ","
        + f"{organic}" + ","
        + f"{lactose}" + ","
        + ("NULL" if weight_g is None else f"{weight_g}") + ","
        + sql_str(store) + ","
        + sql_str(stores) + ","
        + sql_str(search_text) + ","
        + sql_str(data)
        + ")"
    )


# Kør wrangler fra dist/ lokalt (har genereret wrangler.toml), ellers fra roden
# (CI: root wrangler.toml har D1-bindingen + CLOUDFLARE_API_TOKEN/ACCOUNT_ID).
_DIST = os.path.join(ROOT, "dist")
WRANGLER_CWD = _DIST if os.path.isdir(_DIST) else ROOT


def run_wrangler_sql(sql: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as f:
        f.write(sql)
        path = f.name
    try:
        subprocess.run(
            ["npx", "wrangler", "d1", "execute", DB_NAME, "--remote", f"--file={path}", "-y"],
            cwd=WRANGLER_CWD,
            check=True,
        )
    finally:
        os.unlink(path)


_HOME_KV_KEY = "home_data_v1"
_HOME_SALE_LIMIT = 200
_HOME_MEJERI_LIMIT = 200
_HOME_FAV_LIMIT = 60


def fetch_popular_product_ids(limit: int = _HOME_FAV_LIMIT) -> list[str]:
    """Samme udvælgelse som app.py::_popular_product_ids - kørt her så forsiden
    kan læse resultatet fra KV i stedet for at ramme Supabase pr. request."""
    url = (
        f"{SUPABASE_URL}/rest/v1/cart_popularity{TABLE_SUFFIX}"
        f"?select=product_id,count&count=gte.2&order=count.desc&limit={limit}"
    )
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=30
        ).read()
        rows = json.loads(raw)
        return [str(r.get("product_id")) for r in rows if r.get("product_id")]
    except Exception as e:
        print(f"  advarsel: kunne ikke hente popularitets-id'er: {e}")
        return []


_HOME_RECIPE_LIMIT = 10


def fetch_recipe_pool(limit: int = _HOME_RECIPE_LIMIT) -> list[dict]:
    """"Lækre opskrifter"-pulje: godkendte opskrifter rangeret efter akkumuleret
    klik-pointsum (recipe_points{TABLE_SUFFIX}, nedtrappende pointværdi pr.
    klik med opskriftens alder - se scripts/supabase-recipe-clicks.sql).
    Uafgjort afgøres af nyeste opskrift - bekræftet af opdragsgiver 2026-08-01.
    Opskrifter uden klik endnu tælles som 0 point, så nye/upopulære opskrifter
    naturligt fylder tomme pladser i stedet for et sparsomt afsnit ved cold
    start, i stedet for at kun nogensinde-klikkede opskrifter kan optræde."""
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}

    def _get(path: str, params: str) -> list:
        url = f"{SUPABASE_URL}/rest/v1/{path}?{params}"
        try:
            raw = urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=30
            ).read()
            return json.loads(raw)
        except Exception as e:
            print(f"  advarsel: kunne ikke hente {path}: {e}")
            return []

    recipes = _get(
        "recipes",
        "select=id,title,image_url,created_at&status=eq.approved"
        "&order=created_at.desc&limit=2000",
    )
    if not recipes:
        return []

    points_by_id = {
        r["recipe_id"]: r
        for r in _get(f"recipe_points{TABLE_SUFFIX}", "select=recipe_id,total_points,click_count")
    }
    snapshot_by_id = {
        r["recipe_id"]: r
        for r in _get(
            "recipe_price_snapshot",
            "select=recipe_id,cheapest_total_price,matched_ingredient_count,"
            "total_ingredient_count,ingredients_on_sale_count",
        )
    }

    ranked = []
    for r in recipes:
        rid = r["id"]
        pts = points_by_id.get(rid, {})
        snap = snapshot_by_id.get(rid, {})
        total = snap.get("total_ingredient_count") or 0
        on_sale = snap.get("ingredients_on_sale_count") or 0
        ranked.append({
            "id": rid,
            "title": r.get("title", ""),
            "image_url": r.get("image_url", ""),
            "created_at": r.get("created_at", ""),  # kun til sortering, fjernes nedenfor
            "total_points": pts.get("total_points", 0),
            "click_count": pts.get("click_count", 0),
            "cheapest_total_price": snap.get("cheapest_total_price"),
            "matched_ingredient_count": snap.get("matched_ingredient_count", 0),
            "total_ingredient_count": total,
            "sale_ratio": round(on_sale / total, 3) if total else 0.0,
        })

    # (total_points, created_at) begge faldende i én sort: højeste pointsum
    # øverst, uafgjort -> nyeste opskrift (ISO8601-strenge sammenligner
    # korrekt leksikografisk, samme UTC-format fra Supabase).
    ranked.sort(key=lambda r: (r["total_points"], r["created_at"]), reverse=True)
    top = ranked[:limit]
    for r in top:
        del r["created_at"]
    return top


def build_home_data(products: list[dict]) -> dict:
    """Forudberegner forsidens kandidatpuljer (Ugens Tilbud, Køl (kategorisiden,
    ikke længere forsidesektionen), Populære varer, Lækre opskrifter), så
    app.py::home() på edge kan læse ét KV-opslag i stedet for at ramme D1 (2x)
    + Supabase (flere x) pr. samtidig sidevisning - det var hovedbidraget til
    1101/1102-nedbruddet under samtidig trafik.
    Butiksfiltrering (_adjust_for_stores) forbliver pr.-request i app.py,
    da den afhænger af den enkelte besøgendes cookie/query-param."""
    sale_raw, mejeri_raw = [], []
    by_id: dict[str, dict] = {}
    for p in products:
        pid = str(p.get("/product/id", "")).strip()
        if pid and pid not in by_id:
            by_id[pid] = p
        if not _home_is_allowed(p):
            continue
        if len(sale_raw) < _HOME_SALE_LIMIT and (
            p.get("/product/sale_price") or p.get("/product/is_any_sale")
        ):
            sale_raw.append(slim_product(p))
        if len(mejeri_raw) < _HOME_MEJERI_LIMIT:
            category = str(p.get("/product/product_type") or "Andre varer")
            if category == CAT_MEJERI:
                mejeri_raw.append(slim_product(p))

    pop_ids = fetch_popular_product_ids()
    fav_pool = [
        slim_product(by_id[pid]) for pid in pop_ids
        if pid in by_id and _home_is_allowed(by_id[pid])
    ]

    return {
        "sale_raw": sale_raw,
        "mejeri_raw": mejeri_raw,
        "pop_ids": pop_ids,
        "fav_pool": fav_pool,
        "recipe_pool": fetch_recipe_pool(),
    }


def write_home_data(data: dict) -> None:
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write(payload)
        path = f.name
    try:
        subprocess.run(
            ["npx", "wrangler", "kv", "key", "put", _HOME_KV_KEY, f"--path={path}",
             "--namespace-id", KV_NAMESPACE_ID, "--remote"],
            cwd=WRANGLER_CWD,
            check=True,
        )
        print(f"  {_HOME_KV_KEY} opdateret ({len(payload) / 1024:.0f} KB)")
    except Exception as e:
        print(f"  advarsel: kunne ikke skrive {_HOME_KV_KEY}: {e}")
    finally:
        os.unlink(path)


def set_cache_version() -> None:
    """Skriv en ny cache_version til KV. Worker'en bruger den i cache-nøglen,
    så den daglige opdatering automatisk nulstiller edge-cachen (friske priser
    med det samme). Fejler blødt - caching virker stadig med gammel version."""
    version = str(int(time.time()))
    try:
        subprocess.run(
            ["npx", "wrangler", "kv", "key", "put", "cache_version", version,
             "--namespace-id", KV_NAMESPACE_ID, "--remote"],
            cwd=WRANGLER_CWD,
            check=True,
        )
        print(f"  cache_version = {version}")
    except Exception as e:
        print(f"  advarsel: kunne ikke sætte cache_version: {e}")


def last_seed_info() -> tuple[float, str] | None:
    """(timer siden sidste fulde reseed, hvilket miljø det var).

    Tidsstemplet er FÆLLES for madshopper og madshopper-dev, fordi D1-budgettet
    er konto-bredt (se GUARD_KV_NAMESPACE_ID). Miljø-mærket gemmes med, så
    produktionens natlige seed ikke bliver sultet ihjel af et staging-seed -
    se guarden i main().

    Formatet er "<epoch>" (gammelt, uden mærke) eller "<epoch>:<env>". Ukendt
    mærke læses som "prod", så en gammel værdi opfører sig som før.

    Fejler ÅBENT (returnerer None) - en manglende læsning må ikke i sig selv
    blokere en seed."""
    try:
        result = subprocess.run(
            ["npx", "wrangler", "kv", "key", "get", "d1_last_full_seed",
             "--namespace-id", GUARD_KV_NAMESPACE_ID, "--remote"],
            cwd=WRANGLER_CWD, capture_output=True, text=True, timeout=30,
        )
        value = result.stdout.strip()
        if result.returncode != 0 or not value:
            return None
        stamp, _, env = value.partition(":")
        return (time.time() - float(stamp)) / 3600, (env or "prod")
    except Exception:
        return None


def mark_seeded() -> None:
    env_tag = "staging" if TABLE_SUFFIX else "prod"
    try:
        subprocess.run(
            ["npx", "wrangler", "kv", "key", "put", "d1_last_full_seed",
             f"{int(time.time())}:{env_tag}",
             "--namespace-id", GUARD_KV_NAMESPACE_ID, "--remote"],
            cwd=WRANGLER_CWD,
            check=True,
        )
    except Exception as e:
        print(f"  advarsel: kunne ikke gemme reseed-tidsstempel: {e}")


def main() -> int:
    # D1's gratis-plan-budget (100k rows written/dag, KONTO-bredt - delt
    # mellem madshopper og madshopper-dev) blev sprunget 7x på én dag
    # 2026-07-19 af gentagne fulde reseeds, og igen 2026-07-20. En fuld
    # reseed skriver hele produkt-tabellen på ny, så gentagne kørsler samme
    # dag (planlagt + manuel + fallback-triggere) rammer budgettet hurtigt.
    # Spær derfor medmindre FORCE_RESEED=1 er sat eksplicit (fx til en
    # hastende prisrettelse, hvor man accepterer risikoen).
    seeding_prod = not TABLE_SUFFIX
    info = last_seed_info()
    if info is not None and not os.environ.get("FORCE_RESEED"):
        age, last_env = info
        # Produktionens seed viger ikke for et staging-seed. Guarden findes for
        # at bremse GENTAGNE fulde reseeds (D1-budgettet er konto-bredt), ikke
        # for at lade et dev-push kl. 20 spise nattens prod-kørsel kl. 01 - det
        # skete tavst med exit 0, så prod-D1 og cache_version stod på
        # gårsdagens data et helt døgn med grøn CI. Prod seeder én gang i
        # døgnet uanset hvad; det er staging der må vente.
        yields_to_prod = seeding_prod and last_env == "staging"
        if age < GUARD_HOURS and not yields_to_prod:
            print(
                f"Sprunget over: sidste fulde D1-reseed ({last_env}) var for "
                f"{age:.1f} time(r) siden (< {GUARD_HOURS}t-grænse). Sæt FORCE_RESEED=1 for at "
                f"køre alligevel."
            )
            return 0
        if yields_to_prod and age < GUARD_HOURS:
            print(
                f"Sidste reseed var staging for {age:.1f} time(r) siden - "
                f"produktions-seed koerer alligevel."
            )

    # Reservér guarden HER, straks efter tjekket er bestået - ikke først ved
    # succesfuld afslutning (databaserevision 17-08-2026, fund L8). Uden dette
    # er der et TOCTOU-vindue: to næsten-samtidige kørsler (manuel + planlagt,
    # eller to FORCE_RESEED-kørsler) kunne begge læse "ingen nylig seed" og
    # begge fortsætte, hvilket ville fordoble D1-budgetforbruget og potentielt
    # sammenflette to skema-opbygninger. Bagsiden: fejler denne kørsel efter
    # dette punkt, blokerer guarden en øjeblikkelig retry uden FORCE_RESEED=1 -
    # det er en bevidst accept, ikke en fejl: netop ukontrollerede gentagne
    # forsøg samme dag var det der sprængte D1-budgettet 2026-07-19/07-20 (se
    # kommentaren ovenfor). mark_seeded() kaldes igen ved reel succes nedenfor,
    # så tidsstemplet ender med at afspejle den faktiske færdiggørelsestid.
    mark_seeded()

    products = fetch_products()
    if not products:
        print("Ingen produkter - afbryder.")
        return 1

    # Dæknings-værn: updater.py har sit eget (run_updater), men dette script
    # kan køres selvstændigt og seede D1 direkte fra hvad der p.t. ligger i
    # Supabase' app_cache - en anden sti end updateren, og derfor et separat
    # sikkerhedsnet mod at seede + bumpe cache_version med en Rema-only-cache
    # (ingen prissammenligninger) eller et kollaps i antal produkter.
    if len(products) < 8000:
        print(f"Kun {len(products)} produkter (forventet 8000+) - afbryder uden at seede.")
        return 1
    matched = sum(1 for p in products if p.get("/product/store_matches"))
    coverage = matched / len(products)
    if coverage < 0.25:
        print(
            f"Kun {coverage * 100:.1f}% af {len(products)} produkter har en "
            f"butiksmatch (forventet 50%+) - afbryder uden at seede."
        )
        return 1

    print("Opretter schema ...")
    run_wrangler_sql(SCHEMA)

    insert_prefix = (
        "INSERT INTO products_new "
        "(id,category,subcategory,title,price,eff_price,is_sale,organic,lactose,weight_g,store,stores,search_text,data) VALUES "
    )

    file_sql: list[str] = []
    file_bytes = 0
    batch: list[str] = []
    batch_bytes = 0
    total = 0
    file_count = 0

    def flush_file():
        nonlocal file_sql, file_bytes, file_count
        if not file_sql:
            return
        file_count += 1
        print(f"  skriver batch-fil #{file_count} ({file_bytes / 1024:.0f} KB) ...")
        run_wrangler_sql("\n".join(file_sql))
        file_sql = []
        file_bytes = 0

    def flush_batch():
        nonlocal batch, batch_bytes, file_bytes
        if not batch:
            return
        stmt = insert_prefix + ",".join(batch) + ";"
        file_sql.append(stmt)
        file_bytes += len(stmt)
        batch = []
        batch_bytes = 0

    seen_ids: set[str] = set()
    dupes = 0
    placeholders = 0

    for p in products:
        pid = str(p.get("/product/id", "")).strip()
        if not pid or pid in ("None", "nan"):
            continue
        if pid in seen_ids:
            dupes += 1
            continue
        # Kort med placeholder-billede (butikslogo) frafiltreres ALTID ved
        # visning af filter_products_by_stores i app.py - de kan aldrig ses.
        # Men de laa i D1 og blev talt med i COUNT og i LIMIT/OFFSET, saa
        # pagineringen fik huller: en side kunne vise 57 varer i stedet for 60,
        # og total_pages var for hoej. ~2% af kataloget.
        if str(p.get("/product/imageLink", "")).strip() in _PLACEHOLDER_IMGS:
            placeholders += 1
            continue
        seen_ids.add(pid)
        values = build_row_values(p)
        if not values:
            continue
        # Én meget stor vare kan alene overstige grænsen - send den solo.
        if batch and batch_bytes + len(values) >= MAX_STMT_BYTES:
            flush_batch()
            if file_bytes >= BYTES_PER_FILE:
                flush_file()
        batch.append(values)
        batch_bytes += len(values) + 1
        total += 1

    flush_batch()
    flush_file()

    if placeholders:
        print(f"  {placeholders} kort med placeholder-billede udeladt (kan alligevel ikke vises)")
    if dupes:
        print(f"  advarsel: sprang {dupes} duplikerede produkt-id'er over")

    print("Skifter til ny tabel (swap) ...")
    run_wrangler_sql(FINALIZE)

    print("Forudberegner forside-data (sale/køl/favoritter) ...")
    write_home_data(build_home_data(products))

    print("Nulstiller edge-cache (cache_version) ...")
    set_cache_version()
    mark_seeded()

    print(f"Færdig - {total} produkter indlæst i D1 ({file_count} batch-filer).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
