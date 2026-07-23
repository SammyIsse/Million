-- Kør i Supabase SQL Editor.
-- Atomisk klik-tæller til cart_popularity: undgår race condition hvor to
-- samtidige klik læser samme count, og det ene klik tabes.
-- app.py kalder funktionen via POST /rest/v1/rpc/increment_cart_count
-- (med læs-så-skriv som fallback, indtil dette script er kørt).

-- ON CONFLICT kræver et unikt indeks (tabellen har i forvejen én række pr. produkt)
CREATE UNIQUE INDEX IF NOT EXISTS cart_popularity_product_id_idx
  ON public.cart_popularity (product_id);

CREATE OR REPLACE FUNCTION public.increment_cart_count(pid text)
RETURNS void
LANGUAGE sql
AS $$
  INSERT INTO public.cart_popularity (product_id, count)
  VALUES (pid, 1) 
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity.count + 1;
$$;

GRANT EXECUTE ON FUNCTION public.increment_cart_count(text) TO anon, authenticated, service_role;

-- Batch-variant: tæller hele kurven op i ÉT kald. Bruges når brugeren klikker
-- "Sammenlign priser" - et stærkere købssignal end en ren kurv-tilføjelse, så
-- varerne tæller også med i Brugernes Favoritter.
-- Hvorfor batch og ikke N enkeltkald: Workers' gratis-plan tillader kun 50
-- subrequests pr. request, så en stor kurv ville ellers sprænge loftet.
-- DISTINCT er påkrævet - uden den fejler ON CONFLICT med "cannot affect row a
-- second time", hvis samme id optræder to gange i arrayet.
CREATE OR REPLACE FUNCTION public.increment_cart_counts(pids text[])
RETURNS void
LANGUAGE sql
AS $$
  INSERT INTO public.cart_popularity (product_id, count)
  SELECT DISTINCT t.pid, 1
  FROM unnest(pids) AS t(pid)
  WHERE t.pid IS NOT NULL AND t.pid <> ''
  ON CONFLICT (product_id) DO UPDATE
  SET count = cart_popularity.count + 1;
$$;

GRANT EXECUTE ON FUNCTION public.increment_cart_counts(text[]) TO anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Tidsaggregeret kurv-aktivitet (cart_events)
-- ---------------------------------------------------------------------------
-- Én række pr. produkt pr. TIME pr. signaltype - ikke én pr. hændelse. Derfor
-- er data anonyme af konstruktion: der er intet at knytte til en person, kun
-- tællere. Rå tidsstempler ville gøre "12 varer kl. 14:32:07" til et unikt
-- fingeraftryk og trække GDPR-krav med sig.
--
-- hour er dansk lokaltid (timestamp UDEN tz), så døgnrytme-analyse ikke skal
-- konvertere ved hvert opslag. qty summerer kvantiteterne, events tæller
-- hændelserne - "3 stk. mælk i ét klik" er qty=3, events=1.
CREATE TABLE IF NOT EXISTS public.cart_events (
  product_id text NOT NULL,
  hour       timestamp NOT NULL,
  event_type text NOT NULL,          -- 'add' = lagt i kurv, 'compare' = prissammenligning
  events     integer NOT NULL DEFAULT 0,
  qty        integer NOT NULL DEFAULT 0,
  PRIMARY KEY (product_id, hour, event_type)
);

-- Oprydningen (updater.py::prune_cart_events) filtrerer på hour
CREATE INDEX IF NOT EXISTS cart_events_hour_idx ON public.cart_events (hour);

-- Tabellen er helt lukket for den offentlige nøgle: intet i appen læser den
-- (den er til analyse), og skrivning sker udelukkende gennem den SECURITY
-- DEFINER-funktion der defineres nedenfor. Dermed kan en direkte forespørgsel
-- til /rest/v1/cart_events med anon-nøglen hverken læse eller indsætte noget
-- uden om app.py's validering og rate limiting.
-- DELETE til service_role er påkrævet, for at 30-dages oprydningen i
-- updater.py::prune_cart_events kan køre - uden den fejler den med 403.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.cart_events TO service_role;

