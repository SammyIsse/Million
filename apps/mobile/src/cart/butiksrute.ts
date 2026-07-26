/**
 * Butiksrute — 1:1 port af showButiksrute-gruppering (uden multi-deal).
 * SCO = én butik; rute = billigste butik pr. vare blandt selected.
 */
import type { StoreInfo } from '../api/types';
import { calculateStoreComparisons } from './sco';
import { stripStoreBrand } from './stripStoreBrand';
import type { CartItem } from './types';

export type RouteItem = {
  item: CartItem;
  price: number;
  displayName: string;
};

export type RouteStoreGroup = {
  store: string;
  items: RouteItem[];
  subtotal: number;
};

export type RouteResult = {
  groups: RouteStoreGroup[];
  routeTotal: number;
  singleCheapest: { name: string; totalPrice: number } | null;
  savings: number;
};

const isValidPrice = (p: unknown): p is number =>
  p != null && !Number.isNaN(Number(p)) && Number(p) > 0;

export async function calculateButiksrute(
  cartItems: CartItem[],
  allStores: StoreInfo[],
  selectedStores: Set<string>,
): Promise<RouteResult> {
  const { stores } = await calculateStoreComparisons(cartItems, allStores, selectedStores);

  const grouped: Record<string, { items: RouteItem[]; subtotal: number }> = {};

  for (const item of cartItems) {
    let prices: Record<string, number> = {};
    if (item.storePrices && Object.keys(item.storePrices).length > 0) {
      prices = { ...item.storePrices };
    } else {
      const legacy = item as CartItem & Record<string, unknown>;
      const legacyMap: Record<string, unknown> = {
        'Rema 1000': legacy.remaPrice,
        Bilka: legacy.bilkaPrice,
        'Min Købmand': legacy.mkPrice,
        Meny: legacy.menyPrice,
        Spar: legacy.sparPrice,
      };
      for (const [lbl, p] of Object.entries(legacyMap)) {
        if (p != null) prices[lbl] = Number(p);
      }
      if (Object.keys(prices).length === 0) {
        prices[item.store || 'Rema 1000'] = item.price;
      }
    }

    let bestStore: string | null = null;
    let bestPrice = Infinity;
    for (const [store, p] of Object.entries(prices)) {
      if (isValidPrice(p) && selectedStores.has(store) && Number(p) < bestPrice) {
        bestPrice = Number(p);
        bestStore = store;
      }
    }
    if (!bestStore) {
      for (const [store, p] of Object.entries(prices)) {
        if (isValidPrice(p) && Number(p) < bestPrice) {
          bestPrice = Number(p);
          bestStore = store;
        }
      }
    }
    const store = bestStore || item.store || 'Ukendt butik';
    const price = bestPrice === Infinity ? item.price || 0 : bestPrice;
    if (!grouped[store]) grouped[store] = { items: [], subtotal: 0 };
    grouped[store].items.push({
      item,
      price,
      displayName: stripStoreBrand(item.name),
    });
    grouped[store].subtotal += price * (item.quantity || 1);
  }

  const routeTotal = Object.values(grouped).reduce((s, g) => s + g.subtotal, 0);
  const singleCheapest = stores.length
    ? [...stores].sort((a, b) => a.totalPrice - b.totalPrice)[0]
    : null;
  const savings = singleCheapest ? singleCheapest.totalPrice - routeTotal : 0;

  const groups: RouteStoreGroup[] = Object.entries(grouped)
    .sort((a, b) => b[1].subtotal - a[1].subtotal)
    .map(([store, group]) => ({ store, ...group }));

  return {
    groups,
    routeTotal,
    singleCheapest: singleCheapest
      ? { name: singleCheapest.name, totalPrice: singleCheapest.totalPrice }
      : null,
    savings: savings > 0.05 ? savings : 0,
  };
}
