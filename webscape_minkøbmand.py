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

BASE_URL = "https://hollufpile.minkobmand.dk/produkter"

# Ingen EAN-pool længere — varenummer udtrækkes direkte fra URL
EAN_POOL_SIZE = 1  # Beholdes kun for at undgå at ændre hele strukturen

def init_ean_pool():
    pass  # Ikke nødvendigt længere

def quit_ean_pool():
    pass  # Ikke nødvendigt længere


def create_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(service=Service(), options=options)


# ---------------------------------------------------------------------------
# Cookie-håndtering
# ---------------------------------------------------------------------------

def handle_cookies(driver):
    print("  → Venter på cookie-banner...")
    time.sleep(3)

    try:
        buttons = driver.find_elements(By.TAG_NAME, "button")
        for btn in buttons:
            txt = btn.text.lower()
            if any(w in txt for w in ["afvis", "nej", "kun nødvendige", "accepter kun", "decline", "reject", "deny"]):
                btn.click()
                print(f"  ✓ Cookies afvist: '{btn.text.strip()}'")
                time.sleep(1)
                return
    except Exception:
        pass

    for selector in [
        "#declineButton", "button.cookie-decline",
        "button[id*='decline']", "button[id*='deny']",
        "#CybotCookiebotDialogBodyButtonDecline",
    ]:
        try:
            driver.find_element(By.CSS_SELECTOR, selector).click()
            print(f"  ✓ Cookies afvist via '{selector}'")
            time.sleep(1)
            return
        except Exception:
            pass

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
    except Exception:
        pass

    print("  ⚠ Ingen cookie-banner fundet – fortsætter alligevel")


# ---------------------------------------------------------------------------
# Kategori-navigation
# ---------------------------------------------------------------------------

CATEGORIES_TO_SCRAPE = {
    "kolonial": None,
    "mejeri": None,
    "pålæg og kølede middagsretter": None,
    "frost": None,
    "kød": None,
    "fisk og skaldyr": None,
    "frugt og grønt": None,
    "brød og kager": None,
    "drikkevarer": None,
    "vin og spiritus": None,
    "kiosk - slik og snack": ["chips og snacks", "chokolade", "slik"]
}

def get_category_elements(driver, allowed_labels):
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div[tabindex='0'] label"))
        )
    except Exception:
        return {}

    divs = driver.find_elements(By.CSS_SELECTOR, "div[tabindex='0']")
    elements = {}
    for div in divs:
        try:
            label = div.find_element(By.TAG_NAME, "label").text.strip().lower()
            if label in allowed_labels:
                elements[label] = div
        except Exception:
            pass
    return elements


# ---------------------------------------------------------------------------
# Indlæs alle produkter i aktuel kategori
# ---------------------------------------------------------------------------

def click_load_more(driver):
    try:
        btn = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//button[@type='button' and contains(., 'VIS NÆSTE')]"
            ))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
        time.sleep(0.4)
        btn.click()
        return True
    except Exception:
        pass

    try:
        btn = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.text-button.primary"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
        time.sleep(0.4)
        btn.click()
        return True
    except Exception:
        pass

    return False


def load_all_products_in_category(driver):
    max_clicks = 100
    clicks = 0

    while clicks < max_clicks:
        before = len(driver.find_elements(By.CSS_SELECTOR, "a[href*='/produkter/']"))
        if not click_load_more(driver):
            break
        clicks += 1
        for _ in range(20):
            time.sleep(0.5)
            after = len(driver.find_elements(By.CSS_SELECTOR, "a[href*='/produkter/']"))
            if after > before:
                break
        print(f"    Indlæst: {after} produkter", end="\r")

    total = len(driver.find_elements(By.CSS_SELECTOR, "a[href*='/produkter/']"))
    return total


# ---------------------------------------------------------------------------
# Varenummer via Selenium pool
# ---------------------------------------------------------------------------

