import { env } from '../config/env.ts';
import type { ListingParams } from './types.ts';

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function buildQuery(params?: ListingParams): string {
  if (!params) return '';
  const q = new URLSearchParams();
  if (params.stores?.length) q.set('stores', params.stores.join(','));
  if (params.sort) q.set('sort', params.sort);
  if (params.min_price != null) q.set('min_price', String(params.min_price));
  if (params.max_price != null) q.set('max_price', String(params.max_price));
  if (params.sale) q.set('sale', 'true');
  if (params.organic) q.set('organic', 'true');
  if (params.lactose) q.set('lactose', 'true');
  if (params.min_weight != null) q.set('min_weight', String(params.min_weight));
  if (params.max_weight != null) q.set('max_weight', String(params.max_weight));
  if (params.page != null) q.set('page', String(params.page));
  if (params.subcategory) q.set('subcategory', params.subcategory);
  if (params.q) q.set('q', params.q);
  const s = q.toString();
  return s ? `?${s}` : '';
}

export async function apiGet<T>(path: string, params?: ListingParams): Promise<T> {
  const url = `${env.apiBaseUrl}${path}${buildQuery(params)}`;
  const res = await fetch(url, {
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) {
    throw new ApiError(`HTTP ${res.status} for ${path}`, res.status);
  }
  return (await res.json()) as T;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const url = `${env.apiBaseUrl}${path}`;
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError(`HTTP ${res.status} for ${path}`, res.status);
  }
  return (await res.json()) as T;
}
