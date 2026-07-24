#!/usr/bin/env node
// Finder hvor mange SAMTIDIGE aktive søgere /search kan bære, før fejlraten
// stiger. Bruges til at give et målt tal i stedet for et arkitektur-gæt
// (se samtalen der udløste dette script).
//
// /search er den dyre sti: ingen edge-cache (JSON, intet Cache-Control:
// public), og på edge går den til D1 med et uindekseret
// `search_text LIKE '%token%'` - reelt et fuldt table scan pr. søgning
// (scripts/seed-d1.py har intet indeks på search_text, og et LIKE med
// wildcard foran kan alligevel ikke bruge et indeks selv hvis der var et).
//
// MÅ IKKE bruge context.request eller Node's fetch direkte: Cloudflares
// gratis Bot Fight Mode blokerer det med 403 (bekræftet i
// scripts/smoke-test.mjs, forsøg #79) fordi det mangler browserens rigtige
// TLS-fingeraftryk. Kører derfor fetch() INDE I siden via page.evaluate,
// og genbruger warmup-sidens cf_clearance-cookie på tværs af faner i samme
// context - præcis samme mønster som smoke-test.mjs.
//
// Brug: node scripts/search-load-test.mjs [base-url] [staging-access-key]
//   fx  node scripts/search-load-test.mjs https://madshopper-dev.kasp478g.workers.dev "$(cat .staging-secret)"
import { chromium } from "playwright";
import { readFileSync } from "node:fs";

const BASE = (process.argv[2] || "https://madshopper-dev.kasp478g.workers.dev").replace(/\/$/, "");

// PRODUKTIONSSPÆRRING. Testen belaster med vilje til den fejler, og en måling
// 2026-07-24 mod staging gjorde sitet utilgaengeligt i ~1,5 minut BAGEFTER -
// fejltilstanden varer ved efter belastningen stopper (samme mekanisme som
// nedbruddet 2026-07-19). Peger man den mod madshopper.dk, tager man rigtige
// brugere ned. Kraever derfor et eksplicit flag, saa det aldrig kan ske ved en
// tastefejl i URL'en.
const ALLOW_PROD = process.argv.includes("--jeg-vil-belaste-produktion");
if (/madshopper\.dk/i.test(BASE) && !ALLOW_PROD) {
  console.error(
    `NÆGTER at belaste produktion (${BASE}).\n` +
    `Denne test goer sitet utilgaengeligt i flere minutter - koer den mod staging:\n` +
    `  node scripts/search-load-test.mjs\n` +
    `Er det bevidst (fx planlagt vindue uden trafik), tilfoej --jeg-vil-belaste-produktion`
  );
  process.exit(2);
}

// Flag frasorteres, saa "--jeg-vil-belaste-produktion" som 3. argument ikke
// bliver laest som adgangsnoeglen.
let ACCESS_KEY = (process.argv.slice(3).find((a) => !a.startsWith("--")) || "").trim();
if (!ACCESS_KEY) {
  try {
    ACCESS_KEY = readFileSync(new URL("../.staging-secret", import.meta.url), "utf8").trim();
  } catch {
    // Ingen lokal fil - fortsætter uden nøgle (rammer produktion uden gate).
  }
}
const WARMUP_URL = ACCESS_KEY ? `${BASE}/?k=${encodeURIComponent(ACCESS_KEY)}` : `${BASE}/`;

// Rigtige søgeord fra dansk dagligvarehandel - blandet længde, så nogle
// matcher bredt (mange rækker) og andre snævert.
const QUERIES = [
  "mælk", "hakket oksekød", "æg", "kaffe", "smør", "ost", "brød", "kylling",
  "pasta", "ris", "tomater", "yoghurt", "øl", "kartofler", "laks", "chokolade",
];
// Reelt brugermønster: skriv, vent 500ms (debounce i script.js:1826), se
// resultat, evt. skriv en ny søgning. 2-4 søgninger pr. "besøg".
const SEARCHES_PER_USER_MIN = 2;
const SEARCHES_PER_USER_MAX = 4;
const PAUSE_MS_MIN = 400;
const PAUSE_MS_MAX = 900;

// Ramp: stopper automatisk ved første stage med for høj fejlrate, så vi
// ikke unødigt hamrer løs på staging efter vi har fundet knækpunktet.
const STAGES = [2, 3, 4, 5, 6, 8, 10, 15, 20];
const BAD_RATE_STOP = 0.2; // 20% fejl/timeout -> stop ramp'en her

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rand = (min, max) => Math.floor(Math.random() * (max - min + 1)) + min;
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

function percentile(sorted, p) {
  if (sorted.length === 0) return 0;
  const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return sorted[idx];
}

