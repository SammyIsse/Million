from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
import time
import requests
from PIL import Image
from io import BytesIO
import imagehash
import os
import re

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

def compute_image_hash(url):
    if not url:
        return ""
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content))
        return str(imagehash.phash(img))
    except Exception:
        return ""

def parse_netto_vaegt(desc):
    match = re.search(r'([\d.,\-\s]+(?:x\s*[\d.,\-\s]+)?(?:g|kg|l|liter|ml|cl|stk))', desc, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""

def parse_kg_price(desc):
    match = re.search(r'(?:kg-pris|literpris|stk-pris)[^\d]*([\d.,]+)', desc, re.IGNORECASE)
    if match:
        val = match.group(1)
        if re.search(r'kg-pris', desc, re.IGNORECASE):
            unit = "kg"
        elif re.search(r'literpris', desc, re.IGNORECASE):
            unit = "l"
        else:
            unit = "stk"
        return f"{val} kr/{unit}"
    return ""

def parse_normal_price(desc):
    match = re.search(r'[Ff]ør-pris\s*([\d.,]+)', desc, re.IGNORECASE)
    if match:
        return match.group(1).replace(",", ".")
    return ""

def extract_producer(name):
    parts = name.strip().split()
    return parts[0] if parts else ""

def scrape_365discount():
    url = "https://365discount.coop.dk/365avis/"
    driver = create_driver()

    print(f"  -> Henter tilbudsavis fra {url}")
    try:
        driver.get(url)
        time.sleep(3)

        print("  -> Scroller for at indlæse lazy-loaded indhold...")
        for i in range(0, 20000, 1000):
            driver.execute_script(f"window.scrollTo(0, {i});")
            time.sleep(0.3)

        js_extract = """
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
                if (t.endsWith(",-")) {
                    price = t.replace(",-", "");
                    break;
                }
            }

            for (let p of nonInfoPs) {
                let t = p.textContent.trim();
                if (/^\\d+\\s+\\S/.test(t) && !t.includes(",-")) {
                    unit = t;
                    break;
                }
            }

            let img = "";
            let imgEl = offer.querySelector("img");
            if (imgEl) img = imgEl.getAttribute("src");

            return {id, name, desc, unit, price, img};
        });
        """

        cards_data = driver.execute_script(js_extract)
        if not cards_data:
            print("  ! Ingen tilbud fundet.")
            return []

        print(f"    Fandt {len(cards_data)} tilbud.")

        _BLOCKED_NAME_FRAGMENTS = {
            'indlæg', 'batteri', 'shampoo', 'balsam', 'creme', 'lotion', 'bleer',
            'bleposer', 'vaskeserviet', 'vådserviet', 'skumvaskeklud', 'sutteflaske',
            'hundemad', 'kattefoder', 'kattemad', 'hundesnack', 'kattegrus',
            'tandpasta', 'tandbørste', 'håndsæbe', 'shower gel', 'deodorant',
            'bind', 'tampon', 'opvaskemiddel', 'vaskemiddel', 'skyllemiddel',
            'tobak', 'cigaret', 'cigarillo', 'snus', 'nikotin', 'tændstik',
            'lighter', 'fyrstikker', 'toiletpapir', 'køkkenrulle', 'køkken rulle',
            'plante', 'planter', 'potte', 'potteskjuler', 'blomst', 'blomster',
            'buket', 'roser', 'tulipaner', 'orkidé', 'krysantemum', 'jord', 'gødning',
            'fyrfadslys', 'stearinlys', 'lys ', 'kronelys', 'bloklys', 'levende lys',
            'kaffemaskine', 'kaffemaskiner', 'espressomaskine', 'kapselmaskine',
            'babypads', 'babybleer', 'hummel', 'nike', 'friends', 'latz', 'pedigree', 'bagepapir',
            'termokande', 'hot wheels', 'legetøj', 'husk', 'gourmet', 'opvasketabs', 'tørrestativ',
            'stegepande', 'sneakers', 'solseng', 'parasol', 'badeklæde', 'fuglebad', 'smartstore',
            'scrub daddy', 'vileda', 'skuresvampe', 'dørmåtte', 'sengetøj', 'tramontina', 'snaxx',
            'jackpot', 't-shirt', 'fiskegrej', 'høreværn', 'elkedel', 'airfryer', 'robotplæneklipper',
            'sengetæppe', 'støvsugerpose', 'højtaler', 'gavlpude','solbriller','sommerhat', 'gummisko',
            'strandtaske', 'leggings', 'badevinger', 'badedyr', 'strandbold', 'kuglepistol',
            'dame', 'herre', 'voksen', 'barn', 'ung','fritids', 'udendørs','indendørs', 'kridt', 
            'strandkridt', 'fodbold','jumbo', 'eller', 'showergel', 'tabs','rengøring',
            'pande', 'håndopvask', 'massage', 'kurv', 'støvsuger', 'strygerobot',
            'husholdningsprodukter', 'mobiltilbehør', 'opbevaring', 'klar til sommer',
            'deospray', 'håndæbe', 'vaskekapsler', 'vaske-middel', 'toiletrengøring',
            'bref', 'domestos', 'harpic', 'blokke', 'vitaminer', 'livol', 'gerimax',
            'kleenex',
        }

        results = []
        for item in cards_data:
            name = item.get("name", "")
            if not name:
                continue

            desc = item.get("desc", "")

            text_to_check = f"{name} {desc}".lower()
            if any(blocked in text_to_check for blocked in _BLOCKED_NAME_FRAGMENTS):
                continue

            price = item.get("price", "0").replace(",", ".")
            unit = item.get("unit", "")
            img_url = item.get("img", "")

            netto_vaegt = parse_netto_vaegt(desc) or parse_netto_vaegt(unit)
            kg_price = parse_kg_price(desc)
            producer = extract_producer(name)
            normal_price = parse_normal_price(desc)

            img_hash = compute_image_hash(img_url)
            varenummer = item.get("id", "")

            try:
                price_val = float(price)
            except ValueError:
                price_val = price

            results.append((
                "Avis",         # Kategori
                name,           # Navn
                producer,       # Producent
                netto_vaegt,    # Netto Vægt
                kg_price,       # Kg-pris
                price_val,      # Pris
                normal_price,   # Normalpris
                varenummer,     # Varenummer
                img_url,        # Billede URL
                img_hash,       # Billede Hash
                "Ja",           # Tilbud
                unit            # Enhed
            ))

        return results

    finally:
        driver.quit()

def setup_worksheet(ws):
    headers = [
        "Kategori", "Navn", "Producent", "Netto Vægt",
        "Kg-pris", "Pris", "Normalpris", "Varenummer", "Billede URL", "Billede Hash", "Tilbud", "Enhed"
    ]
    ws.append(headers)

    for col, _ in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True, name="Arial")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    ws.column_dimensions["A"].width = 15
    ws.column_dimensions["B"].width = 35
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 15
    ws.column_dimensions["E"].width = 12
    ws.column_dimensions["F"].width = 10
    ws.column_dimensions["G"].width = 12
    ws.column_dimensions["H"].width = 16
    ws.column_dimensions["I"].width = 80
    ws.column_dimensions["J"].width = 20
    ws.column_dimensions["K"].width = 10
    ws.column_dimensions["L"].width = 12

def main():
    wb = Workbook()
    ws = wb.active
    ws.title = "Produkter"
    setup_worksheet(ws)

    print("Starter scraping af 365 Discount tilbudsavis...")
    results = scrape_365discount()

    for row in results:
        ws.append(row)

    filename = os.path.join(_ROOT_DIR, 'Xlsx filer', '365Discount_produkter.xlsx')
    wb.save(filename)
    print(f"\n{len(results)} varer i alt gemt i: {filename}")

if __name__ == "__main__":
    main()
