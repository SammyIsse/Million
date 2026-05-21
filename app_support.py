"""Shared utilities: logging, rate limiting, search index, optional DB flag."""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from functools import wraps
from typing import Callable

logger = logging.getLogger('million')

_db_available: bool | None = None


def configure_logging() -> None:
    level = logging.DEBUG if os.environ.get('FLASK_DEBUG', '0') == '1' else logging.INFO
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        )


def is_price_db_enabled() -> bool:
    """Check env before init. Use db_available() after init_db() attempt."""
    flag = os.environ.get('ENABLE_PRICE_DB', 'auto').lower()
    if flag in ('0', 'false', 'no', 'off'):
        return False
    if flag in ('1', 'true', 'yes', 'on'):
        return True
    return True  # auto: try on startup


def set_db_available(ok: bool) -> None:
    global _db_available
    _db_available = ok


def db_available() -> bool:
    if _db_available is None:
        return is_price_db_enabled()
    return _db_available


class RateLimiter:
    """In-memory per-IP rate limit (no database)."""

    def __init__(self, max_calls: int = 60, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=max_calls))

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = self._hits[key]
        
        while hits and now - hits[0] >= self.window_seconds:
            hits.popleft()
            
        if len(hits) >= self.max_calls:
            return False
            
        hits.append(now)
        return True


api_limiter = RateLimiter(max_calls=60, window_seconds=60)


def rate_limit(limiter: RateLimiter) -> Callable:
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            from flask import jsonify, request

            ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown')
            if ',' in ip:
                ip = ip.split(',')[0].strip()
            key = f'{ip}:{f.__name__}'
            if not limiter.allow(key):
                logger.warning('Rate limit exceeded for %s', key)
                return jsonify(
                    success=False,
                    error='For mange forespørgsler. Prøv igen om lidt.',
                ), 429
            return f(*args, **kwargs)

        return wrapped
    return decorator


def build_search_index(products: list, normalize_fn) -> dict[str, set[str]]:
    """token -> set of product ids for fast AND-search."""
    index: dict[str, set[str]] = {}
    for product in products:
        pid = str(product.get('/product/id', '')).strip()
        if not pid or pid in ('None', ''):
            continue
        text = ' '.join([
            str(product.get('/product/title', '')),
            str(product.get('/product/brand', '')),
            str(product.get('/product/description', '')),
        ])
        norm = normalize_fn(text)
        seen_tokens: set[str] = set()
        for token in norm.split():
            if len(token) >= 3 and token not in seen_tokens:
                seen_tokens.add(token)
                index.setdefault(token, set()).add(pid)
    return index


def search_product_ids(index: dict[str, set[str]], query: str) -> set[str] | None:
    """
    Return product ids matching ALL query terms, or None if index should not be used.
    """
    terms = [t for t in query.lower().split() if len(t) >= 2]
    if not terms or not index:
        return None

    result: set[str] | None = None
    for term in terms:
        term_ids: set[str] = set()
        for token, pids in index.items():
            if term in token:
                term_ids.update(pids)
        if not term_ids:
            return set()
        result = term_ids if result is None else result & term_ids
    return result or set()


def product_matches_query(product: dict, query: str) -> bool:
    """Fallback substring search when index is unavailable."""
    terms = query.lower().split()
    if not terms:
        return False
    name = str(product.get('name', '')).lower()
    brand = str(product.get('brand', '')).lower()
    desc = str(product.get('description', '')).lower()
    fields = (name, brand, desc)
    return all(any(term in field for field in fields) for term in terms)
