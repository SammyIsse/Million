import React, { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';
import { useHeaderHeight } from '@react-navigation/elements';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import {
  fetchRecipeDetail,
  recordRecipeClick,
  type MatchedProduct,
  type RecipeDetail,
  type RecipeIngredient,
  type RecipeSnapshot,
} from '../api/recipes';
import { useCart } from '../cart/CartContext';
import { useTheme } from '../theme/ThemeContext';
import type { RootStackParamList } from '../navigation/types';

type Props = NativeStackScreenProps<RootStackParamList, 'RecipeDetail'>;

// Samme g/kg/l/ml/cl/dl-konvertering som app_support.py::_unit_to_grams og
// templates/opskrift.html's UNIT_TO_GRAMS - "gram-ækvivalent" til at
// sammenligne opskrift-mængder med pakkers weight_g.
const UNIT_TO_GRAMS: Record<string, number> = { g: 1, kg: 1000, ml: 1, cl: 10, dl: 100, l: 1000 };

function formatQty(n: number): string {
  const rounded = Math.round(n * 100) / 100;
  return String(rounded).replace('.', ',');
}

type PickedCandidate = { product: MatchedProduct; units: number; cost: number };

/** Billigste kandidat (og antal pakker) for den skalerede mængde, eller null
 * hvis ingen kandidat kan mængdevurderes (fx vægtløse Dagrofa-varer) - i så
 * fald beholdes det oprindelige match. Port af samme funktion i
 * templates/opskrift.html (web-paritet). */
function pickBestCandidate(
  scaledQty: number,
  unit: string,
  candidates: MatchedProduct[],
): PickedCandidate | null {
  if (!candidates || candidates.length === 0) return null;
  const targetGrams = UNIT_TO_GRAMS[unit] ? scaledQty * UNIT_TO_GRAMS[unit] : null;
  let best: PickedCandidate | null = null;
  for (const c of candidates) {
    let units: number | null = null;
    if (targetGrams !== null && c.weight_g) {
      units = Math.max(1, Math.ceil(targetGrams / c.weight_g));
    } else if (!unit && c.stk_count) {
      units = Math.max(1, Math.ceil(scaledQty / c.stk_count));
    } else {
      continue;
    }
    const cost = units * c.price;
    if (!best || cost < best.cost) {
      best = { product: c, units, cost };
    }
  }
  return best;
}

export function RecipeDetailScreen({ route, navigation }: Props) {
  const { recipeId } = route.params;
  const { colors } = useTheme();
  const { addItem } = useCart();
  const { height: windowHeight } = useWindowDimensions();
  const headerHeight = useHeaderHeight();
  const bodyHeight = Math.max(240, windowHeight - headerHeight);

  const [loading, setLoading] = useState(true);
  const [recipe, setRecipe] = useState<RecipeDetail | null>(null);
  const [ingredients, setIngredients] = useState<RecipeIngredient[]>([]);
  const [snapshot, setSnapshot] = useState<RecipeSnapshot | null>(null);
  const [servings, setServings] = useState(4);
  const [baseServings, setBaseServings] = useState(4);
  const [addedLabel, setAddedLabel] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchRecipeDetail(recipeId)
      .then((res) => {
        if (cancelled || !res.success || !res.recipe) return;
        setRecipe(res.recipe);
        setIngredients(res.ingredients || []);
        setSnapshot(res.snapshot);
        const base = res.recipe.servings || 4;
        setBaseServings(base);
        setServings(base);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    void recordRecipeClick(recipeId);
    return () => {
      cancelled = true;
    };
  }, [recipeId]);

  const scale = servings / baseServings;

  // Ved udgangspunktet (scale=1) vises serverens oprindelige navne-match
  // uændret - kun ved faktisk ændret personer-tal genvurderes hvilken
  // pakke der er billigst for den nye mængde (web-paritet).
  const computed = useMemo(() => {
    let total = 0;
    const rows = ingredients.map((ing) => {
      let displayText = ing.raw_text;
      let matchProduct = ing.matched_product;
      let units = 1;

      if (ing.quantity !== null) {
        const scaled = ing.quantity * scale;
        const parts = [formatQty(scaled), ing.unit, ing.ingredient_name || ing.raw_text].filter(
          Boolean,
        );
        displayText = parts.join(' ');

        if (scale !== 1) {
          const picked = pickBestCandidate(scaled, ing.unit, ing.candidates);
          if (picked) {
            matchProduct = picked.product;
            units = picked.units;
          }
        }
      }

      const lineCost = matchProduct ? matchProduct.price * units : null;
      if (lineCost !== null) total += lineCost;

      return { ing, displayText, matchProduct, units, lineCost };
    });
    return { rows, total };
  }, [ingredients, scale]);

  const displayedTotal =
    scale !== 1 && computed.total > 0 ? computed.total : snapshot?.cheapest_total_price ?? null;

  const onAddAll = () => {
    const matched = computed.rows.filter((r) => r.matchProduct);
    matched.forEach(({ matchProduct, units }) => {
      if (!matchProduct) return;
      addItem({
        id: `product${matchProduct.id}`,
        name: matchProduct.name,
        store: matchProduct.store,
        price: matchProduct.price,
        storePrices: matchProduct.store_prices || {},
        storeMultiDeals: {},
        image: matchProduct.image,
        category: matchProduct.category || 'Andre varer',
        unitMeasure: matchProduct.unit_measure,
        kgPrice: matchProduct.kg_price != null ? String(matchProduct.kg_price) : '',
        multiDeal: matchProduct.multi_deal || undefined,
        quantity: units,
      });
    });
    setAddedLabel(`${matched.length} varer tilføjet til kurv`);
    setTimeout(() => setAddedLabel(null), 2000);
  };

  if (loading) {
    return (
      <View style={[styles.center, { height: bodyHeight, backgroundColor: colors.bg }]}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  if (!recipe) {
    return (
      <View style={[styles.center, { height: bodyHeight, backgroundColor: colors.bg }]}>
        <Text style={{ color: colors.textMuted }}>Opskriften blev ikke fundet.</Text>
      </View>
    );
  }

  return (
    <View style={{ height: bodyHeight, backgroundColor: colors.bg }}>
      <ScrollView
        style={{ height: bodyHeight }}
        contentContainerStyle={{ padding: 16, paddingBottom: 48 }}
        showsVerticalScrollIndicator
      >
        {recipe.image_url ? (
          <Image source={{ uri: recipe.image_url }} style={styles.image} resizeMode="cover" />
        ) : null}
        <Text style={[styles.title, { color: colors.text }]}>{recipe.title}</Text>

        <View style={styles.pillRow}>
          <View style={[styles.stepper, { backgroundColor: colors.surface, borderColor: colors.border }]}>
            <Pressable
              onPress={() => setServings((s) => Math.max(1, s - 1))}
              disabled={servings <= 1}
              hitSlop={8}
            >
              <Text style={[styles.stepperBtn, { color: servings <= 1 ? colors.textMuted : colors.primary }]}>
                −
              </Text>
            </Pressable>
            <Text style={{ color: colors.text, fontWeight: '600' }}>{servings} personer</Text>
            <Pressable onPress={() => setServings((s) => s + 1)} hitSlop={8}>
              <Text style={[styles.stepperBtn, { color: colors.primary }]}>+</Text>
            </Pressable>
          </View>
          {recipe.total_time_minutes ? (
            <View style={[styles.pill, { backgroundColor: colors.surface }]}>
              <Text style={{ color: colors.textMuted, fontSize: 12, fontWeight: '600' }}>
                {recipe.total_time_minutes} min
              </Text>
            </View>
          ) : null}
        </View>

        {recipe.source_name || recipe.source_url ? (
          <Text style={{ color: colors.textMuted, fontSize: 12, marginBottom: 12 }}>
            Opskrift fra {recipe.source_name || recipe.source_url}
          </Text>
        ) : null}

        <View style={[styles.ctaCard, { backgroundColor: colors.surface, borderColor: colors.border }]}>
          <Text style={[styles.ctaAmount, { color: colors.text }]}>
            {displayedTotal ? `${displayedTotal.toFixed(2)} kr` : 'Prisen kunne ikke beregnes endnu'}
          </Text>
          {snapshot ? (
            <Text style={{ color: colors.textMuted, fontSize: 12, marginTop: 2 }}>
              {snapshot.matched_ingredient_count} af {snapshot.total_ingredient_count} ingredienser
              fundet i vores prissammenligning
            </Text>
          ) : null}
          <Pressable
            onPress={onAddAll}
            style={[styles.btn, { backgroundColor: colors.primary, marginTop: 12 }]}
          >
            <Text style={styles.btnText}>
              {addedLabel || 'Læg fundne varer i kurv'}
            </Text>
          </Pressable>
        </View>

        <Text style={[styles.h, { color: colors.text }]}>Ingredienser</Text>
        {computed.rows.map(({ ing, displayText, matchProduct, units }) => (
          <Pressable
            key={ing.id}
            disabled={!matchProduct?.api}
            onPress={() => {
              // matchProduct er den AKTUELT viste pakke (base-match eller en
              // kandidat, hvis personer-skalering har skiftet til en anden) -
              // .api følger med, se computed useMemo ovenfor.
              if (matchProduct?.api) {
                navigation.navigate('ProductDetail', { product: matchProduct.api });
              }
            }}
            style={[styles.ingredientRow, { backgroundColor: colors.surface, borderColor: colors.border }]}
          >
            {matchProduct?.image ? (
              <Image source={{ uri: matchProduct.image }} style={styles.thumb} resizeMode="contain" />
            ) : (
              <View style={[styles.thumb, styles.thumbEmpty, { borderColor: colors.border }]} />
            )}
            <View style={{ flex: 1 }}>
              <Text style={{ color: colors.text, fontWeight: '600', fontSize: 13 }}>{displayText}</Text>
              {matchProduct ? (
                <Text style={{ color: colors.primary, fontSize: 12, fontWeight: '600', marginTop: 2 }}>
                  ({[matchProduct.unit_measure, matchProduct.name].filter(Boolean).join(' ')}
                  {units > 1 ? ` × ${units}` : ''} · {(matchProduct.price * units).toFixed(2)} kr)
                  {matchProduct.is_sale ? ' Tilbud' : ''}
                </Text>
              ) : null}
              {!matchProduct ? (
                <Text style={{ color: colors.textMuted, fontSize: 12, marginTop: 2 }}>
                  Ikke fundet i prissammenligningen
                </Text>
              ) : null}
            </View>
          </Pressable>
        ))}

        {recipe.nutrition_source ? (
          <>
            <Text style={[styles.h, { color: colors.text }]}>Næringsindhold</Text>
            <View style={[styles.nutritionBox, { backgroundColor: colors.surface, borderColor: colors.border }]}>
              <View style={styles.nutritionRow}>
                {recipe.nutrition_source.calories ? (
                  <Text style={{ color: colors.text }}><Text style={styles.nutritionBold}>{recipe.nutrition_source.calories}</Text> energi</Text>
                ) : null}
                {recipe.nutrition_source.protein ? (
                  <Text style={{ color: colors.text }}><Text style={styles.nutritionBold}>{recipe.nutrition_source.protein}</Text> protein</Text>
                ) : null}
                {recipe.nutrition_source.fat ? (
                  <Text style={{ color: colors.text }}><Text style={styles.nutritionBold}>{recipe.nutrition_source.fat}</Text> fedt</Text>
                ) : null}
                {recipe.nutrition_source.carbohydrate ? (
                  <Text style={{ color: colors.text }}><Text style={styles.nutritionBold}>{recipe.nutrition_source.carbohydrate}</Text> kulhydrat</Text>
                ) : null}
              </View>
              <Text style={{ color: colors.textMuted, fontSize: 11, marginTop: 8 }}>
                Ifølge kilden{recipe.nutrition_source.serving_size ? `, pr. ${recipe.nutrition_source.serving_size}` : ''}.
              </Text>
            </View>
          </>
        ) : recipe.nutrition_estimate ? (
          <>
            <Text style={[styles.h, { color: colors.text }]}>Næringsindhold</Text>
            <View style={[styles.nutritionBox, { backgroundColor: colors.surface, borderColor: colors.border }]}>
              <View style={styles.nutritionRow}>
                <Text style={{ color: colors.text }}>
                  <Text style={styles.nutritionBold}>{Math.round(recipe.nutrition_estimate.kcal * scale * 10) / 10}</Text> kcal
                </Text>
                <Text style={{ color: colors.text }}>
                  <Text style={styles.nutritionBold}>{Math.round(recipe.nutrition_estimate.protein * scale * 10) / 10}</Text> g protein
                </Text>
                <Text style={{ color: colors.text }}>
                  <Text style={styles.nutritionBold}>{Math.round(recipe.nutrition_estimate.fedt * scale * 10) / 10}</Text> g fedt
                </Text>
                <Text style={{ color: colors.text }}>
                  <Text style={styles.nutritionBold}>{Math.round(recipe.nutrition_estimate.kulhydrat * scale * 10) / 10}</Text> g kulhydrat
                </Text>
              </View>
              <Text style={{ color: colors.textMuted, fontSize: 11, marginTop: 8, lineHeight: 16 }}>
                Estimat for hele opskriften, baseret på {recipe.nutrition_estimate.contributing_ingredient_count} af{' '}
                {recipe.nutrition_estimate.total_ingredient_count} ingredienser - kan afvige fra det faktiske
                indhold, er mest tænkt som et udgangspunkt.
              </Text>
            </View>
          </>
        ) : null}

        {recipe.instructions && recipe.instructions.length > 0 ? (
          <>
            <Text style={[styles.h, { color: colors.text }]}>Fremgangsmåde</Text>
            {recipe.instructions.map((step, i) => (
              <View key={i} style={styles.stepRow}>
                <View style={[styles.stepNumber, { backgroundColor: colors.primaryMuted }]}>
                  <Text style={{ color: colors.primary, fontWeight: '800', fontSize: 12 }}>{i + 1}</Text>
                </View>
                <Text style={{ color: colors.text, flex: 1, lineHeight: 20 }}>{step}</Text>
              </View>
            ))}
          </>
        ) : recipe.source_url ? (
          <>
            <Text style={[styles.h, { color: colors.text }]}>Fremgangsmåde</Text>
            <Pressable onPress={() => Linking.openURL(recipe.source_url)}>
              <Text style={{ color: colors.primary, fontWeight: '600' }}>
                Se hele fremgangsmåden hos {recipe.source_name || 'kilden'} →
              </Text>
            </Pressable>
          </>
        ) : null}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  center: { alignItems: 'center', justifyContent: 'center' },
  image: { width: '100%', height: 220, borderRadius: 14, marginBottom: 12 },
  title: { fontSize: 22, fontWeight: '800', marginBottom: 10 },
  pillRow: { flexDirection: 'row', gap: 8, marginBottom: 8, alignItems: 'center' },
  stepper: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  stepperBtn: { fontSize: 18, fontWeight: '800', paddingHorizontal: 4 },
  pill: { borderRadius: 999, paddingHorizontal: 12, paddingVertical: 6 },
  ctaCard: { borderWidth: 1, borderRadius: 14, padding: 16, marginBottom: 8 },
  ctaAmount: { fontSize: 22, fontWeight: '800' },
  btn: { padding: 14, borderRadius: 12, alignItems: 'center' },
  btnText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  h: { fontSize: 17, fontWeight: '700', marginTop: 22, marginBottom: 8 },
  ingredientRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    borderWidth: 1,
    borderRadius: 10,
    padding: 10,
    marginBottom: 8,
  },
  thumb: { width: 40, height: 40, borderRadius: 8 },
  thumbEmpty: { borderWidth: 1, borderStyle: 'dashed' },
  stepRow: { flexDirection: 'row', gap: 10, marginBottom: 12, alignItems: 'flex-start' },
  stepNumber: {
    width: 24,
    height: 24,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  nutritionBox: { borderWidth: 1, borderRadius: 12, padding: 14 },
  nutritionRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, columnGap: 16 },
  nutritionBold: { fontWeight: '800' },
});
