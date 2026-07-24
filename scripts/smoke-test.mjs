#!/usr/bin/env node
// Erstatter scripts/smoke-test.sh (curl-baseret). Cloudflares GRATIS Bot
// Fight Mode kan ikke skippes via WAF-regler (kun Super Bot Fight Mode,
// Pro-plan+, understøtter det) - se scripts/playwright-uptime-check.mjs for
// samme forklaring; forsøget med CI_BYPASS_SECRET (2026-07-20) virkede
// derfor aldrig, uanset hvor korrekt secret+regel var sat op.
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
// 2026-07-20, tre mislykkede forsøg før dette (se git-historik for detaljer):
//   #79 context.request (deler cookie, men ikke browserens TLS-fingeraftryk) -> 60/60 403
//   #80 ægte page.goto pr. request, ny side hver gang, PARALLEL=5           -> 60/60 403
//   #81 samme, PARALLEL sænket til 2 + jitter                              -> 60/60 403 (uændret!)
// At #80->#81 gav IDENTISK resultat på trods af halveret samtidighed tyder
// på at det ikke (kun) er et rate-baseret tærskelproblem. Den ene ting der
// adskiller disse fejlende forsøg fra playwright-uptime-check.mjs (som
// BEKRÆFTET virker, 200 OK): den scriptet genbruger ÉN side til sekventielle
// navigationer og bruger ingen page.route()-interception. Denne udgave
// efterligner det mønster: PARALLEL faste sider oprettes én gang, hver
// genbruges sekventielt til sin andel af requests (ægte samtidighed på
// tværs af sider, ingen ny-side-per-request, ingen route-interception).
// Hvis dette STADIG fejler, er næste skridt enten en fuldt seriel test
// (ingen samtidighed) eller et internt Worker-baseret samtidigheds-selvtjek
// (asyncio.gather i selve Python-koden - rører produktionskode, kræver
// separat review).
//
// 3 runder x 2 stier x 10 requests (2 sider, sekventielt pr. side) = 60
// requests over ~1,5 minut. Runde 1 cache-buster med ?smoke=, så alle
// requests reelt renderer koldt (som lige efter deploy/seed, hvor
// cache_version-bump gør alt koldt). Runde 2-3 rammer de RIGTIGE URL'er,
// hvor edge-cachen må hjælpe - det er sådan trafikken faktisk ser ud.
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
// Valgfri adgangsnoegle (2. argument). Staging-workeren er spaerret bag
// STAGING_ACCESS_SECRET og svarer 404 uden den - uden dette ville roegtesten
// maale sin egen spaerring i stedet for sitet. Noeglen bruges KUN paa
// warmup-navigationen; den saetter en cookie, som resten af konteksten
// genbruger, saa den aldrig staar i de oevrige URL'er.
const ACCESS_KEY = (process.argv[3] || "").trim();
const WARMUP_URL = ACCESS_KEY
  ? `${BASE}/?k=${encodeURIComponent(ACCESS_KEY)}`
  : `${BASE}/`;
const ROUNDS = 3;
const PER_ROUND = 10;
const PARALLEL = 2;
const STIER = ["/", "/Mejeri"];
const TOLERANCE = 2;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function runLane(page, urls) {
  const results = [];
  for (const url of urls) {
    try {
      const response = await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: 30_000,
      });
      results.push(response?.status() ?? 0);
    } catch {
      results.push(0);
    }
  }
  return results;
}

