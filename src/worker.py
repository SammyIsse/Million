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


# ---------------------------------------------------------------------------
# Sikkerhedslogning
# ---------------------------------------------------------------------------
# Workers-observability er permanent slået fra (dens introspektion var selv
# årsagen til nedbruddet 2026-07-19), så der findes ingen request- eller fejllog
# i produktion. Uden noget som helst ville et angreb kun kunne opdages ved at
# sitet gik ned.
#
# Derfor: tæl kun de INTERESSANTE hændelser (429 fra rate limiteren, 5xx fra
# appen), aggregér dem i hukommelsen pr. isolate, og skyl højst ÉN gang i
# minuttet. Det er afgørende at det er aggregeret: en log-linje pr. blokeret
# request ville gøre logningen til angrebets egen forstærker - præcis den
# fejlklasse der væltede produktionen sidst. Ved et angreb koster dette 1
# D1-skrivning i minuttet pr. isolate, uanset hvor mange requests der kommer.
#
# Skrivningen sker i ctx.waitUntil, så den aldrig forsinker et svar, og hele
# stien er pakket ind i try/except: logning må aldrig kunne bryde sitet.
_SEC_FLUSH_INTERVAL = 60.0
# Loft på antal distinkte STIER vi tæller hver for sig. Derover samles alt i én
# "(overflow)"-nøgle pr. hændelsestype, så taget er _SEC_MAX_KEYS + antal typer
# (2 i dag). Uden loftet kunne en angriber generere uendeligt mange unikke stier
# og dermed få aggregatet - og D1-tabellen - til at vokse frit.
_SEC_MAX_KEYS = 200
_sec_counts: dict = {}
_sec_flush_at = 0.0
_sec_table_ready = False

_SEC_CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS security_events ("
    "bucket TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL, "
    "events INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (bucket, kind, path))"
)
_SEC_INSERT_SQL = (
    "INSERT INTO security_events (bucket, kind, path, events) VALUES (?, ?, ?, ?) "
    "ON CONFLICT(bucket, kind, path) DO UPDATE SET events = events + excluded.events"
)


def _now_ms() -> float:
    try:
        from js import Date
        return float(Date.now())
    except Exception:
        return 0.0


def _sec_path(request) -> str:
    """Kun første sti-segment. En angriber kan ellers generere uendeligt mange
    unikke stier og dermed uendeligt mange log-rækker."""
    try:
        from urllib.parse import urlparse
        parts = [p for p in urlparse(str(request.url)).path.split('/') if p]
        if not parts:
            return '/'
        head = parts[0][:32]
        # /api/<navn> er værd at skelne - resten samles under sit første led.
        if head == 'api' and len(parts) > 1:
            return f'/api/{parts[1][:32]}'
        return f'/{head}'
    except Exception:
        return '?'


def _sec_note(kind: str, request) -> None:
    try:
        key = (kind, _sec_path(request))
        if key not in _sec_counts and len(_sec_counts) >= _SEC_MAX_KEYS:
            key = (kind, '(overflow)')
        _sec_counts[key] = _sec_counts.get(key, 0) + 1
    except Exception:
        pass


