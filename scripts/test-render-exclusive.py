"""Regressionstest af render-låsen i src/worker.py (_render_exclusive).

Koeres med: python3 scripts/test-render-exclusive.py

Baggrund: edgekit's WSGI.fetch koerer hele Flask-renderingen synkront, og hvert
D1/KV-kald suspenderer via run_sync. Naar en ANDEN request i samme isolate naar
ind i Flask imens, fejler dens D1-kald blødt (app.py: _sync_bridge_busy), og
svaret bliver en tom liste med X-Data-Degraded. Maalt 14-09-2026 mod
produktion: 11 af 20 samtidige soegninger gav 0 varer.

Testen modellerer broen praecis som app.py goer (et isolate-globalt optaget-
flag, der saettes mens en render "venter paa D1") og beviser at:
  1) samtidige renders aldrig overlapper - ingen degraderede svar,
  2) koeen er FIFO,
  3) en exception i renderingen frigiver laasen,
  4) naar ventelofte naas mens forgaengeren STADIG renderer, svares "travlt"
     i stedet for at gaa ind i broen (maalt 15-09-2026: 1102 paa fire requests
     samtidig ved ~11,5 s), og den bag en opgivet ventende slipper ikke ind
     foer tid,
  5) en render der aldrig bliver faerdig (CPU-drab koerer ikke finally) kun
     blokerer til _RENDER_DEAD_MS - isolatet haenger ikke for altid,
  6) koe-loftet (shed=True) svarer "travlt" ud over _RENDER_QUEUE_MAX, og en
     ventende der afbrydes kan ikke laase taelleren,
  7) "travlt"-svaret er JSON til API/AJAX og en selv-genindlaesende side med
     loft til almindelige sidevisninger,
  8) fejler selve laasen (fx js-importen), renderes der alligevel,
  9) INVARIANT: super().fetch() kaldes KUN inde i _render_exclusive, saa en
     ny kodesti ikke stille kan springe laasen over.

Workers-runtime findes ikke uden for Cloudflare, saa js, pyodide, edgekit og
app stubbes med asyncio - testen maaler vores egen logik, ikke platformens."""
import ast, asyncio, os, sys, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- stub runtime-moduler (JS-promises modelleret med asyncio) -------------
class _Promise:
    def __init__(self, fut):
        self.fut = fut

    def __await__(self):
        return self.fut.__await__()

    @staticmethod
    def new(executor):
        fut = asyncio.get_running_loop().create_future()

        def resolve(value=None):
            if not fut.done():
                fut.set_result(value)

        def reject(err=None):
            if not fut.done():
                fut.set_exception(RuntimeError(err))

        executor(resolve, reject)
        return _Promise(fut)

    @staticmethod
    def race(items):
        out = asyncio.get_running_loop().create_future()
        for p in items:
            def _done(f, out=out):
                if not out.done():
                    out.set_result(None if f.exception() else f.result())
            p.fut.add_done_callback(_done)
        return _Promise(out)

    def then(self, cb):
        self.fut.add_done_callback(lambda f: cb(None if f.exception() else f.result()))
        return self


def _set_timeout(fn, ms, *args):
    asyncio.get_running_loop().call_later(ms / 1000, fn, *args)


class _Date:
    @staticmethod
    def now():
        import time
        return time.monotonic() * 1000.0


js = types.ModuleType("js")
js.Promise = _Promise
js.setTimeout = _set_timeout
js.Date = _Date
sys.modules["js"] = js

pyo = types.ModuleType("pyodide"); ffi = types.ModuleType("pyodide.ffi")
ffi.to_js = lambda x, **kw: x
pyo.ffi = ffi
sys.modules["pyodide"] = pyo; sys.modules["pyodide.ffi"] = ffi

for name in ["edgekit", "edgekit.adapters", "edgekit.bindings", "edgekit.webapi",
             "edgekit.webapi.response", "edgekit.runtime", "app"]:
    sys.modules[name] = types.ModuleType(name)


