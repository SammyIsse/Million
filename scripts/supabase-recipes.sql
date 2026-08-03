-- Kør i Supabase SQL Editor (Dashboard → SQL → New query), én gang.
-- Opskrift-import og -matching (docs/Features.md "Opskrift-import og -matching
-- system"): opskrifter matches ingrediens-for-ingrediens mod produkt-cachens
-- id'er (samme id som price_history/carts bruger - IKKE produkter.id, den
-- tabel er raa foer-matching scraper-data og laest kun af service_role, se
-- scripts/supabase-hardening.sql), saa "på tilbud lige nu" kan slaas op mod
-- de samme is_sale-felter sitet allerede viser pr. produkt.
--
-- To kilder til rækker, to tillidsniveauer. Ingen AI/Ollama nogen steder i
-- dette system (se recipe_importer.py's moduldokumentation):
--   * Admin-import (URL → recipe_importer.py, kørt lokalt af udvikleren) er
--     allerede kurateret af den der kører scriptet → status='approved' med
--     det samme, submitted_by er NULL. Kildens fremgangsmåde-tekst gemmes
--     bevidst aldrig (ophavsret) - kun fakta (ingredienser, tid, portioner).
--   * Bruger-indsendte opskrifter (submit_recipe-RPC nedenfor) starter altid
--     som 'pending' og godkendes ALDRIG automatisk. Et separat lokalt/manuelt
--     gennemløb (recipe_importer.py::moderate_pending_recipes, samme mønster
--     som updater.py) genmatcher blot ingredienserne mod frisk produktdata -
--     en administrator sætter derefter selv status='approved'/'rejected' i
--     Supabase. Fail-safe er "vent på mennesket", ikke "vis den" - modsat
--     ai_classifier.py's produkt-filter, hvor fail-safe er at inkludere frem
--     for at misse en fødevare.
--
-- OBS ved en allerede-eksisterende recipes-tabel (CREATE TABLE IF NOT EXISTS
-- nedenfor rører intet på et eksisterende bord): Ollama/AI blev fjernet
-- 2026-08-03, og skemaet herunder er renset for de døde CHECK-værdier/
-- kolonner fra dengang. For at bringe en LEVENDE tabel a jour, kør selv (og
-- kun hvis/når det passer dig - dette script gør det ikke automatisk):
--   ALTER TABLE public.recipes DROP CONSTRAINT IF EXISTS recipes_imported_via_check;
--   ALTER TABLE public.recipes ADD CONSTRAINT recipes_imported_via_check
--     CHECK (imported_via IN ('jsonld', 'user_manual'));
--   ALTER TABLE public.recipes DROP COLUMN IF EXISTS ai_quality_score;
--   ALTER TABLE public.recipes DROP COLUMN IF EXISTS ai_quality_notes;
--   ALTER TABLE public.recipe_ingredients DROP CONSTRAINT IF EXISTS recipe_ingredients_match_method_check;
--   ALTER TABLE public.recipe_ingredients ADD CONSTRAINT recipe_ingredients_match_method_check
--     CHECK (match_method IN ('exact', 'fuzzy', 'unmatched'));

-- ---------------------------------------------------------------------------
-- recipes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.recipes (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_url          text UNIQUE,
  source_name         text NOT NULL DEFAULT '',
  title               text NOT NULL,
  image_url           text NOT NULL DEFAULT '',
  servings            integer,
  total_time_minutes  integer,
  instructions        jsonb NOT NULL DEFAULT '[]'::jsonb,
  imported_via        text NOT NULL DEFAULT 'jsonld'
                         CHECK (imported_via IN ('jsonld', 'user_manual')),
  status              text NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'approved', 'rejected')),
  submitted_by        uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  approved_at         timestamptz,

  -- Samme plads-/misbrugsgrænse-princip som carts_items_valid (supabase-carts.sql):
  -- caser TYPE-tjekket foerst, ellers kan jsonb_array_length kaste i stedet for
  -- at afvise raekken, og Postgres garanterer ikke AND-raekkefoelge.
  CONSTRAINT recipes_instructions_valid CHECK (
    CASE WHEN jsonb_typeof(instructions) = 'array'
         THEN jsonb_array_length(instructions) <= 60 AND length(instructions::text) <= 12000
         ELSE false END
  ),
  CONSTRAINT recipes_title_len CHECK (length(title) BETWEEN 1 AND 200)
);

