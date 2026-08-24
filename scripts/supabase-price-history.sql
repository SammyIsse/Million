-- Kør i Supabase SQL Editor.
-- Sikrer upsert (on_conflict) og hurtig opslag pr. produkt de seneste 30 dage.

CREATE UNIQUE INDEX IF NOT EXISTS price_history_product_store_date_idx
  ON public.price_history (product_id, store, date);

CREATE INDEX IF NOT EXISTS price_history_product_date_idx
  ON public.price_history (product_id, date DESC);

-- Fundet 24-08-2026: updater.py::record_prices_batch() sletter hver nat
-- rækker ældre end 30 dage (DELETE ... WHERE date < X), men INGEN af
-- indeksene ovenfor har date som FØRSTE kolonne - de starter begge med
-- product_id, så et rent dato-filter kan ikke bruge dem og skanner i
-- stedet HELE tabellen. Efterhånden som price_history voksede, blev det
-- nattelige DELETE langsommere og langsommere, indtil det begyndte at
-- time'e ud hver nat (fejlen fanges af et try/except der kun logger en
-- advarsel, se kommentaren i updater.py - så det fortsatte upåagtet).
-- Resultat: tabellen voksede uden loft i stedet for at holde sig til et
-- 30-dages vindue, og var hovedårsagen til at projektet ramte Supabases
-- 500 MB-grænse på gratis-planen (388 MB af price_history alene, data
-- helt tilbage til 38 dage i stedet for 30).
CREATE INDEX IF NOT EXISTS price_history_date_idx
  ON public.price_history (date);
