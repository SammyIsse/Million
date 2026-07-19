-- Kør i Supabase SQL Editor.
--
-- Baggrund: verificeret måling mod databasen 19-07-2026 (ikke gætværk).
-- Anon-nøglen ligger offentligt i wrangler.toml, så det blev testet direkte
-- hvad den nøgle faktisk kan. Resultat - kernedataene er sikre:
--
--   app_cache, price_history, nutrition_data : anon kan LÆSE, ikke skrive
--       (UPDATE giver 42501 "permission denied", bekræftet mod hver tabel)
--   produkter        : anon kan hverken læse eller skrive (RLS filtrerer)
--   cart_popularity  : anon kan læse og skrive - tilsigtet, se cart_event()
--                      i app.py; misbrug påvirker kun sortering
--
-- Der er derfor INTET sikkerhedshul at lukke. Nedenstående retter én
-- driftsfejl og rører ikke ved noget andet.

-- ── Eneste ændring: service_role mangler adgang til to tabeller ─────────────
-- Med DEPLOY_KEY svarer begge tabeller 403 / 42501 "permission denied".
-- Postgres' egen hint er præcis de to linjer nedenfor. Konsekvensen af
-- fejlen: intet backend-job kan læse de prisalarmer brugerne opretter via
-- /api/create-alert, så alarmerne kan aldrig udløses.
--
-- Hvorfor det er sikkert: GRANT tilføjer kun rettigheder, og kun til
-- service_role - en ren backend-rolle, hvis nøgle aldrig sendes til browseren
-- (workeren kører med den offentlige nøgle, verificeret i wrangler.toml).
-- Offentligheden får ingen ny adgang. Ingen policies røres, ingen data ændres.

GRANT SELECT, INSERT, UPDATE, DELETE ON public.cart_popularity TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.price_alerts   TO service_role;

-- ── Bevidst IKKE med i dette script ────────────────────────────────────────
--
-- 1) RLS på app_cache. Et tidligere udkast foreslog det. Unødvendigt: anon
--    er allerede afskåret fra at skrive via grants. En forkert policy ville
--    kunne afbryde hjemmesidens læsning af cachen. Lad den være.
--
-- 2) REVOKE EXECUTE på swap_app_cache / swap_produkter_butik. De er givet
--    til anon, hvilket ser forkert ud, men de er ikke udnyttelige: begge er
--    SECURITY INVOKER og kører "DELETE ... ; UPDATE ...". Anon har ikke
--    UPDATE, så funktionen fejler, og hele transaktionen - inklusive DELETE
--    - rulles tilbage. En plpgsql-funktion kan ikke committe undervejs.
--    Sikkerhedsgevinsten ved en REVOKE er dermed nul, mens risikoen ikke er:
--    hvis GitHub-secret'en DEPLOY_KEY mod forventning ikke er service_role-
--    nøglen, stopper den natlige scraper-pipeline. Kør det ikke uden grund.
--
-- 3) Policy-ændringer på cart_popularity / price_alerts. Fejlen er 42501 på
--    en manglende GRANT, ikke en RLS-blokering. At droppe og gendanne
--    policies ville ændre noget der ikke er i stykker.
