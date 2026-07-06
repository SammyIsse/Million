#!/usr/bin/env python3
"""Verificér integrationskæder: Supabase, app, updater, Cloudflare Worker."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

APP_URL = os.getenv("APP_URL", "https://madshopper.dk").rstrip("/")
SUPABASE_URL = (
    os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    or os.getenv("SUPABASE_URL")
    or ""
).rstrip("/")
SUPABASE_KEY = (
    os.getenv("DEPLOY_KEY")
    or os.getenv("SUPABASE_KEY")
    or os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
    or ""
)
D1_DB_ID = "8a43b0d1-1733-4abe-ad71-aa9bde4d4d12"

PASS = 0
FAIL = 0
SKIP = 0
WARN = 0


def ok(name: str, detail: str = ""):
    global PASS
    PASS += 1
    print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))


def fail(name: str, detail: str = ""):
    global FAIL
    FAIL += 1
    print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def skip(name: str, detail: str = ""):
    global SKIP
    SKIP += 1
    print(f"  ⏭️  {name}" + (f" — {detail}" if detail else ""))


def warn(name: str, detail: str = ""):
    global WARN
    WARN += 1
    print(f"  ⚠️  {name}" + (f" — {detail}" if detail else ""))


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"


def http_request(url: str, method: str = "GET", headers: dict | None = None,
                 body=None, timeout: float = 30.0) -> tuple[int, bytes]:
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, method=method, headers=hdrs)
    if body is not None:
        req.data = body if isinstance(body, bytes) else body.encode()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def supabase_get(path: str, params: dict | None = None) -> tuple[int, object]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return 0, None
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Accept": "application/json",
    }
    status, raw = http_request(url, headers=headers)
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, None


def section(title: str):
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def test_supabase_core():
    section("1. Supabase — læsning (app + updater + seed-d1)")
    if not SUPABASE_URL or not SUPABASE_KEY:
        fail("Supabase konfiguration", "URL eller nøgle mangler i .env")
        return

    ok("Supabase URL + nøgle konfigureret")

    status, rows = supabase_get("app_cache", {"select": "id", "order": "id.asc"})
    if status == 200 and isinstance(rows, list) and rows:
        ok("app_cache læsbar", f"{len(rows)} chunks")
    else:
        fail("app_cache læsbar", f"HTTP {status}")

    status, rows = supabase_get(
        "app_cache",
        {"select": "data", "id": "gt.0", "limit": "1"},
    )
    n_products = 0
    if status == 200 and isinstance(rows, list) and rows:
        chunk = rows[0].get("data")
        if isinstance(chunk, list):
            n_products = len(chunk)
    if n_products > 0:
        ok("app_cache indeholder produkter", f"≥{n_products} i første chunk")
    else:
        fail("app_cache produktdata", "tom eller utilgængelig")

    status, rows = supabase_get(
        "produkter",
        {"select": "id", "butik": "eq.bilka", "limit": "1"},
    )
    has_deploy = bool(os.getenv("DEPLOY_KEY"))
    if status == 200:
        ok("produkter (scrapers)", "Bilka-rækker læsbare")
    elif status == 401 and not has_deploy:
        warn("produkter (scrapers)", "HTTP 401 med publishable key — forventet lokalt; GitHub bruger DEPLOY_KEY")
    else:
        fail("produkter (scrapers)", f"HTTP {status}")

    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    status, rows = supabase_get(
        "price_history",
        {"select": "product_id,store,price,date", "limit": "1", "date": f"gte.{cutoff}"},
    )
    if status == 200:
        ok("price_history tabel", "læsbar (30-dages filter)")
        if isinstance(rows, list) and rows:
            ok("price_history har data", f"eksempel: {rows[0].get('date')}")
        else:
            warn("price_history tom", "forventet indtil næste cache-updater kører")
    else:
        fail("price_history tabel", f"HTTP {status}")

    for table in ("cart_popularity", "feedback", "price_alerts"):
        status, _ = supabase_get(table, {"select": "*", "limit": "1"})
        if status == 200:
            ok(f"{table} tabel læsbar")
        else:
            fail(f"{table} tabel", f"HTTP {status}")


def test_app_supabase_layer():
    section("2. App.py — Supabase REST-lag")
    try:
        from app import _supabase_available, _supabase_rest
    except Exception as e:
        fail("import app", str(e))
        return

    if _supabase_available():
        ok("_supabase_available()")
    else:
        fail("_supabase_available()", "returnerede False")

    rows, status = _supabase_rest(
        "GET", "app_cache",
        params={"select": "id", "limit": "1"},
    )
    if status == 200:
        ok("_supabase_rest → app_cache")
    else:
        fail("_supabase_rest → app_cache", f"status {status}")

    rows, status = _supabase_rest(
        "GET", "price_history",
        params={"select": "store,price,date", "product_id": "eq.1",
                "date": f"gte.{(datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')}",
                "limit": "5"},
    )
    if status == 200:
        ok("_supabase_rest → price_history (30 dage)")
    else:
        fail("_supabase_rest → price_history", f"status {status}")


def test_updater_layer():
    section("3. Updater.py — Supabase-forbindelse")
    try:
        from updater import _get_supabase_client, db_available, collect_store_prices
    except Exception as e:
        fail("import updater", str(e))
        return

    client = _get_supabase_client()
    if client is not None:
        ok("updater Supabase-klient")
    else:
        fail("updater Supabase-klient", "kunne ikke oprettes")

    if db_available():
        ok("db_available()")
    else:
        warn("db_available()", "False — tjek ENABLE_PRICE_DB / nøgler")

    # collect_store_prices er ren logik — ingen netværk
    sample = collect_store_prices([{
        "/product/id": "test123",
        "/product/rema_price": 11.5,
        "/product/store_matches": {"bilka": {"price": 12.0}},
    }])
    if len(sample) == 2:
        ok("collect_store_prices() logik")
    else:
        fail("collect_store_prices()", f"forventede 2 entries, fik {len(sample)}")


def test_scraper_utils():
    section("4. Scraper → Supabase (supabase_utils)")
    try:
        from scraper.supabase_utils import get_client, fetch_existing_products
    except Exception as e:
        fail("import supabase_utils", str(e))
        return

    try:
        client = get_client()
        ok("scraper get_client()")
    except Exception as e:
        fail("scraper get_client()", str(e))
        return

    cache = fetch_existing_products("bilka")
    if isinstance(cache, dict) and len(cache) > 0:
        ok("fetch_existing_products('bilka')", f"{len(cache)} opslag")
    elif isinstance(cache, dict) and not os.getenv("DEPLOY_KEY"):
        warn("fetch_existing_products('bilka')", "0 opslag — publishable key har ikke SELECT på produkter (OK i prod via DEPLOY_KEY)")
    else:
        fail("fetch_existing_products('bilka')", f"{len(cache) if isinstance(cache, dict) else '?'} opslag")


def test_seed_d1_fetch():
    section("5. Supabase → D1 pipeline (seed-d1 fetch)")
    try:
        from scripts.seed_d1 import fetch_products  # type: ignore
    except ImportError:
        # scripts/seed-d1.py har bindestreg — import direkte
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "seed_d1", ROOT / "scripts" / "seed-d1.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fetch_products = mod.fetch_products

    try:
        products = fetch_products()
        if len(products) > 1000:
            ok("seed-d1 fetch_products()", f"{len(products)} produkter klar til D1")
        elif len(products) > 0:
            warn("seed-d1 fetch_products()", f"kun {len(products)} produkter")
        else:
            fail("seed-d1 fetch_products()", "ingen produkter")
    except Exception as e:
        fail("seed-d1 fetch_products()", str(e))


def test_cloudflare_worker():
    section("6. Cloudflare Worker (live site)")
    status, body = http_request(f"{APP_URL}/", timeout=45)
    html = body.decode("utf-8", errors="replace")
    if status == 200 and len(html) > 500:
        ok("GET /", f"HTTP {status}, {len(html)} bytes")
    else:
        fail("GET /", f"HTTP {status}")

    if "product" in html.lower() or "madshopper" in html.lower() or "rema" in html.lower():
        ok("Forside indeholder produktindhold")
    else:
        warn("Forside produktindhold", "kunne ikke bekræfte i HTML")

    status, body = http_request(f"{APP_URL}/feedback", timeout=30)
    fb_html = body.decode("utf-8", errors="replace")
    if status == 200 and "feedback-form" in fb_html:
        ok("GET /feedback", f"HTTP {status}, formular fundet")
    else:
        fail("GET /feedback", f"HTTP {status}")

    status, body = http_request(f"{APP_URL}/api/price-history/1", timeout=20)
    try:
        data = json.loads(body)
        if status == 200 and data.get("success") is True:
            ok("GET /api/price-history/1", f"history={len(data.get('history', []))} punkter")
        else:
            fail("/api/price-history/1", f"HTTP {status}, body={body[:120]!r}")
    except json.JSONDecodeError:
        fail("/api/price-history/1", "ikke JSON")

    status, _ = http_request(
        f"{APP_URL}/api/refresh-cache",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=b"{}",
        timeout=20,
    )
    if status == 401:
        ok("/api/refresh-cache uden secret", "HTTP 401 som forventet")
    else:
        warn("/api/refresh-cache uden secret", f"HTTP {status} (forventede 401)")

    secret_file = ROOT / ".edge-secret"
    if secret_file.exists():
        secret = secret_file.read_text().strip()
        status, body = http_request(
            f"{APP_URL}/api/refresh-cache",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Cache-Secret": secret,
            },
            body=b"{}",
            timeout=45,
        )
        if status == 200:
            ok("/api/refresh-cache med secret", "HTTP 200")
        else:
            fail("/api/refresh-cache med secret", f"HTTP {status}")
    else:
        skip("/api/refresh-cache med secret", ".edge-secret findes ikke")


def test_d1_via_api():
    section("7. Cloudflare D1 (direkte API)")
    token = os.getenv("CLOUDFLARE_API_TOKEN")
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "a592885c7804b0101fa5583ef1f92031")
    if not token:
        skip("D1 product count", "CLOUDFLARE_API_TOKEN ikke sat lokalt")
        return

    query = urllib.parse.quote("SELECT COUNT(*) AS n FROM products")
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account}"
        f"/d1/database/{D1_DB_ID}/query?sql={query}"
    )
    status, raw = http_request(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        body=b"{}",
    )
    try:
        data = json.loads(raw)
        if status == 200 and data.get("success"):
            count = data["result"][0]["results"][0]["n"]
            ok("D1 products tabel", f"{count} rækker")
        else:
            fail("D1 query", f"HTTP {status}: {raw[:200]!r}")
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        fail("D1 query parse", str(e))


def test_github():
    section("8. GitHub Actions (workflow-konfiguration)")
    workflows_dir = ROOT / ".github" / "workflows"
    expected = [
        "cache-updater.yml",
        "deploy-edge.yml",
    ]
    for wf in expected:
        path = workflows_dir / wf
        if path.exists():
            text = path.read_text()
            if wf == "cache-updater.yml":
                if "SUPABASE_URL" in text and "secrets.DEPLOY_KEY" in text:
                    ok(f"{wf} → Supabase secrets")
                if "seed-d1.py" in text:
                    ok("cache-updater.yml → seed-d1 (Supabase→D1)")
                if "refresh-cache" in text:
                    ok("cache-updater.yml → refresh-cache ping")
            elif wf == "deploy-edge.yml":
                if "CLOUDFLARE_API_TOKEN" in text and "build-pages.sh" in text:
                    ok(f"{wf} → Cloudflare deploy")
                if "CACHE_REFRESH_SECRET" in text:
                    ok(f"{wf} → cache secret synk")
        else:
            fail(f"{wf} findes ikke")

    import shutil
    if shutil.which("gh"):
        status, _ = http_request("https://api.github.com/rate_limit", timeout=10)
        # gh auth may be broken — try listing runs via gh subprocess
        import subprocess
        r = subprocess.run(
            ["gh", "run", "list", "--workflow=cache-updater.yml", "--limit", "3"],
            cwd=ROOT, capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            ok("GitHub cache-updater seneste kørsler", "gh run list OK")
            for line in r.stdout.strip().splitlines()[:2]:
                print(f"      {line}")
        else:
            warn("GitHub CLI", "gh ikke logget ind eller ingen runs — tjek manuelt på github.com")
    else:
        skip("GitHub CLI", "gh ikke installeret")


def main():
    import logging
    logging.basicConfig(level=logging.WARNING)

    print("MadShopper integrationsverifikation")
    print(f"Tid: {datetime.now().isoformat(timespec='seconds')}")
    print(f"App URL: {APP_URL}")
    print(f"Supabase: {SUPABASE_URL or '(mangler)'}")

    test_supabase_core()
    test_app_supabase_layer()
    test_updater_layer()
    test_scraper_utils()
    test_seed_d1_fetch()
    test_cloudflare_worker()
    test_d1_via_api()
    test_github()

    section("Resultat")
    total = PASS + FAIL + SKIP + WARN
    print(f"  ✅ {PASS} bestået  ❌ {FAIL} fejlet  ⚠️  {WARN} advarsler  ⏭️  {SKIP} sprunget over")
    if FAIL:
        print("\n  NOGLE TESTS FEJLEDE — se ❌ ovenfor.")
        sys.exit(1)
    if WARN:
        print("\n  Alle kritiske tests bestået (med advarsler — se ⚠️).")
    else:
        print("\n  Alle kritiske tests bestået.")
    sys.exit(0)


if __name__ == "__main__":
    main()
