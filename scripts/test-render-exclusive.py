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
  4) en render der aldrig bliver faerdig (CPU-drab koerer ikke finally) kun
     blokerer indtil _RENDER_WAIT_MAX_MS - isolatet haenger ikke for altid,
  5) fejler selve laasen (fx js-importen), renderes der alligevel,
  6) INVARIANT: super().fetch() kaldes KUN inde i _render_exclusive, saa en
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


def _set_timeout(fn, ms):
    asyncio.get_running_loop().call_later(ms / 1000, fn)


class _Date:
    @staticmethod
    def now():
        return 0.0


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
    BRIDGE.update(busy=False, log=[], degraded=0, active=0, max_active=0)


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


# --- 4) en render der aldrig bliver faerdig blokerer kun til loftet --------
async def _haenger():
    reset()
    W._RENDER_WAIT_MAX_MS = 150
    stuck = asyncio.ensure_future(worker._render_exclusive(req("/haenger")))
    await asyncio.sleep(0.01)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    r = await asyncio.wait_for(worker._render_exclusive(req("/naeste")), timeout=3)
    waited = loop.time() - t0
    stuck.cancel()
    return r, waited

old_max = W._RENDER_WAIT_MAX_MS
try:
    r, waited = asyncio.run(_haenger())
    check("naeste render koerer trods en haengende forgaenger", r == ("ok", "https://madshopper.dk/naeste"))
    check(f"ventede ca. loftet, ikke for evigt ({waited*1000:.0f} ms)", 0.12 <= waited < 1.0)
except asyncio.TimeoutError:
    check("haengende render blokerer ikke isolatet for altid (haengte!)", False)
finally:
    W._RENDER_WAIT_MAX_MS = old_max


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
