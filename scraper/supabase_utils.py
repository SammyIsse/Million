import os
import sys
import time
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from app_support import attach_billede_hashes, compute_image_hash

_client = None


def enrich_billede_hashes(rows: list[dict]) -> None:
    """Beregn manglende billede_hash for dict-rækker før Supabase-gem."""
    attach_billede_hashes(rows)


def get_client():
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        _client = create_client(url, key)
    return _client


def _is_clock_skew_error(exc) -> bool:
    """PostgREST afviser JWT når runner-uret ligger foran serveren (PGRST303)."""
    code = getattr(exc, "code", "") or ""
    msg = str(getattr(exc, "message", "") or exc).lower()
    return code == "PGRST303" or "issued at future" in msg


def with_client_retry(fn, *, attempts: int = 4, base_delay: float = 1.5):
    """Kør fn(client) med retry ved JWT clock skew.

    GitHub Actions-runners kan have et ur lidt foran Supabase, så den første
    request fejler med PGRST303. Vi smider klienten væk og venter, så næste
    forsøg typisk lykkes. Andre fejl raiser med det samme.
    """
    global _client
    last = None
    for i in range(attempts):
        try:
            return fn(get_client())
        except Exception as e:
            if not _is_clock_skew_error(e):
                raise
            last = e
            _client = None
            delay = base_delay * (i + 1)
            print(f"  ⚠ Supabase clock skew (PGRST303), venter {delay:.1f}s ({i + 1}/{attempts})...")
            time.sleep(delay)
    raise last


def shrink_guard_ok(
    client,
    butik: str,
    new_count: int,
    *,
    kategori_eq: str | None = None,
    kategori_neq: str | None = None,
    min_ratio: float = 0.5,
    min_existing: int = 20,
) -> bool:
    """True hvis det er sikkert at erstatte butikkens data med new_count nye rækker.

    Eneste beskyttelse mod partiel-scraping-fejl var før dette scoped til
    Dagrofa-scraperen (dagrofa_scraper.py) - resten af projektet havde kun et
    "helt tomt"-tjek, som IKKE fanger fx en enkelt uparsebar pris, der giver
    12 varer ud af 250, eller et ændret markup-attribut, der stille halverer
    et sortiment. De 12/50 overskrev tidligere de 250/2000 uden fejl og uden
    alarm (se auditrapportens fund A.1.2/A.1.3).

    min_existing forhindrer at et tomt/nyt butiksnavn (0-19 rækker i forvejen)
    blokerer den allerførste rigtige scraping."""
    try:
        q = client.table("produkter").select("id", count="exact", head=True).eq("butik", butik)
        if kategori_eq is not None:
            q = q.eq("kategori", kategori_eq)
        if kategori_neq is not None:
            q = q.neq("kategori", kategori_neq)
        existing = q.execute().count or 0
    except Exception as e:
        print(f"  ⚠ Kunne ikke tjekke eksisterende antal for {butik} ({e}) - fortsætter uden tærskel-tjek")
        return True
    if existing < min_existing:
        return True
    if new_count < existing * min_ratio:
        print(
            f"  ✗ {butik}: kun {new_count} nye varer mod {existing} eksisterende "
            f"(under {min_ratio:.0%}) - gemmer IKKE (sandsynlig scraping-fejl)"
        )
        return False
    return True


