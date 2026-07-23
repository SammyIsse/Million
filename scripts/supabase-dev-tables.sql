-- Kør i Supabase SQL Editor (én gang) - EFTER scripts/supabase-cart-increment.sql,
-- da cart_events_dev oprettes med LIKE public.cart_events og derfor kræver at
-- produktionstabellen findes først.
-- Dev-kopier af skrive-tabellerne, så staging-workeren (madshopper-dev) og
-- lokal kørsel ikke forurener produktionens data med test-klik og test-alarmer.
-- app.py vælger tabel via TABLE_SUFFIX-env-varen ("" i produktion, "_dev" i
-- staging/lokalt) - se scripts/build-pages.sh.

-- Struktur, defaults og indekser kopieres fra produktionstabellerne
CREATE TABLE IF NOT EXISTS public.cart_popularity_dev
  (LIKE public.cart_popularity INCLUDING ALL);
CREATE TABLE IF NOT EXISTS public.price_alerts_dev
  (LIKE public.price_alerts INCLUDING ALL);

-- ON CONFLICT i increment-funktionen kræver et unikt indeks på product_id.
-- (LIKE INCLUDING ALL kopierer normalt prod-indekset, dette er en sikkerhedsnet.)
CREATE UNIQUE INDEX IF NOT EXISTS cart_popularity_dev_product_id_idx
  ON public.cart_popularity_dev (product_id);

-- Atomisk klik-tæller - dev-udgave af public.increment_cart_count
-- (app.py kalder POST /rest/v1/rpc/increment_cart_count_dev når TABLE_SUFFIX=_dev)
CREATE OR REPLACE FUNCTION public.increment_cart_count_dev(pid text)
RETURNS void
LANGUAGE sql
AS $$
  INSERT INTO public.cart_popularity_dev (product_id, count)
  VALUES (pid, 1)
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity_dev.count + 1;
$$;

GRANT EXECUTE ON FUNCTION public.increment_cart_count_dev(text)
  TO anon, authenticated, service_role;

-- Batch-tæller - dev-udgave af public.increment_cart_counts
CREATE OR REPLACE FUNCTION public.increment_cart_counts_dev(pids text[])
RETURNS void
LANGUAGE sql
AS $$
  INSERT INTO public.cart_popularity_dev (product_id, count)
  SELECT DISTINCT t.pid, 1
  FROM unnest(pids) AS t(pid)
  WHERE t.pid IS NOT NULL AND t.pid <> ''
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity_dev.count + 1;
$$;

GRANT EXECUTE ON FUNCTION public.increment_cart_counts_dev(text[])
  TO anon, authenticated, service_role;

-- Tidsaggregeret kurv-aktivitet - dev-udgave af public.cart_events.
-- Uden denne (og funktionen nedenfor) faldt staging og lokal kørsel tilbage til
-- de gamle tællere ved hvert kald, fordi app.py kalder navnet med _dev-suffiks.
CREATE TABLE IF NOT EXISTS public.cart_events_dev
  (LIKE public.cart_events INCLUDING ALL);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.cart_events_dev TO service_role;

ALTER TABLE public.cart_events_dev ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cart_events_dev_service_all ON public.cart_events_dev;
CREATE POLICY cart_events_dev_service_all ON public.cart_events_dev
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Dev-udgave af public.record_cart_activity. Samme validering og vægtning -
-- se scripts/supabase-cart-increment.sql for begrundelserne.
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
    INSERT INTO public.cart_popularity_dev (product_id, count)
    SELECT pid, w FROM agg
    ON CONFLICT (product_id) DO UPDATE
    SET count = cart_popularity_dev.count + w
  )
  INSERT INTO public.cart_events_dev (product_id, hour, event_type, events, qty)
  SELECT pid, bucket, etype, events, qty FROM agg
  ON CONFLICT (product_id, hour, event_type) DO UPDATE
  SET events = cart_events_dev.events + EXCLUDED.events,
      qty    = cart_events_dev.qty + EXCLUDED.qty;
END;
$$;

GRANT EXECUTE ON FUNCTION public.record_cart_activity_dev(jsonb, text)
  TO anon, authenticated, service_role;

-- Rettigheder + RLS: samme adgang som app'en har brug for i produktion
-- (cart: læs/skriv via anon-nøglen; alarmer: kun insert via anon-nøglen)
GRANT SELECT, INSERT, UPDATE ON public.cart_popularity_dev
  TO anon, authenticated;
-- service_role har også DELETE, så testrækker kan ryddes uden om SQL Editor
-- (uden den fejler oprydning med 403, og dev-favoritterne fyldes med testdata)
GRANT SELECT, INSERT, UPDATE, DELETE ON public.cart_popularity_dev TO service_role;
GRANT INSERT ON public.price_alerts_dev TO anon, authenticated;
GRANT ALL ON public.price_alerts_dev TO service_role;

ALTER TABLE public.cart_popularity_dev ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.price_alerts_dev ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cart_popularity_dev_anon_select ON public.cart_popularity_dev;
CREATE POLICY cart_popularity_dev_anon_select ON public.cart_popularity_dev
  FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS cart_popularity_dev_anon_insert ON public.cart_popularity_dev;
CREATE POLICY cart_popularity_dev_anon_insert ON public.cart_popularity_dev
  FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS cart_popularity_dev_anon_update ON public.cart_popularity_dev;
CREATE POLICY cart_popularity_dev_anon_update ON public.cart_popularity_dev
  FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS price_alerts_dev_anon_insert ON public.price_alerts_dev;
CREATE POLICY price_alerts_dev_anon_insert ON public.price_alerts_dev
  FOR INSERT TO anon, authenticated WITH CHECK (true);

-- Oprydning: testrækker fra verifikationskørsler. De er harmløse, men lander
-- ellers i "Brugernes Favoritter" på staging, fordi de har en count.
DELETE FROM public.cart_popularity_dev
WHERE product_id LIKE 'zz\_%' OR product_id LIKE 'verify\_%';
