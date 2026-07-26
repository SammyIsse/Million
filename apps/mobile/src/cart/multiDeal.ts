/**
 * Multi-deal — 1:1 port af parseMultiDeal / applyDealPrice fra script.js.
 * Anvendes KUN i SCO-totals (ikke kurv-footer / butiksrute).
 */

export type MultiDeal = { qty: number; totalPrice: number };

export function parseMultiDeal(
  dealStr: string | null | undefined,
): MultiDeal | null {
  if (!dealStr) return null;
  const m = String(dealStr).match(/(\d+)\s+for\s+([\d.,]+)/i);
  if (!m) return null;
  const qty = parseInt(m[1], 10);
  const totalPrice = parseFloat(m[2].replace(',', '.'));
  return qty > 1 && !Number.isNaN(totalPrice) && totalPrice > 0
    ? { qty, totalPrice }
    : null;
}

export function applyDealPrice(
  regularPrice: number,
  quantity: number,
  dealStr: string | null | undefined,
): number {
  const deal = parseMultiDeal(dealStr);
  if (!deal) return regularPrice * quantity;
  const bundles = Math.floor(quantity / deal.qty);
  return bundles * deal.totalPrice + (quantity % deal.qty) * regularPrice;
}
