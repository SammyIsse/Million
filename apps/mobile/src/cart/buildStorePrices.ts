/**
 * Bygger storePrices/storeMultiDeals med LABEL-nøgler (ikke store-keys) fra en
 * produkt-JSON — genbruges af ProductCard og ProductDetailScreen.
 */
import type { Product, StoreInfo } from '../api/types';

export function buildStorePrices(
  product: Product,
  catalog: StoreInfo[],
): { storePrices: Record<string, number>; storeMultiDeals: Record<string, string> } {
  const storePrices: Record<string, number> = {};
  const storeMultiDeals: Record<string, string> = {};
  const labelByKey = new Map(catalog.map((s) => [s.key, s.label]));
  const remaLabel = labelByKey.get('rema') || 'Rema 1000';

  if (product.rema_price != null && product.rema_price > 0) {
    storePrices[remaLabel] = product.rema_price;
    if (product.multi_deal) storeMultiDeals[remaLabel] = product.multi_deal;
  }

  for (const [key, match] of Object.entries(product.store_matches || {})) {
    if (match.price == null || match.price <= 0) continue;
    const label = labelByKey.get(key) || match.name || key;
    storePrices[label] = match.price;
    if (match.multi_deal) storeMultiDeals[label] = match.multi_deal;
  }

  return { storePrices, storeMultiDeals };
}