-- RLS eksplicit slået til (samme mønster som price_history i
-- scripts/supabase-grants.sql). Uden dette advarer Supabase' SQL Editor om en
-- tabel uden RLS. Kun service_role får en policy; anon/authenticated har hverken
-- grant eller policy og er dermed låst ude i begge lag.
ALTER TABLE public.cart_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.cart_events;
CREATE POLICY "Service role fuld adgang"
  ON public.cart_events
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);

-- ÉN RPC skriver begge tabeller: vægtet popularitet + tidsaggregat. Samlet i
-- én funktion fordi hvert Supabase-kald fra edge er en subrequest, og Workers'
-- gratis-plan giver kun 50 pr. request - to kald ville doble forbruget.
--
-- items er [{"pid": "123", "qty": 2}, ...]; etype er 'add' eller 'compare'.
--
-- SECURITY DEFINER: funktionen kører med ejerens rettigheder, så anon kan
-- opdatere tællerne UDEN at have skriveadgang til tabellerne direkte.
-- search_path sættes eksplicit - uden det kan en angriber med rettigheder til
-- at oprette et skema foran i stien få funktionen til at ramme sine egne
-- tabeller i stedet for public's.
--
-- Netop derfor valideres ALT her, ikke kun i app.py: RPC'en kan kaldes direkte
-- via PostgREST med den offentlige nøgle, uden om Flask-lagets rate limiting og
-- caps. Vægten udledes af etype (kan ikke sendes af kalderen), og antal varer,
-- id-længde og kvantitet klippes til på samme måde som i app.py.
CREATE OR REPLACE FUNCTION public.record_cart_activity(
  items jsonb,
  etype text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  bucket timestamp := date_trunc('hour', timezone('Europe/Copenhagen', now()));
  w integer;
BEGIN
  w := CASE etype WHEN 'compare' THEN 3 WHEN 'add' THEN 1 ELSE NULL END;
  IF w IS NULL THEN
    RETURN;                      -- ukendt signaltype: ignorér i stilhed
  END IF;

  -- GROUP BY håndterer dubletter i items, så ON CONFLICT ikke rammer samme
  -- række to gange ("cannot affect row a second time"). Begge INSERTs deler
  -- det rensede aggregat, så jsonb'en kun parses én gang.
  WITH cleaned AS (
    SELECT i.pid,
           LEAST(GREATEST(COALESCE(i.qty, 1), 1), 99) AS qty
    FROM jsonb_to_recordset(items) AS i(pid text, qty int)
    WHERE i.pid IS NOT NULL AND i.pid <> '' AND length(i.pid) <= 64
    LIMIT 50
  ),
  agg AS (
    SELECT pid, count(*)::int AS events, sum(qty)::int AS qty
    FROM cleaned
    GROUP BY pid
  ),
  -- 1) Rangering til Brugernes Favoritter (vægtet efter signalets styrke)
  pop AS (
    INSERT INTO public.cart_popularity (product_id, count)
    SELECT pid, w FROM agg
    ON CONFLICT (product_id) DO UPDATE
    SET count = cart_popularity.count + w
  )
  -- 2) Tidsaggregat med kvantitet
  INSERT INTO public.cart_events (product_id, hour, event_type, events, qty)
  SELECT pid, bucket, etype, events, qty FROM agg
  ON CONFLICT (product_id, hour, event_type) DO UPDATE
  SET events = cart_events.events + EXCLUDED.events,
      qty    = cart_events.qty + EXCLUDED.qty;
END;
$$;

GRANT EXECUTE ON FUNCTION public.record_cart_activity(jsonb, text) TO anon, authenticated, service_role;

-- Oprydning: fjern testrække fra verifikation (anon-nøglen må ikke slette)
DELETE FROM public.cart_popularity WHERE product_id = 'verify_test_race';

-- Oprydning efter lokal test af batch-fallback. Rækkerne havnede i _dev-kopien,
-- fordi TABLE_SUFFIX er "_dev" lokalt - produktionstabellen er urørt.
-- Betinget, så scriptet også kan køres i et projekt uden dev-tabellerne
-- (ellers ville hele scriptet fejle med "relation does not exist").
DO $$
BEGIN
  IF to_regclass('public.cart_popularity_dev') IS NOT NULL THEN
    DELETE FROM public.cart_popularity_dev
    WHERE product_id IN ('product1', '2', '12345', 'verify_cart_activity', repeat('x', 64));
  END IF;
END $$;
