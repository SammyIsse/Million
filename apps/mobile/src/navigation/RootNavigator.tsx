import React from 'react';
import { Pressable, Text } from 'react-native';
import {
  NavigationContainer,
  DarkTheme,
  DefaultTheme,
  type LinkingOptions,
} from '@react-navigation/native';
import * as Linking from 'expo-linking';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { useCart } from '../cart/CartContext';
import { HomeScreen } from '../screens/HomeScreen';
import { CategoryScreen, SaleScreen } from '../screens/CategoryScreen';
import { SearchScreen } from '../screens/SearchScreen';
import { RecipesScreen } from '../screens/RecipesScreen';
import { CartScreen } from '../screens/CartScreen';
import { SettingsScreen } from '../screens/SettingsScreen';
import { ProductDetailScreen } from '../screens/ProductDetailScreen';
import { RecipeDetailScreen } from '../screens/RecipeDetailScreen';
import { ScoScreen } from '../screens/ScoScreen';
import { RouteScreen } from '../screens/RouteScreen';
import { AuthScreen } from '../screens/AuthScreen';
import { FeedbackScreen } from '../screens/FeedbackScreen';
import { LegalScreen } from '../screens/LegalScreen';
import { isRecoveryUrl } from '../auth/recoveryLink';
import { recipesEnabled } from '../config/env';
import type { RootStackParamList, TabParamList } from './types';

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tabs = createBottomTabNavigator<TabParamList>();

/**
 * Opskrift-gaten (`recipesEnabled`, se config/env.ts) dækker fanen og
 * `RecipeDetail`-skærmen i stakken: indgangen skal væk i produktion, ikke bare
 * indholdet. Web-headeren gør det samme med {% if rpc_suffix %} omkring
 * opskrift-ikonet (templates/base.html).
 *
 * Forsidens opskriftssektion er IKKE gated her - den vises i alle miljøer som
 * ikke-klikbar teaser, ligesom webforsiden (se HomeScreen's recipesClickable +
 * app.py::api_home's recipes_clickable). Derfor må /api/home også svare med
 * puljen i produktion; det er kun klikbarheden der skifter.
 */

/**
 * BEVIDST MEGET SNÆVER deep link-konfiguration.
 *
 * Appen har med vilje ikke haft nogen `linking` på NavigationContainer: uden
 * den kan et link udefra ikke pege på en vilkårlig skærm i stakken - og det
 * er netop dét, der beskytter gatede skærme (opskrifter) mod at blive åbnet
 * i et produktions-build. `filter` slipper derfor KUN recovery-links igennem
 * (parseRecoveryLink kræver eksplicit `type=recovery` + brugbare tokens), og
 * `getStateFromPath` returnerer én fast tilstand: Tabs + Auth. Der findes
 * ingen sti-mapping, så ingen URL kan navigere nogen andre steder hen.
 *
 * Invitations-links (`?liste=`) håndteres fortsat af SharedCartContext' egen
 * Linking-lytter og har ingen navigationsvirkning.
 */
const recoveryLinking: LinkingOptions<RootStackParamList> = {
  prefixes: [Linking.createURL('/'), 'madshopper://'],
  filter: (url) => isRecoveryUrl(url),
  getStateFromPath: () => ({
    routes: [{ name: 'Tabs' as const }, { name: 'Auth' as const }],
  }),
  // Ingen `config`: der er ingen sti→skærm-mapping at misbruge.
};

function CartHeaderButton({ onPress }: { onPress: () => void }) {
  const { colors } = useTheme();
  const { count } = useCart();
  return (
    <Pressable onPress={onPress} style={{ marginRight: 16, paddingVertical: 4 }}>
      <Text style={{ color: colors.primary, fontWeight: '600' }}>
        Kurv{count > 0 ? ` (${count})` : ''}
      </Text>
    </Pressable>
  );
}

type TabIconName = React.ComponentProps<typeof Ionicons>['name'];

function tabIcon(focused: boolean, active: TabIconName, inactive: TabIconName): TabIconName {
  return focused ? active : inactive;
}