CREATE INDEX IF NOT EXISTS recipes_status_idx ON public.recipes (status);
CREATE INDEX IF NOT EXISTS recipes_submitted_by_idx ON public.recipes (submitted_by);

-- ---------------------------------------------------------------------------
-- recipe_ingredients
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.recipe_ingredients (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  recipe_id           bigint NOT NULL REFERENCES public.recipes(id) ON DELETE CASCADE,
  position            integer NOT NULL DEFAULT 0,
  raw_text            text NOT NULL,
  quantity            numeric,
  unit                text NOT NULL DEFAULT '',
  ingredient_name     text NOT NULL DEFAULT '',
  -- Peger paa produkt-cachens id (Rema-varenummer eller
  -- "<butik>_<md5>"-solokort-id, se updater.py build_store_display_products).
  -- Bevidst UDEN foreign key: det id lever i Supabase app_cache/D1, ikke i en
  -- SQL-tabel her - samme løse kobling som price_history/carts bruger til det
  -- samme id.
  matched_product_id  text,
  match_confidence    numeric,
  match_method        text NOT NULL DEFAULT 'unmatched'
                         CHECK (match_method IN ('exact', 'fuzzy', 'unmatched')),

  CONSTRAINT recipe_ingredients_raw_text_len CHECK (length(raw_text) BETWEEN 1 AND 300)
);

CREATE INDEX IF NOT EXISTS recipe_ingredients_recipe_id_idx ON public.recipe_ingredients (recipe_id);
CREATE INDEX IF NOT EXISTS recipe_ingredients_matched_product_id_idx ON public.recipe_ingredients (matched_product_id);

-- ---------------------------------------------------------------------------
-- recipe_price_snapshot - forudberegnet, genbygges nightly sammen med app_cache
-- (se docs/Features.md § prisberegning). Ét opslag pr. opskrift ved
-- sidevisning i stedet for en live join mod aktuelle priser - samme grund som
-- home_data_v1 (KV): tung per-request-beregning på edge var årsagen til
-- nedbruddet 2026-07-19.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.recipe_price_snapshot (
  recipe_id                 bigint PRIMARY KEY REFERENCES public.recipes(id) ON DELETE CASCADE,
  computed_at                timestamptz NOT NULL DEFAULT now(),
  cheapest_total_price       numeric,
  matched_ingredient_count   integer NOT NULL DEFAULT 0,
  total_ingredient_count     integer NOT NULL DEFAULT 0,
  ingredients_on_sale_count  integer NOT NULL DEFAULT 0,
  cheapest_store_breakdown   jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------------
-- Grants + RLS
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON public.recipes TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.recipe_ingredients TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.recipe_price_snapshot TO service_role;

-- Kun SELECT til anon/authenticated - ingen direkte tabelskrivning fra
-- browseren (CLAUDE.md § Sikkerhed). Bruger-indsendelse går udelukkende
-- gennem submit_recipe-RPC'en nedenfor.
GRANT SELECT ON public.recipes TO anon, authenticated;
GRANT SELECT ON public.recipe_ingredients TO anon, authenticated;
GRANT SELECT ON public.recipe_price_snapshot TO anon, authenticated;

ALTER TABLE public.recipes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.recipe_ingredients ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.recipe_price_snapshot ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.recipes;
CREATE POLICY "Service role fuld adgang" ON public.recipes
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.recipe_ingredients;
CREATE POLICY "Service role fuld adgang" ON public.recipe_ingredients
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.recipe_price_snapshot;
CREATE POLICY "Service role fuld adgang" ON public.recipe_price_snapshot
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Offentligt synlige er kun godkendte opskrifter - PLUS ens egne indsendelser
-- uanset status, saa "Mine opskrifter" kan vise "afventer godkendelse".
DROP POLICY IF EXISTS "Godkendte + egne opskrifter" ON public.recipes;
CREATE POLICY "Godkendte + egne opskrifter" ON public.recipes
  FOR SELECT TO anon, authenticated
  USING (status = 'approved' OR auth.uid() = submitted_by);

DROP POLICY IF EXISTS "Ingredienser til synlige opskrifter" ON public.recipe_ingredients;
CREATE POLICY "Ingredienser til synlige opskrifter" ON public.recipe_ingredients
  FOR SELECT TO anon, authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.recipes r
      WHERE r.id = recipe_ingredients.recipe_id
        AND (r.status = 'approved' OR auth.uid() = r.submitted_by)
    )
  );

