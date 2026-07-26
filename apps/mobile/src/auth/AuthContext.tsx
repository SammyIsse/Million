import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { Session, User } from '@supabase/supabase-js';
import * as WebBrowser from 'expo-web-browser';
import { makeRedirectUri } from 'expo-auth-session';
import { env, rpcName } from '../config/env';
import { getSupabase } from './supabase';
import { useCart } from '../cart/CartContext';
import { cartToRows, mergeCarts, type CompactCartItem } from '../cart/types';

WebBrowser.maybeCompleteAuthSession();

const PERSONAL_SYNC_MS = 800;
const CARTS_TABLE = 'carts'; // + TABLE_SUFFIX via env on server; client uses RPC/table from __SB_CARTS pattern

type AuthContextValue = {
  user: User | null;
  session: Session | null;
  ready: boolean;
  displayName: string;
  signInEmail: (email: string, password: string) => Promise<string | null>;
  signUpEmail: (
    email: string,
    password: string,
    displayName: string,
  ) => Promise<string | null>;
  signInGoogle: () => Promise<string | null>;
  resetPassword: (email: string) => Promise<string | null>;
  updatePassword: (password: string) => Promise<string | null>;
  logout: () => Promise<void>;
  deleteAccount: () => Promise<string | null>;
  saveDisplayName: (name: string) => Promise<string | null>;
  requireAuth: () => boolean;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function normalizeDisplayName(raw: string): string {
  return String(raw || '')
    .replace(/[\u0000-\u001f\u007f]/g, '')
    .trim()
    .slice(0, 40);
}

function readUserDisplayName(user: User | null): string {
  const meta = (user?.user_metadata || {}) as Record<string, string>;
  return normalizeDisplayName(meta.display_name || meta.full_name || meta.name || '');
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { items, applyFromServer, addSyncListener } = useCart();
  const [session, setSession] = useState<Session | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const lastSyncedUid = useRef<string | null>(null);
  const syncTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unsubSync = useRef<(() => void) | null>(null);
  const itemsRef = useRef(items);
  itemsRef.current = items;

  const cartsTable = env.rpcSuffix ? `carts${env.rpcSuffix}` : CARTS_TABLE;

  const pullCart = useCallback(async (): Promise<CompactCartItem[]> => {
    const sb = getSupabase();
    if (!sb || !user) return [];
    try {
      const res = await sb.from(cartsTable).select('items').eq('user_id', user.id).maybeSingle();
      if (res.error) return [];
      return (res.data?.items as CompactCartItem[]) || [];
    } catch {
      return [];
    }
  }, [cartsTable, user]);

  const pushCart = useCallback(
    async (cart = itemsRef.current) => {
      const sb = getSupabase();
      const u = user;
      if (!sb || !u) return;
      try {
        await sb.from(cartsTable).upsert(
          { user_id: u.id, items: cartToRows(cart) },
          { onConflict: 'user_id' },
        );
      } catch {
        /* stille */
      }
    },
    [cartsTable, user],
  );

  const scheduleSync = useCallback(
    (cart: typeof items) => {
      if (!user) return;
      if (syncTimer.current) clearTimeout(syncTimer.current);
      syncTimer.current = setTimeout(() => {
        void pushCart(cart);
      }, PERSONAL_SYNC_MS);
    },
    [pushCart, user],
  );

  const handleSignedIn = useCallback(
    async (u: User) => {
      setUser(u);
      if (lastSyncedUid.current === u.id) return;
      lastSyncedUid.current = u.id;
      const localCart = itemsRef.current;
      const sb = getSupabase();
      let serverRows: CompactCartItem[] = [];
      if (sb) {
        try {
          const res = await sb
            .from(cartsTable)
            .select('items')
            .eq('user_id', u.id)
            .maybeSingle();
          serverRows = (res.data?.items as CompactCartItem[]) || [];
        } catch {
          serverRows = [];
        }
      }
      const merged = mergeCarts(localCart, serverRows);
      applyFromServer(merged);
      try {
        if (sb) {
          await sb.from(cartsTable).upsert(
            { user_id: u.id, items: cartToRows(merged) },
            { onConflict: 'user_id' },
          );
        }
      } catch {
        /* ignore */
      }
      if (unsubSync.current) unsubSync.current();
      unsubSync.current = addSyncListener(scheduleSync);
    },
    [addSyncListener, applyFromServer, cartsTable, scheduleSync],
  );

  const handleSignedOut = useCallback(
    (clearLocal: boolean) => {
      setUser(null);
      setSession(null);
      lastSyncedUid.current = null;
      if (unsubSync.current) {
        unsubSync.current();
        unsubSync.current = null;
      }
      if (clearLocal) applyFromServer([]);
    },
    [applyFromServer],
  );

  useEffect(() => {
    const sb = getSupabase();
    if (!sb) {
      setReady(true);
      return;
    }
    sb.auth.getSession().then(({ data }) => {
      setSession(data.session);
      if (data.session?.user) void handleSignedIn(data.session.user);
      setReady(true);
    });
    const { data: sub } = sb.auth.onAuthStateChange((event, sess) => {
      setSession(sess);
      if (event === 'PASSWORD_RECOVERY') {
        if (sess?.user) setUser(sess.user);
        return;
      }
      if (sess?.user) void handleSignedIn(sess.user);
      else handleSignedOut(event === 'SIGNED_OUT');
    });
    return () => sub.subscription.unsubscribe();
  }, [handleSignedIn, handleSignedOut]);

  const signInEmail = useCallback(async (email: string, password: string) => {
    const sb = getSupabase();
    if (!sb) return 'Supabase er ikke konfigureret';
    const { error } = await sb.auth.signInWithPassword({ email, password });
    return error ? error.message : null;
  }, []);

  const signUpEmail = useCallback(
    async (email: string, password: string, name: string) => {
      const sb = getSupabase();
      if (!sb) return 'Supabase er ikke konfigureret';
      const { error } = await sb.auth.signUp({
        email,
        password,
        options: {
          data: { display_name: normalizeDisplayName(name) },
          emailRedirectTo: env.apiBaseUrl,
        },
      });
      return error ? error.message : null;
    },
    [],
  );

  const signInGoogle = useCallback(async () => {
    const sb = getSupabase();
    if (!sb) return 'Supabase er ikke konfigureret';
    const redirectTo = makeRedirectUri({ scheme: 'madshopper' });
    const { data, error } = await sb.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo, skipBrowserRedirect: true },
    });
    if (error) return error.message;
    if (!data.url) return 'Ingen OAuth-URL';
    const result = await WebBrowser.openAuthSessionAsync(data.url, redirectTo);
    if (result.type !== 'success' || !result.url) return 'Google-login afbrudt';
    const url = new URL(result.url);
    const params = new URLSearchParams(url.hash.replace(/^#/, '') || url.search);
    const access_token = params.get('access_token');
    const refresh_token = params.get('refresh_token');
    if (!access_token || !refresh_token) return 'Manglende tokens fra Google';
    const { error: sessErr } = await sb.auth.setSession({ access_token, refresh_token });
    return sessErr ? sessErr.message : null;
  }, []);

  const resetPassword = useCallback(async (email: string) => {
    const sb = getSupabase();
    if (!sb) return 'Supabase er ikke konfigureret';
    const { error } = await sb.auth.resetPasswordForEmail(email, {
      redirectTo: makeRedirectUri({ scheme: 'madshopper' }),
    });
    return error ? error.message : null;
  }, []);

  const updatePassword = useCallback(async (password: string) => {
    const sb = getSupabase();
    if (!sb) return 'Supabase er ikke konfigureret';
    if (password.length < 8) return 'Adgangskode skal være mindst 8 tegn';
    const { error } = await sb.auth.updateUser({ password });
    return error ? error.message : null;
  }, []);

  const logout = useCallback(async () => {
    const sb = getSupabase();
    if (syncTimer.current) {
      clearTimeout(syncTimer.current);
      syncTimer.current = null;
    }
    if (user) await pushCart(itemsRef.current);
    if (sb) await sb.auth.signOut();
  }, [pushCart, user]);

  const deleteAccount = useCallback(async () => {
    const sb = getSupabase();
    if (!sb || !user) return 'Ikke logget ind';
    try {
      await sb.rpc('delete_own_account');
    } catch {
      /* fortsæt */
    }
    await sb.auth.signOut();
    applyFromServer([]);
    return null;
  }, [applyFromServer, user]);

  const saveDisplayName = useCallback(
    async (raw: string) => {
      const sb = getSupabase();
      if (!sb || !user) return 'Ikke logget ind';
      const nm = normalizeDisplayName(raw);
      if (!nm) return 'Navn er påkrævet';
      const { error } = await sb.auth.updateUser({ data: { display_name: nm } });
      if (error) return error.message;
      try {
        await sb.rpc(rpcName('set_my_display_name'), { p_name: nm });
      } catch {
        /* soft */
      }
      return null;
    },
    [user],
  );

  const requireAuth = useCallback(() => !!user, [user]);

  const value = useMemo(
    () => ({
      user,
      session,
      ready,
      displayName: readUserDisplayName(user),
      signInEmail,
      signUpEmail,
      signInGoogle,
      resetPassword,
      updatePassword,
      logout,
      deleteAccount,
      saveDisplayName,
      requireAuth,
    }),
    [
      user,
      session,
      ready,
      signInEmail,
      signUpEmail,
      signInGoogle,
      resetPassword,
      updatePassword,
      logout,
      deleteAccount,
      saveDisplayName,
      requireAuth,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth without AuthProvider');
  return ctx;
}
