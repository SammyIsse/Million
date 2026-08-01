import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, FlatList, StyleSheet, Text, TextInput, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { fetchRecipes, type Recipe } from '../api/recipes';
import { RecipeCard } from '../components/RecipeCard';
import { TabScreenBody } from '../components/ScreenBody';
import { useTheme } from '../theme/ThemeContext';
import type { RootStackParamList } from '../navigation/types';

/** Opskrift-fanen: EGEN søgning, adskilt fra SearchScreen (produkter).
 * Web-paritet med templates/opskrifter.html - søger kun i den allerede
 * hentede opskrift-liste (client-side substring på titel), aldrig produkter. */
export function RecipesScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { colors } = useTheme();
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchRecipes()
      .then((res) => {
        if (!cancelled) setRecipes(res.success ? res.recipes || [] : []);
      })
      .catch(() => {
        if (!cancelled) setRecipes([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return recipes;
    return recipes.filter((r) => r.title.toLowerCase().includes(query));
  }, [recipes, q]);

  return (
    <TabScreenBody style={{ backgroundColor: colors.bg }}>
      <TextInput
        value={q}
        onChangeText={setQ}
        placeholder="Søg efter opskrifter…"
        placeholderTextColor={colors.textMuted}
        style={[
          styles.input,
          { backgroundColor: colors.surface, color: colors.text, borderColor: colors.border },
        ]}
      />
      {loading ? (
        <ActivityIndicator color={colors.primary} style={{ marginTop: 20 }} />
      ) : filtered.length === 0 ? (
        <View style={{ padding: 16 }}>
          <Text style={{ color: colors.textMuted }}>
            {q.trim() ? 'Ingen opskrifter matcher din søgning.' : 'Ingen opskrifter endnu.'}
          </Text>
        </View>
      ) : (
        <FlatList
          style={{ flex: 1 }}
          data={filtered}
          keyExtractor={(r) => String(r.id)}
          numColumns={2}
          columnWrapperStyle={{ paddingHorizontal: 2 }}
          contentContainerStyle={{ padding: 4 }}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator
          renderItem={({ item }) => (
            <RecipeCard
              recipe={item}
              onPress={(r) => navigation.navigate('RecipeDetail', { recipeId: r.id })}
            />
          )}
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
});