DROP POLICY IF EXISTS "Prisdata til synlige opskrifter" ON public.recipe_price_snapshot;
CREATE POLICY "Prisdata til synlige opskrifter" ON public.recipe_price_snapshot
  FOR SELECT TO anon, authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.recipes r
      WHERE r.id = recipe_price_snapshot.recipe_id
        AND (r.status = 'approved' OR auth.uid() = r.submitted_by)
    )
  );

-- ---------------------------------------------------------------------------
-- submit_recipe - eneste skrivevej for brugere. SECURITY DEFINER, saa den kan
-- indsætte trods RLS ovenfor, men validerer alt selv i stedet for at stole på
-- klienten (samme princip som record_cart_activity, supabase-cart-increment.sql).
-- Ingredienser gemmes KUN som raa_text her - kvantitet/enhed-parsing og
-- produkt-matching sker offline af recipe_matching.py (kræver produkt-cachen,
-- som ikke er en SQL-tabel RPC'en kan joine mod).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.submit_recipe(
  p_title       text,
  p_source_url  text,
  p_image_url   text,
  p_servings    integer,
  p_time_min    integer,
  p_instructions jsonb,
  p_ingredients  text[]
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_recipe_id bigint;
  v_ingredient text;
  v_position integer := 0;
BEGIN
  IF auth.uid() IS NULL THEN
    RAISE EXCEPTION 'login required';
  END IF;
  IF p_title IS NULL OR length(trim(p_title)) < 1 OR length(p_title) > 200 THEN
    RAISE EXCEPTION 'invalid title';
  END IF;
  IF p_ingredients IS NULL OR array_length(p_ingredients, 1) IS NULL
     OR array_length(p_ingredients, 1) < 1 OR array_length(p_ingredients, 1) > 60 THEN
    RAISE EXCEPTION 'invalid ingredient count';
  END IF;
  IF p_instructions IS NULL THEN
    p_instructions := '[]'::jsonb;
  END IF;
  IF jsonb_typeof(p_instructions) != 'array'
     OR jsonb_array_length(p_instructions) > 60
     OR length(p_instructions::text) > 12000 THEN
    RAISE EXCEPTION 'invalid instructions';
  END IF;

  INSERT INTO public.recipes (
    title, source_url, source_name, image_url, servings, total_time_minutes,
    instructions, imported_via, status, submitted_by
  ) VALUES (
    trim(p_title),
    NULLIF(trim(coalesce(p_source_url, '')), ''),
    'Bruger',
    coalesce(p_image_url, ''),
    p_servings,
    p_time_min,
    p_instructions,
    'user_manual',
    'pending',
    auth.uid()
  )
  RETURNING id INTO v_recipe_id;

  FOREACH v_ingredient IN ARRAY p_ingredients LOOP
    IF v_ingredient IS NOT NULL AND length(trim(v_ingredient)) > 0 THEN
      IF length(v_ingredient) > 300 THEN
        RAISE EXCEPTION 'ingredient line too long';
      END IF;
      INSERT INTO public.recipe_ingredients (recipe_id, position, raw_text)
      VALUES (v_recipe_id, v_position, trim(v_ingredient));
      v_position := v_position + 1;
    END IF;
  END LOOP;

  RETURN v_recipe_id;
END;
$$;

GRANT EXECUTE ON FUNCTION public.submit_recipe(text, text, text, integer, integer, jsonb, text[]) TO authenticated;
REVOKE EXECUTE ON FUNCTION public.submit_recipe(text, text, text, integer, integer, jsonb, text[]) FROM PUBLIC, anon;
