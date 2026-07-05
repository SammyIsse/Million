-- Kør i Supabase SQL Editor (Dashboard → SQL → New query)
-- Giver service_role (DEPLOY_KEY) skriv-adgang til prishistorik.

GRANT SELECT, INSERT, UPDATE, DELETE ON public.price_history TO service_role;
GRANT SELECT ON public.price_history TO anon, authenticated;

-- Sikr at RLS ikke blokerer service_role (standard i Supabase):
ALTER TABLE public.price_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.price_history;
CREATE POLICY "Service role fuld adgang"
  ON public.price_history
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);

DROP POLICY IF EXISTS "Offentlig læsning" ON public.price_history;
CREATE POLICY "Offentlig læsning"
  ON public.price_history
  FOR SELECT
  TO anon, authenticated
  USING (true);
