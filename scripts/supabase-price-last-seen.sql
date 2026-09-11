-- Kør i Supabase SQL Editor.
--
-- price_history skiftede 11-09-2026 fra "én række pr. produkt/butik PR. DAG"
-- til "kun en ny række NÅR PRISEN ÆNDRER SIG" (event-log / sparse). Årsag:
-- ~38.500-39.200 rækker/dag fyldte tabellen (+ dens indekser) op til ~650 MB
-- efter 30 dages historik, og Supabase Free Plan har kun 0,5 GB i alt.
-- De fleste priser står stille fra dag til dag, så langt de fleste af de
-- rækker var rene dubletter af gårsdagens.
--
-- Denne fil opretter den lille, konstant-store tabel der gør det muligt:
-- price_last_seen holder ÉN række pr. (product_id, store) med den nyeste
-- kendte pris. updater.py::record_prices_batch() slår dagens skrabede priser
-- op mod den for at afgøre om noget ændrede sig, og opdaterer den for ALLE
-- skrabede par (ændrede såvel som uændrede) - dermed ved man også om et
-- produkt overhovedet stadig bliver scrapet (last_checked_date).
--
-- OBS: price_history.date er (stadig) text, ikke en rigtig date-kolonne -
-- se begrundelsen i supabase-normal-price.sql/supabase-lowest-price.sql.
-- Et ALTER COLUMN TYPE på en PK-kolonne i en tabel med ~1,2 mio. rækker
-- kræver en fuld table rewrite under ACCESS EXCLUSIVE-lock; det er bevidst
-- IKKE lavet her, for ikke at risikere endnu et af de kortvarige nedbrud
-- CLAUDE.md's § Miljøer & deploy advarer om. Kan gøres som en separat,
-- isoleret migration senere - når tabellen er skrumpet efter denne omlægning.

CREATE TABLE IF NOT EXISTS public.price_last_seen (
  product_id        text NOT NULL,
  store             text NOT NULL,
  price             double precision NOT NULL,
  last_checked_date date NOT NULL,
  PRIMARY KEY (product_id, store)
);

CREATE INDEX IF NOT EXISTS price_last_seen_checked_idx
  ON public.price_last_seen (last_checked_date);

-- Samme mønster som price_history (supabase-grants.sql): service_role
-- (DEPLOY_KEY, brugt af updater.py) har fuld adgang, anon/authenticated må
-- kun læse. RLS eksplicit slået til, ellers advarer Supabase' SQL Editor.
ALTER TABLE public.price_last_seen ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.price_last_seen;
CREATE POLICY "Service role fuld adgang"
  ON public.price_last_seen
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);

DROP POLICY IF EXISTS "Offentlig læsning" ON public.price_last_seen;
CREATE POLICY "Offentlig læsning"
  ON public.price_last_seen
  FOR SELECT
  TO anon, authenticated
  USING (true);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.price_last_seen TO service_role;
GRANT SELECT ON public.price_last_seen TO anon, authenticated;

-- ---------------------------------------------------------------------------
-- record_price_batch: diff'er én scraper-batch mod price_last_seen og gemmer
-- KUN de par hvor prisen faktisk ændrede sig i price_history, i ét sæt-baseret
-- kald i stedet for ~39.000 enkeltrækker om dagen.
--
-- payload er [{"product_id": "...", "store": "...", "price": 12.95}, ...].
-- Dagens dato sættes server-side (current_date) - ikke fra kalderen - så en
-- uret-forskel mellem GitHub Actions-runneren og Postgres aldrig kan give en
-- forkert dato i historikken.
--
-- SECURITY DEFINER + fast search_path, samme begrundelse som
-- record_cart_activity i supabase-cart-increment.sql. Kun service_role får
-- EXECUTE (kun updater.py's cache-opdatering skriver prishistorik - i
-- modsætning til cart_popularity er der ingen klient-kaldt sti hertil).
CREATE OR REPLACE FUNCTION public.record_price_batch(payload jsonb)
RETURNS TABLE(changed integer, seen integer)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  today date := current_date;
  v_changed integer;
  v_seen integer;
BEGIN
  WITH batch AS (
    SELECT
      (r->>'product_id')::text AS product_id,
      (r->>'store')::text AS store,
      (r->>'price')::double precision AS price
    FROM jsonb_array_elements(payload) AS r
  ),
  changed_rows AS (
    INSERT INTO public.price_history (product_id, store, price, date)
    SELECT b.product_id, b.store, b.price, today
    FROM batch b
    LEFT JOIN public.price_last_seen ls
      ON ls.product_id = b.product_id AND ls.store = b.store
    WHERE ls.product_id IS NULL OR ls.price IS DISTINCT FROM b.price
    ON CONFLICT (product_id, store, date) DO UPDATE
      SET price = excluded.price
    RETURNING 1
  )
  SELECT count(*) INTO v_changed FROM changed_rows;

  WITH batch AS (
    SELECT
      (r->>'product_id')::text AS product_id,
      (r->>'store')::text AS store,
      (r->>'price')::double precision AS price
    FROM jsonb_array_elements(payload) AS r
  ),
  seen_rows AS (
    INSERT INTO public.price_last_seen (product_id, store, price, last_checked_date)
    SELECT product_id, store, price, today FROM batch
    ON CONFLICT (product_id, store) DO UPDATE
      SET price = excluded.price,
          last_checked_date = excluded.last_checked_date
    RETURNING 1
  )
  SELECT count(*) INTO v_seen FROM seen_rows;

  RETURN QUERY SELECT v_changed, v_seen;
END;
$$;

GRANT EXECUTE ON FUNCTION public.record_price_batch(jsonb) TO service_role;

-- ---------------------------------------------------------------------------
-- prune_price_history: erstatter det tidligere inline DELETE i
-- updater.py::record_prices_batch() (som slettede ALT ældre end 30 dage,
-- uden hensyn til at grafens forward-fill har brug for et startpunkt).
--
-- Beholder netop ÉN "anker"-række pr. (product_id, store) fra før grænsen -
-- den nyeste af de gamle - og sletter resten. DISTINCT ON-fremgangsmåden er
-- valgt frem for opgavebeskrivelsens korrelerede subquery-eksempel, fordi den
-- sidste er O(n²)-agtig og for langsom mod en tabel i denne størrelse
-- (1,2+ mio. rækker ved første kørsel, hvor næsten alt endnu er ældre end
-- grænsen).
CREATE OR REPLACE FUNCTION public.prune_price_history(retain_days integer DEFAULT 30)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  cutoff date := current_date - retain_days;
  deleted_count integer;
BEGIN
  WITH anchors AS (
    SELECT DISTINCT ON (product_id, store)
      product_id, store, date::date AS anchor_date
    FROM public.price_history
    WHERE date::date < cutoff
    ORDER BY product_id, store, date::date DESC
  )
  DELETE FROM public.price_history ph
  USING anchors a
  WHERE ph.product_id = a.product_id
    AND ph.store = a.store
    AND ph.date::date < cutoff
    AND ph.date::date <> a.anchor_date;

  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  RETURN deleted_count;
END;
$$;

GRANT EXECUTE ON FUNCTION public.prune_price_history(integer) TO service_role;
