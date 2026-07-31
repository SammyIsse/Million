-- Kør i Supabase SQL Editor, én gang - EFTER scripts/supabase-price-alerts-v2.sql.
--
-- create_price_alert gik direkte fra browseren til Supabase (uden om app.py),
-- så app.py's @rate_limit-decorator beskytter den ikke. Login + 200-alarm-
-- loftet (v2-scriptet) bremser hvor MEGET data en bruger kan oprette, men
-- intet bremsede hvor HURTIGT en bruger kunne kalde funktionen - et script med
-- en gyldig session kunne i teorien hamre løs mange gange i sekundet og
-- belaste den database resten af sitet også læser produktdata fra.
--
-- Løsning: en cooldown på 1 kald/sekund pr. bruger, håndhævet i selve
-- SECURITY DEFINER-funktionen. Billigt at tjekke (indekset på user_id har
-- højst 200 rækker pr. bruger pga. loftet ovenfor).

ALTER TABLE public.price_alerts
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

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

  -- Cooldown: højst ét kald pr. sekund pr. bruger.
  IF EXISTS (
       SELECT 1 FROM public.price_alerts
       WHERE user_id = uid AND updated_at > now() - interval '1 second'
     ) THEN
    RETURN false;
  END IF;

  SELECT count(*) INTO n_rows FROM public.price_alerts WHERE user_id = uid;
  IF n_rows >= 200 AND NOT EXISTS (
       SELECT 1 FROM public.price_alerts
       WHERE user_id = uid AND product_id = pid) THEN
    RETURN false;
  END IF;

  INSERT INTO public.price_alerts
    (product_id, product_name, target_price, current_price, user_id, email, notified_at, updated_at)
  VALUES (pid, left(coalesce(pname, ''), 200), target, current, uid, uemail, NULL, now())
  ON CONFLICT (user_id, product_id) DO UPDATE
  SET target_price  = EXCLUDED.target_price,
      current_price = EXCLUDED.current_price,
      product_name  = EXCLUDED.product_name,
      email         = EXCLUDED.email,
      notified_at   = NULL,
      updated_at    = now();
  RETURN true;
END;
$$;


-- ===========================================================================
-- Dev/staging: price_alerts_dev (samme ændring)
-- ===========================================================================
DO $$
BEGIN
  IF to_regclass('public.price_alerts_dev') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE public.price_alerts_dev
               ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()';
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

  IF EXISTS (
       SELECT 1 FROM public.price_alerts_dev
       WHERE user_id = uid AND updated_at > now() - interval '1 second'
     ) THEN
    RETURN false;
  END IF;

  SELECT count(*) INTO n_rows FROM public.price_alerts_dev WHERE user_id = uid;
  IF n_rows >= 200 AND NOT EXISTS (
       SELECT 1 FROM public.price_alerts_dev
       WHERE user_id = uid AND product_id = pid) THEN
    RETURN false;
  END IF;

  INSERT INTO public.price_alerts_dev
    (product_id, product_name, target_price, current_price, user_id, email, notified_at, updated_at)
  VALUES (pid, left(coalesce(pname, ''), 200), target, current, uid, uemail, NULL, now())
  ON CONFLICT (user_id, product_id) DO UPDATE
  SET target_price  = EXCLUDED.target_price,
      current_price = EXCLUDED.current_price,
      product_name  = EXCLUDED.product_name,
      email         = EXCLUDED.email,
      notified_at   = NULL,
      updated_at    = now();
  RETURN true;
END;
$$;
