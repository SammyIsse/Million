-- Kør i Supabase SQL Editor.
-- View med laveste registrerede pris pr. produkt de seneste 30 dage (på tværs
-- af butikker). Læses af updater.py, som stempler '/product/lowest_price_30d'
-- ind i produkt-cachen til "30 dages laveste"-badget på produktkortene.

CREATE OR REPLACE VIEW public.price_history_low30 AS
SELECT product_id, MIN(price) AS min_price
FROM public.price_history
WHERE date >= (CURRENT_DATE - INTERVAL '30 days')
GROUP BY product_id;

GRANT SELECT ON public.price_history_low30 TO anon, authenticated, service_role;
