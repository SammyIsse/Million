import React from 'react';
import { LogBox, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeProvider, useTheme } from './src/theme/ThemeContext';
import { StoreCatalogProvider } from './src/stores/StoreCatalogContext';
import { CartProvider } from './src/cart/CartContext';
import { AuthProvider } from './src/auth/AuthContext';
import { SharedCartProvider } from './src/cart/SharedCartContext';
import { RootNavigator } from './src/navigation/RootNavigator';

// supabase-js's GoTrueClient logs this via console.error on every failed
// refresh tick even though it explicitly documents it as expected/transient
// (no session to refresh yet, brief network hiccup, ...) - it isn't actionable.
// getValueWithKeyAsync fails the same way on every cold start in this unsigned
// simulator build specifically: without an Apple Team ID (docs/env-setup.md §5)
// there's no keychain-access-groups entitlement, so SecItem lookups error out
// (errSecMissingEntitlement) even though the code path is correct.
LogBox.ignoreLogs([
  'Auto refresh tick failed with error',
  "Calling the 'getValueWithKeyAsync' function has failed",
]);

function AppShell() {
  const { isDark } = useTheme();
  return (
    <>
      <StatusBar style={isDark ? 'light' : 'dark'} />
      <RootNavigator />
    </>
  );
}

export default function App() {
  return (
    <View style={{ flex: 1 }}>
      <SafeAreaProvider>
        <ThemeProvider>
          <StoreCatalogProvider>
            <CartProvider>
              <AuthProvider>
                <SharedCartProvider>
                  <AppShell />
                </SharedCartProvider>
              </AuthProvider>
            </CartProvider>
          </StoreCatalogProvider>
        </ThemeProvider>
      </SafeAreaProvider>
    </View>
  );
}
