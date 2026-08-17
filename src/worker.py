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


def _worker_crash_fallback(request=None) -> EdgeResponse:
    """Sidste sikkerhedsnet om super().fetch(). Uden dette ryger ENHVER ufanget
    undtagelse fra hele Flask/WSGI-stakken - kendt (D1/KV-bro-kollisionen,
    CPU-budget) eller endnu ukendt - urørt op til Cloudflare, som viser sin
    egen rå "error code: 1101/1102" i stedet for et brugervendt svar. Fanget
    2026-08-05: begge fetch()-stier manglede denne try/except, selvom en
    tidligere fix (D1-bro-spærre) allerede havde identificeret præcis dette
    hul uden at lukke det."""
    headers = {"Retry-After": "2", "Cache-Control": "no-store"}
    path = ""
    try:
        if request is not None:
            from urllib.parse import urlparse
            path = urlparse(str(request.url)).path
    except Exception:
        pass
    if path.startswith("/api/"):
        return EdgeResponse.json(
            {"success": False, "error": "MadShopper svarer ikke lige nu. Prøv igen om lidt."},
            status=503,
            headers=headers,
        )
    return EdgeResponse.text(
        '<!doctype html><html lang="da"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>MadShopper</title></head>'
        '<body style="font-family:system-ui,sans-serif;display:flex;'
        'min-height:100vh;align-items:center;justify-content:center;'
        'margin:0;background:#111;color:#eee;text-align:center;padding:1.5rem">'
        '<p>MadShopper svarer ikke lige nu.<br>Prøv at genindlæse siden om et '
        'øjeblik.</p></body></html>',
        status=503,
        headers={**headers, "content-type": "text/html; charset=utf-8"},
    )


# Cache-version caches pr. isolate i 5 min, så vi ikke rammer KV på hver request.
# _cache_ver_kv er den RÅ værdi fra KV, _cache_ver den sammensatte nøgle-del.
# De holdes adskilt, så en forbigående KV-læsefejl beholder den sidst kendte
# version i stedet for at falde til "0": et skifte frem og tilbage mellem to
# nøgler ville tømme cachen to gange for ingenting.
_cache_ver = None
_cache_ver_kv = None
_cache_ver_at = 0.0
_CACHE_VER_TTL = 300.0

# De ENESTE query-parametre app.py rent faktisk læser (verificeret mod hvert
# request.args.get(...)-kald i app.py). Bruges til at normalisere
# cache-nøglen i _cache_key nedenfor, så et ukendt parameter (sporing,
# vilkårlig cache-busting) ikke laver en ny, aldrig-genbrugt nøgle for en
# side der rendrer identisk uden det.
_CACHEABLE_QUERY_PARAMS = frozenset({
    "lactose", "max_price", "max_weight", "min_price", "min_weight",
    "organic", "page", "q", "sale", "sort", "stores", "subcategory",
})

# Single-flight for cache-miss renders pr. isolate. Uden dette renderer N
# samtidige requests til samme kolde URL N gange i parallel - præcis det
# mønster der giver Error 1101 efter deploy (cache_version-bump + CDN-purge)
# og efter nattens seed. Ventende requests awaiter leaderen, rematcher cachen
# og renderer kun selv hvis ventetiden løb tør.
#
# Værdi: (gate-promise, udløbstidspunkt i ms). Udløbet er nødvendigt, fordi en
# hård CPU-terminering ikke kører finally: uden det kunne en nøgle blive
# liggende med en promise der aldrig resolves, og URL'en ville være et sort
# hul i dette isolate resten af dets levetid.
_inflight_renders: dict = {}
# Antal gange en venter prøver "await gate → tjek cache" igen. Mere end én
# runde er hele pointen: fejler leaderen, overtager næste venter i stedet for
# at alle falder igennem til hver sin cold render på én gang.
_SINGLE_FLIGHT_ROUNDS = 3
# En render der ikke er færdig inden for det her, betragtes som død.
_SINGLE_FLIGHT_MAX_MS = 30_000.0

