-- Kør i Supabase SQL Editor, én gang - EFTER scripts/supabase-hardening.sql
-- og scripts/supabase-dev-tables.sql (begge skal have kørt før denne).
--
-- Baggrund: databaserevision 17-08-2026 (fund M1). increment_cart_count(s) og
-- record_cart_activity validerer payloadens FORM (id-længde ≤64, antal varer
-- ≤50, qty 1-99), men har INGEN cooldown - i modsætning til create_price_alert
-- (1 kald/sek pr. bruger, se supabase-price-alerts-throttle.sql) og
-- record_recipe_click (1 sek pr. opskrift, se supabase-recipe-clicks.sql).
-- Alle tre RPC'er er GRANTet til anon og kan derfor kaldes direkte via
-- PostgREST udenom Flasks cart_event_limiter (20/min/IP, app_support.py) -
-- en angriber kunne dermed puste ét vilkårligt produkts popularitet op
-- ubegrænset, hvilket direkte fodrer forsidens "Brugernes Favoritter".
--
-- cart_popularity har ingen bruger-kolonne (hændelser er anonyme MED VILJE,
-- se begrundelsen i supabase-cart-increment.sql), så en pr.-bruger-cooldown
-- som create_price_alert er ikke mulig her. Løsningen følger i stedet samme
-- mønster som record_recipe_click: en pr.-RÆKKE cooldown via kolonnens egen
-- updated_at, håndhævet i selve UPDATE-klausulens WHERE. Det begrænser hvor
-- hurtigt ÉT produkts tæller kan stige - uanset hvem der kalder - til højst
-- 1 gang i sekundet. Det er rigeligt til ægte trafik (ingen bruger klikker
-- "læg i kurv" på samme produkt hurtigere end det) og gør automatiseret
-- oppustning upraktisk, uden at ændre funktionalitet for rigtige brugere.
--
-- Bemærk: cart_events (tidsaggregatet) er bevidst IKKE omfattet - den tabel
-- er lukket for anon-læsning og er ikke selv angrebsfladen; det er kun
-- cart_popularity, der er offentligt læsbar og fodrer UI'et.