def fetch_varenummer_selenium(product_url):
    """
    Åbner produktsiden, finder 'Produktdetaljer'-panelet, klikker det
    åbent og udtrækker varenummeret fra <h2>Varenummer</h2> + <p>.
    """
    if not product_url:
        return ""

    driver = ean_driver_pool.get()
    try:
        driver.get(product_url)

        # Vent på at Angular har renderet mindst ét panel-header
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "mat-expansion-panel-header"))
            )
        except Exception:
            return ""

        # Giv Angular lidt ekstra tid til at færdiggøre rendering
        time.sleep(1.5)

        # Find 'Produktdetaljer'-headeren og klik hvis den er lukket
        try:
            panel_header = driver.execute_script("""
                const headers = document.querySelectorAll('mat-expansion-panel-header');
                for (const h of headers) {
                    const title = h.querySelector('mat-panel-title');
                    if (title && title.innerText.trim() === 'Produktdetaljer') {
                        return h;
                    }
                }
                return null;
            """)

            if panel_header:
                is_expanded = panel_header.get_attribute("aria-expanded")
                if is_expanded != "true":
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", panel_header)
                    time.sleep(0.3)
                    panel_header.click()
                    # Vent på indhold — contains(@class) matcher "wrapper ng-tns-..." korrekt
                    # (original brugte @class='wrapper' som er exact match og aldrig matchede)
                    try:
                        WebDriverWait(driver, 8).until(
                            EC.presence_of_element_located((
                                By.XPATH,
                                "//mat-expansion-panel[.//mat-panel-title[normalize-space()='Produktdetaljer']]"
                                "//div[contains(@class,'wrapper')]"
                                "//h2[normalize-space()='Varenummer']"
                                "/following-sibling::p"
                            ))
                        )
                    except Exception:
                        time.sleep(1.5)
        except Exception:
            pass

        # Udtræk varenummeret — prøv op til 3 gange hvis Angular stadig renderer
        for _ in range(3):
            varenummer = driver.execute_script("""
                const panels = document.querySelectorAll('mat-expansion-panel');
                for (const panel of panels) {
                    const title = panel.querySelector('mat-panel-title');
                    if (!title || title.innerText.trim() !== 'Produktdetaljer') continue;
                    const wrappers = panel.querySelectorAll('div.wrapper');
                    for (const w of wrappers) {
                        const h2 = w.querySelector('h2');
                        const p  = w.querySelector('p');
                        if (h2 && p && h2.innerText.trim() === 'Varenummer') {
                            return p.innerText.trim();
                        }
                    }
                }
                return '';
            """)
            if varenummer:
                return varenummer
            time.sleep(1)

        return ""

    except Exception:
        return ""
    finally:
        ean_driver_pool.put(driver)


# ---------------------------------------------------------------------------
# Data-udtræk
# ---------------------------------------------------------------------------

