-- Kør i Supabase SQL Editor.
-- Atomisk swap af app_cache: updater.py uploader nye chunks til et
-- "staging"-id-space (id >= staging_offset) og kalder derefter denne funktion,
-- som i ÉN transaktion sletter de gamle rækker og flytter staging-rækkerne ned
-- på deres rigtige id'er (0, 1, 2, ...). Så ser en samtidig læser (hjemmesiden)
-- enten den fulde gamle cache eller den fulde nye - aldrig en tom/halv cache,
-- selvom en tidligere upload fejlede midtvejs.
--
-- Indtil dette script er kørt, falder updater.py automatisk tilbage til den
-- gamle (ikke-atomiske) metode - intet går i stykker, cachen opdateres bare
-- uden denne beskyttelse før scriptet er kørt.

CREATE OR REPLACE FUNCTION public.swap_app_cache(staging_offset bigint DEFAULT 1000000)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  DELETE FROM public.app_cache WHERE id < staging_offset;
  UPDATE public.app_cache SET id = id - staging_offset WHERE id >= staging_offset;
END;
$$;

GRANT EXECUTE ON FUNCTION public.swap_app_cache(bigint) TO anon, authenticated, service_role;
