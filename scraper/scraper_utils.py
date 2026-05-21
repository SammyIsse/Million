from webdriver_manager.chrome import ChromeDriverManager
import os
import re
import time
import requests
from io import BytesIO
from PIL import Image
import imagehash
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment

_WEIGHT_RE = re.compile(r'([\d.,\-\s]+(?:x\s*[\d.,\-\s]+)?(?:g|kg|l|liter|ml|cl|stk))', re.IGNORECASE)
_KG_PRICE_RE = re.compile(r'(?P<type>kg-pris|literpris|stk-pris)[^\d]*([\d.,]+)', re.IGNORECASE)
_NORMAL_PRICE_RE = re.compile(r'før-pris\s*([\d.,]+)', re.IGNORECASE)

_HEADERS = [
    "Kategori", "Navn", "Producent", "Netto Vægt",
    "Kg-pris", "Pris", "Normalpris", "Varenummer", "Billede URL", "Billede Hash", "Tilbud", "Enhed",
]

_COLUMN_WIDTHS = {
    "A": 15, "B": 35, "C": 20, "D": 15, "E": 12,
    "F": 10, "G": 12, "H": 16, "I": 80, "J": 20, "K": 10, "L": 12,
}

JS_EXTRACT = """
return Array.from(document.querySelectorAll("div[data-role='offer']")).map(offer => {
    let id = offer.getAttribute("data-id") || "";
    let name = "", desc = "", unit = "", price = "";

    let infoDiv = offer.querySelector("div[data-role='productInformation']");
    if (infoDiv) {
        let ps = infoDiv.querySelectorAll("p");
        if (ps.length > 0) name = ps[0].textContent.trim();
        if (ps.length > 1) desc = ps[1].textContent.trim();
    }

    let allPs = Array.from(offer.querySelectorAll("p"));
    let infoPs = new Set(infoDiv ? Array.from(infoDiv.querySelectorAll("p")) : []);
    let nonInfoPs = allPs.filter(p => !infoPs.has(p));

    for (let p of nonInfoPs) {
        let t = p.textContent.trim();
        if (t.endsWith(",-")) { price = t.replace(",-", ""); break; }
    }

    for (let p of nonInfoPs) {
        let t = p.textContent.trim();
        if (/^\\d+\\s+\\S/.test(t) && !t.includes(",-")) { unit = t; break; }
    }

    let imgEl = offer.querySelector("img");
    return {id, name, desc, unit, price, img: imgEl ? imgEl.getAttribute("src") : ""};
});
"""


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


def scroll_page(driver):
    for i in range(0, 20000, 1000):
        driver.execute_script(f"window.scrollTo(0, {i});")
        time.sleep(0.3)


def compute_image_hash(url):
    if not url:
        return ""
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return str(imagehash.phash(Image.open(BytesIO(response.content))))
    except Exception:
        return ""


def parse_netto_vaegt(desc):
    m = _WEIGHT_RE.search(desc)
    return m.group(1).strip() if m else ""


def parse_kg_price(desc):
    m = _KG_PRICE_RE.search(desc)
    if not m:
        return ""
    t = m.group("type").lower()
    unit = "kg" if "kg" in t else "l" if "liter" in t else "stk"
    return f"{m.group(2)} kr/{unit}"


def parse_normal_price(desc):
    m = _NORMAL_PRICE_RE.search(desc)
    return m.group(1).replace(",", ".") if m else ""


def extract_producer(name):
    parts = name.strip().split()
    return parts[0] if parts else ""


def setup_worksheet(ws):
    ws.append(_HEADERS)
    for col, _ in enumerate(_HEADERS, 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True, name="Arial")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for col, width in _COLUMN_WIDTHS.items():
        ws.column_dimensions[col].width = width


def process_items(cards_data):
    from ai_classifier import should_include_product
    results = []
    for item in cards_data:
        name = item.get("name", "")
        if not name:
            continue
        desc = item.get("desc", "")
        if not should_include_product(name, desc):
            continue
        price = item.get("price", "0").replace(",", ".")
        unit = item.get("unit", "")
        img_url = item.get("img", "")
        try:
            price_val = float(price)
        except ValueError:
            price_val = price
        results.append((
            "Avis",
            name,
            extract_producer(name),
            parse_netto_vaegt(desc) or parse_netto_vaegt(unit),
            parse_kg_price(desc),
            price_val,
            parse_normal_price(desc),
            item.get("id", ""),
            img_url,
            compute_image_hash(img_url),
            "Ja",
            unit,
        ))
    return results


def save_workbook(results, filename):
    wb = Workbook()
    ws = wb.active
    ws.title = "Produkter"
    setup_worksheet(ws)
    for row in results:
        ws.append(row)
    wb.save(filename)
    print(f"\n{len(results)} varer i alt gemt i: {filename}")
