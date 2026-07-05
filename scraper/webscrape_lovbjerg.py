"""
Løvbjerg tilbudsavis via Tjek/ShopGun.

Løvbjerg lister ikke aktive kataloger via dealer-API (65caN returnerer []),
men denne uges avis embedder Tjek-widget med katalog-ID på:
  https://www.lovbjerg.dk/avis/denne-uges-avis
"""
import os
import re
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client
from tjek_tilbud_scraper import fetch_tjek_tilbud, fetch_tjek_tilbud_from_catalog_id

LOEVBJERG_DEALER_ID = "65caN"
LOEVBJERG_AVIS_URL = "https://www.lovbjerg.dk/avis/denne-uges-avis"
BUTIK = "Løvbjerg"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "da,da-DK;q=0.9",
}


def fetch_catalog_id_from_avis_page() -> str | None:
    """Find Tjek-katalog-ID fra Løvbjergs avis-side (data-id på .tjek-widget)."""
    r = requests.get(LOEVBJERG_AVIS_URL, headers=_HEADERS, timeout=20)
    r.raise_for_status()
    m = re.search(r'class="tjek-widget"[^>]*data-id="([^"]+)"', r.text)
    if m:
        return m.group(1)
    m = re.search(r'data-business-id="65caN"[^>]*data-id="([^"]+)"', r.text)
    if m:
        return m.group(1)
    m = re.search(r'ID:([A-Za-z0-9_-]+)', r.text)
    return m.group(1) if m else None


def fetch_lovbjerg_tilbud() -> list[dict]:
    rows = fetch_tjek_tilbud(LOEVBJERG_DEALER_ID, BUTIK)
    if rows:
        return rows

    print("  Ingen kataloger via dealer-API — henter fra lovbjerg.dk avis-side")
    catalog_id = fetch_catalog_id_from_avis_page()
    if not catalog_id:
        print("  Kunne ikke finde Tjek-katalog-ID på avis-siden")
        return []

    print(f"  Fundet katalog-ID: {catalog_id}")
    return fetch_tjek_tilbud_from_catalog_id(catalog_id, BUTIK)


def save_to_supabase(rows: list[dict]):
    if not rows:
        print("  Ingen tilbud — beholder eksisterende Løvbjerg-tilbud (intet slettet).")
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
    print("Starter Løvbjerg scraper (Tjek API + lovbjerg.dk avis)...")
    rows = fetch_lovbjerg_tilbud()
    save_to_supabase(rows)
    print("\nFærdig!")


if __name__ == "__main__":
    main()
