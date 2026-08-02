/** Opskrifter — spejler app.py /api/recipes + /api/recipes/<id> (web-paritet). */
import { apiGet, apiPost } from './client';

export type Recipe = {
  id: number;
  title: string;
  image_url: string;
  servings: number | null;
  total_time_minutes: number | null;
  source_name: string;
  source_url: string;
  cheapest_total_price: number | null;
  matched_ingredient_count: number;
  total_ingredient_count: number;
  ingredients_on_sale_count: number;
  sale_ratio: number;
};

export type RecipeListResponse = {
  success: boolean;
  recipes: Recipe[];
};

export type NutritionRow = { label: string; value: string };

export type Nutrition = {
  per: string;
  rows: NutritionRow[];
  ingredients: string | null;
  source: 'rema' | 'salling' | 'off';
};

export type NutritionSummary = {
  energi: string | null;
  protein: string | null;
  fedt: string | null;
  kulhydrat: string | null;
};

export type MatchedProduct = {
  id: string;
  name: string;
  image: string;
  price: number;
  is_sale: boolean;
  store: string;
  category: string;
  unit_measure: string;
  weight_g: number | null;
  stk_count: number | null;
  kg_price: number | null;
  multi_deal: string;
  store_prices: Record<string, number>;
  nutrition: Nutrition | null;
  nutrition_summary: NutritionSummary | null;
};

/** Kildens egen schema.org NutritionInformation (recipe_importer.py) -
 * autoritativ, vises i stedet for vores eget estimat når den findes. */
export type NutritionSource = {
  serving_size?: string;
  calories?: string;
  protein?: string;
  fat?: string;
  carbohydrate?: string;
  fiber?: string;
};

/** Vores eget estimat (app.py::_recipe_nutrition_estimate) - kun summeret
 * over ingredienser med både en vægt-/volumenenhed OG næringsdata, se
 * contributing_ingredient_count. Skaleres LINEÆRT med personer client-side
 * (samme princip som RecipeDetailScreen's scale-beregning for pris). */
export type NutritionEstimate = {
  kcal: number;
  protein: number;
  fedt: number;
  kulhydrat: number;
  contributing_ingredient_count: number;
  total_ingredient_count: number;
};

export type RecipeIngredient = {
  id: number;
  position: number;
  raw_text: string;
  quantity: number | null;
  unit: string;
  ingredient_name: string;
  matched_product_id: string | null;
  match_confidence: number | null;
  matched_product: MatchedProduct | null;
  candidates: MatchedProduct[];
};

export type RecipeDetail = {
  id: number;
  title: string;
  image_url: string;
  servings: number | null;
  total_time_minutes: number | null;
  instructions: string[];
  source_name: string;
  source_url: string;
  nutrition_source: NutritionSource | null;
  nutrition_estimate: NutritionEstimate | null;
};

export type RecipeSnapshot = {
  recipe_id: number;
  cheapest_total_price: number | null;
  matched_ingredient_count: number;
  total_ingredient_count: number;
  ingredients_on_sale_count: number;
};

export type RecipeDetailResponse = {
  success: boolean;
  recipe: RecipeDetail | null;
  ingredients: RecipeIngredient[];
  snapshot: RecipeSnapshot | null;
};

export async function fetchRecipes(): Promise<RecipeListResponse> {
  return apiGet('/api/recipes');
}

export async function fetchRecipeDetail(id: number): Promise<RecipeDetailResponse> {
  return apiGet(`/api/recipes/${id}`);
}

export async function recordRecipeClick(id: number): Promise<void> {
  // Fire-and-forget, samme fail-safe som web (templates/opskrift.html) - en
  // fejlet klik-registrering må aldrig blokere eller fejle skærmen.
  try {
    await apiPost('/api/recipe-click', { recipe_id: id });
  } catch {
    // stille fail-safe
  }
}
