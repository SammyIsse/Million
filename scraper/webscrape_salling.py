import os
import sys
import requests

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client

BASE_URL = "https://api.sallinggroup.com"


def get_headers():
    api_key = os.environ.get("SALLING_API_KEY")
    if not api_key:
        raise RuntimeError("SALLING_API_KEY mangler i miljøvariable")
    return {"Authorization": f"Bearer {api_key}"}


def fetch_stores(brand: str) -> list[dict]:
    """Henter alle butikker for et givent brand via /v2/stores."""
    url = f"{BASE_URL}/v2/stores"
    params = {"brand": brand, "country": "dk"}
    resp = requests.get(url, headers=get_headers(), params=params, timeout=30)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    return data.get("items", data.get("stores", []))


def fetch_food_waste(store_id: str) -> dict:
    """Henter madspild for en specifik butik via /v1/food-waste/{storeId}."""
    url = f"{BASE_URL}/v1/food-waste/{store_id}"
    resp = requests.get(url, headers=get_headers(), timeout=30)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return resp.json()


def build_rows(store: dict, clearance_items: list, butik_label: str) -> list[dict]:
    store_name = store.get("name", "")
    city = store.get("city", "")
    rows = []
    for item in clearance_items:
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


def scrape_brand_food_waste(brand: str, butik_label: str) -> list[dict]:
    """Henter madspild for alle butikker af et givent brand."""
    print(f"  Henter {butik_label} butikker...")
    stores = fetch_stores(brand)
    print(f"  Fandt {len(stores)} {butik_label} butikker")

    all_rows: list[dict] = []
    seen_keys: set[str] = set()

    for store in stores:
        store_id = store.get("id", "")
        if not store_id:
            continue
        try:
            data = fetch_food_waste(store_id)
        except Exception as e:
            print(f"    ⚠ Fejl ved butik {store_id}: {e}")
            continue
        clearance = data.get("clearance", [])
        if not clearance:
            continue
        rows = build_rows(store, clearance, butik_label)
        for row in rows:
            key = f"{row['kategori']}|{row['navn']}"
            if key not in seen_keys:
                seen_keys.add(key)
                all_rows.append(row)

    return all_rows


def main():
    print("Starter Salling Group madspild-scraper (Føtex)...")
    rows = scrape_brand_food_waste("foetex", "Føtex")
    print(f"\n── Føtex: {len(rows)} varer ──")
    save_rows(rows, "Føtex")
    print("\nFærdig!")


if __name__ == "__main__":
    main()