def parse_netto_vaegt(summary_text):
    """Udtrækker netto vægt og stopper før eventuel ekstra beskrivelse."""
    # Matcher "Netto vægt: " efterfulgt af tal og enhed (stopper efter enheden)
    pattern = r"netto\s+v[æa]gt\s*:\s*([\d.,]+\s*(?:kg|g|l|dl|cl|ml|stk|gram|liter|bdt|pk|ds|pk\.|ps|glas))"
    match = re.search(pattern, summary_text, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # Fald tilbage til den gamle metode, men stop ved punktum eller linjeskift
    match = re.search(r"netto\s+v[æa]gt\s*:\s*([^\n.]+)", summary_text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def parse_kg_price(summary_text):
    match = re.search(
        r"(\d+[.,]?\d*)\s*(?:kr\s*)?/\s*(kg|g|l|cl|ml)", summary_text, re.IGNORECASE
    )
    if match:
        return f"{match.group(1)} kr/{match.group(2)}"
    return ""


def calculate_kg_price(price_str, netto_vaegt_str):
    """Beregner kr/kg eller kr/l ud fra pris og netto vægt."""
    try:
        price = float(str(price_str).replace(",", "."))
    except (ValueError, AttributeError):
        return ""

    if not netto_vaegt_str:
        return ""

    # Support 'gram' and 'liter' in regex
    match = re.search(r'([\d.,]+)\s*(kg|g|l|dl|cl|ml|gram|liter)', netto_vaegt_str, re.IGNORECASE)
    if not match:
        return ""

    try:
        amount = float(match.group(1).replace(",", "."))
    except ValueError:
        return ""

    unit = match.group(2).lower()
    if unit == "gram": unit = "g"
    if unit == "liter": unit = "l"

    conversions = {
        "g":  1 / 1000,
        "kg": 1,
        "ml": 1 / 1000,
        "cl": 1 / 100,
        "dl": 1 / 10,
        "l":  1,
    }
    factor = conversions.get(unit)
    if not factor or amount == 0:
        return ""

    base_unit = "kg" if unit in ("g", "kg") else "l"
    kg_price = price / (amount * factor)
    return f"{kg_price:.2f} kr/{base_unit}"


def extract_producer(name):
    parts = name.strip().split()
    return parts[0] if parts else ""


def extract_varenummer(link, img_url=""):
    """
    Varenummeret (EAN) sidder allerede i produkt-URL'en og billede-URL'en —
    ingen ekstra browser-kald nødvendigt.
    Eksempel: /produkter/fiskefileter-ovn-panering-5700001868771 → 5700001868771
    """
    for url in [link, img_url]:
        if not url:
            continue
        # Find den længste sammenhængende ciffer-streng (mindst 8 cifre = EAN/varenummer)
        matches = re.findall(r'\d{8,}', url)
        if matches:
            # Tag det længste match (EAN er typisk 13 cifre)
            return max(matches, key=len)
    return ""



def compute_image_hash(url):
    if not url:
        return ""
    try:
        response = requests.get(url, timeout=3)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content))
        return str(imagehash.phash(img))
    except Exception:
        return ""


