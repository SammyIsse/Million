// Compliance-audit 19-08-2026 (GDPR-029): NSAllowsLocalNetworking er kun
// nødvendig for at ramme en lokal Flask-server (http://localhost:5001 eller
// http://<mac-lan-ip>:5001) under udvikling - se docs/env-setup.md. Et
// produktions-build peger altid på https://madshopper.dk og har ingen brug
// for undtagelsen, som ellers unødigt svækker App Transport Security i den
// udgave, der reelt havner i App Store.
const IS_PRODUCTION_BUILD_FLAVOR = (process.env.EXPO_PUBLIC_FLAVOR || 'production') === 'production';

// Sikkerhedsnet (18-09-2026): et Xcode Archive (Release-konfiguration) skal
// ALTID pege på produktion, uanset om en lokal .env/.env.production-fil eller
// en efterladt shell-variabel (EXPO_PUBLIC_*) tilfældigvis peger på staging.
// $CONFIGURATION kommer direkte fra Xcodes build-environment (sat af
// react-native-xcode.sh -> export:embed) og kan ikke "efterlades" ved et
// uheld sådan som en .env-fil kan. Kun EAS cloud-builds (som ikke sætter
// CONFIGURATION) og lokal `expo start` er upåvirket af dette.
const IS_XCODE_RELEASE_BUILD = process.env.CONFIGURATION === 'Release';
const IS_PRODUCTION_BUILD = IS_XCODE_RELEASE_BUILD || IS_PRODUCTION_BUILD_FLAVOR;
const PROD_DEFAULTS = {
  apiBaseUrl: 'https://madshopper.dk',
  rpcSuffix: '',
  flavor: 'production',
};
function prodSafe(envVar, key) {
  if (IS_XCODE_RELEASE_BUILD) return PROD_DEFAULTS[key];
  return envVar;
}

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
      // Appen bruger ingen egen kryptering ud over standard HTTPS/TLS -
      // undtaget fra USA's eksportregler, sa vi undgar det interaktive
      // spørgsmål ved hver build.
      ITSAppUsesNonExemptEncryption: false,
      // Bevidst INGEN NSUserTrackingUsageDescription: appen kalder aldrig ATT
      // og svarer "no tracking" i App Privacy. En tilladelsestekst vi ikke
      // bruger, ville modsige den erklæring over for review. Tilføj den igen
      // samtidig med at ATT faktisk kaldes, hvis analytics kommer på.
      NSAppTransportSecurity: {
        // Tillader KUN usikker http:// mod loopback/private IP'er/.local -
        // ikke mod internettet generelt (det ville kræve
        // NSAllowsArbitraryLoads, en meget bredere svækkelse). Nødvendig for
        // EXPO_PUBLIC_API_BASE_URL=http://localhost:5001 (simulator) eller
        // http://<mac-lan-ip>:5001 (telefon), se apps/mobile/.env.example -
        // uden denne fejler alle API-kald mod lokal Flask stille (fetch
        // afvises af ATS før den overhovedet rammer netværket).
        //
        // KUN i ikke-produktionsbuilds (compliance-audit 19-08-2026,
        // GDPR-029): et rigtigt produktions-build peger altid på
        // https://madshopper.dk og har ingen brug for undtagelsen - den
        // fulgte tidligere ubetinget med i App Store-buildet.
        ...(IS_PRODUCTION_BUILD ? {} : { NSAllowsLocalNetworking: true }),
      },
    },
    // NSPrivacyAccessedAPITypes skal spejle ALLE kategorier, som det
    // GENERTEDE ios/MadShopper/PrivacyInfo.xcprivacy faktisk erklærer -
    // ikke kun én af dem (compliance-audit 19-08-2026, GDPR-029). /ios er
    // gitignoreret, så mappen (og manifestet) regenereres ved næste
    // `expo prebuild` fra PRÆCIS denne liste - stod kun UserDefaults her,
    // ville et fremtidigt build ende med et fattigere manifest end det,
    // React Native/Expo/Google-pods'ene reelt kræver.
    privacyManifests: {
      NSPrivacyAccessedAPITypes: [
        {
          NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryUserDefaults',
          NSPrivacyAccessedAPITypeReasons: ['CA92.1', 'C56D.1'],
        },
        {
          NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryFileTimestamp',
          NSPrivacyAccessedAPITypeReasons: ['C617.1', '0A2A.1', '3B52.1'],
        },
        {
          NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategoryDiskSpace',
          NSPrivacyAccessedAPITypeReasons: ['E174.1', '85F4.1'],
        },
        {
          NSPrivacyAccessedAPIType: 'NSPrivacyAccessedAPICategorySystemBootTime',
          NSPrivacyAccessedAPITypeReasons: ['35F9.1'],
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
    // faceIDPermission: false fjerner NSFaceIDUsageDescription helt fra
    // Info.plist (compliance-audit 19-08-2026, GDPR-029). Ingen SecureStore-
    // kald i src/ bruger requireAuthentication, og der er ingen
    // LocalAuthentication-import noget sted - appen bruger aldrig Face ID,
    // og en erklæret-men-ubrugt tilladelse (tidligere Expos engelske
    // standardtekst, i en ellers dansk app) modsagde både LegalScreen.tsx's
    // opremsning af tilladelser og store/review-notes.md.
    ['expo-secure-store', { faceIDPermission: false }],
    'expo-web-browser',
    'expo-asset',
    'expo-apple-authentication',
    [
      '@react-native-google-signin/google-signin',
      {
        // Hardkodet i stedet for udledt af EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID:
        // eas-cli's lokale "anvend config-plugins"-tjek (kører før upload til
        // cloud-build) mister build-profilens env-variabler i det trin, selvom
        // de er korrekt indlæst til selve `expo config` et øjeblik forinden.
        // Værdien er projektets faste Google iOS OAuth-klient-ID (offentligt,
        // ikke en hemmelighed) og er identisk på tværs af flavors - se .env.
        iosUrlScheme: 'com.googleusercontent.apps.683267660851-6ah9du0ig9fs3a0rcrbp72hu6t7j0hr4',
      },
    ],
    // Google Sign-In's Swift pods (AppCheckCore/GoogleUtilities/RecaptchaInterop)
    // require modular headers, which only happens automatically with use_frameworks!.
    ['expo-build-properties', { ios: { useFrameworks: 'static' } }],
  ],
  extra: {
    apiBaseUrl: prodSafe(process.env.EXPO_PUBLIC_API_BASE_URL, 'apiBaseUrl') || 'https://madshopper.dk',
    supabaseUrl: process.env.EXPO_PUBLIC_SUPABASE_URL || '',
    supabaseAnonKey: process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY || '',
    rpcSuffix: prodSafe(process.env.EXPO_PUBLIC_RPC_SUFFIX, 'rpcSuffix') || '',
    googleClientId: process.env.EXPO_PUBLIC_GOOGLE_CLIENT_ID || '',
    googleIosClientId: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID || '',
    googleAndroidClientId: process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID || '',
    flavor: prodSafe(process.env.EXPO_PUBLIC_FLAVOR, 'flavor') || 'production',
    eas: {
      // Fra `eas init` (Cartspotter-organisationen), 2026-07-27
      projectId: '61fb2d3e-805e-4d2f-9c78-5e9705d28fd8',
    },
  },
};

export default config;