# Broen som app.py modellerer den: ét optaget-flag pr. isolate. En render der
# rammer et optaget flag fejler blødt med et degraderet (tomt) svar.
BRIDGE = {"busy": False, "log": [], "degraded": 0, "active": 0, "max_active": 0}


class _WSGI:
    def __class_getitem__(cls, item):
        return cls

    async def fetch(self, request):
        BRIDGE["active"] += 1
        BRIDGE["max_active"] = max(BRIDGE["max_active"], BRIDGE["active"])
        try:
            BRIDGE["log"].append(("start", request.url))
            if request.url.endswith("/boom"):
                raise RuntimeError("render fejlede")
            if request.url.endswith("/haenger"):
                # CPU-drab: renderingen bliver aldrig faerdig og finally i
                # _render_exclusive koerer aldrig.
                await asyncio.Event().wait()
            if "/langsom" in request.url:
                await asyncio.sleep(0.3)  # lang, men levende render
            for _ in range(3):  # tre D1-kald pr. render
                if BRIDGE["busy"]:
                    BRIDGE["degraded"] += 1
                    return ("degraded", request.url)
                BRIDGE["busy"] = True
                try:
                    await asyncio.sleep(0.01)  # run_sync-suspension
                finally:
                    BRIDGE["busy"] = False
            BRIDGE["log"].append(("slut", request.url))
            return ("ok", request.url)
        finally:
            BRIDGE["active"] -= 1


sys.modules["edgekit.adapters"].WSGI = _WSGI
sys.modules["edgekit.bindings"].KVNamespace = object
sys.modules["edgekit.bindings"].D1Database = object
sys.modules["edgekit.webapi.response"].Response = types.SimpleNamespace(
    json=lambda *a, **k: ("json", a, k), text=lambda *a, **k: ("text", a, k))
sys.modules["app"].app = object()

sys.path.insert(0, os.path.join(ROOT, "src"))
import worker as W

fails = []


def check(label, cond):
    print(("  OK   " if cond else "  FEJL ") + label)
    if not cond:
        fails.append(label)


def req(path):
    return types.SimpleNamespace(url=f"https://madshopper.dk{path}", headers={})


def reset():
    W._render_tail = None
    W._render_active = None
    W._render_waiting.clear()
    BRIDGE.update(busy=False, log=[], degraded=0, active=0, max_active=0)


def is_busy(r):
    """_busy_response gennem stubbens EdgeResponse: ("json"|"text", args, kwargs)."""
    return (isinstance(r, tuple) and r and r[0] in ("json", "text")
            and (r[2].get("headers") or {}).get(W._BUSY_HEADER) == "1")


worker = W.Default()


# --- 0) kontrol: UDEN laasen kolliderer samtidige renders ------------------
# Beviser at testens bro-model rent faktisk kan fremkalde fejlen - ellers
# ville test 1 bestaa uanset om laasen virkede.
async def _uden_laas():
    reset()
    return await asyncio.gather(*(_WSGI.fetch(worker, req(f"/search?q={i}")) for i in range(10)))

res = asyncio.run(_uden_laas())
check(f"kontrol: uden laas degraderes samtidige renders ({BRIDGE['degraded']} af 10)",
      BRIDGE["degraded"] > 0)


# --- 1) med laasen: ingen overlap, ingen degraderede svar ------------------
async def _med_laas():
    reset()
    return await asyncio.gather(*(worker._render_exclusive(req(f"/search?q={i}")) for i in range(10)))

res = asyncio.run(_med_laas())
check(f"ingen degraderede svar (fik {BRIDGE['degraded']})", BRIDGE["degraded"] == 0)
check(f"alle 10 renders lykkedes ({sum(1 for r in res if r[0] == 'ok')})",
      all(r[0] == "ok" for r in res))
check(f"hoejst én render i Flask ad gangen (max {BRIDGE['max_active']})", BRIDGE["max_active"] == 1)
check("laasen er frigivet bagefter", W._render_tail is None)

