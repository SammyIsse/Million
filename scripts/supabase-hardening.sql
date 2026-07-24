-- ===========================================================================
-- MadShopper - sikkerhedshaerdning af Supabase (koeres EN gang i SQL Editor)
-- ===========================================================================
--
-- Baggrund: verificeret maaling mod produktionsdatabasen 24-07-2026 med den
-- OFFENTLIGE nogle (sb_publishable_..., som ligger i wrangler.toml, i git og i
-- hver eneste sides HTML - den er kendt af alle). Resultatet var:
--
--   produkter, price_history, nutrition_data, app_cache : anon kan kun LAESE
--       (skrivning gav 42501 "permission denied" paa hver tabel)  ->  OK
--   cart_events, carts                                  : helt lukket for anon
--       (42501 paa baade laes og skriv)                             ->  OK
--   cart_popularity : anon kunne INSERT + UPDATE direkte             ->  HUL
--   price_alerts    : anon kunne INSERT direkte                      ->  HUL
--
-- De to sidste blev bekraeftet ved faktisk at skrive: en POST til
-- /rest/v1/price_alerts med {"product_id": null} svarede 201 Created og
-- oprettede en helt tom raekke (id 9). Raekken er slettet igen.
--
-- Hvorfor det betyder noget: app.py validerer omhyggeligt (id-laengde, antal
-- varer, kvantitet, prisgraenser) og har rate limiting i to lag - men INTET af
-- det gaelder, naar man kalder PostgREST direkte med den offentlige nogle.
-- Enhver kunne saaledes:
--   * saette cart_popularity.count til et vilkaarligt tal og dermed styre
--     "Brugernes Favoritter" paa forsiden, og
--   * fylde price_alerts med ubegraensede junk-raekker.
--
-- Dette script lukker begge huller efter samme moenster som det, der allerede
-- beskytter cart_events: tabellen selv er lukket for anon, og al skrivning gaar
-- gennem en SECURITY DEFINER-funktion, der gentager valideringen i SQL. Saa
-- gaelder reglerne uanset om kaldet kommer fra app.py eller fra en curl-kommando.
--
-- Scriptet er idempotent - det kan koeres igen uden skade.


-- ===========================================================================
-- 1) cart_popularity - luk for direkte skrivning
-- ===========================================================================
-- Laesning skal blive ved med at vaere aaben: forsidens "Brugernes Favoritter"
-- henter den via _popular_product_ids() i app.py med den offentlige nogle.
-- Skrivning gaar udelukkende gennem RPC'erne nedenfor.

REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.cart_popularity FROM anon, authenticated;
GRANT  SELECT                          ON public.cart_popularity TO   anon, authenticated;

ALTER TABLE public.cart_popularity ENABLE ROW LEVEL SECURITY;

-- Policies skal matche grants, ellers er tabellen laast i det ene lag og
-- aaben i det andet. Kun SELECT for offentligheden.
DROP POLICY IF EXISTS "Offentlig laesning"        ON public.cart_popularity;
DROP POLICY IF EXISTS cart_popularity_anon_select ON public.cart_popularity;
DROP POLICY IF EXISTS cart_popularity_anon_insert ON public.cart_popularity;
DROP POLICY IF EXISTS cart_popularity_anon_update ON public.cart_popularity;

CREATE POLICY "Offentlig laesning" ON public.cart_popularity
  FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.cart_popularity;
CREATE POLICY "Service role fuld adgang" ON public.cart_popularity
  FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.cart_popularity TO service_role;


-- ===========================================================================
-- 2) increment_cart_count(s) - fra SECURITY INVOKER til SECURITY DEFINER
-- ===========================================================================
-- Disse to er fallbacks, som app.py bruger, hvis record_cart_activity mangler.
-- De var LANGUAGE sql UDEN SECURITY DEFINER, saa de kun virkede, fordi anon
-- havde skriveadgang til tabellen direkte. Naar den adgang nu er vaek, skal de
-- koere med ejerens rettigheder - praecis som record_cart_activity allerede gor.
--
-- Valideringen gentages her (ikke kun i app.py), fordi RPC'en kan kaldes
-- direkte via PostgREST udenom Flask-lagets caps og rate limiting.
-- SET search_path er paakraevet paa SECURITY DEFINER: uden den kan en rolle med
-- ret til at oprette et skema tidligere i stien faa funktionen til at ramme
-- sine egne tabeller i stedet for public's.

