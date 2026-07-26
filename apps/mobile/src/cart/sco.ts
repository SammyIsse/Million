/**
 * SCO-motor — 1:1 port af calculateStoreComparisons + getProductPrice (script.js).
 * storePrices-nøgler er ALTID display-labels ('Rema 1000', 'Bilka', …).
 *
 * `../api/listing` importeres dynamisk (kun når `fetchPrices` ikke er givet), så denne
 * fil kan unit-testes med ren `node` uden at loade Expo-runtime-afhængigheder
 * (`expo-constants` via `config/env`) på modul-niveau.
 */
import type { StoreInfo } from '../api/types.ts';
import { applyDealPrice } from './multiDeal.ts';
import { stripStoreBrand } from './stripStoreBrand.ts';
import type { CartItem } from './types.ts';

export type ScoMissingDetail = {
  cart_id: string;
  name: string;
  image: string;
  category: string;
  weight_str: string;
  store: string;
};

export type ScoMatchedItem = {
  cart_id: string;
  name: string;
  image: string;
  price: number;
  quantity: number;
};

export type ScoStoreResult = {
  name: string;
  totalPrice: number;
  coverage: number;
  totalItems: number;
  missingDetails: ScoMissingDetail[];
};

export type ScoResult = {
  stores: ScoStoreResult[];
  linesWithoutMatches: number;
  exclusiveItems: Record<string, Array<{
    name: string;
    image: string;
    unitPrice: number;
    quantity: number;
  }>>;
  partialItems: Array<{ name: string; image: string; missingStores: string[] }>;
  matchedItemsPerStore: Record<string, ScoMatchedItem[]>;
};

type ApiProduct = {
  '/product/id': string;
  '/product/price': number;
  '/product/sale_price': number | null;
  '/product/store_matches': Record<string, { price?: number }>;
};

export function getProductPrice(product: ApiProduct): number {
  const salePrice = product['/product/sale_price'];
  const regularPrice = product['/product/price'];
  return salePrice != null && !Number.isNaN(Number(salePrice))
    ? parseFloat(String(salePrice))
    : parseFloat(String(regularPrice));
}

function legacyPrices(item: CartItem): Record<string, number> {
  const legacy: Record<string, unknown> = item as CartItem & Record<string, unknown>;
  const map: Record<string, unknown> = {
    'Rema 1000': legacy.remaPrice,
    Bilka: legacy.bilkaPrice,
    'Min Købmand': legacy.mkPrice,
    Meny: legacy.menyPrice,
    Spar: legacy.sparPrice,
  };
  const prices: Record<string, number> = {};
  for (const [label, p] of Object.entries(map)) {
    const v = Number(p);
    if (p != null && !Number.isNaN(v) && v > 0) prices[label] = v;
  }
  return prices;
}

