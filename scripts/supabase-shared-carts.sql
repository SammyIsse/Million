-- Live delt kurv (gruppe).
-- Kør i Supabase SQL Editor / migration.
--
-- Model:
--   * Opret kræver et gruppenavn (p_title).
--   * Én delt kurv pr. bruger ad gangen (shared_cart_members.user_id = PK).
--   * Max 6 medlemmer pr. kurv.
--   * Alle medlemmer er lige: kan redigere kurven, invitere og melde sig ud.
--     owner_id er kun intern (så gruppen overlever hvis opretteren forlader).
--   * Man er medlem indtil man kalder leave_shared_cart().
--   * Items synces live (samme kompakte format som carts).
--   * Gemte lister (saved_lists) er fælles for gruppen - alle kan gemme/indlæse/slette.
--     Max 10 lister pr. gruppe (og max 10 privat uden for gruppe).
--   * Ingen direkte tabel-adgang - kun SECURITY DEFINER-RPC'er.
--   * Staging: *_dev-tabeller + *_dev-RPC'er.

-- ---------------------------------------------------------------------------
-- Ryd evt. gammel snapshot-model
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.create_shared_list(jsonb, text);
DROP FUNCTION IF EXISTS public.claim_shared_list(text);
DROP FUNCTION IF EXISTS public.create_shared_list_dev(jsonb, text);
DROP FUNCTION IF EXISTS public.claim_shared_list_dev(text);
DROP FUNCTION IF EXISTS public._normalize_shared_list_items(jsonb);
DROP TABLE IF EXISTS public.shared_lists_dev;
DROP TABLE IF EXISTS public.shared_lists;

