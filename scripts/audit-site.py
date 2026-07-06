#!/usr/bin/env python3
"""Fuld gennemgang af live-siden: sider, kategorier, underkategorier, API'er og AJAX."""
from __future__ import annotations

import html as html_module
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

BASE = os.getenv("APP_URL", "https://madshopper.kasp478g.workers.dev").rstrip("/")

PAGES = [
    "/",
    "/ugens_tilbud",
    "/Mejeri",
    "/Koed_og_fisk",
    "/Frugt_og_groent",
    "/Broed_og_kager",
    "/Kolonial",
    "/Frost",
    "/Drikkevarer",
    "/Slik",
    "/about",
    "/feedback",
    "/terms-of-service",
    "/robots.txt",
    "/sitemap.xml",
]

REDIRECTS = [
    ("/index.html", 301),
    ("/sale.html", 301),
]

ERROR_MARKERS = (
    "internal server error",
    "category not found",
    "page not found",
    "traceback",
    "error 1102",
    "worker exceeded",
)

PASS = FAIL = WARN = 0
ISSUES: list[str] = []


def req(url: str, method: str = "GET", headers: dict | None = None,
        body: bytes | None = None, timeout: float = 45) -> tuple[int, bytes, dict]:
    h = {"User-Agent": "MadShopper-audit/1.0", "Accept": "*/*"}
    if headers:
        h.update(headers)
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def ok(name: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))