def save_product_dicts(
    butik: str,
    rows: list[dict],
    *,
    delete_eq_kategori: str | None = None,
    delete_neq_kategori: str | None = None,
) -> None:
    """Slet+indsæt dict-rækker for én butik, atomisk via staging+swap-RPC.

    delete_eq_kategori/delete_neq_kategori er gensidigt udelukkende og scoper
    sletningen til en kategori-delmængde af butikken (fx en tilbudsavis-scraper
    der ikke må røre en separat katalog-scrapers rækker for samme butik).
    Samme mønster som save_to_supabase() nedenfor - se dens kommentarer for
    hvorfor staging+swap findes, og hvorfor kun "funktionen findes ikke"
    udløser den gamle to-kalds-fallback.

    Raiser (i stedet for blot at logge og returnere) når intet gemmes, fordi
    hverken bilka_katalog.py, netto_katalog.py, foetex_katalog.py m.fl. tjekkede
    returværdien - deres main() printede "Færdig! N produkter gemt" og
    afsluttede med kode 0 UANSET om denne funktion reelt havde gemt noget.
    Et site der stille returnerer 0/delvise resultater (uden HTTP-fejl) fik
    dermed aldrig et rødt GitHub Actions-job, og butikken kunne blive
    forældet ubemærket i ugevis (produktionsrevision 18-08-2026, blokerer #3).
    Et rejst RuntimeError får processen til at afslutte med fejlkode, så
    workflowet fejler synligt - ligesom dagrofa_scraper.py's egne tærskler."""
    if not rows:
        raise RuntimeError(
            f"Ingen varer scrapet for {butik} - gemmer IKKE. Butikken beholder sine "
            f"eksisterende data, men jobbet fejler bevidst i stedet for at afslutte "
            f"stille, saa en tom scraping ikke gaar ubemaerket hen."
        )

    if not shrink_guard_ok(
        get_client(), butik, len(rows),
        kategori_eq=delete_eq_kategori, kategori_neq=delete_neq_kategori,
    ):
        raise RuntimeError(
            f"{butik}: for faa nye varer mod eksisterende antal (shrink-vaern) - "
            f"gemmer IKKE. Se advarslen ovenfor for det praecise antal. Butikken "
            f"beholder sine eksisterende data, men jobbet fejler bevidst i stedet "
            f"for at afslutte stille."
        )

    staging = f"__staging__{butik}"
    for r in rows:
        r["butik"] = staging

    def _insert_staging(c):
        # Ryd evt. rester fra en tidligere fejlet kørsel
        c.table("produkter").delete().eq("butik", staging).execute()
        for i in range(0, len(rows), 500):
            c.table("produkter").insert(rows[i:i + 500]).execute()

    try:
        with_client_retry(_insert_staging)
    except Exception:
        try:
            with_client_retry(
                lambda c: c.table("produkter").delete().eq("butik", staging).execute()
            )
        except Exception:
            pass
        raise

    scoped = delete_eq_kategori is not None or delete_neq_kategori is not None
    try:
        if scoped:
            with_client_retry(
                lambda c: c.rpc(
                    "swap_produkter_butik_scoped",
                    {
                        "target_butik": butik,
                        "staging_butik": staging,
                        "kategori_eq": delete_eq_kategori,
                        "kategori_neq": delete_neq_kategori,
                    },
                ).execute()
            )
        else:
            with_client_retry(
                lambda c: c.rpc(
                    "swap_produkter_butik",
                    {"target_butik": butik, "staging_butik": staging},
                ).execute()
            )
    except Exception as e:
        # Kun "funktionen findes ikke" maa udloese den gamle to-kalds-metode -
        # se save_to_supabase()'s kommentar for hvorfor ENHVER anden fejl i
        # stedet skal afbryde med data uroert.
        code = getattr(e, "code", "") or ""
        message = str(getattr(e, "message", "") or e)
        missing_function = code == "PGRST202" or "does not exist" in message.lower() \
            or "could not find the function" in message.lower()
        if not missing_function:
            fn = "swap_produkter_butik_scoped" if scoped else "swap_produkter_butik"
            print(f"  ✗ {fn} fejlede ({code or 'ukendt'}): {message}")
            print(f"    {butik} beholder sine nuvaerende data - staging ryddes ved naeste koersel.")
            raise
        # Funktionen er ikke oprettet endnu - koer scripts/supabase-produkter-swap.sql
        # (og scripts/supabase-produkter-swap-scoped.sql for den scopede variant)
        # for atomisk swap. Indtil da bruges den gamle to-kalds-metode, som har et
        # kort (men sjaeldent ramt) vindue uden data hvis netvaerket doer mellem kaldene.
        fn = "swap_produkter_butik_scoped" if scoped else "swap_produkter_butik"
        print(f"  ⚠ {fn}-funktion mangler, bruger gammel swap-metode ({message})")

        def _legacy_swap(c):
            q = c.table("produkter").delete().eq("butik", butik)
            if delete_eq_kategori is not None:
                q = q.eq("kategori", delete_eq_kategori)
            elif delete_neq_kategori is not None:
                q = q.neq("kategori", delete_neq_kategori)
            q.execute()
            # Ingen kategori-filter her: de staged raekker er allerede noejagtigt
            # den nye delmaengde, saa alle raekker under staging-navnet skal omdoebes.
            c.table("produkter").update({"butik": butik}).eq("butik", staging).execute()

        with_client_retry(_legacy_swap)

    print(f"✅ {len(rows)} rækker gemt i Supabase for {butik}")