# --- 2) FIFO ---------------------------------------------------------------
starts = [u for (kind, u) in BRIDGE["log"] if kind == "start"]
check("koeen er FIFO", starts == [f"https://madshopper.dk/search?q={i}" for i in range(10)])


# --- 3) exception frigiver laasen ------------------------------------------
async def _exception():
    reset()
    async def boom():
        try:
            await worker._render_exclusive(req("/boom"))
        except RuntimeError:
            return "kastet"
    return await asyncio.wait_for(
        asyncio.gather(boom(), worker._render_exclusive(req("/efter"))), timeout=2)

try:
    r = asyncio.run(_exception())
    check("exception propageres til fetch()'s crash-fallback", r[0] == "kastet")
    check("naeste render koerer efter en exception", r[1] == ("ok", "https://madshopper.dk/efter"))
    check("laasen er frigivet efter exception", W._render_tail is None)
except asyncio.TimeoutError:
    check("exception frigiver laasen (haengte!)", False)


# --- 4) timeout mens forgaengeren LEVER: "travlt", aldrig kollision --------
# Foer gik den ventende ind i Flask efter loftet, selv om forgaengeren stadig
# renderede - maalt 15-09-2026 som 1102 paa fire requests samtidig ved ~11,5 s.
old_max, old_dead = W._RENDER_WAIT_MAX_MS, W._RENDER_DEAD_MS


async def _levende_forgaenger():
    reset()
    # a renderer ~330 ms. b og c naar deres loft (150 ms) mens a lever og
    # giver op. d ankommer sent nok til at a bliver faerdig inden for d's
    # eget loft - og maa foerst starte, naar a er ude, selv om b og c (som d
    # venter bag) har givet op laenge foer.
    W._RENDER_WAIT_MAX_MS, W._RENDER_DEAD_MS = 150, 5_000
    a = asyncio.ensure_future(worker._render_exclusive(req("/langsom?a")))
    await asyncio.sleep(0.01)
    b = asyncio.ensure_future(worker._render_exclusive(req("/search?b")))    # loft ved ~160 ms
    await asyncio.sleep(0.03)
    c = asyncio.ensure_future(worker._render_exclusive(req("/search?c")))    # loft ved ~190 ms
    await asyncio.sleep(0.22)
    d = asyncio.ensure_future(worker._render_exclusive(req("/search?d")))    # ~260 ms, venter paa c->b->a
    return await asyncio.wait_for(asyncio.gather(a, b, c, d), timeout=3)

try:
    ra, rb, rc, rd = asyncio.run(_levende_forgaenger())
    check("forgaengeren renderer faerdig", ra == ("ok", "https://madshopper.dk/langsom?a"))
    check("ventende der naar loftet mens forgaengeren lever, faar 'travlt'", is_busy(rb) and is_busy(rc))
    check(f"den bag de opgivne ventende renderer (fik {rd!r:.60})", rd == ("ok", "https://madshopper.dk/search?d"))
    _log = BRIDGE["log"]
    check("... og starter foerst efter forgaengeren er ude",
          ("slut", "https://madshopper.dk/langsom?a") in _log
          and _log.index(("slut", "https://madshopper.dk/langsom?a"))
          < _log.index(("start", "https://madshopper.dk/search?d")))
    check(f"aldrig to i Flask samtidig (max {BRIDGE['max_active']}, degraderet {BRIDGE['degraded']})",
          BRIDGE["max_active"] == 1 and BRIDGE["degraded"] == 0)
except asyncio.TimeoutError:
    check("levende forgaenger: haengte!", False)
finally:
    W._RENDER_WAIT_MAX_MS, W._RENDER_DEAD_MS = old_max, old_dead


# --- 5) en render der aldrig bliver faerdig blokerer kun til den er erklaeret doed
async def _haenger():
    reset()
    W._RENDER_WAIT_MAX_MS, W._RENDER_DEAD_MS = 200, 300
    stuck = asyncio.ensure_future(worker._render_exclusive(req("/haenger")))
    await asyncio.sleep(0.01)
    tidlig = await asyncio.wait_for(worker._render_exclusive(req("/tidlig")), timeout=3)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    r = await asyncio.wait_for(worker._render_exclusive(req("/naeste")), timeout=3)
    waited = loop.time() - t0
    stuck.cancel()
    return tidlig, r, waited

