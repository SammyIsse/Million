-- ===========================================================================
-- MadShopper - personlig månedlig besparelse (kør i Supabase SQL Editor)
-- ===========================================================================
-- Én række pr. bruger. Ved "Sammenlign priser" lægges
-- (dyreste butik − billigste butik) til månedens total (kræver login).
-- Percentil: rangorden blandt månedens brugere → Top X% (heltal, bedst = 1%).
-- Bevidst IKKE en ratio mod MAX(amount) - amount er klient-input, og en
-- ratio mod max betyder at én bruger med et højt selvrapporteret beløb kan
-- trække alle andres percentil ned. RANK() er immunt, fordi det kun kigger
-- på orden, ikke størrelsen af forspringet til nummer ét.
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
--   top_pct = CEIL(100 * rang / antal_brugere)  (rang 1 = højeste beløb)
-- se begrundelsen i filens header.

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
  my_rank bigint;
  total_cnt bigint;
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

  -- Percentil er RANGORDEN blandt månedens brugere, ikke en ratio mod det
  -- højeste beløb (MAX). amount er klient-input (kappet 0..100000, men
  -- ellers utroværdigt), og en ratio mod MAX betød at ÉN bruger, der satte
  -- sit eget beløb til loftet, trak stort set alle andres "Top X%" ned mod
  -- 100% for resten af måneden. RANK() afhænger kun af ORDEN, ikke af
  -- størrelsen på afstanden til nummer ét, så det er immunt over for præcis
  -- det misbrug.
  SELECT ranked.rnk, ranked.cnt INTO my_rank, total_cnt
  FROM (
    SELECT user_id,
           RANK() OVER (ORDER BY amount DESC) AS rnk,
           COUNT(*) OVER ()                   AS cnt
    FROM public.user_monthly_savings
    WHERE month_key = mk
  ) ranked
  WHERE ranked.user_id = uid;

  IF total_cnt IS NULL OR total_cnt <= 1 OR amt <= 0 THEN
    top_pct := 100;
  ELSE
    top_pct := LEAST(100, GREATEST(1, CEIL(100.0 * my_rank / total_cnt)))::integer;
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
  my_rank bigint;
  total_cnt bigint;
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

  -- Pris-sanity: cheap/expensive er klient-beregnede kurv-totaler og kan
  -- kaldes direkte med et gyldigt JWT uden om selve prissammenligningen
  -- (produktionsrevision 18-08-2026, blokerer #8) - de daglige/livsvarige
  -- lofter nedenfor beskytter ANDRE brugeres percentil (RANK() er allerede
  -- immun over for det, se kommentaren i filens header), men intet forhindrede
  -- før en bruger i at rapportere et urealistisk spring for sin EGEN kurv, fx
  -- cheap=1, expensive=5000. 5x er samme størrelsesorden som pris-sanity-tjekket
  -- i updater.py's matchmotor og er langt over enhver ægte kurv-prisforskel.
  IF cheap > 0 AND expensive > cheap * 5 THEN
    expensive := cheap * 5;
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

  -- Rangordens-percentil, se kommentaren i get_personal_savings().
  SELECT ranked.rnk, ranked.cnt INTO my_rank, total_cnt
  FROM (
    SELECT user_id,
           RANK() OVER (ORDER BY amount DESC) AS rnk,
           COUNT(*) OVER ()                   AS cnt
    FROM public.user_monthly_savings
    WHERE month_key = mk
  ) ranked
  WHERE ranked.user_id = uid;

  IF total_cnt IS NULL OR total_cnt <= 1 OR amt <= 0 THEN
    top_pct := 100;
  ELSE
    top_pct := LEAST(100, GREATEST(1, CEIL(100.0 * my_rank / total_cnt)))::integer;
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
  my_rank bigint;
  total_cnt bigint;
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

  SELECT ranked.rnk, ranked.cnt INTO my_rank, total_cnt
  FROM (
    SELECT user_id,
           RANK() OVER (ORDER BY amount DESC) AS rnk,
           COUNT(*) OVER ()                   AS cnt
    FROM public.user_monthly_savings_dev
    WHERE month_key = mk
  ) ranked
  WHERE ranked.user_id = uid;

  IF total_cnt IS NULL OR total_cnt <= 1 OR amt <= 0 THEN
    top_pct := 100;
  ELSE
    top_pct := LEAST(100, GREATEST(1, CEIL(100.0 * my_rank / total_cnt)))::integer;
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
  my_rank bigint;
  total_cnt bigint;
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

  -- Pris-sanity, se begrundelsen ved samme tjek i record_compare_savings().
  IF cheap > 0 AND expensive > cheap * 5 THEN
    expensive := cheap * 5;
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

  SELECT ranked.rnk, ranked.cnt INTO my_rank, total_cnt
  FROM (
    SELECT user_id,
           RANK() OVER (ORDER BY amount DESC) AS rnk,
           COUNT(*) OVER ()                   AS cnt
    FROM public.user_monthly_savings_dev
    WHERE month_key = mk
  ) ranked
  WHERE ranked.user_id = uid;

  IF total_cnt IS NULL OR total_cnt <= 1 OR amt <= 0 THEN
    top_pct := 100;
  ELSE
    top_pct := LEAST(100, GREATEST(1, CEIL(100.0 * my_rank / total_cnt)))::integer;
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
