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
import AsyncStorage from '@react-native-async-storage/async-storage';
import { makeRedirectUri } from 'expo-auth-session';
import * as Crypto from 'expo-crypto';
import * as Linking from 'expo-linking';
import * as AppleAuthentication from 'expo-apple-authentication';
import {
  GoogleSignin,
  isErrorWithCode,
  statusCodes,
} from '@react-native-google-signin/google-signin';
import { env, rpcName } from '../config/env';
import { getSupabase } from './supabase';
import { parseRecoveryLink } from './recoveryLink';
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
  /**
   * Sand fra det øjeblik et recovery-deep-link er vekslet til en session og
   * indtil adgangskoden er skiftet (eller brugeren forlader flowet).
   * AuthScreen bruger den til at åbne "vælg ny adgangskode" i stedet for
   * "du er logget ind".
   */
  recoveryActive: boolean;
  /** Sidste fejl fra et recovery-link (fx udløbet), til visning i AuthScreen. */
  recoveryError: string | null;
  endRecovery: () => void;
  /**
   * Falsk mens den personlige kurv hentes og merges ved login. Delt kurv må
   * først overtage kurven bagefter, ellers kan de to skrive oven i hinanden
   * (se SharedCartContext' synkroniseringsmodel).
   */
  personalCartReady: boolean;
  signInEmail: (email: string, password: string) => Promise<string | null>;
  signUpEmail: (
    email: string,
    password: string,
    displayName: string,
  ) => Promise<{ error: string | null; needsConfirmation: boolean }>;
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

/**
 * Supabase svarer paa engelsk ("Invalid login credentials"), og det blev vist
 * raat til brugeren. Samme oversaettelse som webbens translateErr() i
 * static/js/auth.js - hold de to i sync.
 */
