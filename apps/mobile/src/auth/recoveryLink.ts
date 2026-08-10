/**
 * Deep link til "glemt adgangskode".
 *
 * `resetPasswordForEmail` sender brugeren til Supabases /auth/v1/verify, som
 * redirecter videre til appens scheme (`madshopper://…`) med enten
 *   - implicit flow: `#access_token=…&refresh_token=…&type=recovery`
 *   - PKCE:          `?code=…&type=recovery`
 *   - eller en fejl: `#error=access_denied&error_description=…` (udløbet link)
 *
 * Klienten kører med `detectSessionInUrl: false` (der findes ingen browser-URL
 * i en native app), så URL'en skal veksles til en session i hånden. Parsingen
 * ligger i sin egen fil, dels fordi den er ren logik der kan testes uden
 * Expo-runtime, dels fordi den er sikkerhedsafgørende: kun URL'er der
 * utvetydigt er et recovery-link må åbne "vælg ny adgangskode".
 */

export type RecoveryLink =
  | { kind: 'tokens'; accessToken: string; refreshToken: string }
  | { kind: 'code'; code: string }
  | { kind: 'error'; message: string };

function paramsFrom(url: string): URLSearchParams {
  const merged = new URLSearchParams();
  const hashIndex = url.indexOf('#');
  const queryIndex = url.indexOf('?');
  if (queryIndex >= 0) {
    const end = hashIndex > queryIndex ? hashIndex : url.length;
    for (const [k, v] of new URLSearchParams(url.slice(queryIndex + 1, end))) {
      merged.set(k, v);
    }
  }
  if (hashIndex >= 0) {
    for (const [k, v] of new URLSearchParams(url.slice(hashIndex + 1))) merged.set(k, v);
  }
  return merged;
}

/**
 * Returnerer et resultat KUN for links der er markeret som recovery. Alt
 * andet (invitations-links, tilfældige dybe links, OAuth-callbacks) giver
 * null, så de hverken kan skabe en session eller åbne en skærm.
 */
export function parseRecoveryLink(url: string): RecoveryLink | null {
  if (!url) return null;
  let params: URLSearchParams;
  try {
    params = paramsFrom(url);
  } catch {
    return null;
  }

  const type = params.get('type');
  const isRecovery = type === 'recovery' || url.includes('type=recovery');
  if (!isRecovery) return null;

  const error = params.get('error_description') || params.get('error');
  if (error) return { kind: 'error', message: decodeURIComponent(error.replace(/\+/g, ' ')) };

  const accessToken = params.get('access_token');
  const refreshToken = params.get('refresh_token');
  if (accessToken && refreshToken) return { kind: 'tokens', accessToken, refreshToken };

  const code = params.get('code');
  if (code) return { kind: 'code', code };

  return null;
}

/** Sandt kun for URL'er `parseRecoveryLink` faktisk kan bruge til noget. */
export function isRecoveryUrl(url: string): boolean {
  return parseRecoveryLink(url) !== null;
}
