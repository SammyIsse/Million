from webdriver_manager.chrome import ChromeDriverManager
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
import time
import re
import requests
from PIL import Image
from io import BytesIO
import imagehash
import concurrent.futures
from queue import Queue
import os
import json

URLS = [
    "https://www.bilkatogo.dk/kategori/kolonial/",
    "https://www.bilkatogo.dk/kategori/drikkevarer/",
    "https://www.bilkatogo.dk/kategori/mejeri-og-koel/",
    "https://www.bilkatogo.dk/kategori/slik-og-snacks/",
    "https://www.bilkatogo.dk/kategori/broed-og-kager/",
    "https://www.bilkatogo.dk/kategori/mad-fra-hele-verden/",
    "https://www.bilkatogo.dk/kategori/frugt-og-groent/",
    "https://www.bilkatogo.dk/kategori/koed-og-fisk/",
    "https://www.bilkatogo.dk/kategori/frost/",
    "https://www.bilkatogo.dk/kategori/kiosk/",
    "https://www.bilkatogo.dk/kategori/dyremad/",
]

# ── Antal parallelle Selenium-instanser til EAN-hentning ──────────────────────
EAN_POOL_SIZE = 12
ean_driver_pool = Queue()

# ── Normalpris Historik ───────────────────────────────────────────────────────
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NORMAL_PRICES_FILE = os.path.join(_ROOT_DIR, 'data', 'bilka_normal_prices.json')
bilka_normal_prices = {}

def load_normal_prices():
    global bilka_normal_prices
    if os.path.exists(NORMAL_PRICES_FILE):
        try:
            with open(NORMAL_PRICES_FILE, 'r', encoding='utf-8') as f:
                bilka_normal_prices = json.load(f)
            print(f"  ✓ Indlæste {len(bilka_normal_prices)} normalpriser fra historik.")
        except Exception as e:
            print(f"  ❌ Fejl ved indlæsning af normalpriser: {e}")
            bilka_normal_prices = {}
    else:
        bilka_normal_prices = {}

def save_normal_prices():
    try:
        os.makedirs(os.path.dirname(NORMAL_PRICES_FILE), exist_ok=True)
        with open(NORMAL_PRICES_FILE, 'w', encoding='utf-8') as f:
            json.dump(bilka_normal_prices, f, ensure_ascii=False, indent=2)
        print(f"  ✓ Gemte {len(bilka_normal_prices)} normalpriser til historik.")
    except Exception as e:
        print(f"  ❌ Fejl ved gemning af normalpriser: {e}")


def create_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def create_ean_driver():
    """Optimeret driver specifikt til hurtig EAN-hentning (deaktiverer billeder og CSS)"""
    options = Options()
    options.page_load_strategy = "eager"
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.stylesheet": 2
    }
    options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)


def init_ean_pool():
    print(f"  → Starter {EAN_POOL_SIZE} EAN-browsere...")
    for _ in range(EAN_POOL_SIZE):
        ean_driver_pool.put(create_ean_driver())
    print(f"  ✓ EAN-pool klar\n")


def quit_ean_pool():
    while not ean_driver_pool.empty():
        d = ean_driver_pool.get_nowait()
        try:
            d.quit()
        except:
            pass


# ── Cookie-håndtering ─────────────────────────────────────────────────────────

def handle_cookies(driver):
    print("  → Venter på cookie-banner...")
    time.sleep(1.5)

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

    for selector, method in [
        (By.ID, "deny"),
        (By.CSS_SELECTOR, ".uc-deny-button"),
    ]:
        try:
            driver.find_element(selector, method).click()
            print(f"  ✓ Cookies afvist via DOM ({method})")
            time.sleep(1)
            return
        except:
            pass

    try:
        driver.execute_script(
            "if (window.__ucCmp && window.__ucCmp.denyAll) { window.__ucCmp.denyAll(); }"
        )
        print("  ✓ Cookies afvist via UC JS API")
        time.sleep(1)
        return
    except:
        pass

    print("  ⚠ Ingen cookie-banner fundet – fortsætter alligevel")


# ── Scroll / indlæs-knap ──────────────────────────────────────────────────────

