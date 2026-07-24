"""Regressionstest af sikkerhedslogningen i src/worker.py.

Koeres med: python3 scripts/test-security-logging.py

Den vigtigste egenskab der bevises her, er at logningen IKKE kan blive
angrebets egen forstaerker: 500 haendelser giver EN D1-skrivning, og 500
mere inden for samme minut giver INGEN. Det var praecis den fejlklasse
(logning der skalerer med trafikken) der vaeltede produktionen 2026-07-19,
saa den maa aldrig kunne snige sig ind igen.

Workers-runtime findes ikke uden for Cloudflare, saa js, pyodide, edgekit og
app stubbes - testen maaler vores egen logik, ikke platformens."""
import os, sys, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- stub runtime-moduler -------------------------------------------------
class _Date:
    _now = 1_000_000.0
    @staticmethod
    def now(): return _Date._now
    @staticmethod
    def new(ms):
        import datetime
        d = datetime.datetime.fromtimestamp(ms/1000, datetime.timezone.utc)
        return types.SimpleNamespace(toISOString=lambda: d.strftime("%Y-%m-%dT%H:%M:%S.000Z"))

js = types.ModuleType("js"); js.Date = _Date
js.Object = types.SimpleNamespace(fromEntries=lambda x: x)
sys.modules["js"] = js

pyo = types.ModuleType("pyodide"); ffi = types.ModuleType("pyodide.ffi")
ffi.to_js = lambda x, **kw: x
pyo.ffi = ffi
sys.modules["pyodide"] = pyo; sys.modules["pyodide.ffi"] = ffi

for name in ["edgekit", "edgekit.adapters", "edgekit.bindings", "edgekit.webapi",
             "edgekit.webapi.response", "edgekit.runtime", "app"]:
    sys.modules[name] = types.ModuleType(name)
class _WSGI:
    def __class_getitem__(cls, item): return cls
sys.modules["edgekit.adapters"].WSGI = _WSGI
sys.modules["edgekit.bindings"].KVNamespace = object
sys.modules["edgekit.bindings"].D1Database = object
sys.modules["edgekit.webapi.response"].Response = types.SimpleNamespace(
    json=lambda *a, **k: ("json", a, k), text=lambda *a, **k: ("text", a, k))
sys.modules["app"].app = object()

sys.path.insert(0, os.path.join(ROOT, "src"))
import worker as W

# --- fakes ----------------------------------------------------------------
class FakeStmt:
    def __init__(self, sql, log): self.sql, self.log, self.args = sql, log, None
    def bind(self, *a): self.args = a; return self
    def run(self): self.log.append(("run", self.sql, self.args)); return "promise"
class FakeDB:
    def __init__(self): self.log = []; self.batches = []
    def prepare(self, sql): return FakeStmt(sql, self.log)
    def batch(self, stmts): self.batches.append(stmts); return "promise"
class FakeCtx:
    def __init__(self): self.promises = []
    def waitUntil(self, p): self.promises.append(p)
def req(path):
    return types.SimpleNamespace(url=f"https://madshopper.dk{path}", headers={})

fails = []
def check(label, cond):
    print(("  OK   " if cond else "  FEJL ") + label)
    if not cond: fails.append(label)

# --- 1) sti-normalisering (kardinalitetsloft) -----------------------------
check("/ -> '/'",                W._sec_path(req("/")) == "/")
check("/Mejeri?x=1 -> '/Mejeri'", W._sec_path(req("/Mejeri?x=1")) == "/Mejeri")
check("/api/cart-event bevares",  W._sec_path(req("/api/cart-event")) == "/api/cart-event")
check("dyb sti kollapses",        W._sec_path(req("/product/abc/def/ghi")) == "/product")

# --- 2) aggregering --------------------------------------------------------
env = types.SimpleNamespace(DB=FakeDB()); ctx = FakeCtx()
W._sec_counts.clear(); W._sec_flush_at = 0.0; W._sec_table_ready = False
for _ in range(500):
    W._sec_note("rate_limit", req("/api/cart-event"))
for _ in range(3):
    W._sec_note("server_error", req("/Mejeri"))
check("500 haendelser -> 2 noegler", len(W._sec_counts) == 2)
check("taeller korrekt", W._sec_counts[("rate_limit", "/api/cart-event")] == 500)

# --- 3) foerste skylning sker straks --------------------------------------
W._sec_flush(env, ctx)
check("aggregatet toemt efter skyl", len(W._sec_counts) == 0)
check("1 batch med 2 INSERTs", len(env.DB.batches) == 1 and len(env.DB.batches[0]) == 2)
check("CREATE koert separat (ikke i batch)",
      any(o[0] == "run" and "CREATE TABLE" in o[1] for o in env.DB.log))
row = env.DB.batches[0][0]
import datetime as _dt
_exp = _dt.datetime.fromtimestamp(_Date._now/1000, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")
check(f"bind: bucket er minut-praecis ({row.args[0]})", row.args[0] == _exp and len(row.args[0]) == 16)
check("bind: antal med", 500 in [s.args[3] for s in env.DB.batches[0]])

# --- 4) 500 nye haendelser inden for 60s giver INGEN ny skrivning ---------
before = len(env.DB.batches)
for _ in range(500):
    W._sec_note("rate_limit", req("/api/cart-event"))
W._sec_flush(env, ctx)
check("inden for 60s: ingen ny D1-skrivning", len(env.DB.batches) == before)
check("haendelser bevaret til naeste vindue", len(W._sec_counts) == 1)

# --- 5) efter 60s skylles der igen ----------------------------------------
_Date._now += 61_000
W._sec_flush(env, ctx)
check("efter 60s: ny skrivning", len(env.DB.batches) == before + 1)
check("CREATE koeres kun EN gang pr. isolate",
      sum(1 for o in env.DB.log if o[0] == "run" and "CREATE TABLE" in o[1]) == 1)

# --- 6) kardinalitetsloft --------------------------------------------------
W._sec_counts.clear()
for i in range(1000):
    W._sec_note("rate_limit", req(f"/vilkaarlig{i}"))
check(f"loft holder ({len(W._sec_counts)} <= {W._SEC_MAX_KEYS}+1)", len(W._sec_counts) <= W._SEC_MAX_KEYS + 1)
check("overloeb samles i én noegle", ("rate_limit", "(overflow)") in W._sec_counts)

# --- 7) fejl i D1 maa aldrig boble op -------------------------------------
class BoomDB:
    def prepare(self, *a): raise RuntimeError("D1 nede")
    def batch(self, *a): raise RuntimeError("D1 nede")
W._sec_counts.clear(); W._sec_flush_at = 0.0
W._sec_note("rate_limit", req("/"))
try:
    W._sec_flush(types.SimpleNamespace(DB=BoomDB()), FakeCtx())
    check("D1-fejl kastes ikke videre", True)
except Exception as e:
    check(f"D1-fejl kastes ikke videre (kastede {e!r})", False)

# --- 8) manglende DB-binding ----------------------------------------------
W._sec_counts.clear(); W._sec_flush_at = 0.0
W._sec_note("rate_limit", req("/"))
W._sec_flush(types.SimpleNamespace(), FakeCtx())
check("uden DB-binding: ingen fejl, aggregat ryddet", len(W._sec_counts) == 0)

print()
print("ALLE TESTS BESTAAET" if not fails else f"{len(fails)} FEJLEDE: {fails}")
sys.exit(1 if fails else 0)