try:
    tidlig, r, waited = asyncio.run(_haenger())
    check("foer doeds-graensen: 'travlt' i stedet for kollision", is_busy(tidlig))
    check("efter doeds-graensen koerer naeste render", r == ("ok", "https://madshopper.dk/naeste"))
    check(f"isolatet haenger ikke for altid ({waited*1000:.0f} ms)", waited < 1.0)
except asyncio.TimeoutError:
    check("haengende render blokerer ikke isolatet for altid (haengte!)", False)
finally:
    W._RENDER_WAIT_MAX_MS, W._RENDER_DEAD_MS = old_max, old_dead


# --- 6) koe-loft: ud over _RENDER_QUEUE_MAX svares "travlt" med det samme ----
async def _koe_loft():
    reset()
    return await asyncio.gather(*(worker._render_exclusive(req(f"/search?q={i}"), shed=True)
                                  for i in range(10)))

res = asyncio.run(_koe_loft())
ok_n = sum(1 for r in res if isinstance(r, tuple) and r[0] == "ok")
busy_n = sum(1 for r in res if is_busy(r))
check(f"koe-loft: 1 holder + {W._RENDER_QUEUE_MAX} ventende renderes ({ok_n}), resten 'travlt' ({busy_n})",
      ok_n == 1 + W._RENDER_QUEUE_MAX and busy_n == 10 - ok_n)
check(f"koe-loft: aldrig overlap eller degraderede svar (max {BRIDGE['max_active']}, {BRIDGE['degraded']})",
      BRIDGE["max_active"] == 1 and BRIDGE["degraded"] == 0)
check("koe-loft: ventetaelleren er tom bagefter", not W._render_waiting)
W._render_waiting["glemt"] = W._now_ms() - (W._RENDER_WAIT_MAX_MS + 10_000)
W._prune_render_waiting(W._now_ms())
check("afbrudte ventende ryddes op (taelleren kan ikke laase isolatet)", not W._render_waiting)


# --- 7) "travlt"-svarets form --------------------------------------------------
_j = W._busy_response(req("/api/search?q=x"))
check("travlt til API: JSON 503 med Retry-After", _j[0] == "json" and _j[2]["status"] == 503
      and _j[2]["headers"]["Retry-After"] == str(W._BUSY_RETRY_SECONDS))
_h = W._busy_response(types.SimpleNamespace(url="https://madshopper.dk/Mejeri?page=2", headers={}))
check("travlt til side: HTML der genindlaeser med taeller", _h[0] == "text" and "refresh" in _h[1][0]
      and "_travlt=1" in _h[1][0] and "page=2" in _h[1][0])
_h = W._busy_response(types.SimpleNamespace(
    url=f"https://madshopper.dk/Mejeri?_travlt={W._BUSY_PAGE_MAX_RETRIES}", headers={}))
check("travlt til side: stopper genindlaesningen efter loftet", "refresh" not in _h[1][0])


# --- 8) CPU-budget pr. isolate (token-bucket) ---------------------------------
# Sekventielle kolde renders (~1/s) draebte isolaten efter 27-43 renders
# 15-09-2026 (1102 + 1101-kaskade); 6 i minuttet gik fint.
W._cpu_budget, W._cpu_budget_at = W._CPU_BUDGET_CAPACITY, 0.0
t = 1_000_000.0
n_ok = 0
while W._cpu_budget_take(W._CPU_COST_DEFAULT, t) == 0.0:
    n_ok += 1
    t += 1000.0  # én kold side i sekundet, som auditten
    if n_ok > 500:
        break
check(f"budget: en kold side i sekundet bremses efter {n_ok} renders (foer draebet ved 27-43)",
      10 <= n_ok < 27)
