-- Kør i Supabase SQL Editor.
-- View med laveste registrerede pris pr. produkt de seneste 30 dage (på tværs
-- af butikker). Læses af updater.py, som stempler '/product/lowest_price_30d'
-- ind i produkt-cachen til "30 dages laveste"-badget på produktkortene.

-- price_history.date er type text (format 'YYYY-MM-DD'), ikke date/timestamp -
-- deraf ::date-cast'et, ellers fejler sammenligningen med 42883.
--
-- security_invoker = true: viewet kører med FORESPØRGERENS rettigheder (ikke
-- ejerens), så det respekterer RLS på price_history. Fjerner Supabase-linterens
-- "Security Definer View"-advarsel. Sikkert her: service_role (updater) springer
-- RLS over, og anon/authenticated har "Offentlig læsning"-policy på price_history.
CREATE OR REPLACE VIEW public.price_history_low30
WITH (security_invoker = true) AS
SELECT product_id, MIN(price) AS min_price
FROM public.price_history
WHERE date::date >= (CURRENT_DATE - INTERVAL '30 days')
GROUP BY product_id;

GRANT SELECT ON public.price_history_low30 TO anon, authenticated, service_role;
