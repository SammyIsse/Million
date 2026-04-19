from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from openpyxl import Workbook
import time
import re

# Alle kategorilinks
URLS = [
    "https://www.bilkatogo.dk/kategori/koed-og-fisk/",
    "https://www.bilkatogo.dk/kategori/frugt-og-groent/",
    "https://www.bilkatogo.dk/kategori/mejeri-og-koel/",
    "https://www.bilkatogo.dk/kategori/drikkevarer/",
    "https://www.bilkatogo.dk/kategori/broed-og-kager/",
    "https://www.bilkatogo.dk/kategori/kolonial/",
    "https://www.bilkatogo.dk/kategori/mad-fra-hele-verden/",
    "https://www.bilkatogo.dk/kategori/slik-og-snacks/",
    "https://www.bilkatogo.dk/kategori/frost/"
]

def handle_cookies(driver):
    """
    Usercentrics CMP – prøver fire metoder i rækkefølge:
      1. Shadow DOM via #usercentrics-root (standard Usercentrics)
      2. Direkte DOM – id="deny"
      3. Direkte DOM – class .uc-deny-button
      4. JavaScript UC API
    """
    print("  → Venter på cookie-banner...")
    time.sleep(3)

    # --- Metode 1: Shadow DOM ---
    try:
        result = driver.execute_script("""
            const host = document.querySelector('#usercentrics-root');
            if (host && host.shadowRoot) {
                const btn = host.shadowRoot.querySelector('button[data-action-type="deny"]')
                           || host.shadowRoot.querySelector('#deny')
                           || host.shadowRoot.querySelector('.uc-deny-button');
                if (btn) { btn.click(); return true; }
            }
            return false;
        """)
        if result:
            print("  ✓ Cookies afvist via Shadow DOM")
            time.sleep(1)
            return
    except:
        pass

    # --- Metode 2: Direkte DOM id ---
    try:
        btn = driver.find_element(By.ID, "deny")
        btn.click()
        print("  ✓ Cookies afvist via direkte DOM (id)")
        time.sleep(1)
        return
    except:
        pass

    # --- Metode 3: Direkte DOM class ---
    try:
        btn = driver.find_element(By.CSS_SELECTOR, ".uc-deny-button")
        btn.click()
        print("  ✓ Cookies afvist via direkte DOM (class)")
        time.sleep(1)
        return
    except:
        pass

    # --- Metode 4: UC JavaScript API ---
    try:
        driver.execute_script("""
            if (window.__ucCmp && window.__ucCmp.denyAll) {
                window.__ucCmp.denyAll();
            }
        """)
        print("  ✓ Cookies afvist via UC JS API")
        time.sleep(1)
        return
    except:
        pass

    print("  ⚠ Ingen cookie-banner fundet – fortsætter alligevel")

def scroll_to_element(driver, element):
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});", element
    )

def click_load_more(driver):
    try:
        btn = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable((
                By.XPATH, "//button[.//span[normalize-space()='Indlæs flere']]"
            ))
        )
        scroll_to_element(driver, btn)
        time.sleep(0.3)
        btn.click()
        return True
    except:
        return False

def load_all_products_on_page(driver):
    max_clicks = 50  # Sikkerhedsgrænse – stop efter 50 klik
    clicks = 0
    while clicks < max_clicks:
        before = len(driver.find_elements(By.CSS_SELECTOR, "div.product-card-container"))
        if not click_load_more(driver):
            break
        clicks += 1
        for _ in range(20):  # Maks 10 sek ventetid per klik
            time.sleep(0.5)
            after = len(driver.find_elements(By.CSS_SELECTOR, "div.product-card-container"))
            if after > before:
                break
        print(f"    Indlæst: {after} produkter", end="\r")

def parse_description(description):
    """
    Returnerer: type, vægt, kg_pris
    """
    product_type = ""
    weight = ""
    kg_price = ""

    # Type (første tekst / strong)
    type_match = re.search(r"^[A-Za-zÆØÅæøå\s]+", description)
    if type_match:
        product_type = type_match.group(0).strip()

    # Vægt: g, kg, L
    weight_match = re.search(
        r"(\d+[.,]?\d*)\s*(kg|g|l)",
        description,
        re.IGNORECASE
    )
    if weight_match:
        weight = f"{weight_match.group(1)} {weight_match.group(2)}"

    # Kg / g / L pris  ✅
    kg_price_match = re.search(
        r"(\d+[.,]?\d*)\s*(?:kr\s*)?/\s*(kg|g|l)",
        description,
        re.IGNORECASE
    )
    if kg_price_match:
        kg_price = f"{kg_price_match.group(1)} kr/{kg_price_match.group(2)}"

    return product_type, weight, kg_price

def collect_all_products(driver):
    cards = driver.find_elements(By.CSS_SELECTOR, "div.product-card-container")
    rows = []

    for card in cards:
        try:
            name = card.find_element(By.CSS_SELECTOR, "p.name").text.strip()
        except:
            name = ""

        try:
            description = card.find_element(By.CSS_SELECTOR, "p.description").text.strip()
        except:
            description = ""

        product_type, weight, kg_price = parse_description(description)

        try:
            raw_price = card.find_element(
                By.CSS_SELECTOR, "span.product-price__integer"
            ).text.strip()
            price_match = re.search(r"\d+", raw_price)
            price = price_match.group() if price_match else "0"
        except:
            price = "0"

        rows.append((name, product_type, weight, kg_price, price))

    return rows

def main():
    options = Options()
    # Brug den nye headless-mode (kræves fra Chrome 112+)
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    # Anti-detektions-flags så siden ikke ved vi er en bot
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(service=Service(), options=options)

    wb = Workbook()
    ws = wb.active
    ws.title = "Produkter"
    ws.append(["Navn", "Type", "Vægt", "Kg-pris", "Pris"])

    total_saved = 0

    try:
        for i, url in enumerate(URLS, 1):
            kategori = url.split("/kategori/")[1].strip("/")
            print(f"\n[{i}/{len(URLS)}] Henter: {kategori}")
            driver.get(url)
            time.sleep(1.5)
            handle_cookies(driver)

            load_all_products_on_page(driver)
            print()  # Ny linje efter \r-statuslinjen

            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

            rows = collect_all_products(driver)
            for row in rows:
                ws.append(row)
                total_saved += 1
            print(f"  ✓ {len(rows)} varer fundet i denne kategori")

    finally:
        driver.quit()

    filename = "produktnavne.xlsx"
    wb.save(filename)

    print(f"{total_saved} varer er gemt i excel arket: {filename}")

if __name__ == "__main__":
    main()