wait = W._cpu_budget_take(W._CPU_COST_DEFAULT, t)
check(f"budget: afvist render faar ventetid til der er raad ({wait:.1f} s)", 0 < wait <= 5)
W._cpu_budget, W._cpu_budget_at = W._CPU_BUDGET_CAPACITY, 0.0
t = 2_000_000.0
spredt_ok = all(W._cpu_budget_take(600.0, t + i * 8000.0) == 0.0 for i in range(60))
check("budget: tungeste render hvert 8. sekund i 8 minutter bremses aldrig (maalt taalt)", spredt_ok)
W._cpu_budget = 100.0
W._cpu_budget_refund(1_000_000.0)
check("budget: refundering kan ikke overstige kapaciteten", W._cpu_budget == W._CPU_BUDGET_CAPACITY)
_r = lambda p, m="GET": types.SimpleNamespace(url=f"https://madshopper.dk{p}", method=m, headers={})
check("budget: vaegte pr. rutetype",
      W._cpu_cost(_r("/search/results?q=x")) == 600.0 and W._cpu_cost(_r("/api/autocomplete?q=x")) == 100.0
      and W._cpu_cost(_r("/Mejeri")) == W._CPU_COST_DEFAULT
      and W._cpu_cost(_r("/api/cart-event", "POST")) == W._CPU_COST_NON_GET)
_b = W._busy_response(req("/api/search?q=x"), retry_after=7.2)
check("budget: Retry-After foelger ventetiden (rundet op)", _b[2]["headers"]["Retry-After"] == "8")
_b = W._busy_response(req("/api/search?q=x"), retry_after=500)
check("budget: Retry-After har et loft", _b[2]["headers"]["Retry-After"] == str(W._BUSY_RETRY_MAX_SECONDS))
_src = open(os.path.join(ROOT, "src", "worker.py"), encoding="utf-8").read()
check("budget: begge fetch-veje tjekker budgettet foer render", _src.count("self._cpu_admit(request)") == 2)
W._cpu_budget, W._cpu_budget_at = W._CPU_BUDGET_CAPACITY, 0.0


# --- 5) fejler laasen selv, renderes der alligevel -------------------------
async def _laas_fejler():
    reset()
    return await worker._render_exclusive(req("/uden-js"))

_saved = js.Promise
js.Promise = None  # Promise.new -> AttributeError inde i laasen
try:
    r = asyncio.run(_laas_fejler())
    check("laas-fejl giver stadig et svar", r == ("ok", "https://madshopper.dk/uden-js"))
except Exception as e:
    check(f"laas-fejl giver stadig et svar (kastede {e!r})", False)
finally:
    js.Promise = _saved

check("modul-konstant: ventelofte er positivt og under single-flight-loftet",
      0 < W._RENDER_WAIT_MAX_MS < W._SINGLE_FLIGHT_MAX_MS)
check("modul-konstant: en render erklaeres foerst doed efter ventelofte",
      W._RENDER_WAIT_MAX_MS < W._RENDER_DEAD_MS)
check("modul-konstant: koe-loftet er mindst 1", W._RENDER_QUEUE_MAX >= 1)


# --- 6) INVARIANT: super().fetch() kun inde i _render_exclusive ------------
_tree = ast.parse(open(os.path.join(ROOT, "src", "worker.py"), encoding="utf-8").read())
_span = None
for node in ast.walk(_tree):
    if isinstance(node, ast.AsyncFunctionDef) and node.name == "_render_exclusive":
        _span = (node.lineno, node.end_lineno or node.lineno)
check("_render_exclusive findes", _span is not None)

_bypass = []
for node in ast.walk(_tree):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "fetch"
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Name)
            and node.func.value.func.id == "super"):
        if not (_span and _span[0] <= node.lineno <= _span[1]):
            _bypass.append(node.lineno)
check(f"ingen super().fetch() uden om laasen (fandt linjer {_bypass})", not _bypass)

print()
print("ALLE TESTS BESTAAET" if not fails else f"{len(fails)} FEJLEDE: {fails}")
sys.exit(1 if fails else 0)
