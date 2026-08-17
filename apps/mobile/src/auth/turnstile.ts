/**
 * Bot-beskyttelse (Cloudflare Turnstile) i den native app.
 *
 * Web indlejrer Turnstile-widget'en direkte (iframe, static/js/auth.js +
 * templates/feedback.html). Der findes intet tilsvarende i React Native
 * uden en ny native WebView-afhængighed og en fuld genbuild af appen.
 *
 * I stedet åbner vi den SAMME udfordring (templates/turnstile_challenge.html,
 * app.py::turnstile_challenge) i systemets browser via expo-web-browser
 * (allerede en afhængighed - bruges også til Google/Apple-lignende flows) og
 * fanger resultatet via et redirect til appens egen URL-scheme. Samme
 * mønster som et OAuth-flow, ingen ny native modul nødvendig.
 *
 * Fundet manglende under paritetsrevisionen 2026-08-17: uden dette modul
 * havde app'en hverken bot-beskyttelse på signup, og /api/feedback afviste
 * ALTID app'ens indsendelser, fordi den påkrævede turnstile_token aldrig
 * blev sendt.
 */
import * as WebBrowser from 'expo-web-browser';
import { env } from '../config/env';

const TURNSTILE_VERIFY_URL = 'https://turnstile-siteverify-madshopper.kasp478g.workers.dev';
const RETURN_URL = 'madshopper://turnstile-callback';

function extractToken(url: string): string | null {
  const idx = url.indexOf('token=');
  if (idx === -1) return null;
  const rest = url.slice(idx + 'token='.length);
  const end = rest.indexOf('&');
  const raw = end === -1 ? rest : rest.slice(0, end);
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw || null;
  }
}

/** Åbner udfordringen og venter på brugeren. Returnerer et Turnstile-token, eller null hvis annulleret/fejlet. */
export async function getTurnstileToken(): Promise<string | null> {
  try {
    const challengeUrl =
      `${env.apiBaseUrl}/turnstile-challenge?returnUrl=${encodeURIComponent(RETURN_URL)}`;
    const result = await WebBrowser.openAuthSessionAsync(challengeUrl, RETURN_URL);
    if (result.type !== 'success') return null;
    return extractToken(result.url);
  } catch {
    return null;
  }
}

/**
 * Server-til-server-verificering af tokenet, FØR selve konto-oprettelsen -
 * samme værktøj web's klient kalder inline i auth.js::submitForm. Bruges kun
 * til signup: /api/feedback genverificerer selv token'et server-side
 * (app.py::_verify_turnstile_token), så feedback behøver ikke dette ekstra
 * kald.
 */
export async function verifyTurnstileToken(token: string): Promise<boolean> {
  try {
    const res = await fetch(TURNSTILE_VERIFY_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    const data = (await res.json().catch(() => null)) as { success?: boolean } | null;
    return !!(data && data.success);
  } catch {
    return false;
  }
}
