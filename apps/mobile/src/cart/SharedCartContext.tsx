/**
 * Shared cart — poll 2500 / push debounce 450 / max 6 members / max 10 lists.
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import * as Linking from 'expo-linking';
import { rpcName } from '../config/env';
import { getSupabase } from '../auth/supabase';
import { useAuth } from '../auth/AuthContext';
import { useCart } from './CartContext';
import { cartToRows, rowsToCart, type CartItem, type CompactCartItem } from './types';

const PUSH_DEBOUNCE_MS = 450;
const POLL_MS = 2500;
const MAX_MEMBERS = 6;
const MAX_SAVED_LISTS = 10;

export type SharedMember = { id: string; name: string; me: boolean };

export type SavedList = {
  id: string;
  name: string;
  createdAt: string;
  items: CartItem[];
};

type SharedPayload = {
  ok?: boolean;
  already?: boolean;
  full?: boolean;
  cart_id?: string;
  token?: string;
  title?: string;
  items?: CompactCartItem[];
  saved_lists?: Array<{
    id: string;
    name: string;
    created_at?: string;
    createdAt?: string;
    items: CompactCartItem[];
  }>;
  revision?: number;
  updated_at?: string;
  updated_by?: string;
  members?: number;
  max_members?: number;
  member_list?: SharedMember[];
};

type SharedContextValue = {
  active: boolean;
  token: string | null;
  title: string;
  revision: number;
  members: SharedMember[];
  maxMembers: number;
  savedLists: SavedList[];
  inviteUrl: string | null;
  createShared: (title: string) => Promise<string | null>;
  joinShared: (token: string) => Promise<string | null>;
  leaveShared: () => Promise<void>;
  saveList: (name: string) => Promise<string | null>;
  loadList: (id: string) => void;
  deleteList: (id: string) => Promise<void>;
  pendingInviteToken: string | null;
  clearPendingInvite: () => void;
};

const SharedContext = createContext<SharedContextValue | null>(null);

function hydrateLists(raw: SharedPayload['saved_lists']): SavedList[] {
  return (raw || []).slice(0, MAX_SAVED_LISTS).map((l) => ({
    id: l.id,
    name: l.name,
    createdAt: l.created_at || l.createdAt || new Date().toISOString(),
    items: rowsToCart(l.items || []),
  }));
}

function compactLists(lists: SavedList[]) {
  return lists.slice(0, MAX_SAVED_LISTS).map((l) => ({
    id: l.id,
    name: l.name,
    created_at: l.createdAt,
    items: cartToRows(l.items),
  }));
}

export function SharedCartProvider({ children }: { children: React.ReactNode }) {
  const { user, displayName, requireAuth } = useAuth();
  const { items, applyFromServer, addSyncListener } = useCart();
  const [state, setState] = useState<SharedPayload | null>(null);
  const [savedLists, setSavedLists] = useState<SavedList[]>([]);
  const [pendingInviteToken, setPendingInviteToken] = useState<string | null>(null);
  const localRev = useRef(0);
  const pushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const applyingRemote = useRef(false);
  const unsubPush = useRef<(() => void) | null>(null);
  const stateToken = useRef<string | null>(null);

  const rpc = useCallback(async (name: string, args?: Record<string, unknown>) => {
    const sb = getSupabase();
    if (!sb) throw new Error('Supabase mangler');
    const { data, error } = await sb.rpc(rpcName(name), args || {});
    if (error) throw error;
    return data as SharedPayload;
  }, []);

  const pushShared = useCallback(
    async (cart: CartItem[]) => {
      if (!stateToken.current || applyingRemote.current) return;
      try {
        const data = await rpc('push_shared_cart', { p_items: cartToRows(cart) });
        if (data?.revision != null) localRev.current = data.revision;
        setState((prev) => (prev ? { ...prev, ...data } : data));
      } catch {
        /* soft */
      }
    },
    [rpc],
  );

  const schedulePush = useCallback(
    (cart: CartItem[]) => {
      if (!stateToken.current) return;
      if (pushTimer.current) clearTimeout(pushTimer.current);
      pushTimer.current = setTimeout(() => {
        void pushShared(cart);
      }, PUSH_DEBOUNCE_MS);
    },
    [pushShared],
  );

  const stopShared = useCallback(() => {
    if (pollTimer.current) clearInterval(pollTimer.current);
    pollTimer.current = null;
    if (pushTimer.current) clearTimeout(pushTimer.current);
    pushTimer.current = null;
    if (unsubPush.current) {
      unsubPush.current();
      unsubPush.current = null;
    }
    stateToken.current = null;
    setState(null);
    localRev.current = 0;
  }, []);

  const pullShared = useCallback(async () => {
    if (!stateToken.current) return;
    try {
      const data = await rpc('get_my_shared_cart');
      if (!data?.ok && !data?.token) {
        stopShared();
        return;
      }
      const remoteRev = data.revision || 0;
      const me = data.member_list?.find((m) => m.me)?.id;
      if (remoteRev > localRev.current && data.updated_by && data.updated_by !== me) {
        applyingRemote.current = true;
        applyFromServer(rowsToCart(data.items || []));
        localRev.current = remoteRev;
        setSavedLists(hydrateLists(data.saved_lists));
        applyingRemote.current = false;
      }
      setState((prev) => ({ ...(prev || {}), ...data }));
    } catch {
      /* soft */
    }
  }, [applyFromServer, rpc, stopShared]);

  const enterShared = useCallback(
    (data: SharedPayload) => {
      stateToken.current = data.token || null;
      setState(data);
      localRev.current = data.revision || 0;
      applyFromServer(rowsToCart(data.items || []));
      setSavedLists(hydrateLists(data.saved_lists));
      if (unsubPush.current) unsubPush.current();
      unsubPush.current = addSyncListener(schedulePush);
      if (pollTimer.current) clearInterval(pollTimer.current);
      pollTimer.current = setInterval(() => {
        void pullShared();
      }, POLL_MS);
    },
    [addSyncListener, applyFromServer, pullShared, schedulePush],
  );

  useEffect(() => {
    const handle = (url: string) => {
      try {
        const parsed = Linking.parse(url);
        let token = parsed.queryParams?.liste as string | undefined;
        if (!token) {
          const q = url.includes('?') ? url.split('?')[1] : '';
          token = new URLSearchParams(q).get('liste') || undefined;
        }
        if (token && /^[a-f0-9]{8,32}$/i.test(token)) {
          setPendingInviteToken(token);
        }
      } catch {
        /* ignore */
      }
    };
    const sub = Linking.addEventListener('url', ({ url }) => handle(url));
    void Linking.getInitialURL().then((url) => {
      if (url) handle(url);
    });
    return () => sub.remove();
  }, []);

  const createShared = useCallback(
    async (title: string) => {
      if (!requireAuth()) return 'Log ind for at dele kurv';
      try {
        const data = await rpc('create_shared_cart', {
          p_items: cartToRows(items),
          p_title: title || 'Fælles kurv',
          p_name: displayName || 'Mig',
        });
        if (data.already && data.token) {
          enterShared(data);
          return null;
        }
        if (data.ok || data.token) {
          enterShared(data);
          return null;
        }
        return 'Kunne ikke oprette delt kurv';
      } catch (e) {
        return e instanceof Error ? e.message : 'Fejl';
      }
    },
    [displayName, enterShared, items, requireAuth, rpc],
  );

  const joinShared = useCallback(
    async (token: string) => {
      if (!requireAuth()) {
        setPendingInviteToken(token);
        return 'Log ind for at joine';
      }
      try {
        const data = await rpc('join_shared_cart', {
          p_token: token,
          p_name: displayName || 'Mig',
        });
        if (data.full) return `Kurven er fuld (max ${MAX_MEMBERS})`;
        if (data.ok || data.token) {
          enterShared(data);
          setPendingInviteToken(null);
          return null;
        }
        return 'Kunne ikke joine';
      } catch (e) {
        return e instanceof Error ? e.message : 'Fejl';
      }
    },
    [displayName, enterShared, requireAuth, rpc],
  );

  const leaveShared = useCallback(async () => {
    try {
      await rpc('leave_shared_cart');
    } catch {
      /* soft */
    }
    stopShared();
  }, [rpc, stopShared]);

  const saveList = useCallback(
    async (name: string) => {
      if (savedLists.length >= MAX_SAVED_LISTS) return 'Max 10 lister';
      const list: SavedList = {
        id: `${Date.now()}`,
        name: name.slice(0, 60),
        createdAt: new Date().toISOString(),
        items: [...items],
      };
      const next = [list, ...savedLists].slice(0, MAX_SAVED_LISTS);
      setSavedLists(next);
      if (stateToken.current) {
        try {
          await rpc('push_shared_saved_lists', { p_lists: compactLists(next) });
        } catch {
          /* soft */
        }
      }
      return null;
    },
    [items, rpc, savedLists],
  );

  const loadList = useCallback(
    (id: string) => {
      const list = savedLists.find((l) => l.id === id);
      if (list) applyFromServer(list.items);
    },
    [applyFromServer, savedLists],
  );

  const deleteList = useCallback(
    async (id: string) => {
      const next = savedLists.filter((l) => l.id !== id);
      setSavedLists(next);
      if (stateToken.current) {
        try {
          await rpc('push_shared_saved_lists', { p_lists: compactLists(next) });
        } catch {
          /* soft */
        }
      }
    },
    [rpc, savedLists],
  );

  useEffect(() => {
    if (user && pendingInviteToken) {
      void joinShared(pendingInviteToken);
    }
  }, [user, pendingInviteToken, joinShared]);

  useEffect(() => {
    if (!user) {
      stopShared();
      return;
    }
    void (async () => {
      try {
        const data = await rpc('get_my_shared_cart');
        if (data?.token) enterShared(data);
      } catch {
        /* none */
      }
    })();
  }, [user]); // eslint-disable-line react-hooks/exhaustive-deps

  const inviteUrl = state?.token
    ? `https://madshopper.dk/?liste=${state.token}`
    : null;

  const value = useMemo(
    () => ({
      active: !!state?.token,
      token: state?.token || null,
      title: state?.title || 'Fælles kurv',
      revision: state?.revision || 0,
      members: state?.member_list || [],
      maxMembers: state?.max_members || MAX_MEMBERS,
      savedLists,
      inviteUrl,
      createShared,
      joinShared,
      leaveShared,
      saveList,
      loadList,
      deleteList,
      pendingInviteToken,
      clearPendingInvite: () => setPendingInviteToken(null),
    }),
    [
      state,
      savedLists,
      inviteUrl,
      createShared,
      joinShared,
      leaveShared,
      saveList,
      loadList,
      deleteList,
      pendingInviteToken,
    ],
  );

  return <SharedContext.Provider value={value}>{children}</SharedContext.Provider>;
}

export function useSharedCart() {
  const ctx = useContext(SharedContext);
  if (!ctx) throw new Error('useSharedCart without provider');
  return ctx;
}
