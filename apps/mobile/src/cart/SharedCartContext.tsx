/**
 * Delt kurv + gemte lister — poll 2500 / push debounce 450 / max 6 medlemmer /
 * max 10 lister (docs/native-app.md §9).
 *
 * SYNKRONISERINGSMODEL (læs denne før du retter noget herinde — en fejl her
 * MISTER varer for brugeren):
 *
 * 1. Sandheden er serverens `revision`. Konflikter afgøres last-write-wins,
 *    præcis som på web: en poll overtager kun, når `remoteRev > localRev` OG
 *    ændringen kom fra en anden (`updated_by !== mig`).
 * 2. Push er debounced 450 ms, men debouncen holder IKKE på et snapshot.
 *    Timeren pusher altid `pendingCart.current`, dvs. kurven som den ser ud
 *    når timeren rent faktisk løber ud. Et gemt snapshot kunne nå at blive
 *    forældet, inden det blev sendt.
 * 3. Når en fjern-ændring bliver anvendt, ANNULLERES en ventende push, og
 *    `pendingCart` ryddes. Lokalt indhold blev jo netop erstattet af det
 *    fjerne, så den ventende push ville skrive den fjerne ændring væk igen.
 * 4. Modsat: der pulles ikke, mens vi har en push i luften eller i kø
 *    (`pushTimer`/`pushInFlight`). Ellers kunne en poll nå at overskrive
 *    lokale ændringer, der endnu ikke er nået frem til serveren.
 * 5. `epoch` tælles op ved hver enter/stop. Alle svar fra igangværende RPC'er
 *    kasseres, hvis epoch er skiftet undervejs — ellers kunne et svar sætte
 *    state (medlemmer, kurv) EFTER at brugeren har forladt gruppen eller er
 *    logget ud.
 * 6. Ved login venter opstarten på `personalCartReady` fra AuthContext, så
 *    den personlige kurv-merge er færdig, FØR den delte kurv overtager.
 *    Uden den rækkefølge kunne UI'et vise den personlige kurv, mens gruppen
 *    troede noget andet — og næste redigering ville skubbe hele den
 *    personlige kurv ud til alle i gruppen.
 * 7. Init reagerer på bruger-ID, ikke på bruger-OBJEKTET. Et token-refresh
 *    (~1 t) giver et nyt objekt uden at brugeren skifter, og en re-init dér
 *    ville kassere upushede lokale ændringer.
 *
 * Gemte lister: personlige lister ligger i AsyncStorage under
 * `savedLists:<userId>` (samme nøgle og form som webbens localStorage), mens
 * lister i en gruppe ligger i `shared_carts.saved_lists` via RPC. `savedLists`
 * udstiller derfor gruppens lister når man er i en gruppe, ellers de
 * personlige — nøjagtig som webbens getSavedLists().
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
import AsyncStorage from '@react-native-async-storage/async-storage';
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
  error?: string;
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
  maxSavedLists: number;
  inviteUrl: string | null;
  createShared: (title: string) => Promise<string | null>;
  joinShared: (token: string) => Promise<string | null>;
  leaveShared: () => Promise<void>;
  saveList: (name: string) => Promise<string | null>;
  loadList: (id: string) => void;
  deleteList: (id: string) => Promise<string | null>;
  pendingInviteToken: string | null;
  clearPendingInvite: () => void;
};

const SharedContext = createContext<SharedContextValue | null>(null);

/** Samme nøgle som webbens localStorage (`savedLists:<userId>`). */
function personalListsKey(userId: string): string {
  return `savedLists:${userId}`;
}

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
    id: String(l.id || '').slice(0, 40),
    name: String(l.name || 'Liste').trim().slice(0, 80) || 'Liste',
    created_at: l.createdAt,
    items: cartToRows(l.items),
  }));
}

/** Tolerant parse af det lokale JSON — en defekt værdi må ikke vælte kurven. */
function parseStoredLists(raw: string | null): SavedList[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as SavedList[];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((l) => l && typeof l.id === 'string' && Array.isArray(l.items))
      .slice(0, MAX_SAVED_LISTS);
  } catch {
    return [];
  }
}

