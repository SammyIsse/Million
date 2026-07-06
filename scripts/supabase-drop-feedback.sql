-- Kør i Supabase SQL Editor (Dashboard → SQL → New query)
-- Fjerner den ubrugte feedback-tabel - feedback går kun til Google Sheet.

DROP POLICY IF EXISTS "Offentlig indsendelse af feedback" ON public.feedback;
DROP POLICY IF EXISTS "Service role fuld adgang feedback" ON public.feedback;

DROP TABLE IF EXISTS public.feedback;
