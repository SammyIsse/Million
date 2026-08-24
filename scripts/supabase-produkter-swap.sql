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
SET search_path = public, pg_temp
AS $$
BEGIN
  DELETE FROM public.produkter WHERE butik = target_butik;
  UPDATE public.produkter SET butik = target_butik WHERE butik = staging_butik;
END;
$$;

-- service_role - uændret. authenticated tilføjet 24-08-2026 (compliance-audit
-- 19-08-2026, GDPR-030): scraperne er ved at flytte væk fra service_role til
-- en dedikeret, RLS-begrænset Supabase Auth-bruger (scripts/supabase-scraper-
-- account.sql), og denne funktion er IKKE SECURITY DEFINER - den kører derfor
-- med KALDERENS rettigheder, og dens interne DELETE/UPDATE rammer stadig
-- RLS-policyen på produkter. En almindelig kunde kan godt KALDE funktionen,
-- men RLS-policyen (auth.uid() = kun scraper-bot) gør de interne DELETE/
-- UPDATE til et no-op for enhver anden bruger - grant'et alene giver ingen
-- reel adgang, RLS-policyen er det der beskytter.
GRANT EXECUTE ON FUNCTION public.swap_produkter_butik(text, text) TO service_role, authenticated;
REVOKE EXECUTE ON FUNCTION public.swap_produkter_butik(text, text) FROM PUBLIC, anon;
