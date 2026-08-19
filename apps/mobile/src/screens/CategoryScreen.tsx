import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { fetchCategory, fetchSale } from '../api/listing';
import type { Product } from '../api/types';
import { FiltersBar, type FiltersValue } from '../components/FiltersBar';
import { ProductCard } from '../components/ProductCard';
import { StackScreenBody } from '../components/ScreenBody';
import { useStoreCatalog, storesParam } from '../stores/StoreCatalogContext';
import { useTheme } from '../theme/ThemeContext';
import { Pager } from '../components/Pager';
import type { RootStackParamList } from '../navigation/types';

type CategoryProps = NativeStackScreenProps<RootStackParamList, 'Category'>;
type SaleProps = NativeStackScreenProps<RootStackParamList, 'Sale'>;

function ListingBody({
  products,
  page,
  totalPages,
  loading,
  subcategories,
  currentSub,
  onPage,
  onSub,
  onProduct,
  filters,
  onFiltersChange,
  error,
  onRetry,
}: {
  products: Product[];
  page: number;
  totalPages: number;
  loading: boolean;
  subcategories: string[];
  currentSub: string | null;
  onPage: (p: number) => void;
  onSub: (s: string | null) => void;
  onProduct: (p: Product) => void;
  filters: FiltersValue;
  onFiltersChange: (next: FiltersValue) => void;
  error?: string | null;
  onRetry?: () => void;
}) {
  const { colors } = useTheme();

  return (
    <StackScreenBody style={{ backgroundColor: colors.bg }}>
      <FiltersBar values={filters} onChange={onFiltersChange} showSubcats={subcategories.length > 0} />
      {subcategories.length > 0 ? (
        <FlatList
          horizontal
          data={[{ label: 'Alle', value: null as string | null }, ...subcategories.map((s) => ({ label: s, value: s }))]}
          keyExtractor={(i) => i.label}
          style={{ maxHeight: 48, marginVertical: 8, flexGrow: 0 }}
          contentContainerStyle={{ paddingHorizontal: 12 }}
          renderItem={({ item }) => {
            const active = currentSub === item.value || (!currentSub && item.value === null);
            return (
              <Pressable
                onPress={() => onSub(item.value)}
                style={[
                  styles.chip,
                  {
                    backgroundColor: active ? colors.primary : colors.surface,
                    borderColor: colors.border,
                  },
                ]}
              >
                <Text style={{ color: active ? '#fff' : colors.text, fontWeight: '600' }}>
                  {item.label}
                </Text>
              </Pressable>
            );
          }}
        />
      ) : null}
      {error && !loading ? (
        <View style={{ padding: 24, alignItems: 'center', gap: 10 }}>
          <Text style={{ color: colors.text, fontWeight: '600', textAlign: 'center' }}>
            {error}
          </Text>
          {onRetry ? (
            <Pressable
              onPress={onRetry}
              style={{
                borderWidth: 1,
                borderColor: colors.border,
                borderRadius: 8,
                paddingHorizontal: 14,
                paddingVertical: 8,
              }}
            >
              <Text style={{ color: colors.primary, fontWeight: '600' }}>Prøv igen</Text>
            </Pressable>
          ) : null}
        </View>
      ) : loading && !products.length ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.primary} />
      ) : (
        <FlatList
          style={{ flex: 1 }}
          data={products}
          keyExtractor={(p) => p.id}
          numColumns={2}
          columnWrapperStyle={{ paddingHorizontal: 2 }}
          contentContainerStyle={{ padding: 4 }}
          showsVerticalScrollIndicator
          renderItem={({ item }) => <ProductCard product={item} onPress={onProduct} />}
          ListEmptyComponent={
            // Uden denne var 0 resultater en HELT blank skærm - brugeren kunne
            // ikke se forskel på "ingen varer matcher" og "noget gik i stykker".
            // Webben har haft hjælpeteksten hele tiden
            // (templates/partials/product_grid.html).
            !loading ? (
              <View style={{ padding: 32, alignItems: 'center', gap: 6 }}>
                <Text style={{ color: colors.text, fontWeight: '600', textAlign: 'center' }}>
                  Ingen varer matcher dine valg.
                </Text>
                <Text style={{ color: colors.textMuted, textAlign: 'center' }}>
                  Prøv at fjerne et filter eller vælge flere butikker.
                </Text>
              </View>
            ) : null
          }
          ListFooterComponent={<Pager page={page} totalPages={totalPages} onPage={onPage} />}
        />
      )}
    </StackScreenBody>
  );
}

