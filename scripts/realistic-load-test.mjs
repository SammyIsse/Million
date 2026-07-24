#!/usr/bin/env node
// Maaler hvor mange SAMTIDIGE BESOEGENDE sitet kan baere - ikke hvor mange
// samtidige soegninger (det er scripts/search-load-test.mjs).
//
// Forskellen er stor og var kilden til en misforstaaelse 2026-07-24:
// search-load-test lader hver virtuel bruger soege uafbrudt uden pause, hvilket
// er worst case og ikke ligner et menneske. Her gennemloeber hver virtuel bruger
// i stedet en rigtig kunderejse med taenkepauser:
//
//   forside -> (4-9s) -> soeg -> (6-14s laese resultater) -> ofte soeg igen
//   -> (5-10s) -> ofte en kategoriside -> (4-8s) -> forfra
//
// Det betyder at langt de fleste requests rammer edge-cachen (forside og
// kategorier er cachebare, se _CACHEABLE_ENDPOINTS i app.py), praecis som i
// virkeligheden - og at kun soegningerne koster CPU i worker'en.
//
// Hver stage koerer et fast TIDSRUM (ikke et fast antal requests), saa vi maaler
// en steady-state trafikmaengde og faar requests/sekund ud - det tal der kan
// sammenlignes direkte med rigtig trafik.
//
// Brug: node scripts/realistic-load-test.mjs [base-url] [staging-noegle]
import { chromium } from "playwright";
import { readFileSync } from "node:fs";

const BASE = (process.argv[2] || "https://madshopper-dev.kasp478g.workers.dev").replace(/\/$/, "");

// Samme produktionsspaerring som search-load-test.mjs - se begrundelsen dér.
if (/madshopper\.dk/i.test(BASE) && !process.argv.includes("--jeg-vil-belaste-produktion")) {
  console.error(
    `NÆGTER at belaste produktion (${BASE}).\n` +
    `Koer mod staging: node scripts/realistic-load-test.mjs`
  );
  process.exit(2);
}

let ACCESS_KEY = (process.argv.slice(3).find((a) => !a.startsWith("--")) || "").trim();
if (!ACCESS_KEY) {
  try {
    ACCESS_KEY = readFileSync(new URL("../.staging-secret", import.meta.url), "utf8").trim();
  } catch { /* ingen noegle - rammer et site uden gate */ }
}

// Andel af soegninger der TVINGES uden om edge-cachen (0-100).
// Standardlisten er kun 20 soegeord, saa uden dette rammer ~96% af
// soegningerne cachen - et kunstigt pænt resultat. Med --miss=100 maaler man
// bunden: hver eneste soegning renderes forfra. Sandheden for rigtig trafik
// ligger imellem og afhaenger af hvor tit brugere soeger paa det samme.
const MISS_PCT = Number(
  (process.argv.find((a) => a.startsWith("--miss=")) || "--miss=0").split("=")[1]
);

// --stages=5,10,15,... naar man vil maale finere omkring en forventet graense.
const STAGES = (process.argv.find((a) => a.startsWith("--stages=")) || "")
  .split("=")[1]?.split(",").map(Number).filter((n) => n > 0) || [5, 10, 25, 50, 100];
const STAGE_SECONDS = 60;
// Fejltilstanden varer ved efter belastningen stopper (bekraeftet 2026-07-24:
// sitet var utilgaengeligt ~1,5 min bagefter), saa hver stage skal maale sin
// EGEN kapacitet - ikke eftervirkningen af den forrige.
const RECOVERY_SECONDS = 45;
const BAD_RATE_STOP = 0.02; // 2% fejl = for daarligt til rigtige brugere
// Virtuelle brugere fordeles over flere faner, saa de ikke alle deler én
// HTTP/2-forbindelse (head-of-line blocking ville maale vores egen klient).
const USERS_PER_PAGE = 25;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Koeres INDE i browseren: rigtig fetch med rigtigt TLS-fingeraftryk og de
// cookies warmup satte. Node's fetch/context.request bliver blokeret 403 af
// Cloudflares Bot Fight Mode (se scripts/smoke-test.mjs).
const JOURNEY = async ({ users, seconds, queries, categories, missPct }) => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rand = (a, b) => Math.floor(Math.random() * (b - a + 1)) + a;
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  const deadline = Date.now() + seconds * 1000;
  const results = [];

  async function hit(path, kind) {
    const t0 = performance.now();
    try {
      const res = await fetch(path);
      const ms = performance.now() - t0;
      // Edge-cache-status: CF-Cache-Status findes ikke paa worker-svar, saa vi
      // kan ikke skelne hit/miss her - latenstiden viser det i praksis.
      let ok = res.status === 200;
      if (ok && kind === "search") {
        try {
          const d = await res.json();
          ok = typeof d?.html === "string" && !d.html.includes('class="error"');
        } catch { ok = false; }
      }
      return { kind, ms, status: res.status, ok };
    } catch (e) {
      return { kind, ms: performance.now() - t0, status: 0, ok: false };
    }
  }

  // Soege-URL. Ved cache-miss tilfoejes en ubrugt parameter: den aendrer
  // worker'ens cache-noegle (hele URL'en indgaar, se _cache_key i
  // src/worker.py) mens Flask ignorerer den, saa svaret koster PRAECIS samme
  // arbejde - blot uden at kunne serveres fra cachen. Et vroevle-soegeord
  // ville derimod give nul resultater og dermed vaere kunstigt billigt.
  const searchUrl = () => {
    const base = `/search?q=${encodeURIComponent(pick(queries))}&stores=`;
    return Math.random() * 100 < missPct ? `${base}&_=${Math.random().toString(36).slice(2)}` : base;
  };

  async function oneUser() {
    // Spred starten, saa alle brugere ikke rammer i samme millisekund.
    await sleep(rand(0, 3000));
    while (Date.now() < deadline) {
      results.push(await hit("/", "side"));
      await sleep(rand(4000, 9000));            // laeser forsiden

      results.push(await hit(searchUrl(), "search"));
      await sleep(rand(6000, 14000));           // laeser soegeresultater

      if (Math.random() < 0.6) {                // soeger tit én gang mere
        results.push(await hit(searchUrl(), "search"));
        await sleep(rand(5000, 10000));
      }
      if (Math.random() < 0.5) {                // klikker af og til en kategori
        results.push(await hit(`/${pick(categories)}`, "side"));
        await sleep(rand(4000, 8000));
      }
    }
  }

  await Promise.all(Array.from({ length: users }, oneUser));
  return results;
};

