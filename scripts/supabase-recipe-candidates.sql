-- Kør i Supabase SQL Editor, én gang - EFTER scripts/supabase-recipes.sql.
--
-- Personer-skalering (opskrift.html) kan ændre hvor MEGET en ingrediens skal
-- bruges i (fx 300 g -> 1200 g ved 4x flere personer) - så det billigste
-- valg kan skifte fra en lille pakke til en stor, eller kræve flere styk af
-- samme pakke. matched_product_id alene rækker ikke til det: det er ÉT fast
-- produkt, valgt af navne-matchet alene, uden hensyn til pakkestørrelse.
--
-- candidate_product_ids gemmer top-5 navnematch-kandidater (samme
-- fuzzy/kødtype-gates som matched_product_id, se recipe_matching.py), beregnet
-- ÉN gang ved import/moderation (dyrt at genberegne - kræver at scanne hele
-- produkt-cachen). Pris/vægt for hver kandidat slås derimod op LIVE ved hver
-- sidevisning (_fetch_recipe_detail i app.py, samme load_products_by_ids-kald
-- der allerede henter matched_product) - billigt, ingen ny fuzzy-beregning,
-- og prisen er dermed altid frisk selvom kandidat-id'erne er en dag gamle.

ALTER TABLE public.recipe_ingredients
  ADD COLUMN IF NOT EXISTS candidate_product_ids text[] NOT NULL DEFAULT '{}';
