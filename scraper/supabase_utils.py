import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

_client = None

def get_client():
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        _client = create_client(url, key)
    return _client


def fetch_existing_products(butik):
    """
    Returnerer en cache der kan slås op på to måder:
      cache[ean]        → til scrapers der har EAN fra URL (Meny, Spar, minkøbmand)
      cache[navn_lower] → til Bilka, der skal bruge navn for at undgå Selenium-kald
    Begge peger på {varenummer, billede_hash, billede_url}.
    """
    client = get_client()
    try:
        resp = client.table("produkter").select("navn,varenummer,billede_hash,billede_url").eq("butik", butik).execute()
        cache = {}
        for row in resp.data:
            ean  = row.get("varenummer") or ""
            navn = row.get("navn") or ""
            entry = {
                "varenummer":   ean,
                "billede_hash": row.get("billede_hash") or "",
                "billede_url":  row.get("billede_url") or "",
            }
            if ean:
                cache[ean] = entry          # EAN-opslag (Meny/Spar/minkøbmand)
            if navn:
                cache[navn.lower()] = entry  # Navn-opslag (Bilka)
        print(f"  ✓ Cache: {len(cache)} opslag hentet fra Supabase ({butik})")
        return cache
    except Exception as e:
        print(f"  ⚠ Kunne ikke hente produktcache: {e}")
        return {}


def save_to_supabase(results, butik, row_type="full"):
    """
    row_type:
      'full'    → Meny, Spar, minkøbmand: 11 kolonner
      'bilka'   → Bilka: 12 kolonner (med multikøb)
      'simple'  → 365discount, Brugsen, Kvickly, SuperBrugsen: 12 kolonner (med enhed)
    """
    client = get_client()
    rows = []

    for row in results:
        img_url = str(row[8] or '').replace(',e_grayscale', '')
        if row_type == "bilka":
            record = {
                "butik":        butik,
                "kategori":     row[0],
                "navn":         row[1],
                "producent":    row[2],
                "netto_vaegt":  row[3],
                "kg_price":     row[4],
                "pris":         float(row[5]) if row[5] else None,
                "normalpris":   str(row[6]) if row[6] != "" else None,
                "varenummer":   str(row[7]) if row[7] else None,
                "billede_url":  img_url,
                "billede_hash": row[9],
                "tilbud":       str(row[10]),
                "multikob":     row[11] if len(row) > 11 else None,
            }
        elif row_type == "simple":
            record = {
                "butik":        butik,
                "kategori":     row[0],
                "navn":         row[1],
                "producent":    row[2],
                "netto_vaegt":  row[3],
                "kg_price":     row[4],
                "pris":         float(row[5]) if row[5] else None,
                "normalpris":   str(row[6]) if row[6] != "" else None,
                "varenummer":   str(row[7]) if row[7] else None,
                "billede_url":  img_url,
                "billede_hash": row[9],
                "tilbud":       str(row[10]),
                "enhed":        row[11] if len(row) > 11 else None,
            }
        else:  # full
            record = {
                "butik":        butik,
                "kategori":     row[0],
                "navn":         row[1],
                "producent":    row[2],
                "netto_vaegt":  row[3],
                "kg_price":     row[4],
                "pris":         float(row[5]) if row[5] else None,
                "normalpris":   str(row[6]) if row[6] != "" else None,
                "varenummer":   str(row[7]) if row[7] else None,
                "billede_url":  img_url,
                "billede_hash": row[9],
                "tilbud":       str(row[10]),
            }
        rows.append(record)

    # Slet gamle data fra denne butik
    client.table("produkter").delete().eq("butik", butik).execute()

    # Indsæt i batches af 500
    for i in range(0, len(rows), 500):
        client.table("produkter").insert(rows[i:i+500]).execute()

    print(f"✅ {len(rows)} rækker gemt i Supabase for {butik}")
