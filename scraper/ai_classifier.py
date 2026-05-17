import os
import sys
import csv
import sqlite3
import requests as _requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keywords import NON_FOOD_KEYWORDS, FOOD_KEYWORDS

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DB = os.path.join(_ROOT_DIR, 'data', 'ai_classifier_cache.db')
_LOG_FILE = os.path.join(_ROOT_DIR, 'data', 'ai_decisions.csv')

_OLLAMA_MODEL = 'gemma3:4b'
_OLLAMA_URL = 'http://localhost:11434/api/generate'

_conn = sqlite3.connect(_CACHE_DB)
_conn.execute("""
    CREATE TABLE IF NOT EXISTS ai_cache (
        product_key TEXT PRIMARY KEY,
        is_food     INTEGER NOT NULL,
        created_at  TEXT    NOT NULL
    )
""")
_conn.commit()


def _cache_key(name: str, category: str) -> str:
    return f"{name.lower().strip()}|{category.lower().strip()}"


def _log_decision(name: str, category: str, is_food: bool) -> None:
    file_exists = os.path.isfile(_LOG_FILE)
    with open(_LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['timestamp', 'product_name', 'category', 'decision'])
        writer.writerow([datetime.now().isoformat(), name, category, 'JA' if is_food else 'NEJ'])


def _build_prompt(name: str, category: str) -> str:
    lines = [f'Produktnavn: {name}']
    if category:
        lines.append(f'Kategori: {category}')
    lines.append('\nEr dette en fødevare eller drikkevare til mennesker?\nSvar kun JA eller NEJ.')
    return '\n'.join(lines)


def classify_product_with_ai(name: str, category: str = '') -> bool:
    key = _cache_key(name, category)
    row = _conn.execute('SELECT is_food FROM ai_cache WHERE product_key = ?', (key,)).fetchone()
    if row is not None:
        return bool(row[0])

    try:
        payload = {
            'model': _OLLAMA_MODEL,
            'prompt': _build_prompt(name, category),
            'stream': False,
            'options': {'temperature': 0, 'num_predict': 5},
        }
        resp = _requests.post(_OLLAMA_URL, json=payload, timeout=30)
        resp.raise_for_status()
        is_food = resp.json()['response'].strip().upper().startswith('JA')
    except Exception as e:
        print(f'    [AI-klassificering fejlede for "{name}": {e}]')
        return True  # fail-safe: inkluder hellere end at misse en fødevare

    _conn.execute(
        'INSERT OR REPLACE INTO ai_cache (product_key, is_food, created_at) VALUES (?, ?, ?)',
        (key, int(is_food), datetime.now().isoformat()),
    )
    _conn.commit()
    _log_decision(name, category, is_food)
    return is_food


def should_include_product(name: str, description: str = '', category: str = '') -> bool:
    """
    Returnerer True hvis produktet bør inkluderes (er en fødevare).

    Rækkefølge:
    1. NON_FOOD_KEYWORDS sortliste  → False hvis match (ingen AI)
    2. FOOD_KEYWORDS hvidliste      → True hvis match (ingen AI)
    3. Ollama-fallback              → spørg lokalt, gem i cache
    """
    clean_name = name.lower().rstrip('*').rstrip() + ' '
    text = f'{clean_name} {description}'.lower()

    if any(frag in text for frag in NON_FOOD_KEYWORDS):
        return False
    if any(kw in clean_name for kw in FOOD_KEYWORDS):
        return True
    return classify_product_with_ai(name, category)