CREATE OR REPLACE FUNCTION public.increment_cart_count(pid text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF pid IS NULL OR pid = '' OR length(pid) > 64 THEN
    RETURN;                        -- ugyldigt id: ignorer i stilhed
  END IF;
  INSERT INTO public.cart_popularity (product_id, count)
  VALUES (pid, 1)
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity.count + 1;
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
  -- DISTINCT er paakraevet: uden den fejler ON CONFLICT med "cannot affect row
  -- a second time", hvis samme id optraeder to gange i arrayet.
  -- LIMIT 50 spejler _CART_EVENT_MAX_IDS i app.py.
  INSERT INTO public.cart_popularity (product_id, count)
  SELECT pid, 1 FROM (
    SELECT DISTINCT t.pid
    FROM unnest(pids) AS t(pid)
    WHERE t.pid IS NOT NULL AND t.pid <> '' AND length(t.pid) <= 64
    LIMIT 50
  ) AS q
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity.count + 1;
END;
$$;

-- EXECUTE-rettigheden er uaendret; det er funktionens rettighedsmodel der er
-- skaerpet. Saettes eksplicit, saa scriptet ogsaa virker paa et frisk projekt.
GRANT EXECUTE ON FUNCTION public.increment_cart_count(text)    TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.increment_cart_counts(text[]) TO anon, authenticated, service_role;


-- ===========================================================================
-- 3) price_alerts - luk helt for anon, indsaet kun via valideret RPC
-- ===========================================================================
-- app.py LAESER aldrig price_alerts (verificeret: eneste forekomst er den
-- POST, der erstattes nedenfor), saa anon har heller ingen grund til SELECT.
-- Alarmerne laeses kun af backend-jobs med service_role.

REVOKE ALL ON public.price_alerts FROM anon, authenticated;

ALTER TABLE public.price_alerts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS price_alerts_anon_insert   ON public.price_alerts;
DROP POLICY IF EXISTS "Anon kan oprette alarmer" ON public.price_alerts;
DROP POLICY IF EXISTS "Offentlig laesning"       ON public.price_alerts;

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.price_alerts;
CREATE POLICY "Service role fuld adgang" ON public.price_alerts
  FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.price_alerts TO service_role;

-- Dedupering: uden en unik noegle kan den samme alarm indsaettes uendeligt
-- mange gange, og saa er en rate limit i appen det eneste der bounder
-- lagerforbruget. Med noeglen kollapser gentagne kald til en opdatering af den
-- eksisterende raekke, saa flooding ikke laengere kan puste tabellen op.
-- Tabellen har ingen bruger-kolonne, saa en alarm er i forvejen ikke knyttet
-- til en person - dedupering aendrer derfor ingen funktionalitet.
CREATE UNIQUE INDEX IF NOT EXISTS price_alerts_product_target_idx
  ON public.price_alerts (product_id, target_price);

-- SECURITY DEFINER-indgangen. Samme graenser som app.py's create_alert():
-- id <= 64 tegn, navn <= 200 tegn, priser > 0 og <= 99999.
-- Derudover et globalt loft, saa tabellen ikke kan vokse ubegraenset selv med
-- gyldige, unikke vaerdier. Loftet blokerer KUN nye distinkte raekker -
-- eksisterende alarmer kan stadig opdateres, saa funktionen kan ikke
-- "laases ude" af en angriber der har fyldt tabellen.
CREATE OR REPLACE FUNCTION public.create_price_alert(
  pid     text,
  pname   text,
  target  numeric,
  current numeric
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  n_rows bigint;
BEGIN
  IF pid IS NULL OR pid = '' OR length(pid) > 64 THEN
    RETURN false;
  END IF;
  IF target IS NULL OR current IS NULL
     OR target  <= 0 OR target  > 99999
     OR current <= 0 OR current > 99999 THEN
    RETURN false;
  END IF;

  SELECT count(*) INTO n_rows FROM public.price_alerts;
  IF n_rows >= 50000 AND NOT EXISTS (
       SELECT 1 FROM public.price_alerts
       WHERE product_id = pid AND target_price = target) THEN
    RETURN false;                  -- loft naaet: ingen nye distinkte raekker
  END IF;

  INSERT INTO public.price_alerts (product_id, product_name, target_price, current_price)
  VALUES (pid, left(coalesce(pname, ''), 200), target, current)
  ON CONFLICT (product_id, target_price) DO UPDATE
  SET current_price = EXCLUDED.current_price,
      product_name  = EXCLUDED.product_name;
  RETURN true;
END;
$$;

REVOKE ALL    ON FUNCTION public.create_price_alert(text, text, numeric, numeric) FROM public;
GRANT  EXECUTE ON FUNCTION public.create_price_alert(text, text, numeric, numeric) TO anon, authenticated, service_role;


-- ===========================================================================
-- 4) app_cache - RLS slaaet til (i dag beskytter KUN grants)
-- ===========================================================================
-- Laesning er tilsigtet og skal fortsaette: det er praecis de data, siden viser,
-- og app.py henter dem med den offentlige nogle, naar KV-cachen er kold.
-- Policy og grant oprettes SAMMEN med ENABLE, saa der ikke findes et oejeblik
-- hvor RLS er slaaet til uden en laesepolicy (det ville soerge siden sort).

