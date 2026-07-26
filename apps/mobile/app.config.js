/** @type {import('expo/config').ExpoConfig} */
const config = {
  name: 'MadShopper',
  slug: 'madshopper',
  version: '0.1.0',
  orientation: 'portrait',
  icon: './assets/icon.png',
  userInterfaceStyle: 'automatic',
  scheme: 'madshopper',
  ios: {
    supportsTablet: true,
    bundleIdentifier: 'dk.madshopper.app',
    associatedDomains: ['applinks:madshopper.dk'],
  },
  android: {
    package: 'dk.madshopper.app',
    adaptiveIcon: {
      backgroundColor: '#1B5E20',
      foregroundImage: './assets/android-icon-foreground.png',
      backgroundImage: './assets/android-icon-background.png',
      monochromeImage: './assets/android-icon-monochrome.png',
    },
    intentFilters: [
      {
        action: 'VIEW',
        autoVerify: true,
        data: [{ scheme: 'https', host: 'madshopper.dk', pathPrefix: '/' }],
        category: ['BROWSABLE', 'DEFAULT'],
      },
    ],
    predictiveBackGestureEnabled: false,
  },
  web: {
    favicon: './assets/favicon.png',
  },
  plugins: ['expo-secure-store', 'expo-web-browser', 'expo-asset'],
  extra: {
    apiBaseUrl: process.env.EXPO_PUBLIC_API_BASE_URL || 'https://madshopper.dk',
    supabaseUrl: process.env.EXPO_PUBLIC_SUPABASE_URL || '',
    supabaseAnonKey: process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY || '',
    rpcSuffix: process.env.EXPO_PUBLIC_RPC_SUFFIX || '',
    googleClientId: process.env.EXPO_PUBLIC_GOOGLE_CLIENT_ID || '',
    flavor: process.env.EXPO_PUBLIC_FLAVOR || 'production',
  },
};

export default config;
