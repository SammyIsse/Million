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

-- Samme begrundelse som cart_popularity_count_idx i supabase-cart-increment.sql
CREATE INDEX IF NOT EXISTS cart_popularity_dev_count_idx
  ON public.cart_popularity_dev (count DESC);

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

-- Rettigheder + RLS: samme SLUTTILSTAND som produktion (efter
-- supabase-hardening.sql er kørt) - IKKE et mellemtrin hardening bagefter
-- strammer. Dette script blev tidligere skrevet til at give anon direkte
-- INSERT/UPDATE (matchende hardening.sql §1's "FØR"-tilstand), hvilket gjorde
-- scriptet farligt at køre igen: en gen-kørsel (nyt dev-opsæt, "kør alle
-- scripts") genåbnede PRÆCIS de huller hardening.sql lukkede - fri
-- prisalarm-impersonering (fremmed user_id/email via price_alerts_dev) og
-- ubegrænsede cart_popularity_dev-skrivninger uden om record_cart_activity_dev's
-- validering. Begge tabellers RIGTIGE skrivevej er allerede en SECURITY
-- DEFINER-RPC (record_cart_activity_dev ovenfor; create_price_alert_dev i
-- hardening.sql) - de kræver INGEN direkte table-grant til anon/authenticated,
-- fordi SECURITY DEFINER kører med definer'ens rettigheder, ikke kalderens.
GRANT SELECT ON public.cart_popularity_dev TO anon, authenticated;
-- service_role har også DELETE, så testrækker kan ryddes uden om SQL Editor
-- (uden den fejler oprydning med 403, og dev-favoritterne fyldes med testdata)
GRANT SELECT, INSERT, UPDATE, DELETE ON public.cart_popularity_dev TO service_role;
GRANT ALL ON public.price_alerts_dev TO service_role;

ALTER TABLE public.cart_popularity_dev ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.price_alerts_dev ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cart_popularity_dev_anon_select ON public.cart_popularity_dev;
CREATE POLICY cart_popularity_dev_anon_select ON public.cart_popularity_dev
  FOR SELECT TO anon, authenticated USING (true);
-- Ingen anon/authenticated INSERT/UPDATE-policy her med vilje - se kommentaren
-- ovenfor. Fjernede tidligere cart_popularity_dev_anon_insert/_update
-- (USING/WITH CHECK (true) = "enhver må ændre enhver række") og
-- price_alerts_dev_anon_insert.

-- Oprydning: testrækker fra verifikationskørsler. De er harmløse, men lander
-- ellers i "Brugernes Favoritter" på staging, fordi de har en count.
DELETE FROM public.cart_popularity_dev
WHERE product_id LIKE 'zz\_%' OR product_id LIKE 'verify\_%';

-- ---------------------------------------------------------------------------
-- Dev-kopi af carts (gemt kurv) - kør EFTER scripts/supabase-carts.sql
-- ---------------------------------------------------------------------------
-- I den client-side model skriver browseren direkte til kurv-tabellen. Staging
-- injicerer tabelnavnet "carts_dev" til browseren (window.__SB_CARTS, sat af
-- app.py's context-processor ud fra TABLE_SUFFIX), så test-konti ikke lander i
-- produktionens carts. Samme Supabase-projekt og samme auth.users - kun kurv-
-- rækkerne adskilles, præcis som cart_popularity_dev.
--
-- LIKE INCLUDING ALL kopierer PK, CHECK-constraint og defaults, men IKKE FK,
-- trigger eller RLS - dem tilføjer vi eksplicit nedenfor.
CREATE TABLE IF NOT EXISTS public.carts_dev
  (LIKE public.carts INCLUDING ALL);

-- FK til auth.users (LIKE kopierer den ikke) - så kontosletning cascader.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'carts_dev_user_id_fkey'
  ) THEN
    ALTER TABLE public.carts_dev
      ADD CONSTRAINT carts_dev_user_id_fkey
      FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;
  END IF;
END $$;

-- Genbruger trigger-funktionen fra hovedscriptet.
DROP TRIGGER IF EXISTS carts_dev_touch ON public.carts_dev;
CREATE TRIGGER carts_dev_touch
  BEFORE UPDATE ON public.carts_dev
  FOR EACH ROW EXECUTE FUNCTION public.carts_touch_updated_at();

GRANT SELECT, INSERT, UPDATE, DELETE ON public.carts_dev TO authenticated;
GRANT ALL ON public.carts_dev TO service_role;

ALTER TABLE public.carts_dev ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS carts_dev_own_select ON public.carts_dev;
CREATE POLICY carts_dev_own_select ON public.carts_dev
  FOR SELECT TO authenticated USING (auth.uid() = user_id);
DROP POLICY IF EXISTS carts_dev_own_insert ON public.carts_dev;
CREATE POLICY carts_dev_own_insert ON public.carts_dev
  FOR INSERT TO authenticated WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS carts_dev_own_update ON public.carts_dev;
CREATE POLICY carts_dev_own_update ON public.carts_dev
  FOR UPDATE TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS carts_dev_own_delete ON public.carts_dev;
CREATE POLICY carts_dev_own_delete ON public.carts_dev
  FOR DELETE TO authenticated USING (auth.uid() = user_id);
DROP POLICY IF EXISTS carts_dev_service_all ON public.carts_dev;
CREATE POLICY carts_dev_service_all ON public.carts_dev
  FOR ALL TO service_role USING (true) WITH CHECK (true);
