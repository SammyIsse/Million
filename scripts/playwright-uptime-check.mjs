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

// Status + "MadShopper" alene beviser intet om DATAEN: ordet står i
// base.html's <title>/logo uden for enhver data-afhængig blok, så en
// degraderet side med et tomt produktgitter (D1/edge-fejl,
// _mark_data_degraded) eller en Rema-only-cache uden en eneste
// prissammenligning (se dæknings-værnet i updater.py::run_updater)
// bestod dette tjek uændret - status 200, ordet til stede, INGEN alarm,
// selvom sitet reelt viste ingen priser at sammenligne. Hvert produktkort
// bærer data-has-match="true|false" (macros/product_card.html), så vi kan
// tælle produkter OG matchede produkter direkte i den rå HTML.
const MIN_PRODUCTS = 10;
const MIN_MATCH_RATIO = 0.2; // sund baseline er ~50 %+ - se updater.py

function countProducts(body) {
  const total = (body.match(/data-has-match="(?:true|false)"/g) || []).length;
  const matched = (body.match(/data-has-match="true"/g) || []).length;
  return { total, matched };
}

// Søgning er kerneproduktet, men forsiden og /Mejeri ligger i edge-cachen:
// et tjek af dem rammer aldrig den render-vej, hvor søgningen kan fejle.
// Samtidige søgninger gav 0 varer i over en måned (rettet 14-09-2026), uden at
// noget tjek så det. For /search/results tjekkes derfor, at søgningen giver
// varer, og URL'en gøres unik pr. forsøg med et uopnåeligt højt max_price
// (filtreres efter søgningen, ændrer ikke resultatet) - så svaret ALTID
// renderes frisk i stedet for at komme fra cachen. Match-andelen tjekkes ikke
// her: en søgning viser legitimt mange "kun hos én butik"-varer.
function isSearch(url) {
  return new URL(url).pathname === "/search/results";
}

function freshSearchUrl(url) {
  const u = new URL(url);
  u.searchParams.set("max_price", String(1_000_000 + (Date.now() % 1_000_000)));
  return u.toString();
}

async function check(page, url) {
  const search = isSearch(url);
  for (let attempt = 1; attempt <= ATTEMPTS; attempt++) {
    const target = search ? freshSearchUrl(url) : url;
    try {
      const response = await page.goto(target, {
        waitUntil: "domcontentloaded",
        timeout: 25_000,
      });
      const status = response?.status() ?? 0;
      // Serverens svar, før healDegradedContent() i script.js kan nå at
      // udskifte en degraderet liste - det er serverens fejl vi vil se.
      const body = await page.content();
      const degraded = response?.headers()["x-data-degraded"] === "1";
      if (status === 200 && body.includes("MadShopper")) {
        const { total, matched } = countProducts(body);
        const ratio = total > 0 ? matched / total : 0;
        if (total < MIN_PRODUCTS) {
          console.log(
            `FEJL ${target} (HTTP ${status}, kun ${total} produktkort${degraded ? ", X-Data-Degraded" : " - degraderet data?"}, forsøg ${attempt})`
          );
        } else if (search) {
          console.log(
            `OK   ${target} (HTTP ${status}, ${total} søgeresultater, forsøg ${attempt})`
          );
          return true;
        } else if (ratio < MIN_MATCH_RATIO) {
          console.log(
            `FEJL ${url} (HTTP ${status}, kun ${(ratio * 100).toFixed(0)}% af ${total} kort har en butiksmatch - Rema-only-cache?, forsøg ${attempt})`
          );
        } else {
          console.log(
            `OK   ${url} (HTTP ${status}, ${total} produkter, ${(ratio * 100).toFixed(0)}% matchet, forsøg ${attempt})`
          );
          return true;
        }
      } else {
        console.log(`FEJL ${url} (HTTP ${status}, forsøg ${attempt})`);
      }
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
// det mest oplagte automatiserings-fingeraftryk.
//
// 2026-07-27: mønsteret viste sig IKKE at være timing-baseret. Uanset
// pause mellem sideindlæsninger bestod den FØRSTE url i sessionen altid,
// og enhver efterfølgende url fejlede altid (403) - også med kun to sider
// og en 2s pause. Det er selve session-/cookie-genbruget mellem
// sideindlæsninger der bliver mistænkeliggjort, ikke hastigheden. Derfor:
// hver url får sin egen browser-instans og kontekst, så hvert tjek ligner
// et uafhængigt førstegangsbesøg i stedet for flere sider i samme session.
async function checkUrl(url) {
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
    return await check(page, url);
  } finally {
    await browser.close();
  }
}

let fail = false;
for (let i = 0; i < urls.length; i++) {
  if (i > 0) await sleep(2_000);
  const ok = await checkUrl(urls[i]);
  if (!ok) fail = true;
}
if (fail) {
  console.log(
    "::error::madshopper.dk svarer ikke korrekt. Tjek Cloudflare-dashboardet (Workers & Pages -> madshopper -> Deployments) og rul evt. tilbage til seneste stabile version."
  );
  process.exit(1);
}