# Server-side opvarmningskø. Historikken: GitHub Actions-baseret opvarmning
# (Playwright mod https://madshopper.dk) har fejlet 100 % hver eneste nat
# siden mindst 2026-07-27 - Cloudflares GRATIS Bot Fight Mode blokerer ALT
# fra GitHub Actions-IP'er ubetinget, og kan (bekræftet mod Cloudflares egen
# dokumentation, se kommentaren ved "Roegtest af produktionen" i
# deploy-edge.yml) IKKE skippes af nogen WAF-regel. continue-on-error skjulte
# fejlen i over en uge og forklarer de tilbagevendende 1101-nedbrud
# (2026-07-19, -28, 2026-08-02/03).
#
# Løsningen kan derfor ikke være et bedre eksternt kald - GitHub Actions kan
# strukturelt aldrig nå produktion. I stedet varmer hver isolate sig selv:
# den FØRSTE rigtige (ikke-AJAX GET) besøgende der ser en ny cache_version
# udløser at ÉN endnu-uvarmet sti renderes i baggrunden via ctx.waitUntil
# (blokerer aldrig selve svaret). Næste besøgende tager den næste, osv.,
# indtil hele listen er dækket. self.fetch() på en selv-konstrueret Request
# er et rent Python-kald i samme isolate - der opstår intet nyt HTTP-kald
# mod Cloudflares edge, så Bot Fight Mode er slet ikke i spil.
#
# Bevidst kun ÉN sti pr. rigtig request (ikke alle 14 i ét baggrundskald):
# at rendere 14 sider i træk i én invocation risikerer selv at sprænge
# CPU-budgettet - præcis den fejlklasse dette skal forhindre.
_WARM_PATHS = (
    "/", "/ugens_tilbud", "/Mejeri", "/Koed_og_fisk", "/Frugt_og_groent",
    "/Broed_og_kager", "/Kolonial", "/Frost", "/Drikkevarer", "/Slik",
    "/about", "/feedback", "/terms-of-service", "/privatliv",
)
_warm_version = None
_warm_queue: list = []
_warm_busy = False


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
    STAGING_ACCESS_EMAIL: str
    STAGING_ACCESS_PASSWORD: str


# Eneste sti hvor en uautentificeret besøgende ser andet end blankt 404 -
# resten af _staging_blocked() nedenfor er uændret på det punkt.
_STAGING_LOGIN_PATH = "/staging-login"


def _staging_session_token(secret: str) -> str:
    """Afledt, uigennemsigtig sessionsværdi til ms_staging-cookien - ALDRIG
    den rå delte hemmelighed selv. En lækket cookie afslører dermed ikke
    ?k=-nøglen, og cookien kan i praksis ikke bruges til at genudlede
    secret'et (HMAC, ikke reversibel). Kan endnu ikke tilbagekaldes uden at
    rotere secret'et for alle (kræver server-side sessionslager - ude af
    scope), men den rå hemmelighed forlader i det mindste aldrig serveren."""
    import hashlib
    import hmac as _hmac
    return _hmac.new(secret.encode(), b"staging-session", hashlib.sha256).hexdigest()


def _cookie_value(cookie_header: str, name: str) -> str:
    """Henter værdien af én navngiven cookie fra en rå Cookie-header."""
    for part in (cookie_header or "").split(";"):
        part = part.strip()
        if part.startswith(name + "="):
            return part[len(name) + 1:]
    return ""


