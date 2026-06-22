import os
import sys
import requests

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client

BASE_URL = "https://api.sallinggroup.com"
BRANDS = ["foetex", "netto"]
BRAND_LABEL = {"foetex": "Føtex", "netto": "Netto"}


def get_headers():
    api_key = os.environ.get("SALLING_API_KEY")
    if not api_key:
        raise RuntimeError("SALLING_API_KEY mangler i miljøvariable")
    return {"Authorization": f"Bearer {api_key}"}


def fetch_food_waste_by_brand(brand: str) -> list[dict]:
    """Henter alle madspild-varer for en brand direkte — kræver kun Food Waste API scope."""
    url = f"{BASE_URL}/v1/food-waste"
    params = {"brand": brand}
    headers = get_headers()
    api_key = os.environ.get("SALLING_API_KEY", "")
    print(f"  [debug] API-nøgle sat: {bool(api_key)}, længde: {len(api_key)}")
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    print(f"  [debug] HTTP {resp.status_code} — svar: {resp.text[:300]}")
    resp.raise_for_status()
    data = resp.json()
    # Returnerer liste af { store: {...}, clearance: [...] }
    if isinstance(data, list):
        return data
    return []


def build_rows(stores_data: list[dict], brand: str) -> list[dict]:
    butik_label = BRAND_LABEL[brand]
    rows = []
    for entry in stores_data:
        store = entry.get("store", {})
        store_name = store.get("name", "")
        city = store.get("city", "")
        clearance = entry.get("clearance", [])
        for item in clearance:
            offer = item.get("offer", {})
            ean = offer.get("ean") or offer.get("id") or ""
            navn = offer.get("description", "")
            pris = offer.get("newPrice")
            normalpris = offer.get("originalPrice")
            billede_url = offer.get("image", "")
            discount_pct = offer.get("percentDiscount")
            stock = item.get("stock", {})
            antal = stock.get("quantity") if isinstance(stock, dict) else None
            end_time = offer.get("endTime", "")

            tilbud_str = f"{discount_pct}% rabat" if discount_pct else ""
            enhed = f"Antal: {antal}" if antal is not None else (end_time[:10] if end_time else "")

            rows.append({
                "butik":        butik_label,
                "kategori":     f"Madspild – {store_name}, {city}".strip("– ,"),
                "navn":         navn,
                "producent":    None,
                "netto_vaegt":  None,
                "kg_price":     None,
                "pris":         float(pris) if pris is not None else None,
                "normalpris":   str(normalpris) if normalpris is not None else None,
                "varenummer":   str(ean) if ean else None,
                "billede_url":  billede_url,
                "billede_hash": None,
                "tilbud":       tilbud_str,
                "enhed":        enhed,
            })
    return rows


def save_rows(rows: list[dict], butik_label: str):
    if not rows:
        print(f"  ⚠ Ingen madspild-varer fundet for {butik_label}")
        return
    client = get_client()
    client.table("produkter").delete().eq("butik", butik_label).execute()
    for i in range(0, len(rows), 500):
        client.table("produkter").insert(rows[i:i+500]).execute()
    print(f"  ✅ {len(rows)} madspild-varer gemt for {butik_label}")


def scrape_brand(brand: str):
    butik_label = BRAND_LABEL[brand]
    print(f"\n── {butik_label} ──")
    stores_data = fetch_food_waste_by_brand(brand)
    total_stores = len(stores_data)
    total_items = sum(len(e.get("clearance", [])) for e in stores_data)
    print(f"  ✓ {total_stores} butikker, {total_items} madspild-varer")
    rows = build_rows(stores_data, brand)
    save_rows(rows, butik_label)


def main():
    print("Starter Salling Group madspild-scraper (Føtex + Netto)...")
    for brand in BRANDS:
        scrape_brand(brand)
    print("\nFærdig!")


if __name__ == "__main__":
    main()
