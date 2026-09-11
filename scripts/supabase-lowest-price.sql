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
--
-- 11-09-2026: price_history blev sparse (kun én række pr. prisÆNDRING, se
-- supabase-price-last-seen.sql) i stedet for én række pr. dag. Det simple
-- "WHERE date >= grænse"-filter herunder virkede kun så længe hver dag havde
-- sin egen række - med sparse data forsvinder ethvert produkt hvis pris ikke
-- er ændret i 30+ dage helt fra viewet (ingen rækker >= grænsen), selvom
-- prisen stadig er gældende og velkendt. prune_price_history (samme fil)
-- garanterer at der findes højst én række FØR grænsen pr. (produkt, butik) -
-- "carried_in" henter den ind, så den fremføres ind i vinduet ligesom
-- grafens forward-fill (app.py::get_price_history).
CREATE OR REPLACE VIEW public.price_history_low30
WITH (security_invoker = true) AS
WITH cutoff AS (
  SELECT (CURRENT_DATE - INTERVAL '30 days')::date AS d
),
carried_in AS (
  SELECT DISTINCT ON (ph.product_id, ph.store) ph.product_id, ph.store, ph.price
  FROM public.price_history ph, cutoff
  WHERE ph.date::date < cutoff.d
  ORDER BY ph.product_id, ph.store, ph.date::date DESC
),
in_window AS (
  SELECT ph.product_id, ph.store, ph.price
  FROM public.price_history ph, cutoff
  WHERE ph.date::date >= cutoff.d
),
relevant AS (
  SELECT * FROM carried_in
  UNION ALL
  SELECT * FROM in_window
)
SELECT product_id, MIN(price) AS min_price
FROM relevant
GROUP BY product_id;

GRANT SELECT ON public.price_history_low30 TO anon, authenticated, service_role;
