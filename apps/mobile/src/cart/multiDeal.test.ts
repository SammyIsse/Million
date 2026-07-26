/**
 * Unit tests for multi-deal (docs/native-app.md §7.4) — spejler script.js.
 * Kør: node --experimental-strip-types src/cart/multiDeal.test.ts
 */
import { applyDealPrice, parseMultiDeal } from './multiDeal.ts';

function assert(cond: boolean, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(parseMultiDeal('2 for 30')?.qty === 2, 'parse qty');
assert(parseMultiDeal('2 for 30')?.totalPrice === 30, 'parse price');
assert(parseMultiDeal('3 for 25,50')?.totalPrice === 25.5, 'comma decimal');
assert(parseMultiDeal('') === null, 'empty');
assert(parseMultiDeal('tilbud') === null, 'invalid');
assert(parseMultiDeal('1 for 10') === null, 'qty must be > 1');

assert(applyDealPrice(20, 2, '2 for 30') === 30, 'exact bundle');
assert(applyDealPrice(20, 3, '2 for 30') === 50, 'bundle + remainder');
assert(applyDealPrice(20, 1, '2 for 30') === 20, 'under bundle');
assert(applyDealPrice(20, 4, '2 for 30') === 60, 'two bundles');
assert(applyDealPrice(20, 2, null) === 40, 'no deal');

console.log('multiDeal tests OK');