export function CategoryScreen({ route, navigation }: CategoryProps) {
  const { slug } = route.params;
  const { queryLabels, catalog, ready } = useStoreCatalog();
  const [products, setProducts] = useState<Product[]>([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [subs, setSubs] = useState<string[]>([]);
  const [sub, setSub] = useState<string | null>(null);
  const [filters, setFilters] = useState<FiltersValue>({ sort: 'relevance' });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);


  // Butiksskift skal ramme side 1. Uden det kunne man staa paa side 5 med 14
  // butikker, skaere ned til 2 - og faa en tom skaerm, fordi det nye
  // resultatsaet kun har 2 sider. SearchScreen har haft nulstillingen hele
  // tiden, og webben goer det samme (updateDynamicStoreContent(resetPage=true)).
  useEffect(() => {
    setPage(1);
  }, [queryLabels, catalog]);
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchCategory(slug, {
        page,
        subcategory: sub || undefined,
        stores: storesParam(queryLabels, catalog),
        ...filters,
      });
      setProducts(data.products || []);
      setTotalPages(data.total_pages || 1);
      setSubs(data.available_subcategories || []);
    } catch (e) {
      // Uden denne gren var offline lig med en helt blank skærm: try/finally
      // uden catch, og `void load()` gjorde afvisningen til en unhandled
      // rejection. client.ts har pæne fejltekster - de nåede bare aldrig frem.
      setError(e instanceof Error ? e.message : 'Kunne ikke hente varerne.');
      setProducts([]);
    } finally {
      setLoading(false);
    }
  }, [slug, page, sub, queryLabels, catalog, filters]);

  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);

  return (
    <ListingBody
      error={error}
      onRetry={() => void load()}
      products={products}
      page={page}
      totalPages={totalPages}
      loading={loading}
      subcategories={subs}
      currentSub={sub}
      onPage={setPage}
      onSub={(s) => {
        setPage(1);
        setSub(s);
      }}
      onProduct={(p) => navigation.navigate('ProductDetail', { product: p })}
      filters={filters}
      onFiltersChange={(next) => {
        setPage(1);
        setFilters(next);
      }}
    />
  );
}

export function SaleScreen({ navigation }: SaleProps) {
  const { queryLabels, catalog, ready } = useStoreCatalog();
  const [products, setProducts] = useState<Product[]>([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [filters, setFilters] = useState<FiltersValue>({ sort: 'relevance' });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);


  // Butiksskift skal ramme side 1. Uden det kunne man staa paa side 5 med 14
  // butikker, skaere ned til 2 - og faa en tom skaerm, fordi det nye
  // resultatsaet kun har 2 sider. SearchScreen har haft nulstillingen hele
  // tiden, og webben goer det samme (updateDynamicStoreContent(resetPage=true)).
  useEffect(() => {
    setPage(1);
  }, [queryLabels, catalog]);
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchSale({
        page,
        stores: storesParam(queryLabels, catalog),
        ...filters,
      });
      setProducts(data.products || []);
      setTotalPages(data.total_pages || 1);
    } catch (e) {
      // Se CategoryScreen: uden catch var offline en blank skærm.
      setError(e instanceof Error ? e.message : 'Kunne ikke hente tilbuddene.');
      setProducts([]);
    } finally {
      setLoading(false);
    }
  }, [page, queryLabels, catalog, filters]);

  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);

  return (
    <ListingBody
      error={error}
      onRetry={() => void load()}
      products={products}
      page={page}
      totalPages={totalPages}
      loading={loading}
      subcategories={[]}
      currentSub={null}
      onPage={setPage}
      onSub={() => {}}
      onProduct={(p) => navigation.navigate('ProductDetail', { product: p })}
      filters={filters}
      onFiltersChange={(next) => {
        setPage(1);
        setFilters(next);
      }}
    />
  );
}

const styles = StyleSheet.create({
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 16,
    borderWidth: 1,
    marginRight: 8,
  },
  pager: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
  },
  pageBtn: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8 },
});
