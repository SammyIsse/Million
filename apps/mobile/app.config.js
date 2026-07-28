/** @type {import('expo/config').ExpoConfig} */
const config = {
  name: 'MadShopper',
  slug: 'madshopper',
  version: '1.0.0',
  orientation: 'portrait',
  icon: './assets/icon.png',
  userInterfaceStyle: 'automatic',
  scheme: 'madshopper',
  // Brandgrøn = samme #059669 som favicon/app-ikonet (scripts/build-icons.py).
  // Appens egne UI-grønne toner ligger i src/theme/colors.ts.
  primaryColor: '#059669',
  splash: {
    image: './assets/splash-icon.png',
    resizeMode: 'contain',
    backgroundColor: '#059669',
  },
  ios: {
    // Portrait-first iPhone-app. `true` ville kræve iPad-screenshots i App Store
    // Connect og gøre iPad til en review-flade vi ikke tester på.
    supportsTablet: false,
    bundleIdentifier: 'dk.madshopper.app',
    associatedDomains: ['applinks:madshopper.dk'],
    infoPlist: {
      CFBundleAllowMixedLocalizations: true,
      // Bevidst INGEN NSUserTrackingUsageDescription: appen kalder aldrig ATT
      // og svarer "no tracking" i App Privacy. En tilladelsestekst vi ikke
      // bruger, ville modsige den erklæring over for review. Tilføj den igen
      // samtidig med at ATT faktisk kaldes, hvis analytics kommer på.
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
      backgroundColor: '#059669',
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
