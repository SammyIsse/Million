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
