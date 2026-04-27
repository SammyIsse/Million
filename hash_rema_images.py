"""
hash_rema_images.py
-------------------
Downloader billeder fra Rema 1000's XML-feed og beregner perceptuelle
billedhasher (pHash).  Resultatet gemmes i  data/rema_hashes.json  som:

    { "<product_id>": "<hash_hex>", ... }

app.py læser automatisk denne fil ved opstart.

Kør én gang (tager typisk 5-15 min afhængigt af antal produkter):
    python hash_rema_images.py

Scriptet er idempotent: eksisterende hashes springes over.
"""

import os
import sys
import json
import time
import requests
import xmltodict
import imagehash
import concurrent.futures
from PIL import Image
from io import BytesIO

XML_URL      = "https://cphapp.rema1000.dk/api/v1/products.xml"
OUTPUT_DIR   = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_PATH  = os.path.join(OUTPUT_DIR, "rema_hashes.json")
TIMEOUT      = 6
MAX_WORKERS  = 8
BATCH_PRINT  = 100

DEFAULT_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "da,da-DK;q=0.9,en;q=0.8",
}


def compute_hash(url: str) -> str:
    """Download billede og returner pHash-streng, eller '' ved fejl."""
    if not url or not str(url).startswith("http"):
        return ""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=DEFAULT_HTTP_HEADERS)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        return str(imagehash.phash(img))
    except Exception:
        return ""


def fetch_rema_products():
    """Henter Rema XML og returnerer liste af (id, imageLink)-tupler."""
    print(f"Henter XML fra {XML_URL} …")
    r = requests.get(XML_URL, timeout=60, headers=DEFAULT_HTTP_HEADERS)
    r.raise_for_status()
    xml_dict = xmltodict.parse(r.text)
    products = xml_dict.get("products", {}).get("product", [])
    if isinstance(products, dict):
        products = [products]
    result = []
    for p in products:
        pid   = str(p.get("id", "")).strip()
        url   = str(p.get("imageLink", "")).strip()
        price_str = str(p.get("price", "0")).replace("DKK", "").strip()
        try:
            price = float(price_str.replace(",", "."))
        except ValueError:
            price = 0.0
        if pid and url.startswith("http") and price > 0:
            result.append((pid, url))
    print(f"  {len(result)} Rema-produkter med billeder fundet")
    return result


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Indlæs eksisterende hashes
    existing: dict = {}
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
            print(f"Indlæste {len(existing)} eksisterende hashes fra {OUTPUT_PATH}")
        except Exception as e:
            print(f"Advarsel: Kunne ikke læse {OUTPUT_PATH}: {e}")

    products = fetch_rema_products()

    # Filtrer dem der allerede har en hash
    to_process = [(pid, url) for pid, url in products if not existing.get(pid)]
    total = len(to_process)
    print(f"  {len(products) - total} allerede hashet, {total} tilbage")

    if total == 0:
        print("Alle produkter er allerede hashet – afslutter.")
        return

    done   = 0
    start  = time.time()
    new_hashes: dict = {}

    print(f"Starter download af {total} billeder med {MAX_WORKERS} tråde …")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_pid = {
            executor.submit(compute_hash, url): pid
            for pid, url in to_process
        }
        for future in concurrent.futures.as_completed(future_to_pid):
            pid = future_to_pid[future]
            try:
                h = future.result()
            except Exception:
                h = ""
            if h:
                new_hashes[pid] = h
            done += 1
            if done % BATCH_PRINT == 0 or done == total:
                elapsed  = time.time() - start
                rate     = done / elapsed if elapsed > 0 else 0
                remaining = (total - done) / rate if rate > 0 else 0
                print(
                    f"  {done}/{total} ({100*done//total}%) | "
                    f"{len(new_hashes)} nye hashes | "
                    f"~{remaining:.0f}s tilbage"
                )
                # Gem løbende så vi ikke mister data ved fejl
                merged = {**existing, **new_hashes}
                with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=2)

    # Endelig gem
    merged = {**existing, **new_hashes}
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(
        f"\n✅ Færdig! {len(merged)} hashes i alt gemt til {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
