import os
import sys
import time

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scraper_utils import create_driver, scroll_page, JS_EXTRACT, process_items, save_workbook


def scrape_365discount():
    url = "https://365discount.coop.dk/365avis/"
    driver = create_driver()
    print(f"  -> Henter tilbudsavis fra {url}")
    try:
        driver.get(url)
        time.sleep(3)
        print("  -> Scroller for at indlæse lazy-loaded indhold...")
        scroll_page(driver)
        cards_data = driver.execute_script(JS_EXTRACT)
        if not cards_data:
            print("  ! Ingen tilbud fundet.")
            return []
        print(f"    Fandt {len(cards_data)} tilbud.")
        return process_items(cards_data)
    finally:
        driver.quit()


def main():
    print("Starter scraping af 365 Discount tilbudsavis...")
    results = scrape_365discount()
    save_to_supabase(results, "365discount", row_type="simple")


if __name__ == "__main__":
    main()
