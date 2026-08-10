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
  googleIosClientId?: string;
  googleAndroidClientId?: string;
  flavor?: string;
};

const extra = (Constants.expoConfig?.extra || {}) as Extra;

export const env = {
  flavor: (extra.flavor || 'production') as 'production' | 'staging' | 'local',
  /** Vises i Indstillinger, så en fejlmelding kan knyttes til en konkret udgave. */
  appVersion: Constants.expoConfig?.version || '',
  apiBaseUrl: (extra.apiBaseUrl || 'https://madshopper.dk').replace(/\/$/, ''),
  supabaseUrl: extra.supabaseUrl || '',
  supabaseAnonKey: extra.supabaseAnonKey || '',
  /** '' i prod, '_dev' på staging/lokal — spejler __SB_RPC_SUFFIX. */
  rpcSuffix: extra.rpcSuffix ?? '',
  /** Web Client ID — audience Supabase bruger til at verificere ID-tokens. */
  googleClientId: extra.googleClientId || '',
  googleIosClientId: extra.googleIosClientId || '',
  googleAndroidClientId: extra.googleAndroidClientId || '',
};

/**
 * Opskrift-featuren er kun åben på staging/lokalt - aldrig i et
 * produktions-build. Samme miljøsignal som webbens _recipes_enabled() i
 * app.py (`rpc_suffix` er tom i produktion, "_dev" ellers).
 *
 * Flaget bor her og ikke i RootNavigator, så både navigationen, forsiden og
 * alt andet kan gate på præcis den samme værdi uden at importere hinanden på
 * kryds (cirkulær import).
 */
export const recipesEnabled = Boolean(env.rpcSuffix);

export function rpcName(base: string): string {
  // delete_own_account har ingen _dev-variant (docs/native-app.md §10.3)
  if (base === 'delete_own_account') return base;
  return `${base}${env.rpcSuffix}`;
}
