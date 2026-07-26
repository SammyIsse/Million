/** Rich lokal kurv-item (docs/native-app.md §7.1) + cloud compact (auth.js 1:1). */

export type CartItem = {
  id: string;
  name: string;
  store: string;
  price: number;
  storePrices: Record<string, number>;
  storeMultiDeals: Record<string, string>;
  image: string;
  category: string;
  unitMeasure: string;
  kgPrice: string;
  multiDeal?: string;
  quantity: number;
};

/** Kompakt cloud-form {p,q,n,i,s,pr} — spejler auth.js cartToRows. */
export type CompactCartItem = {
  p: string;
  q: number;
  n: string;
  i: string;
  s: string;
  pr: number | null;
};

export function cartToRows(cart: CartItem[]): CompactCartItem[] {
  const out: CompactCartItem[] = [];
  for (const it of cart || []) {
    if (!it || !it.id) continue;
    let q = parseInt(String(it.quantity), 10);
    if (Number.isNaN(q) || q < 1) q = 1;
    if (q > 99) q = 99;
    out.push({
      p: String(it.id).slice(0, 64),
      q,
      n: (it.name || '').slice(0, 120),
      i: (it.image || '').slice(0, 300),
      s: (it.store || '').slice(0, 40),
      pr: it.price != null && !Number.isNaN(Number(it.price)) ? Number(it.price) : null,
    });
  }
  return out.slice(0, 100);
}

export function rowsToCart(rows: CompactCartItem[]): CartItem[] {
  return (rows || []).map((r) => ({
    id: r.p,
    name: r.n || '',
    image: r.i || '',
    store: r.s || '',
    price: r.pr != null ? r.pr : 0,
    quantity: r.q || 1,
    storePrices: {},
    storeMultiDeals: {},
    category: 'Andre varer',
    unitMeasure: '',
    kgPrice: '',
  }));
}

/** Login-merge: server først, lokal overskriver rige felter, qty = max. */
export function mergeCarts(localCart: CartItem[], serverRows: CompactCartItem[]): CartItem[] {
  const byId: Record<string, CartItem> = {};
  rowsToCart(serverRows).forEach((it) => {
    byId[it.id] = it;
  });
  for (const it of localCart || []) {
    if (!it || !it.id) continue;
    const prevQ = byId[it.id] ? byId[it.id].quantity || 1 : 0;
    byId[it.id] = {
      ...(byId[it.id] || ({} as CartItem)),
      ...it,
      quantity: Math.max(it.quantity || 1, prevQ),
    };
  }
  return Object.keys(byId).map((k) => byId[k]);
}
