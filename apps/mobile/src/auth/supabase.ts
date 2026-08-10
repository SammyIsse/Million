import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { env } from '../config/env';

/**
 * Secure session-storage: Keychain / EncryptedSharedPreferences via
 * expo-secure-store. Web-fallback til AsyncStorage (kun Expo web).
 *
 * Supabase-sessioner (JWT + user_metadata) overstiger ofte Keychains
 * ~2048-byte-grænse pr. item, så store værdier splittes over flere nøgler.
 */
const CHUNK_SIZE = 1800;

/**
 * Hvor mange chunk-nøgler ud over den nye/kendte længde vi rydder op i. Et
 * loop uden loft ville kunne hænge på en defekt Keychain; 64 chunks er ~115 KB
 * session-JSON, langt mere end en Supabase-session nogensinde fylder.
 */
const SWEEP_LIMIT = 64;

/**
 * Sletter `${key}_${from}`, `${key}_${from+1}`, … indtil en nøgle mangler.
 * Bruges både ved skrivning (ny session fylder færre chunks end den gamle) og
 * ved logout, så der aldrig ligger token-fragmenter tilbage i Keychain.
 */
async function sweepChunksFrom(key: string, from: number) {
  for (let i = from; i < from + SWEEP_LIMIT; i++) {
    const existing = await SecureStore.getItemAsync(`${key}_${i}`);
    if (existing == null) return;
    await SecureStore.deleteItemAsync(`${key}_${i}`);
  }
}

/**
 * Rækkefølgen er bevidst: chunks FØRST, derefter tælleren, og til sidst
 * oprydning af overskydende gamle chunks. Tælleren fungerer altså som
 * commit-markør - en samtidig læser kan aldrig se et nyt antal chunks som
 * endnu ikke er skrevet. Tælleren gemmes som "<antal>:<længde>", så
 * getChunked kan afvise en halvskrevet værdi i stedet for at aflevere en
 * sammenklistret blanding af ny og gammel session (parseInt læser stadig
 * antallet, så gamle "<antal>"-værdier fra tidligere versioner virker).
 */
async function setChunked(key: string, value: string) {
  const chunks: string[] = [];
  for (let i = 0; i < value.length; i += CHUNK_SIZE) {
    chunks.push(value.slice(i, i + CHUNK_SIZE));
  }
  await Promise.all(chunks.map((chunk, i) => SecureStore.setItemAsync(`${key}_${i}`, chunk)));
  await SecureStore.setItemAsync(`${key}_chunks`, `${chunks.length}:${value.length}`);
  await sweepChunksFrom(key, chunks.length);
}

async function getChunked(key: string): Promise<string | null> {
  const countRaw = await SecureStore.getItemAsync(`${key}_chunks`);
  if (!countRaw) return SecureStore.getItemAsync(key);
  const [countPart, lengthPart] = countRaw.split(':');
  const count = parseInt(countPart, 10);
  if (Number.isNaN(count) || count < 0) return null;
  const parts = await Promise.all(
    Array.from({ length: count }, (_, i) => SecureStore.getItemAsync(`${key}_${i}`)),
  );
  if (!parts.every((p) => p != null)) return null;
  const joined = parts.join('');
  const expected = lengthPart != null ? parseInt(lengthPart, 10) : NaN;
  if (!Number.isNaN(expected) && joined.length !== expected) return null;
  return joined;
}

async function removeChunked(key: string) {
  const countRaw = await SecureStore.getItemAsync(`${key}_chunks`);
  const count = countRaw ? parseInt(countRaw.split(':')[0], 10) : 0;
  if (count > 0) {
    await Promise.all(
      Array.from({ length: count }, (_, i) => SecureStore.deleteItemAsync(`${key}_${i}`)),
    );
  }
  // Fragmenter fra en tidligere, større session (eller fra versionen før
  // sweep'et fandtes) ligger ud over `count` - de skal også væk.
  await sweepChunksFrom(key, Math.max(count, 0));
  await SecureStore.deleteItemAsync(`${key}_chunks`);
  await SecureStore.deleteItemAsync(key);
}

/**
 * Keychain kræver en entitlement, som et USIGNERET build ikke har. Alt
 * SecureStore-kald fejler så med "Fandt ikke en nødvendig godkendelse", og
 * supabase-js kan hverken gemme eller læse sessionen: login lykkes på serveren,
 * men appen kommer aldrig i logget-ind-tilstand. Det er præcis situationen i
 * simulatoren, så længe der ikke findes en Apple Developer-konto til signering
 * (docs/env-setup.md §5) - og det gjorde appen umulig at teste bag login.
 *
 * I DEV-builds falder vi derfor tilbage til AsyncStorage, så udvikling og test
 * kan lade sig gøre. AsyncStorage er IKKE krypteret, så det må aldrig ske i et
 * produktionsbuild: dér lader vi fejlen boble op, så den bliver set og rettet
 * frem for at sessionen stilletiende ligger ubeskyttet.
 */
// Et DEV-build er usigneret og har derfor ingen Keychain-entitlement: HVERT
// SecureStore-kald fejler. Et forsøg-og-fald-tilbage var ikke nok - supabase-js
// har sin egen auto-refresh, der kalder storage direkte, og den blev ved med at
// fejle ("Auto refresh tick failed"), hvilket gjorde sessionen ustabil. Når
// sessionen vakler, er `user` null i ny og næ, og så no-op'er kurv-synken uden
// at sige noget. Derfor: i dev bruger vi AsyncStorage HELE vejen og rører slet
// ikke Keychain.
//
// AsyncStorage er IKKE krypteret, så det gælder kun dev. Produktionsbuilds er
// signerede og bruger Keychain som før - inkl. chunk-opdelingen ovenfor, som
// derfor ikke bliver afprøvet lokalt. Det er prisen for overhovedet at kunne
// teste bag login uden en Apple Developer-konto (docs/env-setup.md §5).
const brugUkrypteretLager = Platform.OS === 'web' || __DEV__;

if (__DEV__ && Platform.OS !== 'web') {
  console.warn(
    '[auth] DEV-build: sessionen gemmes i AsyncStorage (ukrypteret), fordi ' +
      'Keychain kræver et signeret build. Produktion bruger Keychain.',
  );
}

const ExpoSecureStoreAdapter = {
  getItem: (key: string) =>
    brugUkrypteretLager ? AsyncStorage.getItem(key) : getChunked(key),
  setItem: (key: string, value: string) =>
    brugUkrypteretLager ? AsyncStorage.setItem(key, value) : setChunked(key, value),
  removeItem: (key: string) =>
    brugUkrypteretLager ? AsyncStorage.removeItem(key) : removeChunked(key),
};

let client: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient | null {
  if (!env.supabaseUrl || !env.supabaseAnonKey) return null;
  if (!client) {
    client = createClient(env.supabaseUrl, env.supabaseAnonKey, {
      auth: {
        storage: ExpoSecureStoreAdapter,
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: false,
      },
    });
  }
  return client;
}
