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

# ── Antal parallelle Selenium-instanser til EAN-hentning ──────────────────────
EAN_POOL_SIZE = 12
ean_driver_pool = Queue()


def create_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(service=Service(), options=options)

def create_ean_driver():
    """Optimeret driver specifikt til hurtig EAN-hentning (deaktiverer billeder og CSS)"""
    options = Options()
    options.page_load_strategy = "eager"  # Venter kun på DOMContentLoaded
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    # Deaktiver billeder og stylesheets for lynhurtig load
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.stylesheet": 2
    }
    options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(service=Service(), options=options)


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
    max_clicks = 50
    clicks = 0
    while clicks < max_clicks:
        before = len(driver.find_elements(By.CSS_SELECTOR, "div.product-card-container"))
        if not click_load_more(driver):
            break
        clicks += 1
        for _ in range(20):
            time.sleep(0.5)
            after = len(driver.find_elements(By.CSS_SELECTOR, "div.product-card-container"))
            if after > before:
                break
        print(f"    Indlæst: {after} produkter", end="\r")


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_description(description):
    product_type = ""
    weight = ""
    kg_price = ""

    type_match = re.search(r"^[A-Za-zÆØÅæøå\s]+", description)
    if type_match:
        product_type = type_match.group(0).strip()

    weight_match = re.search(r"(\d+[.,]?\d*)\s*(kg|g|l)", description, re.IGNORECASE)
    if weight_match:
        weight = f"{weight_match.group(1)} {weight_match.group(2)}"

    kg_price_match = re.search(
        r"(\d+[.,]?\d*)\s*(?:kr\s*)?/\s*(kg|g|l)", description, re.IGNORECASE
    )
    if kg_price_match:
        kg_price = f"{kg_price_match.group(1)} kr/{kg_price_match.group(2)}"

    return product_type, weight, kg_price


# ── EAN via Selenium pool ─────────────────────────────────────────────────────

def fetch_ean_selenium(product_url):
    """
    Åbner produktsiden i en Selenium-instans fra poolen og venter på at
    Vue-komponenten '#content-product_details' er renderet, før EAN udtrækkes.
    """
    if not product_url:
        return ""

    driver = ean_driver_pool.get()
    try:
        driver.get(product_url)

        # Vent på at produktdetalje-sektionen er synlig i DOM
        try:
            WebDriverWait(driver, 5).until(
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
    # Scroll for at trigge lazy-load af billeder
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
        if (nameEl) name = nameEl.innerText.trim();

        let desc = "";
        let descEl = card.querySelector("p.description");
        if (descEl) desc = descEl.innerText.trim();

        let price = "0";
        // Find pris-containeren
        const priceContainer = card.querySelector("div.product-price") || card.querySelector("[class*='price']") || card;
        
        if (priceContainer) {
            // Hvis teksten har komma, er det nemt
            let rawText = priceContainer.innerText;
            if (rawText.includes(",")) {
                price = rawText.replace(/[^\d,]/g, "").replace(",", ".");
            } else if (rawText.includes(".")) {
                price = rawText.replace(/[^\d.]/g, "");
            } else {
                // Ingen komma eller punktum fundet i den samlede tekst.
                // Bilka har muligvis lagt kroner og øre i hver sin HTML-node (f.eks. to span-tags) uden separator.
                // Vi finder alle tekst-noder og samler tallene.
                let nums = [];
                let walker = document.createTreeWalker(priceContainer, NodeFilter.SHOW_TEXT, null, false);
                let node;
                while (node = walker.nextNode()) {
                    let t = node.nodeValue.replace(/[^\d]/g, "");
                    if (t.length > 0) nums.push(t);
                }
                
                if (nums.length >= 2) {
                    price = nums[0] + "." + nums[1];
                } else if (nums.length === 1) {
                    // Kun et tal. For at være sikker på vi ikke returnerer 1050 for 10,50
                    // Hvis der kun er et tal, og teksten oprindeligt var f.eks. "1050" uden komma,
                    // antager vi at de sidste to cifre er ører, DA danske priser altid vises med ører,
                    // HVIS original string faktisk indeholdt over 2 cifre og vi VED at der mangler decimal.
                    // Men normalt vil TreeWalker finde to noder, fordi de er i hvert sit HTML-tag.
                    let v = nums[0];
                    if (v.length >= 3) {
                        price = v.substring(0, v.length - 2) + "." + v.substring(v.length - 2);
                    } else {
                        price = v;
                    }
                }
            }
        }

        let imgUrl = "";
        let img = card.querySelector("img.product-image");
        if (img) {
            let src = img.getAttribute("src") || "";
            if (src.startsWith("http") && !src.includes("loading.svg")) {
                imgUrl = src;
            } else {
                let srcset = img.getAttribute("srcset") || "";
                if (srcset) {
                    imgUrl = srcset.split(",")[0].trim().split(" ")[0];
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
        const stickers = card.querySelector(".product-stickers, .product-card__offer, .leaflet-sticker, .sticker");
        if (stickers) isSale = true;

        return { name, desc, price, imgUrl, link, isSale };
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
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=EAN_POOL_SIZE) as executor:
        results = list(executor.map(process_item, cards_data))

    return results


# ── Excel-opsætning ───────────────────────────────────────────────────────────

def setup_worksheet(ws):
    headers = ["Navn", "Type", "Vægt", "Kg-pris", "Pris", "EAN", "Billede URL", "Billede Hash", "Tilbud"]
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
    ws.column_dimensions["F"].width = 16   # EAN
    ws.column_dimensions["G"].width = 80   # Billede URL
    ws.column_dimensions["H"].width = 20   # Hash
    ws.column_dimensions["I"].width = 10   # Tilbud


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    init_ean_pool()

    main_driver = create_driver()

    wb = Workbook()
    ws = wb.active
    ws.title = "Produkter"
    setup_worksheet(ws)

    total_saved = 0

    try:
        for i, url in enumerate(URLS, 1):
            kategori = url.split("/kategori/")[1].strip("/")
            print(f"\n[{i}/{len(URLS)}] Henter: {kategori}")
            main_driver.get(url)
            time.sleep(1.5)
            handle_cookies(main_driver)

            load_all_products_on_page(main_driver)
            print()

            main_driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

            rows = collect_all_products(main_driver)
            for name, product_type, weight, kg_price, price, ean, img_url, img_hash, is_sale in rows:
                try:
                    price_val = float(str(price).replace(',', '.'))
                except ValueError:
                    price_val = price
                ws.append([name, product_type, weight, kg_price, price_val, ean, img_url, img_hash, "Ja" if is_sale else "Nej"])
                total_saved += 1

            print(f"  ✓ {len(rows)} varer gemt fra denne kategori")

    finally:
        main_driver.quit()
        quit_ean_pool()

    filename = "Bilka_produkter.xlsx"
    wb.save(filename)
    print(f"\n✅ {total_saved} varer er gemt i: {filename}")


if __name__ == "__main__":
    main()