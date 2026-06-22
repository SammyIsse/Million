import os
import sys
import time
import re
import requests
import concurrent.futures
from io import BytesIO
from queue import Queue
import json

from PIL import Image
import imagehash
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_utils import get_client, fetch_existing_products

# netto.dk kategori-URLs (Salling Group platform, samme struktur som bilkatogo.dk)
CATEGORY_URLS = [
    "https://netto.dk/varer/kolonial/",
    "https://netto.dk/varer/drikkevarer/",
    "https://netto.dk/varer/mejeri-og-koel/",
    "https://netto.dk/varer/slik-og-snacks/",
    "https://netto.dk/varer/broed-og-kager/",
    "https://netto.dk/varer/frugt-og-groent/",
    "https://netto.dk/varer/koed-og-fisk/",
    "https://netto.dk/varer/frost/",
]

SALLING_BASE_URL = "https://api.sallinggroup.com"
CATEGORY_POOL_SIZE = 1
EAN_POOL_SIZE = 2
_EAN_RESTART_AFTER = 80

_product_cache: dict = {}
_CHROMEDRIVER_PATH: str = ""
ean_driver_pool: Queue = Queue()

# ── Normalpris historik ───────────────────────────────────────────────────────
NORMAL_PRICES_FILE = os.path.join(_ROOT_DIR, "data", "netto_normal_prices.json")
netto_normal_prices: dict = {}


def load_normal_prices():
    global netto_normal_prices
    if os.path.exists(NORMAL_PRICES_FILE):
        try:
            with open(NORMAL_PRICES_FILE, "r", encoding="utf-8") as f:
                netto_normal_prices = json.load(f)
            print(f"  ✓ Indlæste {len(netto_normal_prices)} normalpriser fra historik.")
        except Exception as e:
            print(f"  ❌ Fejl ved indlæsning af normalpriser: {e}")
            netto_normal_prices = {}
    else:
        netto_normal_prices = {}


def save_normal_prices():
    try:
        os.makedirs(os.path.dirname(NORMAL_PRICES_FILE), exist_ok=True)
        with open(NORMAL_PRICES_FILE, "w", encoding="utf-8") as f:
            json.dump(netto_normal_prices, f, ensure_ascii=False, indent=2)
        print(f"  ✓ Gemte {len(netto_normal_prices)} normalpriser til historik.")
    except Exception as e:
        print(f"  ❌ Fejl ved gemning af normalpriser: {e}")


# ── ChromeDriver ──────────────────────────────────────────────────────────────

def _ensure_chromedriver():
    global _CHROMEDRIVER_PATH
    if not _CHROMEDRIVER_PATH:
        import glob as _glob
        for stale in _glob.glob(r"C:\Users\Kasp4\.wdm\.wdm-lock-*"):
            try:
                os.remove(stale)
            except OSError:
                pass
        _CHROMEDRIVER_PATH = ChromeDriverManager().install()


def _base_options() -> Options:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return options


def create_driver():
    _ensure_chromedriver()
    options = _base_options()
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-webgl")
    options.add_argument("--renderer-process-limit=2")
    options.add_argument("--js-flags=--max-old-space-size=256")
    return webdriver.Chrome(service=Service(_CHROMEDRIVER_PATH), options=options)


def create_ean_driver():
    _ensure_chromedriver()
    options = _base_options()
    options.page_load_strategy = "eager"
    options.add_argument("--window-size=1280,720")
    options.add_argument("--disable-application-cache")
    options.add_argument("--disk-cache-size=1")
    options.add_argument("--renderer-process-limit=2")
    options.add_argument("--js-flags=--max-old-space-size=128")
    options.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.stylesheet": 2,
    })
    return webdriver.Chrome(service=Service(_CHROMEDRIVER_PATH), options=options)


def init_ean_pool():
    _ensure_chromedriver()
    print(f"  → Starter {EAN_POOL_SIZE} EAN-browsere...")
    for _ in range(EAN_POOL_SIZE):
        ean_driver_pool.put(create_ean_driver())
    print(f"  ✓ EAN-pool klar\n")