ALTER TABLE public.app_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Offentlig laesning" ON public.app_cache;
CREATE POLICY "Offentlig laesning" ON public.app_cache
  FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.app_cache;
CREATE POLICY "Service role fuld adgang" ON public.app_cache
  FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT                         ON public.app_cache TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.app_cache TO service_role;


-- ===========================================================================
-- 5) price_history_low30 - view'et bruges kun af updater.py (service_role)
-- ===========================================================================
-- Det afleder ganske vist kun offentlige data fra price_history, men mindste
-- privilegium: ingen grund til at eksponere et aggregat, intet i browseren kalder.

REVOKE ALL ON public.price_history_low30 FROM anon, authenticated;
GRANT SELECT ON public.price_history_low30 TO service_role;


-- ===========================================================================
-- 6) Samme haerdning paa *_dev-kopierne
-- ===========================================================================
-- Staging og lokal koersel rammer disse. De ligger i SAMME database, saa et hul
-- her er lige saa aabent som et hul i produktion.

DO $$
BEGIN
  IF to_regclass('public.cart_popularity_dev') IS NOT NULL THEN
    EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.cart_popularity_dev FROM anon, authenticated';
    EXECUTE 'GRANT SELECT ON public.cart_popularity_dev TO anon, authenticated';
    EXECUTE 'ALTER TABLE public.cart_popularity_dev ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS cart_popularity_dev_anon_insert ON public.cart_popularity_dev';
    EXECUTE 'DROP POLICY IF EXISTS cart_popularity_dev_anon_update ON public.cart_popularity_dev';
    EXECUTE 'DROP POLICY IF EXISTS cart_popularity_dev_anon_select ON public.cart_popularity_dev';
    EXECUTE 'CREATE POLICY cart_popularity_dev_anon_select ON public.cart_popularity_dev
               FOR SELECT TO anon, authenticated USING (true)';
  END IF;

  IF to_regclass('public.price_alerts_dev') IS NOT NULL THEN
    EXECUTE 'REVOKE ALL ON public.price_alerts_dev FROM anon, authenticated';
    EXECUTE 'ALTER TABLE public.price_alerts_dev ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS price_alerts_dev_anon_insert ON public.price_alerts_dev';
    EXECUTE 'CREATE UNIQUE INDEX IF NOT EXISTS price_alerts_dev_product_target_idx
               ON public.price_alerts_dev (product_id, target_price)';
  END IF;
END $$;

-- Dev-udgaver af RPC'erne, saa staging/lokalt foelger samme model.
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
  INSERT INTO public.cart_popularity_dev (product_id, count)
  VALUES (pid, 1)
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity_dev.count + 1;
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
  INSERT INTO public.cart_popularity_dev (product_id, count)
  SELECT pid, 1 FROM (
    SELECT DISTINCT t.pid
    FROM unnest(pids) AS t(pid)
    WHERE t.pid IS NOT NULL AND t.pid <> '' AND length(t.pid) <= 64
    LIMIT 50
  ) AS q
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity_dev.count + 1;
END;
$$;