function oversaetFejl(err: { message?: string } | null | undefined): string {
  const m = (err?.message || '').toLowerCase();
  if (m.includes('invalid login')) return 'Forkert email eller adgangskode.';
  if (m.includes('already registered') || m.includes('already been registered'))
    return 'Der findes allerede en konto med denne email. Prøv at logge ind.';
  if (m.includes('password') && (m.includes('least') || m.includes('short') ||
      m.includes('6 characters') || m.includes('8 characters')))
    return 'Adgangskoden skal være mindst 8 tegn.';
  if (m.includes('weak')) return 'Adgangskoden er for svag - vælg en længere.';
  if (m.includes('email') && m.includes('valid')) return 'Indtast en gyldig email.';
  if (m.includes('email not confirmed')) return 'Bekræft din email, før du logger ind.';
  if (m.includes('rate') || m.includes('too many')) return 'For mange forsøg - vent lidt og prøv igen.';
  if (m.includes('network') || m.includes('fetch')) return 'Ingen forbindelse. Tjek dit netværk.';
  return 'Noget gik galt. Prøv igen.';
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { items, applyFromServer, addSyncListener } = useCart();
  const [session, setSession] = useState<Session | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [recoveryActive, setRecoveryActive] = useState(false);
  const [recoveryError, setRecoveryError] = useState<string | null>(null);
  const [personalCartReady, setPersonalCartReady] = useState(false);
  // handleSignedIn kalder setUser(u) og registrerer LIGE EFTER kurv-lytteren.
  // Men state er ikke opdateret endnu i den render, saa baade scheduleSync og
  // pushCart lukkede om user === null - og de starter begge med "returnér hvis
  // ingen bruger". Lytteren blev dermed en permanent no-op efter login, og den
  // blev aldrig registreret igen, fordi lastSyncedUid afviser samme bruger.
  // Resultat: kurv-aendringer naaede aldrig serveren. En ref er altid ajour.
  const userRef = useRef<User | null>(null);
  const lastSyncedUid = useRef<string | null>(null);
  const syncTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unsubSync = useRef<(() => void) | null>(null);
  const itemsRef = useRef(items);
  userRef.current = user;
  itemsRef.current = items;

  const cartsTable = env.rpcSuffix ? `carts${env.rpcSuffix}` : CARTS_TABLE;

  /* Kurv-ejerskab og kvittering - samme model som webbens auth.js.
   *
   * Fletningen er en union med Math.max paa antal. Det er rigtigt naar en
   * gaest logger ind (gaestens varer skal med over), men en union kan ikke
   * udtrykke en SLETNING. Naar den samme bruger aabnede appen igen, kom en
   * fjernet vare derfor tilbage, hvis pushet ikke var naaet frem.
   *
   * Vi sammenligner IKKE tidsstempler - enhedens ur og Postgres' ur er ikke
   * det samme. I stedet gemmer vi serverens eget updated_at som kvittering,
   * hver gang vi selv har skrevet. Staar serveren uaendret siden da, var vi
   * den sidste der skrev, og den lokale kurv er sandheden inkl. sletninger.
   * Er den anderledes, har en anden enhed skrevet, og serveren vinder. */
  const OWNER_KEY = 'cartOwner';
  const SYNCED_KEY = 'cartSyncedAt';

  const rememberReceipt = useCallback(async (userId: string, updatedAt?: string | null) => {
    if (!updatedAt) return;
    try {
      await AsyncStorage.multiSet([[OWNER_KEY, userId], [SYNCED_KEY, String(updatedAt)]]);
    } catch {
      /* uden kvittering falder vi bare tilbage til at flette */
    }
  }, []);

  const pushCart = useCallback(
    async (cart = itemsRef.current) => {
      const sb = getSupabase();
      const u = userRef.current;
      if (!sb || !u) return;
      try {
        // select() giver serverens nye updated_at tilbage som kvittering.
        const res = await sb
          .from(cartsTable)
          .upsert({ user_id: u.id, items: cartToRows(cart) }, { onConflict: 'user_id' })
          .select('updated_at')
          .maybeSingle();
        if (!res.error) await rememberReceipt(u.id, res.data?.updated_at as string | undefined);
      } catch {
        /* stille */
      }
    },
    [cartsTable, user, rememberReceipt],
  );

  const scheduleSync = useCallback(
    (cart: typeof items) => {
      if (!userRef.current) return;
      if (syncTimer.current) clearTimeout(syncTimer.current);
      syncTimer.current = setTimeout(() => {
        void pushCart(cart);
      }, PERSONAL_SYNC_MS);
    },
    [pushCart, user],
  );

  const handleSignedIn = useCallback(
    async (u: User) => {
      userRef.current = u;
      setUser(u);
      if (lastSyncedUid.current === u.id) {
        // Samme bruger som sidst (fx token-refresh): kurven er allerede merget.
        setPersonalCartReady(true);
        return;
      }
      lastSyncedUid.current = u.id;
      setPersonalCartReady(false);
      const localCart = itemsRef.current;
      const sb = getSupabase();
      let serverRows: CompactCartItem[] = [];
      let serverUpdatedAt: string | null = null;
      if (sb) {
        try {
          const res = await sb
            .from(cartsTable)
            .select('items,updated_at')
            .eq('user_id', u.id)
            .maybeSingle();
          serverRows = (res.data?.items as CompactCartItem[]) || [];
          serverUpdatedAt = (res.data?.updated_at as string | undefined) || null;
        } catch {
          serverRows = [];
        }
      }

      let owner: string | null = null;
      let syncedAt: string | null = null;
      try {
        const pairs = await AsyncStorage.multiGet([OWNER_KEY, SYNCED_KEY]);
        owner = pairs[0]?.[1] ?? null;
        syncedAt = pairs[1]?.[1] ?? null;
      } catch {
        /* uden kvittering fletter vi */
      }

      // Se kommentaren ved OWNER_KEY. Tre tilfaelde, i denne raekkefoelge.
      let resolved;
      if (owner !== u.id || !syncedAt) {
        // Gaestekurv eller foerste login paa denne enhed: flet, saa varer lagt
        // i kurven inden login foelger med over.
        resolved = mergeCarts(localCart, serverRows);
      } else if (String(serverUpdatedAt || '') === syncedAt) {
        // Serveren staar som vi efterlod den - ingen anden enhed har rettet.
        // Den lokale kurv er nyeste sandhed, OGSAA naar den har faerre varer.
        resolved = localCart;
      } else {
        // En anden enhed har skrevet siden; den vinder.
        resolved = mergeCarts([], serverRows);
      }

      applyFromServer(resolved);
      try {
        if (sb) {
          const res = await sb
            .from(cartsTable)
            .upsert({ user_id: u.id, items: cartToRows(resolved) }, { onConflict: 'user_id' })
            .select('updated_at')
            .maybeSingle();
          if (!res.error) await rememberReceipt(u.id, res.data?.updated_at as string | undefined);
        }
      } catch {
        /* ignore */
      }
      if (unsubSync.current) unsubSync.current();
      unsubSync.current = addSyncListener(scheduleSync);
      // Først nu må delt kurv overtage kurven (se SharedCartContext).
      setPersonalCartReady(true);
    },
    [addSyncListener, applyFromServer, cartsTable, scheduleSync, rememberReceipt],
  );

  const handleSignedOut = useCallback(
    (clearLocal: boolean) => {
      setUser(null);
      setSession(null);
      setRecoveryActive(false);
      setPersonalCartReady(false);
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
        setRecoveryActive(true);
        return;
      }
      if (!applySession(sess) && event !== 'SIGNED_OUT') return;
      if (sess?.user) void handleSignedInRef.current(sess.user);
      else handleSignedOutRef.current(event === 'SIGNED_OUT');
    });
    return () => sub.subscription.unsubscribe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * Recovery-deep-link → session.
   *
   * Uden det her er "Glemt adgangskode" en blindgyde: mailen åbner appen, men
   * `detectSessionInUrl` er (med vilje) slået fra i en native app, så ingen
   * veksler linkets tokens til en session, og skærmen "vælg ny adgangskode"
   * kan aldrig nås. `parseRecoveryLink` accepterer KUN links der eksplicit er
   * mærket `type=recovery` - alt andet ignoreres her.
   */
  useEffect(() => {
    let cancelled = false;

    const handle = async (url: string) => {
      const link = parseRecoveryLink(url);
      if (!link || cancelled) return;
      if (link.kind === 'error') {
        setRecoveryError(link.message || 'Linket er udløbet. Bed om et nyt.');
        return;
      }
      const sb = getSupabase();
      if (!sb) {
        setRecoveryError('Supabase er ikke konfigureret');
        return;
      }
      setRecoveryError(null);
      // Sæt flaget FØR sessionen: onAuthStateChange fyrer synkront bagefter,
      // og AuthScreen skal vise "vælg ny adgangskode", ikke "du er logget ind".
      setRecoveryActive(true);
      const { error } =
        link.kind === 'tokens'
          ? await sb.auth.setSession({
              access_token: link.accessToken,
              refresh_token: link.refreshToken,
            })
          : await sb.auth.exchangeCodeForSession(link.code);
      if (cancelled) return;
      if (error) {
        setRecoveryActive(false);
        setRecoveryError(oversaetFejl(error));
      }
    };

    const sub = Linking.addEventListener('url', ({ url }) => void handle(url));
    void Linking.getInitialURL().then((url) => {
      if (url) void handle(url);
    });
    return () => {
      cancelled = true;
      sub.remove();
    };
  }, []);

  const endRecovery = useCallback(() => {
    setRecoveryActive(false);
    setRecoveryError(null);
  }, []);

  const signInEmail = useCallback(async (email: string, password: string) => {
    const sb = getSupabase();
    if (!sb) return 'Supabase er ikke konfigureret';
    const { error } = await sb.auth.signInWithPassword({ email, password });
    return error ? oversaetFejl(error) : null;
  }, []);

  const signUpEmail = useCallback(
    async (email: string, password: string, name: string) => {
      const sb = getSupabase();
      if (!sb) return { error: 'Supabase er ikke konfigureret', needsConfirmation: false };
      const { data, error } = await sb.auth.signUp({
        email,
        password,
        options: {
          data: { display_name: normalizeDisplayName(name) },
          emailRedirectTo: env.apiBaseUrl,
        },
      });
      if (error) return { error: oversaetFejl(error), needsConfirmation: false };
      // Supabase koerer med mailer_autoconfirm, saa signUp returnerer en
      // FAERDIG session og brugeren er logget ind med det samme. Kalderen skal
      // kunne se forskel: skaermen sagde foer "Tjek din mail for at bekraefte
      // oprettelsen" ved ENHVER succes - en mail der aldrig bliver sendt - og
      // blev staaende i stedet for at lukke. Kontoen blev oprettet, men for
      // brugeren lignede det at intet skete.
      return { error: null, needsConfirmation: !data.session };
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
      return error ? oversaetFejl(error) : null;
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
      if (error) return oversaetFejl(error);
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
    return error ? oversaetFejl(error) : null;
  }, []);

  const updatePassword = useCallback(async (password: string) => {
    const sb = getSupabase();
    if (!sb) return 'Supabase er ikke konfigureret';
    if (password.length < 8) return 'Adgangskode skal være mindst 8 tegn';
    const { error } = await sb.auth.updateUser({ password });
    if (error) return oversaetFejl(error);
    setRecoveryActive(false);
    setRecoveryError(null);
    return null;
  }, []);

  const logout = useCallback(async () => {
    const sb = getSupabase();
    if (syncTimer.current) {
      clearTimeout(syncTimer.current);
      syncTimer.current = null;
    }
    if (user) await pushCart(itemsRef.current);
    // Ejerskab og kvittering foelger brugeren - uden dette ville naeste bruger
    // paa samme enhed arve dem og faa sin serverkurv overskrevet af den
    // forriges lokale kopi.
    try {
      await AsyncStorage.multiRemove([OWNER_KEY, SYNCED_KEY]);
    } catch {
      /* ignore */
    }
    if (sb) await sb.auth.signOut();
  }, [pushCart, user]);

  /**
   * Apple Guideline 5.1.1(v): sletningen skal reelt gennemføres, og fejler
   * den, skal brugeren få det at vide. supabase-js KASTER ikke ved en
   * RPC-fejl - den returnerer `{ error }` - så en try/catch alene ville melde
   * succes selvom kontoen stadig findes. Vi logger derfor kun ud og rydder
   * kurven når RPC'en faktisk lykkedes.
   */
  const deleteAccount = useCallback(async () => {
    const sb = getSupabase();
    if (!sb || !user) return 'Ikke logget ind';
    try {
      const { error } = await sb.rpc('delete_own_account');
      if (error) {
        return oversaetFejl(error);
      }
    } catch (e) {
      // Netværksfejl o.l. - supabase-js kaster kun her, ikke ved SQL-fejl.
      return e instanceof Error ? e.message : 'Kontoen kunne ikke slettes. Prøv igen.';
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
      if (error) return oversaetFejl(error);
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
      recoveryActive,
      recoveryError,
      endRecovery,
      personalCartReady,
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
      recoveryActive,
      recoveryError,
      endRecovery,
      personalCartReady,
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
