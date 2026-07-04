"""Cloudflare Workers entry — WSGI bridge til Flask."""
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


def _too_many() -> EdgeResponse:
    return EdgeResponse.text(
        "For mange forespørgsler — prøv igen om lidt.",
        status=429,
        headers={"Retry-After": "10", "Cache-Control": "no-store"},
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

    async def fetch(self, request):
        # Ikke-GET (POST mv.) er dyre/skrivende → rate limit før arbejde.
        if request.method != "GET":
            if not await self._rate_ok(request):
                return _too_many()
            return await super().fetch(request)

        # Edge-cache GET-svar (Cache-Control: public) så samtidige/gentagne
        # visninger betjenes uden dyr gengivelse. Nøglen versioneres, så den
        # daglige opdatering automatisk nulstiller cachen (24t TTL uden staleness).
        cache = None
        key_req = None
        try:
            from js import caches
            cache = caches.default
            key_req = await self._cache_key(request)
            hit = await cache.match(key_req)
            if hit is not None:
                return hit
        except Exception:
            cache = None

        # Bevidst INGEN rate limiting på GET-render-stien: den er CPU-tæt på
        # free-plan (10 ms), og ekstra arbejde her ville kunne udløse 1102-fejl.
        # GET beskyttes i stedet af caching + Cloudflares automatiske DDoS-værn.
        response = await super().fetch(request)
        try:
            if cache is not None and key_req is not None:
                cc = response.headers.get("Cache-Control") or ""
                if "public" in cc and "no-store" not in cc:
                    self.ctx.waitUntil(cache.put(key_req, response.clone()))
        except Exception:
            pass
        return response
