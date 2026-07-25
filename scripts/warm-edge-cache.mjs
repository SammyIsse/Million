#!/usr/bin/env node
// Sekventiel opvarmning af edge-cachen efter deploy/seed.
//
// Hvert deploy bumper cache_version og purger CDN (deploy-edge.yml). Det gør
// ALLE sider kolde på én gang. Python Workers på free-planen tåler ikke mange
// samtidige cold renders (Error 1101) - målt 2026-07-19 og igen 2026-07-25
// lige efter deploy af body-scan-fixet.
//
// Denne script rammer de vigtigste URL'er ÉN AD GANGEN med Playwright (så
// Bot Fight Mode slipper os ind), retries ved 5xx, og giver edge-cachen tid
// til at fylde FØR røgtesten eller rigtige brugere stormer ind.
//
// Brug: node scripts/warm-edge-cache.mjs <base-url> [staging-access-key]

import { chromium } from "playwright";

const base = process.argv[2];
if (!base) {
  console.error("brug: warm-edge-cache.mjs <base-url> [staging-access-key]");
  process.exit(2);
}
const BASE = base.replace(/\/$/, "");
const ACCESS_KEY = (process.argv[3] || "").trim();

// Samme stier som sitemap + forsiden. Ingen søge-URL'er: for mange
// kombinationer, og søgning er alligevel tungere end kategorisider.
const PATHS = [
  "/",
  "/ugens_tilbud",
  "/Mejeri",
  "/Koed_og_fisk",
  "/Frugt_og_groent",
  "/Broed_og_kager",
  "/Kolonial",
  "/Frost",
  "/Drikkevarer",
  "/Slik",
  "/about",
  "/feedback",
  "/terms-of-service",
  "/privatliv",
];

const MAX_ATTEMPTS = 4;
const RETRY_MS = 2_500;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch({
  args: ["--disable-blink-features=AutomationControlled"],
});

let failed = 0;
try {
  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });

  const page = await context.newPage();
  const gateUrl = ACCESS_KEY
    ? `${BASE}/?k=${encodeURIComponent(ACCESS_KEY)}`
    : `${BASE}/`;

  // Løs evt. Cloudflare-udfordring / staging-cookie én gang.
  let cleared = false;
  for (let attempt = 1; attempt <= 3 && !cleared; attempt++) {
    try {
      await page.goto(gateUrl, { waitUntil: "load", timeout: 30_000 });
      await page.waitForFunction(
        () => document.body?.innerText?.includes("MadShopper"),
        { timeout: 20_000 }
      );
      cleared = true;
    } catch (err) {
      console.log(`session-forsøg ${attempt} fejlede: ${err.message}`);
      if (attempt < 3) await sleep(8_000);
    }
  }
  if (!cleared) {
    console.error(
      `::error::Kunne ikke etablere session mod ${BASE} - cache ikke opvarmet.`
    );
    process.exit(1);
  }

  for (const path of PATHS) {
    const url = `${BASE}${path}`;
    let ok = false;
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      try {
        const response = await page.goto(url, {
          waitUntil: "domcontentloaded",
          timeout: 45_000,
        });
        const status = response?.status() ?? 0;
        if (status === 200) {
          console.log(`OK  ${path}`);
          ok = true;
          break;
        }
        console.log(`…  ${path} got ${status} (forsøg ${attempt}/${MAX_ATTEMPTS})`);
      } catch (err) {
        console.log(
          `…  ${path} fejl: ${err.message} (forsøg ${attempt}/${MAX_ATTEMPTS})`
        );
      }
      if (attempt < MAX_ATTEMPTS) await sleep(RETRY_MS * attempt);
    }
    if (!ok) {
      console.log(`FAIL ${path}`);
      failed += 1;
    }
  }

  await page.close();
} finally {
  await browser.close();
}

if (failed > 0) {
  console.error(
    `::error::Edge-cache warmup: ${failed}/${PATHS.length} stier nåede aldrig 200. Fortsæt forsigtigt - røgtesten vil sandsynligvis også fejle.`
  );
  process.exit(1);
}
console.log(`OK: edge-cache opvarmet for ${PATHS.length} stier på ${BASE}`);
