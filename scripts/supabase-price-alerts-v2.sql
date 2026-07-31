-- Kør i Supabase SQL Editor (Dashboard → SQL → New query), én gang.
-- Opgraderer price_alerts fra helt anonym til login-krævet + mail-notifikation
-- (docs/prisovervaagning.md). Køres EFTER scripts/supabase-hardening.sql og
-- scripts/supabase-dev-tables.sql (begge skal have kørt før denne).
--
-- Hvorfor: price_alerts havde ingen bruger-kolonne overhovedet (se
-- supabase-hardening.sql §3) - der var derfor ingen at sende en notifikation
-- til, og "Overvåg pris" viste kun et "kommer snart"-overlay. Nu kræver
-- oprettelse login, og emailen hentes fra JWT'et (kan ikke forfalskes af
-- browseren), så updater.py's natlige tjek kan maile den rigtige bruger.

-- ===========================================================================
-- Produktion: price_alerts
-- ===========================================================================
ALTER TABLE public.price_alerts
  ADD COLUMN IF NOT EXISTS user_id     uuid REFERENCES auth.users(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS email       text,
  ADD COLUMN IF NOT EXISTS notified_at timestamptz;

-- Gammel dedup-nøgle (product_id, target_price) gav mening for anonyme
-- alarmer. Nu skal to brugere kunne overvåge samme vare uafhængigt, og én
-- bruger skal kun have ÉN aktiv alarm pr. vare - et nyt kald opdaterer target
-- i stedet for at oprette endnu en række.
DROP INDEX IF EXISTS price_alerts_product_target_idx;
CREATE UNIQUE INDEX IF NOT EXISTS price_alerts_user_product_idx
  ON public.price_alerts (user_id, product_id);

-- Brugeren må se og slette SIN EGEN alarm (fx en fremtidig "Mine alarmer"-
-- liste), men aldrig indsætte/opdatere direkte - det går fortsat kun gennem
-- RPC'en nedenfor, så prisgrænser og loft håndhæves ét sted.
GRANT SELECT, DELETE ON public.price_alerts TO authenticated;

DROP POLICY IF EXISTS price_alerts_own_select ON public.price_alerts;
CREATE POLICY price_alerts_own_select ON public.price_alerts
  FOR SELECT TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS price_alerts_own_delete ON public.price_alerts;
CREATE POLICY price_alerts_own_delete ON public.price_alerts
  FOR DELETE TO authenticated USING (auth.uid() = user_id);

-- Erstatter den anonyme create_price_alert: kræver nu login (auth.uid()) og
-- henter email fra JWT'et. anon mister EXECUTE helt til sidst i scriptet.
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
  uid    uuid := auth.uid();
  uemail text := auth.jwt() ->> 'email';
BEGIN
  IF uid IS NULL OR uemail IS NULL OR uemail = '' THEN
    RETURN false;                  -- kræver login med kendt email
  END IF;
  IF pid IS NULL OR pid = '' OR length(pid) > 64 THEN
    RETURN false;
  END IF;
  IF target IS NULL OR current IS NULL
     OR target  <= 0 OR target  > 99999
     OR current <= 0 OR current > 99999 THEN
    RETURN false;
  END IF;

  -- Pr.-bruger-loft (ikke globalt) - login gør spam markant dyrere end før,
  -- men en enkelt kompromitteret konto skal stadig ikke kunne vokse ubegrænset.
  SELECT count(*) INTO n_rows FROM public.price_alerts WHERE user_id = uid;
  IF n_rows >= 200 AND NOT EXISTS (
       SELECT 1 FROM public.price_alerts
       WHERE user_id = uid AND product_id = pid) THEN
    RETURN false;
  END IF;

  INSERT INTO public.price_alerts
    (product_id, product_name, target_price, current_price, user_id, email, notified_at)
  VALUES (pid, left(coalesce(pname, ''), 200), target, current, uid, uemail, NULL)
  ON CONFLICT (user_id, product_id) DO UPDATE
  SET target_price  = EXCLUDED.target_price,
      current_price = EXCLUDED.current_price,
      product_name  = EXCLUDED.product_name,
      email         = EXCLUDED.email,
      notified_at   = NULL;        -- ny alarm på en vare der allerede har udløst → aktivér igen
  RETURN true;
END;
$$;

REVOKE ALL     ON FUNCTION public.create_price_alert(text, text, numeric, numeric) FROM public;
GRANT  EXECUTE ON FUNCTION public.create_price_alert(text, text, numeric, numeric) TO authenticated;
-- anon krævede reelt altid login (RETURN false var det eneste der stoppede
-- dem) - nu nægtes retten helt i stedet for at afvise inde i funktionen.
REVOKE EXECUTE ON FUNCTION public.create_price_alert(text, text, numeric, numeric) FROM anon;


-- ===========================================================================
-- Dev/staging: price_alerts_dev (samme ændringer, TABLE_SUFFIX=_dev)
-- ===========================================================================
DO $$
BEGIN
  IF to_regclass('public.price_alerts_dev') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE public.price_alerts_dev
               ADD COLUMN IF NOT EXISTS user_id     uuid REFERENCES auth.users(id) ON DELETE CASCADE,
               ADD COLUMN IF NOT EXISTS email       text,
               ADD COLUMN IF NOT EXISTS notified_at timestamptz';
    EXECUTE 'DROP INDEX IF EXISTS price_alerts_dev_product_target_idx';
    EXECUTE 'CREATE UNIQUE INDEX IF NOT EXISTS price_alerts_dev_user_product_idx
               ON public.price_alerts_dev (user_id, product_id)';
    EXECUTE 'GRANT SELECT, DELETE ON public.price_alerts_dev TO authenticated';
    EXECUTE 'DROP POLICY IF EXISTS price_alerts_dev_own_select ON public.price_alerts_dev';
    EXECUTE 'CREATE POLICY price_alerts_dev_own_select ON public.price_alerts_dev
               FOR SELECT TO authenticated USING (auth.uid() = user_id)';
    EXECUTE 'DROP POLICY IF EXISTS price_alerts_dev_own_delete ON public.price_alerts_dev';
    EXECUTE 'CREATE POLICY price_alerts_dev_own_delete ON public.price_alerts_dev
               FOR DELETE TO authenticated USING (auth.uid() = user_id)';
    -- Den gamle anon-insert-policy fra supabase-dev-tables.sql giver ikke
    -- længere adgang til noget (anon mister EXECUTE på RPC'en nedenfor), men
    -- fjernes for en god ordens skyld så den ikke fremstår som en åben dør.
    EXECUTE 'DROP POLICY IF EXISTS price_alerts_dev_anon_insert ON public.price_alerts_dev';
  END IF;
END $$;

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
  uid    uuid := auth.uid();
  uemail text := auth.jwt() ->> 'email';
BEGIN
  IF uid IS NULL OR uemail IS NULL OR uemail = '' THEN
    RETURN false;
  END IF;
  IF pid IS NULL OR pid = '' OR length(pid) > 64 THEN
    RETURN false;
  END IF;
  IF target IS NULL OR current IS NULL
     OR target  <= 0 OR target  > 99999
     OR current <= 0 OR current > 99999 THEN
    RETURN false;
  END IF;

  SELECT count(*) INTO n_rows FROM public.price_alerts_dev WHERE user_id = uid;
  IF n_rows >= 200 AND NOT EXISTS (
       SELECT 1 FROM public.price_alerts_dev
       WHERE user_id = uid AND product_id = pid) THEN
    RETURN false;
  END IF;

  INSERT INTO public.price_alerts_dev
    (product_id, product_name, target_price, current_price, user_id, email, notified_at)
  VALUES (pid, left(coalesce(pname, ''), 200), target, current, uid, uemail, NULL)
  ON CONFLICT (user_id, product_id) DO UPDATE
  SET target_price  = EXCLUDED.target_price,
      current_price = EXCLUDED.current_price,
      product_name  = EXCLUDED.product_name,
      email         = EXCLUDED.email,
      notified_at   = NULL;
  RETURN true;
END;
$$;

REVOKE ALL     ON FUNCTION public.create_price_alert_dev(text, text, numeric, numeric) FROM public;
GRANT  EXECUTE ON FUNCTION public.create_price_alert_dev(text, text, numeric, numeric) TO authenticated;
REVOKE EXECUTE ON FUNCTION public.create_price_alert_dev(text, text, numeric, numeric) FROM anon;
