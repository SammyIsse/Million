import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keywords import NON_FOOD_KEYWORDS, FOOD_KEYWORDS

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DB = os.path.join(_ROOT_DIR, 'data', 'ai_classifier_cache.db')

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


def should_include_product(name: str, description: str = '', category: str = '') -> bool:
    """
    Returnerer True hvis produktet bør inkluderes (er en fødevare).

    Rækkefølge:
    1. NON_FOOD_KEYWORDS sortliste     → False hvis match
    2. FOOD_KEYWORDS hvidliste         → True hvis match
    3. Tidligere klassificerede varer  → cache-hit (historiske afgørelser
       fra dengang dette slog Ollama op - rent opslag, ingen nye kald)
    4. Fail-safe                       → inkluder hellere end at misse en
       fødevare
    """
    clean_name = name.lower().rstrip('*').rstrip() + ' '
    text = f'{clean_name} {description}'.lower()

    if any(frag in text for frag in NON_FOOD_KEYWORDS):
        return False
    if any(kw in clean_name for kw in FOOD_KEYWORDS):
        return True

    row = _conn.execute(
        'SELECT is_food FROM ai_cache WHERE product_key = ?',
        (_cache_key(name, category),),
    ).fetchone()
    if row is not None:
        return bool(row[0])

    return True
