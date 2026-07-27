-- ===========================================================================
-- MadShopper - personlig månedlig besparelse (kør i Supabase SQL Editor)
-- ===========================================================================
-- Én række pr. bruger. Ved "Sammenlign priser" lægges
-- (dyreste butik − billigste butik) til månedens total (kræver login).
-- Percentil: lineær skala 0..max → Top X% (heltal, bedst = 1%).
-- Forrige måned vises de første 7 dage af den nye måned.
--
-- Sikkerhedsmodel: ingen direkte tabel-adgang for anon/authenticated.
-- Al læsning/skrivning går gennem SECURITY DEFINER-RPC'er (auth.uid()).
-- Staging/lokal: *_dev-varianter nedenfor (samme mønster som carts_dev).


-- ---------------------------------------------------------------------------
-- Tabel (produktion)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.user_monthly_savings (
  user_id         uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  month_key       text NOT NULL DEFAULT '',
  amount          numeric(12, 2) NOT NULL DEFAULT 0
                  CHECK (amount >= 0 AND amount <= 100000),
  prev_month_key  text NOT NULL DEFAULT '',
  prev_amount     numeric(12, 2) NOT NULL DEFAULT 0
                  CHECK (prev_amount >= 0 AND prev_amount <= 100000),
  events_day      text NOT NULL DEFAULT '',   -- 'YYYY-MM-DD' (Europe/Copenhagen)
  events_today    integer NOT NULL DEFAULT 0 CHECK (events_today >= 0),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS user_monthly_savings_month_amount_idx
  ON public.user_monthly_savings (month_key, amount DESC);

REVOKE ALL ON public.user_monthly_savings FROM anon, authenticated;
GRANT ALL ON public.user_monthly_savings TO service_role;

ALTER TABLE public.user_monthly_savings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.user_monthly_savings;
CREATE POLICY "Service role fuld adgang"
  ON public.user_monthly_savings FOR ALL TO service_role
  USING (true) WITH CHECK (true);


-- ---------------------------------------------------------------------------
-- Hjælpere (fælles logik via inline i RPC'erne - undgår ekstra grants)
-- ---------------------------------------------------------------------------
-- Måned/dag i dansk tid. Percentil:
--   top_pct = GREATEST(1, ROUND(100 - (amount / max_amount) * 99))
-- når max_amount > 0, ellers 100.

CREATE OR REPLACE FUNCTION public._savings_month_key(ts timestamptz DEFAULT now())
RETURNS text
LANGUAGE sql
STABLE
AS $$
  SELECT to_char(timezone('Europe/Copenhagen', ts), 'YYYY-MM');
$$;

CREATE OR REPLACE FUNCTION public._savings_day_key(ts timestamptz DEFAULT now())
RETURNS text
LANGUAGE sql
STABLE
AS $$
  SELECT to_char(timezone('Europe/Copenhagen', ts), 'YYYY-MM-DD');
$$;

CREATE OR REPLACE FUNCTION public._savings_day_of_month(ts timestamptz DEFAULT now())
RETURNS integer
LANGUAGE sql
STABLE
AS $$
  SELECT EXTRACT(DAY FROM timezone('Europe/Copenhagen', ts))::integer;
$$;

REVOKE ALL ON FUNCTION public._savings_month_key(timestamptz) FROM public, anon, authenticated;
REVOKE ALL ON FUNCTION public._savings_day_key(timestamptz) FROM public, anon, authenticated;
REVOKE ALL ON FUNCTION public._savings_day_of_month(timestamptz) FROM public, anon, authenticated;


-- ---------------------------------------------------------------------------
-- get_personal_savings()
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.get_personal_savings()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  uid uuid := auth.uid();
  mk text := public._savings_month_key();
  day_num integer := public._savings_day_of_month();
  r public.user_monthly_savings%ROWTYPE;
  max_amt numeric := 0;
  amt numeric := 0;
  prev_amt numeric := 0;
  prev_mk text := '';
  show_prev boolean := false;
  top_pct integer := 100;
BEGIN
  IF uid IS NULL THEN
    RETURN jsonb_build_object(
      'available', false,
      'amount', 0,
      'top_pct', 100,
      'month_key', mk,
      'prev_amount', 0,
      'prev_month_key', '',
      'show_prev', false,
      'message', 'Log ind for at tracke besparelse'
    );
  END IF;

  SELECT * INTO r FROM public.user_monthly_savings WHERE user_id = uid;

  IF FOUND THEN
    -- Månedsskifte ved læsning: flyt current → prev
    IF r.month_key IS DISTINCT FROM mk AND r.month_key <> '' THEN
      UPDATE public.user_monthly_savings
      SET prev_month_key = r.month_key,
          prev_amount    = r.amount,
          month_key      = mk,
          amount         = 0,
          events_day     = '',
          events_today   = 0,
          updated_at     = now()
      WHERE user_id = uid
      RETURNING * INTO r;
    END IF;
    amt     := COALESCE(r.amount, 0);
    prev_amt := COALESCE(r.prev_amount, 0);
    prev_mk  := COALESCE(r.prev_month_key, '');
  END IF;

  show_prev := (day_num <= 7 AND prev_amt > 0 AND prev_mk <> '');

  SELECT COALESCE(MAX(amount), 0) INTO max_amt
  FROM public.user_monthly_savings
  WHERE month_key = mk;

  IF max_amt > 0 THEN
    top_pct := GREATEST(1, ROUND(100 - (amt / max_amt) * 99))::integer;
  ELSE
    top_pct := 100;
  END IF;

  RETURN jsonb_build_object(
    'available', true,
    'amount', amt,
    'top_pct', top_pct,
    'month_key', mk,
    'prev_amount', prev_amt,
    'prev_month_key', prev_mk,
    'show_prev', show_prev,
    'message', ''
  );
END;
$$;

REVOKE ALL ON FUNCTION public.get_personal_savings() FROM public, anon;
GRANT EXECUTE ON FUNCTION public.get_personal_savings() TO authenticated, service_role;


-- ---------------------------------------------------------------------------
-- record_compare_savings(p_cheap, p_expensive)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.record_compare_savings(
  p_cheap     numeric,
  p_expensive numeric
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  uid uuid := auth.uid();
  mk text := public._savings_month_key();
  dk text := public._savings_day_key();
  day_num integer := public._savings_day_of_month();
  cheap numeric;
  expensive numeric;
  delta numeric;
  r public.user_monthly_savings%ROWTYPE;
  max_amt numeric := 0;
  amt numeric := 0;
  prev_amt numeric := 0;
  prev_mk text := '';
  show_prev boolean := false;
  top_pct integer := 100;
  new_events integer;
BEGIN
  IF uid IS NULL THEN
    RETURN jsonb_build_object(
      'available', false,
      'amount', 0,
      'top_pct', 100,
      'month_key', mk,
      'prev_amount', 0,
      'prev_month_key', '',
      'show_prev', false,
      'message', 'Log ind for at tracke besparelse'
    );
  END IF;

  -- Validering (gentages her fordi RPC kan kaldes direkte med JWT)
  cheap := COALESCE(p_cheap, 0);
  expensive := COALESCE(p_expensive, 0);
  IF cheap < 0 OR expensive < 0 OR cheap > 50000 OR expensive > 50000
     OR expensive < cheap THEN
    -- ugyldigt: returnér nuværende tilstand uden at ændre
    RETURN public.get_personal_savings();
  END IF;

  delta := expensive - cheap;
  IF delta > 5000 THEN
    delta := 5000;
  END IF;

  SELECT * INTO r FROM public.user_monthly_savings WHERE user_id = uid FOR UPDATE;

  IF NOT FOUND THEN
    INSERT INTO public.user_monthly_savings (
      user_id, month_key, amount, prev_month_key, prev_amount,
      events_day, events_today, updated_at
    ) VALUES (
      uid, mk, LEAST(delta, 100000), '', 0, dk, 1, now()
    )
    RETURNING * INTO r;
  ELSE
    -- Månedsskifte
    IF r.month_key IS DISTINCT FROM mk AND r.month_key <> '' THEN
      r.prev_month_key := r.month_key;
      r.prev_amount := r.amount;
      r.month_key := mk;
      r.amount := 0;
      r.events_day := '';
      r.events_today := 0;
    ELSIF r.month_key = '' THEN
      r.month_key := mk;
    END IF;

    -- Dagligt event-loft (50)
    IF r.events_day IS DISTINCT FROM dk THEN
      r.events_day := dk;
      r.events_today := 0;
    END IF;

    IF r.events_today >= 50 THEN
      -- loft nået: ingen tilføjelse
      UPDATE public.user_monthly_savings
      SET month_key = r.month_key,
          amount = r.amount,
          prev_month_key = r.prev_month_key,
          prev_amount = r.prev_amount,
          events_day = r.events_day,
          events_today = r.events_today,
          updated_at = now()
      WHERE user_id = uid
      RETURNING * INTO r;
    ELSE
      new_events := r.events_today + 1;
      UPDATE public.user_monthly_savings
      SET month_key = r.month_key,
          amount = LEAST(r.amount + delta, 100000),
          prev_month_key = r.prev_month_key,
          prev_amount = r.prev_amount,
          events_day = r.events_day,
          events_today = new_events,
          updated_at = now()
      WHERE user_id = uid
      RETURNING * INTO r;
    END IF;
  END IF;

  amt := COALESCE(r.amount, 0);
  prev_amt := COALESCE(r.prev_amount, 0);
  prev_mk := COALESCE(r.prev_month_key, '');
  show_prev := (day_num <= 7 AND prev_amt > 0 AND prev_mk <> '');

  SELECT COALESCE(MAX(amount), 0) INTO max_amt
  FROM public.user_monthly_savings
  WHERE month_key = mk;

  IF max_amt > 0 THEN
    top_pct := GREATEST(1, ROUND(100 - (amt / max_amt) * 99))::integer;
  ELSE
    top_pct := 100;
  END IF;

  RETURN jsonb_build_object(
    'available', true,
    'amount', amt,
    'top_pct', top_pct,
    'month_key', mk,
    'prev_amount', prev_amt,
    'prev_month_key', prev_mk,
    'show_prev', show_prev,
    'message', ''
  );
END;
$$;

REVOKE ALL ON FUNCTION public.record_compare_savings(numeric, numeric) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.record_compare_savings(numeric, numeric)
  TO authenticated, service_role;


-- ===========================================================================
-- Dev-kopier (staging / lokal TABLE_SUFFIX=_dev)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS public.user_monthly_savings_dev
  (LIKE public.user_monthly_savings INCLUDING ALL);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_monthly_savings_dev_user_id_fkey'
  ) THEN
    ALTER TABLE public.user_monthly_savings_dev
      ADD CONSTRAINT user_monthly_savings_dev_user_id_fkey
      FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS user_monthly_savings_dev_month_amount_idx
  ON public.user_monthly_savings_dev (month_key, amount DESC);

REVOKE ALL ON public.user_monthly_savings_dev FROM anon, authenticated;
GRANT ALL ON public.user_monthly_savings_dev TO service_role;

ALTER TABLE public.user_monthly_savings_dev ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.user_monthly_savings_dev;
CREATE POLICY "Service role fuld adgang"
  ON public.user_monthly_savings_dev FOR ALL TO service_role
  USING (true) WITH CHECK (true);


CREATE OR REPLACE FUNCTION public.get_personal_savings_dev()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  uid uuid := auth.uid();
  mk text := public._savings_month_key();
  day_num integer := public._savings_day_of_month();
  r public.user_monthly_savings_dev%ROWTYPE;
  max_amt numeric := 0;
  amt numeric := 0;
  prev_amt numeric := 0;
  prev_mk text := '';
  show_prev boolean := false;
  top_pct integer := 100;
BEGIN
  IF uid IS NULL THEN
    RETURN jsonb_build_object(
      'available', false,
      'amount', 0,
      'top_pct', 100,
      'month_key', mk,
      'prev_amount', 0,
      'prev_month_key', '',
      'show_prev', false,
      'message', 'Log ind for at tracke besparelse'
    );
  END IF;

  SELECT * INTO r FROM public.user_monthly_savings_dev WHERE user_id = uid;

  IF FOUND THEN
    IF r.month_key IS DISTINCT FROM mk AND r.month_key <> '' THEN
      UPDATE public.user_monthly_savings_dev
      SET prev_month_key = r.month_key,
          prev_amount    = r.amount,
          month_key      = mk,
          amount         = 0,
          events_day     = '',
          events_today   = 0,
          updated_at     = now()
      WHERE user_id = uid
      RETURNING * INTO r;
    END IF;
    amt      := COALESCE(r.amount, 0);
    prev_amt := COALESCE(r.prev_amount, 0);
    prev_mk  := COALESCE(r.prev_month_key, '');
  END IF;

  show_prev := (day_num <= 7 AND prev_amt > 0 AND prev_mk <> '');

  SELECT COALESCE(MAX(amount), 0) INTO max_amt
  FROM public.user_monthly_savings_dev
  WHERE month_key = mk;

  IF max_amt > 0 THEN
    top_pct := GREATEST(1, ROUND(100 - (amt / max_amt) * 99))::integer;
  ELSE
    top_pct := 100;
  END IF;

  RETURN jsonb_build_object(
    'available', true,
    'amount', amt,
    'top_pct', top_pct,
    'month_key', mk,
    'prev_amount', prev_amt,
    'prev_month_key', prev_mk,
    'show_prev', show_prev,
    'message', ''
  );
END;
$$;

REVOKE ALL ON FUNCTION public.get_personal_savings_dev() FROM public, anon;
GRANT EXECUTE ON FUNCTION public.get_personal_savings_dev() TO authenticated, service_role;


CREATE OR REPLACE FUNCTION public.record_compare_savings_dev(
  p_cheap     numeric,
  p_expensive numeric
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  uid uuid := auth.uid();
  mk text := public._savings_month_key();
  dk text := public._savings_day_key();
  day_num integer := public._savings_day_of_month();
  cheap numeric;
  expensive numeric;
  delta numeric;
  r public.user_monthly_savings_dev%ROWTYPE;
  max_amt numeric := 0;
  amt numeric := 0;
  prev_amt numeric := 0;
  prev_mk text := '';
  show_prev boolean := false;
  top_pct integer := 100;
  new_events integer;
BEGIN
  IF uid IS NULL THEN
    RETURN jsonb_build_object(
      'available', false,
      'amount', 0,
      'top_pct', 100,
      'month_key', mk,
      'prev_amount', 0,
      'prev_month_key', '',
      'show_prev', false,
      'message', 'Log ind for at tracke besparelse'
    );
  END IF;

  cheap := COALESCE(p_cheap, 0);
  expensive := COALESCE(p_expensive, 0);
  IF cheap < 0 OR expensive < 0 OR cheap > 50000 OR expensive > 50000
     OR expensive < cheap THEN
    RETURN public.get_personal_savings_dev();
  END IF;

  delta := expensive - cheap;
  IF delta > 5000 THEN
    delta := 5000;
  END IF;

  SELECT * INTO r FROM public.user_monthly_savings_dev WHERE user_id = uid FOR UPDATE;

  IF NOT FOUND THEN
    INSERT INTO public.user_monthly_savings_dev (
      user_id, month_key, amount, prev_month_key, prev_amount,
      events_day, events_today, updated_at
    ) VALUES (
      uid, mk, LEAST(delta, 100000), '', 0, dk, 1, now()
    )
    RETURNING * INTO r;
  ELSE
    IF r.month_key IS DISTINCT FROM mk AND r.month_key <> '' THEN
      r.prev_month_key := r.month_key;
      r.prev_amount := r.amount;
      r.month_key := mk;
      r.amount := 0;
      r.events_day := '';
      r.events_today := 0;
    ELSIF r.month_key = '' THEN
      r.month_key := mk;
    END IF;

    IF r.events_day IS DISTINCT FROM dk THEN
      r.events_day := dk;
      r.events_today := 0;
    END IF;

    IF r.events_today >= 50 THEN
      UPDATE public.user_monthly_savings_dev
      SET month_key = r.month_key,
          amount = r.amount,
          prev_month_key = r.prev_month_key,
          prev_amount = r.prev_amount,
          events_day = r.events_day,
          events_today = r.events_today,
          updated_at = now()
      WHERE user_id = uid
      RETURNING * INTO r;
    ELSE
      new_events := r.events_today + 1;
      UPDATE public.user_monthly_savings_dev
      SET month_key = r.month_key,
          amount = LEAST(r.amount + delta, 100000),
          prev_month_key = r.prev_month_key,
          prev_amount = r.prev_amount,
          events_day = r.events_day,
          events_today = new_events,
          updated_at = now()
      WHERE user_id = uid
      RETURNING * INTO r;
    END IF;
  END IF;

  amt := COALESCE(r.amount, 0);
  prev_amt := COALESCE(r.prev_amount, 0);
  prev_mk := COALESCE(r.prev_month_key, '');
  show_prev := (day_num <= 7 AND prev_amt > 0 AND prev_mk <> '');

  SELECT COALESCE(MAX(amount), 0) INTO max_amt
  FROM public.user_monthly_savings_dev
  WHERE month_key = mk;

  IF max_amt > 0 THEN
    top_pct := GREATEST(1, ROUND(100 - (amt / max_amt) * 99))::integer;
  ELSE
    top_pct := 100;
  END IF;

  RETURN jsonb_build_object(
    'available', true,
    'amount', amt,
    'top_pct', top_pct,
    'month_key', mk,
    'prev_amount', prev_amt,
    'prev_month_key', prev_mk,
    'show_prev', show_prev,
    'message', ''
  );
END;
$$;

REVOKE ALL ON FUNCTION public.record_compare_savings_dev(numeric, numeric) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.record_compare_savings_dev(numeric, numeric)
  TO authenticated, service_role;
