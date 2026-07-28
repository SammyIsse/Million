/** @type {import('expo/config').ExpoConfig} */
const config = {
  name: 'MadShopper',
  slug: 'madshopper',
  version: '0.1.0',
  orientation: 'portrait',
  icon: './assets/icon.png',
  userInterfaceStyle: 'automatic',
  scheme: 'madshopper',
  primaryColor: '#1B5E20',
  splash: {
    image: './assets/splash-icon.png',
    resizeMode: 'contain',
    backgroundColor: '#1B5E20',
  },
  ios: {
    supportsTablet: true,
    bundleIdentifier: 'dk.madshopper.app',
    associatedDomains: ['applinks:madshopper.dk'],
    infoPlist: {
      CFBundleAllowMixedLocalizations: true,
      NSUserTrackingUsageDescription:
        'MadShopper bruger ikke sporingsannoncer. Tilladelsen bruges kun hvis du senere aktiverer analytics.',
    },
    privacyManifests: {
      NSPrivacyAccessedAPITypes: [
        {
          NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryUserDefaults',
          NSPrivacyAccessedAPITypeReasons: ['CA92.1'],
        },
      ],
    },
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
  plugins: [
    'expo-secure-store',
    'expo-web-browser',
    'expo-asset',
    'expo-apple-authentication',
    [
      '@react-native-google-signin/google-signin',
      {
        iosUrlScheme: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID
          ? `com.googleusercontent.apps.${process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID.split('.')[0]}`
          : undefined,
      },
    ],
    // Google Sign-In's Swift pods (AppCheckCore/GoogleUtilities/RecaptchaInterop)
    // require modular headers, which only happens automatically with use_frameworks!.
    ['expo-build-properties', { ios: { useFrameworks: 'static' } }],
  ],
  extra: {
    apiBaseUrl: process.env.EXPO_PUBLIC_API_BASE_URL || 'https://madshopper.dk',
    supabaseUrl: process.env.EXPO_PUBLIC_SUPABASE_URL || '',
    supabaseAnonKey: process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY || '',
    rpcSuffix: process.env.EXPO_PUBLIC_RPC_SUFFIX || '',
    googleClientId: process.env.EXPO_PUBLIC_GOOGLE_CLIENT_ID || '',
    googleIosClientId: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID || '',
    googleAndroidClientId: process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID || '',
    flavor: process.env.EXPO_PUBLIC_FLAVOR || 'production',
    eas: {
      // Fra `eas init` (Cartspotter-organisationen), 2026-07-27
      projectId: '61fb2d3e-805e-4d2f-9c78-5e9705d28fd8',
    },
  },
};

export default config;
