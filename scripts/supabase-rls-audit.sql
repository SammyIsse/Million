-- Kør i Supabase SQL Editor (Dashboard → SQL → New query).
-- REN LÆSNING - ændrer ingenting. Svarer på: kan en fremmed med den
-- offentlige anon-nøgle (som ligger i wrangler.toml og dermed er kendt af
-- alle) skrive til eller slette vores data?
--
-- Baggrund: Supabase giver som standard anon/authenticated brede rettigheder
-- på tabeller i public. Det er RLS - ikke grants - der reelt beskytter.
-- En tabel med RLS = false og INSERT/UPDATE/DELETE til anon kan tømmes af
-- hvem som helst med en enkelt curl-kommando.

-- 1) RLS-status pr. tabel. Alt med rls_enabled = false og skriverettigheder
--    i forespørgsel 2 er kritisk.
SELECT
  c.relname                         AS tabel,
  c.relrowsecurity                  AS rls_enabled,
  c.relforcerowsecurity             AS rls_forced,
  (SELECT count(*) FROM pg_policies p
    WHERE p.schemaname = 'public' AND p.tablename = c.relname) AS antal_policies
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relrowsecurity ASC, c.relname;

-- 2) Hvad må anon/authenticated helt konkret? Kig efter INSERT/UPDATE/DELETE
--    på produkter, app_cache, price_history og nutrition_data - de skal kun
--    kunne læses offentligt; skrivning hører til service_role (updater/scrapers).
SELECT
  table_name                        AS tabel,
  grantee,
  string_agg(privilege_type, ', ' ORDER BY privilege_type) AS rettigheder
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND grantee IN ('anon', 'authenticated')
GROUP BY table_name, grantee
ORDER BY table_name, grantee;

-- 3) Policies i detaljer - hvilke rækker en rolle må røre, og til hvad.
SELECT tablename AS tabel, policyname, cmd AS kommando, roles, qual, with_check
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename, policyname;

-- 4) Funktioner anon må EXECUTE. swap_app_cache og swap_produkter_butik
--    sletter data. De er SECURITY INVOKER (prosecdef = false), så de kun kan
--    gøre skade hvis anon i forvejen har DELETE/UPDATE på tabellen - men de
--    har ingen grund til at være kaldbare for anon overhovedet.
--    Er prosecdef = true på en af dem, er den udnytbar uanset tabelrettigheder.
SELECT
  p.proname                         AS funktion,
  p.prosecdef                       AS security_definer,
  pg_get_userbyid(p.proowner)       AS ejer,
  has_function_privilege('anon', p.oid, 'EXECUTE')          AS anon_maa_koere,
  has_function_privilege('authenticated', p.oid, 'EXECUTE') AS auth_maa_koere
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
ORDER BY p.proname;
