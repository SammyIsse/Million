-- Kør i Supabase SQL Editor (én gang).
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

-- Rettigheder + RLS: samme adgang som app'en har brug for i produktion
-- (cart: læs/skriv via anon-nøglen; alarmer: kun insert via anon-nøglen)
GRANT SELECT, INSERT, UPDATE ON public.cart_popularity_dev
  TO anon, authenticated, service_role;
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