def scroll_to_element(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)


def click_load_more(driver):
    try:
        btn = WebDriverWait(driver, 10).until(
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
    max_clicks = 100
    clicks = 0
    while clicks < max_clicks:
        before = len(driver.find_elements(By.CSS_SELECTOR, "div.product-card-container"))
        if not click_load_more(driver):
            break
        clicks += 1
        # Vent på at nye produkter dukker op (maks 15 sek)
        for _ in range(30):
            time.sleep(0.5)
            after = len(driver.find_elements(By.CSS_SELECTOR, "div.product-card-container"))
            if after > before:
                break
        # Giv siden ekstra tid til at gøre knappen klar igen inden næste klik
        time.sleep(2)
        print(f"    Indlæst: {after} produkter", end="\r")


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_description(description):
    product_type = ""
    weight = ""
    kg_price = ""

    weight_match = re.search(r"(\d+[.,]?\d*)\s*(kg|g|l|ml|cl|dl|stk)", description, re.IGNORECASE)
    if weight_match:
        unit_found = weight_match.group(2).lower()
        weight = f"{weight_match.group(1)} {unit_found}"
        product_type = description[:weight_match.start()].strip().strip(',| -').strip()
    else:
        type_match = re.search(r"^[^,]+", description)
        if type_match:
            product_type = type_match.group(0).strip()

    kg_price_match = re.search(
        r"(\d+[.,]?\d*)\s*(?:kr\s*)?/\s*(kg|g|l|ml|cl|dl)", description, re.IGNORECASE
    )
    if kg_price_match:
        kg_price = f"{kg_price_match.group(1)} kr/{kg_price_match.group(2)}"

    return product_type, weight, kg_price


# ── EAN via Selenium pool ─────────────────────────────────────────────────────

def fetch_ean_selenium(product_url):
    if not product_url:
        return ""

    driver = ean_driver_pool.get()
    try:
        driver.get(product_url)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "content-product_details"))
            )
        except:
            return ""

        ean = driver.execute_script("""
            const section = document.getElementById('content-product_details');
            if (!section) return '';
            const rows = section.querySelectorAll('div.row');
            for (const row of rows) {
                const label = row.querySelector('span.col-4');
                const value = row.querySelector('div.col-8');
                if (label && value && label.innerText.trim() === 'EAN') {
                    return value.innerText.trim();
                }
            }
            return '';
        """)
        return ean or ""
    except Exception:
        return ""
    finally:
        ean_driver_pool.put(driver)


# ── Billedhash ────────────────────────────────────────────────────────────────

DEFAULT_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
}

def compute_image_hash(url):
    if not url:
        return ""
    try:
        response = requests.get(url, timeout=6, headers=DEFAULT_HTTP_HEADERS)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content)).convert("RGB")
        return str(imagehash.phash(img))
    except Exception:
        return ""


# ── Indsaml produkter fra én side ─────────────────────────────────────────────

