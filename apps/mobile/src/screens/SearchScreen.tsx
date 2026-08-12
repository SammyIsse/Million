import React, { useCallback, useEffect, useRef, useState } from 'react';
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
  // Kun opdateret 500 ms efter brugeren er holdt op med at skrive - selve
  // søgningen (inkl. sideskift) afhænger af DENNE, ikke af q direkte, så et
  // sideskift ikke også skal vente 500 ms.
  const [committedQuery, setCommittedQuery] = useState('');
  const [suggestions, setSuggestions] = useState<
    Array<{ name: string; brand: string; price: number }>
  >([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [filters, setFilters] = useState<FiltersValue>({ sort: 'relevance' });

  // Delt mellem de to debounce-effects nedenfor, så søge-kaldet kan annullere
  // en ventende/igangværende autocomplete FØR det selv sendes af sted - uden
  // det kan begge ramme samme Cloudflare Workers-isolate næsten samtidig, som
  // ikke tillader overlappende request-tasks (samme fejlklasse som blev
  // rettet for web i static/js/script.js, closeAutocomplete()).
  const acTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const acControllerRef = useRef<AbortController | null>(null);
  // Aktuel søgnings AbortController - annulleres ved næste søgning, så et
  // langsomt, forældet svar ikke kan overskrive et nyere (fx skriver
  // brugeren "mælk" og retter hurtigt til "mælkebøtte": to kald i luften,
  // uden dette vandt det langsomste, uanset hvilket der var nyest).
  const searchControllerRef = useRef<AbortController | null>(null);

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

  // Skriv-debounce 500 ms: opdaterer KUN committedQuery og nulstiller til
  // side 1. Selve hentningen sker i effekten nedenfor, som også dækker
  // sideskift - et sideskift skal ikke vente 500 ms som en tastetryk-søgning.
  useEffect(() => {
    const query = q.trim();
    if (!query) {
      setCommittedQuery('');
      setProducts([]);
      setTotal(0);
      setTotalPages(1);
      setError(null);
      return;
    }
    const t = setTimeout(() => {
      cancelAutocomplete();
      setPage(1);
      setCommittedQuery(query);
    }, 500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  // Filterskift/butiksskift skal ramme side 1, ikke blive på en side der
  // måske ikke findes i det nye resultatsæt.
  useEffect(() => {
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, selectedLabels, catalog]);

  const load = useCallback(async () => {
    if (!committedQuery) return;
    if (searchControllerRef.current) searchControllerRef.current.abort();
    const controller = new AbortController();
    searchControllerRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const r = await fetchSearch(
        {
          q: committedQuery,
          page,
          stores: storesParam(selectedLabels, catalog),
          ...filters,
        },
        controller,
      );
      if (controller.signal.aborted) return;
      setProducts(r.products || []);
      setTotal(r.total ?? 0);
      setTotalPages(r.total_pages || 1);
    } catch (e) {
      if (controller.signal.aborted) return;
      // Uden denne gren var offline/en serverfejl lig med en tom skærm, ikke
      // til at skelne fra "0 resultater" - client.ts har pæne fejltekster,
      // de nåede bare aldrig frem.
      setError(e instanceof Error ? e.message : 'Kunne ikke søge. Prøv igen.');
      setProducts([]);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [committedQuery, page, selectedLabels, catalog, filters]);

  useEffect(() => {
    void load();
    return () => {
      if (searchControllerRef.current) searchControllerRef.current.abort();
    };
  }, [load]);

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
      {q.trim().length > 0 && products.length > 0 ? (
        <FiltersBar values={filters} onChange={setFilters} />
      ) : null}
      {total > 0 ? (
        <Text style={[styles.meta, { color: colors.textMuted }]}>{total} resultater</Text>
      ) : null}
      {error && !loading ? (
        <View style={{ padding: 24, alignItems: 'center', gap: 10 }}>
          <Text style={{ color: colors.text, fontWeight: '600', textAlign: 'center' }}>
            {error}
          </Text>
          <Pressable
            onPress={() => void load()}
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
        </View>
      ) : loading && !products.length ? (
        <ActivityIndicator color={colors.primary} style={{ marginTop: 20 }} />
      ) : (
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
          ListFooterComponent={
            totalPages > 1 ? (
              <View style={styles.pager}>
                <Pressable
                  disabled={page <= 1}
                  onPress={() => setPage((p) => p - 1)}
                  style={[styles.pageBtn, { opacity: page <= 1 ? 0.4 : 1, backgroundColor: colors.surface }]}
                >
                  <Text style={{ color: colors.text }}>Forrige</Text>
                </Pressable>
                <Text style={{ color: colors.textMuted }}>
                  {page} / {totalPages}
                </Text>
                <Pressable
                  disabled={page >= totalPages}
                  onPress={() => setPage((p) => p + 1)}
                  style={[styles.pageBtn, { opacity: page >= totalPages ? 0.4 : 1, backgroundColor: colors.surface }]}
                >
                  <Text style={{ color: colors.text }}>Næste</Text>
                </Pressable>
              </View>
            ) : null
          }
        />
      )}
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
  pager: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
  },
  pageBtn: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8 },
});