-- ===========================================================================
-- Produktion: cart_popularity
-- ===========================================================================
ALTER TABLE public.cart_popularity
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE OR REPLACE FUNCTION public.increment_cart_count(pid text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF pid IS NULL OR pid = '' OR length(pid) > 64 THEN
    RETURN;
  END IF;
  INSERT INTO public.cart_popularity (product_id, count, updated_at)
  VALUES (pid, 1, now())
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity.count + 1,
      updated_at = now()
  WHERE cart_popularity.updated_at < now() - interval '1 second';
END;
$$;

CREATE OR REPLACE FUNCTION public.increment_cart_counts(pids text[])
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF pids IS NULL THEN
    RETURN;
  END IF;
  -- DISTINCT er påkrævet: uden den fejler ON CONFLICT med "cannot affect row
  -- a second time", hvis samme id optræder to gange i arrayet.
  INSERT INTO public.cart_popularity (product_id, count, updated_at)
  SELECT pid, 1, now() FROM (
    SELECT DISTINCT t.pid
    FROM unnest(pids) AS t(pid)
    WHERE t.pid IS NOT NULL AND t.pid <> '' AND length(t.pid) <= 64
    LIMIT 50
  ) AS q
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity.count + 1,
      updated_at = now()
  WHERE cart_popularity.updated_at < now() - interval '1 second';
END;
$$;

CREATE OR REPLACE FUNCTION public.record_cart_activity(
  items jsonb,
  etype text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  bucket timestamp := date_trunc('hour', timezone('Europe/Copenhagen', now()));
  w integer;
BEGIN
  w := CASE etype WHEN 'compare' THEN 3 WHEN 'add' THEN 1 ELSE NULL END;
  IF w IS NULL THEN
    RETURN;
  END IF;

  WITH cleaned AS (
    SELECT i.pid,
           LEAST(GREATEST(COALESCE(i.qty, 1), 1), 99) AS qty
    FROM jsonb_to_recordset(items) AS i(pid text, qty int)
    WHERE i.pid IS NOT NULL AND i.pid <> '' AND length(i.pid) <= 64
    LIMIT 50
  ),
  agg AS (
    SELECT pid, count(*)::int AS events, sum(qty)::int AS qty
    FROM cleaned
    GROUP BY pid
  ),
  pop AS (
    INSERT INTO public.cart_popularity (product_id, count, updated_at)
    SELECT pid, w, now() FROM agg
    ON CONFLICT (product_id) DO UPDATE
    SET count = cart_popularity.count + w,
        updated_at = now()
    WHERE cart_popularity.updated_at < now() - interval '1 second'
  )
  INSERT INTO public.cart_events (product_id, hour, event_type, events, qty)
  SELECT pid, bucket, etype, events, qty FROM agg
  ON CONFLICT (product_id, hour, event_type) DO UPDATE
  SET events = cart_events.events + EXCLUDED.events,
      qty    = cart_events.qty + EXCLUDED.qty;
END;
$$;

-- ===========================================================================
-- Dev/staging: cart_popularity_dev (samme ændringer, TABLE_SUFFIX=_dev)
-- ===========================================================================
DO $$
BEGIN
  IF to_regclass('public.cart_popularity_dev') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE public.cart_popularity_dev
               ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION public.increment_cart_count_dev(pid text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF pid IS NULL OR pid = '' OR length(pid) > 64 THEN
    RETURN;
  END IF;
  INSERT INTO public.cart_popularity_dev (product_id, count, updated_at)
  VALUES (pid, 1, now())
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity_dev.count + 1,
      updated_at = now()
  WHERE cart_popularity_dev.updated_at < now() - interval '1 second';
END;
$$;

CREATE OR REPLACE FUNCTION public.increment_cart_counts_dev(pids text[])
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF pids IS NULL THEN
    RETURN;
  END IF;
  INSERT INTO public.cart_popularity_dev (product_id, count, updated_at)
  SELECT pid, 1, now() FROM (
    SELECT DISTINCT t.pid
    FROM unnest(pids) AS t(pid)
    WHERE t.pid IS NOT NULL AND t.pid <> '' AND length(t.pid) <= 64
    LIMIT 50
  ) AS q
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity_dev.count + 1,
      updated_at = now()
  WHERE cart_popularity_dev.updated_at < now() - interval '1 second';
END;
$$;

CREATE OR REPLACE FUNCTION public.record_cart_activity_dev(
  items jsonb,
  etype text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  bucket timestamp := date_trunc('hour', timezone('Europe/Copenhagen', now()));
  w integer;
BEGIN
  w := CASE etype WHEN 'compare' THEN 3 WHEN 'add' THEN 1 ELSE NULL END;
  IF w IS NULL THEN
    RETURN;
  END IF;

  WITH cleaned AS (
    SELECT i.pid,
           LEAST(GREATEST(COALESCE(i.qty, 1), 1), 99) AS qty
    FROM jsonb_to_recordset(items) AS i(pid text, qty int)
    WHERE i.pid IS NOT NULL AND i.pid <> '' AND length(i.pid) <= 64
    LIMIT 50
  ),
  agg AS (
    SELECT pid, count(*)::int AS events, sum(qty)::int AS qty
    FROM cleaned
    GROUP BY pid
  ),
  pop AS (
    INSERT INTO public.cart_popularity_dev (product_id, count, updated_at)
    SELECT pid, w, now() FROM agg
    ON CONFLICT (product_id) DO UPDATE
    SET count = cart_popularity_dev.count + w,
        updated_at = now()
    WHERE cart_popularity_dev.updated_at < now() - interval '1 second'
  )
  INSERT INTO public.cart_events_dev (product_id, hour, event_type, events, qty)
  SELECT pid, bucket, etype, events, qty FROM agg
  ON CONFLICT (product_id, hour, event_type) DO UPDATE
  SET events = cart_events_dev.events + EXCLUDED.events,
      qty    = cart_events_dev.qty + EXCLUDED.qty;
END;
$$;

-- GRANT/REVOKE er uændrede (samme rettigheder som før - kun funktionskroppen
-- og tabellens kolonner ændres). Sættes eksplicit igen alligevel, så scriptet
-- også virker uafhængigt på et projekt hvor det køres ude af rækkefølge.
GRANT EXECUTE ON FUNCTION public.increment_cart_count(text)        TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.increment_cart_counts(text[])     TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.record_cart_activity(jsonb, text) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.increment_cart_count_dev(text)        TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.increment_cart_counts_dev(text[])     TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.record_cart_activity_dev(jsonb, text) TO anon, authenticated, service_role;