const QUERIES = [
  "mælk", "hakket oksekød", "æg", "kaffe", "smør", "ost", "brød", "kylling",
  "pasta", "ris", "tomater", "yoghurt", "øl", "kartofler", "laks", "chokolade",
  "bananer", "rugbrød", "leverpostej", "havregryn",
];
const CATEGORIES = [
  "Mejeri", "Koed_og_fisk", "Frugt_og_groent", "Broed_og_kager",
  "Kolonial", "Frost", "Drikkevarer", "Slik",
];

function pct(sorted, p) {
  if (!sorted.length) return 0;
  return sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))];
}

function report(users, samples, seconds) {
  const total = samples.length;
  const bad = samples.filter((s) => !s.ok);
  const badRate = total ? bad.length / total : 1;
  const rps = (total / seconds).toFixed(1);

  const line = (kind) => {
    const set = samples.filter((s) => s.kind === kind);
    const okTimes = set.filter((s) => s.ok).map((s) => s.ms).sort((a, b) => a - b);
    const nBad = set.filter((s) => !s.ok).length;
    return `${kind.padEnd(6)} n=${String(set.length).padStart(4)} fejl=${String(nBad).padStart(3)}` +
           `  p50=${pct(okTimes, 50).toFixed(0).padStart(5)}ms  p95=${pct(okTimes, 95).toFixed(0).padStart(5)}ms`;
  };

  const codes = new Map();
  for (const s of bad) codes.set(s.status, (codes.get(s.status) ?? 0) + 1);
  const codeStr = [...codes.entries()].map(([c, n]) => `${n}x${c || "netfejl"}`).join(" ") || "-";

  console.log(`\n=== ${users} samtidige besoegende (${seconds}s) ===`);
  console.log(`  i alt: ${total} requests, ${rps} req/s, ${bad.length} fejl (${(badRate * 100).toFixed(2)}%)  ${codeStr}`);
  console.log(`  ${line("side")}`);
  console.log(`  ${line("search")}`);
  return badRate;
}

const browser = await chromium.launch({ args: ["--disable-blink-features=AutomationControlled"] });
try {
  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });

  const warmup = await context.newPage();
  let cleared = false;
  for (let i = 1; i <= 3 && !cleared; i++) {
    try {
      await warmup.goto(ACCESS_KEY ? `${BASE}/?k=${encodeURIComponent(ACCESS_KEY)}` : `${BASE}/`,
                        { waitUntil: "load", timeout: 30_000 });
      await warmup.waitForFunction(() => document.body?.innerText?.includes("MadShopper"),
                                   { timeout: 20_000 });
      cleared = true;
    } catch (e) {
      console.log(`warmup forsoeg ${i} fejlede: ${e.message}`);
      if (i < 3) await sleep(15_000);
    }
  }
  if (!cleared) {
    console.log(`::error::Kunne ikke faa en gyldig session mod ${BASE}.`);
    process.exit(1);
  }
  console.log(`Warmet op mod ${BASE}`);
  console.log(`Hver bruger: forside -> soeg -> (ofte) soeg igen -> (ofte) kategori, med 4-14s taenkepauser`);
  console.log(`Cache-miss paatvunget: ${MISS_PCT}% af soegningerne`);
  console.log(`Stopper naar fejlraten overstiger ${BAD_RATE_STOP * 100}%`);

  for (const users of STAGES) {
    const nPages = Math.max(1, Math.ceil(users / USERS_PER_PAGE));
    const pages = [warmup];
    for (let i = 1; i < nPages; i++) {
      const p = await context.newPage();
      await p.goto(`${BASE}/`, { waitUntil: "domcontentloaded", timeout: 30_000 });
      pages.push(p);
    }
    for (const p of pages) p.setDefaultTimeout(0);

    const share = Math.floor(users / pages.length);
    const extra = users - share * pages.length;
    const batches = await Promise.all(
      pages.map((p, i) =>
        p.evaluate(JOURNEY, {
          users: share + (i < extra ? 1 : 0),
          seconds: STAGE_SECONDS,
          queries: QUERIES,
          categories: CATEGORIES,
          missPct: MISS_PCT,
        })
      )
    );
    for (let i = 1; i < pages.length; i++) await pages[i].close();

    const badRate = report(users, batches.flat(), STAGE_SECONDS);
    if (badRate > BAD_RATE_STOP) {
      console.log(`\n-> Graensen gaar ved ca. ${users} samtidige besoegende (fejlrate over ${BAD_RATE_STOP * 100}%).`);
      break;
    }
    if (users !== STAGES[STAGES.length - 1]) {
      console.log(`  (venter ${RECOVERY_SECONDS}s saa naeste stage maaler sin egen kapacitet)`);
      await sleep(RECOVERY_SECONDS * 1000);
    }
  }
} finally {
  await browser.close();
}
