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
    try:
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "declineButton"))
        ).click()
    except:
        pass

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
    while True:
        before = len(driver.find_elements(By.CSS_SELECTOR, "div.product-card-container"))
        if not click_load_more(driver):
            break
        for _ in range(30):
            time.sleep(0.5)
            after = len(driver.find_elements(By.CSS_SELECTOR, "div.product-card-container"))
            if after > before:
                break

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
    options.headless = True
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(service=Service(), options=options)

    wb = Workbook()
    ws = wb.active
    ws.title = "Produkter"
    ws.append(["Navn", "Type", "Vægt", "Kg-pris", "Pris"])

    total_saved = 0

    try:
        for url in URLS:
            driver.get(url)
            time.sleep(1.5)
            handle_cookies(driver)

            load_all_products_on_page(driver)

            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

            rows = collect_all_products(driver)
            for row in rows:
                ws.append(row)
                total_saved += 1

    finally:
        driver.quit()

    filename = "produktnavne.xlsx"
    wb.save(filename)

    print(f"{total_saved} varer er gemt i excel arket: {filename}")

if __name__ == "__main__":
    main()