def collect_all_products(driver):
    try:
        last_height = driver.execute_script("return document.body.scrollHeight")
        for i in range(0, last_height, 800):
            driver.execute_script(f"window.scrollTo(0, {i});")
            time.sleep(0.05)
        time.sleep(0.5)
    except:
        pass

    js_extract = """
    return Array.from(document.querySelectorAll("div.product-card-container")).map(card => {
        let name = "";
        let nameEl = card.querySelector("p.name");
        if (nameEl) {
            name = Array.from(nameEl.childNodes)
                .map(node => node.textContent.trim())
                .filter(t => t.length > 0)
                .join(" ");
        }

        let desc = "";
        let descEl = card.querySelector("p.description");
        if (descEl) {
            desc = Array.from(descEl.childNodes)
                .map(node => node.textContent.trim())
                .filter(t => t.length > 0)
                .join(" ");
        }

        let price = "0";
        // Try to find specific price elements to avoid grabbing kg-prices or weights
        const priceContainer = card.querySelector(".price, [data-testid='product-price'], .current-price, .sales-price") 
            || Array.from(card.querySelectorAll("[class*='price']")).find(el => !el.innerText.toLowerCase().includes('kg') && !el.innerText.toLowerCase().includes('pr.'));
            
        if (priceContainer) {
            let intPart = priceContainer.querySelector("span[class*='int'], span[class*='whole']");
            let decPart = priceContainer.querySelector("span[class*='dec'], sup");
            if (intPart && decPart) {
                price = intPart.innerText.replace(/[^\\d]/g, "") + "." + decPart.innerText.replace(/[^\\d]/g, "");
            } else {
                let rawText = priceContainer.innerText;
                if (rawText.includes(",")) {
                    price = rawText.replace(/[^\\d,]/g, "").replace(",", ".");
                } else if (rawText.includes(".")) {
                    price = rawText.replace(/[^\\d.]/g, "");
                } else {
                    price = rawText.replace(/[^\\d]/g, "");
                }
            }
        } else {
            // Fallback: regex on the card text for standard price formats
            let text = card.innerText;
            let match = text.match(/(?:\\b|^)(\\d+)[,.](\\d{2})(?:\\s*kr\\.?)/i);
            if (match) {
                price = match[1] + "." + match[2];
            }
        }

        let imgUrl = "";
        let img = card.querySelector("img.product-image");
        if (img) {
            let dataSrc = img.getAttribute("data-src") || "";
            if (dataSrc && dataSrc.startsWith("http")) {
                imgUrl = dataSrc;
            } else {
                let src = img.getAttribute("src") || "";
                if (src.startsWith("http") && !src.includes("loading.svg")) {
                    imgUrl = src;
                } else {
                    let srcset = img.getAttribute("srcset") || "";
                    if (srcset && !srcset.includes("loading.svg")) {
                        imgUrl = srcset.split(",")[0].trim().split(" ")[0];
                    }
                }
            }
        }

        let link = "";
        let aEl = card.querySelector("a[href]");
        if (aEl) {
            link = aEl.getAttribute("href") || "";
            if (link && !link.startsWith("http")) link = "https://www.bilkatogo.dk" + link;
        }

        let isSale = false;

        // 1. Check for strikethrough price (old price)
        const oldPrice = card.querySelector("s, del, .old-price, [class*='original-price'], [class*='before-price']");
        if (oldPrice && oldPrice.innerText.trim().length > 0) {
            isSale = true;
        }

        // 2. Check stickers for explicit sale keywords
        const stickersList = card.querySelectorAll(".product-stickers, .product-card__offer, .leaflet-sticker, .sticker, [class*='sticker'], [class*='badge'], [class*='campaign']");
        stickersList.forEach(sticker => {
            const text = sticker.innerText.toLowerCase();
            if (/(tilbud|spar|prisfald|avis|vild pris|fødselsdag|multikøb|rabat|gul pris|ugens fund|spot)/i.test(text)) {
                isSale = true;
            }
            if (sticker.className.toLowerCase().includes('offer')) {
                isSale = true;
            }
        });

        // 3. Check for specific text anywhere on the card
        if (/(?:^|\\s)(spar\\s+\\d+|tilbud)(?:\\s|$)/i.test(card.innerText)) {
            isSale = true;
        }

        // 4. Extract multi-promo deal text (e.g. "Mix 2 for 36.-")
        let multiDeal = "";
        let multiDealUnitPrice = 0;
        const multiPromoEl = card.querySelector(".product-stickers__multipromo");
        if (multiPromoEl) {
            const descEl = multiPromoEl.querySelector(".offer-description");
            const priceEl = multiPromoEl.querySelector(".offer-price");
            const descText = descEl ? descEl.textContent.trim().replace(/\\s+/g, " ") : "";
            const priceText = priceEl ? priceEl.textContent.trim().replace(/\\s+/g, " ") : "";
            if (descText || priceText) {
                multiDeal = (descText + " " + priceText).trim();
                isSale = true;
                const qtyMatch = descText.match(/(\\d+)/);
                const totalMatch = priceText.match(/(\\d+[.,]?\\d*)/);
                if (qtyMatch && totalMatch) {
                    const qty = parseInt(qtyMatch[1], 10);
                    const total = parseFloat(totalMatch[1].replace(",", "."));
                    if (qty > 0 && !isNaN(total)) multiDealUnitPrice = total / qty;
                }
            }
        }

        return { name, desc, price, imgUrl, link, isSale, multiDeal, multiDealUnitPrice };
    });
    """

    cards_data = driver.execute_script(js_extract)
    if not cards_data:
        return []

    print(f"    Ekstraherede {len(cards_data)} emner (parallel EAN + hash)...")

    def process_item(item):
        p_type, weight, kg_price = parse_description(item["desc"])
        img_hash = compute_image_hash(item["imgUrl"])
        ean = fetch_ean_selenium(item.get("link", ""))
        return (
            item["name"],
            p_type,
            weight,
            kg_price,
            item["price"],
            ean,
            item["imgUrl"],
            img_hash,
            item["isSale"],
            item.get("multiDeal", ""),
            item.get("multiDealUnitPrice", 0),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=EAN_POOL_SIZE) as executor:
        results = list(executor.map(process_item, cards_data))

    return results


