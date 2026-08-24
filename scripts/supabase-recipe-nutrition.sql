-- Kør i Supabase SQL Editor, én gang - EFTER scripts/supabase-recipes.sql.
--
-- Næringsindhold for HELE opskriften (ikke kun pr. matchet ingrediens):
--   1) Kildens egen erklæring, hvis den findes - mange madsider har
--      schema.org NutritionInformation i deres Recipe-JSON-LD
--      (recipe_importer.py, se node.get('nutrition')). Autoritativ, men
--      ikke alle sider har den.
--   2) Findes den ikke, estimerer app.py et samlet tal ud fra de matchede
--      ingrediensers pr.-100g-næringsdata × den mængde opskriften bruger -
--      ALTID mærket som estimat i UI'et (usikker string-parsing af
--      blandede kilde-formater, se _fetch_recipe_detail-kommentaren i app.py).

ALTER TABLE public.recipes
  ADD COLUMN IF NOT EXISTS nutrition_source jsonb;

-- supabase-recipes.sql (compliance-audit 19-08-2026, GDPR-031) gav anon/
-- authenticated en KOLONNESPECIFIK SELECT-rettighed på recipes, der bevidst
-- udelader submitted_by. En ny kolonne arver ikke automatisk den rettighed,
-- så uden denne linje ville app.py's _fetch_recipe_detail (som eksplicit
-- selecter nutrition_source) begynde at fejle mod en frisk kørsel.
GRANT SELECT (nutrition_source) ON public.recipes TO anon, authenticated;
