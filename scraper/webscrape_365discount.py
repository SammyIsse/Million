import os
import sys
import re
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client
from keywords import is_non_food as _is_non_food

TJEK_BASE = "https://squid-api.tjek.com"
DISCOUNT365_DEALER_ID = "DWZE1w"
BUTIK = "365discount"

# Kataloger (Tjek API catalog labels) der er 100% ikke-mad
_NON_FOOD_CATALOG = [
    'sommersk', 'skønhed', 'beauty', 'non-food', 'helse og pleje',
    'kæledyr', 'dyr og natur', 'dyremad', 'husholdning', 'rengøring',
    'personlig pleje', 'pleje', 'tøj', 'sko', 'sport', 'fritid',
    'elektronik', 'legetøj', 'blomster', 'have', 'haven', 'udendørs',
    'tekstil', 'sengetøj', 'køkkengrej', 'service og bestik',
]


def is_food(heading: str, catalog_label: str | None) -> bool:
    label = (catalog_label or "").lower()
    if any(p in label for p in _NON_FOOD_CATALOG):
        return False
    return not _is_non_food(heading)


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


def fetch_active_catalogs() -> list[dict]:
    r = requests.get(
        f"{TJEK_BASE}/v2/catalogs",
        params={"dealer_id": DISCOUNT365_DEALER_ID, "limit": 20},
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


def fetch_365discount_tilbud() -> list[dict]:
    catalogs = fetch_active_catalogs()
    print(f"  Fandt {len(catalogs)} aktive 365discount-kataloger")

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
            if not heading:
                continue
            if not is_food(heading, label):
                continue
            key = f"{cat_id}|{heading}"
            if key in seen:
                continue
            seen.add(key)

            desc = o.get("description", "")
            p_type, weight, kg_price = parse_description(desc)

            pricing = o.get("pricing", {})
            pris = pricing.get("price")
            pre_price = pricing.get("pre_price")

            img = o.get("images", {})
            billede_url = img.get("view") or img.get("thumb") or ""

            rows.append({
                "butik":        BUTIK,
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
                "tilbud":       "Ja",
                "enhed":        None,
            })

    print(f"  OK: {len(rows)} 365discount tilbud hentet fra Tjek API")
    return rows


def save_to_supabase(rows: list[dict]):
    # Sikkerhed: tomt resultat må aldrig slette eksisterende data
    if not rows:
        print(f"⚠ Ingen varer at gemme for {BUTIK} — beholder eksisterende data (intet slettet)")
        return
    client = get_client()
    client.table("produkter").delete().eq("butik", BUTIK).execute()
    for i in range(0, len(rows), 500):
        client.table("produkter").insert(rows[i:i+500]).execute()
    print(f"Gemt {len(rows)} rækker i Supabase for {BUTIK}")


def main():
    print("Starter 365discount scraper (Tjek API)...")
    rows = fetch_365discount_tilbud()
    save_to_supabase(rows)
    print("\nFærdig!")


if __name__ == "__main__":
    main()
