import os
import sys
import re
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client

TJEK_BASE = "https://squid-api.tjek.com"
NETTO_DEALER_ID = "9ba51"
SALLING_BASE_URL = "https://api.sallinggroup.com"


# ── Parsing ───────────────────────────────────────────────────────────────────

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


# ── Tjek/ShopGun API ──────────────────────────────────────────────────────────

def fetch_active_catalogs() -> list[dict]:
    r = requests.get(
        f"{TJEK_BASE}/v2/catalogs",
        params={"dealer_id": NETTO_DEALER_ID, "limit": 20},
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


def fetch_netto_tilbud() -> list[dict]:
    catalogs = fetch_active_catalogs()
    print(f"  Fandt {len(catalogs)} aktive Netto-kataloger")

    rows: list[dict] = []
    seen: set[str] = set()

    for cat in catalogs:
        cat_id = cat["id"]
        label = cat.get("label", cat_id)
        run_till = cat.get("run_till", "")[:10]
        offers = fetch_all_offers(cat_id)
        print(f"    {label} ({run_till}): {len(offers)} tilbud")

        for o in offers:
            heading = o.get("heading", "")
            key = f"{cat_id}|{heading}"
            if key in seen:
                continue
            seen.add(key)

            desc = o.get("description", "")
            p_type, weight, kg_price = parse_description(desc)

            pricing = o.get("pricing", {})
            pris = pricing.get("price")
            pre_price = pricing.get("pre_price")
            # Alle Tjek API-produkter er katalogtilbud (ugeavis) → altid tilbud
            is_sale = True

            img = o.get("images", {})
            billede_url = img.get("view") or img.get("thumb") or ""

            rows.append({
                "butik":        "Netto",
                "kategori":     label,
                "navn":         heading,
                "producent":    p_type or None,
                "netto_vaegt":  weight or None,
                "kg_price":     kg_price or None,
                "pris":         float(pris) if pris is not None else None,
                "normalpris":   str(pre_price) if pre_price is not None else None,
                "varenummer":   None,
                "billede_url":  billede_url,
                "billede_hash": None,
                "tilbud":       "Ja" if is_sale else "Nej",
                "multikob":     None,
            })

    print(f"  OK: {len(rows)} Netto tilbud hentet fra Tjek API")
    return rows


# ── Salling API: Netto madspild ───────────────────────────────────────────────

def _salling_headers() -> dict:
    api_key = os.environ.get("SALLING_API_KEY")
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def fetch_netto_food_waste() -> list[dict]:
    headers = _salling_headers()
    if not headers:
        print("  ! SALLING_API_KEY ikke sat - springer Netto madspild over")
        return []

    resp = requests.get(
        f"{SALLING_BASE_URL}/v2/stores",
        headers=headers, params={"brand": "netto", "country": "dk"}, timeout=30,
    )
    if resp.status_code != 200:
        print(f"  ! Kunne ikke hente Netto butikker: {resp.status_code}")
        return []

    stores = resp.json()
    if not isinstance(stores, list):
        stores = stores.get("items", stores.get("stores", []))

    print(f"  Fandt {len(stores)} Netto butikker (madspild)")
    rows: list[dict] = []
    seen: set[str] = set()

    for store in stores:
        store_id = store.get("id", "")
        if not store_id:
            continue
        try:
            fw_resp = requests.get(
                f"{SALLING_BASE_URL}/v1/food-waste/{store_id}",
                headers=headers, timeout=30,
            )
            if fw_resp.status_code != 200:
                continue
            data = fw_resp.json()
        except Exception as e:
            print(f"    ! Fejl ved butik {store_id}: {e}")
            continue

        store_name = store.get("name", "")
        city = store.get("city", "")
        for item in data.get("clearance", []):
            offer = item.get("offer", {})
            navn = offer.get("description", "")
            pris = offer.get("newPrice")
            normalpris = offer.get("originalPrice")
            discount_pct = offer.get("percentDiscount")
            ean = offer.get("ean") or offer.get("id") or ""
            billede_url = offer.get("image", "")
            stock = item.get("stock", {})
            antal = stock.get("quantity") if isinstance(stock, dict) else None
            end_time = offer.get("endTime", "")

            key = f"madspild|{store_name}|{navn}"
            if key in seen:
                continue
            seen.add(key)

            rows.append({
                "butik":        "Netto",
                "kategori":     f"Madspild - {store_name}, {city}".strip("- ,"),
                "navn":         navn,
                "producent":    None,
                "netto_vaegt":  None,
                "kg_price":     None,
                "pris":         float(pris) if pris is not None else None,
                "normalpris":   str(normalpris) if normalpris is not None else None,
                "varenummer":   str(ean) if ean else None,
                "billede_url":  billede_url,
                "billede_hash": None,
                "tilbud":       f"{discount_pct}% rabat" if discount_pct else "",
                "enhed":        f"Antal: {antal}" if antal is not None else (end_time[:10] if end_time else ""),
                "multikob":     None,
            })

    print(f"  OK: {len(rows)} Netto madspild-varer hentet")
    return rows


# ── Gem til Supabase ──────────────────────────────────────────────────────────

def save_to_supabase(tilbud_rows: list[dict], food_waste_rows: list[dict]):
    client = get_client()
    records = tilbud_rows + food_waste_rows
    client.table("produkter").delete().eq("butik", "Netto").execute()
    for i in range(0, len(records), 500):
        client.table("produkter").insert(records[i:i+500]).execute()
    print(f"Gemt {len(records)} raekker i Supabase for Netto "
          f"({len(tilbud_rows)} tilbud + {len(food_waste_rows)} madspild)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Starter Netto scraper (Tjek API)...")

    print("\nHenter Netto tilbud fra Tjek/ShopGun API...")
    tilbud_rows = fetch_netto_tilbud()

    print("\nHenter Netto madspild fra Salling API...")
    food_waste_rows = fetch_netto_food_waste()

    save_to_supabase(tilbud_rows, food_waste_rows)
    print("\nFærdig!")


if __name__ == "__main__":
    main()
