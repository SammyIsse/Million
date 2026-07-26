/**
 * MadShopper native — miljø / flavors.
 *
 * Produktion: EXPO_PUBLIC_FLAVOR=production, RPC-suffix ""
 * Staging:    EXPO_PUBLIC_FLAVOR=staging,    RPC-suffix "_dev"
 *             (samme Auth-projekt; skriver til *_dev — se docs/native-app.md §10.3)
 */
import Constants from 'expo-constants';

type Extra = {
  apiBaseUrl?: string;
  supabaseUrl?: string;
  supabaseAnonKey?: string;
  rpcSuffix?: string;
  googleClientId?: string;
  flavor?: string;
};

const extra = (Constants.expoConfig?.extra || {}) as Extra;

export const env = {
  flavor: (extra.flavor || 'production') as 'production' | 'staging' | 'local',
  apiBaseUrl: (extra.apiBaseUrl || 'https://madshopper.dk').replace(/\/$/, ''),
  supabaseUrl: extra.supabaseUrl || '',
  supabaseAnonKey: extra.supabaseAnonKey || '',
  /** '' i prod, '_dev' på staging/lokal — spejler __SB_RPC_SUFFIX. */
  rpcSuffix: extra.rpcSuffix ?? '',
  googleClientId: extra.googleClientId || '',
};

export function rpcName(base: string): string {
  // delete_own_account har ingen _dev-variant (docs/native-app.md §10.3)
  if (base === 'delete_own_account') return base;
  return `${base}${env.rpcSuffix}`;
}
