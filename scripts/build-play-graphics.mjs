#!/usr/bin/env node
/**
 * Genererer Google Plays feature graphic (1024x500) til
 * apps/mobile/store/graphics/feature-graphic-1024x500.png.
 *
 *     node scripts/build-play-graphics.mjs
 *
 * Køres MANUELT og lokalt (kræver Playwright + Chromium, som repoet allerede
 * bruger til røgtest). Resultatet committes, så hverken CI eller deploy
 * afhænger af scriptet - præcis som scripts/build-icons.py.
 *
 * Hvorfor Chromium og ikke Pillow: teksten skal sættes i sidens egen
 * skrifttype med rigtig kerning. Skrifttypen læses fra static/ hvis den ligger
 * der; ellers bruges systemets sans, og grafikken er stadig brugbar - men se
 * den efter før upload.
 *
 * Play beskærer grafikken forskelligt afhængigt af, hvor den vises, så alt
 * væsentligt holdes inden for de midterste ~80 %.
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const OUT = join(ROOT, 'apps/mobile/store/graphics/feature-graphic-1024x500.png');

const GREEN = '#059669';

// Samme kurve-glyf som static/favicon.svg og app-ikonet. Tegnet inline (ikke
// som PNG-ikon) fordi tilen her vender farverne om: grøn kurv på hvidt, så
// mærket ikke forsvinder ind i den grønne baggrund.
const glyph = `<svg viewBox="0 0 24 24" fill="none" stroke="${GREEN}" stroke-width="2"
  stroke-linecap="round" stroke-linejoin="round">
  <path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>
  <circle cx="9" cy="21" r="1.9" fill="${GREEN}" stroke="none"/>
  <circle cx="20" cy="21" r="1.9" fill="${GREEN}" stroke="none"/>
</svg>`;

const html = `<!doctype html>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1024px; height: 500px; }
  body {
    background: ${GREEN};
    display: flex;
    align-items: center;
    gap: 52px;
    padding: 0 96px;
    font-family: 'Plus Jakarta Sans', 'Inter', system-ui, -apple-system, sans-serif;
    color: #fff;
    -webkit-font-smoothing: antialiased;
  }
  .tile {
    width: 184px; height: 184px; flex: none;
    background: #fff; border-radius: 40px;
    display: flex; align-items: center; justify-content: center;
  }
  .tile svg { width: 108px; height: 108px; }
  h1 { font-size: 74px; font-weight: 800; letter-spacing: -0.02em; line-height: 1; }
  p  { font-size: 30px; font-weight: 500; line-height: 1.35; margin-top: 16px; color: rgba(255,255,255,.92); }
  .stores { margin-top: 20px; font-size: 21px; font-weight: 600; color: rgba(255,255,255,.78); white-space: nowrap; }
</style>
<div class="tile">${glyph}</div>
<div>
  <h1>MadShopper</h1>
  <p>Find de billigste dagligvarer<br>&mdash; én kurv, alle butikker</p>
  <div class="stores">14+ butikker &middot; Rema 1000 &middot; Bilka &middot; Netto &middot; Føtex &middot; Lidl</div>
</div>`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1024, height: 500 }, deviceScaleFactor: 1 });
await page.setContent(html, { waitUntil: 'load' });
await page.evaluate(() => document.fonts.ready);
mkdirSync(dirname(OUT), { recursive: true });
await page.screenshot({ path: OUT });
await browser.close();
console.log(`  apps/mobile/store/graphics/feature-graphic-1024x500.png (1024x500)`);
