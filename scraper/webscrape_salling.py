import os
import sys
import requests

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client

BASE_URL = "https://api.sallinggroup.com"

# Dækker hele Danmark: København, Odense, Aarhus, Aalborg, Esbjerg
DENMARK_ZIPS = ["1000", "5000", "8000", "9000", "6700"]
RADIUS_KM = 100

SALLING_BRANDS = {"foetex", "netto"}
BRAND_LABEL = {"foetex": "Føtex", "netto": "Netto"}


def get_headers():
    api_key = os.environ.get("SALLING_API_KEY")
    if not api_key:
        raise RuntimeError("SALLING_API_KEY mangler i miljøvariable")
    return {"Authorization": f"Bearer {api_key}"}


def fetch_food_waste_by_zip(zip_code: str) -> list[dict]:
    """Henter madspild-butikker nær et postnummer."""
    url = f"{BASE_URL}/v1/food-waste"
    params = {"zip": zip_code, "radius": RADIUS_KM}
    resp = requests.get(url, headers=get_headers(), params=params, timeout=30)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def build_rows(stores_data: list[dict]) -> dict[str, list[dict]]:
    """Returnerer dict med brand -> liste af rækker."""
    result: dict[str, list] = {label: [] for label in BRAND_LABEL.values()}
    seen_store_ids: set[str] = set()

    for entry in stores_data:
        store = entry.get("store", {})
        store_id = store.get("id", "")
        brand = store.get("brand", "").lower()

        if brand not in SALLING_BRANDS:
            continue
        if store_id in seen_store_ids:
            continue
        seen_store_ids.add(store_id)

        butik_label = BRAND_LABEL[brand]
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

            result[butik_label].append({
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
    return result


def save_rows(rows: list[dict], butik_label: str):
    if not rows:
        print(f"  ⚠ Ingen madspild-varer fundet for {butik_label}")
        return
    client = get_client()
    client.table("produkter").delete().eq("butik", butik_label).execute()
    for i in range(0, len(rows), 500):
        client.table("produkter").insert(rows[i:i+500]).execute()
    print(f"  ✅ {len(rows)} madspild-varer gemt for {butik_label}")


def main():
    print("Starter Salling Group madspild-scraper (Føtex + Netto)...")
    all_data: dict[str, list] = {label: [] for label in BRAND_LABEL.values()}
    seen_ids: set[str] = set()

    for zip_code in DENMARK_ZIPS:
        print(f"  Henter postnummer {zip_code}...")
        stores_data = fetch_food_waste_by_zip(zip_code)
        rows_by_brand = build_rows(stores_data)
        for label, rows in rows_by_brand.items():
            for row in rows:
                key = f"{row['kategori']}|{row['navn']}"
                if key not in seen_ids:
                    seen_ids.add(key)
                    all_data[label].append(row)

    for butik_label, rows in all_data.items():
        print(f"\n── {butik_label}: {len(rows)} varer ──")
        save_rows(rows, butik_label)

    print("\nFærdig!")


if __name__ == "__main__":
    main()
