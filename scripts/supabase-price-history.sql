-- Kør i Supabase SQL Editor.
-- Sikrer upsert (on_conflict) og hurtig opslag pr. produkt de seneste 30 dage.

CREATE UNIQUE INDEX IF NOT EXISTS price_history_product_store_date_idx
  ON public.price_history (product_id, store, date);

CREATE INDEX IF NOT EXISTS price_history_product_date_idx
  ON public.price_history (product_id, date DESC);
