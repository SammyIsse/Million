import React from 'react';
import { Image, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import type { Recipe } from '../api/recipes';
import { useTheme } from '../theme/ThemeContext';

type Props = {
  recipe: Recipe;
  onPress: (recipe: Recipe) => void;
};

/** Visuelt beslægtet med ProductCard, men egen komponent - en opskrift har
 * ingen butik/store_matches/kurv-knap på selve kortet (web-paritet, se
 * templates/macros/recipe_card.html for hvorfor .product ikke genbruges). */
export function RecipeCard({ recipe, onPress }: Props) {
  const { colors, isDark } = useTheme();
  const salePct = recipe.sale_ratio > 0 ? Math.floor(recipe.sale_ratio * 100) : null;

  return (
    <TouchableOpacity
      activeOpacity={0.9}
      delayPressIn={80}
      onPress={() => onPress(recipe)}
      style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}
    >
      <View style={[styles.imageWrap, { backgroundColor: isDark ? '#252825' : '#F3F5F0' }]}>
        {salePct ? (
          <View style={styles.saleBadge}>
            <Text style={styles.saleText}>{salePct}% på tilbud</Text>
          </View>
        ) : null}
        {recipe.image_url ? (
          <Image source={{ uri: recipe.image_url }} style={styles.image} resizeMode="cover" />
        ) : (
          <View style={[styles.imagePlaceholder, { backgroundColor: colors.border }]} />
        )}
      </View>
      <Text style={[styles.name, { color: colors.text }]} numberOfLines={2}>
        {recipe.title}
      </Text>
      {recipe.total_ingredient_count ? (
        <Text style={[styles.meta, { color: colors.textMuted }]}>
          {recipe.matched_ingredient_count}/{recipe.total_ingredient_count} ingredienser fundet
        </Text>
      ) : null}
      <Text style={[styles.price, { color: colors.text }]}>
        {recipe.cheapest_total_price ? `~${recipe.cheapest_total_price.toFixed(2)} kr` : 'Pris ukendt'}
      </Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  card: {
    flex: 1,
    borderWidth: 1,
    borderRadius: 12,
    paddingTop: 10,
    paddingHorizontal: 10,
    paddingBottom: 12,
    marginHorizontal: 3,
    marginVertical: 4,
    overflow: 'hidden',
  },
  imageWrap: {
    height: 118,
    marginBottom: 8,
    borderRadius: 14,
    overflow: 'hidden',
    position: 'relative',
  },
  image: { width: '100%', height: '100%' },
  imagePlaceholder: { width: '100%', height: '100%' },
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
  name: { fontSize: 14, fontWeight: '600', minHeight: 36 },
  meta: { fontSize: 11, marginTop: 4 },
  price: { fontSize: 16, fontWeight: '700', marginTop: 6 },
});