def collect_products_in_category(driver, kategori_navn):
    """Indsamler alle produktkort hurtigt via JavaScript og trådet hashing + varenummer."""

    # Scroll langsomt ned og vent på lazy-load af produktkort
    try:
        prev_count = 0
        for _ in range(30):  # maks 30 scroll-steps
            driver.execute_script("window.scrollBy(0, 600);")
            time.sleep(0.6)
            cur_count = len(driver.find_elements(By.CSS_SELECTOR, "a[href*='/produkter/']"))
            if cur_count == prev_count:
                # Ingen nye elementer — rul lidt mere og vent
                time.sleep(1.0)
                cur_count = len(driver.find_elements(By.CSS_SELECTOR, "a[href*='/produkter/']"))
                if cur_count == prev_count:
                    break  # Færdig
            prev_count = cur_count
        # Rul tilbage til toppen så billeder inde i viewport loader
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)
    except Exception:
        pass

    js_script = """
    // Produkt-URLs: /produkter/produkt-navn-MED-EAN-tal  (2 segmenter, ender på cifre)
    // Kategori-URLs: /produkter/fisk-og-skaldyr           (2 segmenter, ingen cifre til sidst)
    // Underkategori-URLs: /produkter/fisk/fileter-og-fars (3 segmenter)
    const allLinks = Array.from(document.querySelectorAll("a[href*='/produkter/']")).filter(el => {
        const href = el.getAttribute('href') || '';
        const segments = href.split('/').filter(Boolean);
        const lastSeg = segments[segments.length - 1] || '';
        // Produkt: præcis 2 segmenter OG sidst segment indeholder cifre (EAN-del)
        return segments.length === 2 && /\\d/.test(lastSeg);
    });

    // Deduplicer på href
    const seen = new Set();
    const containers = [];
    for (const a of allLinks) {
        const href = a.getAttribute('href');
        if (seen.has(href)) continue;
        seen.add(href);
        const container = a.closest('app-product-card, mat-card, li, article, [class*="product-card"], [class*="product-item"]') || a.parentElement || a;
        containers.push({ a, container, href });
    }

    return containers.map(({ a, container, href }) => {
        // ── NAVN ──────────────────────────────────────────────────────────────
        let name = "";
        for (const sel of ["b.product-card-name","[class*='product-card-name']","[class*='product-name']","[class*='item-name']","strong","b","h2","h3","h4"]) {
            const el = container.querySelector(sel);
            if (el && el.innerText.trim().length > 1) { name = el.innerText.trim(); break; }
        }

        // ── SUMMARY ───────────────────────────────────────────────────────────
        let summary = "";
        for (const sel of ["span.product-card-summary","[class*='summary']","[class*='description']","[class*='subtitle']","p"]) {
            const el = container.querySelector(sel);
            if (el && el.innerText.trim().length > 3) { summary = el.innerText.trim(); break; }
        }

        // ── PRIS ──────────────────────────────────────────────────────────────
        let priceStr = "0";
        for (const sel of ["app-price","[class*='price']","[class*='amount']","[class*='cost']"]) {
            const el = container.querySelector(sel);
            if (!el) continue;
            const sup = el.querySelector("sup");
            const decimal = sup ? sup.innerText.replace(/\\D/g, "") : "";
            const clone = el.cloneNode(true);
            const s = clone.querySelector("sup");
            if (s) s.remove();
            const integer = clone.innerText.replace(/[^\\d]/g, "");
            if (integer) { priceStr = decimal ? integer + "." + decimal : integer; break; }
        }

        // ── BILLEDE ───────────────────────────────────────────────────────────
        let imgUrl = "";
        for (const imgSel of ["div.product-card-image-container img","[class*='image-container'] img","[class*='product-image'] img","figure img","img"]) {
            const img = container.querySelector(imgSel);
            if (!img) continue;
            const src = img.getAttribute("src") || "";
            if (src.startsWith("http") && !src.toLowerCase().includes("loading")) { imgUrl = src; break; }
            const srcset = img.getAttribute("srcset") || "";
            if (srcset && !srcset.toLowerCase().includes("loading")) { imgUrl = srcset.split(",")[0].trim().split(" ")[0]; break; }
            const dSrc = img.getAttribute("data-src") || "";
            if (dSrc.startsWith("http")) { imgUrl = dSrc; break; }
        }

        // ── LINK ──────────────────────────────────────────────────────────────
        let link = href || "";
        if (link && !link.startsWith("http")) link = "https://hollufpile.minkobmand.dk" + link;

        let isSale = false;
        const saleBadge = container.querySelector(".product-card-offer, .badge-offer, .price-sale, [class*='offer'], [class*='sale-tag'], .sticker");
        if (saleBadge) isSale = true;

        return { name, summary, price: priceStr, imgUrl, link, isSale };
    });
    """

    cards_data = driver.execute_script(js_script)
    if not cards_data:
        print("  ⚠ Ingen produktkort fundet — tjek om siden er korrekt indlæst")
        return []

    parsed_items = []
    for item in cards_data:
        name = item.get("name", "")
        producer = extract_producer(name)
        summary = item.get("summary", "")
        netto_vaegt = parse_netto_vaegt(summary)
        price = item.get("price", "0")
        img_url = item.get("imgUrl", "")
        link = item.get("link", "")

        # Beregn kg-pris — prøv beregning først, fald tilbage på summary-parsing
        kg_price = calculate_kg_price(price, netto_vaegt) or parse_kg_price(summary)

        parsed_items.append({
            "name": name,
            "producer": producer,
            "netto_vaegt": netto_vaegt,
            "kg_price": kg_price,
            "price": price,
            "img_url": img_url,
            "link": link,
            "is_sale": item.get("isSale", False)
        })

    print(f"    Ekstraherede {len(parsed_items)} emner (parallel varenummer + billed-hash)...")

    def process_item(item):
        img_hash = compute_image_hash(item["img_url"])
        varenummer = extract_varenummer(item["link"], item["img_url"])
        return img_hash, varenummer, item["is_sale"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=EAN_POOL_SIZE) as executor:
        results = list(executor.map(process_item, parsed_items))

    rows = []
    for item, (img_hash, varenummer, is_sale) in zip(parsed_items, results):
        rows.append((
            kategori_navn,
            item["name"],
            item["producer"],
            item["netto_vaegt"],
            item["kg_price"],
            item["price"],
            varenummer,
            item["img_url"],
            img_hash,
            is_sale
        ))

    return rows


