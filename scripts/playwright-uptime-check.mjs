#!/usr/bin/env node
// Erstatter det curl-baserede uptime-tjek: Cloudflares GRATIS Bot Fight Mode
// kører uden om WAF'ens Ruleset-motor, så en delt hemmelighed i en header
// (forsøgt 2026-07-20 via CI_BYPASS_SECRET) kan aldrig skippe den - kun
// Super Bot Fight Mode (Pro-plan+) understøtter Skip-regler. curl kan
// desuden aldrig løse selve JS-udfordringen ("JS Detections: On").
// En rigtig (headless) browser kører den faktiske JS og fremstår som en
// normal besøgende, så den slipper igennem uden at vi behøver ændre
// sitets bot-beskyttelse for andre besøgende.
import { chromium } from "playwright";

const urls = process.argv.slice(2);
if (urls.length === 0) {
  console.error("brug: playwright-uptime-check.mjs <url> [url...]");
  process.exit(2);
}

const ATTEMPTS = 3;
const RETRY_DELAY_MS = 15_000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Status alene er ikke nok: en Cloudflare-fejlside kan i teorien svare 200
// fra cache, og et AJAX-fragment mangler resten af siden. "MadShopper" står
// i base.html's <title>/logo og findes ikke på Cloudflares fejlsider.
async function check(page, url) {
  for (let attempt = 1; attempt <= ATTEMPTS; attempt++) {
    try {
      const response = await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: 25_000,
      });
      const status = response?.status() ?? 0;
      const body = await page.content();
      if (status === 200 && body.includes("MadShopper")) {
        console.log(`OK   ${url} (HTTP ${status}, forsøg ${attempt})`);
        return true;
      }
      console.log(`FEJL ${url} (HTTP ${status}, forsøg ${attempt})`);
    } catch (err) {
      console.log(`FEJL ${url} (${err.message}, forsøg ${attempt})`);
    }
    if (attempt < ATTEMPTS) await sleep(RETRY_DELAY_MS);
  }
  return false;
}

// 2026-07-20 (run #6): fik rå HTTP 403 på alle forsøg - ikke en udfordring
// der kunne løses, et direkte afslag. Playwright sætter som standard
// navigator.webdriver=true, som er det mest almindelige signal
// bot-beskyttelse kigger efter; kombineret med at trafikken kommer fra
// GitHub Actions' Azure-IP-range er det sandsynligvis nok til at Cloudflare
// afviser med det samme i stedet for at tilbyde en JS-udfordring. Fjerner
// det mest oplagte automatiserings-fingeraftryk - hvis det STADIG giver
// 403, er det IP/ASN-baseret og kan ikke løses fra klient-siden (se
// scripts/smoke-test.mjs's kommentar for de reelle alternativer).
const browser = await chromium.launch({
  args: ["--disable-blink-features=AutomationControlled"],
});
try {
  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });
  const page = await context.newPage();
  let fail = false;
  for (const url of urls) {
    const ok = await check(page, url);
    if (!ok) fail = true;
  }
  if (fail) {
    console.log(
      "::error::madshopper.dk svarer ikke korrekt. Tjek Cloudflare-dashboardet (Workers & Pages -> madshopper -> Deployments) og rul evt. tilbage til seneste stabile version."
    );
    process.exit(1);
  }
} finally {
  await browser.close();
}
