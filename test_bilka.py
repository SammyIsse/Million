"""
Lokal test af Bilka-scraperen.
Kør med:  python test_bilka.py

Tester:
  1. Kan scraperen indlæse produkter fra mejeri-og-koel?
  2. Finder den "Minimælk 0,4% fedt" specifikt?
  3. Har produkterne de forventede felter (navn, pris, billede)?
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scraper"))

from Webscrape_Bilka import (
    create_driver,
    handle_cookies,
    load_all_products_on_page,
    collect_all_products,
)
from selenium.webdriver.common.by import By

PASS = "✅ PASS"
FAIL = "❌ FAIL"

results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    msg = f"  {status}  {name}"
    if detail:
        msg += f"\n         → {detail}"
    print(msg)
    results.append(condition)
    return condition


TEST_URL = "https://www.bilkatogo.dk/kategori/mejeri-og-koel/"
TARGET_PRODUCT = "Minimælk 0,4% fedt"

print("\n══════════════════════════════════════════")
print(" BILKA SCRAPER TEST – mejeri-og-koel")
print("══════════════════════════════════════════\n")

print(f"TEST 1: Produktindlæsning fra {TEST_URL}\n")

driver = create_driver()
try:
    driver.get(TEST_URL)
    time.sleep(2)
    handle_cookies(driver)
    time.sleep(1)

    # Tæl produkter FØR indlæsning
    before = len(driver.find_elements(By.CSS_SELECTOR, "div.product-card-container"))
    print(f"  Produktkort ved sideindlæsning: {before}")

    load_all_products_on_page(driver)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(1)

    after = len(driver.find_elements(By.CSS_SELECTOR, "div.product-card-container"))
    check("Siden har produktkort", after > 0, f"{after} produktkort fundet (startede med {before})")

    # ── TEST 2: Rå JS-ekstraktion (hurtig – ingen EAN/hash) ───────────────────
    print("\nTEST 2: Rå navneekstraktion via JS\n")

    js = """
    return Array.from(document.querySelectorAll("div.product-card-container")).map(card => {
        const nameEl = card.querySelector("p.name");
        const name = nameEl ? nameEl.innerText.trim() : "(ingen navn)";
        const priceEl = card.querySelector("[class*='price']");
        const price = priceEl ? priceEl.innerText.trim().split("\\n")[0] : "";
        const imgEl = card.querySelector("img.product-image");
        let img = "";
        if (imgEl) {
            const dataSrc = imgEl.getAttribute("data-src") || "";
            if (dataSrc && dataSrc.startsWith("http")) {
                img = dataSrc;
            } else {
                const src = imgEl.getAttribute("src") || "";
                if (src.startsWith("http") && !src.includes("loading.svg")) {
                    img = src;
                } else {
                    const srcset = imgEl.getAttribute("srcset") || "";
                    if (srcset && !srcset.includes("loading.svg")) {
                        img = srcset.split(",")[0].trim().split(" ")[0];
                    }
                }
            }
        }
        const aEl = card.querySelector("a[href]");
        const link = aEl ? aEl.getAttribute("href") : "";
        return { name, price, img, link };
    });
    """
    sample = driver.execute_script(js)

    check("JS-ekstraktion returnerer data", len(sample) > 0, f"{len(sample)} emner")

    if sample:
        has_names  = sum(1 for p in sample if p["name"] and p["name"] != "(ingen navn)")
        has_prices = sum(1 for p in sample if p["price"])
        has_imgs   = sum(1 for p in sample if p["img"] and "loading" not in p["img"])

        check("Produkter har navne",  has_names == len(sample),
              f"{has_names}/{len(sample)}")
        check("Produkter har priser (80%+)", has_prices >= len(sample) * 0.8,
              f"{has_prices}/{len(sample)}")
        check("Produkter har billeder (60%+)", has_imgs >= len(sample) * 0.6,
              f"{has_imgs}/{len(sample)}")

        # ── TEST 3: Søg specifikt efter Minimælk ─────────────────────────────
        print(f"\nTEST 3: Søger efter '{TARGET_PRODUCT}'\n")

        all_names = [p["name"] for p in sample]
        exact_match = [n for n in all_names if TARGET_PRODUCT.lower() in n.lower()]
        partial_match = [n for n in all_names if "minimælk" in n.lower() or "minimalm" in n.lower()]

        check(f"Eksakt match på '{TARGET_PRODUCT}'", len(exact_match) > 0,
              f"Fandt: {exact_match}" if exact_match else "Ikke fundet")
        check("Mindst ét produkt med 'minimælk'", len(partial_match) > 0,
              f"Fandt: {partial_match}" if partial_match else "Ikke fundet")

        # ── Vis alle fundne navne (til fejlfinding) ───────────────────────────
        print(f"\n  Alle {len(all_names)} produktnavne fra mejeri-og-koel:")
        for i, name in enumerate(sorted(all_names), 1):
            marker = " ◀ MATCH" if "minimælk" in name.lower() else ""
            print(f"    {i:3}. {name}{marker}")

        # ── Vis CSS-strukturen for det første kort (til fejlfinding) ─────────
        print("\n  Debug: HTML-struktur for første produktkort:")
        html_debug = driver.execute_script("""
            const card = document.querySelector("div.product-card-container");
            return card ? card.outerHTML.substring(0, 2000) : "Ingen kort fundet";
        """)
        print(f"  {html_debug[:1500]}")

finally:
    driver.quit()

# ─────────────────────────────────────────────────────────────
print("\n══════════════════════════════════════════")
passed = sum(results)
total  = len(results)
print(f" RESULTAT: {passed}/{total} tests bestået")
if passed == total:
    print(" Scraperen ser ud til at virke korrekt ✅")
else:
    print(" Nogle tests fejlede – tjek output ovenfor ❌")
print("══════════════════════════════════════════\n")

sys.exit(0 if passed == total else 1)
