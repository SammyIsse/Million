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
import { Platform } from 'react-native';
import { makeRedirectUri } from 'expo-auth-session';
import * as Crypto from 'expo-crypto';
import * as AppleAuthentication from 'expo-apple-authentication';
import {
  GoogleSignin,
  isErrorWithCode,
  statusCodes,
} from '@react-native-google-signin/google-signin';
import { env, rpcName } from '../config/env';
import { getSupabase } from './supabase';
import { useCart } from '../cart/CartContext';
import { cartToRows, mergeCarts, type CompactCartItem } from '../cart/types';

let googleConfigured = false;
function ensureGoogleConfigured() {
  if (googleConfigured) return;
  googleConfigured = true;
  GoogleSignin.configure({
    webClientId: env.googleClientId,
    iosClientId: env.googleIosClientId,
  });
}

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
  signInApple: () => Promise<string | null>;
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

  const lastAccessToken = useRef<string | null | undefined>(undefined);

  /**
   * handleSignedIn/handleSignedOut skifter identitet når `user` ændrer sig
   * (via scheduleSync→pushCart→user). Hvis effekten nedenfor havde dem som
   * deps, ville hvert setUser-kald genstarte effekten → ny getSession() →
   * nyt setUser-kald → uendelig løkke. Derfor: refs der altid peger på den
   * nyeste version, og effekten kører kun én gang ved mount.
   */
  const handleSignedInRef = useRef(handleSignedIn);
  handleSignedInRef.current = handleSignedIn;
  const handleSignedOutRef = useRef(handleSignedOut);
  handleSignedOutRef.current = handleSignedOut;

  useEffect(() => {
    const sb = getSupabase();
    if (!sb) {
      setReady(true);
      return;
    }
    const applySession = (sess: Session | null) => {
      const token = sess?.access_token ?? null;
      if (lastAccessToken.current === token) return false;
      lastAccessToken.current = token;
      setSession(sess);
      return true;
    };
    sb.auth.getSession().then(({ data }) => {
      applySession(data.session);
      if (data.session?.user) void handleSignedInRef.current(data.session.user);
      setReady(true);
    });
    const { data: sub } = sb.auth.onAuthStateChange((event, sess) => {
      if (event === 'PASSWORD_RECOVERY') {
        applySession(sess);
        if (sess?.user) setUser(sess.user);
        return;
      }
      if (!applySession(sess) && event !== 'SIGNED_OUT') return;
      if (sess?.user) void handleSignedInRef.current(sess.user);
      else handleSignedOutRef.current(event === 'SIGNED_OUT');
    });
    return () => sub.subscription.unsubscribe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    ensureGoogleConfigured();
    try {
      if (Platform.OS === 'android') {
        await GoogleSignin.hasPlayServices({ showPlayServicesUpdateDialog: true });
      }
      const response = await GoogleSignin.signIn();
      if (response.type !== 'success') return 'Google-login afbrudt';
      const idToken = response.data.idToken;
      if (!idToken) return 'Manglende ID-token fra Google';
      const { error } = await sb.auth.signInWithIdToken({ provider: 'google', token: idToken });
      return error ? error.message : null;
    } catch (err) {
      if (isErrorWithCode(err) && err.code === statusCodes.IN_PROGRESS) {
        return 'Google-login er allerede i gang';
      }
      return err instanceof Error ? err.message : 'Google-login fejlede';
    }
  }, []);

  const signInApple = useCallback(async () => {
    const sb = getSupabase();
    if (!sb) return 'Supabase er ikke konfigureret';
    if (Platform.OS !== 'ios') return 'Apple-login er kun tilgængeligt på iOS';
    try {
      const rawNonce = Crypto.randomUUID();
      const hashedNonce = await Crypto.digestStringAsync(
        Crypto.CryptoDigestAlgorithm.SHA256,
        rawNonce,
      );
      const credential = await AppleAuthentication.signInAsync({
        requestedScopes: [
          AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
          AppleAuthentication.AppleAuthenticationScope.EMAIL,
        ],
        nonce: hashedNonce,
      });
      if (!credential.identityToken) return 'Manglende ID-token fra Apple';
      const { error } = await sb.auth.signInWithIdToken({
        provider: 'apple',
        token: credential.identityToken,
        nonce: rawNonce,
      });
      if (error) return error.message;
      // Apple sender kun fullName ved allerførste login med denne Apple ID.
      if (credential.fullName) {
        const name = AppleAuthentication.formatFullName(credential.fullName);
        if (name.trim()) {
          try {
            await sb.auth.updateUser({ data: { display_name: normalizeDisplayName(name) } });
          } catch {
            /* soft */
          }
        }
      }
      return null;
    } catch (err) {
      if (err instanceof Error && 'code' in err && err.code === 'ERR_REQUEST_CANCELED') {
        return 'Apple-login afbrudt';
      }
      return err instanceof Error ? err.message : 'Apple-login fejlede';
    }
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
      signInApple,
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
      signInApple,
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
