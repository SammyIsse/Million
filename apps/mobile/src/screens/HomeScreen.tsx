import React from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';
import { useBottomTabBarHeight } from '@react-navigation/bottom-tabs';
import { useHeaderHeight } from '@react-navigation/elements';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { fetchHome } from '../api/listing';
import type { HomeSection, Product } from '../api/types';
import type { Recipe } from '../api/recipes';
import { useAuth } from '../auth/AuthContext';
import { applyClientFilters, FiltersBar, type FiltersValue } from '../components/FiltersBar';
import { ProductCard } from '../components/ProductCard';
import { RecipeCard } from '../components/RecipeCard';
import {
  emptySavings,
  fetchPersonalSavings,
  formatKr,
  monthLabel,
  type PersonalSavings,
} from '../savings/personalSavings';
import { useStoreCatalog, storesParam } from '../stores/StoreCatalogContext';
import { useTheme } from '../theme/ThemeContext';
import { recipesEnabled } from '../navigation/RootNavigator';
import type { RootStackParamList } from '../navigation/types';

const CATEGORY_LINKS: Array<{ label: string; slug: string }> = [
  { label: 'Ugens Tilbud', slug: 'sale' },
  { label: 'Køl', slug: 'Mejeri' },
  { label: 'Kød & Fisk', slug: 'Koed_og_fisk' },
  { label: 'Frugt & Grønt', slug: 'Frugt_og_groent' },
  { label: 'Brød & Kager', slug: 'Broed_og_kager' },
  { label: 'Frost', slug: 'Frost' },
  { label: 'Kolonial', slug: 'Kolonial' },
  { label: 'Drikkevarer', slug: 'Drikkevarer' },
  { label: 'Slik', slug: 'Slik' },
];

type HomeRow =
  | { key: string; kind: 'hero' }
  | { key: string; kind: 'cats' }
  | { key: string; kind: 'filters' }
  | { key: string; kind: 'error'; message: string }
  | { key: string; kind: 'section'; section: HomeSection; products: Product[] }
  | { key: string; kind: 'savings'; savings: PersonalSavings }
  | { key: string; kind: 'recipes'; recipes: Recipe[] };

