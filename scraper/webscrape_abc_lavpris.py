"""
ABC Lavpris tilbudsavis via Tjek/ShopGun.

Primær kilde: dealer-API (70d42L) med 16 regionale ugentlige aviser, dedupliceret
på produktnavn. ABC viser avisen som JPG på abc-lavpris.dk — ikke Tjek-widget —
så fallback scanner butik-sider for evt. Tjek-katalog-ID'er (samme mønster som Løvbjerg).
"""
import os
import re
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client
from tjek_tilbud_scraper import (
    _HEADERS,
    fetch_tjek_tilbud,
    fetch_tjek_tilbud_from_catalog_ids,
    scrape_catalog_ids_from_pages,
)

ABC_DEALER_ID = "70d42L"
ABC_BASE_URL = "https://www.abc-lavpris.dk"
BUTIK = "ABC Lavpris"


def fetch_abc_store_page_urls() -> list[str]:
    """Find alle /butikker/{slug}-sider fra forsiden."""
    urls = [ABC_BASE_URL, f"{ABC_BASE_URL}/tilbudsaviser"]
    try:
        r = requests.get(ABC_BASE_URL, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        slugs = set(re.findall(r'href="/butikker/([a-z]+)"', r.text))
        urls.extend(f"{ABC_BASE_URL}/butikker/{slug}" for slug in sorted(slugs))
    except requests.RequestException as exc:
        print(f"  Kunne ikke hente butiksliste fra forsiden: {exc}")
    return urls


def fetch_abc_tilbud() -> list[dict]:
    rows = fetch_tjek_tilbud(ABC_DEALER_ID, BUTIK, dedupe_by_heading=True)
    if rows:
        return rows

    print("  Ingen kataloger via dealer-API — scanner abc-lavpris.dk for Tjek-katalog-ID'er")
    page_urls = fetch_abc_store_page_urls()
    catalog_ids = scrape_catalog_ids_from_pages(page_urls, ABC_DEALER_ID)
    if not catalog_ids:
        print("  Ingen Tjek-katalog-ID'er fundet på abc-lavpris.dk")
        return []

    print(f"  Fundet {len(catalog_ids)} katalog-ID(er): {', '.join(catalog_ids)}")
    return fetch_tjek_tilbud_from_catalog_ids(catalog_ids, BUTIK, dedupe_by_heading=True)


def save_to_supabase(rows: list[dict]):
    if not rows:
        print("  Ingen tilbud — beholder eksisterende ABC Lavpris-tilbud (intet slettet).")
        return
    client = get_client()
    (client.table("produkter").delete()
        .eq("butik", BUTIK)
        .neq("kategori", "Katalog")
        .execute())
    for i in range(0, len(rows), 500):
        client.table("produkter").insert(rows[i:i + 500]).execute()
    print(f"Gemt {len(rows)} rækker i Supabase for {BUTIK}")


def main():
    print("Starter ABC Lavpris scraper (Tjek API + abc-lavpris.dk fallback)...")
    rows = fetch_abc_tilbud()
    save_to_supabase(rows)
    print("\nFærdig!")


if __name__ == "__main__":
    main()