def _staging_login_page(error: str | None = None) -> str:
    error_html = (
        f'<p class="err">{error}</p>' if error else ""
    )
    return f"""<!doctype html>
<html lang="da"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>MadShopper staging</title>
<style>
body{{font-family:system-ui,sans-serif;background:#111;color:#eee;display:flex;
  min-height:100vh;align-items:center;justify-content:center;margin:0}}
form{{background:#1b1b1b;padding:2rem;border-radius:8px;width:min(320px,90vw)}}
h1{{font-size:1.1rem;margin:0 0 1.2rem}}
label{{display:block;font-size:.85rem;margin:.8rem 0 .3rem;color:#aaa}}
input{{width:100%;box-sizing:border-box;padding:.6rem;border-radius:4px;
  border:1px solid #333;background:#0d0d0d;color:#eee;font-size:1rem}}
button{{margin-top:1.2rem;width:100%;padding:.6rem;border:0;border-radius:4px;
  background:#10b981;color:#fff;font-size:1rem;cursor:pointer}}
.err{{color:#f87171;font-size:.85rem;margin:.8rem 0 0}}
</style></head><body>
<form method="POST" action="{_STAGING_LOGIN_PATH}">
<h1>MadShopper staging</h1>
<label for="email">Mail</label>
<input type="email" name="email" id="email" required autofocus>
<label for="password">Adgangskode</label>
<input type="password" name="password" id="password" required>
<button type="submit">Log ind</button>
{error_html}
</form></body></html>"""


