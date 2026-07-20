#!/usr/bin/env node
// Erstatter scripts/smoke-test.sh (curl-baseret). Cloudflares GRATIS Bot
// Fight Mode kan ikke skippes via WAF-regler (kun Super Bot Fight Mode,
// Pro-plan+, understøtter det) - se scripts/playwright-uptime-check.mjs for
// samme forklaring; forsøget med CI_BYPASS_SECRET (2026-07-20) virkede
// derfor aldrig, uanset hvor korrekt secret+regel var sat op.
//
// Denne udgave løser en evt. JS-udfordring ÉN gang med en rigtig (headless)
// sidevisning (warmup), og bruger derefter RIGTIGE sidevisninger for hver af
// de 60 samtidige requests - IKKE Playwrights lette request-API. Forsøgt
// (2026-07-20, run #79): context.request genbruger cf_clearance-cookien,
// men går uden om Chromiums netværksstak, så TLS/browser-fingeraftrykket
// ikke matcher det, der løste udfordringen - Cloudflare afviste det som
// cookie-genbrug fra en ikke-browser-klient (60/60 HTTP 403). Ikke-væsentlige
// ressourcer (billeder/fonte/CSS) blokeres pr. side for at holde det
// nogenlunde hurtigt - kun selve dokument-requesten skal ligne en browser.
//
// Røgtest efter deploy: rammer sitet med SAMTIDIGE requests og fejler ved
// ikke-200-svar. Fanger fejlklassen fra 2026-07-19 (1101 asyncio-reentrancy
// og 1102 CPU-grænse), som kun viser sig når flere renders er i gang på én
// gang - fejlene kom dengang under 2 minutter efter deploy, men blev aldrig
// fanget af enkeltstående funktionstjek uden samtidighed.
//
// Brug: node scripts/smoke-test.mjs <base-url>
//   fx  node scripts/smoke-test.mjs https://madshopper.dk
//
// 3 runder x 2 stier x 10 requests (5 parallelle) = 60 requests over ~1,5
// minut. Runde 1 cache-buster med ?smoke=, så alle samtidige requests reelt
// renderer koldt (som lige efter deploy/seed, hvor cache_version-bump gør
// alt koldt). Runde 2-3 rammer de RIGTIGE URL'er, hvor edge-cachen må
// hjælpe - det er sådan trafikken faktisk ser ud.
//
// ADVARSEL - skru IKKE op for PARALLEL/PER_ROUND uden god grund: ved højere
// belastning (10 parallelle x 20 pr. runde, cold-bust i alle runder) væltede
// testen selv den ellers stabile produktionsversion 77ce1327 (målt
// 2026-07-19: 500/503 fra runde 3, fejltilstanden VARENDE VED efter
// belastningen stoppede). Free-planens CPU-budget tåler kun begrænset
// samtidig kold rendering, så gaten skal ligge i det realistiske
// trafikbånd - nok til at fange 1101/1102-fejlklassen, ikke nok til selv at
// lægge sitet ned.
// Tolerance på 2 ikke-200 i alt, så et enkelt netværksblip ikke fejler et
// ellers sundt deploy.
import { chromium } from "playwright";

const base = process.argv[2];
if (!base) {
  console.error("brug: smoke-test.mjs <base-url>");
  process.exit(2);
}
const BASE = base.replace(/\/$/, "");
const ROUNDS = 3;
const PER_ROUND = 10;
const PARALLEL = 2;
const STIER = ["/", "/Mejeri"];
const TOLERANCE = 2;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// 2026-07-20 (run #79, #80): hverken context.request eller ægte page.goto
// fra en allerede-godkendt kontekst kunne komme forbi - begge fik 60/60
// HTTP 403. Ikke et fingeraftryksproblem: gratis Bot Fight Mode har en
// HASTIGHEDS-/adfærdsbaseret heuristik der reagerer på selve mønstret
// "mange samtidige requests mod samme mål fra samme kilde", uanset hvor
// browser-ægte klienten ser ud. PARALLEL sænket fra 5 til 2 + lidt tilfældig
// jitter pr. request, så mønstret ligner en synkroniseret bot-byrde mindre -
// et forsøg på at blive under den tærskel uden at opgive samtidighed helt
// (mister noget af evnen til at fange 2026-07-19-fejlklassen ved lavere
// samtidighed, men stadig mere end en seriel test).
async function fetchStatus(context, url) {
  await sleep(Math.random() * 250);
  const page = await context.newPage();
  try {
    await page.route("**/*", (route) => {
      const type = route.request().resourceType();
      if (["image", "font", "media", "stylesheet"].includes(type)) {
        return route.abort();
      }
      return route.continue();
    });
    const response = await page.goto(url, {
      waitUntil: "domcontentloaded",
      timeout: 30_000,
    });
    return response?.status() ?? 0;
  } catch {
    return 0;
  } finally {
    await page.close();
  }
}

