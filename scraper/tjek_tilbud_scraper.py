"""Fælles Tjek/ShopGun tilbudsavis-scraper for discount-butikker."""
import os
import re
import sys
import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from app_support import attach_billede_hashes

from keywords import is_non_food as _is_non_food

TJEK_BASE = "https://squid-api.tjek.com"
KATEGORI = "Tilbudsavis"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "da,da-DK;q=0.9",
}

_NON_FOOD_CATALOG = [
    'sommersk', 'skønhed', 'beauty', 'non-food', 'helse og pleje',
    'kæledyr', 'dyr og natur', 'dyremad', 'husholdning', 'rengøring',
    'personlig pleje', 'pleje', 'tøj', 'sko', 'sport', 'fritid',
    'elektronik', 'legetøj', 'blomster', 'have', 'haven', 'udendørs',
    'tekstil', 'sengetøj', 'køkkengrej', 'service og bestik',
]


def is_food(heading: str, catalog_label: str | None) -> bool:
    label = (catalog_label or "").lower()
    if any(p in label for p in _NON_FOOD_CATALOG):
        return False
    return not _is_non_food(heading)


def parse_description(description: str):
    product_type = weight = kg_price = ""
    wm = re.search(r"(\d+[.,]?\d*)\s*(kg|g|l|ml|cl|dl|stk)", description, re.IGNORECASE)
    if wm:
        weight = f"{wm.group(1)} {wm.group(2).lower()}"
        product_type = description[:wm.start()].strip().strip(",| -").strip()
    else:
        tm = re.search(r"^[^,\.]+", description)
        if tm:
            product_type = tm.group(0).strip()
    km = re.search(r"(\d+[.,]?\d*)\s*(?:kr\s*)?/\s*(kg|g|l|ml|cl|dl)", description, re.IGNORECASE)
    if km:
        kg_price = f"{km.group(1)} kr/{km.group(2)}"
    return product_type, weight, kg_price