def quit_ean_pool():
    while not ean_driver_pool.empty():
        d = ean_driver_pool.get_nowait()
        try:
            d.quit()
        except Exception:
            pass


# ── Cookie-håndtering ─────────────────────────────────────────────────────────

def handle_cookies(driver):
    time.sleep(1.5)
    try:
        result = driver.execute_script("""
            const host = document.querySelector('#usercentrics-root');
            if (host && host.shadowRoot) {
                const btn = host.shadowRoot.querySelector('button[data-action-type="deny"]')
                           || host.shadowRoot.querySelector('#deny')
                           || host.shadowRoot.querySelector('.uc-deny-button');
                if (btn) { btn.click(); return 'shadow'; }
            }
            return null;
        """)
        if result:
            print(f"  ✓ Cookies afvist via Shadow DOM")
            time.sleep(1)
            return
    except Exception:
        pass

    for sel in ["#CybotCookiebotDialogBodyButtonDecline", "button[id*='deny']",
                "button[id*='decline']", ".uc-deny-button"]:
        try:
            driver.find_element(By.CSS_SELECTOR, sel).click()
            print(f"  ✓ Cookies afvist via {sel}")
            time.sleep(1)
            return
        except Exception:
            pass

    # Søg på knaptekst
    try:
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            txt = btn.text.lower()
            if any(w in txt for w in ["afvis", "kun nødvendige", "decline", "deny", "reject"]):
                btn.click()
                print(f"  ✓ Cookies afvist via knaptekst: '{btn.text.strip()}'")
                time.sleep(1)
                return
    except Exception:
        pass

    print("  ⚠ Ingen cookie-banner fundet – fortsætter alligevel")


# ── Scroll og indlæs-knap ─────────────────────────────────────────────────────

def click_load_more(driver) -> bool:
    for xpath in [
        "//button[.//span[normalize-space()='Indlæs flere']]",
        "//button[normalize-space()='Indlæs flere']",
        "//button[contains(@class,'load-more')]",
    ]:
        try:
            btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.3)
            btn.click()
            return True
        except Exception:
            pass
    return False


def load_all_products(driver):
    max_clicks = 100
    for _ in range(max_clicks):
        before = len(driver.find_elements(By.CSS_SELECTOR, "div.product-card-container"))
        if not click_load_more(driver):
            break
        for _ in range(30):
            time.sleep(0.5)
            after = len(driver.find_elements(By.CSS_SELECTOR, "div.product-card-container"))
            if after > before:
                break
        time.sleep(2)
        print(f"    Indlæst: {after} produkter", end="\r")


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_description(description: str):
    product_type = weight = kg_price = ""
    wm = re.search(r"(\d+[.,]?\d*)\s*(kg|g|l|ml|cl|dl|stk)", description, re.IGNORECASE)
    if wm:
        weight = f"{wm.group(1)} {wm.group(2).lower()}"
        product_type = description[:wm.start()].strip().strip(",| -").strip()
    else:
        tm = re.search(r"^[^,]+", description)
        if tm:
            product_type = tm.group(0).strip()
    km = re.search(r"(\d+[.,]?\d*)\s*(?:kr\s*)?/\s*(kg|g|l|ml|cl|dl)", description, re.IGNORECASE)
    if km:
        kg_price = f"{km.group(1)} kr/{km.group(2)}"
    return product_type, weight, kg_price


DEFAULT_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
}


def compute_image_hash(url: str) -> str:
    if not url:
        return ""
    try:
        r = requests.get(url, timeout=6, headers=DEFAULT_HTTP_HEADERS)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        return str(imagehash.phash(img))
    except Exception:
        return ""


# ── EAN via Selenium pool ─────────────────────────────────────────────────────