export function HomeScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { colors } = useTheme();
  const { user } = useAuth();
  const { selectedLabels, catalog, ready } = useStoreCatalog();
  const { height: windowHeight } = useWindowDimensions();
  const headerHeight = useHeaderHeight();
  const tabBarHeight = useBottomTabBarHeight();
  const listHeight = Math.max(240, windowHeight - headerHeight - tabBarHeight);

  const [sections, setSections] = React.useState<HomeSection[]>([]);
  const [recipes, setRecipes] = React.useState<Recipe[]>([]);
  const [filters, setFilters] = React.useState<FiltersValue>({ sort: 'relevance' });
  const [savings, setSavings] = React.useState<PersonalSavings>(() => emptySavings(false));
  const [loading, setLoading] = React.useState(true);
  const [refreshing, setRefreshing] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const loadSavings = React.useCallback(async () => {
    if (!user) {
      setSavings(emptySavings(false));
      return;
    }
    try {
      const data = await fetchPersonalSavings();
      setSavings(data);
    } catch {
      setSavings(emptySavings(true));
    }
  }, [user]);

  const load = React.useCallback(
    async (isRefresh = false) => {
      if (isRefresh) setRefreshing(true);
      else setLoading(true);
      setError(null);
      try {
        const data = await fetchHome({
          stores: storesParam(selectedLabels, catalog),
        });
        if (!data.success) throw new Error(data.error || 'Fejl');
        setSections(data.sections || []);
        setRecipes(data.recipes || []);
        await loadSavings();
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Kunne ikke hente forsiden');
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [selectedLabels, catalog, loadSavings],
  );

  React.useEffect(() => {
    if (ready) void load(false);
  }, [ready, load]);

  useFocusEffect(
    React.useCallback(() => {
      void loadSavings();
    }, [loadSavings]),
  );

  const openProduct = (product: Product) => {
    navigation.navigate('ProductDetail', { product });
  };

  const rows = React.useMemo<HomeRow[]>(() => {
    const out: HomeRow[] = [
      { key: 'hero', kind: 'hero' },
      { key: 'savings', kind: 'savings', savings },
      { key: 'cats', kind: 'cats' },
      { key: 'filters', kind: 'filters' },
    ];
    if (error) out.push({ key: 'error', kind: 'error', message: error });
    for (const section of sections) {
      const products = applyClientFilters(section.products, filters).slice(0, 6);
      if (!products.length) continue;
      out.push({ key: `section-${section.key}`, kind: 'section', section, products });
    }
    if (recipes.length) out.push({ key: 'recipes', kind: 'recipes', recipes });
    return out;
  }, [sections, filters, error, savings, recipes]);

  if (!ready || (loading && !sections.length)) {
    return (
      <View style={[styles.center, { backgroundColor: colors.bg, height: listHeight }]}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  return (
    <View style={{ height: listHeight, backgroundColor: colors.bg }}>
      <FlatList
        style={{ height: listHeight }}
        data={rows}
        keyExtractor={(item) => item.key}
        contentContainerStyle={{ paddingBottom: 40 }}
        scrollEnabled
        bounces
        showsVerticalScrollIndicator
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => void load(true)} />
        }
        renderItem={({ item }) => {
          if (item.kind === 'hero') {
            return (
              <View style={styles.hero}>
                <Text style={[styles.brand, { color: colors.primary }]}>MadShopper</Text>
              </View>
            );
          }

          if (item.kind === 'cats') {
            return (
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                directionalLockEnabled
                style={styles.catsScroll}
                contentContainerStyle={styles.catsContent}
              >
                {CATEGORY_LINKS.map((c) => (
                  <Pressable
                    key={c.slug}
                    onPress={() => {
                      if (c.slug === 'sale') navigation.navigate('Sale');
                      else navigation.navigate('Category', { slug: c.slug, title: c.label });
                    }}
                    style={[
                      styles.catChip,
                      { backgroundColor: colors.surface, borderColor: colors.border },
                    ]}
                  >
                    <Text style={{ color: colors.text, fontWeight: '600' }}>{c.label}</Text>
                  </Pressable>
                ))}
              </ScrollView>
            );
          }

          if (item.kind === 'filters') {
            return <FiltersBar values={filters} onChange={setFilters} />;
          }

          if (item.kind === 'error') {
            return <Text style={[styles.error, { color: colors.sale }]}>{item.message}</Text>;
          }

          if (item.kind === 'savings') {
            const s = item.savings;
            if (!s.available) {
              return (
                <Pressable
                  onPress={() => navigation.navigate('Auth')}
                  style={styles.savingsBanner}
                >
                  <View style={styles.savingsContent}>
                    <Text style={styles.savingsLabel}>Personlig besparelse</Text>
                    <Text style={styles.savingsAmount}>Log ind for at tracke besparelse</Text>
                  </View>
                  <View style={styles.savingsBadge}>
                    <Text style={styles.savingsBadgeText}>Log ind</Text>
                  </View>
                </Pressable>
              );
            }
            return (
              <View style={styles.savingsBanner}>
                <View style={styles.savingsContent}>
                  <Text style={styles.savingsLabel}>Personlig besparelse</Text>
                  <Text style={styles.savingsAmount}>
                    Du har sparet {formatKr(s.amount)} kr denne måned
                  </Text>
                  {s.show_prev && s.prev_amount > 0 ? (
                    <Text style={styles.savingsPrev}>
                      I {monthLabel(s.prev_month_key)} sparede du {formatKr(s.prev_amount)} kr
                    </Text>
                  ) : null}
                </View>
                <View style={styles.savingsBadge}>
                  <Text style={styles.savingsBadgeText}>Top {s.top_pct}%</Text>
                </View>
              </View>
            );
          }

          if (item.kind === 'recipes') {
            return (
              <View style={styles.section}>
                <View style={styles.sectionHead}>
                  <Text style={[styles.sectionTitle, { color: colors.text }]}>Lækre opskrifter</Text>
                  <Pressable onPress={() => navigation.navigate('Tabs', { screen: 'Recipes' })}>
                    <Text style={{ color: colors.primary }}>Vis alle</Text>
                  </Pressable>
                </View>
                <View style={styles.grid}>
                  {item.recipes.slice(0, 10).map((recipe) => (
                    <View key={recipe.id} style={styles.gridItem}>
                      <RecipeCard
                        recipe={recipe}
                        onPress={(r) => navigation.navigate('RecipeDetail', { recipeId: r.id })}
                      />
                    </View>
                  ))}
                </View>
              </View>
            );
          }

          const { section, products } = item;
          return (
            <View style={styles.section}>
              <View style={styles.sectionHead}>
                <Text style={[styles.sectionTitle, { color: colors.text }]}>{section.title}</Text>
                {section.href === '/ugens_tilbud' ? (
                  <Pressable onPress={() => navigation.navigate('Sale')}>
                    <Text style={{ color: colors.primary }}>Vis alle</Text>
                  </Pressable>
                ) : section.href ? (
                  <Pressable
                    onPress={() =>
                      navigation.navigate('Category', {
                        slug: section.href!.replace(/^\//, ''),
                        title: section.title,
                      })
                    }
                  >
                    <Text style={{ color: colors.primary }}>Vis alle</Text>
                  </Pressable>
                ) : null}
              </View>
              <View style={styles.grid}>
                {products.map((product) => (
                  <View key={product.id} style={styles.gridItem}>
                    <ProductCard product={product} onPress={openProduct} />
                  </View>
                ))}
              </View>
            </View>
          );
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  center: { alignItems: 'center', justifyContent: 'center' },
  hero: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 4 },
  brand: { fontSize: 32, fontWeight: '800', letterSpacing: -0.5 },
  catsScroll: {
    maxHeight: 44,
    marginVertical: 8,
    flexGrow: 0,
  },
  catsContent: {
    paddingHorizontal: 12,
    alignItems: 'center',
  },
  catChip: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 20,
    borderWidth: 1,
    marginRight: 8,
  },
  section: { marginTop: 12 },
  sectionHead: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    marginBottom: 4,
  },
  sectionTitle: { fontSize: 18, fontWeight: '700' },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    paddingHorizontal: 4,
  },
  gridItem: {
    width: '50%',
    paddingHorizontal: 2,
  },
  savingsBanner: {
    marginHorizontal: 16,
    marginTop: 8,
    marginBottom: 4,
    padding: 16,
    borderRadius: 16,
    backgroundColor: '#059669',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  savingsContent: { flex: 1 },
  savingsLabel: {
    fontSize: 12,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.85)',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 4,
  },
  savingsAmount: {
    fontSize: 16,
    fontWeight: '800',
    color: '#fff',
    lineHeight: 22,
  },
  savingsPrev: {
    marginTop: 6,
    fontSize: 13,
    fontWeight: '600',
    color: 'rgba(255,255,255,0.9)',
  },
  savingsBadge: {
    backgroundColor: '#fff',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
  },
  savingsBadgeText: {
    color: '#059669',
    fontWeight: '800',
    fontSize: 13,
  },
  error: { padding: 16 },
});
