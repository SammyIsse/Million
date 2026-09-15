import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { postCartEvent } from '../api/listing';
import type { CartItem } from './types';

const CART_KEY = 'madshopper_cart';

/**
 * Web-paritet (static/js/script.js queueCartEvent, tilføjet 2026-08-17):
 * debounce populæritets-registreringen 600ms, så hurtige tilføjelser batches
 * til ét kald i stedet for ét pr. klik. Uden denne kunne "læg opskrift i kurv"
 * (N kald i træk) og hurtige klik ramme den delte rate-grænse på
 * /api/cart-event (20/min pr. IP, delt med webbens trafik).
 *
 * Siden 15-09-2026 samler køen også FORSKELLIGE varer i ét kald (før: én
 * debounce pr. vare, så ti forskellige varer = ti kald - på web målt til 429
 * i browser-test). API'et tager op til 50 varer pr. kald; loftet på
 * ventetiden sikrer, at vedvarende klik ikke skubber afsendelsen uendeligt.
 */
const CART_EVENT_DEBOUNCE_MS = 600;
const CART_EVENT_MAX_WAIT_MS = 3000;
const CART_EVENT_MAX_ITEMS = 50;
const cartEventQueue = new Map<'add' | 'compare', Map<string, number>>();
let cartEventTimer: ReturnType<typeof setTimeout> | null = null;
let cartEventFirstQueuedAt = 0;

function flushCartEvents() {
  if (cartEventTimer) clearTimeout(cartEventTimer);
  cartEventTimer = null;
  cartEventFirstQueuedAt = 0;
  cartEventQueue.forEach((items, eventType) => {
    const all = Array.from(items, ([id, qty]) => ({ id, qty }));
    for (let i = 0; i < all.length; i += CART_EVENT_MAX_ITEMS) {
      void postCartEvent(eventType, all.slice(i, i + CART_EVENT_MAX_ITEMS)).catch(() => {});
    }
  });
  cartEventQueue.clear();
}

function queueCartEvent(eventType: 'add' | 'compare', id: string, qty: number) {
  let items = cartEventQueue.get(eventType);
  if (!items) {
    items = new Map();
    cartEventQueue.set(eventType, items);
  }
  items.set(id, (items.get(id) || 0) + qty);

  const now = Date.now();
  if (!cartEventFirstQueuedAt) cartEventFirstQueuedAt = now;
  if (cartEventTimer) clearTimeout(cartEventTimer);
  const wait = Math.min(
    CART_EVENT_DEBOUNCE_MS,
    Math.max(0, cartEventFirstQueuedAt + CART_EVENT_MAX_WAIT_MS - now),
  );
  cartEventTimer = setTimeout(flushCartEvents, wait);
}

type SyncFn = (cart: CartItem[]) => void;
type SyncListener = SyncFn | null;

type AddItemOptions = {
  /**
   * Springer den enkeltvise populæritets-registrering over. Bruges når
   * kalderen selv sender ét batched /api/cart-event-kald for flere varer på
   * én gang (fx "læg alle opskrift-varer i kurv") — spejler webbens
   * addRecipeToCart, som heller ikke går gennem queueCartEvent.
   */
  silent?: boolean;
};

type CartContextValue = {
  items: CartItem[];
  count: number;
  addItem: (
    item: Omit<CartItem, 'quantity'> & { quantity?: number },
    opts?: AddItemOptions,
  ) => void;
  updateQuantity: (id: string, quantity: number) => void;
  removeItem: (id: string) => void;
  clearCart: () => void;
  applyFromServer: (items: CartItem[]) => void;
  replaceItem: (oldId: string, next: CartItem) => void;
  setSyncListener: (fn: SyncListener) => void;
  addSyncListener: (fn: SyncFn) => () => void;
  notify: () => void;
};

const CartContext = createContext<CartContextValue | null>(null);

