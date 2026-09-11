-- Kør i Supabase SQL Editor.
-- View med "typisk" (mest hyppige) pris pr. produkt+butik de seneste 30 dage.
-- Læses af updater.py (_fetch_normal_prices_30d) som fallback-førpris, når en
-- butiks-scraper markerer en vare som "tilbud" uden selv at levere en
-- førpris (fx Bilkas multikøbs-kampagner uden beforePrice, se
-- scraper/bilka_katalog.py). Uden dette fald build_store_display_products/
-- _apply_cheapest_display tilbage til at vise tilbudsprisen som "førpris"
-- også - to identiske priser på samme kort.
--
-- price_history.date er type text (format 'YYYY-MM-DD'), ikke date/timestamp -
-- deraf ::date-cast'et, ellers fejler sammenligningen med 42883.
--
-- Gruppen (product_id, store, price) tælles og rangeres pr. (product_id,
-- store) - den hyppigst forekommende pris de seneste 30 dage vindes som
-- "normalpris"; ved uafgjort vælges den højeste og dernæst den senest sete.
--
-- security_invoker = true: viewet kører med FORESPØRGERENS rettigheder (ikke
-- ejerens), så det respekterer RLS på price_history - samme mønster som
-- price_history_low30 (se supabase-lowest-price.sql).
--
-- 11-09-2026: price_history blev sparse (kun én række pr. prisÆNDRING, se
-- supabase-price-last-seen.sql). Den oprindelige "COUNT(*) pr. pris"-logik
-- herunder forudsatte én række pr. dag - med sparse data har en pris der
-- ALDRIG har ændret sig kun freq=1, præcis som enhver anden pris, og
-- "typisk pris"-begrebet giver ikke længere mening ud fra rækkeantal.
-- Erstattet med en varighedsvægtet udgave: hver række dækker perioden fra
-- sin egen dato til NÆSTE registrerede ændring for samme par (eller til i
-- dag) - klippet til de seneste 30 dage - og "typisk" bliver den pris der
-- reelt var gældende flest dage, ikke den der har flest rækker.
CREATE OR REPLACE VIEW public.price_history_normal30
WITH (security_invoker = true) AS
WITH cutoff AS (
  SELECT (CURRENT_DATE - INTERVAL '30 days')::date AS c, CURRENT_DATE AS today
),
segments AS (
  SELECT
    ph.product_id, ph.store, ph.price,
    GREATEST(ph.date::date, cutoff.c) AS seg_start,
    LEAST(
      COALESCE(
        LEAD(ph.date::date) OVER (PARTITION BY ph.product_id, ph.store ORDER BY ph.date::date),
        cutoff.today + 1
      ),
      cutoff.today + 1
    ) AS seg_end
  FROM public.price_history ph, cutoff
),
clipped AS (
  SELECT product_id, store, price, (seg_end - seg_start) AS days
  FROM segments
  WHERE seg_end > seg_start
),
ranked AS (
    SELECT
        product_id, store, price,
        SUM(days) AS days_active,
        ROW_NUMBER() OVER (
            PARTITION BY product_id, store
            ORDER BY SUM(days) DESC, price DESC
        ) AS rn
    FROM clipped
    GROUP BY product_id, store, price
)
SELECT product_id, store, price AS normal_price
FROM ranked
WHERE rn = 1;

GRANT SELECT ON public.price_history_normal30 TO anon, authenticated, service_role;