def fetch_ean_selenium(product_url: str) -> str:
    if not product_url:
        return ""
    driver = ean_driver_pool.get()
    try:
        driver.get(product_url)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "content-product_details"))
            )
        except Exception:
            return ""
        ean = driver.execute_script("""
            const section = document.getElementById('content-product_details');
            if (!section) return '';
            for (const row of section.querySelectorAll('div.row')) {
                const label = row.querySelector('span.col-4');
                const value = row.querySelector('div.col-8');
                if (label && value && label.innerText.trim() === 'EAN') return value.innerText.trim();
            }
            return '';
        """)
        return ean or ""
    except Exception:
        return ""
    finally:
        if not hasattr(driver, "_load_count"):
            driver._load_count = 0
        driver._load_count += 1
        if driver._load_count >= _EAN_RESTART_AFTER:
            try:
                driver.quit()
            except Exception:
                pass
            driver = create_ean_driver()
            print("  ♻ EAN-browser genstartet")
        ean_driver_pool.put(driver)


# ── JS-udtræk af produktkort (samme platform som bilkatogo.dk) ────────────────

_JS_EXTRACT = """
return Array.from(document.querySelectorAll("div.product-card-container")).map(card => {
    let name = "";
    const nameEl = card.querySelector("p.name");
    if (nameEl) {
        name = Array.from(nameEl.childNodes).map(n => n.textContent.trim()).filter(t => t).join(" ");
    }

    let desc = "";
    const descEl = card.querySelector("p.description");
    if (descEl) {
        desc = Array.from(descEl.childNodes).map(n => n.textContent.trim()).filter(t => t).join(" ");
    }

    let price = "0";
    const pc = card.querySelector(".price, [data-testid='product-price'], .current-price, .sales-price")
        || Array.from(card.querySelectorAll("[class*='price']"))
            .find(el => !el.innerText.toLowerCase().includes('kg') && !el.innerText.toLowerCase().includes('pr.'));
    if (pc) {
        const intPart = pc.querySelector("span[class*='int'], span[class*='whole']");
        const decPart = pc.querySelector("span[class*='dec'], sup");
        if (intPart && decPart) {
            price = intPart.innerText.replace(/[^\\d]/g, "") + "." + decPart.innerText.replace(/[^\\d]/g, "");
        } else {
            const raw = pc.innerText;
            if (raw.includes(",")) price = raw.replace(/[^\\d,]/g, "").replace(",", ".");
            else if (raw.includes(".")) price = raw.replace(/[^\\d.]/g, "");
            else price = raw.replace(/[^\\d]/g, "");
        }
    } else {
        const m = card.innerText.match(/(?:\\b|^)(\\d+)[,.](\\d{2})(?:\\s*kr\\.?)/i);
        if (m) price = m[1] + "." + m[2];
    }

    let imgUrl = "";
    const img = card.querySelector("img.product-image");
    if (img) {
        const dataSrc = img.getAttribute("data-src") || "";
        if (dataSrc && dataSrc.startsWith("http")) imgUrl = dataSrc;
        else {
            const src = img.getAttribute("src") || "";
            if (src.startsWith("http") && !src.includes("loading.svg")) imgUrl = src;
            else {
                const srcset = img.getAttribute("srcset") || "";
                if (srcset && !srcset.includes("loading.svg")) imgUrl = srcset.split(",")[0].trim().split(" ")[0];
            }
        }
    }

    let link = "";
    const aEl = card.querySelector("a[href]");
    if (aEl) {
        link = aEl.getAttribute("href") || "";
        if (link && !link.startsWith("http")) link = "https://netto.dk" + link;
    }

    let isSale = false;
    const oldPrice = card.querySelector("s, del, .old-price, [class*='original-price'], [class*='before-price']");
    if (oldPrice && oldPrice.innerText.trim()) isSale = true;

    card.querySelectorAll(".product-stickers, [class*='sticker'], [class*='badge'], [class*='campaign']")
        .forEach(s => {
            if (/(tilbud|spar|prisfald|avis|vild pris|rabat|gul pris)/i.test(s.innerText)) isSale = true;
            if (s.className.toLowerCase().includes('offer')) isSale = true;
        });

    return { name, desc, price, imgUrl, link, isSale };
});
"""


