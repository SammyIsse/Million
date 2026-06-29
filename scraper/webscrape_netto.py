import os
import sys
import re
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client

TJEK_BASE = "https://squid-api.tjek.com"
NETTO_DEALER_ID = "9ba51"

# Kataloger der udelukkende indeholder ikke-mad
_NON_FOOD_CATALOG = [
    'sommersk', 'skønhed', 'beauty', 'non-food', 'helse og pleje',
    'kæledyr', 'dyr og natur', 'dyremad', 'husholdning', 'rengøring',
    'personlig pleje', 'pleje', 'tøj', 'sko', 'sport', 'fritid',
    'elektronik', 'legetøj', 'blomster', 'have', 'haven', 'udendørs',
    'tekstil', 'sengetøj', 'køkkengrej', 'service og bestik',
]

# Produktnavne der indikerer ikke-mad
_NON_FOOD_KEYWORDS = [
    'shampoo', 'balsam', 'deodorant', 'tandpasta', 'tandbørste',
    'solcreme', 'sollotion', 'solspray', 'solfaktor',
    'mascara', 'neglelak', 'parfume', 'makeupfjern',
    'vaskemiddel', 'opvaskemiddel', 'skyllemiddel', 'tøjvask', 'vaskekapsler', 'opvasketabs',
    'toiletpapir', 'køkkenrulle', 'køkken rulle',
    'bleer', 'vådserviet', 'babybleer',
    'proteinpulver', 'whey',
    'hudpleje', 'shower gel', 'brusegel', 'håndsæbe',
    'hundemad', 'kattemad', 'kattefoder', 'hundesnack', 'kattegrus', 'pedigree', 'whiskas', 'felix',
    'stearinlys', 'fyrfadslys', 'kronelys',
    'stegepande', 'gryde', 'kaffemaskine', 'elkedel',
    'sneakers', 't-shirt', 'solbriller', 'badeklæde',
    'blomster', 'plante', 'potteplante', 'gødning',
    'batterier', 'lyspære', 'pærer',
    'vitaminer', 'kosttilskud', 'proteinbar',
    # Møbler, have, tekstil & sæson
    'havestol', 'spisebordsstol', 'lænestol', 'liggestol', 'klapstol',
    'gyngestol', 'havebord', 'sofabord', 'spisebord', 'havemøbel', 'parasol',
    'krukke', 'trolley', 'telt', 'slipper', 'hjemmesko', 'kasket', 'uneflex',
    'nissehave', 'gadekridt', 'sengetøj',
]


def is_food(heading: str, catalog_label: str | None) -> bool:
    label = (catalog_label or "").lower()
    if any(p in label for p in _NON_FOOD_CATALOG):
        return False
    h = heading.lower()
    return not any(kw in h for kw in _NON_FOOD_KEYWORDS)


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
                "tilbud":       "Ja",
                "multikob":     None,
            })

    print(f"  OK: {len(rows)} Netto tilbud hentet fra Tjek API")
    return rows


# ── Gem til Supabase ──────────────────────────────────────────────────────────

def save_to_supabase(rows: list[dict]):
    client = get_client()
    client.table("produkter").delete().eq("butik", "Netto").execute()
    for i in range(0, len(rows), 500):
        client.table("produkter").insert(rows[i:i+500]).execute()
    print(f"Gemt {len(rows)} rækker i Supabase for Netto")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Starter Netto scraper (Tjek API)...")
    rows = fetch_netto_tilbud()
    save_to_supabase(rows)
    print("\nFærdig!")


if __name__ == "__main__":
    main()