class Default(WSGI[Env]):
    app = flask_app

    def _staging_cookie_response(self, secret: str, redirect_path: str) -> EdgeResponse:
        # Cookien bærer den AFLEDTE sessionstoken, ikke selve secret'et - se
        # _staging_session_token() ovenfor.
        token = _staging_session_token(secret)
        return EdgeResponse.text("", status=302, headers={
            "Location": redirect_path or "/",
            "Set-Cookie": (
                f"ms_staging={token}; Path=/; Max-Age=86400; "
                "HttpOnly; Secure; SameSite=Lax"
            ),
            "Cache-Control": "no-store",
        })

    async def _staging_blocked(self, request):
        """Adgangsspærring på staging-workeren.

        madshopper-dev kører den samme kode mod *_dev-tabellerne, men på en
        offentlig custom domain uden nogen form for adgangskontrol - og mod
        SAMME Supabase-projekt og samme auth.users som produktionen. Var'en
        sættes kun i staging-bygget, så produktionen aldrig rammer denne sti.

        Returnerer et svar hvis requesten skal afvises, ellers None. 404 frem
        for 401 på alt UNDTAGEN login-siden: et 401 bekræfter at der ER noget
        bag, et 404 gør ikke. Login-siden på _STAGING_LOGIN_PATH er den ene
        bevidste undtagelse - en menneskelig bruger skal kunne finde et
        mail+adgangskode-login uden at kende en hex-nøgle udenad.
        """
        try:
            secret = getattr(self.raw_env, "STAGING_ACCESS_SECRET", None)
        except Exception:
            secret = None
        if not secret:
            # Korrekt for produktion, som ALDRIG sætter secret'et - denne
            # gren rammes derfor på hver eneste produktionsrequest, og må
            # IKKE logge der (se _sec_note-kommentaren om aggregeret,
            # lav-volumen logning og nedbruddet 2026-07-19). EMAIL/PASSWORD
            # sættes af build-pages.sh KUN når DEPLOY_ENV=staging, samtidig
            # med SECRET - så "email/password sat, men secret mangler" kan
            # kun ske i en fejlkonfigureret staging, aldrig i produktion.
            # Det gør signalet billigt at skelne uden per-request-logning.
            try:
                email = getattr(self.raw_env, "STAGING_ACCESS_EMAIL", None)
                password = getattr(self.raw_env, "STAGING_ACCESS_PASSWORD", None)
                if email or password:
                    _sec_note("staging_gate_unconfigured", request)
                    _sec_flush(self.raw_env, self.ctx)
            except Exception:
                pass
            return None
        secret = str(secret)
        try:
            import hmac
            from urllib.parse import urlparse, parse_qs
            url = urlparse(str(request.url))
            path = url.path or "/"

            # ?k=<secret> sætter en cookie, så resten af sessionen bare
            # virker. Bruges af CI's warmup/røgtest (se deploy-edge-dev.yml).
            got_key = parse_qs(url.query or "").get("k", [""])[0]
            if got_key and hmac.compare_digest(got_key.encode(), secret.encode()):
                return self._staging_cookie_response(secret, path)

            cookie = request.headers.get("Cookie") or ""
            got_token = _cookie_value(cookie, "ms_staging")
            if got_token and hmac.compare_digest(
                got_token.encode(), _staging_session_token(secret).encode()
            ):
                return None

            if path == _STAGING_LOGIN_PATH:
                email = getattr(self.raw_env, "STAGING_ACCESS_EMAIL", None)
                password = getattr(self.raw_env, "STAGING_ACCESS_PASSWORD", None)
                if not email or not password:
                    return EdgeResponse.text("Not found", status=404,
                                             headers={"Cache-Control": "no-store"})
                error = None
                if request.method == "POST":
                    # Login-forsøg var tidligere helt uden rate limiting -
                    # _rate_ok kaldes ellers kun senere i fetch() for andre
                    # requesttyper. Genbruger samme (fail-open) limiter;
                    # _rate_ok understøtter ikke en separat nøgle/bucket pr.
                    # formål i dag, så dette deler bucket med den generelle
                    # rate limit - en fremtidig udvidelse kunne give den sin
                    # egen "staging_login:<ip>"-nøgle.
                    if not await self._rate_ok(request):
                        _sec_note("rate_limit", request)
                        _sec_flush(self.raw_env, self.ctx)
                        return _too_many(request)
                    try:
                        body = await request.text()
                    except Exception:
                        body = ""
                    form = parse_qs(body or "")
                    got_email = form.get("email", [""])[0]
                    got_password = form.get("password", [""])[0]
                    if got_email and got_password and hmac.compare_digest(
                        got_email.encode(), str(email).encode()
                    ) and hmac.compare_digest(
                        got_password.encode(), str(password).encode()
                    ):
                        return self._staging_cookie_response(secret, "/")
                    error = "Forkert mail eller adgangskode."
                    _sec_note("staging_login_fail", request)
                    _sec_flush(self.raw_env, self.ctx)
                return EdgeResponse.text(
                    _staging_login_page(error), status=200,
                    headers={"content-type": "text/html; charset=utf-8",
                             "Cache-Control": "no-store"},
                )

            # Ingen gyldig ?k=, ingen gyldig cookie, og ikke login-siden -
            # requesten afvises. Eneste sti hvor gate'en reelt lukker nogen
            # ude, så det er her angrebsforsøg mod staging bliver synlige.
            _sec_note("staging_gate_denied", request)
            _sec_flush(self.raw_env, self.ctx)
            return EdgeResponse.text("Not found", status=404,
                                     headers={"Cache-Control": "no-store"})
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
                # Bindingen mangler helt - burde ikke ske (RATE_LIMITER er
                # konfigureret ubetinget i begge miljøer i build-pages.sh),
                # så det er en reel fejltilstand værd at se. Fejler stadig
                # åbent (return True) - kun synligheden er ny.
                _sec_note("rate_limiter_unavailable", request)
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
            # Samme fail-open-design som ovenfor - kun tilføjer et
            # aggregeret logningssignal, ændrer ikke adfærden.
            _sec_note("rate_limiter_unavailable", request)
            return True

    def _utc_day(self) -> str:
        """Dagens dato (UTC) som YYYYMMDD. Bruges som nødbremse i cache-nøglen."""
        try:
            from js import Date
            return str(Date.new().toISOString())[:10].replace("-", "")
        except Exception:
            return "0"

    async def _cache_version(self) -> str:
        """Aktuel cache-version fra KV (sat af daglig seed). Cachet pr. isolate.

        Dagens UTC-dato indgår SAMMEN med KV-versionen, fordi
        set_cache_version() i scripts/seed-d1.py fejler blødt (den printer en
        advarsel og lader jobbet lykkes). Med den gamle 10-minutters TTL var det
        harmløst. Med 24 timers TTL ville et fejlet bump betyde et helt døgn med
        gårsdagens priser i edge-cachen. Dato-komponenten ruller nøglen over ved
        midnat UTC uanset KV, så staleness aldrig kan overskride dataens egen
        daglige kadence - også hvis KV-skrivningen eller KV-læsningen svigter.
        """
        global _cache_ver, _cache_ver_kv, _cache_ver_at
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
            if val:
                _cache_ver_kv = str(val)
        except Exception:
            pass                       # behold sidst kendte version
        _cache_ver = f"{_cache_ver_kv or '0'}-{self._utc_day()}"
        _cache_ver_at = now
        return _cache_ver

    async def _cache_key(self, request):
        """Versioneret cache-nøgle (JS Request). Når cache_version ændres ved
        daglig seed, misser alle gamle nøgler → friske priser med det samme.

        Query-strengen normaliseres til KUN de parametre app.py rent faktisk
        læser (se _CACHEABLE_QUERY_PARAMS), sorteret. Uden det var hele
        URL'en - inkl. ubrugte parametre som ?utm_source=... - en del af
        nøglen: en angriber kunne tvinge ubegrænset mange garanterede
        cold renders med ?a=1, ?a=2, ..., og et delt link med et
        sporingsparameter ramte altid koldt selv om den rendrede side var
        identisk med den uden."""
        from js import Request as JSRequest
        from urllib.parse import urlparse, parse_qsl, urlencode
        ver = await self._cache_version()
        parsed = urlparse(str(request.url))
        kept = sorted(
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k in _CACHEABLE_QUERY_PARAMS
        )
        query = urlencode(kept)
        base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        normalized = f"{base}?{query}" if query else base
        sep = "&" if query else "?"
        return JSRequest.new(f"{normalized}{sep}__cv={ver}")

    def _queue_background_warm(self, request, ver: str) -> None:
        """Se modulkommentar ved _WARM_PATHS. Lægger højst én baggrunds-
        opvarmning i ctx.waitUntil - kaldes på hver rigtig GET, er en no-op
        (to hurtige sammenligninger) når køen allerede er tom eller en anden
        opvarmning er i gang."""
        global _warm_version, _warm_queue, _warm_busy
        try:
            # Kun produktion. Staging har sit eget (mindre kritiske) CI-
            # opvarmningsforsøg og deler ikke denne kode-sti.
            if getattr(self.raw_env, "STAGING_ACCESS_SECRET", None):
                return
            if ver != _warm_version:
                _warm_version = ver
                _warm_queue = list(_WARM_PATHS)
            if _warm_busy or not _warm_queue:
                return
            path = _warm_queue.pop(0)
            from urllib.parse import urlparse
            parsed = urlparse(str(request.url))
            if path == (parsed.path or "/"):
                # Denne besøgende varmer selv præcis den sti via den normale
                # cache-skrivning nedenfor - spar en overflødig rendering.
                return
            origin = f"{parsed.scheme}://{parsed.netloc}"
            _warm_busy = True
            try:
                self.ctx.waitUntil(self._run_background_warm(origin, path))
            except Exception:
                # Kastede waitUntil, kørte _run_background_warm aldrig, og
                # dermed heller ikke dens finally. Flaget ville så stå på True
                # for evigt, og opvarmningen var tavst død resten af isolatets
                # levetid. Læg samtidig stien tilbage i køen - ellers taber vi
                # den uigenkaldeligt.
                _warm_busy = False
                _warm_queue.insert(0, path)
                raise
        except Exception:
            pass

    async def _run_background_warm(self, origin: str, path: str) -> None:
        global _warm_busy
        try:
            from js import Request as JSRequest
            warm_request = JSRequest.new(f"{origin}{path}")
            await self.fetch(warm_request)
        except Exception:
            pass
        finally:
            _warm_busy = False

    async def fetch(self, request):
        # Staging: afvis alt uden adgangsnøgle FØR der laves noget arbejde.
        blocked = await self._staging_blocked(request)
        if blocked is not None:
            return blocked

        # Ikke-GET (POST mv.) er dyre/skrivende → rate limit før arbejde.
        # HEAD skal FOELGE GET-vejen. Foer faldt den i non-GET-grenen: ingen
        # cache-laesning, ingen cache-skrivning og ingen single-flight, saa
        # hver eneste HEAD (link-previews, crawlere, uptime-botter) kostede en
        # fuld rendering hvor werkzeug bygger hele siden og smider body'en vaek.
        if request.method not in ("GET", "HEAD"):
            if not await self._rate_ok(request):
                _sec_note("rate_limit", request)
                _sec_flush(self.raw_env, self.ctx)
                return _too_many(request)
            try:
                response = await super().fetch(request)
            except Exception:
                _sec_note("server_error", request)
                _sec_flush(self.raw_env, self.ctx)
                return _worker_crash_fallback(request)
            if int(getattr(response, "status", 200) or 200) >= 500:
                _sec_note("server_error", request)
            if response.headers.get("X-Data-Degraded"):
                _sec_note("degraded", request)
            _sec_flush(self.raw_env, self.ctx)
            return response

        # AJAX-kald (X-Requested-With) rammer de samme URL'er som en normal
        # sidevisning, men Flask returnerer et HTML-fragment uden <head>/CSS
        # (se X-Requested-With-check i app.py). Cache-nøglen varierer ikke
        # efter denne header, så et cachet fragment ville blive serveret som
        # hele siden til almindelige besøgende (forsiden mistede CSS herved).
        # Undgå det ved slet ikke at læse/skrive edge-cache for AJAX-kald.
        #
        # VIGTIGT: Valider IKKE cache-hits ved at læse body'en. En tidligere
        # _cache_hit_ok() parsede hele HTML-svaret (~135 KB) på hvert Cache
        # API-hit - det alene sprængte Python Workers' CPU-budget og gav
        # Error 1101 under helt almindelig trafik (målt 2026-07-25: ~halvdelen
        # af worker-invocations på / fejlede, mens CDN-HIT var fine). AJAX er
        # allerede udelukket her, så body-scan er overflødig.
        is_ajax = (request.headers.get("X-Requested-With") or "") == "XMLHttpRequest"

        # Se modulkommentar ved _WARM_PATHS. Udløser i baggrunden (blokerer
        # aldrig dette svar) højst én opvarmning af en anden central side,
        # hvis der er tilbage i køen for den aktuelle cache_version.
        if not is_ajax:
            try:
                self._queue_background_warm(request, await self._cache_version())
            except Exception:
                pass

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
                if hit is not None:
                    return hit
            except Exception:
                cache = None

        # Single-flight: hvis en anden request i dette isolate allerede
        # renderer samme cache-nøgle, vent og prøv cachen igen i stedet for
        # at starte endnu en dyr cold render.
        #
        # Vi venter i FLERE runder. Før ventede vi kun én gang: fejlede
        # leaderen (CPU-kill, 429, crash), var cachen stadig tom, og alle
        # ventere faldt videre til hver sin cold render - kun den første af
        # dem oprettede en ny gate, resten så den ligge der og rendrede helt
        # uden. Det er nøjagtig den stampede single-flight skulle forhindre,
        # og den rammer hårdest lige efter et versionsbump, hvor alt er koldt.
        flight_key = None
        if key_req is not None:
            try:
                flight_key = str(key_req.url)
            except Exception:
                flight_key = None
        elif is_ajax:
            # AJAX-fragmenter deler ALDRIG Cache API (se kommentaren ovenfor -
            # de mangler <head> og ville lække som hele siden til almindelige
            # besøgende), men uden en flight_key her sprang N samtidige
            # identiske fragment-requests HVER for sig ind i deres egen fulde
            # cold render - en ukoordineret variant af netop det
            # samtidigheds-mønster single-flight ellers forhindrer for
            # normale sidevisninger, og billigste kendte vej til at
            # fremkalde 1101 (kræver kun én header, ingen cache-læsning).
            # "|ajax" adskiller nøglen fra den ikke-AJAX cache-nøgle for
            # samme URL, så de to aldrig deler en gate ved en fejl.
            try:
                ajax_key = await self._cache_key(request)
                flight_key = f"{ajax_key.url}|ajax"
            except Exception:
                flight_key = None

        if flight_key:
            for _ in range(_SINGLE_FLIGHT_ROUNDS):
                entry = _inflight_renders.get(flight_key)
                if entry is None:
                    break
                pending, deadline = entry
                if _now_ms() > deadline:
                    # Forældet gate. En hård CPU-terminering kører ikke
                    # finally, så nøglen kan blive liggende med en promise der
                    # aldrig resolves - uden det her ville URL'en være et sort
                    # hul i dette isolate resten af dets levetid.
                    _inflight_renders.pop(flight_key, None)
                    break
                try:
                    await pending
                except Exception:
                    pass
                if cache is not None and key_req is not None:
                    try:
                        hit = await cache.match(key_req)
                        if hit is not None:
                            return hit
                    except Exception:
                        pass

        gate = None
        gate_resolve = None
        if flight_key and flight_key not in _inflight_renders:
            try:
                from js import Promise

                holder: list = []

                def _executor(resolve, _reject):
                    holder.append(resolve)

                gate = Promise.new(_executor)
                gate_resolve = holder[0] if holder else None
                _inflight_renders[flight_key] = (gate, _now_ms() + _SINGLE_FLIGHT_MAX_MS)
            except Exception:
                gate = None
                gate_resolve = None

        # Rate limit KUN cache-miss-stien (cache-hits returnerede allerede
        # ovenfor og rammer aldrig her). _rate_ok er et enkelt async I/O-kald
        # til Cloudflares binding og lægger ikke CPU-tid på selve renderingen
        # - men den forhindrer at mange samtidige cold-cache-renders sender
        # worker'en over 10 ms-grænsen ad gangen (det var præcis mønstret der
        # væltede produktionen 2026-07-19, se cloudflare-incident-2026-07-19).
        try:
            if not await self._rate_ok(request):
                _sec_note("rate_limit", request)
                _sec_flush(self.raw_env, self.ctx)
                return _too_many(request)
            try:
                response = await super().fetch(request)
            except Exception:
                _sec_note("server_error", request)
                _sec_flush(self.raw_env, self.ctx)
                return _worker_crash_fallback(request)
            try:
                if cache is not None and key_req is not None:
                    # Edge Cache API: cache når CDN-header (eller legacy
                    # Cache-Control) siger public. HTML sendes til browseren
                    # som no-store, så deploy'ede ?v=-assets slår igennem
                    # uden hard refresh - mens Worker stadig serverer fra
                    # Cache API.
                    cc = response.headers.get("Cache-Control") or ""
                    cdn_cc = (
                        response.headers.get("CDN-Cache-Control")
                        or response.headers.get("Cloudflare-CDN-Cache-Control")
                        or ""
                    )
                    eligible = (
                        ("public" in cdn_cc and "no-store" not in cdn_cc)
                        or ("public" in cc and "no-store" not in cc)
                    )
                    if eligible:
                        # Await put FØR single-flight slippes løs, så ventende
                        # requests rammer cachen i stedet for at rendere om.
                        try:
                            await cache.put(key_req, response.clone())
                        except Exception:
                            self.ctx.waitUntil(cache.put(key_req, response.clone()))
            except Exception:
                pass
            # Tælles efter cache-skrivningen, så en fejl i logningen aldrig kan
            # koste os cachen (og dermed kapaciteten). Degraderede svar (se
            # app.py::_set_response_headers) er allerede udelukket fra
            # 'eligible' ovenfor (X-Data-Degraded medfører no-store), men er
            # ellers usynlige for al overvågning - status er stadig 200, og
            # uptime-tjekket ser stadig "MadShopper" i title/logo.
            try:
                if int(getattr(response, "status", 200) or 200) >= 500:
                    _sec_note("server_error", request)
                if response.headers.get("X-Data-Degraded"):
                    _sec_note("degraded", request)
                _sec_flush(self.raw_env, self.ctx)
            except Exception:
                pass
            return response
        finally:
            if flight_key:
                entry = _inflight_renders.get(flight_key)
                # Kun vores egen gate må fjernes - en anden request kan have
                # overtaget nøglen, hvis vores blev betragtet som forældet.
                if entry is not None and entry[0] is gate:
                    _inflight_renders.pop(flight_key, None)
            if gate_resolve is not None:
                try:
                    gate_resolve(None)
                except Exception:
                    pass
