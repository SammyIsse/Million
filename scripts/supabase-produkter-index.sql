-- Kør i Supabase SQL Editor.
-- produkter forespørges og skrives konstant filtreret på butik uden
-- understøttende indeks - hver af de ~18 scraperes DELETE ... WHERE butik = X
-- (scraper/supabase_utils.py) og updater.py's paginerede
-- SELECT * WHERE butik = X ORDER BY id (linje ~85) lavede et fuldt scan af
-- HELE produkter-tabellen, hver nat, for hver butik.
CREATE INDEX IF NOT EXISTS produkter_butik_id_idx
  ON public.produkter (butik, id);
