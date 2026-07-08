-- Kør i Supabase SQL Editor.
-- Atomisk klik-tæller til cart_popularity: undgår race condition hvor to
-- samtidige klik læser samme count, og det ene klik tabes.
-- app.py kalder funktionen via POST /rest/v1/rpc/increment_cart_count
-- (med læs-så-skriv som fallback, indtil dette script er kørt).

-- ON CONFLICT kræver et unikt indeks (tabellen har i forvejen én række pr. produkt)
CREATE UNIQUE INDEX IF NOT EXISTS cart_popularity_product_id_idx
  ON public.cart_popularity (product_id);

CREATE OR REPLACE FUNCTION public.increment_cart_count(pid text)
RETURNS void
LANGUAGE sql
AS $$
  INSERT INTO public.cart_popularity (product_id, count)
  VALUES (pid, 1)
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity.count + 1;
$$;

GRANT EXECUTE ON FUNCTION public.increment_cart_count(text) TO anon, authenticated, service_role;

-- Oprydning: fjern testrække fra verifikation (anon-nøglen må ikke slette)
DELETE FROM public.cart_popularity WHERE product_id = 'verify_test_race';
