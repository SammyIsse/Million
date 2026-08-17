-- Kør i Supabase SQL Editor. REN LÆSNING - ændrer ingenting.
--
-- Baggrund: databaserevision 17-08-2026 (fund I2). produkter, app_cache,
-- price_history og cart_popularity har - i modsætning til alle ~18 andre
-- tabeller i scripts/supabase-*.sql - INTET CREATE TABLE-script i dette repo.
-- De blev tilsyneladende oprettet direkte i Supabase-dashboardet, før denne
-- fils og de øvrige scripts/supabase-*.sql-filers konvention opstod. De
-- øvrige scripts (supabase-hardening.sql, supabase-grants.sql,
-- supabase-lowest-price.sql, supabase-produkter-index.sql m.fl.) forudsætter
-- alle at disse fire tabeller allerede findes.
--
-- Konsekvens: hvis Supabase-projektet nogensinde skal genskabes fra bunden ud
-- fra kun dette repo, mangler skemaet for de fire vigtigste tabeller. Dette
-- script er IKKE en erstatning for et rigtigt CREATE TABLE-script (at gætte
-- kolonnetyper fra applikationskoden ville risikere at være forkert og dermed
-- værre end slet ingen dokumentation) - det er i stedet et værktøj til at
-- udtrække den FAKTISKE, autoritative skema-definition fra det kørende
-- projekt. Kør det, og gem outputtet et sted holdbart (fx som en kommentar
-- øverst i en fremtidig supabase-core-tables-CREATE.sql, eller i docs/).
--
-- Ingen tabeller ændres eller berøres - kun information_schema/pg_catalog
-- forespørges.

-- 1) Kolonner, typer, nullability, defaults for de fire tabeller.
SELECT
  c.table_name,
  c.ordinal_position,
  c.column_name,
  c.data_type,
  c.character_maximum_length,
  c.numeric_precision,
  c.numeric_scale,
  c.is_nullable,
  c.column_default
FROM information_schema.columns c
WHERE c.table_schema = 'public'
  AND c.table_name IN ('produkter', 'app_cache', 'price_history', 'cart_popularity')
ORDER BY c.table_name, c.ordinal_position;

-- 2) Primærnøgler og unikke constraints.
SELECT
  tc.table_name,
  tc.constraint_name,
  tc.constraint_type,
  string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position) AS kolonner
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
WHERE tc.table_schema = 'public'
  AND tc.table_name IN ('produkter', 'app_cache', 'price_history', 'cart_popularity')
  AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
GROUP BY tc.table_name, tc.constraint_name, tc.constraint_type
ORDER BY tc.table_name, tc.constraint_type;

-- 3) CHECK-constraints (fx tilbud/normalpris-værdisæt, hvis nogen findes).
SELECT
  tc.table_name,
  tc.constraint_name,
  cc.check_clause
FROM information_schema.table_constraints tc
JOIN information_schema.check_constraints cc
  ON cc.constraint_name = tc.constraint_name AND cc.constraint_schema = tc.table_schema
WHERE tc.table_schema = 'public'
  AND tc.table_name IN ('produkter', 'app_cache', 'price_history', 'cart_popularity')
  AND tc.constraint_type = 'CHECK'
ORDER BY tc.table_name;

-- 4) Indekser (bekræfter bl.a. produkter_butik_id_idx,
--    cart_popularity_product_id_idx, cart_popularity_count_idx m.fl.).
SELECT
  tablename,
  indexname,
  indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('produkter', 'app_cache', 'price_history', 'cart_popularity')
ORDER BY tablename, indexname;

-- 5) Fremmednøgler FRA eller TIL disse tabeller (forventes ingen - de fire er
--    bevidst løst koblet til resten af skemaet via tekst-id'er, ikke FK'er,
--    jf. kommentarerne i scripts/supabase-recipes.sql om matched_product_id).
SELECT
  tc.table_name AS fra_tabel,
  kcu.column_name AS fra_kolonne,
  ccu.table_name AS til_tabel,
  ccu.column_name AS til_kolonne,
  tc.constraint_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema = 'public'
  AND (
    tc.table_name IN ('produkter', 'app_cache', 'price_history', 'cart_popularity')
    OR ccu.table_name IN ('produkter', 'app_cache', 'price_history', 'cart_popularity')
  );

-- 6) Row-count pr. tabel (til sammenligning med forventede størrelser -
--    produkter forventes ~8000+, jf. dæknings-værnet i scripts/seed-d1.py).
SELECT 'produkter' AS tabel, count(*) FROM public.produkter
UNION ALL SELECT 'app_cache', count(*) FROM public.app_cache
UNION ALL SELECT 'price_history', count(*) FROM public.price_history
UNION ALL SELECT 'cart_popularity', count(*) FROM public.cart_popularity;
