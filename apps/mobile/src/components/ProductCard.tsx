import React from 'react';
import {
  Image,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import type { Product } from '../api/types';
import { useTheme } from '../theme/ThemeContext';
import { useCart } from '../cart/CartContext';
import { buildStorePrices } from '../cart/buildStorePrices';
import { useStoreCatalog } from '../stores/StoreCatalogContext';

type Props = {
  product: Product;
  onPress: (product: Product) => void;
  /** `rail` = horisontal forside-række (fast bredde, ingen 48%-max). */
  variant?: 'grid' | 'rail';
};

export function ProductCard({ product, onPress, variant = 'grid' }: Props) {
  const { colors, isDark } = useTheme();
  const { addItem } = useCart();
  const { catalog } = useStoreCatalog();
  const onSale = product.is_sale || product.is_any_sale;
  const discountPct =
    product.is_sale && product.normal_price > product.price
      ? Math.round((1 - product.price / product.normal_price) * 100)
      : null;

  return (
    <TouchableOpacity
      activeOpacity={0.9}
      delayPressIn={80}
      onPress={() => onPress(product)}
      style={[
        styles.card,
        variant === 'rail' ? styles.cardRail : styles.cardGrid,
        { backgroundColor: colors.surface, borderColor: colors.border },
      ]}
    >
      <View
        style={[
          styles.imageWrap,
          {
            backgroundColor: isDark ? '#252825' : '#F3F5F0',
          },
        ]}
      >
        {onSale ? (
          <View style={styles.saleBadge}>
            <Text style={styles.saleText}>{discountPct ? `SPAR ${discountPct}%` : 'TILBUD'}</Text>
          </View>
        ) : null}
        <View style={[styles.storeBadge, { backgroundColor: colors.primaryMuted }]}>
          <Text style={[styles.storeText, { color: colors.primary }]} numberOfLines={1}>
            {product.store}
          </Text>
        </View>
        {product.image ? (
          <Image source={{ uri: product.image }} style={styles.image} resizeMode="contain" />
        ) : (
          <View style={[styles.imagePlaceholder, { backgroundColor: colors.border }]} />
        )}
      </View>
      <Text style={[styles.brand, { color: colors.textMuted }]} numberOfLines={1}>
        {product.brand}
      </Text>
      <Text style={[styles.name, { color: colors.text }]} numberOfLines={2}>
        {product.name}
      </Text>
      {/* Vægt/beskrivelse, antal og kg-pris - præcis de tre linjer webbens
          produktkort altid har haft (.product-weight × 2 + .product-kg-price i
          templates/macros/product_card.html), i samme rækkefølge og med samme
          betingelser. Felterne har ligget klar i listing-JSON'en hele tiden
          (app_support.py::product_to_api_dict: description / stk_count /
          kg_price) - kortet rendrede dem bare ikke, så app-brugeren kunne se
          en pris uden at kunne se hvor meget man fik for den. I en
          pris-sammenligning er kg-prisen selve sammenligningsgrundlaget. */}
      {product.description ? (
        <Text style={[styles.meta, { color: colors.textMuted }]} numberOfLines={1}>
          {product.description}
        </Text>
      ) : null}
      {product.stk_count ? (
        <Text style={[styles.meta, { color: colors.textMuted }]} numberOfLines={1}>
          {product.stk_count} stk
        </Text>
      ) : null}
      {product.kg_price != null && product.kg_price > 0 ? (
        <Text style={[styles.kgPrice, { color: colors.textMuted }]} numberOfLines={1}>
          {product.kg_price.toFixed(2)} kr/kg
        </Text>
      ) : null}
      {/* Samme betingelse som webben: `{% if not product.store_matches %}`.
          `has_match` er `bool(store_matches) or rema_price > 0`
          (app_support.py::product_to_api_dict), så et Rema-kort UDEN
          krydsmatch har has_match=true - webben viste badget, appen gjorde
          ikke, selvom varen faktisk kun findes ét sted. */}
      {Object.keys(product.store_matches || {}).length === 0 ? (
        <Text style={[styles.only, { color: colors.textMuted }]}>
          Kun hos {product.store}
        </Text>
      ) : null}
      <View style={styles.footer}>
        <View style={styles.priceCol}>
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
      </View>
      {/* Varenavnet SKAL med i etiketten: i et gitter med 60 kort hoerer
          VoiceOver ellers 60 identiske "Tilføj til kurv". Samme rettelse er
          lavet i webbens produktkort-makro. */}
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel={`Tilføj ${product.name} til kurv`}
        activeOpacity={0.8}
        onPress={() => {
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
          });
        }}
        style={[styles.addBtn, { backgroundColor: colors.primary }]}
      >
        <Text style={styles.addBtnText}>+</Text>
      </TouchableOpacity>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  card: {
    borderWidth: 1,
    borderRadius: 12,
    paddingTop: 10,
    paddingHorizontal: 10,
    paddingBottom: 12,
    marginHorizontal: 3,
    marginVertical: 4,
    position: 'relative',
    overflow: 'hidden',
  },
  cardGrid: {
    flex: 1,
    width: '100%',
    alignSelf: 'stretch',
  },
  cardRail: {
    width: '100%',
    marginHorizontal: 2,
  },
  imageWrap: {
    height: 118,
    marginBottom: 8,
    position: 'relative',
    alignItems: 'center',
    justifyContent: 'center',
    width: '100%',
    overflow: 'hidden',
    borderRadius: 14,
    paddingHorizontal: 8,
    paddingVertical: 6,
  },
  image: {
    width: '100%',
    height: '100%',
    borderRadius: 10,
  },
  imagePlaceholder: {
    width: '70%',
    height: '70%',
    borderRadius: 10,
    alignSelf: 'center',
  },
  saleBadge: {
    position: 'absolute',
    top: 8,
    left: 8,
    zIndex: 2,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 4,
    backgroundColor: '#FFD500',
  },
  saleText: { color: '#1A1C19', fontSize: 11, fontWeight: '800', letterSpacing: 0.2 },
  storeBadge: {
    position: 'absolute',
    top: 8,
    right: 8,
    zIndex: 2,
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 3,
    maxWidth: '55%',
  },
  storeText: { fontSize: 10, fontWeight: '600' },
  brand: { fontSize: 11, marginBottom: 2 },
  name: { fontSize: 14, fontWeight: '600', minHeight: 36 },
  meta: { fontSize: 11, marginTop: 2 },
  kgPrice: { fontSize: 11, fontWeight: '600', marginTop: 2 },
  only: { fontSize: 11, marginTop: 4 },
  footer: {
    marginTop: 8,
    paddingRight: 44,
    minHeight: 36,
    justifyContent: 'flex-end',
  },
  priceCol: {
    alignSelf: 'flex-start',
  },
  original: { fontSize: 12, textDecorationLine: 'line-through' },
  price: { fontSize: 16, fontWeight: '700' },
  addBtn: {
    position: 'absolute',
    right: 8,
    bottom: 8,
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  addBtnText: { color: '#fff', fontSize: 22, fontWeight: '600', marginTop: -2 },
});
