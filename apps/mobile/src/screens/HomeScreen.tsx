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
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { fetchHome } from '../api/listing';
import type { HomeSection, Product } from '../api/types';
import { applyClientFilters, FiltersBar, type FiltersValue } from '../components/FiltersBar';
import { ProductCard } from '../components/ProductCard';
import { useStoreCatalog, storesParam } from '../stores/StoreCatalogContext';
import { useTheme } from '../theme/ThemeContext';
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
  | { key: string; kind: 'savings'; message: string };

export function HomeScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { colors } = useTheme();
  const { selectedLabels, catalog, ready } = useStoreCatalog();
  const { height: windowHeight } = useWindowDimensions();
  const headerHeight = useHeaderHeight();
  const tabBarHeight = useBottomTabBarHeight();
  // Fast viewport-højde = FlatList kan scrolle (ikke vokse med indhold)
  const listHeight = Math.max(240, windowHeight - headerHeight - tabBarHeight);

  const [sections, setSections] = React.useState<HomeSection[]>([]);
  const [filters, setFilters] = React.useState<FiltersValue>({ sort: 'relevance' });
  const [savingsMsg, setSavingsMsg] = React.useState('Kommer snart');
  const [loading, setLoading] = React.useState(true);
  const [refreshing, setRefreshing] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const data = await fetchHome({
        stores: storesParam(selectedLabels, catalog),
      });
      if (!data.success) throw new Error(data.error || 'Fejl');
      setSections(data.sections || []);
      setSavingsMsg(data.personal_savings?.message || 'Kommer snart');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Kunne ikke hente forsiden');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [selectedLabels, catalog]);

  React.useEffect(() => {
    if (ready) void load(false);
  }, [ready, load]);

  const openProduct = (product: Product) => {
    navigation.navigate('ProductDetail', { product });
  };

  const rows = React.useMemo<HomeRow[]>(() => {
    const out: HomeRow[] = [
      { key: 'hero', kind: 'hero' },
      { key: 'savings', kind: 'savings', message: savingsMsg },
      { key: 'cats', kind: 'cats' },
      { key: 'filters', kind: 'filters' },
    ];
    if (error) out.push({ key: 'error', kind: 'error', message: error });
    for (const section of sections) {
      const products = applyClientFilters(section.products, filters).slice(0, 6);
      if (!products.length) continue;
      out.push({ key: `section-${section.key}`, kind: 'section', section, products });
    }
    return out;
  }, [sections, filters, error, savingsMsg]);

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
            return (
              <View
                style={[styles.stub, { backgroundColor: colors.surface, borderColor: colors.border }]}
              >
                <Text style={[styles.sectionTitle, { color: colors.text }]}>
                  Personlig besparelse
                </Text>
                <Text style={{ color: colors.textMuted, marginTop: 6 }}>{item.message}</Text>
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
                ) : section.href === '/Mejeri' ? (
                  <Pressable
                    onPress={() =>
                      navigation.navigate('Category', { slug: 'Mejeri', title: 'Køl' })
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
  stub: {
    margin: 16,
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
  },
  error: { padding: 16 },
});