function MainTabs() {
  const { colors } = useTheme();

  return (
    <Tabs.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.text,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
          paddingTop: 4,
        },
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.tabInactive,
        tabBarLabelStyle: { fontSize: 11, fontWeight: '600', marginBottom: 2 },
        sceneStyle: { flex: 1, minHeight: 0 },
      }}
    >
      <Tabs.Screen
        name="Home"
        component={HomeScreen}
        options={({ navigation }) => ({
          title: 'MadShopper',
          tabBarLabel: 'Hjem',
          tabBarIcon: ({ color, size, focused }) => (
            <Ionicons
              name={tabIcon(focused, 'home', 'home-outline')}
              size={size}
              color={color}
            />
          ),
          headerRight: () => (
            <CartHeaderButton onPress={() => navigation.getParent()?.navigate('Cart')} />
          ),
        })}
      />
      <Tabs.Screen
        name="Search"
        component={SearchScreen}
        options={({ navigation }) => ({
          title: 'Søg',
          tabBarLabel: 'Søg',
          tabBarIcon: ({ color, size, focused }) => (
            <Ionicons
              name={tabIcon(focused, 'search', 'search-outline')}
              size={size}
              color={color}
            />
          ),
          headerRight: () => (
            <CartHeaderButton onPress={() => navigation.getParent()?.navigate('Cart')} />
          ),
        })}
      />
      {recipesEnabled && (
        <Tabs.Screen
          name="Recipes"
          component={RecipesScreen}
          options={({ navigation }) => ({
            title: 'Opskrifter',
            tabBarLabel: 'Opskrifter',
            tabBarIcon: ({ color, size, focused }) => (
              <Ionicons
                name={tabIcon(focused, 'restaurant', 'restaurant-outline')}
                size={size}
                color={color}
              />
            ),
            headerRight: () => (
              <CartHeaderButton onPress={() => navigation.getParent()?.navigate('Cart')} />
            ),
          })}
        />
      )}
      <Tabs.Screen
        name="Settings"
        component={SettingsScreen}
        options={({ navigation }) => ({
          title: 'Indstillinger',
          tabBarLabel: 'Indstillinger',
          tabBarIcon: ({ color, size, focused }) => (
            <Ionicons
              name={tabIcon(focused, 'settings', 'settings-outline')}
              size={size}
              color={color}
            />
          ),
          headerRight: () => (
            <CartHeaderButton onPress={() => navigation.getParent()?.navigate('Cart')} />
          ),
        })}
      />
    </Tabs.Navigator>
  );
}

export function RootNavigator() {
  const { colors, isDark } = useTheme();
  const navTheme = {
    ...(isDark ? DarkTheme : DefaultTheme),
    colors: {
      ...(isDark ? DarkTheme.colors : DefaultTheme.colors),
      background: colors.bg,
      card: colors.surface,
      text: colors.text,
      border: colors.border,
      primary: colors.primary,
    },
  };

  return (
    <NavigationContainer theme={navTheme} linking={recoveryLinking}>
      <Stack.Navigator
        screenOptions={{
          headerStyle: { backgroundColor: colors.surface },
          headerTintColor: colors.text,
          contentStyle: { flex: 1, backgroundColor: colors.bg },
        }}
      >
        <Stack.Screen name="Tabs" component={MainTabs} options={{ headerShown: false }} />
        <Stack.Screen
          name="Category"
          component={CategoryScreen}
          options={({ route }) => ({ title: route.params.title })}
        />
        <Stack.Screen name="Sale" component={SaleScreen} options={{ title: 'Ugens Tilbud' }} />
        <Stack.Screen name="Cart" component={CartScreen} options={{ title: 'Indkøbsliste' }} />
        <Stack.Screen
          name="ProductDetail"
          component={ProductDetailScreen}
          options={{ title: 'Produkt' }}
        />
        {/* Samme gate som opskrift-fanen: uden den ville skærmen findes i
            stakken i produktion, hvor featuren ikke er åben. */}
        {recipesEnabled && (
          <Stack.Screen
            name="RecipeDetail"
            component={RecipeDetailScreen}
            options={{ title: 'Opskrift' }}
          />
        )}
        <Stack.Screen name="Sco" component={ScoScreen} options={{ title: 'Find billigste' }} />
        <Stack.Screen name="Route" component={RouteScreen} options={{ title: 'Butiksrute' }} />
        <Stack.Screen
          name="Auth"
          component={AuthScreen}
          options={{ title: 'Konto', presentation: 'modal' }}
        />
        <Stack.Screen name="Feedback" component={FeedbackScreen} options={{ title: 'Feedback' }} />
        <Stack.Screen
          name="Legal"
          component={LegalScreen}
          options={({ route }) => ({
            title:
              route.params.kind === 'terms'
                ? 'Vilkår'
                : route.params.kind === 'privacy'
                  ? 'Privatliv'
                  : 'Om os',
          })}
        />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
