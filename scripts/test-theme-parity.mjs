#!/usr/bin/env node
// Tema-paritet mellem web og app.
//
// BAGGRUND: app'ens ThemeContext har altid haft TRE valg (følg system / lys /
// mørk), mens webben kun havde en til/fra-kontakt og behandlede "aldrig valgt"
// som lys. Webben fik de tre valg 19-08-2026 - med NØJAGTIG samme
// lagringskontrakt som app'en, så en bruger møder samme opførsel begge steder:
//
//     localStorage/AsyncStorage['madshopper_darkmode']
//       'true'      -> mørk
//       'false'     -> lys
//       fraværende  -> følg systemet (prefers-color-scheme)
//
// Kontrakten er nem at bryde ved et uheld: sætter man fx 'false' i stedet for
// at FJERNE nøglen ved "følg system", er de to platforme lydløst ude af trit,
// og intet andet i projektet ville opdage det. Derfor denne test.
//
// Brug: node scripts/test-theme-parity.mjs [base-url]
//   fx  node scripts/test-theme-parity.mjs http://127.0.0.1:5001
//
// Kræver en kørende server (lokal Flask eller staging) og playwright.

import { chromium } from "playwright";

const BASE = (process.argv[2] || "http://127.0.0.1:5001").replace(/\/$/, "");
const KEY = "madshopper_darkmode";

let fails = 0;
function check(label, ok, extra = "") {
  console.log((ok ? "  OK   " : "  FEJL ") + label + (extra ? ` (${extra})` : ""));
  if (!ok) fails++;
}

const themeAttr = (page) => page.getAttribute("body", "data-theme");
const stored = (page) => page.evaluate((k) => localStorage.getItem(k), KEY);
const activeOption = (page) =>
  page.$eval(".theme-option.active", (el) => el.dataset.themeMode).catch(() => null);

// Temaet sættes af et inline-script i toppen af <body>, men .theme-option-
// markeringen sker først i initSettings() - derfor ventes der på script.js.
async function open(page) {
  await page.goto(`${BASE}/`, { waitUntil: "load" });
  await page.waitForFunction(() => typeof window.setThemeMode === "function");
}

const browser = await chromium.launch();

// 1) Førstegangsbesøg følger systemet - i BEGGE retninger.
for (const [scheme, forventet] of [["dark", "dark"], ["light", null]]) {
  const ctx = await browser.newContext({ colorScheme: scheme });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  const faktisk = await themeAttr(page);
  check(
    `førstegangsbesøg, system=${scheme} -> data-theme=${forventet}`,
    faktisk === forventet,
    `fik ${faktisk}`,
  );
  await ctx.close();
}

// 2) De tre valg: virkning, markering, lagring, holdbarhed.
{
  const ctx = await browser.newContext({ colorScheme: "dark" });
  const page = await ctx.newPage();
  await open(page);
  check('"Følg system" er standardvalget', (await activeOption(page)) === "system");

  await page.evaluate(() => window.setThemeMode("light"));
  check('"Lys" vinder over mørkt system', (await themeAttr(page)) === null);
  check('"Lys" markeres i panelet', (await activeOption(page)) === "light");
  check('"Lys" gemmes som "false"', (await stored(page)) === "false");

  await open(page);
  check(
    '"Lys" overlever genindlæsning',
    (await themeAttr(page)) === null && (await activeOption(page)) === "light",
  );

  await page.evaluate(() => window.setThemeMode("dark"));
  check('"Mørk" giver data-theme=dark', (await themeAttr(page)) === "dark");
  check('"Mørk" gemmes som "true"', (await stored(page)) === "true");

  await page.evaluate(() => window.setThemeMode("system"));
  check('"Følg system" FJERNER nøglen (app-kontrakten)', (await stored(page)) === null);
  check('"Følg system" på mørkt system giver mørkt', (await themeAttr(page)) === "dark");
  await ctx.close();
}

// 3) Systemskift midt i en session.
{
  const ctx = await browser.newContext({ colorScheme: "light" });
  const page = await ctx.newPage();
  await open(page);
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForTimeout(200);
  check("systemskift slår igennem live i \"Følg system\"", (await themeAttr(page)) === "dark");

  await page.evaluate(() => window.setThemeMode("light"));
  await page.emulateMedia({ colorScheme: "light" });
  await page.waitForTimeout(100);
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForTimeout(200);
  check('eksplicit "Lys" ignorerer systemskift', (await themeAttr(page)) === null);
  await ctx.close();
}

await browser.close();

if (fails) {
  console.log(`\n${fails} FEJL - tema-pariteten er brudt`);
  process.exit(1);
}
console.log("\nALLE TESTS BESTAAET");
