"""
ABC Lavpris tilbudsavis via Tjek/ShopGun API.
Dealer ID: 70d42L — regional aviser dedupliceres på produktnavn.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client
from tjek_tilbud_scraper import fetch_tjek_tilbud

ABC_DEALER_ID = "70d42L"
BUTIK = "ABC Lavpris"


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
    print("Starter ABC Lavpris scraper (Tjek API)...")
    rows = fetch_tjek_tilbud(ABC_DEALER_ID, BUTIK, dedupe_by_heading=True)
    save_to_supabase(rows)
    print("\nFærdig!")


if __name__ == "__main__":
    main()
