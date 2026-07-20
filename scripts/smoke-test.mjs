#!/usr/bin/env node
// Erstatter scripts/smoke-test.sh (curl-baseret). Cloudflares GRATIS Bot
// Fight Mode kan ikke skippes via WAF-regler (kun Super Bot Fight Mode,
// Pro-plan+, understøtter det) - se scripts/playwright-uptime-check.mjs for
// samme forklaring; forsøget med CI_BYPASS_SECRET (2026-07-20) virkede
// derfor aldrig, uanset hvor korrekt secret+regel var sat op.
//
// Denne udgave løser en evt. JS-udfordring ÉN gang med en rigtig (headless)
// sidevisning, og genbruger derefter browserkontekstens cf_clearance-cookie
// til de samtidige requests via Playwrights request-API - lige så hurtigt
// som curl, men med en gyldig "jeg er en browser"-status.
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
const PARALLEL = 5;
const STIER = ["/", "/Mejeri"];
const TOLERANCE = 2;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchStatus(request, url) {
  try {
    const res = await request.get(url, { timeout: 30_000 });
    return res.status();
  } catch {
    return 0;
  }
}

async function runBatch(request, url, count, parallel) {
  const results = [];
  for (let i = 0; i < count; i += parallel) {
    const chunkSize = Math.min(parallel, count - i);
    const chunk = await Promise.all(
      Array.from({ length: chunkSize }, () => fetchStatus(request, url))
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

const browser = await chromium.launch();
let total = 0;
let totalBad = 0;
let cleared = false;
try {
  const context = await browser.newContext();
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
        const codes = await runBatch(context.request, url, PER_ROUND, PARALLEL);
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
