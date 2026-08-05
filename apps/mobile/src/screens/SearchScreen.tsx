import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { fetchAutocomplete, fetchSearch } from '../api/listing';
import type { Product } from '../api/types';
import { FiltersBar, type FiltersValue } from '../components/FiltersBar';
import { ProductCard } from '../components/ProductCard';
import { TabScreenBody } from '../components/ScreenBody';
import { useStoreCatalog, storesParam } from '../stores/StoreCatalogContext';
import { useTheme } from '../theme/ThemeContext';
import type { RootStackParamList } from '../navigation/types';

export function SearchScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { colors } = useTheme();
  const { selectedLabels, catalog } = useStoreCatalog();
  const [q, setQ] = useState('');
  const [suggestions, setSuggestions] = useState<
    Array<{ name: string; brand: string; price: number }>
  >([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [filters, setFilters] = useState<FiltersValue>({ sort: 'relevance' });

  // Delt mellem de to debounce-effects nedenfor, så søge-kaldet kan annullere
  // en ventende/igangværende autocomplete FØR det selv sendes af sted - uden
  // det kan begge ramme samme Cloudflare Workers-isolate næsten samtidig, som
  // ikke tillader overlappende request-tasks (samme fejlklasse som blev
  // rettet for web i static/js/script.js, closeAutocomplete()).
  const acTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const acControllerRef = useRef<AbortController | null>(null);

  function cancelAutocomplete() {
    if (acTimeoutRef.current) {
      clearTimeout(acTimeoutRef.current);
      acTimeoutRef.current = null;
    }
    if (acControllerRef.current) {
      acControllerRef.current.abort();
      acControllerRef.current = null;
    }
  }

  // Ny søgning nulstiller advanced filters (web-paritet).
  useEffect(() => {
    setFilters({ sort: 'relevance' });
  }, [q]);

  // Autocomplete debounce 200 ms
  useEffect(() => {
    if (q.trim().length < 2) {
      setSuggestions([]);
      cancelAutocomplete();
      return;
    }
    acTimeoutRef.current = setTimeout(() => {
      const controller = new AbortController();
      acControllerRef.current = controller;
      void fetchAutocomplete(q.trim(), storesParam(selectedLabels, catalog), controller)
        .then((r) => setSuggestions(r.suggestions || []))
        .catch(() => setSuggestions([]));
    }, 200);
    return () => {
      if (acTimeoutRef.current) clearTimeout(acTimeoutRef.current);
    };
  }, [q, selectedLabels, catalog]);

  // Search debounce 500 ms
  useEffect(() => {
    const query = q.trim();
    if (!query) {
      setProducts([]);
      setTotal(0);
      return;
    }
    const t = setTimeout(() => {
      cancelAutocomplete();
      setLoading(true);
      void fetchSearch({
        q: query,
        page: 1,
        stores: storesParam(selectedLabels, catalog),
        ...filters,
      })
        .then((r) => {
          setProducts(r.products || []);
          setTotal(r.total ?? 0);
        })
        .catch(() => setProducts([]))
        .finally(() => setLoading(false));
    }, 500);
    return () => clearTimeout(t);
  }, [q, selectedLabels, catalog, filters]);

  return (
    <TabScreenBody style={{ backgroundColor: colors.bg }}>
      <TextInput
        value={q}
        onChangeText={setQ}
        placeholder="Søg produkter…"
        placeholderTextColor={colors.textMuted}
        autoFocus
        style={[
          styles.input,
          { backgroundColor: colors.surface, color: colors.text, borderColor: colors.border },
        ]}
      />
      {q.trim().length >= 2 && suggestions.length > 0 && !products.length ? (
        <View style={{ paddingHorizontal: 12 }}>
          <Pressable onPress={() => setQ(q.trim())}>
            <Text style={{ color: colors.primary, paddingVertical: 8 }}>
              Søg efter {q.trim()}
            </Text>
          </Pressable>
          {suggestions.map((s) => (
            <Pressable key={s.name} onPress={() => setQ(s.name)} style={styles.sug}>
              <Text style={{ color: colors.text }}>{s.name}</Text>
              <Text style={{ color: colors.textMuted }}>{s.price.toFixed(2)} kr</Text>
            </Pressable>
          ))}
        </View>
      ) : null}
      {loading ? <ActivityIndicator color={colors.primary} style={{ marginTop: 20 }} /> : null}
      {q.trim().length > 0 && products.length > 0 ? (
        <FiltersBar values={filters} onChange={setFilters} />
      ) : null}
      {total > 0 ? (
        <Text style={[styles.meta, { color: colors.textMuted }]}>{total} resultater</Text>
      ) : null}
      <FlatList
        style={{ flex: 1 }}
        data={products}
        keyExtractor={(p) => p.id}
        numColumns={2}
        columnWrapperStyle={{ paddingHorizontal: 2 }}
        contentContainerStyle={{ padding: 4 }}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator
        renderItem={({ item }) => (
          <ProductCard
            product={item}
            onPress={(p) => navigation.navigate('ProductDetail', { product: p })}
          />
        )}
      />
    </TabScreenBody>
  );
}

const styles = StyleSheet.create({
  input: {
    margin: 12,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
  },
  sug: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#ccc',
  },
  meta: { paddingHorizontal: 16, marginBottom: 4 },
});
