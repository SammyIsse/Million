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

from app import app as flask_app


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

    async def fetch(self, request):
        # Edge-cache GET-svar (Cache-Control: public) så samtidige/gentagne
        # visninger betjenes uden dyr gengivelse. På custom domæne betyder det
        # at worker'en spares helt for cache-hits — afgørende for free-plan.
        if request.method != "GET":
            return await super().fetch(request)

        cache = None
        raw_req = None
        try:
            from js import caches
            cache = caches.default
            raw_req = request.raw
            hit = await cache.match(raw_req)
            if hit is not None:
                return hit
        except Exception:
            cache = None

        response = await super().fetch(request)
        try:
            if cache is not None and raw_req is not None:
                cc = response.headers.get("Cache-Control") or ""
                if "public" in cc and "no-store" not in cc:
                    self.ctx.waitUntil(cache.put(raw_req, response.clone()))
        except Exception:
            pass
        return response
