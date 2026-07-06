-- Kør i Supabase SQL Editor (Dashboard → SQL → New query)
-- Giver offentlig indsendelse af feedback + service_role fuld adgang.

CREATE TABLE IF NOT EXISTS public.feedback (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  feedback_type text NOT NULL,
  name text,
  email text,
  subject text,
  message text NOT NULL,
  page_url text,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.feedback ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT ON public.feedback TO anon, authenticated;
GRANT ALL ON public.feedback TO service_role;

DROP POLICY IF EXISTS "Offentlig indsendelse af feedback" ON public.feedback;
CREATE POLICY "Offentlig indsendelse af feedback"
  ON public.feedback
  FOR INSERT
  TO anon, authenticated
  WITH CHECK (true);

DROP POLICY IF EXISTS "Service role fuld adgang feedback" ON public.feedback;
CREATE POLICY "Service role fuld adgang feedback"
  ON public.feedback
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);
