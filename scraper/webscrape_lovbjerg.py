"""
Løvbjerg tilbudsavis via Tjek/ShopGun.

Løvbjerg lister ikke aktive kataloger via dealer-API (65caN returnerer []),
men denne uges avis embedder Tjek-widget med katalog-ID på:
  https://www.lovbjerg.dk/avis/denne-uges-avis
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client
from tjek_tilbud_scraper import (
    fetch_tjek_tilbud,
    fetch_tjek_tilbud_from_catalog_id,
    scrape_catalog_ids_from_pages,
)

LOEVBJERG_DEALER_ID = "65caN"
LOEVBJERG_AVIS_URL = "https://www.lovbjerg.dk/avis/denne-uges-avis"
BUTIK = "Løvbjerg"


def fetch_lovbjerg_tilbud() -> list[dict]:
    rows = fetch_tjek_tilbud(LOEVBJERG_DEALER_ID, BUTIK)
    if rows:
        return rows

    print("  Ingen kataloger via dealer-API - henter fra lovbjerg.dk avis-side")
    catalog_ids = scrape_catalog_ids_from_pages([LOEVBJERG_AVIS_URL], LOEVBJERG_DEALER_ID)
    if not catalog_ids:
        print("  Kunne ikke finde Tjek-katalog-ID på avis-siden")
        return []

    catalog_id = catalog_ids[0]
    print(f"  Fundet katalog-ID: {catalog_id}")
    return fetch_tjek_tilbud_from_catalog_id(catalog_id, BUTIK)


def save_to_supabase(rows: list[dict]):
    if not rows:
        print("  Ingen tilbud - beholder eksisterende Løvbjerg-tilbud (intet slettet).")
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