-- ---------------------------------------------------------------------------
-- Tabeller (produktion)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.shared_carts (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  token       text NOT NULL,
  owner_id    uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  title       text NOT NULL DEFAULT 'Delt kurv',
  items       jsonb NOT NULL DEFAULT '[]'::jsonb,
  saved_lists jsonb NOT NULL DEFAULT '[]'::jsonb,
  revision    bigint NOT NULL DEFAULT 1,
  updated_by  uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  created_at  timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT shared_carts_token_len CHECK (
    char_length(token) >= 8 AND char_length(token) <= 32
  ),
  CONSTRAINT shared_carts_title_len CHECK (
    char_length(title) >= 1 AND char_length(title) <= 80
  ),
  CONSTRAINT shared_carts_items_valid CHECK (
    CASE WHEN jsonb_typeof(items) = 'array'
         THEN jsonb_array_length(items) <= 100 AND length(items::text) <= 8000
         ELSE false END
  ),
  CONSTRAINT shared_carts_saved_lists_valid CHECK (
    CASE WHEN jsonb_typeof(saved_lists) = 'array'
         THEN jsonb_array_length(saved_lists) <= 10 AND length(saved_lists::text) <= 60000
         ELSE false END
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS shared_carts_token_uidx
  ON public.shared_carts (token);
CREATE INDEX IF NOT EXISTS shared_carts_owner_idx
  ON public.shared_carts (owner_id);

-- user_id er PK → en bruger kan kun være i ÉN delt kurv.
CREATE TABLE IF NOT EXISTS public.shared_cart_members (
  user_id       uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  cart_id       uuid NOT NULL REFERENCES public.shared_carts(id) ON DELETE CASCADE,
  display_name  text NOT NULL DEFAULT '',
  joined_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT shared_cart_members_display_name_len CHECK (char_length(display_name) <= 40)
);

-- Hard cap: aldrig mere end 6 medlemmer (også ved samtidige joins)
CREATE OR REPLACE FUNCTION public._enforce_shared_cart_member_cap()
RETURNS trigger LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM public.shared_cart_members WHERE cart_id = NEW.cart_id;
  IF n > 6 THEN
    RAISE EXCEPTION 'shared_cart_full' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS shared_cart_members_cap ON public.shared_cart_members;
CREATE TRIGGER shared_cart_members_cap
  AFTER INSERT ON public.shared_cart_members
  FOR EACH ROW EXECUTE FUNCTION public._enforce_shared_cart_member_cap();

CREATE INDEX IF NOT EXISTS shared_cart_members_cart_idx
  ON public.shared_cart_members (cart_id);

REVOKE ALL ON public.shared_carts FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.shared_cart_members FROM PUBLIC, anon, authenticated;
GRANT ALL ON public.shared_carts TO service_role;
GRANT ALL ON public.shared_cart_members TO service_role;

ALTER TABLE public.shared_carts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.shared_cart_members ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.shared_carts;
CREATE POLICY "Service role fuld adgang"
  ON public.shared_carts FOR ALL TO service_role
  USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Service role fuld adgang" ON public.shared_cart_members;
CREATE POLICY "Service role fuld adgang"
  ON public.shared_cart_members FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------------
-- Normalisering af items
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public._normalize_shared_cart_items(raw jsonb)
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
SET search_path = public, pg_temp
AS $$
DECLARE
  out_items jsonb := '[]'::jsonb;
  elem jsonb;
  q int;
  pid text;
  n int := 0;
BEGIN
  IF raw IS NULL OR jsonb_typeof(raw) <> 'array' THEN
    RETURN '[]'::jsonb;
  END IF;

  FOR elem IN SELECT value FROM jsonb_array_elements(raw)
  LOOP
    EXIT WHEN n >= 100;
    IF jsonb_typeof(elem) <> 'object' THEN CONTINUE; END IF;
    pid := left(coalesce(elem->>'p', ''), 64);
    IF pid = '' THEN CONTINUE; END IF;
    BEGIN
      q := greatest(1, least(99, coalesce((elem->>'q')::int, 1)));
    EXCEPTION WHEN others THEN
      q := 1;
    END;
    out_items := out_items || jsonb_build_array(jsonb_build_object(
      'p', pid,
      'q', q,
      'n', left(coalesce(elem->>'n', ''), 120),
      'i', left(coalesce(elem->>'i', ''), 300),
      's', left(coalesce(elem->>'s', ''), 40),
      'pr', CASE
              WHEN (elem->>'pr') ~ '^-?[0-9]+(\.[0-9]+)?$'
              THEN (elem->>'pr')::numeric
              ELSE NULL
            END
    ));
    n := n + 1;
  END LOOP;
  RETURN out_items;
END;
$$;

REVOKE ALL ON FUNCTION public._normalize_shared_cart_items(jsonb)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public._normalize_shared_cart_items(jsonb) TO service_role;

-- ---------------------------------------------------------------------------
-- Visningsnavn + payload med medlemsliste
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public._normalize_display_name(raw text)
RETURNS text LANGUAGE sql IMMUTABLE SET search_path = public, pg_temp AS $$
  SELECT left(trim(regexp_replace(coalesce(raw, ''), E'[\\x00-\\x1f\\x7f]', '', 'g')), 40);
$$;
REVOKE ALL ON FUNCTION public._normalize_display_name(text) FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public._shared_cart_payload(p_cart_id uuid, p_uid uuid)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  c public.shared_carts%ROWTYPE;
  mcount int;
  members jsonb;
BEGIN
  SELECT * INTO c FROM public.shared_carts WHERE id = p_cart_id;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'error', 'not_found');
  END IF;
  SELECT count(*) INTO mcount FROM public.shared_cart_members WHERE cart_id = p_cart_id;
  SELECT coalesce(jsonb_agg(
           jsonb_build_object(
             'id', m.user_id,
             'name', NULLIF(m.display_name, ''),
             'me', (m.user_id = p_uid)
           ) ORDER BY m.joined_at ASC
         ), '[]'::jsonb)
    INTO members
    FROM public.shared_cart_members m
   WHERE m.cart_id = p_cart_id;
  RETURN jsonb_build_object(
    'ok', true,
    'cart_id', c.id,
    'token', c.token,
    'title', c.title,
    'items', c.items,
    'saved_lists', coalesce(c.saved_lists, '[]'::jsonb),
    'revision', c.revision,
    'updated_at', c.updated_at,
    'updated_by', c.updated_by,
    'members', mcount,
    'max_members', 6,
    'member_list', members
  );
END;
$$;

REVOKE ALL ON FUNCTION public._shared_cart_payload(uuid, uuid)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public._shared_cart_payload(uuid, uuid) TO service_role;

CREATE OR REPLACE FUNCTION public.set_my_display_name(p_name text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE uid uuid := auth.uid(); nm text;
BEGIN
  IF uid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'login'); END IF;
  nm := public._normalize_display_name(p_name);
  IF nm IS NULL OR nm = '' THEN RETURN jsonb_build_object('ok', false, 'error', 'name'); END IF;
  UPDATE public.shared_cart_members SET display_name = nm WHERE user_id = uid;
  RETURN jsonb_build_object('ok', true, 'name', nm);
END;
$$;
REVOKE ALL ON FUNCTION public.set_my_display_name(text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.set_my_display_name(text) TO authenticated, service_role;

-- ---------------------------------------------------------------------------
-- get_my_shared_cart()
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.get_my_shared_cart()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  uid uuid := auth.uid();
  cid uuid;
BEGIN
  IF uid IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', 'login');
  END IF;
  SELECT cart_id INTO cid FROM public.shared_cart_members WHERE user_id = uid;
  IF cid IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', 'none');
  END IF;
  RETURN public._shared_cart_payload(cid, uid);
END;
$$;

REVOKE ALL ON FUNCTION public.get_my_shared_cart() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.get_my_shared_cart() TO authenticated, service_role;

-- ---------------------------------------------------------------------------
-- create_shared_cart(items, title, name)
-- Hvis allerede i en gruppe: returnér den (invite-link), ingen ny gruppe.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.create_shared_cart(
  p_items jsonb DEFAULT '[]'::jsonb,
  p_title text DEFAULT NULL,
  p_name text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  uid uuid := auth.uid();
  existing uuid;
  norm jsonb;
  tok text;
  ttl text;
  nm text;
  new_id uuid;
BEGIN
  IF uid IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', 'login');
  END IF;

  SELECT cart_id INTO existing FROM public.shared_cart_members WHERE user_id = uid;
  IF existing IS NOT NULL THEN
    nm := public._normalize_display_name(p_name);
    IF nm IS NOT NULL AND nm <> '' THEN
      UPDATE public.shared_cart_members SET display_name = nm WHERE user_id = uid;
    END IF;
    RETURN public._shared_cart_payload(existing, uid)
      || jsonb_build_object('already', true);
  END IF;

  ttl := left(trim(coalesce(p_title, '')), 80);
  IF ttl = '' THEN
    RETURN jsonb_build_object('ok', false, 'error', 'title');
  END IF;

  nm := public._normalize_display_name(p_name);
  norm := public._normalize_shared_cart_items(p_items);
  tok := substr(replace(gen_random_uuid()::text, '-', ''), 1, 12);

  INSERT INTO public.shared_carts (token, owner_id, title, items, updated_by)
  VALUES (tok, uid, ttl, norm, uid)
  RETURNING id INTO new_id;

  INSERT INTO public.shared_cart_members (user_id, cart_id, display_name)
  VALUES (uid, new_id, coalesce(nm, ''));

  RETURN public._shared_cart_payload(new_id, uid);
END;
$$;

REVOKE ALL ON FUNCTION public.create_shared_cart(jsonb, text, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.create_shared_cart(jsonb, text, text)
  TO authenticated, service_role;

-- ---------------------------------------------------------------------------
-- join_shared_cart(token, name)
-- FOR UPDATE på kurv-rækken serialiserer samtidige joins.
-- Trigger + EXCEPTION er ekstra sikkerhed, så loftet aldrig kan sprænges.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.join_shared_cart(
  p_token text,
  p_name text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  uid uuid := auth.uid();
  tok text;
  target public.shared_carts%ROWTYPE;
  prev uuid;
  mcount int;
  nm text;
BEGIN
  IF uid IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', 'login');
  END IF;

  tok := lower(trim(coalesce(p_token, '')));
  IF char_length(tok) < 8 OR char_length(tok) > 32 THEN
    RETURN jsonb_build_object('ok', false, 'error', 'not_found');
  END IF;

  nm := public._normalize_display_name(p_name);

  SELECT * INTO target FROM public.shared_carts WHERE token = tok FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'error', 'not_found');
  END IF;

  IF EXISTS (
    SELECT 1 FROM public.shared_cart_members
    WHERE user_id = uid AND cart_id = target.id
  ) THEN
    IF nm IS NOT NULL AND nm <> '' THEN
      UPDATE public.shared_cart_members SET display_name = nm WHERE user_id = uid;
    END IF;
    RETURN public._shared_cart_payload(target.id, uid)
      || jsonb_build_object('already_member', true);
  END IF;

  SELECT cart_id INTO prev FROM public.shared_cart_members WHERE user_id = uid;
  IF prev IS NOT NULL THEN
    PERFORM public._leave_shared_cart_internal(uid);
  END IF;

  SELECT count(*) INTO mcount FROM public.shared_cart_members WHERE cart_id = target.id;
  IF mcount >= 6 THEN
    RETURN jsonb_build_object('ok', false, 'error', 'full', 'max_members', 6);
  END IF;

  BEGIN
    INSERT INTO public.shared_cart_members (user_id, cart_id, display_name)
    VALUES (uid, target.id, coalesce(nm, ''));
  EXCEPTION
    WHEN check_violation THEN
      IF SQLERRM LIKE '%shared_cart_full%' THEN
        RETURN jsonb_build_object('ok', false, 'error', 'full', 'max_members', 6);
      END IF;
      RAISE;
  END;

  RETURN public._shared_cart_payload(target.id, uid)
    || jsonb_build_object('already_member', false, 'switched', prev IS NOT NULL);
END;
$$;

REVOKE ALL ON FUNCTION public.join_shared_cart(text, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.join_shared_cart(text, text)
  TO authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Intern leave (bruges af leave + join-switch)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public._leave_shared_cart_internal(p_uid uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  cid uuid;
  was_owner boolean;
  new_owner uuid;
  left_count int;
BEGIN
  SELECT cart_id INTO cid FROM public.shared_cart_members WHERE user_id = p_uid;
  IF cid IS NULL THEN RETURN; END IF;

  SELECT (owner_id = p_uid) INTO was_owner FROM public.shared_carts WHERE id = cid;

  DELETE FROM public.shared_cart_members WHERE user_id = p_uid;

  SELECT count(*) INTO left_count FROM public.shared_cart_members WHERE cart_id = cid;

  IF left_count = 0 THEN
    DELETE FROM public.shared_carts WHERE id = cid;
    RETURN;
  END IF;

  IF was_owner THEN
    SELECT user_id INTO new_owner
    FROM public.shared_cart_members
    WHERE cart_id = cid
    ORDER BY joined_at ASC
    LIMIT 1;
    UPDATE public.shared_carts SET owner_id = new_owner WHERE id = cid;
  END IF;
END;
$$;

REVOKE ALL ON FUNCTION public._leave_shared_cart_internal(uuid)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public._leave_shared_cart_internal(uuid) TO service_role;

CREATE OR REPLACE FUNCTION public.leave_shared_cart()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  uid uuid := auth.uid();
BEGIN
  IF uid IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', 'login');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.shared_cart_members WHERE user_id = uid) THEN
    RETURN jsonb_build_object('ok', false, 'error', 'none');
  END IF;
  PERFORM public._leave_shared_cart_internal(uid);
  RETURN jsonb_build_object('ok', true);
END;
$$;

REVOKE ALL ON FUNCTION public.leave_shared_cart() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.leave_shared_cart() TO authenticated, service_role;

-- ---------------------------------------------------------------------------
-- push_shared_cart(items) - live opdatering
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.push_shared_cart(p_items jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  uid uuid := auth.uid();
  cid uuid;
  norm jsonb;
BEGIN
  IF uid IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', 'login');
  END IF;

  SELECT cart_id INTO cid FROM public.shared_cart_members WHERE user_id = uid;
  IF cid IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', 'none');
  END IF;

  norm := public._normalize_shared_cart_items(p_items);

  UPDATE public.shared_carts
  SET items = norm,
      revision = revision + 1,
      updated_by = uid,
      updated_at = now()
  WHERE id = cid;

  RETURN public._shared_cart_payload(cid, uid);
END;
$$;

REVOKE ALL ON FUNCTION public.push_shared_cart(jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.push_shared_cart(jsonb)
  TO authenticated, service_role;

-- ===========================================================================
-- Staging (*_dev)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS public.shared_carts_dev (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  token       text NOT NULL,
  owner_id    uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  title       text NOT NULL DEFAULT 'Delt kurv',
  items       jsonb NOT NULL DEFAULT '[]'::jsonb,
  saved_lists jsonb NOT NULL DEFAULT '[]'::jsonb,
  revision    bigint NOT NULL DEFAULT 1,
  updated_by  uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT shared_carts_dev_token_len CHECK (
    char_length(token) >= 8 AND char_length(token) <= 32
  ),
  CONSTRAINT shared_carts_dev_title_len CHECK (
    char_length(title) >= 1 AND char_length(title) <= 80
  ),
  CONSTRAINT shared_carts_dev_items_valid CHECK (
    CASE WHEN jsonb_typeof(items) = 'array'
         THEN jsonb_array_length(items) <= 100 AND length(items::text) <= 8000
         ELSE false END
  )
);
CREATE UNIQUE INDEX IF NOT EXISTS shared_carts_dev_token_uidx ON public.shared_carts_dev (token);
CREATE INDEX IF NOT EXISTS shared_carts_dev_owner_idx ON public.shared_carts_dev (owner_id);

CREATE TABLE IF NOT EXISTS public.shared_cart_members_dev (
  user_id       uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  cart_id       uuid NOT NULL REFERENCES public.shared_carts_dev(id) ON DELETE CASCADE,
  display_name  text NOT NULL DEFAULT '',
  joined_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT shared_cart_members_dev_display_name_len CHECK (char_length(display_name) <= 40)
);
CREATE INDEX IF NOT EXISTS shared_cart_members_dev_cart_idx
  ON public.shared_cart_members_dev (cart_id);

REVOKE ALL ON public.shared_carts_dev FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.shared_cart_members_dev FROM PUBLIC, anon, authenticated;
GRANT ALL ON public.shared_carts_dev TO service_role;
GRANT ALL ON public.shared_cart_members_dev TO service_role;
ALTER TABLE public.shared_carts_dev ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.shared_cart_members_dev ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS shared_carts_dev_service_all ON public.shared_carts_dev;
CREATE POLICY shared_carts_dev_service_all ON public.shared_carts_dev
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS shared_cart_members_dev_service_all ON public.shared_cart_members_dev;
CREATE POLICY shared_cart_members_dev_service_all ON public.shared_cart_members_dev
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Dev leave / get / push (create/join/payload med navn står længere nede)
CREATE OR REPLACE FUNCTION public._leave_shared_cart_internal_dev(p_uid uuid)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE cid uuid; was_owner boolean; new_owner uuid; left_count int;
BEGIN
  SELECT cart_id INTO cid FROM public.shared_cart_members_dev WHERE user_id = p_uid;
  IF cid IS NULL THEN RETURN; END IF;
  SELECT (owner_id = p_uid) INTO was_owner FROM public.shared_carts_dev WHERE id = cid;
  DELETE FROM public.shared_cart_members_dev WHERE user_id = p_uid;
  SELECT count(*) INTO left_count FROM public.shared_cart_members_dev WHERE cart_id = cid;
  IF left_count = 0 THEN
    DELETE FROM public.shared_carts_dev WHERE id = cid; RETURN;
  END IF;
  IF was_owner THEN
    SELECT user_id INTO new_owner FROM public.shared_cart_members_dev
    WHERE cart_id = cid ORDER BY joined_at ASC LIMIT 1;
    UPDATE public.shared_carts_dev SET owner_id = new_owner WHERE id = cid;
  END IF;
END;
$$;
REVOKE ALL ON FUNCTION public._leave_shared_cart_internal_dev(uuid) FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.leave_shared_cart_dev()
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE uid uuid := auth.uid();
BEGIN
  IF uid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'login'); END IF;
  IF NOT EXISTS (SELECT 1 FROM public.shared_cart_members_dev WHERE user_id = uid) THEN
    RETURN jsonb_build_object('ok', false, 'error', 'none');
  END IF;
  PERFORM public._leave_shared_cart_internal_dev(uid);
  RETURN jsonb_build_object('ok', true);
END; $$;
REVOKE ALL ON FUNCTION public.leave_shared_cart_dev() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.leave_shared_cart_dev() TO authenticated, service_role;

CREATE OR REPLACE FUNCTION public.push_shared_cart_dev(p_items jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE uid uuid := auth.uid(); cid uuid; norm jsonb;
BEGIN
  IF uid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'login'); END IF;
  SELECT cart_id INTO cid FROM public.shared_cart_members_dev WHERE user_id = uid;
  IF cid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'none'); END IF;
  norm := public._normalize_shared_cart_items(p_items);
  UPDATE public.shared_carts_dev
  SET items = norm, revision = revision + 1, updated_by = uid, updated_at = now()
  WHERE id = cid;
  RETURN public._shared_cart_payload_dev(cid, uid);
END; $$;
REVOKE ALL ON FUNCTION public.push_shared_cart_dev(jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.push_shared_cart_dev(jsonb) TO authenticated, service_role;


-- ---------------------------------------------------------------------------
-- Gruppens gemte lister (alle medlemmer er lige, max 10)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public._normalize_shared_saved_lists(raw jsonb)
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
SET search_path = public, pg_temp
AS $$
DECLARE
  out_lists jsonb := '[]'::jsonb;
  elem jsonb;
  lid text;
  lname text;
  n int := 0;
  items jsonb;
BEGIN
  IF raw IS NULL OR jsonb_typeof(raw) <> 'array' THEN
    RETURN '[]'::jsonb;
  END IF;
  FOR elem IN SELECT value FROM jsonb_array_elements(raw)
  LOOP
    EXIT WHEN n >= 10;
    IF jsonb_typeof(elem) <> 'object' THEN CONTINUE; END IF;
    lid := left(coalesce(elem->>'id', ''), 40);
    IF lid = '' THEN
      lid := substr(md5(random()::text || clock_timestamp()::text), 1, 12);
    END IF;
    lname := left(trim(coalesce(elem->>'name', 'Liste')), 80);
    IF lname = '' THEN lname := 'Liste'; END IF;
    items := public._normalize_shared_cart_items(coalesce(elem->'items', '[]'::jsonb));
    IF jsonb_array_length(items) < 1 THEN CONTINUE; END IF;
    out_lists := out_lists || jsonb_build_array(jsonb_build_object(
      'id', lid,
      'name', lname,
      'created_at', left(coalesce(elem->>'created_at', elem->>'createdAt', ''), 40),
      'items', items
    ));
    n := n + 1;
  END LOOP;
  RETURN out_lists;
END;
$$;
REVOKE ALL ON FUNCTION public._normalize_shared_saved_lists(jsonb)
  FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.push_shared_saved_lists(p_lists jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE uid uuid := auth.uid(); cid uuid; norm jsonb;
BEGIN
  IF uid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'login'); END IF;
  SELECT cart_id INTO cid FROM public.shared_cart_members WHERE user_id = uid;
  IF cid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'none'); END IF;
  PERFORM 1 FROM public.shared_carts WHERE id = cid FOR UPDATE;
  norm := public._normalize_shared_saved_lists(p_lists);
  BEGIN
    UPDATE public.shared_carts
    SET saved_lists = norm, revision = revision + 1, updated_by = uid, updated_at = now()
    WHERE id = cid;
  EXCEPTION
    WHEN check_violation THEN
      RETURN jsonb_build_object('ok', false, 'error', 'lists_full');
  END;
  RETURN public._shared_cart_payload(cid, uid);
END;
$$;
REVOKE ALL ON FUNCTION public.push_shared_saved_lists(jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.push_shared_saved_lists(jsonb) TO authenticated, service_role;

-- Dev-spejl: display_name + cap-trigger + create/join/push med samme kontrakt
ALTER TABLE public.shared_cart_members_dev
  ADD COLUMN IF NOT EXISTS display_name text NOT NULL DEFAULT '';
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'shared_cart_members_dev_display_name_len'
  ) THEN
    ALTER TABLE public.shared_cart_members_dev
      ADD CONSTRAINT shared_cart_members_dev_display_name_len
      CHECK (char_length(display_name) <= 40);
  END IF;
END $$;

CREATE OR REPLACE FUNCTION public._enforce_shared_cart_member_cap_dev()
RETURNS trigger LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM public.shared_cart_members_dev WHERE cart_id = NEW.cart_id;
  IF n > 6 THEN
    RAISE EXCEPTION 'shared_cart_full' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS shared_cart_members_dev_cap ON public.shared_cart_members_dev;
CREATE TRIGGER shared_cart_members_dev_cap
  AFTER INSERT ON public.shared_cart_members_dev
  FOR EACH ROW EXECUTE FUNCTION public._enforce_shared_cart_member_cap_dev();

CREATE OR REPLACE FUNCTION public._shared_cart_payload_dev(p_cart_id uuid, p_uid uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE c public.shared_carts_dev%ROWTYPE; mcount int; members jsonb;
BEGIN
  SELECT * INTO c FROM public.shared_carts_dev WHERE id = p_cart_id;
  IF NOT FOUND THEN RETURN jsonb_build_object('ok', false, 'error', 'not_found'); END IF;
  SELECT count(*) INTO mcount FROM public.shared_cart_members_dev WHERE cart_id = p_cart_id;
  SELECT coalesce(jsonb_agg(
           jsonb_build_object('id', m.user_id, 'name', NULLIF(m.display_name, ''), 'me', (m.user_id = p_uid))
           ORDER BY m.joined_at ASC), '[]'::jsonb)
    INTO members FROM public.shared_cart_members_dev m WHERE m.cart_id = p_cart_id;
  RETURN jsonb_build_object(
    'ok', true, 'cart_id', c.id, 'token', c.token, 'title', c.title,
    'items', c.items, 'saved_lists', coalesce(c.saved_lists, '[]'::jsonb),
    'revision', c.revision, 'updated_at', c.updated_at, 'updated_by', c.updated_by,
    'members', mcount, 'max_members', 6, 'member_list', members
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.get_my_shared_cart_dev()
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE uid uuid := auth.uid(); cid uuid;
BEGIN
  IF uid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'login'); END IF;
  SELECT cart_id INTO cid FROM public.shared_cart_members_dev WHERE user_id = uid;
  IF cid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'none'); END IF;
  RETURN public._shared_cart_payload_dev(cid, uid);
END; $$;
REVOKE ALL ON FUNCTION public.get_my_shared_cart_dev() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.get_my_shared_cart_dev() TO authenticated, service_role;

CREATE OR REPLACE FUNCTION public.set_my_display_name_dev(p_name text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE uid uuid := auth.uid(); nm text;
BEGIN
  IF uid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'login'); END IF;
  nm := public._normalize_display_name(p_name);
  IF nm IS NULL OR nm = '' THEN RETURN jsonb_build_object('ok', false, 'error', 'name'); END IF;
  UPDATE public.shared_cart_members_dev SET display_name = nm WHERE user_id = uid;
  RETURN jsonb_build_object('ok', true, 'name', nm);
END;
$$;
REVOKE ALL ON FUNCTION public.set_my_display_name_dev(text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.set_my_display_name_dev(text) TO authenticated, service_role;

CREATE OR REPLACE FUNCTION public.create_shared_cart_dev(
  p_items jsonb DEFAULT '[]'::jsonb, p_title text DEFAULT NULL, p_name text DEFAULT NULL
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE uid uuid := auth.uid(); existing uuid; norm jsonb; tok text; ttl text; nm text; new_id uuid;
BEGIN
  IF uid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'login'); END IF;
  SELECT cart_id INTO existing FROM public.shared_cart_members_dev WHERE user_id = uid;
  IF existing IS NOT NULL THEN
    nm := public._normalize_display_name(p_name);
    IF nm IS NOT NULL AND nm <> '' THEN
      UPDATE public.shared_cart_members_dev SET display_name = nm WHERE user_id = uid;
    END IF;
    RETURN public._shared_cart_payload_dev(existing, uid) || jsonb_build_object('already', true);
  END IF;
  ttl := left(trim(coalesce(p_title, '')), 80);
  IF ttl = '' THEN RETURN jsonb_build_object('ok', false, 'error', 'title'); END IF;
  nm := public._normalize_display_name(p_name);
  norm := public._normalize_shared_cart_items(p_items);
  tok := substr(replace(gen_random_uuid()::text, '-', ''), 1, 12);
  INSERT INTO public.shared_carts_dev (token, owner_id, title, items, updated_by)
  VALUES (tok, uid, ttl, norm, uid) RETURNING id INTO new_id;
  INSERT INTO public.shared_cart_members_dev (user_id, cart_id, display_name)
  VALUES (uid, new_id, coalesce(nm, ''));
  RETURN public._shared_cart_payload_dev(new_id, uid);
END; $$;
REVOKE ALL ON FUNCTION public.create_shared_cart_dev(jsonb, text, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.create_shared_cart_dev(jsonb, text, text) TO authenticated, service_role;

CREATE OR REPLACE FUNCTION public.join_shared_cart_dev(p_token text, p_name text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE uid uuid := auth.uid(); tok text; target public.shared_carts_dev%ROWTYPE; prev uuid; mcount int; nm text;
BEGIN
  IF uid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'login'); END IF;
  tok := lower(trim(coalesce(p_token, '')));
  IF char_length(tok) < 8 OR char_length(tok) > 32 THEN
    RETURN jsonb_build_object('ok', false, 'error', 'not_found');
  END IF;
  nm := public._normalize_display_name(p_name);
  SELECT * INTO target FROM public.shared_carts_dev WHERE token = tok FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('ok', false, 'error', 'not_found'); END IF;
  IF EXISTS (SELECT 1 FROM public.shared_cart_members_dev WHERE user_id = uid AND cart_id = target.id) THEN
    IF nm IS NOT NULL AND nm <> '' THEN
      UPDATE public.shared_cart_members_dev SET display_name = nm WHERE user_id = uid;
    END IF;
    RETURN public._shared_cart_payload_dev(target.id, uid) || jsonb_build_object('already_member', true);
  END IF;
  SELECT cart_id INTO prev FROM public.shared_cart_members_dev WHERE user_id = uid;
  IF prev IS NOT NULL THEN PERFORM public._leave_shared_cart_internal_dev(uid); END IF;
  SELECT count(*) INTO mcount FROM public.shared_cart_members_dev WHERE cart_id = target.id;
  IF mcount >= 6 THEN RETURN jsonb_build_object('ok', false, 'error', 'full', 'max_members', 6); END IF;
  BEGIN
    INSERT INTO public.shared_cart_members_dev (user_id, cart_id, display_name)
    VALUES (uid, target.id, coalesce(nm, ''));
  EXCEPTION
    WHEN check_violation THEN
      IF SQLERRM LIKE '%shared_cart_full%' THEN
        RETURN jsonb_build_object('ok', false, 'error', 'full', 'max_members', 6);
      END IF;
      RAISE;
  END;
  RETURN public._shared_cart_payload_dev(target.id, uid)
    || jsonb_build_object('already_member', false, 'switched', prev IS NOT NULL);
END; $$;
REVOKE ALL ON FUNCTION public.join_shared_cart_dev(text, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.join_shared_cart_dev(text, text) TO authenticated, service_role;

CREATE OR REPLACE FUNCTION public.push_shared_saved_lists_dev(p_lists jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE uid uuid := auth.uid(); cid uuid; norm jsonb;
BEGIN
  IF uid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'login'); END IF;
  SELECT cart_id INTO cid FROM public.shared_cart_members_dev WHERE user_id = uid;
  IF cid IS NULL THEN RETURN jsonb_build_object('ok', false, 'error', 'none'); END IF;
  PERFORM 1 FROM public.shared_carts_dev WHERE id = cid FOR UPDATE;
  norm := public._normalize_shared_saved_lists(p_lists);
  BEGIN
    UPDATE public.shared_carts_dev
    SET saved_lists = norm, revision = revision + 1, updated_by = uid, updated_at = now()
    WHERE id = cid;
  EXCEPTION
    WHEN check_violation THEN
      RETURN jsonb_build_object('ok', false, 'error', 'lists_full');
  END;
  RETURN public._shared_cart_payload_dev(cid, uid);
END;
$$;
REVOKE ALL ON FUNCTION public.push_shared_saved_lists_dev(jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.push_shared_saved_lists_dev(jsonb) TO authenticated, service_role;
