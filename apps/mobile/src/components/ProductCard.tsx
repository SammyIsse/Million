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
  const { colors } = useTheme();
  const { addItem } = useCart();
  const { catalog } = useStoreCatalog();
  const onSale = product.is_sale || product.is_any_sale;

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
      <View style={styles.imageWrap}>
        {onSale ? (
          <View style={[styles.saleBadge, { backgroundColor: colors.sale }]}>
            <Text style={styles.saleText}>Tilbud</Text>
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
      {!product.has_match ? (
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
      <TouchableOpacity
        accessibilityLabel="Tilføj til kurv"
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
    height: 110,
    marginBottom: 8,
    position: 'relative',
    alignItems: 'center',
    justifyContent: 'center',
    width: '100%',
    overflow: 'hidden',
  },
  image: {
    width: '100%',
    height: '100%',
  },
  imagePlaceholder: {
    width: '70%',
    height: '70%',
    borderRadius: 8,
    alignSelf: 'center',
  },
  saleBadge: {
    position: 'absolute',
    top: 4,
    left: 4,
    zIndex: 2,
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  saleText: { color: '#fff', fontSize: 11, fontWeight: '700' },
  storeBadge: {
    position: 'absolute',
    top: 4,
    right: 4,
    zIndex: 2,
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
    maxWidth: '55%',
  },
  storeText: { fontSize: 10, fontWeight: '600' },
  brand: { fontSize: 11, marginBottom: 2 },
  name: { fontSize: 14, fontWeight: '600', minHeight: 36 },
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