# ── Excel-opsætning ───────────────────────────────────────────────────────────

def setup_worksheet(ws):
    headers = ["Kategori", "Navn", "Type", "Vægt", "Kg-pris", "Pris", "Normalpris", "EAN", "Billede URL", "Billede Hash", "Tilbud", "Multikøb"]
    ws.append(headers)

    for col, _ in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True, name="Arial")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 10
    ws.column_dimensions["F"].width = 10
    ws.column_dimensions["G"].width = 16
    ws.column_dimensions["H"].width = 80
    ws.column_dimensions["I"].width = 20
    ws.column_dimensions["J"].width = 10
    ws.column_dimensions["K"].width = 20


# ── Parallel kategori-behandling ──────────────────────────────────────────────
CATEGORY_POOL_SIZE = 1


def process_single_category(url, i, total_urls):
    kategori = url.split("/kategori/")[1].strip("/")
    print(f"  → [{i}/{total_urls}] Starter kategori: {kategori}")
    
    driver = create_driver()
    all_rows = []
    try:
        driver.get(url)
        time.sleep(1.5)
        handle_cookies(driver)

        load_all_products_on_page(driver)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)

        rows = collect_all_products(driver)
        for name, product_type, weight, kg_price, price, ean, img_url, img_hash, is_sale, multi_deal, multi_unit_price in rows:
            try:
                price_val = float(str(price).replace(',', '.'))
            except ValueError:
                price_val = price

            unique_id = str(ean).strip() if ean else f"{name}_{weight}"
            normal_price = ""

            if multi_unit_price and multi_unit_price > 0:
                # Multipromo: den viste pris på kortet er normalprisen;
                # promoprisen er total/antal (fx 36/2 = 18 kr)
                normal_price = price_val
                price_val = round(multi_unit_price, 2)
            elif not is_sale:
                bilka_normal_prices[unique_id] = price_val
            else:
                normal_price = bilka_normal_prices.get(unique_id, "")

            all_rows.append([kategori, name, product_type, weight, kg_price, price_val, normal_price, ean, img_url, img_hash, "Ja" if is_sale else "Nej", multi_deal])
        
        print(f"  ✓ [{i}/{total_urls}] Færdig med {kategori}: {len(all_rows)} varer")
        return all_rows
    except Exception as e:
        print(f"  ❌ Fejl i kategori {kategori}: {e}")
        return []
    finally:
        driver.quit()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from supabase_utils import save_to_supabase
    load_normal_prices()
    init_ean_pool()

    total_urls = len(URLS)
    all_results = []

    print(f"🚀 Starter parallel scraping af {total_urls} kategorier (Pool size: {CATEGORY_POOL_SIZE})...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=CATEGORY_POOL_SIZE) as executor:
        futures = [executor.submit(process_single_category, url, i, total_urls) for i, url in enumerate(URLS, 1)]
        for future in concurrent.futures.as_completed(futures):
            all_results.extend(future.result())

    quit_ean_pool()
    save_normal_prices()
    save_to_supabase(all_results, "Bilka", row_type="bilka")


if __name__ == "__main__":
    main()