async function runBatch(context, url, count, parallel) {
  const results = [];
  for (let i = 0; i < count; i += parallel) {
    const chunkSize = Math.min(parallel, count - i);
    const chunk = await Promise.all(
      Array.from({ length: chunkSize }, () => fetchStatus(context, url))
    );
    results.push(...chunk);
  }
  return results;
}

function distribution(codes) {
  const counts = new Map();
  for (const c of codes) counts.set(c, (counts.get(c) ?? 0) + 1);
  return [...counts.entries()].map(([code, n]) => `${n} ${code}`).join(" ");
}

// 2026-07-20 (run #77): warmup fik "load" til at fuldføre, men
// "MadShopper"-teksten kom aldrig - dvs. den ladede side var Cloudflares
// blokerings-/fejlside, ikke sitet. Samme rå-403-mønster som
// scripts/playwright-uptime-check.mjs fandt samtidig - se den fils
// kommentar. Fjerner samme automatiserings-fingeraftryk her.
const browser = await chromium.launch({
  args: ["--disable-blink-features=AutomationControlled"],
});
let total = 0;
let totalBad = 0;
let cleared = false;
try {
  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });
  // Løs en evt. JS-udfordring én gang, så konteksten får en gyldig
  // cf_clearance-cookie, som request-API'et genbruger for alle requests.
  // "networkidle" frarådes af Playwright selv og hang her i praksis til
  // 30s-timeout (2026-07-20, run #76) - en Cloudflare-udfordringsside (eller
  // sitets egen polling) går aldrig helt i netværks-ro. "load" er robust nok
  // til at afgøre om noget overhovedet svarer, og det er selve
  // MadShopper-teksten (ikke netværksstilhed) der reelt beviser, at
  // udfordringen er løst og cf_clearance er sat.
  const warmup = await context.newPage();
  for (let attempt = 1; attempt <= 3 && !cleared; attempt++) {
    try {
      await warmup.goto(`${BASE}/`, { waitUntil: "load", timeout: 30_000 });
      await warmup.waitForFunction(
        () => document.body?.innerText?.includes("MadShopper"),
        { timeout: 20_000 }
      );
      cleared = true;
    } catch (err) {
      console.log(`warmup forsøg ${attempt} fejlede: ${err.message}`);
      if (attempt < 3) await sleep(10_000);
    }
  }
  await warmup.close();

  if (cleared) {
    for (let round = 1; round <= ROUNDS; round++) {
      for (const sti of STIER) {
        const url =
          round === 1 ? `${BASE}${sti}?smoke=${Date.now()}` : `${BASE}${sti}`;
        const codes = await runBatch(context, url, PER_ROUND, PARALLEL);
        const bad = codes.filter((c) => c !== 200).length;
        console.log(`runde ${round} ${sti}: ${distribution(codes)} (${bad} ikke-200)`);
        total += PER_ROUND;
        totalBad += bad;
      }
      if (round < ROUNDS) await sleep(20_000);
    }
  }
} finally {
  await browser.close();
}

if (!cleared) {
  console.log(
    `::error::Kunne ikke etablere en gyldig session mod ${BASE} (Cloudflare-udfordring løste sig aldrig efter 3 forsøg). Røgtesten kunne ikke køre.`
  );
  process.exit(1);
}

console.log(`I alt: ${totalBad} ikke-200 af ${total} requests`);
if (totalBad > TOLERANCE) {
  console.log(
    `::error::Røgtest fejlede (${totalBad}/${total} ikke-200 mod ${BASE}). Mønsteret fra 2026-07-19: fejl kommer straks efter deploy under samtidig trafik. Rul tilbage i Cloudflare-dashboardet (Workers & Pages -> madshopper -> Deployments -> Rollback). Hjælper rollback ikke, er det free-planens CPU-budget der er udtømt af selve belastningen - så vent nogle minutter og genkør, i stedet for at rulle længere tilbage.`
  );
  process.exit(1);
}
console.log(`OK: ${BASE} holder til samtidig trafik`);
