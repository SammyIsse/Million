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

/**
 * Ventetider mellem gentagne forsøg efter et X-Data-Degraded-svar. Stigende
 * (ikke fast interval), så et forbigående hik forsøges hurtigt igen, mens et
 * lidt sejere tilfælde får mere tid til at klare sig selv, uden at vi banker
 * løs med faste 300 ms-mellemrum på en server der måske allerede er presset.
 * Total ventetid ved alle tre forsøg: ~1,8 s - mærkbart, men langt at
 * foretrække frem for en søgning der viser "ingen resultater" på en vare der
 * findes.
 */
const DEGRADED_RETRY_DELAYS_MS = [300, 600, 900];

async function fetchWithTimeout(url: string, init: RequestInit | undefined, controller: AbortController): Promise<Response> {
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function request<T>(
  url: string,
  init?: RequestInit,
  externalController?: AbortController,
): Promise<T> {
  const controller = externalController ?? new AbortController();
  let res: Response;
  try {
    res = await fetchWithTimeout(url, init, controller);
  } catch (e) {
    const timedOut = e instanceof Error && e.name === 'AbortError';
    throw new ApiError(
      timedOut ? 'Det tog for lang tid at hente data. Prøv igen.' : friendlyMessage(0),
      0,
      timedOut ? `timeout efter ${TIMEOUT_MS} ms: ${url}` : `netværksfejl: ${url}`,
    );
  }
  // Serveren sætter X-Data-Degraded, når et 200-svar bygger på ufuldstændige
  // data - en isolate-kollision i D1-broen ELLER at CPU-budgettet (Workers
  // gratis-plan: 10 ms) blev overskredet midt i en tung søgning
  // (app.py: _mark_data_degraded). Begge kan tage mere end ét øjeblikkeligt
  // ekstra forsøg at komme fri af under rigtig trafik - målt 02-09-2026 mod
  // produktion: nogle gange healede ét ekstra kald med det samme, andre
  // gange var selv 2-3 kald i hurtig rækkefølge stadig degraderede, mens et
  // kald et par sekunder senere lykkedes. DEGRADED_RETRY_DELAYS_MS er derfor
  // FLERE forsøg med stigende ventetid, ikke ét. Loopet giver op efter sidste
  // forsøg uanset udfald - et VEDVARENDE degraderet svar (fx databasen reelt
  // nede) skal stadig vises, ikke hænge i en uendelig løkke. Uden dette så en
  // søgning eller liste ramt af racen ud som "ingen resultater", selvom data
  // findes.
  if (res.ok && res.headers.get('X-Data-Degraded') === '1') {
    for (const delayMs of DEGRADED_RETRY_DELAYS_MS) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
      try {
        const retryController = externalController ?? new AbortController();
        const retry = await fetchWithTimeout(url, init, retryController);
        res = retry;
        if (retry.ok && retry.headers.get('X-Data-Degraded') !== '1') break;
      } catch {
        break; // netværksfejl på retry - behold seneste svar, giv op
      }
    }
  }
  if (!res.ok) {
    // app.py sender en præcis dansk fejltekst i {error} på de fleste 4xx-svar
    // (fx "Beskeden er for lang (maks. 500 tegn)."), som blev smidt væk til
    // fordel for en generisk friendlyMessage(status) - brugeren fik aldrig at
    // vide HVORFOR noget blev afvist, kun at "noget gik galt".
    let serverMessage: string | null = null;
    try {
      const body = (await res.json()) as { error?: unknown } | null;
      if (body && typeof body.error === 'string' && body.error.trim()) {
        serverMessage = body.error.trim();
      }
    } catch {
      /* body var ikke (gyldig) JSON - fx en Cloudflare-fejlside */
    }
    throw new ApiError(
      serverMessage || friendlyMessage(res.status),
      res.status,
      serverMessage ? `HTTP ${res.status} for ${url}: ${serverMessage}` : `HTTP ${res.status} for ${url}`,
    );
  }
  try {
    return (await res.json()) as T;
  } catch (e) {
    // Status 200 garanterer ikke en gyldig JSON-body - fx en Bot Fight
    // Mode-interstitial eller en Cloudflare-fejlside der (sjældent) svarer
    // 200. Uden dette lækkede en rå "SyntaxError: JSON Parse error:
    // Unexpected character: <" direkte ud til skærmene.
    throw new ApiError(friendlyMessage(res.status), res.status, `ugyldig JSON fra ${url}: ${e}`);
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
