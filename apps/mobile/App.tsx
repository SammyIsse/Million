import React from 'react';
import { View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeProvider, useTheme } from './src/theme/ThemeContext';
import { StoreCatalogProvider } from './src/stores/StoreCatalogContext';
import { CartProvider } from './src/cart/CartContext';
import { AuthProvider } from './src/auth/AuthContext';
import { SharedCartProvider } from './src/cart/SharedCartContext';
import { RootNavigator } from './src/navigation/RootNavigator';

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
