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

/**
 * Web-paritet (static/js/script.js _priceHistoryCache/_nutritionCache):
 * begge endepunkter er edge-cachede 600s server-side, men uden en klient-
 * cache genhenter React Navigation dem alligevel ved hver mount af
 * ProductDetailScreen — inkl. ved at navigere tilbage til samme produkt.
 * Modul-niveau cache pr. produkt-id for app'ens levetid, ligesom web.
 */
const priceHistoryCache = new Map<string, PriceHistoryResponse>();
const nutritionCache = new Map<string, NutritionResponse>();

export async function fetchPriceHistory(id: string): Promise<PriceHistoryResponse> {
  const cached = priceHistoryCache.get(id);
  if (cached) return cached;
  const res = await apiGet<PriceHistoryResponse>(`/api/price-history/${encodeURIComponent(id)}`);
  if (res.success) priceHistoryCache.set(id, res);
  return res;
}

export async function fetchNutrition(id: string): Promise<NutritionResponse> {
  const cached = nutritionCache.get(id);
  if (cached) return cached;
  const res = await apiGet<NutritionResponse>(`/api/nutrition/${encodeURIComponent(id)}`);
  if (res.success) nutritionCache.set(id, res);
  return res;
}
