import os
import sys
import requests
import time

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


def fetch_stores(brand: str) -> list[dict]:
    """Henter alle butikker for en given brand."""
    url = f"{BASE_URL}/v2/stores"
    stores = []
    params = {"brand": brand, "per_page": 100, "page": 1}
    while True:
        resp = requests.get(url, headers=get_headers(), params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        stores.extend(data)
        if len(data) < 100:
            break
        params["page"] += 1
    print(f"  ✓ Fandt {len(stores)} {brand}-butikker")
    return stores


def fetch_food_waste(store_id: str) -> list[dict]:
    """Henter madspild-tilbud for én butik via /v1/food-waste/{storeId}."""
    url = f"{BASE_URL}/v1/food-waste/{store_id}"
    try:
        resp = requests.get(url, headers=get_headers(), timeout=30)
        if resp.status_code in (404, 204):
            return []
        resp.raise_for_status()
        data = resp.json()
        # Svar er enten direkte liste af clearance-items eller en dict med "clearance" nøgle
        if isinstance(data, list):
            return data
        return data.get("clearance", [])
    except requests.HTTPError as e:
        print(f"    ⚠ HTTP-fejl for butik {store_id}: {e}")
        return []


def build_rows(clearance: list[dict], store: dict, brand: str) -> list[dict]:
    """Konverterer API-svar til rækker til Supabase."""
    butik_label = BRAND_LABEL[brand]
    store_name = store.get("name", "")
    city = store.get("city", "")
    rows = []
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
            "kategori":     f"Madspild – {store_name}, {city}".strip(", "),
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
    stores = fetch_stores(brand)
    all_rows = []
    for store in stores:
        store_id = store.get("id", "")
        clearance = fetch_food_waste(store_id)
        if clearance:
            rows = build_rows(clearance, store, brand)
            all_rows.extend(rows)
            print(f"    {store.get('name','?')} ({store.get('city','?')}): {len(rows)} varer")
        time.sleep(0.1)  # respekter rate limit
    save_rows(all_rows, butik_label)


def main():
    print("Starter Salling Group madspild-scraper (Føtex + Netto)...")
    for brand in BRANDS:
        scrape_brand(brand)
    print("\nFærdig!")


if __name__ == "__main__":
    main()
