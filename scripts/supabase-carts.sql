-- Kør i Supabase SQL Editor (Dashboard → SQL → New query), én gang.
-- Opretter gemt-kurv-tabellen til brugerkonti + selvbetjent kontosletning.
--
-- Sikkerhedsmodel (kravet: en besøgende må INTET kunne ødelægge i databasen):
--   * carts er kun tilgængelig for rollen `authenticated` (indloggede brugere).
--     `anon` (den offentlige nøgle browseren bruger uden login) får hverken
--     grant eller policy og er dermed låst helt ude.
--   * RLS binder hver bruger til SIN EGEN række (auth.uid() = user_id), så en
--     bruger aldrig kan læse eller ændre en anden brugers kurv - håndhævet af
--     Postgres, ikke af app-kode, og kan derfor ikke omgås fra browseren.
--   * En CHECK-constraint capper rækkens størrelse, så en bruger ikke kan puste
--     databasen op (minimal-plads-kravet er også et sikkerhedskrav her).
--   * Kerne-tabellerne (produkter, app_cache, price_history) er allerede låst
--     for anon/authenticated - se scripts/supabase-lockdown.sql. Dette script
--     giver INGEN ny adgang til dem.

-- ---------------------------------------------------------------------------
-- Tabel: én række pr. bruger
-- ---------------------------------------------------------------------------
-- items gemmer KUN [{"p": "<product_id>", "q": <antal>}, ...] - navn, pris og
-- butik genudledes fra produkt-cachen ved indlæsning (samme princip som
-- cart_popularity). Det holder rækken på ~1-3 KB i stedet for at duplikere hele
-- produktdata pr. bruger.
--
-- FK ON DELETE CASCADE: når brugeren slettes (delete_own_account nedenfor eller
-- via dashboard) forsvinder kurven automatisk - ingen forældreløse rækker.
CREATE TABLE IF NOT EXISTS public.carts (
  user_id    uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  items      jsonb NOT NULL DEFAULT '[]'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now(),

  -- Plads-/misbrugsgrænse. CASE sikrer at typetjekket sker FØR
  -- jsonb_array_length kaldes: uden det ville et ikke-array (fx et objekt) få
  -- jsonb_array_length til at kaste en fejl i stedet for pænt at afvise rækken,
  -- og Postgres garanterer ikke AND-rækkefølge. Grænserne (max 100 varer, max
  -- ~8 KB tekst) bounder worst case til én lille række pr. bruger.
  CONSTRAINT carts_items_valid CHECK (
    CASE WHEN jsonb_typeof(items) = 'array'
         THEN jsonb_array_length(items) <= 100 AND length(items::text) <= 8000
         ELSE false END
  )
);

-- updated_at holdes ajour automatisk, så et fremtidigt oprydningsjob kan fjerne
-- kurve der ikke er rørt i mange måneder (yderligere plads-besparelse).
CREATE OR REPLACE FUNCTION public.carts_touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS carts_touch ON public.carts;
CREATE TRIGGER carts_touch
  BEFORE UPDATE ON public.carts
  FOR EACH ROW EXECUTE FUNCTION public.carts_touch_updated_at();

-- ---------------------------------------------------------------------------
-- Rettigheder + RLS
-- ---------------------------------------------------------------------------
-- Kun authenticated (indloggede brugere med JWT). Bevidst INTET til anon.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.carts TO authenticated;
-- service_role (backend-jobs, fx fremtidig oprydning) - nøglen sendes aldrig
-- til browseren, jf. lockdown-doc'en.
GRANT ALL ON public.carts TO service_role;

ALTER TABLE public.carts ENABLE ROW LEVEL SECURITY;

-- Én policy pr. operation, alle bundet til brugerens egen række.
DROP POLICY IF EXISTS "Egen kurv - laes" ON public.carts;
CREATE POLICY "Egen kurv - laes"
  ON public.carts FOR SELECT TO authenticated
  USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Egen kurv - indsaet" ON public.carts;
CREATE POLICY "Egen kurv - indsaet"
  ON public.carts FOR INSERT TO authenticated
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Egen kurv - opdater" ON public.carts;
CREATE POLICY "Egen kurv - opdater"
  ON public.carts FOR UPDATE TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Egen kurv - slet" ON public.carts;
CREATE POLICY "Egen kurv - slet"
  ON public.carts FOR DELETE TO authenticated
  USING (auth.uid() = user_id);

-- service_role skal kunne alt (RLS gælder også for den medmindre en policy
-- åbner - samme mønster som scripts/supabase-grants.sql).
DROP POLICY IF EXISTS "Service role fuld adgang" ON public.carts;
CREATE POLICY "Service role fuld adgang"
  ON public.carts FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------------
-- GDPR: selvbetjent kontosletning uden service-nøgle på edge
-- ---------------------------------------------------------------------------
-- "Ret til at blive glemt". At slette en bruger fra auth.users kræver normalt
-- service_role (admin), men den nøgle holder vi bevidst UDE af worker'en. I
-- stedet en SECURITY DEFINER-funktion (samme mønster som record_cart_activity):
-- den kører med ejerens rettigheder, så en indlogget bruger kan slette PRÆCIS
-- sin egen konto - og kun den - via auth.uid(), uden nogen adminnøgle i browseren.
--
-- auth.uid() virker inde i SECURITY DEFINER, fordi det læser JWT-claimet fra
-- request-GUC'en PostgREST sætter pr. kald - ikke fra rollen. carts fjernes via
-- FK-cascade; det samme gør auth.identities/sessions/refresh_tokens.
CREATE OR REPLACE FUNCTION public.delete_own_account()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  uid uuid := auth.uid();
BEGIN
  IF uid IS NULL THEN
    RAISE EXCEPTION 'Ingen aktiv session';
  END IF;
  DELETE FROM auth.users WHERE id = uid;
END;
$$;

-- Kun indloggede må kalde den; anon/public har intet at gøre her.
REVOKE ALL ON FUNCTION public.delete_own_account() FROM public, anon;
GRANT EXECUTE ON FUNCTION public.delete_own_account() TO authenticated;
