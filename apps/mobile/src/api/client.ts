import { env } from '../config/env.ts';
import type { ListingParams } from './types.ts';

export class ApiError extends Error {
  /** HTTP-status, eller 0 ved netværksfejl/timeout (ingen svar overhovedet). */
  status: number;

  /** Teknisk beskrivelse til fejlsøgning. `message` er den brugervendte tekst. */
  detail: string;

  constructor(message: string, status: number, detail = message) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Skærmene viser `error.message` direkte, så teksten skal være noget en
 * bruger kan handle på - ikke "HTTP 503 for /api/home".
 */
function friendlyMessage(status: number): string {
  if (status === 0) return 'Ingen forbindelse. Tjek dit netværk, og prøv igen.';
  if (status === 404) return 'Vi kunne ikke finde det, du søgte efter.';
  if (status === 429) return 'Lidt for mange forespørgsler. Prøv igen om et øjeblik.';
  if (status >= 500) return 'MadShopper svarer ikke lige nu. Prøv igen om lidt.';
  return 'Noget gik galt. Prøv igen.';
}

/**
 * Uden timeout kan et hængende kald efterlade skærmen i uendelig
 * indlæsning - fx på et hotelnetværk der sluger pakker i stedet for at
 * afvise dem. 15 s er rigeligt til det tungeste kald (/api/products) på en
 * langsom forbindelse og kort nok til, at brugeren får en fejl at reagere på.
 */
const TIMEOUT_MS = 15_000;

async function request<T>(
  url: string,
  init?: RequestInit,
  externalController?: AbortController,
): Promise<T> {
  const controller = externalController ?? new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(url, { ...init, signal: controller.signal });
  } catch (e) {
    const timedOut = e instanceof Error && e.name === 'AbortError';
    throw new ApiError(
      timedOut ? 'Det tog for lang tid at hente data. Prøv igen.' : friendlyMessage(0),
      0,
      timedOut ? `timeout efter ${TIMEOUT_MS} ms: ${url}` : `netværksfejl: ${url}`,
    );
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    throw new ApiError(friendlyMessage(res.status), res.status, `HTTP ${res.status} for ${url}`);
  }
  return (await res.json()) as T;
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

export async function apiGet<T>(
  path: string,
  params?: ListingParams,
  controller?: AbortController,
): Promise<T> {
  return request<T>(
    `${env.apiBaseUrl}${path}${buildQuery(params)}`,
    { headers: { Accept: 'application/json' } },
    controller,
  );
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(`${env.apiBaseUrl}${path}`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
}
