"""Cloudflare Workers entry - WSGI bridge til Flask."""
from __future__ import annotations

import os

# Cloudflare Python Workers eksponerer ikke bindings i os.environ. Markér
# edge-tilstand FØR app importeres, så app._IS_EDGE bliver True ved import.
os.environ["CLOUDFLARE_WORKERS"] = "1"
os.environ.setdefault("ENABLE_PRICE_DB", "0")

from typing import Protocol

from edgekit.adapters import WSGI
from edgekit.bindings import KVNamespace, D1Database
from edgekit.webapi.response import Response as EdgeResponse

from app import app as flask_app


def _too_many(request=None) -> EdgeResponse:
    """JSON for /api/* så fetch().json() i browseren ikke fejler på rate limit."""
    headers = {"Retry-After": "10", "Cache-Control": "no-store"}
    path = ""
    try:
        if request is not None:
            from urllib.parse import urlparse
            path = urlparse(str(request.url)).path
    except Exception:
        pass
    if path.startswith("/api/"):
        return EdgeResponse.json(
            {"success": False, "error": "For mange forespørgsler - prøv igen om lidt."},
            status=429,
            headers=headers,
        )
    return EdgeResponse.text(
        "For mange forespørgsler - prøv igen om lidt.",
        status=429,
        headers=headers,
    )


# Cache-version caches pr. isolate i 5 min, så vi ikke rammer KV på hver request.
_cache_ver = None
_cache_ver_at = 0.0
_CACHE_VER_TTL = 300.0


class Env(Protocol):
    CACHE_KV: KVNamespace
    DB: D1Database
    # Vars/secrets deklareres så EdgeKit kan læse dem fra env-objektet.
    SUPABASE_URL: str
    NEXT_PUBLIC_SUPABASE_URL: str
    SUPABASE_KEY: str
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: str
    CACHE_REFRESH_SECRET: str
    ENABLE_PRICE_DB: str
    TABLE_SUFFIX: str


class Default(WSGI[Env]):
    app = flask_app

    async def _rate_ok(self, request) -> bool:
        """Rate limiting via Cloudflares gratis native binding. Fail-open:
        enhver fejl (manglende binding, undtagelse) tillader forespørgslen,
        så kernefunktionen aldrig kan brydes af beskyttelsen."""
        try:
            limiter = None
            try:
                limiter = getattr(self.raw_env, "RATE_LIMITER", None)
            except Exception:
                limiter = None
            if limiter is None:
                return True
            import js
            from pyodide.ffi import to_js
            ip = (
                request.headers.get("CF-Connecting-IP")
                or request.headers.get("X-Forwarded-For")
                or "anon"
            )
            arg = to_js({"key": str(ip)}, dict_converter=js.Object.fromEntries)
            outcome = await limiter.limit(arg)
            return bool(getattr(outcome, "success", True))
        except Exception:
            return True

    async def _cache_version(self) -> str:
        """Aktuel cache-version fra KV (sat af daglig seed). Cachet pr. isolate."""
        global _cache_ver, _cache_ver_at
        try:
            from js import Date
            now = float(Date.now()) / 1000.0
        except Exception:
            now = 0.0
        if _cache_ver is not None and (now - _cache_ver_at) < _CACHE_VER_TTL:
            return _cache_ver
        try:
            kv = getattr(self.raw_env, "CACHE_KV", None)
            val = await kv.get("cache_version") if kv is not None else None
            _cache_ver = str(val) if val else (_cache_ver or "0")
        except Exception:
            _cache_ver = _cache_ver or "0"
        _cache_ver_at = now
        return _cache_ver

    async def _cache_key(self, request):
        """Versioneret cache-nøgle (JS Request). Når cache_version ændres ved
        daglig seed, misser alle gamle nøgler → friske priser med det samme."""
        from js import Request as JSRequest
        ver = await self._cache_version()
        url = str(request.url)
        sep = "&" if "?" in url else "?"
        return JSRequest.new(f"{url}{sep}__cv={ver}")

    async def _cache_hit_ok(self, response) -> bool:
        """Afvis cache-treff der er AJAX-fragmenter uden <head>/CSS - de gør
        forsiden ubrugelig hvis de serveres som hel side."""
        try:
            ct = (response.headers.get("Content-Type") or "").lower()
            if "text/html" not in ct:
                return True
            text = str(await response.clone().text())
            low = text.lower()
            return "<head" in low and ("stylesheet" in low or 'rel="stylesheet"' in low)
        except Exception:
            return True

    async def fetch(self, request):
        # Ikke-GET (POST mv.) er dyre/skrivende → rate limit før arbejde.
        if request.method != "GET":
            if not await self._rate_ok(request):
                return _too_many(request)
            return await super().fetch(request)

        # AJAX-kald (X-Requested-With) rammer de samme URL'er som en normal
        # sidevisning, men Flask returnerer et HTML-fragment uden <head>/CSS
        # (se X-Requested-With-check i app.py). Cache-nøglen varierer ikke
        # efter denne header, så et cachet fragment ville blive serveret som
        # hele siden til almindelige besøgende (forsiden mistede CSS herved).
        # Undgå det ved slet ikke at læse/skrive edge-cache for AJAX-kald.
        is_ajax = (request.headers.get("X-Requested-With") or "") == "XMLHttpRequest"

        # Edge-cache GET-svar (Cache-Control: public) så samtidige/gentagne
        # visninger betjenes uden dyr gengivelse. Nøglen versioneres, så den
        # daglige opdatering automatisk nulstiller cachen (24t TTL uden staleness).
        cache = None
        key_req = None
        if not is_ajax:
            try:
                from js import caches
                cache = caches.default
                key_req = await self._cache_key(request)
                hit = await cache.match(key_req)
                if hit is not None and await self._cache_hit_ok(hit):
                    return hit
            except Exception:
                cache = None

        # Rate limit KUN cache-miss-stien (cache-hits returnerede allerede
        # ovenfor og rammer aldrig her). _rate_ok er et enkelt async I/O-kald
        # til Cloudflares binding og lægger ikke CPU-tid på selve renderingen
        # - men den forhindrer at mange samtidige cold-cache-renders sender
        # worker'en over 10 ms-grænsen ad gangen (det var præcis mønstret der
        # væltede produktionen 2026-07-19, se cloudflare-incident-2026-07-19).
        if not await self._rate_ok(request):
            return _too_many(request)
        response = await super().fetch(request)
        try:
            if cache is not None and key_req is not None:
                cc = response.headers.get("Cache-Control") or ""
                if "public" in cc and "no-store" not in cc:
                    if await self._cache_hit_ok(response):
                        self.ctx.waitUntil(cache.put(key_req, response.clone()))
        except Exception:
            pass
        return response
