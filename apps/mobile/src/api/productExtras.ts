/** Prishistorik + næringsindhold — docs/native-app.md §4.5 / §4.6. */
import { apiGet } from './client';

export type PriceHistoryPoint = { price: number; date: string };

export type PriceHistoryResponse = {
  success: boolean;
  history: PriceHistoryPoint[];
  history_by_store: Record<string, PriceHistoryPoint[]>;
  error?: string;
};

export type NutritionRow = { label: string; value: string };

export type Nutrition = {
  per: string;
  rows: NutritionRow[];
  ingredients: string | null;
  source: 'rema' | 'salling' | 'off';
};

export type NutritionResponse = {
  success: boolean;
  nutrition: Nutrition | null;
  error?: string;
};

export async function fetchPriceHistory(id: string): Promise<PriceHistoryResponse> {
  return apiGet(`/api/price-history/${encodeURIComponent(id)}`);
}

export async function fetchNutrition(id: string): Promise<NutritionResponse> {
  return apiGet(`/api/nutrition/${encodeURIComponent(id)}`);
}