CREATE OR REPLACE FUNCTION public.create_price_alert_dev(
  pid     text,
  pname   text,
  target  numeric,
  current numeric
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  n_rows bigint;
BEGIN
  IF pid IS NULL OR pid = '' OR length(pid) > 64 THEN
    RETURN false;
  END IF;
  IF target IS NULL OR current IS NULL
     OR target  <= 0 OR target  > 99999
     OR current <= 0 OR current > 99999 THEN
    RETURN false;
  END IF;

  SELECT count(*) INTO n_rows FROM public.price_alerts_dev;
  IF n_rows >= 50000 AND NOT EXISTS (
       SELECT 1 FROM public.price_alerts_dev
       WHERE product_id = pid AND target_price = target) THEN
    RETURN false;
  END IF;

  INSERT INTO public.price_alerts_dev (product_id, product_name, target_price, current_price)
  VALUES (pid, left(coalesce(pname, ''), 200), target, current)
  ON CONFLICT (product_id, target_price) DO UPDATE
  SET current_price = EXCLUDED.current_price,
      product_name  = EXCLUDED.product_name;
  RETURN true;
END;
$$;

GRANT EXECUTE ON FUNCTION public.increment_cart_count_dev(text)    TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.increment_cart_counts_dev(text[]) TO anon, authenticated, service_role;
REVOKE ALL     ON FUNCTION public.create_price_alert_dev(text, text, numeric, numeric) FROM public;
GRANT  EXECUTE ON FUNCTION public.create_price_alert_dev(text, text, numeric, numeric) TO anon, authenticated, service_role;


-- ===========================================================================
-- 7) Sikkerhedshaendelser fra edge (skrives af Cloudflare-workeren)
-- ===========================================================================
-- Workers-observability er permanent slaaet fra (dens introspektion var selv
-- aarsag til nedbruddet 19-07-2026), saa der findes ingen request- eller
-- fejllog i produktion. Denne tabel er erstatningen: workeren aggregerer
-- haendelser pr. isolate og skyller hoejst en raekke pr. minut, saa selv et
-- angreb ikke kan forvandle logningen til sin egen forstaerker.
--
-- Kun service_role har adgang. Workeren skriver via D1 (ikke herind) og
-- GitHub Actions-relayet loefter dem hertil - samme moenster som feedback.
CREATE TABLE IF NOT EXISTS public.security_events (
  id         bigserial PRIMARY KEY,
  bucket     timestamptz NOT NULL,
  kind       text        NOT NULL,   -- 'rate_limit' | 'server_error' | 'auth_fail'
  path       text,
  events     integer     NOT NULL DEFAULT 0,
  UNIQUE (bucket, kind, path)
);

CREATE INDEX IF NOT EXISTS security_events_bucket_idx ON public.security_events (bucket DESC);

REVOKE ALL ON public.security_events FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.security_events TO service_role;

-- bigserial opretter en sekvens, som INSERT ogsaa kraever rettigheder til.
-- Uden denne fejler relayet med 42501 "permission denied for sequence
-- security_events_id_seq" - tabel-granten alene er ikke nok. (Fundet ved
-- foerste rigtige koersel af security-monitor.yml, ikke ved gennemlaesning.)
GRANT USAGE, SELECT ON SEQUENCE public.security_events_id_seq TO service_role;
REVOKE ALL ON SEQUENCE public.security_events_id_seq FROM anon, authenticated;

ALTER TABLE public.security_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.security_events;
CREATE POLICY "Service role fuld adgang" ON public.security_events
  FOR ALL TO service_role USING (true) WITH CHECK (true);


-- ===========================================================================
-- 8) Verifikation - koer denne til sidst og laes resultatet
-- ===========================================================================
-- Forventet efter scriptet: INGEN raekker med INSERT/UPDATE/DELETE for anon
-- eller authenticated. Kun SELECT paa app_cache, price_history,
-- nutrition_data og cart_popularity.

SELECT
  table_name AS tabel,
  grantee,
  string_agg(privilege_type, ', ' ORDER BY privilege_type) AS rettigheder
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND grantee IN ('anon', 'authenticated')
GROUP BY table_name, grantee
ORDER BY table_name, grantee;

-- Alle tabeller skal have rls_enabled = true.
SELECT c.relname AS tabel, c.relrowsecurity AS rls_enabled,
       (SELECT count(*) FROM pg_policies p
         WHERE p.schemaname = 'public' AND p.tablename = c.relname) AS antal_policies
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relrowsecurity ASC, c.relname;

-- Alle funktioner anon maa kalde, skal nu vaere security_definer = true.
SELECT p.proname AS funktion, p.prosecdef AS security_definer,
       has_function_privilege('anon', p.oid, 'EXECUTE') AS anon_maa_koere
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
ORDER BY p.proname;
