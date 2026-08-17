-- Kør i Supabase SQL Editor.
-- Samme atomiske staging-swap som supabase-produkter-swap.sql, men SCOPET til
-- en kategori-delmængde af en butik. Flere scrapere ejer kun en del af en
-- butiks rækker (fx en tilbudsavis-scraper der kun må røre kategori='Tilbudsavis',
-- mens en separat katalog-scraper ejer kategori='Katalog' for samme butik).
-- Den almindelige swap_produkter_butik() sletter og omdøber på tværs af HELE
-- butikken - kaldt fra en scoped scraper ville den ustraffet udslette den
-- anden scrapers rækker (fx katalog-data) hver nat.
--
-- save_product_dicts() (scraper/supabase_utils.py) kalder denne, når den er
-- kaldt med delete_eq_kategori eller delete_neq_kategori, og falder tilbage
-- til den plain swap_produkter_butik() når ingen af de to er sat.
--
-- Indtil dette script er kørt, falder save_product_dicts() automatisk tilbage
-- til den gamle (ikke-atomiske) to-kalds-metode - intet går i stykker,
-- data opdateres bare uden denne beskyttelse før scriptet er kørt.

CREATE OR REPLACE FUNCTION public.swap_produkter_butik_scoped(
  target_butik   text,
  staging_butik  text,
  kategori_eq    text DEFAULT NULL,
  kategori_neq   text DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
  IF kategori_eq IS NOT NULL THEN
    DELETE FROM public.produkter WHERE butik = target_butik AND kategori = kategori_eq;
  ELSIF kategori_neq IS NOT NULL THEN
    DELETE FROM public.produkter WHERE butik = target_butik AND kategori <> kategori_neq;
  ELSE
    DELETE FROM public.produkter WHERE butik = target_butik;
  END IF;
  UPDATE public.produkter SET butik = target_butik WHERE butik = staging_butik;
END;
$$;

-- Kun service_role: samme begrundelse som i supabase-produkter-swap.sql.
-- Funktionen sletter rækker og kaldes kun fra scraper/supabase_utils.py,
-- der kører med DEPLOY_KEY (workflows sætter SUPABASE_KEY = secrets.DEPLOY_KEY).
GRANT EXECUTE ON FUNCTION public.swap_produkter_butik_scoped(text, text, text, text) TO service_role;
REVOKE EXECUTE ON FUNCTION public.swap_produkter_butik_scoped(text, text, text, text) FROM PUBLIC, anon, authenticated;
