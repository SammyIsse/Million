#!/usr/bin/env python3
"""Indlæser produkter fra Supabase app_cache ind i Cloudflare D1.

Kører lokalt (hvor der er netværk + wrangler-login). Bygger en tabel med
queryable kolonner, så Worker'en kun henter det en side skal bruge.
"""
from __future__ import annotations

import json
import os
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
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app_support import (  # noqa: E402
    _get_subcategory, _STORE_CONFIGS,
    is_organic, is_lactose_free, parse_weight_to_grams,
    normalize_name,
)

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
FINALIZE = """
DROP TABLE IF EXISTS products;
ALTER TABLE products_new RENAME TO products;
CREATE INDEX idx_products_category ON products(category);
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


def fetch_products() -> list[dict]:
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
    search_text = normalize_name(" ".join([
        str(p.get("/product/title", "")),
        str(p.get("/product/brand", "")),
        str(p.get("/product/description", "")),
    ]))
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


def main() -> int:
    products = fetch_products()
    if not products:
        print("Ingen produkter - afbryder.")
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

    for p in products:
        pid = str(p.get("/product/id", "")).strip()
        if not pid or pid in ("None", "nan"):
            continue
        if pid in seen_ids:
            dupes += 1
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

    if dupes:
        print(f"  advarsel: sprang {dupes} duplikerede produkt-id'er over")

    print("Skifter til ny tabel (swap) ...")
    run_wrangler_sql(FINALIZE)

    print("Nulstiller edge-cache (cache_version) ...")
    set_cache_version()

    print(f"Færdig - {total} produkter indlæst i D1 ({file_count} batch-filer).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
