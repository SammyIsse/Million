import { apiGet, apiPost } from './client.ts';
import type {
  HomeResponse,
  ListingParams,
  ListingResponse,
  Product,
  StoreInfo,
} from './types.ts';

export async function fetchStores(): Promise<{
  stores: StoreInfo[];
  version: number;
  stores_added: Record<string, string[]>;
}> {
  return apiGet('/api/stores');
}

export async function fetchHome(params?: ListingParams): Promise<HomeResponse> {
  return apiGet('/api/home', params);
}

export async function fetchSale(params?: ListingParams): Promise<ListingResponse> {
  return apiGet('/api/sale', params);
}

export async function fetchCategory(
  slug: string,
  params?: ListingParams,
): Promise<ListingResponse> {
  return apiGet(`/api/category/${encodeURIComponent(slug)}`, params);
}

export async function fetchSearch(params: ListingParams & { q: string }): Promise<ListingResponse> {
  return apiGet('/api/search', params);
}

export async function fetchAutocomplete(
  q: string,
  stores?: string[],
): Promise<{
  suggestions: Array<{
    name: string;
    brand: string;
    price: number;
    is_sale: boolean;
    image: string;
    category: string;
  }>;
  query_suggestion: string | null;
}> {
  return apiGet('/api/autocomplete', { q, stores });
}

/** Slim priser til SCO — bilka_products er legacy [] og ignoreres. */
export async function fetchProductPrices(): Promise<{
  success: boolean;
  rema_products: Array<{
    '/product/id': string;
    '/product/price': number;
    '/product/sale_price': number | null;
    '/product/store_matches': Record<string, { price?: number }>;
  }>;
}> {
  return apiGet('/api/products');
}

export async function postCartEvent(
  event: 'add' | 'compare',
  items: Array<{ id: string; qty: number }>,
): Promise<{ ok: boolean; persisted: boolean }> {
  return apiPost('/api/cart-event', { event, items });
}

export type { Product };