# ---------------------------------------------------------------------------
# Excel-opsætning
# ---------------------------------------------------------------------------

def setup_worksheet(ws):
    headers = [
        "Kategori", "Navn", "Producent", "Netto Vægt",
        "Kg-pris", "Pris", "Varenummer", "Billede URL", "Billede Hash", "Tilbud"
    ]
    ws.append(headers)

    for col, _ in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True, name="Arial")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    ws.column_dimensions["A"].width = 25   # Kategori
    ws.column_dimensions["B"].width = 35   # Navn
    ws.column_dimensions["C"].width = 20   # Producent
    ws.column_dimensions["D"].width = 15   # Netto Vægt
    ws.column_dimensions["E"].width = 12   # Kg-pris
    ws.column_dimensions["F"].width = 10   # Pris
    ws.column_dimensions["G"].width = 16   # Varenummer
    ws.column_dimensions["H"].width = 80   # Billede URL
    ws.column_dimensions["I"].width = 20   # Billede Hash
    ws.column_dimensions["J"].width = 10   # Tilbud


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    init_ean_pool()

    driver = create_driver()

    wb = Workbook()
    ws = wb.active
    ws.title = "Produkter"
    setup_worksheet(ws)

    total_saved = 0

    try:
        print(f"\nÅbner: {BASE_URL}")
        driver.get(BASE_URL)
        time.sleep(2)
        handle_cookies(driver)
        time.sleep(2)

        main_labels = list(CATEGORIES_TO_SCRAPE.keys())

        for idx, main_label in enumerate(main_labels, 1):
            print(f"\n[{idx}/{len(main_labels)}] Kategori: {main_label.title()}")

            driver.get(BASE_URL)
            time.sleep(3)

            main_elements = get_category_elements(driver, [main_label])
            if main_label not in main_elements:
                print(f"  ⚠ Fandt ikke hovedkategori: {main_label}")
                continue

            subcategories = CATEGORIES_TO_SCRAPE[main_label]

            if subcategories is None:
                cat_div = main_elements[main_label]
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", cat_div)
                time.sleep(1)
                cat_div.click()
                time.sleep(3)

                label_display = main_label.title()
                load_all_products_in_category(driver)
                print()

                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(0.5)

                rows = collect_products_in_category(driver, label_display)
                print()

                for row in rows:
                    row_list = list(row)
                    row_list[-1] = "Ja" if row_list[-1] else "Nej"
                    ws.append(row_list)
                    total_saved += 1
                print(f"  ✓ {len(rows)} varer gemt fra '{label_display}'")

            else:
                for sub_label in subcategories:
                    print(f"\n  → Går til underkategori: {sub_label.title()}")
                    driver.get(BASE_URL)
                    time.sleep(3)

                    main_els_again = get_category_elements(driver, [main_label])
                    if main_label in main_els_again:
                        main_els_again[main_label].click()
                        time.sleep(3)

                    sub_els = get_category_elements(driver, [sub_label])
                    if sub_label not in sub_els:
                        print(f"  ⚠ Fandt ikke underkategori: {sub_label}")
                        continue

                    sub_div = sub_els[sub_label]
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", sub_div)
                    time.sleep(1)
                    sub_div.click()
                    time.sleep(3)

                    label_display = f"{main_label.title()} - {sub_label.title()}"
                    print(f"  → Indlæser varer for: {label_display}")
                    load_all_products_in_category(driver)
                    print()

                    driver.execute_script("window.scrollTo(0, 0);")
                    time.sleep(0.5)

                    rows = collect_products_in_category(driver, label_display)
                    print()

                    for row in rows:
                        row_list = list(row)
                        row_list[-1] = "Ja" if row_list[-1] else "Nej"
                        ws.append(row_list)
                        total_saved += 1
                    print(f"  ✓ {len(rows)} varer gemt fra '{label_display}'")

    finally:
        driver.quit()
        quit_ean_pool()

    filename = "minkobmand_produkter.xlsx"
    wb.save(filename)
    print(f"\n✅ {total_saved} varer i alt gemt i: {filename}")


if __name__ == "__main__":
    main()