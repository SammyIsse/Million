import re

with open('updater.py', 'r', encoding='utf-8') as f:
    c = f.read()

c = re.sub(r'def _refresh_product_cache.*', '''
def run_updater():
    import os, httpx
    logger.info("Starter opdatering af produkt-cache...")
    fresh = fetch_and_parse_xml()
    if not fresh:
        return
    search_index = build_search_index(fresh, normalize_name)
    if not db_available():
        return
    payload = {'id': 1, 'data': fresh, 'search_index': search_index}
    try:
        url = f"{os.getenv('SUPABASE_URL')}/rest/v1/app_cache"
        headers = {"apikey": os.getenv("SUPABASE_KEY"), "Authorization": f"Bearer {os.getenv('SUPABASE_KEY')}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
        with httpx.Client(timeout=120.0) as client:
            res = client.post(url, headers=headers, json=payload)
            res.raise_for_status()
        record_prices_batch(collect_store_prices(fresh))
    except Exception as e:
        logger.error(f"Fejl: {e}")

if __name__ == '__main__':
    run_updater()
''', c, flags=re.DOTALL)

with open('updater.py', 'w', encoding='utf-8') as f:
    f.write(c)
