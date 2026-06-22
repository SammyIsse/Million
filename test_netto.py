"""
Hurtig test af Netto-scraperen.
Kør: python test_netto.py
"""
import os, sys, time, requests
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scraper"))

BASE_URL = "https://api.sallinggroup.com"

# URLs vi prøver for at finde den rigtige netto.dk-struktur
CANDIDATE_URLS = [
    "https://netto.dk/netto-avisen/",
    "https://netto.dk/pris-chok/",
]


# ── 1. Salling API ────────────────────────────────────────────────────────────
def test_api():
    api_key = os.environ.get("SALLING_API_KEY")
    if not api_key:
        print("❌  SALLING_API_KEY ikke fundet i .env")
        print("    Tilføj: SALLING_API_KEY=din-nøgle-her")
        return False

    headers = {"Authorization": f"Bearer {api_key}"}

    print("── Salling API ──────────────────────────────────────────")
    r = requests.get(f"{BASE_URL}/v2/stores", headers=headers,
                     params={"brand": "netto", "country": "dk"}, timeout=15)
    print(f"  /v2/stores?brand=netto  →  HTTP {r.status_code}")
    if r.status_code == 200:
        stores = r.json()
        if not isinstance(stores, list):
            stores = stores.get("items", stores.get("stores", []))
        print(f"  Fandt {len(stores)} Netto-butikker")
        if stores:
            first_id = stores[0].get("id", "")
            r2 = requests.get(f"{BASE_URL}/v1/food-waste/{first_id}",
                              headers=headers, timeout=15)
            print(f"  /v1/food-waste/{first_id}  →  HTTP {r2.status_code}")
            if r2.status_code == 200:
                items = r2.json().get("clearance", [])
                print(f"  {len(items)} madspild-varer i første butik")
        return True
    else:
        print(f"  Svar: {r.text[:300]}")
        return False


# ── 2. netto.dk URL-opdagelse ─────────────────────────────────────────────────
def test_website():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from webdriver_manager.chrome import ChromeDriverManager

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    def reject_cookies():
        try:
            r = driver.execute_script("""
                const h = document.querySelector('#usercentrics-root');
                if (h && h.shadowRoot) {
                    const b = h.shadowRoot.querySelector('button[data-action-type="deny"]');
                    if (b) { b.click(); return true; }
                }
                return false;
            """)
            if r:
                time.sleep(1)
        except Exception:
            pass

    def dump_structure():
        """Printer nyttige CSS-klasser, links og HTML-snippets fra siden."""
        info = driver.execute_script("""
            const links = Array.from(document.querySelectorAll('a[href]'))
                .map(a => a.getAttribute('href'))
                .filter(h => h && h.startsWith('/') && h.length > 1)
                .filter((h,i,arr) => arr.indexOf(h) === i)
                .slice(0, 30);

            const classes = Array.from(document.querySelectorAll('[class]'))
                .map(e => {
                    const cn = e.className;
                    return (typeof cn === 'string') ? cn.trim().split(/\\s+/)[0] : '';
                })
                .filter(c => c && c.length > 3)
                .filter((c,i,arr) => arr.indexOf(c) === i)
                .slice(0, 50);

            const h1s = Array.from(document.querySelectorAll('h1,h2,h3'))
                .map(e => e.innerText.trim())
                .filter(t => t && t.length < 100)
                .slice(0, 8);

            // Find elementer der ligner produktkort (har billede + tekst + pris-lignende tal)
            const candidates = Array.from(document.querySelectorAll('article, li, [class*="card"], [class*="item"], [class*="product"], [class*="offer"], [class*="tile"]'))
                .filter(el => el.querySelector('img') && el.innerText.trim().length > 5)
                .slice(0, 3)
                .map(el => ({
                    tag: el.tagName,
                    cls: (typeof el.className === 'string') ? el.className.slice(0,80) : '',
                    text: el.innerText.trim().slice(0, 120),
                    html: el.outerHTML.slice(0, 300),
                }));

            return { links, classes, h1s, candidates };
        """)
        print(f"\n  Sidetitel: {driver.title}")
        print(f"  Interne links: {info['links'][:20]}")
        print(f"  Overskrifter: {info['h1s']}")
        print(f"  CSS-klasser: {info['classes'][:30]}")
        if info['candidates']:
            print(f"\n  Produktkort-kandidater ({len(info['candidates'])}):")
            for c in info['candidates']:
                print(f"    <{c['tag']} class=\"{c['cls']}\">")
                print(f"    Tekst: {c['text']}")
                print(f"    HTML:  {c['html']}")
                print()

    try:
        print("\n── netto.dk URL-opdagelse ───────────────────────────────")
        found_url = None
        found_selector = None

        for url in CANDIDATE_URLS:
            print(f"\n  Prøver: {url}")
            driver.get(url)
            time.sleep(3)
            reject_cookies()
            time.sleep(1)
            driver.execute_script("window.scrollTo(0, 1500);")
            time.sleep(1.5)

            title = driver.title
            if "eksisterer ikke" in title.lower() or "404" in title or "not found" in title.lower():
                print(f"    → 404/ikke fundet ({title})")
                continue

            print(f"    → Sidetitel: {title}")

            # Tjek for produktkort med alle kendte selektorer
            selectors = [
                "div.product-card-container",
                "[class*='ProductCard']",
                "[class*='product-card']",
                "[class*='product-tile']",
                "[class*='ProductTile']",
                "[class*='offer-card']",
                "[data-testid*='product']",
                "article[class*='product']",
                "li[class*='product']",
            ]
            for sel in selectors:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els:
                    print(f"    ✅ '{sel}'  →  {len(els)} produktkort fundet!")
                    found_url = url
                    found_selector = sel
                    break

            if found_url:
                break
            else:
                print(f"    ⚠  Ingen produktkort fundet — dumper sidestruktur...")
                dump_structure()

        if found_url:
            print(f"\n  ✅ Fungerende URL: {found_url}")
            print(f"  ✅ CSS-selector:   {found_selector}")
            print(f"\n  OBS: Opdater CATEGORY_URLS i webscrape_netto.py til den rigtige sti")
            return True, found_url, found_selector
        else:
            # Dump struktur for forsiden som sidste udvej
            print("\n  Henter forsiden for at se navigationsstruktur...")
            driver.get("https://netto.dk/")
            time.sleep(3)
            reject_cookies()
            time.sleep(1)
            dump_structure()
            print("\n  ❌ Ingen af de prøvede URLs returnerede produktkort.")
            print("     Brug de interne links/klasser ovenfor til at finde den rigtige URL.")
            return False, None, None
    finally:
        driver.quit()


# ── Kør ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    api_ok = test_api()
    web_ok, url, sel = test_website()

    print("\n══════════════════════════════════════")
    print(f"  API:      {'✅ OK' if api_ok else '❌ Tilføj SALLING_API_KEY til .env'}")
    print(f"  netto.dk: {'✅ ' + (url or '') if web_ok else '❌ Se dump ovenfor'}")
    if web_ok and sel != "div.product-card-container":
        print(f"\n  ⚠  Selector er '{sel}' (ikke standard).")
        print(f"     Opdater _JS_EXTRACT i webscrape_netto.py")
    print("══════════════════════════════════════")