export async function calculateStoreComparisons(
  cartItems: CartItem[],
  allStores: StoreInfo[],
  selectedStores: Set<string>,
  fetchPrices?: () => Promise<{
    success: boolean;
    rema_products: ApiProduct[];
  }>,
): Promise<ScoResult> {
  const doFetch = fetchPrices ?? (await import('../api/listing.ts')).fetchProductPrices;
  const allLabels = allStores.map((s) => s.label);
  const storeTotals = Object.fromEntries(allLabels.map((l) => [l, 0])) as Record<string, number>;
  const storeCoverage = Object.fromEntries(allLabels.map((l) => [l, 0])) as Record<string, number>;
  const missingDetails = Object.fromEntries(allLabels.map((l) => [l, [] as ScoMissingDetail[]])) as Record<
    string,
    ScoMissingDetail[]
  >;
  const matchedItemsPerStore = Object.fromEntries(
    allLabels.map((l) => [l, [] as ScoMatchedItem[]]),
  ) as Record<string, ScoMatchedItem[]>;
  let linesWithoutMatches = 0;
  const exclusiveItems = Object.fromEntries(allLabels.map((l) => [l, [] as Array<{
    name: string;
    image: string;
    unitPrice: number;
    quantity: number;
  }>])) as Record<string, Array<{
    name: string;
    image: string;
    unitPrice: number;
    quantity: number;
  }>>;
  const partialItems: ScoResult['partialItems'] = [];
  const rawPartials: Array<{ name: string; image: string; prices: Record<string, number> }> = [];

  let remaMap: Map<string, ApiProduct> | null = null;
  try {
    const data = await doFetch();
    if (data.success) {
      remaMap = new Map(data.rema_products.map((p) => [String(p['/product/id']), p]));
    }
  } catch {
    remaMap = null;
  }

  for (const cartItem of cartItems) {
    const productId = String(cartItem.id.replace('product', ''));
    const quantity = cartItem.quantity;
    const itemStore = cartItem.store || 'Rema 1000';

    const prices: Record<string, number> = {};
    if (cartItem.storePrices) {
      for (const [label, p] of Object.entries(cartItem.storePrices)) {
        const v = Number(p);
        if (!Number.isNaN(v) && v > 0) prices[label] = v;
      }
    } else {
      Object.assign(prices, legacyPrices(cartItem));
      const inferredStore =
        itemStore ||
        (productId.startsWith('bilka_')
          ? 'Bilka'
          : productId.startsWith('mk_')
            ? 'Min Købmand'
            : 'Rema 1000');
      if (
        inferredStore !== 'Rema 1000' &&
        prices['Rema 1000'] != null &&
        prices[inferredStore] == null
      ) {
        prices[inferredStore] = prices['Rema 1000'];
        delete prices['Rema 1000'];
      }
      if (Object.keys(prices).length === 0 && cartItem.price != null && Number(cartItem.price) > 0) {
        prices[inferredStore] = Number(cartItem.price);
      }
    }

    const remaProduct = remaMap ? remaMap.get(productId) : null;
    if (remaProduct) {
      if (prices['Rema 1000'] == null) {
        prices['Rema 1000'] = getProductPrice(remaProduct);
      }
      const storeMatches = remaProduct['/product/store_matches'] || {};
      for (const [key, match] of Object.entries(storeMatches)) {
        const storeEntry = allStores.find((s) => s.key === key);
        if (storeEntry && prices[storeEntry.label] == null) {
          const v = parseFloat(String(match.price));
          if (!Number.isNaN(v) && v > 0) prices[storeEntry.label] = v;
        }
      }
    }

    for (const [label, p] of Object.entries(prices)) {
      if (selectedStores.has(label) && !Number.isNaN(p)) {
        storeCoverage[label] += 1;
        const dealStr = cartItem.storeMultiDeals ? cartItem.storeMultiDeals[label] || '' : '';
        storeTotals[label] = (storeTotals[label] || 0) + applyDealPrice(p, quantity, dealStr);
        matchedItemsPerStore[label].push({
          cart_id: cartItem.id,
          name: stripStoreBrand(cartItem.name || 'Vare'),
          image: cartItem.image || '',
          price: p,
          quantity,
        });
      }
    }

    for (const label of selectedStores) {
      if (prices[label] == null || Number.isNaN(Number(prices[label])) || Number(prices[label]) <= 0) {
        missingDetails[label].push({
          cart_id: cartItem.id,
          name: stripStoreBrand(cartItem.name || 'Vare'),
          image: cartItem.image || '',
          category: cartItem.category || '',
          weight_str: cartItem.unitMeasure || '',
          store: label,
        });
      }
    }

    const availableCount = Object.values(prices).filter((p) => p != null && !Number.isNaN(p)).length;
    if (availableCount < 2) linesWithoutMatches += 1;

    if (availableCount === 1) {
      const [onlyLabel, onlyPrice] = Object.entries(prices)[0];
      if (exclusiveItems[onlyLabel]) {
        exclusiveItems[onlyLabel].push({
          name: cartItem.name || 'Vare',
          image: cartItem.image || '',
          unitPrice: onlyPrice,
          quantity,
        });
      }
    }

    const availableInSelected = Object.entries(prices).filter(
      ([label, p]) => selectedStores.has(label) && !Number.isNaN(Number(p)) && Number(p) > 0,
    ).length;
    if (availableInSelected > 0 && availableInSelected < selectedStores.size) {
      rawPartials.push({
        name: stripStoreBrand(cartItem.name || 'Vare'),
        image: cartItem.image || '',
        prices,
      });
    }
  }

  const totalCartItems = cartItems.length;
  const stores: ScoStoreResult[] = allLabels
    .filter((l) => selectedStores.has(l) && (storeTotals[l] > 0 || storeCoverage[l] > 0))
    .map((l) => ({
      name: l,
      totalPrice: parseFloat(storeTotals[l].toFixed(2)),
      coverage: storeCoverage[l],
      totalItems: totalCartItems,
      missingDetails: missingDetails[l],
    }));

  const comparisonStores = new Set(stores.map((s) => s.name));
  for (const raw of rawPartials) {
    const missingStores = [...comparisonStores].filter((label) => {
      const p = raw.prices[label];
      return p == null || Number.isNaN(Number(p)) || Number(p) <= 0;
    });
    if (missingStores.length > 0) {
      partialItems.push({ name: raw.name, image: raw.image, missingStores });
    }
  }

  return { stores, linesWithoutMatches, exclusiveItems, partialItems, matchedItemsPerStore };
}

/** Sort: coverage desc, then totalPrice asc — spejler showReference. */
export function sortScoStores(stores: ScoStoreResult[]): ScoStoreResult[] {
  return stores.slice().sort((a, b) => {
    if (b.coverage !== a.coverage) return b.coverage - a.coverage;
    return a.totalPrice - b.totalPrice;
  });
}

export const SCO_TOP_N = 5;