export function CartProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<CartItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const syncListeners = useRef(new Set<SyncFn>());
  const itemsRef = useRef(items);
  itemsRef.current = items;

  useEffect(() => {
    AsyncStorage.getItem(CART_KEY).then((raw) => {
      if (raw) {
        try {
          setItems(JSON.parse(raw) as CartItem[]);
        } catch {
          /* ignore */
        }
      }
      setLoaded(true);
    });
  }, []);

  useEffect(() => {
    if (!loaded) return;
    void AsyncStorage.setItem(CART_KEY, JSON.stringify(items));
  }, [items, loaded]);

  const fireSync = useCallback((next: CartItem[]) => {
    for (const fn of syncListeners.current) fn(next);
  }, []);

  const notify = useCallback(() => {
    fireSync(itemsRef.current);
  }, [fireSync]);

  const addItem = useCallback(
    (item: Omit<CartItem, 'quantity'> & { quantity?: number }, opts?: AddItemOptions) => {
      const qty = Math.max(1, item.quantity ?? 1);
      setItems((prev) => {
        const idx = prev.findIndex((p) => p.id === item.id);
        let next: CartItem[];
        if (idx >= 0) {
          next = [...prev];
          next[idx] = { ...next[idx], quantity: next[idx].quantity + qty };
        } else {
          next = [
            ...prev,
            {
              ...item,
              store: item.store || 'Rema 1000',
              category: item.category || 'Andre varer',
              storePrices: item.storePrices || {},
              storeMultiDeals: item.storeMultiDeals || {},
              quantity: qty,
            },
          ];
        }
        itemsRef.current = next;
        fireSync(next);
        return next;
      });
      if (!opts?.silent) {
        const rawId = item.id.replace(/^product/, '');
        queueCartEvent('add', rawId, qty);
      }
    },
    [fireSync],
  );

  const updateQuantity = useCallback(
    (id: string, quantity: number) => {
      setItems((prev) => {
        const next =
          quantity <= 0
            ? prev.filter((p) => p.id !== id)
            : prev.map((p) => (p.id === id ? { ...p, quantity } : p));
        itemsRef.current = next;
        fireSync(next);
        return next;
      });
    },
    [fireSync],
  );

  const removeItem = useCallback(
    (id: string) => {
      setItems((prev) => {
        const next = prev.filter((p) => p.id !== id);
        itemsRef.current = next;
        fireSync(next);
        return next;
      });
    },
    [fireSync],
  );

  const clearCart = useCallback(() => {
    const next: CartItem[] = [];
    setItems(next);
    itemsRef.current = next;
    fireSync(next);
  }, [fireSync]);

  /** Spejler CartBridge.applyFromServer — kalder IKKE notify. */
  const applyFromServer = useCallback((next: CartItem[]) => {
    setItems(next);
    itemsRef.current = next;
  }, []);

  const replaceItem = useCallback(
    (oldId: string, nextItem: CartItem) => {
      setItems((prev) => {
        const next = prev.map((p) => (p.id === oldId ? nextItem : p));
        itemsRef.current = next;
        fireSync(next);
        return next;
      });
    },
    [fireSync],
  );

  const setSyncListener = useCallback((fn: SyncListener) => {
    syncListeners.current.clear();
    if (fn) syncListeners.current.add(fn);
  }, []);

  const addSyncListener = useCallback((fn: SyncFn) => {
    syncListeners.current.add(fn);
    return () => {
      syncListeners.current.delete(fn);
    };
  }, []);

  const count = useMemo(
    () => items.reduce((sum, i) => sum + i.quantity, 0),
    [items],
  );

  const value = useMemo(
    () => ({
      items,
      count,
      addItem,
      updateQuantity,
      removeItem,
      clearCart,
      applyFromServer,
      replaceItem,
      setSyncListener,
      addSyncListener,
      notify,
    }),
    [
      items,
      count,
      addItem,
      updateQuantity,
      removeItem,
      clearCart,
      applyFromServer,
      replaceItem,
      setSyncListener,
      addSyncListener,
      notify,
    ],
  );

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart() {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error('useCart without CartProvider');
  return ctx;
}