// Kører inde i browsersiden: bruger dens fetch (rigtig TLS-fingeraftryk,
// deler cf_clearance + evt. ms_staging-cookie automatisk).
async function timedSearch(page, query) {
  return page.evaluate(async (q) => {
    const t0 = performance.now();
    try {
      // Præcis som søgefeltet i static/js/script.js:1801 - INGEN
      // X-Requested-With. Den header ville få src/worker.py:319 til at
      // springe edge-cache-stien over, altså teste en anden kodesti end
      // brugerne faktisk rammer.
      const res = await fetch(`/search?q=${encodeURIComponent(q)}&stores=`);
      const status = res.status;
      let bodyOk = true;
      let errSnippet = "";
      const rawText = await res.clone().text().catch(() => "");
      try {
        const data = JSON.parse(rawText);
        bodyOk = !(typeof data?.html === "string" && data.html.includes('class="error"'));
        if (!bodyOk) errSnippet = data.html.slice(0, 300);
      } catch {
        bodyOk = false;
        // Cloudflares fejlside: den præcise kode afgør diagnosen (1101 =
        // worker kastede en exception, 1102 = CPU-budget opbrugt, 1042 =
        // ulovligt subrequest). Regex mod rå HTML fandt den ikke (kodens
        // format varierer), så vi parser siden til ren tekst - dér står den
        // menneskelæselige besked uden markup-støj.
        let plain = rawText;
        try {
          plain = new DOMParser().parseFromString(rawText, "text/html")
            .body?.innerText || rawText;
        } catch { /* behold rå tekst */ }
        errSnippet = plain.replace(/\s+/g, " ").trim().slice(0, 400);
      }
      const ok = status === 200 && bodyOk;
      return { ms: performance.now() - t0, status, ok, err: ok ? "" : errSnippet };
    } catch (e) {
      return { ms: performance.now() - t0, status: 0, ok: false, err: String(e?.message || e) };
    }
  }, query);
}

async function runVirtualUser(page) {
  const results = [];
  const n = rand(SEARCHES_PER_USER_MIN, SEARCHES_PER_USER_MAX);
  for (let i = 0; i < n; i++) {
    if (i > 0) await sleep(rand(PAUSE_MS_MIN, PAUSE_MS_MAX));
    results.push(await timedSearch(page, pick(QUERIES)));
  }
  return results;
}

async function runStage(context, concurrency) {
  const pages = [];
  for (let i = 0; i < concurrency; i++) pages.push(await context.newPage());
  try {
    // Nye faner starter på about:blank - fetch('/search?...') derfra har intet
    // origin at resolve den relative URL imod og fejler øjeblikkeligt (0ms,
    // network-fail). Naviger til sitet først, ligesom en rigtig besøgende ville.
    await Promise.all(
      pages.map((p) => p.goto(`${BASE}/`, { waitUntil: "domcontentloaded", timeout: 30_000 }))
    );
    const perUser = await Promise.all(pages.map((p) => runVirtualUser(p)));
    return perUser.flat();
  } finally {
    await Promise.all(pages.map((p) => p.close()));
  }
}

function report(concurrency, samples) {
  const total = samples.length;
  const bad = samples.filter((s) => !s.ok).length;
  const times = samples.filter((s) => s.ok).map((s) => s.ms).sort((a, b) => a - b);
  const statusCounts = new Map();
  for (const s of samples) statusCounts.set(s.status, (statusCounts.get(s.status) ?? 0) + 1);
  const statusStr = [...statusCounts.entries()].map(([c, n]) => `${n}x${c || "network-fail"}`).join(" ");
  const badRate = total ? bad / total : 1;
  console.log(
    `samtidige brugere=${concurrency.toString().padStart(3)}  requests=${total.toString().padStart(4)}  ` +
    `fejl=${bad.toString().padStart(3)} (${(badRate * 100).toFixed(1)}%)  ` +
    `p50=${percentile(times, 50).toFixed(0)}ms  p95=${percentile(times, 95).toFixed(0)}ms  max=${(times.at(-1) ?? 0).toFixed(0)}ms  [${statusStr}]`
  );
  const uniqueErrs = [...new Set(samples.filter((s) => !s.ok && s.err).map((s) => s.err))];
  for (const e of uniqueErrs.slice(0, 5)) console.log(`  fejltekst: ${e.replace(/\s+/g, " ").trim()}`);
  return badRate;
}

const browser = await chromium.launch({
  args: ["--disable-blink-features=AutomationControlled"],
});
let cleared = false;
try {
  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });

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
  await warmup.close();

  if (!cleared) {
    console.log(`::error::Kunne ikke etablere en gyldig session mod ${BASE}. Load-testen kan ikke køre.`);
    process.exit(1);
  }

  console.log(`Warmet op mod ${BASE} - starter ramp (stopper hvis fejlrate > ${BAD_RATE_STOP * 100}%)\n`);

  for (const concurrency of STAGES) {
    const samples = await runStage(context, concurrency);
    const badRate = report(concurrency, samples);
    if (badRate > BAD_RATE_STOP) {
      console.log(
        `\n-> Knækpunkt fundet omkring ${concurrency} samtidige aktive søgere (fejlrate over ${BAD_RATE_STOP * 100}%).`
      );
      break;
    }
    // Lang pause mellem stages, så hver stage måler SIN EGEN kapacitet.
    // scripts/smoke-test.mjs dokumenterer (2026-07-19) at fejltilstanden
    // VARER VED efter belastningen stopper - med en kort pause måler man
    // derfor eftervirkningen af forrige stage i stedet.
    await sleep(30_000);
  }
} finally {
  await browser.close();
}