def _sec_flush(env, ctx) -> None:
    """Skyl aggregatet til D1, højst én gang i minuttet. Aldrig blokerende."""
    global _sec_flush_at, _sec_table_ready
    try:
        now = _now_ms()
        if not _sec_counts:
            return
        if _sec_flush_at and (now - _sec_flush_at) < _SEC_FLUSH_INTERVAL * 1000.0:
            return
        _sec_flush_at = now

        db = getattr(env, 'DB', None)
        if db is None:
            _sec_counts.clear()
            return

        snapshot = list(_sec_counts.items())
        _sec_counts.clear()

        # Minut-spand: gør rækkerne idempotente på tværs af isolates og holder
        # tabellen lille uanset trafikmængde.
        try:
            from js import Date
            bucket = str(Date.new(Date.now()).toISOString())[:16]
        except Exception:
            bucket = '?'

        from pyodide.ffi import to_js
        # DDL holdes UDE af batch'en. D1's batch koerer som én alt-eller-intet-
        # transaktion, og bliver et CREATE afvist deri, ville hver eneste
        # efterfoelgende skylning fejle med - altsaa permanent tavs logning.
        # Som separat statement er moensteret det samme som
        # _ensure_pending_feedback_table() i app.py, der er bevist i drift.
        # Én gang pr. isolate; tabellen oprettes desuden hver 15. minut af
        # scripts/relay-security-events.py, saa den findes i praksis altid.
        if not _sec_table_ready:
            _sec_table_ready = True
            ctx.waitUntil(db.prepare(_SEC_CREATE_SQL).run())

        stmts = [db.prepare(_SEC_INSERT_SQL).bind(bucket, kind, path, int(count))
                 for (kind, path), count in snapshot]
        if stmts:
            ctx.waitUntil(db.batch(to_js(stmts)))
    except Exception:
        # Logning må aldrig kunne vælte en request.
        try:
            _sec_counts.clear()
        except Exception:
            pass


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
    STAGING_ACCESS_SECRET: str


class Default(WSGI[Env]):
    app = flask_app

    def _staging_blocked(self, request):
        """Adgangsspærring på staging-workeren.

        madshopper-dev kører den samme kode mod *_dev-tabellerne, men på en
        offentlig workers.dev-URL uden nogen form for adgangskontrol - og mod
        SAMME Supabase-projekt og samme auth.users som produktionen. Var'en
        sættes kun i staging-bygget, så produktionen aldrig rammer denne sti.

        Returnerer et svar hvis requesten skal afvises, ellers None. 404 frem
        for 401: et 401 bekræfter at der ER noget bag, et 404 gør ikke.
        """
        try:
            secret = getattr(self.raw_env, "STAGING_ACCESS_SECRET", None)
        except Exception:
            secret = None
        if not secret:
            return None
        secret = str(secret)
        try:
            from urllib.parse import urlparse, parse_qs
            url = urlparse(str(request.url))
            # ?k=<secret> sætter en cookie, så resten af sessionen bare virker.
            if parse_qs(url.query or "").get("k", [""])[0] == secret:
                return EdgeResponse.text("", status=302, headers={
                    "Location": url.path or "/",
                    "Set-Cookie": (
                        f"ms_staging={secret}; Path=/; Max-Age=86400; "
                        "HttpOnly; Secure; SameSite=Lax"
                    ),
                    "Cache-Control": "no-store",
                })
            cookie = request.headers.get("Cookie") or ""
            if f"ms_staging={secret}" in cookie:
                return None
        except Exception:
            pass
        return EdgeResponse.text("Not found", status=404,
                                 headers={"Cache-Control": "no-store"})

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
        # Staging: afvis alt uden adgangsnøgle FØR der laves noget arbejde.
        blocked = self._staging_blocked(request)
        if blocked is not None:
            return blocked

        # Ikke-GET (POST mv.) er dyre/skrivende → rate limit før arbejde.
        if request.method != "GET":
            if not await self._rate_ok(request):
                _sec_note("rate_limit", request)
                _sec_flush(self.raw_env, self.ctx)
                return _too_many(request)
            response = await super().fetch(request)
            if int(getattr(response, "status", 200) or 200) >= 500:
                _sec_note("server_error", request)
            _sec_flush(self.raw_env, self.ctx)
            return response

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
            _sec_note("rate_limit", request)
            _sec_flush(self.raw_env, self.ctx)
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
        # Tælles efter cache-skrivningen, så en fejl i logningen aldrig kan
        # koste os cachen (og dermed kapaciteten).
        try:
            if int(getattr(response, "status", 200) or 200) >= 500:
                _sec_note("server_error", request)
            _sec_flush(self.raw_env, self.ctx)
        except Exception:
            pass
        return response
