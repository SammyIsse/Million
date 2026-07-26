/**
 * SCO unit tests — coverage→price sort + multi-deal kun i SCO-totals.
 * Kør: node --experimental-strip-types src/cart/sco.test.ts
 */
import { applyDealPrice } from './multiDeal.ts';
import { calculateStoreComparisons, sortScoStores } from './sco.ts';
import type { CartItem } from './types.ts';
import type { StoreInfo } from '../api/types.ts';

function assert(cond: boolean, msg: string) {
  if (!cond) throw new Error(msg);
}

const stores: StoreInfo[] = [
  { key: 'rema', label: 'Rema 1000', logo: '' },
  { key: 'bilka', label: 'Bilka', logo: '' },
  { key: 'netto', label: 'Netto', logo: '' },
];

const cart: CartItem[] = [
  {
    id: 'product1',
    name: 'Mælk',
    store: 'Rema 1000',
    price: 10,
    storePrices: { 'Rema 1000': 10, Bilka: 12, Netto: 11 },
    storeMultiDeals: { Bilka: '2 for 18' },
    image: '',
    category: 'Køl',
    unitMeasure: '1 L',
    kgPrice: '',
    quantity: 2,
  },
  {
    id: 'product2',
    name: 'Brød',
    store: 'Rema 1000',
    price: 20,
    storePrices: { 'Rema 1000': 20, Bilka: 15 },
    storeMultiDeals: {},
    image: '',
    category: 'Brød',
    unitMeasure: '',
    kgPrice: '',
    quantity: 1,
  },
];

const selected = new Set(['Rema 1000', 'Bilka', 'Netto']);

const fakeFetch = async () => ({ success: true as const, rema_products: [] });

const result = await calculateStoreComparisons(cart, stores, selected, fakeFetch);
const sorted = sortScoStores(result.stores);

assert(sorted.length === 3, '3 stores');
// Rema coverage 2, Bilka 2, Netto 1 → Rema/Bilka før Netto
assert(sorted[2].name === 'Netto', 'Netto lowest coverage last');
assert(sorted[0].coverage >= sorted[1].coverage, 'coverage desc');

const bilka = result.stores.find((s) => s.name === 'Bilka')!;
// Mælk 2× med 2 for 18 → 18; Brød 15 → total 33
assert(bilka.totalPrice === 33, `Bilka deal total got ${bilka.totalPrice}`);
assert(applyDealPrice(12, 2, '2 for 18') === 18, 'deal helper');

const rema = result.stores.find((s) => s.name === 'Rema 1000')!;
// 10*2 + 20 = 40 (ingen deal)
assert(rema.totalPrice === 40, `Rema total got ${rema.totalPrice}`);

console.log('sco tests OK');
