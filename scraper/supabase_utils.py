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
                "billede_url":  row[8],
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
                "billede_url":  row[8],
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
                "billede_url":  row[8],
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