export function SharedCartProvider({ children }: { children: React.ReactNode }) {
  const { user, displayName, requireAuth, personalCartReady } = useAuth();
  const { items, applyFromServer, addSyncListener, notify } = useCart();
  const [state, setState] = useState<SharedPayload | null>(null);
  const [groupLists, setGroupLists] = useState<SavedList[]>([]);
  const [personalLists, setPersonalLists] = useState<SavedList[]>([]);
  const [pendingInviteToken, setPendingInviteToken] = useState<string | null>(null);
  const localRev = useRef(0);
  const pushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const applyingRemote = useRef(false);
  const pushInFlight = useRef(false);
  const pendingCart = useRef<CartItem[] | null>(null);
  const unsubPush = useRef<(() => void) | null>(null);
  const stateToken = useRef<string | null>(null);
  /** Se punkt 5 i modellen ovenfor. */
  const epoch = useRef(0);

  const userId = user?.id || null;
  const active = !!state?.token;

  const rpc = useCallback(async (name: string, args?: Record<string, unknown>) => {
    const sb = getSupabase();
    if (!sb) throw new Error('Supabase mangler');
    const { data, error } = await sb.rpc(rpcName(name), args || {});
    if (error) throw error;
    return data as SharedPayload;
  }, []);

  /** Rydder en ventende push. Kaldes både ved remote-apply og ved stop. */
  const cancelPendingPush = useCallback(() => {
    if (pushTimer.current) {
      clearTimeout(pushTimer.current);
      pushTimer.current = null;
    }
    pendingCart.current = null;
  }, []);

  const pushShared = useCallback(async () => {
    const cart = pendingCart.current;
    pendingCart.current = null;
    pushTimer.current = null;
    if (!stateToken.current || applyingRemote.current || !cart) return;
    const myEpoch = epoch.current;
    pushInFlight.current = true;
    try {
      const data = await rpc('push_shared_cart', { p_items: cartToRows(cart) });
      if (epoch.current !== myEpoch) return;
      if (data?.revision != null) localRev.current = data.revision;
      setState((prev) => (prev ? { ...prev, ...data } : data));
    } catch {
      /* soft — næste redigering eller poll retter op */
    } finally {
      if (epoch.current === myEpoch) pushInFlight.current = false;
    }
  }, [rpc]);

  const schedulePush = useCallback(
    (cart: CartItem[]) => {
      if (!stateToken.current || applyingRemote.current) return;
      // Gem den NYESTE kurv, ikke et snapshot pr. tastetryk (punkt 2).
      pendingCart.current = cart;
      if (pushTimer.current) clearTimeout(pushTimer.current);
      pushTimer.current = setTimeout(() => {
        void pushShared();
      }, PUSH_DEBOUNCE_MS);
    },
    [pushShared],
  );

  const stopShared = useCallback(() => {
    epoch.current += 1;
    if (pollTimer.current) clearInterval(pollTimer.current);
    pollTimer.current = null;
    cancelPendingPush();
    pushInFlight.current = false;
    if (unsubPush.current) {
      unsubPush.current();
      unsubPush.current = null;
    }
    stateToken.current = null;
    setState(null);
    setGroupLists([]);
    localRev.current = 0;
  }, [cancelPendingPush]);

  const pullShared = useCallback(async () => {
    if (!stateToken.current) return;
    // Punkt 4: hold fingrene væk mens vores egen ændring er undervejs.
    if (pushTimer.current || pushInFlight.current || applyingRemote.current) return;
    const myEpoch = epoch.current;
    try {
      const data = await rpc('get_my_shared_cart');
      if (epoch.current !== myEpoch) return;
      if (!data?.ok && !data?.token) {
        stopShared();
        return;
      }
      const remoteRev = data.revision || 0;
      const me = data.member_list?.find((m) => m.me)?.id;
      if (remoteRev > localRev.current && data.updated_by && data.updated_by !== me) {
        applyingRemote.current = true;
        try {
          // Punkt 3: den ventende push bygger på en kurv der lige er blevet
          // overhalet — den ville skrive den fjerne ændring væk igen.
          cancelPendingPush();
          applyFromServer(rowsToCart(data.items || []));
          localRev.current = remoteRev;
        } finally {
          applyingRemote.current = false;
        }
      }
      // Punkt e i rettelsesplanen: listerne skal opdateres uanset revision,
      // ellers ser samme bruger på en anden enhed aldrig liste-ændringer.
      setGroupLists((prev) => {
        const next = hydrateLists(data.saved_lists);
        return JSON.stringify(prev) === JSON.stringify(next) ? prev : next;
      });
      setState((prev) => ({ ...(prev || {}), ...data }));
    } catch {
      /* soft */
    }
  }, [applyFromServer, cancelPendingPush, rpc, stopShared]);

  const enterShared = useCallback(
    (data: SharedPayload) => {
      epoch.current += 1;
      cancelPendingPush();
      pushInFlight.current = false;
      stateToken.current = data.token || null;
      setState(data);
      localRev.current = data.revision || 0;
      applyingRemote.current = true;
      try {
        applyFromServer(rowsToCart(data.items || []));
      } finally {
        applyingRemote.current = false;
      }
      setGroupLists(hydrateLists(data.saved_lists));
      if (unsubPush.current) unsubPush.current();
      unsubPush.current = addSyncListener(schedulePush);
      if (pollTimer.current) clearInterval(pollTimer.current);
      pollTimer.current = setInterval(() => {
        void pullShared();
      }, POLL_MS);
    },
    [addSyncListener, applyFromServer, cancelPendingPush, pullShared, schedulePush],
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
        if (data.full) {
          setPendingInviteToken(null);
          return `Kurven er fuld (max ${MAX_MEMBERS})`;
        }
        if (data.ok || data.token) {
          enterShared(data);
          setPendingInviteToken(null);
          return null;
        }
        // Ugyldig/udløbet kode: ryd token'et, ellers forsøger effekten nedenfor
        // i det uendelige at joine den samme døde invitation.
        setPendingInviteToken(null);
        return 'Kunne ikke joine — tjek koden';
      } catch (e) {
        setPendingInviteToken(null);
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

  /* ---------------- Gemte lister ---------------- */

  // Personlige lister hentes/ryddes efter bruger-ID (ikke bruger-objektet).
  useEffect(() => {
    if (!userId) {
      setPersonalLists([]);
      return;
    }
    let cancelled = false;
    void AsyncStorage.getItem(personalListsKey(userId)).then((raw) => {
      if (!cancelled) setPersonalLists(parseStoredLists(raw));
    });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  const persistPersonalLists = useCallback(
    async (next: SavedList[]) => {
      if (!userId) return false;
      setPersonalLists(next);
      try {
        await AsyncStorage.setItem(
          personalListsKey(userId),
          JSON.stringify(next.slice(0, MAX_SAVED_LISTS)),
        );
        return true;
      } catch {
        return false;
      }
    },
    [userId],
  );

  const savedLists = active ? groupLists : personalLists;

  const saveList = useCallback(
    async (name: string) => {
      if (!requireAuth()) return 'Log ind for at gemme lister';
      if (!items.length) return 'Kurven er tom';
      if (savedLists.length >= MAX_SAVED_LISTS) {
        return `Du kan maks have ${MAX_SAVED_LISTS} gemte lister. Slet en først.`;
      }
      const list: SavedList = {
        id: `${Date.now()}`,
        name: name.trim().slice(0, 80) || 'Liste',
        createdAt: new Date().toISOString(),
        items: items.map((i) => ({ ...i })),
      };
      const next = [list, ...savedLists].slice(0, MAX_SAVED_LISTS);
      if (stateToken.current) {
        try {
          const data = await rpc('push_shared_saved_lists', { p_lists: compactLists(next) });
          if (data && data.ok === false) {
            return data.error === 'lists_full'
              ? `Gruppen kan maks have ${MAX_SAVED_LISTS} gemte lister.`
              : 'Kunne ikke gemme listen. Prøv igen.';
          }
          setGroupLists(data?.saved_lists ? hydrateLists(data.saved_lists) : next);
          return null;
        } catch (e) {
          return e instanceof Error ? e.message : 'Kunne ikke gemme listen. Prøv igen.';
        }
      }
      const ok = await persistPersonalLists(next);
      return ok ? null : 'Kunne ikke gemme listen. Prøv igen.';
    },
    [items, persistPersonalLists, requireAuth, rpc, savedLists],
  );

  const loadList = useCallback(
    (id: string) => {
      const list = savedLists.find((l) => l.id === id);
      if (!list) return;
      applyFromServer(list.items.map((i) => ({ ...i })));
      // applyFromServer notificerer ikke af sig selv (CartBridge-paritet), men
      // web's loadSavedList kalder saveCart() — listen skal altså synke videre
      // til gruppen/skyen som enhver anden ændring.
      notify();
    },
    [applyFromServer, notify, savedLists],
  );

  const deleteList = useCallback(
    async (id: string) => {
      const next = savedLists.filter((l) => l.id !== id);
      if (stateToken.current) {
        try {
          const data = await rpc('push_shared_saved_lists', { p_lists: compactLists(next) });
          if (data && data.ok === false) return 'Kunne ikke slette listen. Prøv igen.';
          setGroupLists(data?.saved_lists ? hydrateLists(data.saved_lists) : next);
          return null;
        } catch (e) {
          return e instanceof Error ? e.message : 'Kunne ikke slette listen. Prøv igen.';
        }
      }
      const ok = await persistPersonalLists(next);
      return ok ? null : 'Kunne ikke slette listen. Prøv igen.';
    },
    [persistPersonalLists, rpc, savedLists],
  );

  /* ---------------- Opstart / nedlukning ---------------- */

  useEffect(() => {
    if (user && pendingInviteToken) {
      void joinShared(pendingInviteToken);
    }
  }, [user, pendingInviteToken, joinShared]);

  useEffect(() => {
    if (!userId) {
      stopShared();
      return;
    }
    // Punkt 6: vent på den personlige kurv-merge, så de to ikke skriver oven
    // i hinanden ved login.
    if (!personalCartReady) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await rpc('get_my_shared_cart');
        if (cancelled) return;
        if (data?.token) enterShared(data);
      } catch {
        /* ingen gruppe / offline */
      }
    })();
    return () => {
      cancelled = true;
    };
    // Punkt 7: bruger-ID, ikke bruger-objektet.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, personalCartReady]);

  // Ryd op ved unmount, så timere ikke overlever provideren.
  useEffect(() => stopShared, [stopShared]);

  const inviteUrl = state?.token
    ? `https://madshopper.dk/?liste=${state.token}`
    : null;

  const value = useMemo(
    () => ({
      active,
      token: state?.token || null,
      title: state?.title || 'Fælles kurv',
      revision: state?.revision || 0,
      members: state?.member_list || [],
      maxMembers: state?.max_members || MAX_MEMBERS,
      savedLists,
      maxSavedLists: MAX_SAVED_LISTS,
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
      active,
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
