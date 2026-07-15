-- Kør i Supabase SQL Editor (Dashboard → SQL → New query).
-- Næringsindhold pr. varekort-nøgle (rema:<id> eller ean:<ean>), bygget offline
-- af scripts/build-nutrition.py. Læses af GET /api/nutrition/<id> via samme
-- _supabase_rest-helper som prishistorik (app.py).

CREATE TABLE IF NOT EXISTS public.nutrition_data (
  key        text PRIMARY KEY,
  payload    jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.nutrition_data TO service_role;
GRANT SELECT ON public.nutrition_data TO anon, authenticated;

ALTER TABLE public.nutrition_data ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.nutrition_data;
CREATE POLICY "Service role fuld adgang"
  ON public.nutrition_data
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);

DROP POLICY IF EXISTS "Offentlig læsning" ON public.nutrition_data;
CREATE POLICY "Offentlig læsning"
  ON public.nutrition_data
  FOR SELECT
  TO anon, authenticated
  USING (true);
