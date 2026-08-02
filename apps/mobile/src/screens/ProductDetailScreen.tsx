import React, { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Image,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';
import { useHeaderHeight } from '@react-navigation/elements';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { fetchNutrition, fetchPriceHistory, type Nutrition } from '../api/productExtras';
import type { PricePoint, PriceSeries } from '../components/PriceHistoryChart';
import { PriceHistoryChart } from '../components/PriceHistoryChart';
import { buildStorePrices } from '../cart/buildStorePrices';
import { useAuth } from '../auth/AuthContext';
import { getSupabase } from '../auth/supabase';
import { rpcName } from '../config/env';
import { useCart } from '../cart/CartContext';
import { useStoreCatalog } from '../stores/StoreCatalogContext';
import { useTheme } from '../theme/ThemeContext';
import type { RootStackParamList } from '../navigation/types';
import type { Product, StoreInfo } from '../api/types';

type Props = NativeStackScreenProps<RootStackParamList, 'ProductDetail'>;

const OVERLAY_COMP_MAX_STORES = 5;

/** Web-paritet (static/js/script.js HISTORY_PALETTE/HISTORY_STORE_ORDER) - samme farve pr. butik. */
const HISTORY_PALETTE = ['#1baf7a', '#2a78d6', '#eda100', '#008300', '#4a3aa7', '#e34948', '#e87ba4', '#eb6834'];
const HISTORY_FALLBACK_COLOR = '#898781';
const HISTORY_STORE_ORDER = [
  'rema', 'bilka', 'foetex', 'netto', 'sb', 'kvickly', 'brugsen', 'lidl',
  'discount365', 'loevbjerg', 'abclavpris', 'meny', 'spar', 'mk',
];

function colorForStoreKey(key: string): string {
  const idx = HISTORY_STORE_ORDER.indexOf(key);
  return idx >= 0 && idx < HISTORY_PALETTE.length ? HISTORY_PALETTE[idx] : HISTORY_FALLBACK_COLOR;
}

type ComparisonEntry = {
  label: string;
  price: number;
  kgPrice: number | null;
  multiDeal: string;
  isSale: boolean;
};

function buildComparisons(
  product: Product,
  catalog: StoreInfo[],
  selectedLabels: Set<string>,
): ComparisonEntry[] {
  const labelByKey = new Map(catalog.map((s) => [s.key, s.label]));
  const remaLabel = labelByKey.get('rema') || 'Rema 1000';
  const entries: ComparisonEntry[] = [];

  if (product.rema_price != null && product.rema_price > 0 && selectedLabels.has(remaLabel)) {
    entries.push({
      label: remaLabel,
      price: product.rema_price,
      kgPrice: product.kg_price,
      multiDeal: product.multi_deal || '',
      isSale: product.rema_is_sale,
    });
  }

  for (const [key, match] of Object.entries(product.store_matches || {})) {
    if (match.price == null || match.price <= 0) continue;
    const label = labelByKey.get(key) || match.name || key;
    if (label === remaLabel) continue;
    if (!selectedLabels.has(label)) continue;
    entries.push({
      label,
      price: match.price,
      kgPrice: match.kg_price,
      multiDeal: match.multi_deal || '',
      isSale: match.is_sale,
    });
  }

  entries.sort((a, b) => a.price - b.price);
  return entries.slice(0, OVERLAY_COMP_MAX_STORES);
}

function todayStr(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Patcher dagens pris ind i historikken (web-paritet — overlay-prisen er altid friskest). */
function patchToday(points: PricePoint[], currentPrice: number | null): PricePoint[] {
  const mapped = points.map((p) => ({ date: p.date, price: p.price }));
  if (currentPrice == null || currentPrice <= 0) return mapped;
  const t = todayStr();
  if (mapped.length && mapped[mapped.length - 1].date === t) {
    mapped[mapped.length - 1] = { date: t, price: currentPrice };
  } else {
    mapped.push({ date: t, price: currentPrice });
  }
  return mapped;
}

type Insight = { label: string; kind: 'good' | 'small' | 'stable' };

function computeInsight(points: PricePoint[]): Insight | null {
  if (!points.length) return null;
  const avg = points.reduce((s, p) => s + p.price, 0) / points.length;
  const cur = points[points.length - 1].price;
  if (avg <= 0) return null;
  if (cur < avg * 0.9) return { label: 'Godt tilbud!', kind: 'good' };
  if (cur < avg) return { label: 'Lille besparelse', kind: 'small' };
  return { label: 'Stabil pris', kind: 'stable' };
}

const NUTRITION_SOURCE_LABEL: Record<Nutrition['source'], string> = {
  rema: 'Rema 1000',
  salling: 'Butikkens varedeklaration',
  off: 'Open Food Facts',
};

export function ProductDetailScreen({ route, navigation }: Props) {
  const { product } = route.params;
  const { colors } = useTheme();
  const { user } = useAuth();
  const { addItem } = useCart();
  const { catalog, selectedLabels } = useStoreCatalog();
  const { height: windowHeight } = useWindowDimensions();
  const headerHeight = useHeaderHeight();
  const bodyHeight = Math.max(240, windowHeight - headerHeight);

  const [qty, setQty] = useState(1);
  const [monitorOpen, setMonitorOpen] = useState(false);
  const [loginOverlay, setLoginOverlay] = useState(false);
  const [targetPriceInput, setTargetPriceInput] = useState('');
  const [alertSaving, setAlertSaving] = useState(false);
  const [alertSet, setAlertSet] = useState(false);

  const onMonitorPress = () => {
    if (!user) {
      setLoginOverlay(true);
      return;
    }
    setMonitorOpen(true);
  };

  /** Web-paritet (static/js/script.js savePriceAlert) - samme RPC, samme validering. */
  const onSavePriceAlert = async () => {
    const target = parseFloat(targetPriceInput.replace(',', '.'));
    if (!target || target <= 0) {
      Alert.alert('Ugyldig pris', 'Indtast venligst en gyldig målpris.');
      return;
    }
    const currentPrice = product.price;
    if (!product.id || !currentPrice) return;
    const sb = getSupabase();
    if (!sb) return;
    setAlertSaving(true);
    try {
      const { data, error } = await sb.rpc(rpcName('create_price_alert'), {
        pid: String(product.id),
        pname: product.name,
        target,
        current: currentPrice,
      });
      if (error || data === false) {
        Alert.alert('Fejl', 'Kunne ikke oprette prisalarm. Prøv igen.');
        return;
      }
      setAlertSet(true);
      setTargetPriceInput('');
    } catch {
      Alert.alert('Fejl', 'Kunne ikke oprette prisalarm. Prøv igen.');
    } finally {
      setAlertSaving(false);
    }
  };

  const [historyLoading, setHistoryLoading] = useState(true);
  const [history, setHistory] = useState<PricePoint[]>([]);
  const [historyByStore, setHistoryByStore] = useState<Record<string, PricePoint[]>>({});
  const [activeHistoryKey, setActiveHistoryKey] = useState<string | null>(null);

  const [nutrition, setNutrition] = useState<Nutrition | null>(null);
  const [nutritionLoading, setNutritionLoading] = useState(true);

  const comparisons = useMemo(
    () => buildComparisons(product, catalog, selectedLabels),
    [product, catalog, selectedLabels],
  );

  const labelByKey = useMemo(() => new Map(catalog.map((s) => [s.key, s.label])), [catalog]);

  useEffect(() => {
    let cancelled = false;
    setHistoryLoading(true);
    fetchPriceHistory(product.id)
      .then((res) => {
        if (cancelled || !res.success) return;
        setHistory(res.history || []);
        setHistoryByStore(res.history_by_store || {});
      })
      .catch(() => {
        if (!cancelled) {
          setHistory([]);
          setHistoryByStore({});
        }
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [product.id]);

  useEffect(() => {
    let cancelled = false;
    setNutritionLoading(true);
    fetchNutrition(product.id)
      .then((res) => {
        if (!cancelled) setNutrition(res.success ? res.nutrition : null);
      })
      .catch(() => {
        if (!cancelled) setNutrition(null);
      })
      .finally(() => {
        if (!cancelled) setNutritionLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [product.id]);

  const storeTabs = useMemo(
    () => Object.keys(historyByStore).filter((k) => (historyByStore[k] || []).length > 0),
    [historyByStore],
  );

  const currentPriceForKey = (key: string | null): number | null => {
    if (key === null) return product.price;
    if (key === 'rema') return product.rema_price;
    return product.store_matches?.[key]?.price ?? null;
  };

  const displayedPoints = useMemo(() => {
    const raw = activeHistoryKey === null ? history : historyByStore[activeHistoryKey] || [];
    return patchToday(raw, currentPriceForKey(activeHistoryKey));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeHistoryKey, history, historyByStore, product]);

  const insight = useMemo(() => computeInsight(displayedPoints), [displayedPoints]);

  const displayedSeries = useMemo<PriceSeries[]>(() => {
    if (activeHistoryKey !== null) {
      return [{ key: activeHistoryKey, color: colors.primary, points: displayedPoints }];
    }
    return storeTabs.map((key) => ({
      key,
      color: colorForStoreKey(key),
      points: patchToday(historyByStore[key] || [], currentPriceForKey(key)),
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeHistoryKey, storeTabs, historyByStore, displayedPoints, colors.primary, product]);

  const onAddToCart = () => {
    const { storePrices, storeMultiDeals } = buildStorePrices(product, catalog);
    addItem({
      id: `product${product.id}`,
      name: product.name,
      store: product.store,
      price: product.price,
      storePrices,
      storeMultiDeals,
      image: product.image,
      category: product.category || 'Andre varer',
      unitMeasure: product.unit_measure,
      kgPrice: product.kg_price != null ? String(product.kg_price) : '',
      multiDeal: product.multi_deal || undefined,
      quantity: qty,
    });
  };

  return (
    <View style={{ height: bodyHeight, backgroundColor: colors.bg }}>
      <ScrollView
        style={{ height: bodyHeight }}
        contentContainerStyle={{ padding: 16, paddingBottom: 48 }}
        scrollEnabled
        bounces
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator
      >
      {product.image ? (
        <Image source={{ uri: product.image }} style={styles.image} resizeMode="contain" />
      ) : null}
      <Text style={[styles.brand, { color: colors.textMuted }]}>{product.brand}</Text>
      <Text style={[styles.title, { color: colors.text }]}>{product.name}</Text>
      {product.description ? (
        <Text style={{ color: colors.textMuted, marginTop: 4 }}>{product.description}</Text>
      ) : null}
      {product.is_sale && product.sale_end_date ? (
        <Text style={{ color: colors.sale, marginTop: 6 }}>
          Tilbud frem til: {product.sale_end_date}
        </Text>
      ) : null}

      <View style={{ marginTop: 12 }}>
        {product.is_sale ? (
          <>
            <Text style={[styles.original, { color: colors.textMuted }]}>
              {product.normal_price.toFixed(2)} kr
            </Text>
            <Text style={[styles.price, { color: colors.sale }]}>
              {product.price.toFixed(2)} kr
            </Text>
          </>
        ) : (
          <Text style={[styles.price, { color: colors.text }]}>
            {product.price.toFixed(2)} kr
          </Text>
        )}
      </View>

      <Pressable
        onPress={onMonitorPress}
        style={[styles.btnOutline, { borderColor: colors.border }]}
      >
        <Text style={{ color: colors.text }}>Overvåg pris</Text>
      </Pressable>

      <View style={styles.qtyRow}>
        <Pressable onPress={() => setQty((q) => Math.max(1, q - 1))}>
          <Text style={[styles.qtyBtn, { color: colors.primary }]}>−</Text>
        </Pressable>
        <Text style={{ color: colors.text, fontSize: 18, minWidth: 32, textAlign: 'center' }}>
          {qty}
        </Text>
        <Pressable onPress={() => setQty((q) => q + 1)}>
          <Text style={[styles.qtyBtn, { color: colors.primary }]}>+</Text>
        </Pressable>
      </View>

      <Pressable onPress={onAddToCart} style={[styles.btn, { backgroundColor: colors.primary }]}>
        <Text style={styles.btnText}>Tilføj til kurv · {product.store}</Text>
      </Pressable>

      <Text style={[styles.h, { color: colors.text }]}>Prissammenligning</Text>
      {comparisons.length === 0 ? (
        <Text style={{ color: colors.textMuted }}>Ingen matchende butikker</Text>
      ) : (
        comparisons.map((c, i) => (
          <View
            key={c.label}
            style={[styles.compare, { backgroundColor: colors.surface, borderColor: colors.border }]}
          >
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text style={{ color: colors.text, fontWeight: '600' }}>{c.label}</Text>
              {c.isSale ? (
                <View style={[styles.miniBadge, { backgroundColor: colors.sale }]}>
                  <Text style={styles.miniBadgeText}>Tilbud</Text>
                </View>
              ) : null}
            </View>
            <Text style={{ color: i === 0 ? colors.badge : colors.text, marginTop: 2 }}>
              {c.price.toFixed(2)} kr
              {i === 0 ? ' · Billigst' : ` · +${(c.price - comparisons[0].price).toFixed(2)} kr`}
            </Text>
            {c.kgPrice != null ? (
              <Text style={{ color: colors.textMuted, fontSize: 12 }}>{c.kgPrice.toFixed(2)} kr/kg</Text>
            ) : null}
            {c.multiDeal ? (
              <Text style={{ color: colors.textMuted, marginTop: 2 }}>{c.multiDeal}</Text>
            ) : null}
          </View>
        ))
      )}

      <Text style={[styles.h, { color: colors.text }]}>Prishistorik</Text>
      {historyLoading ? (
        <ActivityIndicator color={colors.primary} style={{ marginVertical: 12 }} />
      ) : (
        <>
          {storeTabs.length > 0 ? (
            <View style={styles.histTabs}>
              <Pressable
                onPress={() => setActiveHistoryKey(null)}
                style={[
                  styles.histTab,
                  {
                    backgroundColor: activeHistoryKey === null ? colors.primary : colors.surface,
                    borderColor: colors.border,
                  },
                ]}
              >
                <Text style={{ color: activeHistoryKey === null ? '#fff' : colors.text, fontWeight: '600' }}>
                  Alle
                </Text>
              </Pressable>
              {storeTabs.map((key) => {
                const active = activeHistoryKey === key;
                return (
                  <Pressable
                    key={key}
                    onPress={() => setActiveHistoryKey(key)}
                    style={[
                      styles.histTab,
                      styles.histTabRow,
                      {
                        backgroundColor: active ? colors.primary : colors.surface,
                        borderColor: colors.border,
                      },
                    ]}
                  >
                    {activeHistoryKey === null ? (
                      <View style={[styles.histDot, { backgroundColor: colorForStoreKey(key) }]} />
                    ) : null}
                    <Text style={{ color: active ? '#fff' : colors.text, fontWeight: '600' }}>
                      {labelByKey.get(key) || key}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          ) : null}
          <PriceHistoryChart series={displayedSeries} height={140} />
          {insight ? (
            <View
              style={[
                styles.insightBadge,
                {
                  backgroundColor:
                    insight.kind === 'good'
                      ? colors.badge
                      : insight.kind === 'small'
                        ? colors.primaryMuted
                        : colors.surface,
                  borderColor: colors.border,
                },
              ]}
            >
              <Text
                style={{
                  color: insight.kind === 'good' ? '#fff' : insight.kind === 'small' ? colors.primary : colors.text,
                  fontWeight: '700',
                }}
              >
                {insight.label}
              </Text>
            </View>
          ) : null}
        </>
      )}

      <Text style={[styles.h, { color: colors.text }]}>Næringsindhold</Text>
      {nutritionLoading ? (
        <ActivityIndicator color={colors.primary} style={{ marginVertical: 12 }} />
      ) : nutrition ? (
        <View style={[styles.nutritionBox, { backgroundColor: colors.surface, borderColor: colors.border }]}>
          <Text style={{ color: colors.textMuted, marginBottom: 8 }}>Pr. {nutrition.per}</Text>
          {nutrition.rows.map((r) => (
            <View key={r.label} style={styles.nutritionRow}>
              <Text style={{ color: colors.text }}>{r.label}</Text>
              <Text style={{ color: colors.text, fontWeight: '600' }}>{r.value}</Text>
            </View>
          ))}
          {nutrition.ingredients ? (
            <>
              <Text style={{ color: colors.text, fontWeight: '600', marginTop: 10 }}>Ingredienser</Text>
              <Text style={{ color: colors.textMuted, marginTop: 4 }}>{nutrition.ingredients}</Text>
            </>
          ) : null}
          <Text style={{ color: colors.textMuted, fontSize: 11, marginTop: 10 }}>
            Kilde: {NUTRITION_SOURCE_LABEL[nutrition.source]}
          </Text>
        </View>
      ) : (
        <Text style={{ color: colors.textMuted }}>Ingen næringsinformation tilgængelig</Text>
      )}

      <View style={{ height: 24 }} />
      </ScrollView>
      <Modal visible={monitorOpen} transparent animationType="fade" onRequestClose={() => setMonitorOpen(false)}>
        <View style={styles.modalBackdrop}>
          <View style={[styles.modalCard, { backgroundColor: colors.surface }]}>
            <Text style={{ color: colors.text, fontWeight: '700', fontSize: 16, marginBottom: 8 }}>
              Prisovervågning
            </Text>
            {alertSet ? (
              <Text style={{ color: colors.text }}>✅ Alarm sat - du får en mail</Text>
            ) : (
              <>
                <Text style={{ color: colors.textMuted, marginBottom: 12 }}>
                  Giv mig besked på mail når prisen falder til:
                </Text>
                <TextInput
                  value={targetPriceInput}
                  onChangeText={setTargetPriceInput}
                  placeholder="Eks. 40"
                  placeholderTextColor={colors.textMuted}
                  keyboardType="decimal-pad"
                  style={[styles.alertInput, { borderColor: colors.border, color: colors.text }]}
                />
              </>
            )}
            <Pressable
              onPress={alertSet ? () => setMonitorOpen(false) : onSavePriceAlert}
              disabled={alertSaving}
              style={[
                styles.btn,
                { backgroundColor: colors.primary, marginTop: 16, opacity: alertSaving ? 0.6 : 1 },
              ]}
            >
              <Text style={styles.btnText}>
                {alertSet ? 'Luk' : alertSaving ? 'Gemmer…' : 'Sæt alarm'}
              </Text>
            </Pressable>
            {!alertSet ? (
              <Pressable
                onPress={() => setMonitorOpen(false)}
                style={[styles.btnOutline, { marginTop: 8, borderColor: colors.border }]}
              >
                <Text style={{ color: colors.text }}>Annuller</Text>
              </Pressable>
            ) : null}
          </View>
        </View>
      </Modal>
      <Modal visible={loginOverlay} transparent animationType="fade" onRequestClose={() => setLoginOverlay(false)}>
        <View style={styles.modalBackdrop}>
          <View style={[styles.modalCard, { backgroundColor: colors.surface, alignItems: 'center' }]}>
            <Text style={{ fontSize: 28, marginBottom: 8 }}>🔒</Text>
            <Text style={{ color: colors.text, fontWeight: '700', fontSize: 16, marginBottom: 6, textAlign: 'center' }}>
              Log ind for at fortsætte
            </Text>
            <Text style={{ color: colors.textMuted, fontSize: 14, textAlign: 'center', marginBottom: 16 }}>
              Du skal være logget ind for at bruge prisovervågning.
            </Text>
            <View style={{ flexDirection: 'row', gap: 12, width: '100%' }}>
              <Pressable
                onPress={() => setLoginOverlay(false)}
                style={[styles.btnOutline, { flex: 1, marginTop: 0, borderColor: colors.border }]}
              >
                <Text style={{ color: colors.text }}>Luk</Text>
              </Pressable>
              <Pressable
                onPress={() => {
                  setLoginOverlay(false);
                  navigation.navigate('Auth');
                }}
                style={[styles.btn, { flex: 1, marginTop: 0, backgroundColor: colors.primary }]}
              >
                <Text style={styles.btnText}>Log ind</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  image: { width: '100%', height: 220, marginBottom: 12 },
  brand: { fontSize: 13 },
  title: { fontSize: 22, fontWeight: '700', marginTop: 4 },
  original: { textDecorationLine: 'line-through', fontSize: 14 },
  price: { fontSize: 24, fontWeight: '800' },
  btnOutline: {
    marginTop: 12,
    padding: 12,
    borderRadius: 10,
    borderWidth: 1,
    alignItems: 'center',
  },
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  modalCard: { width: '100%', borderRadius: 16, padding: 20 },
  alertInput: {
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    fontSize: 16,
  },
  qtyRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 16,
    gap: 12,
  },
  qtyBtn: { fontSize: 28, fontWeight: '600', paddingHorizontal: 12 },
  btn: {
    marginTop: 12,
    padding: 14,
    borderRadius: 12,
    alignItems: 'center',
  },
  btnText: { color: '#fff', fontWeight: '700', fontSize: 16 },
  h: { fontSize: 17, fontWeight: '700', marginTop: 24, marginBottom: 8 },
  compare: {
    padding: 12,
    borderRadius: 10,
    borderWidth: 1,
    marginBottom: 8,
  },
  miniBadge: { borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2 },
  miniBadgeText: { color: '#fff', fontSize: 10, fontWeight: '700' },
  histTabs: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 10,
  },
  histTab: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 14,
    borderWidth: 1,
  },
  histTabRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  histDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  insightBadge: {
    alignSelf: 'flex-start',
    marginTop: 10,
    borderRadius: 10,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  nutritionBox: { borderWidth: 1, borderRadius: 12, padding: 14 },
  nutritionRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: 'rgba(128,128,128,0.2)',
  },
});