def fetch_active_catalogs(dealer_id: str) -> list[dict]:
    r = requests.get(
        f"{TJEK_BASE}/v2/catalogs",
        params={"dealer_id": dealer_id, "limit": 50},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def fetch_all_offers(catalog_id: str) -> list[dict]:
    offers = []
    offset = 0
    limit = 100
    while True:
        r = requests.get(
            f"{TJEK_BASE}/v2/offers",
            params={"catalog_id": catalog_id, "limit": limit, "offset": offset},
            timeout=15,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        offers.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return offers


def _rows_from_offers(
    offers: list[dict],
    cat_id: str,
    label: str,
    butik: str,
    seen: set[str],
    *,
    dedupe_by_heading: bool = False,
) -> list[dict]:
    rows: list[dict] = []
    for o in offers:
        heading = o.get("heading", "")
        if not heading:
            continue
        if not is_food(heading, label):
            continue
        key = heading if dedupe_by_heading else f"{cat_id}|{heading}"
        if key in seen:
            continue
        seen.add(key)

        desc = o.get("description", "")
        p_type, weight, kg_price = parse_description(desc)

        pricing = o.get("pricing", {})
        pris = pricing.get("price")
        pre_price = pricing.get("pre_price")

        img = o.get("images", {})
        billede_url = img.get("view") or img.get("thumb") or ""

        rows.append({
            "butik":        butik,
            "kategori":     KATEGORI,
            "navn":         heading,
            "producent":    p_type or None,
            "netto_vaegt":  weight or None,
            "kg_price":     kg_price or None,
            "pris":         float(pris) if pris is not None else None,
            "normalpris":   str(pre_price) if pre_price is not None else None,
            "varenummer":   None,
            "billede_url":  billede_url,
            "billede_hash": None,
            "tilbud":       "Ja",
            "multikob":     None,
        })
    attach_billede_hashes(rows)
    return rows


def fetch_tjek_tilbud_from_catalog_id(catalog_id: str, butik: str) -> list[dict]:
    """Hent tilbud fra ét specifikt Tjek-katalog (fx fundet på butikkens avis-side)."""
    r = requests.get(f"{TJEK_BASE}/v2/catalogs/{catalog_id}", timeout=15)
    r.raise_for_status()
    cat = r.json()
    label = cat.get("label", catalog_id)
    run_till = cat.get("run_till", "")[:10]
    offers = fetch_all_offers(catalog_id)
    print(f"    {label} ({run_till}): {len(offers)} tilbud [katalog {catalog_id}]")
    seen: set[str] = set()
    rows = _rows_from_offers(offers, catalog_id, label, butik, seen)
    print(f"  OK: {len(rows)} {butik}-tilbud hentet fra katalog {catalog_id}")
    return rows


def extract_tjek_catalog_ids(html: str, business_id: str | None = None) -> list[str]:
    """Find Tjek-katalog-ID'er i HTML (data-id på .tjek-widget)."""
    ids: list[str] = []
    seen: set[str] = set()
    patterns = [
        r'class="tjek-widget"[^>]*data-id="([^"]+)"',
        r'data-id="([^"]+)"[^>]*class="tjek-widget"',
    ]
    if business_id:
        patterns.extend([
            rf'data-business-id="{re.escape(business_id)}"[^>]*data-id="([^"]+)"',
            rf'data-id="([^"]+)"[^>]*data-business-id="{re.escape(business_id)}"',
        ])
    for pat in patterns:
        for match in re.finditer(pat, html):
            cid = match.group(1)
            if cid not in seen:
                seen.add(cid)
                ids.append(cid)
    for match in re.finditer(r'ID:([A-Za-z0-9_-]+)', html):
        cid = match.group(1)
        if cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return ids


def fetch_tjek_tilbud_from_catalog_ids(
    catalog_ids: list[str],
    butik: str,
    *,
    dedupe_by_heading: bool = False,
) -> list[dict]:
    """Hent og deduplicer tilbud fra flere Tjek-katalog-ID'er."""
    rows: list[dict] = []
    seen: set[str] = set()
    for catalog_id in catalog_ids:
        r = requests.get(f"{TJEK_BASE}/v2/catalogs/{catalog_id}", timeout=15)
        if r.status_code == 404:
            print(f"    Katalog {catalog_id} findes ikke - springer over")
            continue
        r.raise_for_status()
        cat = r.json()
        label = cat.get("label", catalog_id)
        run_till = cat.get("run_till", "")[:10]
        offers = fetch_all_offers(catalog_id)
        print(f"    {label} ({run_till}): {len(offers)} tilbud [katalog {catalog_id}]")
        rows.extend(_rows_from_offers(
            offers, catalog_id, label, butik, seen, dedupe_by_heading=dedupe_by_heading,
        ))
    print(f"  OK: {len(rows)} {butik}-tilbud hentet fra {len(catalog_ids)} katalog(er)")
    return rows


def scrape_catalog_ids_from_pages(urls: list[str], business_id: str | None = None) -> list[str]:
    """Hent Tjek-katalog-ID'er fra en liste af butikssider."""
    found: list[str] = []
    seen: set[str] = set()
    for url in urls:
        try:
            r = requests.get(url, headers=_HEADERS, timeout=20)
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"    Kunne ikke hente {url}: {exc}")
            continue
        for cid in extract_tjek_catalog_ids(r.text, business_id):
            if cid not in seen:
                seen.add(cid)
                found.append(cid)
    return found


def fetch_tjek_tilbud(dealer_id: str, butik: str, *, dedupe_by_heading: bool = False) -> list[dict]:
    catalogs = fetch_active_catalogs(dealer_id)
    print(f"  Fandt {len(catalogs)} aktive {butik}-kataloger")

    rows: list[dict] = []
    seen: set[str] = set()

    for cat in catalogs:
        cat_id = cat["id"]
        label = cat.get("label", cat_id)
        run_till = cat.get("run_till", "")[:10]
        offers = fetch_all_offers(cat_id)
        print(f"    {label} ({run_till}): {len(offers)} tilbud")
        rows.extend(_rows_from_offers(
            offers, cat_id, label, butik, seen, dedupe_by_heading=dedupe_by_heading,
        ))

    print(f"  OK: {len(rows)} {butik}-tilbud hentet fra Tjek API")
    return rows
