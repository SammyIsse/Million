-- Kør i Supabase SQL Editor, ÉN GANG - og aktivér den DEREFTER manuelt i
-- Supabase Dashboard → Authentication → Hooks → "Before user created".
--
-- ⚠️ Denne hook kører for HVER ny bruger (email/password OG Google/Apple), og
-- en fejl i logikken kan blokere ALLE nye kontooprettelser. Skrevet under
-- produktionsrevisionen 18-08-2026 (blokerer #5) uden adgang til en levende
-- Supabase-instans - kontraktten (event-JSON-formen, extensions.http_post) er
-- verificeret mod Supabases egen dokumentation, men selve kørslen er ALDRIG
-- testet i praksis.
--
-- Der findes IKKE et isoleret dev/staging Auth-miljø at teste imod først:
-- staging og produktion deler samme Supabase-projekt/Auth-instans (kun
-- databasetabellerne er adskilt via _dev-suffiks), så aktivering af hook'et
-- gælder ALLE signups overalt med det samme. Test derfor med en rigtig
-- testbruger LIGE EFTER aktivering, på et tidspunkt med lav trafik, og hav
-- Dashboard-siden åben klar til at slå hook'et fra igen hvis noget går galt.
--
-- ---------------------------------------------------------------------------
-- Hvorfor
-- ---------------------------------------------------------------------------
-- static/js/auth.js kalder turnstile-siteverify-madshopper-workeren FØR
-- SB.auth.signUp() - men det er kun et JS-lag. Et rent POST mod Supabases
-- eget /auth/v1/signup med den offentlige anon-nøgle (fx via curl) springer
-- hele Turnstile-tjekket over, fordi Supabase Auth selv intet ved om det.
-- Denne "before user created"-hook flytter den AUTORITATIVE verificering
-- server-side, ind i selve kontooprettelsen - den kan ikke omgås uden om
-- Supabase Auth.
--
-- VIGTIGT: fordi hooket ogsaa fyrer for Google/Apple-login, tjekkes Turnstile
-- KUN naar provider = 'email'. Et Turnstile-token giver ingen mening for et
-- login der aldrig rammer vores egen signup-formular.
--
-- VIGTIGT #2: et Turnstile-token er ENGANGS - Cloudflare afviser et token der
-- allerede er blevet verificeret én gang. auth.js's tidligere klient-side
-- fetch() mod verificerings-workeren FØR signUp() SKAL fjernes samtidig med
-- at denne hook aktiveres, ellers bliver ethvert token allerede "brugt op" af
-- klienten, og hook'et afviser derefter ALLE signups. Se ændringen i
-- static/js/auth.js (submitForm): klienten sender nu kun tokenet med i
-- signUp()'s options.data, uden selv at kalde verificerings-workeren først.
--
-- ---------------------------------------------------------------------------
-- Forudsætning: http-extension til synkrone udgående kald fra Postgres
-- ---------------------------------------------------------------------------
create extension if not exists http with schema extensions;

-- ---------------------------------------------------------------------------
-- Hook-funktion
-- ---------------------------------------------------------------------------
create or replace function public.hook_verify_signup_turnstile(event jsonb)
returns jsonb
language plpgsql
set search_path = public, extensions, pg_temp
as $$
declare
  provider text;
  token text;
  http_result record;
  verified boolean;
begin
  provider := event->'user'->'app_metadata'->>'provider';

  -- Kun email/password-signup har et Turnstile-token at tjekke - Google/Apple
  -- rammer aldrig vores signup-formular og skal ikke afvises her.
  if provider IS DISTINCT FROM 'email' THEN
    RETURN '{}'::jsonb;
  END IF;

  token := event->'user'->'user_metadata'->>'turnstile_token';
  if token IS NULL OR length(token) = 0 THEN
    RETURN jsonb_build_object(
      'error', jsonb_build_object(
        'message', 'Bot-tjek mangler. Genindlæs siden og prøv igen.',
        'http_code', 400
      )
    );
  END IF;

  -- Lukket ved manglende/ugyldigt token, ÅBENT ved netværksfejl mod selve
  -- verificerings-workeren - samme afvejning som _verify_turnstile_token() i
  -- app.py (bruges til /api/feedback): at blokere ALLE signups fordi en
  -- ekstern afhaengighed er nede er en vaerre fejltilstand end midlertidigt
  -- at miste bot-beskyttelsen.
  BEGIN
    SELECT r.status, r.content::jsonb INTO http_result
    FROM extensions.http_post(
      'https://turnstile-siteverify-madshopper.kasp478g.workers.dev',
      jsonb_build_object('token', token)::text,
      'application/json'
    ) AS r;
  EXCEPTION WHEN OTHERS THEN
    RETURN '{}'::jsonb;   -- verificerings-workeren uden for raekkevidde - tillad
  END;

  IF http_result IS NULL THEN
    RETURN '{}'::jsonb;
  END IF;

  verified := COALESCE((http_result.content::jsonb->>'success')::boolean, false);
  IF verified THEN
    RETURN '{}'::jsonb;
  END IF;

  RETURN jsonb_build_object(
    'error', jsonb_build_object(
      'message', 'Bot-tjek fejlede. Prøv igen.',
      'http_code', 400
    )
  );
end;
$$;

-- Kun auth-tjenesten selv må kalde hook'et - ingen anden rolle skal kunne det.
GRANT EXECUTE ON FUNCTION public.hook_verify_signup_turnstile(jsonb) TO supabase_auth_admin;
REVOKE EXECUTE ON FUNCTION public.hook_verify_signup_turnstile(jsonb) FROM authenticated, anon, public;

-- ---------------------------------------------------------------------------
-- Sidste trin (kan IKKE gøres fra SQL Editor)
-- ---------------------------------------------------------------------------
-- Supabase Dashboard → Authentication → Hooks → "Before user created" →
-- vælg public.hook_verify_signup_turnstile → Enable. Test straks bagefter
-- (se guiden), og hav siden klar til at slå den fra igen hvis noget går galt.