def collect_all_products(driver) -> list:
    try:
        h = driver.execute_script("return document.body.scrollHeight")
        for i in range(0, h, 800):
            driver.execute_script(f"window.scrollTo(0, {i});")
            time.sleep(0.05)
        time.sleep(0.5)
    except Exception:
        pass

    cards_data = driver.execute_script(_JS_EXTRACT)
    if not cards_data:
        return []

    print(f"    Ekstraherede {len(cards_data)} emner...")

    def process_item(item):
        p_type, weight, kg_price = parse_description(item.get("desc", ""))
        name_lower = item.get("name", "").lower()
        cached = _product_cache.get(name_lower)
        if cached and cached.get("varenummer"):
            ean = cached["varenummer"]
            img_hash = cached["billede_hash"] if cached["billede_url"] == item.get("imgUrl") else compute_image_hash(item.get("imgUrl", ""))
        else:
            img_hash = compute_image_hash(item.get("imgUrl", ""))
            ean = fetch_ean_selenium(item.get("link", ""))
        return (
            item.get("name", ""),
            p_type, weight, kg_price,
            item.get("price", "0"),
            ean,
            item.get("imgUrl", ""),
            img_hash,
            item.get("isSale", False),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=EAN_POOL_SIZE) as ex:
        return list(ex.map(process_item, cards_data))


# ── Scrape én kategori-URL ────────────────────────────────────────────────────

def process_single_category(url: str, i: int, total: int) -> list[list]:
    kategori = url.rstrip("/").split("/")[-1]
    print(f"  → [{i}/{total}] Starter kategori: {kategori}")
    driver = create_driver()
    all_rows = []
    try:
        driver.get(url)
        time.sleep(1.5)
        handle_cookies(driver)
        load_all_products(driver)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)

        rows = collect_all_products(driver)
        for name, p_type, weight, kg_price, price, ean, img_url, img_hash, is_sale in rows:
            try:
                price_val = float(str(price).replace(",", "."))
            except ValueError:
                price_val = price

            unique_id = str(ean).strip() if ean else f"{name}_{weight}"
            normal_price = ""
            if not is_sale:
                netto_normal_prices[unique_id] = price_val
            else:
                normal_price = netto_normal_prices.get(unique_id, "")

            all_rows.append([
                kategori, name, p_type, weight, kg_price,
                price_val, normal_price, ean,
                img_url, img_hash,
                "Ja" if is_sale else "Nej",
                None,
            ])

        print(f"  ✓ [{i}/{total}] Færdig med {kategori}: {len(all_rows)} varer")
        return all_rows
    except Exception as e:
        print(f"  ❌ Fejl i kategori {kategori}: {e}")
        return []
    finally:
        driver.quit()


# ── Salling API: Netto madspild ───────────────────────────────────────────────

