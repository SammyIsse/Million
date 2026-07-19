-- Kør i Supabase SQL Editor.
-- Atomisk swap af produkter for én butik: save_to_supabase() (scraper/supabase_utils.py)
-- uploader nye rækker under et "staging"-butiksnavn og kalder derefter denne
-- funktion, som i ÉN transaktion sletter de gamle rækker for butikken og
-- omdøber staging-rækkerne til det rigtige butiksnavn. Så ser en samtidig
-- læser (hjemmesiden) enten den fulde gamle eller den fulde nye butik -
-- aldrig 0 rækker, selvom netværket dør lige mellem slet og omdøb.
--
-- Indtil dette script er kørt, falder save_to_supabase() automatisk tilbage
-- til den gamle (ikke-atomiske) to-kalds-metode - intet går i stykker,
-- data opdateres bare uden denne beskyttelse før scriptet er kørt.

CREATE OR REPLACE FUNCTION public.swap_produkter_butik(target_butik text, staging_butik text)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  DELETE FROM public.produkter WHERE butik = target_butik;
  UPDATE public.produkter SET butik = target_butik WHERE butik = staging_butik;
END;
$$;

-- Kun service_role: samme begrundelse som i supabase-app-cache-swap.sql.
-- Funktionen sletter rækker og kaldes kun fra scraper/supabase_utils.py,
-- der kører med DEPLOY_KEY (workflows sætter SUPABASE_KEY = secrets.DEPLOY_KEY).
GRANT EXECUTE ON FUNCTION public.swap_produkter_butik(text, text) TO service_role;
REVOKE EXECUTE ON FUNCTION public.swap_produkter_butik(text, text) FROM PUBLIC;