def fail(name: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    msg = f"{name}: {detail}" if detail else name
    ISSUES.append(msg)
    print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def warn(name: str, detail: str = "") -> None:
    global WARN
    WARN += 1
    print(f"  ⚠️  {name}" + (f" — {detail}" if detail else ""))


def check_page(path: str, expect_products: bool = False) -> None:
    url = f"{BASE}{path}"
    status, body, _ = req(url)
    html = body.decode("utf-8", errors="replace").lower()
    name = f"GET {path}"

    if status != 200:
        fail(name, f"HTTP {status}")
        return

    for marker in ERROR_MARKERS:
        if marker in html:
            fail(name, f"indeholder '{marker}'")
            return

    if expect_products and "class=\"product\"" not in html and "class='product'" not in html:
        warn(name, "ingen produktkort i HTML")
    else:
        n = html.count('class="product"') + html.count("class='product'")
        ok(name, f"HTTP 200" + (f", {n} produkter" if n else ""))


def extract_subcategories(html: str) -> list[str]:
    subs = re.findall(r'class="subcategory-pill[^"]*"[^>]*data-sub="([^"]*)"', html)
    return [html_module.unescape(s) for s in subs if s]


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def main() -> None:
    print(f"Audit af {BASE}\n")

    section("1. Statiske sider og navigation")
    for path in PAGES:
        expect = path not in ("/about", "/feedback", "/terms-of-service",
                              "/robots.txt", "/sitemap.xml")
        check_page(path, expect_products=expect)

    section("2. Redirects og alias-URL'er")
    for src, expect_status in REDIRECTS:
        status, body, hdrs = req(f"{BASE}{src}")
        loc = hdrs.get("Location", "")
        if status == expect_status:
            ok(f"GET {src}", f"HTTP {status} → {loc or '?'}")
        else:
            fail(f"GET {src}", f"HTTP {status} (forventede {expect_status})")
    for src in ("/feedback.html", "/om-os.html", "/vilkaar.html"):
        status, _, _ = req(f"{BASE}{src}")
        if status == 200:
            ok(f"GET {src}", "HTTP 200")
        else:
            fail(f"GET {src}", f"HTTP {status}")

    section("3. Kategori-underkategorier (pills / ?subcategory=)")
    sub_total = 0
    for cat in ("/Mejeri", "/Koed_og_fisk", "/Frugt_og_groent", "/Broed_og_kager",
                "/Kolonial", "/Frost", "/Drikkevarer", "/Slik"):
        status, body, _ = req(f"{BASE}{cat}")
        if status != 200:
            fail(f"Underkategorier {cat}", f"HTTP {status}")
            continue
        html = body.decode("utf-8", errors="replace")
        subs = extract_subcategories(html)
        if not subs:
            warn(f"Underkategorier {cat}", "ingen subcategory-pills fundet")
            continue
        for sub in subs[:8]:  # max 8 pr. kategori
            q = urllib.parse.urlencode({"subcategory": sub})
            path = f"{cat}?{q}"
            check_page(path, expect_products=True)
            sub_total += 1
        ok(f"Pills på {cat}", f"{len(subs)} underkategorier")

    section("4. AJAX / filtrering (X-Requested-With)")
    ajax_paths = [
        "/",
        "/ugens_tilbud",
        "/Mejeri",
        "/Mejeri?" + urllib.parse.urlencode({"subcategory": "Mælk & Fløde"}),
        "/search?" + urllib.parse.urlencode({"q": "mælk"}),
    ]
    for path in ajax_paths:
        url = BASE + path
        status, body, _ = req(url, headers={"X-Requested-With": "XMLHttpRequest"})
        html = body.decode("utf-8", errors="replace")
        if status != 200:
            fail(f"AJAX {path}", f"HTTP {status}")
            continue
        if "<!doctype" in html.lower() and "<head>" in html.lower():
            warn(f"AJAX {path}", "fuld HTML-side i stedet for fragment")
        elif "product" in html.lower() or "no-results" in html.lower() or "dynamic-content" in html.lower():
            ok(f"AJAX {path}", f"HTTP 200, {len(body)} bytes")
        else:
            warn(f"AJAX {path}", f"HTTP 200 men uventet indhold ({len(body)} bytes)")

    section("5. Søgning og autocomplete")
    check_page("/search?" + urllib.parse.urlencode({"q": "mælk"}), expect_products=True)
    check_page("/search/results?" + urllib.parse.urlencode({"q": "mælk"}), expect_products=True)

    status, body, _ = req(f"{BASE}/api/autocomplete?" + urllib.parse.urlencode({"q": "mæl"}))
    try:
        data = json.loads(body)
        n = len(data.get("suggestions", []))
        if status == 200 and n > 0:
            ok("GET /api/autocomplete?q=mæl", f"{n} forslag")
        elif status == 200:
            warn("/api/autocomplete", "tomt svar")
        else:
            fail("/api/autocomplete", f"HTTP {status}")
    except json.JSONDecodeError:
        fail("/api/autocomplete", "ikke JSON")

    section("6. API-endpoints")
    status, body, _ = req(f"{BASE}/api/stores")
    try:
        data = json.loads(body)
        stores = data.get("stores", [])
        if status == 200 and len(stores) >= 5:
            ok("/api/stores", f"{len(stores)} butikker")
        else:
            fail("/api/stores", f"HTTP {status}, {len(stores)} butikker")
    except json.JSONDecodeError:
        fail("/api/stores", "ikke JSON")

    status, body, _ = req(f"{BASE}/api/products?store=rema")
    try:
        data = json.loads(body)
        prods = data.get("products", data if isinstance(data, list) else [])
        if status == 200 and len(prods) > 100:
            ok("/api/products?store=rema", f"{len(prods)} produkter")
            sample_id = str(prods[0].get("id") or prods[0].get("/product/id") or "")
        else:
            fail("/api/products", f"HTTP {status}")
            sample_id = ""
    except (json.JSONDecodeError, IndexError, KeyError):
        fail("/api/products", "ikke JSON")
        sample_id = ""

    status, body, _ = req(f"{BASE}/api/price-history/1")
    try:
        data = json.loads(body)
        if status == 200 and data.get("success") is True:
            ok("/api/price-history/1", f"{len(data.get('history', []))} punkter")
        else:
            fail("/api/price-history/1", f"HTTP {status}")
    except json.JSONDecodeError:
        fail("/api/price-history/1", "ikke JSON")

    if sample_id:
        check_page(f"/product/{sample_id}", expect_products=False)

    section("7. Statiske assets")
    for asset in (
        "/static/css/styles.css?v=8",
        "/static/js/script.js?v=8",
    ):
        status, body, hdrs = req(f"{BASE}{asset}")
        ct = hdrs.get("Content-Type", "")
        if status == 200 and len(body) > 1000:
            ok(f"GET {asset.split('?')[0]}", f"{len(body)} bytes, {ct.split(';')[0]}")
        else:
            fail(f"GET {asset}", f"HTTP {status}, {len(body)} bytes")

    section("8. Footer-links og interne links på forsiden")
    status, body, _ = req(f"{BASE}/")
    html = body.decode("utf-8", errors="replace")
    for href in ("/terms-of-service", "/about", "/feedback", "/ugens_tilbud", "/Mejeri"):
        if f'href="{href}"' in html or f"href='{href}'" in html:
            ok(f"Forside link {href}", "fundet")
        else:
            warn(f"Forside link {href}", "ikke fundet i HTML")

    section("9. Paginering")
    for path in (
        "/Mejeri?" + urllib.parse.urlencode({"page": 2}),
        "/ugens_tilbud?" + urllib.parse.urlencode({"page": 2}),
        "/search?" + urllib.parse.urlencode({"q": "mælk", "page": 2}),
    ):
        check_page(path, expect_products=True)

    section("10. POST API'er (smoke test)")
    post_tests = [
        ("/api/feedback", {"type": "feedback", "message": "Audit smoke test besked"}),
        ("/api/cart-event", {"product_id": "1"}),
    ]
    for path, payload in post_tests:
        data = json.dumps(payload).encode()
        status, body, _ = req(
            f"{BASE}{path}", method="POST",
            headers={"Content-Type": "application/json"},
            body=data,
        )
        try:
            resp = json.loads(body)
            if status == 200 and (resp.get("success") or resp.get("ok")):
                ok(f"POST {path}", json.dumps(resp)[:80])
            else:
                fail(f"POST {path}", f"HTTP {status} {body[:120]!r}")
        except json.JSONDecodeError:
            fail(f"POST {path}", f"HTTP {status}, ikke JSON: {body[:80]!r}")

    section("RESULTAT")
    print(f"\n  ✅ {PASS} bestået  |  ⚠️  {WARN} advarsler  |  ❌ {FAIL} fejl")
    if ISSUES:
        print("\nFejl:")
        for issue in ISSUES:
            print(f"  • {issue}")
    print()
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