def fetch_existing_products(butik):
    """
    Returnerer en cache der kan slås op på to måder:
      cache[ean]        → til scrapers der har EAN fra URL (Meny, Spar, minkøbmand)
      cache[navn_lower] → til Bilka, der skal bruge navn for at undgå Selenium-kald
    Begge peger på {varenummer, billede_hash, billede_url}.
    """
    client = get_client()
    try:
        # PostgREST returnerer hoejst 1000 raekker pr. kald (verificeret mod
        # projektet: et kald med limit=3000 gav praecis 1000 tilbage). Uden
        # paginering blev cachen stille afkortet for enhver butik med over
        # 1000 varer, saa billede_hash blev genberegnet for resten hver koersel.
        page_size = 1000
        offset = 0
        rows = []
        while True:
            resp = (
                client.table("produkter")
                .select("navn,varenummer,billede_hash,billede_url")
                .eq("butik", butik)
                .order("id")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            batch = resp.data or []
            rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        cache = {}
        for row in rows:
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
    # Sikkerhed: en tom scraping må ALDRIG slette eksisterende data.
    # Tilbudsaviser (fx 365discount) kan være tomme mellem avis-perioder.
    if not results:
        print(f"⚠ Ingen varer at gemme for {butik} - beholder eksisterende data (intet slettet)")
        return

    rows = []

    for row in results:
        img_url = str(row[8] or '').replace(',e_grayscale', '')
        img_hash = row[9] or ''
        if not img_hash and img_url:
            img_hash = compute_image_hash(img_url)
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
                "billede_hash": img_hash,
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
                "billede_hash": img_hash,
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
                "billede_hash": img_hash,
                "tilbud":       str(row[10]),
            }
        rows.append(record)

    if not rows:
        print(f"⚠ Ingen gyldige rækker for {butik} efter filtrering - beholder eksisterende data (intet slettet)")
        return

    if not shrink_guard_ok(get_client(), butik, len(rows)):
        return

    # Indsæt under et staging-navn først, så eksisterende data aldrig røres,
    # hvis et insert-batch fejler midtvejs (netværk/kvote). Til sidst swappes:
    # slet gamle rækker og omdøb staging til det rigtige butiksnavn - to
    # hurtige kald i stedet for et langt slet-før-indsæt-vindue uden data.
    staging = f"__staging__{butik}"
    for r in rows:
        r["butik"] = staging

    def _insert_staging(c):
        # Ryd evt. rester fra en tidligere fejlet kørsel
        c.table("produkter").delete().eq("butik", staging).execute()
        for i in range(0, len(rows), 500):
            c.table("produkter").insert(rows[i:i + 500]).execute()

    try:
        with_client_retry(_insert_staging)
    except Exception:
        try:
            with_client_retry(
                lambda c: c.table("produkter").delete().eq("butik", staging).execute()
            )
        except Exception:
            pass
        raise

    try:
        with_client_retry(
            lambda c: c.rpc(
                "swap_produkter_butik",
                {"target_butik": butik, "staging_butik": staging},
            ).execute()
        )
    except Exception as e:
        # Kun "funktionen findes ikke" maa udloese den gamle to-kalds-metode.
        # Tidligere fangede denne gren ENHVER fejl, og det er farligt: hvis
        # RPC'en lykkes server-side men svaret timer ud, er staging-raekkerne
        # allerede omdoebt - saa ville fallbacken slette de netop indsatte data
        # og bagefter ikke finde nogen staging-raekker at omdoebe. Resultatet
        # er en toemt butik. Ved alt andet end en manglende funktion afbryder
        # vi i stedet: staging-raekkerne ryddes ved naeste koersel, og butikkens
        # nuvaerende data staar uroert tilbage.
        code = getattr(e, "code", "") or ""
        message = str(getattr(e, "message", "") or e)
        missing_function = code == "PGRST202" or "does not exist" in message.lower() \
            or "could not find the function" in message.lower()
        if not missing_function:
            print(f"  ✗ swap_produkter_butik fejlede ({code or 'ukendt'}): {message}")
            print(f"    {butik} beholder sine nuvaerende data - staging ryddes ved naeste koersel.")
            raise
        # Funktionen er ikke oprettet endnu - koer scripts/supabase-produkter-swap.sql
        # for atomisk swap. Indtil da bruges den gamle to-kalds-metode, som har et
        # kort (men sjaeldent ramt) vindue uden data hvis netvaerket doer mellem kaldene.
        print(f"  ⚠ swap_produkter_butik-funktion mangler, bruger gammel swap-metode ({message})")

        def _legacy_swap(c):
            c.table("produkter").delete().eq("butik", butik).execute()
            c.table("produkter").update({"butik": butik}).eq("butik", staging).execute()

        with_client_retry(_legacy_swap)

    print(f"✅ {len(rows)} rækker gemt i Supabase for {butik}")
