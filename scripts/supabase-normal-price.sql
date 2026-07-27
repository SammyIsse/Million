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
CREATE OR REPLACE VIEW public.price_history_normal30
WITH (security_invoker = true) AS
SELECT product_id, store, price AS normal_price
FROM (
    SELECT
        product_id, store, price,
        COUNT(*)  AS freq,
        MAX(date) AS last_seen,
        ROW_NUMBER() OVER (
            PARTITION BY product_id, store
            ORDER BY COUNT(*) DESC, price DESC, MAX(date) DESC
        ) AS rn
    FROM public.price_history
    WHERE date::date >= (CURRENT_DATE - INTERVAL '30 days')
    GROUP BY product_id, store, price
) ranked
WHERE rn = 1;

GRANT SELECT ON public.price_history_normal30 TO anon, authenticated, service_role;