async function runBatch(pages, url, count) {
  const lanes = pages.map(() => []);
  for (let i = 0; i < count; i++) lanes[i % pages.length].push(url);
  const laneResults = await Promise.all(
    pages.map((page, i) => runLane(page, lanes[i]))
  );
  return laneResults.flat();
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
const alleKoder = [];
try {
  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });
  // Løs en evt. JS-udfordring én gang, så konteksten får en gyldig
  // cf_clearance-cookie, som de øvrige sider i samme kontekst genbruger.
  // "networkidle" frarådes af Playwright selv og hang her i praksis til
  // 30s-timeout (2026-07-20, run #76) - en Cloudflare-udfordringsside (eller
  // sitets egen polling) går aldrig helt i netværks-ro. "load" er robust nok
  // til at afgøre om noget overhovedet svarer, og det er selve
  // MadShopper-teksten (ikke netværksstilhed) der reelt beviser, at
  // udfordringen er løst og cf_clearance er sat.
  const warmup = await context.newPage();
  for (let attempt = 1; attempt <= 3 && !cleared; attempt++) {
    try {
      await warmup.goto(WARMUP_URL, { waitUntil: "load", timeout: 30_000 });
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

  if (cleared) {
    // Genbrug warmup-siden som lane 0 (den er allerede godkendt), opret
    // PARALLEL-1 yderligere sider én gang - ikke en ny side pr. request.
    const pages = [warmup];
    for (let i = 1; i < PARALLEL; i++) pages.push(await context.newPage());

    for (let round = 1; round <= ROUNDS; round++) {
      for (const sti of STIER) {
        const url =
          round === 1 ? `${BASE}${sti}?smoke=${Date.now()}` : `${BASE}${sti}`;
        const codes = await runBatch(pages, url, PER_ROUND);
        alleKoder.push(...codes);
        const bad = codes.filter((c) => c !== 200).length;
        console.log(`runde ${round} ${sti}: ${distribution(codes)} (${bad} ikke-200)`);
        total += PER_ROUND;
        totalBad += bad;
      }
      if (round < ROUNDS) await sleep(20_000);
    }

    for (const page of pages) await page.close();
  } else {
    await warmup.close();
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
  // Skeln mellem "sitet er i stykker" og "vi kunne ikke maale sitet".
  // Uden den skelnen raabte testen "rul tilbage!" hver gang Cloudflares Bot
  // Fight Mode afviste CI-runneren - en alarm der altid er roed, laerer man
  // at ignorere, og saa daekker den over den aegte fejl den skulle fange.
  const daarlige = alleKoder.filter((c) => c !== 200);
  const alleEr = (kode) => daarlige.length > 0 && daarlige.every((c) => c === kode);

  if (alleEr(403)) {
    console.log(
      `::error::Røgtesten kunne IKKE MÅLE ${BASE}: alle ${daarlige.length} svar var 403 fra Cloudflare, altså afvist i kanten før worker'en. Det er bot-beskyttelsen (gratis Bot Fight Mode kører uden om WAF'ens Ruleset-motor og kan ikke skippes af en regel), ikke et sygdomstegn ved sitet. RUL IKKE TILBAGE på dette signal alene - verificér i stedet manuelt, eller tillad GitHub Actions' IP-range i Cloudflare.`
    );
    process.exit(1);
  }
  if (alleEr(404)) {
    console.log(
      `::error::Røgtesten kunne IKKE MÅLE ${BASE}: alle ${daarlige.length} svar var 404. Kører denne mod staging, mangler adgangsnøglen - send STAGING_ACCESS_SECRET med som 2. argument til smoke-test.mjs. RUL IKKE TILBAGE på dette signal alene.`
    );
    process.exit(1);
  }
  console.log(
    `::error::Røgtest fejlede (${totalBad}/${total} ikke-200 mod ${BASE}). Mønsteret fra 2026-07-19: fejl kommer straks efter deploy under samtidig trafik. Rul tilbage i Cloudflare-dashboardet (Workers & Pages -> madshopper -> Deployments -> Rollback). Hjælper rollback ikke, er det free-planens CPU-budget der er udtømt af selve belastningen - så vent nogle minutter og genkør, i stedet for at rulle længere tilbage.`
  );
  process.exit(1);
}
console.log(`OK: ${BASE} holder til samtidig trafik`);
