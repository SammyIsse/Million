-- Kør i Supabase SQL Editor, én gang - EFTER scripts/supabase-recipes.sql.
-- "Lækre opskrifter"-forsidesektion: pointsystem der rangerer opskrifter efter
-- klik, med aftagende point pr. klik i takt med at opskriften ældes (så nye
-- opskrifter får en fair chance mod gamle med mange akkumulerede klik).
--
-- Separat tabel (mønster fra cart_popularity, ikke kolonner på recipes selv):
-- recipes er IKKE TABLE_SUFFIX-opdelt (delt indhold på tværs af prod/dev/lokal,
-- se scripts/supabase-recipes.sql), men klik-aktivitet SKAL være det - ellers
-- ville test-klik under lokal/staging-udvikling forurene den rigtige
-- produktions-rangering direkte på samme rækker.
--
-- Pointformel (bekræftet af opdragsgiver 2026-08-01, skala sænket 2026-08-02 -
-- de oprindelige 100/10 gav unødvendigt høje totaler): lineært fald.
--   uge = floor(dage_siden_oprettelse / 7) + 1
--   point_for_dette_klik = greatest(10 - (uge - 1) * 1, 1)
-- Uge 1 = 10 point/klik, uge 2 = 9, ..., gulv på 1 point/klik nås uge 10.
-- Allerede optjente point ændres aldrig - kun værdien af NYE klik falder,
-- håndhævet ved at total_points er en løbende sum (+=), ikke genberegnet.

CREATE TABLE IF NOT EXISTS public.recipe_points (
  recipe_id    bigint PRIMARY KEY REFERENCES public.recipes(id) ON DELETE CASCADE,
  total_points bigint NOT NULL DEFAULT 0,
  click_count  integer NOT NULL DEFAULT 0,
  updated_at   timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.recipe_points TO service_role;
ALTER TABLE public.recipe_points ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.recipe_points;
CREATE POLICY "Service role fuld adgang" ON public.recipe_points
  FOR ALL TO service_role USING (true) WITH CHECK (true);
-- Ingen anon/authenticated-policy: kun record_recipe_click (SECURITY DEFINER
-- nedenfor) og scripts/seed-d1.py (service_role) rører denne tabel. Samme
-- lukket-som-udgangspunkt-model som cart_events (supabase-cart-increment.sql).

CREATE OR REPLACE FUNCTION public.record_recipe_click(p_recipe_id bigint)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_created_at timestamptz;
  v_weeks      integer;
  v_points     integer;
BEGIN
  SELECT created_at INTO v_created_at
  FROM public.recipes
  WHERE id = p_recipe_id AND status = 'approved';

  IF v_created_at IS NULL THEN
    RETURN;  -- opskrift findes ikke/er ikke godkendt - stille no-op, ingen fejl
    -- der afslører om et givent id findes (samme princip som cart-RPC'erne).
  END IF;

  v_weeks  := floor(extract(epoch FROM (now() - v_created_at)) / 604800)::integer + 1;
  v_points := greatest(10 - (v_weeks - 1) * 1, 1);

  INSERT INTO public.recipe_points (recipe_id, total_points, click_count, updated_at)
  VALUES (p_recipe_id, v_points, 1, now())
  ON CONFLICT (recipe_id) DO UPDATE
  SET total_points = public.recipe_points.total_points + v_points,
      click_count   = public.recipe_points.click_count + 1,
      updated_at    = now();
END;
$$;

-- Anonyme klik er OK (som record_cart_activity) - Flask-laget rate-limiter
-- (cart_event_limiter, genbrugt til /api/recipe-click) er det egentlige værn
-- mod misbrug, ikke denne funktion.
GRANT EXECUTE ON FUNCTION public.record_recipe_click(bigint) TO anon, authenticated, service_role;


-- ===========================================================================
-- Dev/staging: recipe_points_dev (samme struktur, samme RPC-navn virker på
-- begge via _table_suffix() i app.py -> rpc/record_recipe_click{suffix} kalder
-- reelt to forskellige funktionsnavne, se funktionen nedenfor for _dev)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS public.recipe_points_dev (
  recipe_id    bigint PRIMARY KEY REFERENCES public.recipes(id) ON DELETE CASCADE,
  total_points bigint NOT NULL DEFAULT 0,
  click_count  integer NOT NULL DEFAULT 0,
  updated_at   timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.recipe_points_dev TO service_role;
ALTER TABLE public.recipe_points_dev ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.recipe_points_dev;
CREATE POLICY "Service role fuld adgang" ON public.recipe_points_dev
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE OR REPLACE FUNCTION public.record_recipe_click_dev(p_recipe_id bigint)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_created_at timestamptz;
  v_weeks      integer;
  v_points     integer;
BEGIN
  SELECT created_at INTO v_created_at
  FROM public.recipes
  WHERE id = p_recipe_id AND status = 'approved';

  IF v_created_at IS NULL THEN
    RETURN;
  END IF;

  v_weeks  := floor(extract(epoch FROM (now() - v_created_at)) / 604800)::integer + 1;
  v_points := greatest(10 - (v_weeks - 1) * 1, 1);

  INSERT INTO public.recipe_points_dev (recipe_id, total_points, click_count, updated_at)
  VALUES (p_recipe_id, v_points, 1, now())
  ON CONFLICT (recipe_id) DO UPDATE
  SET total_points = public.recipe_points_dev.total_points + v_points,
      click_count   = public.recipe_points_dev.click_count + 1,
      updated_at    = now();
END;
$$;

GRANT EXECUTE ON FUNCTION public.record_recipe_click_dev(bigint) TO anon, authenticated, service_role;
