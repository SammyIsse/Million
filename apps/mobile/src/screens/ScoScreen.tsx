import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { apiPost } from '../api/client';
import { postCartEvent } from '../api/listing';
import { useAuth } from '../auth/AuthContext';
import { useCart } from '../cart/CartContext';
import {
  SCO_TOP_N,
  calculateStoreComparisons,
  sortScoStores,
  type ScoResult,
  type ScoStoreResult,
} from '../cart/sco';
import type { CartItem } from '../cart/types';
import {
  fullCoveragePriceRange,
  recordCompareSavings,
} from '../savings/personalSavings';
import { useStoreCatalog } from '../stores/StoreCatalogContext';
import { useTheme } from '../theme/ThemeContext';

/** Dedup pr. app-session — spejler web's `_comparedProductIds`. */
const comparedProductIds = new Set<string>();

type AlternativeItem = {
  cart_id: string;
  store: string;
  alt_id: string;
  alt_name: string;
  alt_price: number;
  alt_image: string;
  alt_storePrices: Record<string, number>;
  alt_category: string;
  alt_unitMeasure: string;
  alt_kgPrice: string;
  alt_store: string;
};

type AlternativesResponse = {
  success: boolean;
  alternatives: AlternativeItem[];
  error?: string;
};

export function ScoScreen() {
  const { colors } = useTheme();
  const { user } = useAuth();
  const { items, replaceItem } = useCart();
  const { catalog, selectedLabels, ready } = useStoreCatalog();

  const [loading, setLoading] = useState(true);
  const [result, setResult] = useState<ScoResult | null>(null);
  const [activeStore, setActiveStore] = useState<string | null>(null);
  const [alternatives, setAlternatives] = useState<Record<string, AlternativeItem>>({});
  const [altLoading, setAltLoading] = useState(false);
  const altFetchKey = useRef<string>('');

  const runSco = useCallback(async () => {
    if (!items.length) {
      setResult(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const r = await calculateStoreComparisons(items, catalog, selectedLabels);
      setResult(r);
      const sortedAll = sortScoStores(r.stores);
      const sorted = sortedAll.slice(0, SCO_TOP_N);
      setActiveStore((prev) => {
        if (prev && sorted.some((s) => s.name === prev)) return prev;
        return sorted[0]?.name ?? null;
      });

      const toCompare = items.filter((i) => !comparedProductIds.has(i.id));
      if (toCompare.length) {
        for (const i of toCompare) comparedProductIds.add(i.id);
        const rawItems = toCompare.map((i) => ({
          id: i.id.replace(/^product/, ''),
          qty: i.quantity,
        }));
        void postCartEvent('compare', rawItems).catch(() => {});
      }

      // Personlig besparelse: dyreste − billigste (fuld dækning), kræver login
      if (user) {
        const range = fullCoveragePriceRange(sortedAll);
        if (range) {
          void recordCompareSavings(range.cheap, range.expensive).catch(() => {});
        }
      }
    } finally {
      setLoading(false);
    }
  }, [items, catalog, selectedLabels, user]);

  useEffect(() => {
    if (ready) void runSco();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, items, catalog, selectedLabels]);

  const topStores = useMemo(
    () => (result ? sortScoStores(result.stores).slice(0, SCO_TOP_N) : []),
    [result],
  );

  const active: ScoStoreResult | undefined = topStores.find((s) => s.name === activeStore);

  useEffect(() => {
    if (!active || !active.missingDetails.length) {
      setAlternatives({});
      return;
    }
    const key = `${active.name}:${active.missingDetails.map((m) => m.cart_id).join(',')}`;
    if (altFetchKey.current === key) return;
    altFetchKey.current = key;
    setAltLoading(true);
    const missing_items = active.missingDetails.map((m) => ({
      cart_id: m.cart_id,
      store: active.name,
      category: m.category,
      name: m.name,
      weight_str: m.weight_str,
      image: m.image,
    }));
    apiPost<AlternativesResponse>('/api/alternatives', { missing_items })
      .then((res) => {
        const map: Record<string, AlternativeItem> = {};
        for (const alt of res.alternatives || []) map[alt.cart_id] = alt;
        setAlternatives(map);
      })
      .catch(() => setAlternatives({}))
      .finally(() => setAltLoading(false));
  }, [active]);

  const acceptAlternative = useCallback(
    (alt: AlternativeItem) => {
      const original = items.find((i) => i.id === alt.cart_id);
      const nextId = alt.alt_id.startsWith('product') ? alt.alt_id : `product${alt.alt_id}`;
      const nextItem: CartItem = {
        id: nextId,
        name: alt.alt_name,
        store: alt.alt_store,
        price: alt.alt_price,
        storePrices: alt.alt_storePrices || {},
        storeMultiDeals: {},
        image: alt.alt_image,
        category: alt.alt_category,
        unitMeasure: alt.alt_unitMeasure,
        kgPrice: alt.alt_kgPrice,
        quantity: original?.quantity ?? 1,
      };
      replaceItem(alt.cart_id, nextItem);
    },
    [items, replaceItem],
  );

  if (!ready || (loading && !result)) {
    return (
      <View style={[styles.center, { backgroundColor: colors.bg }]}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  if (!items.length) {
    return (
      <View style={[styles.center, { backgroundColor: colors.bg }]}>
        <Text style={{ color: colors.textMuted }}>Kurven er tom</Text>
      </View>
    );
  }

  if (!result || !topStores.length) {
    return (
      <View style={[styles.center, { backgroundColor: colors.bg }]}>
        <Text style={{ color: colors.textMuted }}>Ingen butikker matcher din kurv endnu</Text>
      </View>
    );
  }

  return (
    <ScrollView style={{ flex: 1, backgroundColor: colors.bg }} contentContainerStyle={{ padding: 16 }}>
      <Text style={[styles.h1, { color: colors.text }]}>Find billigste butik</Text>
      <Text style={{ color: colors.textMuted, marginBottom: 12 }}>
        Sammenligning af {topStores.length} butikker for din kurv
      </Text>

      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 16 }}>
        {topStores.map((s, i) => {
          const isActive = s.name === activeStore;
          return (
            <Pressable
              key={s.name}
              onPress={() => setActiveStore(s.name)}
              style={[
                styles.storeCard,
                {
                  backgroundColor: isActive ? colors.primaryMuted : colors.surface,
                  borderColor: isActive ? colors.primary : colors.border,
                },
              ]}
            >
              {i === 0 ? (
                <View style={[styles.winnerBadge, { backgroundColor: colors.badge }]}>
                  <Text style={styles.winnerText}>Billigst</Text>
                </View>
              ) : null}
              <Text style={[styles.storeName, { color: colors.text }]} numberOfLines={1}>
                {s.name}
              </Text>
              <Text style={{ color: colors.textMuted, fontSize: 12, marginTop: 2 }}>
                Dækning: {s.coverage}/{s.totalItems}
              </Text>
              <Text style={[styles.storePrice, { color: colors.primary }]}>
                {s.totalPrice.toFixed(2)} kr
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {active ? (
        <>
          {active.missingDetails.length > 0 ? (
            <View style={{ marginBottom: 20 }}>
              <Text style={[styles.h2, { color: colors.text }]}>
                Mangler i {active.name} ({active.missingDetails.length})
              </Text>
              {altLoading ? <ActivityIndicator color={colors.primary} style={{ marginVertical: 8 }} /> : null}
              {active.missingDetails.map((m) => {
                const alt = alternatives[m.cart_id];
                return (
                  <View
                    key={m.cart_id}
                    style={[styles.itemRow, { backgroundColor: colors.surface, borderColor: colors.border }]}
                  >
                    {m.image ? (
                      <Image source={{ uri: m.image }} style={styles.itemImg} resizeMode="contain" />
                    ) : null}
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: colors.text, fontWeight: '600' }} numberOfLines={2}>
                        {m.name}
                      </Text>
                      <Text style={{ color: colors.textMuted, fontSize: 12 }}>Ikke tilgængelig her</Text>
                      {alt ? (
                        <Text style={{ color: colors.textMuted, fontSize: 12, marginTop: 2 }}>
                          Alternativ: {alt.alt_name} · {alt.alt_price.toFixed(2)} kr
                        </Text>
                      ) : null}
                    </View>
                    {alt ? (
                      <Pressable
                        onPress={() => acceptAlternative(alt)}
                        style={[styles.acceptBtn, { backgroundColor: colors.primary }]}
                      >
                        <Text style={styles.acceptBtnText}>Vælg alt.</Text>
                      </Pressable>
                    ) : null}
                  </View>
                );
              })}
            </View>
          ) : null}

          <Text style={[styles.h2, { color: colors.text }]}>
            I kurven hos {active.name}
          </Text>
          {(result.matchedItemsPerStore[active.name] || []).map((m) => (
            <View
              key={m.cart_id}
              style={[styles.itemRow, { backgroundColor: colors.surface, borderColor: colors.border }]}
            >
              {m.image ? <Image source={{ uri: m.image }} style={styles.itemImg} resizeMode="contain" /> : null}
              <View style={{ flex: 1 }}>
                <Text style={{ color: colors.text, fontWeight: '600' }} numberOfLines={2}>
                  {m.name}
                </Text>
                <Text style={{ color: colors.textMuted, fontSize: 12 }}>
                  {m.quantity} × {m.price.toFixed(2)} kr
                </Text>
              </View>
              <Text style={{ color: colors.text, fontWeight: '700' }}>
                {(m.price * m.quantity).toFixed(2)} kr
              </Text>
            </View>
          ))}

          <View style={[styles.totalBox, { backgroundColor: colors.primaryMuted }]}>
            <Text style={{ color: colors.text, fontWeight: '700', fontSize: 16 }}>
              Total hos {active.name}
            </Text>
            <Text style={{ color: colors.primary, fontWeight: '800', fontSize: 20, marginTop: 4 }}>
              {active.totalPrice.toFixed(2)} kr
            </Text>
            <Text style={{ color: colors.textMuted, fontSize: 12, marginTop: 4 }}>
              (uden accepterede alternativers pris)
            </Text>
          </View>
        </>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  h1: { fontSize: 22, fontWeight: '800' },
  h2: { fontSize: 16, fontWeight: '700', marginBottom: 8, marginTop: 4 },
  storeCard: {
    width: 150,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    marginRight: 10,
  },
  winnerBadge: {
    position: 'absolute',
    top: -8,
    right: 8,
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  winnerText: { color: '#fff', fontSize: 10, fontWeight: '700' },
  storeName: { fontSize: 14, fontWeight: '700' },
  storePrice: { fontSize: 18, fontWeight: '800', marginTop: 6 },
  itemRow: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 10,
    borderRadius: 10,
    borderWidth: 1,
    marginBottom: 8,
    gap: 10,
  },
  itemImg: { width: 40, height: 40 },
  acceptBtn: { paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8 },
  acceptBtnText: { color: '#fff', fontWeight: '700', fontSize: 12 },
  totalBox: { padding: 16, borderRadius: 12, marginTop: 8, marginBottom: 24 },
});