def _salling_headers() -> dict:
    api_key = os.environ.get("SALLING_API_KEY")
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def fetch_netto_food_waste() -> list[dict]:
    """Henter madspild fra alle Netto-butikker via Salling API."""
    headers = _salling_headers()
    if not headers:
        print("  ⚠ SALLING_API_KEY ikke sat – springer Netto madspild over")
        return []

    resp = requests.get(
        f"{SALLING_BASE_URL}/v2/stores",
        headers=headers, params={"brand": "netto", "country": "dk"}, timeout=30
    )
    if resp.status_code != 200:
        print(f"  ⚠ Kunne ikke hente Netto butikker: {resp.status_code}")
        return []

    stores = resp.json()
    if not isinstance(stores, list):
        stores = stores.get("items", stores.get("stores", []))

    print(f"  Fandt {len(stores)} Netto butikker (madspild)")
    rows: list[dict] = []
    seen: set[str] = set()

    for store in stores:
        store_id = store.get("id", "")
        if not store_id:
            continue
        try:
            fw_resp = requests.get(
                f"{SALLING_BASE_URL}/v1/food-waste/{store_id}",
                headers=headers, timeout=30
            )
            if fw_resp.status_code != 200:
                continue
            data = fw_resp.json()
        except Exception as e:
            print(f"    ⚠ Fejl ved butik {store_id}: {e}")
            continue

        store_name = store.get("name", "")
        city = store.get("city", "")
        for item in data.get("clearance", []):
            offer = item.get("offer", {})
            navn = offer.get("description", "")
            pris = offer.get("newPrice")
            normalpris = offer.get("originalPrice")
            discount_pct = offer.get("percentDiscount")
            ean = offer.get("ean") or offer.get("id") or ""
            billede_url = offer.get("image", "")
            stock = item.get("stock", {})
            antal = stock.get("quantity") if isinstance(stock, dict) else None
            end_time = offer.get("endTime", "")

            key = f"madspild|{store_name}|{navn}"
            if key in seen:
                continue
            seen.add(key)

            rows.append({
                "butik":        "Netto",
                "kategori":     f"Madspild – {store_name}, {city}".strip("– ,"),
                "navn":         navn,
                "producent":    None,
                "netto_vaegt":  None,
                "kg_price":     None,
                "pris":         float(pris) if pris is not None else None,
                "normalpris":   str(normalpris) if normalpris is not None else None,
                "varenummer":   str(ean) if ean else None,
                "billede_url":  billede_url,
                "billede_hash": None,
                "tilbud":       f"{discount_pct}% rabat" if discount_pct else "",
                "enhed":        f"Antal: {antal}" if antal is not None else (end_time[:10] if end_time else ""),
                "multikob":     None,
            })

    print(f"  ✅ {len(rows)} Netto madspild-varer hentet")
    return rows


# ── Gem til Supabase ──────────────────────────────────────────────────────────

def save_to_supabase(web_rows: list[list], food_waste_rows: list[dict]):
    client = get_client()

    records: list[dict] = []

    for row in web_rows:
        img_url = str(row[8] or "").replace(",e_grayscale", "")
        records.append({
            "butik":        "Netto",
            "kategori":     row[0],
            "navn":         row[1],
            "producent":    row[2],
            "netto_vaegt":  row[3],
            "kg_price":     row[4],
            "pris":         float(row[5]) if row[5] else None,
            "normalpris":   str(row[6]) if row[6] != "" else None,
            "varenummer":   str(row[7]) if row[7] else None,
            "billede_url":  img_url,
            "billede_hash": row[9],
            "tilbud":       str(row[10]),
            "multikob":     row[11],
        })

    records.extend(food_waste_rows)

    client.table("produkter").delete().eq("butik", "Netto").execute()
    for i in range(0, len(records), 500):
        client.table("produkter").insert(records[i:i+500]).execute()

    print(f"✅ {len(records)} rækker gemt i Supabase for Netto "
          f"({len(web_rows)} sortiment + {len(food_waste_rows)} madspild)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _product_cache
    _product_cache = fetch_existing_products("Netto")
    load_normal_prices()
    init_ean_pool()

    total = len(CATEGORY_URLS)
    all_web_rows: list[list] = []

    print(f"🚀 Starter Netto sortiment-scraper ({total} kategorier)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=CATEGORY_POOL_SIZE) as ex:
        futures = [ex.submit(process_single_category, url, i, total)
                   for i, url in enumerate(CATEGORY_URLS, 1)]
        for fut in concurrent.futures.as_completed(futures):
            all_web_rows.extend(fut.result())

    quit_ean_pool()
    save_normal_prices()

    print("\nHenter Netto madspild fra Salling API...")
    food_waste_rows = fetch_netto_food_waste()

    save_to_supabase(all_web_rows, food_waste_rows)
    print("\nFærdig!")


if __name__ == "__main__":
    main()
