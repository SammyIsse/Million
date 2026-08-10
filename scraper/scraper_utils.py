from webdriver_manager.chrome import ChromeDriverManager
import re
import time
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from app_support import compute_image_hash

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

_WEIGHT_RE = re.compile(r'([\d.,\-\s]+(?:x\s*[\d.,\-\s]+)?(?:g|kg|l|liter|ml|cl|stk))', re.IGNORECASE)
_KG_PRICE_RE = re.compile(r'(?P<type>kg-pris|literpris|stk-pris)[^\d]*([\d.,]+)', re.IGNORECASE)
_NORMAL_PRICE_RE = re.compile(r'før-pris\s*([\d.,]+)', re.IGNORECASE)

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
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)


def scroll_page(driver, max_steps=60):
    """Scroll til bunden, saa alt lazy-loadet indhold naar at komme med.

    Foer scrollede vi til et fast punkt paa 20.000 px. En laengere avis blev
    derfor aldrig loadet faerdig, og de sidste tilbud manglede stille - uden
    fejl, saa ingen opdagede det. Nu foelger vi den faktiske sidehoejde og
    stopper foerst, naar den ikke vokser mere (eller ved max_steps, saa en
    side med uendelig scroll ikke kan koere loebsk)."""
    last_height = 0
    for _ in range(max_steps):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.4)
        height = driver.execute_script("return document.body.scrollHeight") or 0
        if height <= last_height:
            break
        last_height = height


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


_AVIS_PRICE_RE = re.compile(r'(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{1,2}))?')


def _parse_avis_price(raw):
    """Pris fra en avistekst, eller None hvis den ikke kan laeses.

    Coop-avisernes JS tager foerste <p> der ender paa ",-" og fjerner kun
    ",-", saa teksten kan indeholde alt fra "12,95" til "FRIT VALG 10" og
    "2 FOR 25". Vi tager det SIDSTE tal i strengen (det er prisen i
    "2 FOR 25") og haandterer dansk tusindtalsseparator: "1.234,-" er 1234
    kroner, ikke 1,23 - float("1.234") gav foer den fejl for varer over 999 kr.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    matches = _AVIS_PRICE_RE.findall(text)
    if not matches:
        return None
    heltal, decimaler = matches[-1]
    try:
        value = float(heltal.replace('.', '') + ('.' + decimaler if decimaler else ''))
    except ValueError:
        return None
    return value if value > 0 else None


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
        unit = item.get("unit", "")
        img_url = item.get("img", "")
        price_val = _parse_avis_price(item.get("price", ""))
        if price_val is None:
            # Kan prisen ikke laeses, SPRINGES varen over. Foer beholdt vi den
            # raa streng, og gem-laget kalder float(row[5]) uden try - saa én
            # uparsebar pris ("FRIT VALG 10,-", "2 FOR 25,-") kastede
            # ValueError og vaeltede HELE butikkens gem. Avisen gaelder en uge,
            # saa alle koersler fejlede indtil den skiftede: Brugsen, Kvickly
            # og SuperBrugsen kunne staa med gamle priser i op til syv dage.
            # Én manglende vare er uendeligt meget billigere.
            print(f"  advarsel: springer over (uparsebar pris {item.get('price')!r}): {name}")
            continue
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
