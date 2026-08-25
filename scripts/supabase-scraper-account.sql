-- Kør i Supabase SQL Editor, ÉN GANG, EFTER du har oprettet en dedikeret
-- scraper-bruger under Authentication → Users → Add user.
--
-- Baggrund (compliance-audit 19-08-2026, GDPR-030, plan opdateret 21-08-2026):
-- Alle 15 scraper-workflows bruger i dag service_role-nøglen (DEPLOY_KEY), som
-- omgår al RLS og kan læse ALT - inklusive auth.users og price_alerts.email.
-- En scraper skal kun kunne skrive til produkter.
--
-- Oprindelig plan var at signere et JWT selv til en custom Postgres-rolle,
-- men Supabase er skiftet til asymmetrisk (ECC P-256) JWT-signering - der
-- findes ikke længere en delt hemmelighed man selv kan signere nye tokens
-- med (se Settings → JWT Keys). Denne udgave bruger i stedet Supabases eget,
-- understøttede Auth-system: en rigtig bruger, låst til produkter-tabellen
-- med RLS + auth.uid() - PRÆCIS samme mønster som carts/price_alerts/
-- shared_carts allerede bruger i resten af projektet.
--
-- ---------------------------------------------------------------------------
-- Forudsætning: opret brugeren i dashboardet FØRST
-- ---------------------------------------------------------------------------
-- Authentication → Users → Add user → email (fx scraper-bot@madshopper.dk),
-- en stærk genereret adgangskode, "Auto Confirm User" = ja (så den kan logge
-- ind med det samme uden at bekræfte en mail den ikke kan læse).
--
-- Erstat e-mailen herunder med den, du reelt brugte - policyen slår selv
-- brugerens uuid op via en SECURITY DEFINER-funktion, så du ikke skal
-- kopiere den manuelt.
--
-- RETTET 24-08-2026 efter en live test mod produktionen fejlede: en policy
-- der laver "(SELECT id FROM auth.users WHERE email = ...)" DIREKTE kræver at
-- den kaldende rolle (authenticated) selv har SELECT på auth.users - det har
-- den ikke, og Postgres fejlede med "permission denied for table users"
-- (42501). At give authenticated den rettighed ville løse fejlen, men åbne
-- HELE brugertabellen (alle e-mails m.m.) for enhver indlogget kunde - en
-- alvorlig regression. Løsningen er en lille SECURITY DEFINER-funktion (kører
-- med SKABERENS rettigheder, typisk postgres, som allerede kan læse
-- auth.users), så authenticated kun får lov at EXEKVERE funktionen - ikke
-- læse tabellen selv. Samme mønster som fx delete_own_account() i
-- supabase-carts.sql.

-- ---------------------------------------------------------------------------
-- RLS: produkter har i dag INGEN row level security overhovedet (kun
-- REVOKE/GRANT, se supabase-hardening.sql linje 427-429) - anon/authenticated
-- har nul rettigheder, service_role har det hele. Vi tilføjer RLS + én policy
-- der kun lukker scraper-bot-brugeren ind; alle andre "authenticated"-brugere
-- (dine rigtige kunder) får stadig et GRANT på papiret, men policyen
-- evaluerer altid falsk for dem, så de reelt ser/skriver ingenting.
-- ---------------------------------------------------------------------------
ALTER TABLE public.produkter ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.produkter TO authenticated;

CREATE OR REPLACE FUNCTION public._scraper_bot_uid()
RETURNS uuid
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = public, pg_temp
AS $$
  SELECT id FROM auth.users WHERE email = 'scraper-bot@madshopper.dk';
$$;

-- Kun EXECUTE, aldrig direkte tabeladgang - se begrundelsen ovenfor.
REVOKE ALL ON FUNCTION public._scraper_bot_uid() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public._scraper_bot_uid() TO authenticated;

DROP POLICY IF EXISTS "scraper_bot_full_access" ON public.produkter;
CREATE POLICY "scraper_bot_full_access"
  ON public.produkter
  FOR ALL
  TO authenticated
  USING (auth.uid() = public._scraper_bot_uid())
  WITH CHECK (auth.uid() = public._scraper_bot_uid());

-- service_role er upåvirket: den rolle bypasser RLS helt (Postgres/Supabase-
-- standard), så updater.py og alt andet der stadig bruger DEPLOY_KEY mod
-- denne tabel fortsætter helt uændret.
