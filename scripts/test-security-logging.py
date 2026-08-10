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


# --- 9) INVARIANT: ingen logningssti uden om aggregatoren -------------------
# Testene ovenfor beviser at _sec_note/_sec_flush opfoerer sig ordentligt. De
# siger derimod INTET om en HELT NY logningssti ved siden af dem - fx et
# console.log pr. request eller en direkte env.DB-skrivning i fetch(). Det var
# praecis den slags (logning der skalerer med trafikken) der vaeltede
# produktionen 2026-07-19, saa den skal en gate stoppe, ikke en kodegennemgang.
#
# Reglen: i src/worker.py maa D1-kald (prepare/batch/exec) KUN forekomme inde i
# _sec_flush, og print/console maa slet ikke forekomme.
import ast

WORKER_SRC = os.path.join(ROOT, "src", "worker.py")
_src = open(WORKER_SRC, encoding="utf-8").read()
_tree = ast.parse(_src)

_flush_span = None
for node in ast.walk(_tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_sec_flush":
        _flush_span = (node.lineno, node.end_lineno or node.lineno)
check("_sec_flush findes (aggregatoren er ikke omdoebt/fjernet)", _flush_span is not None)

_D1_METHODS = {"prepare", "batch", "exec"}
_bad_d1, _bad_log = [], []
for node in ast.walk(_tree):
    if not isinstance(node, ast.Call):
        continue
    fn = node.func
    if isinstance(fn, ast.Attribute):
        # print(...) / console.log(...) - alt der logger pr. request
        if isinstance(fn.value, ast.Name) and fn.value.id == "console":
            _bad_log.append(node.lineno)
        if fn.attr in _D1_METHODS:
            inside = _flush_span and _flush_span[0] <= node.lineno <= _flush_span[1]
            if not inside:
                _bad_d1.append((node.lineno, fn.attr))
    elif isinstance(fn, ast.Name) and fn.id == "print":
        _bad_log.append(node.lineno)

check(f"ingen print/console i src/worker.py (fandt linjer {_bad_log})", not _bad_log)
check(f"D1-kald kun i _sec_flush (fandt {_bad_d1})", not _bad_d1)

# _SEC_FLUSH_INTERVAL er selve loftet: hoejst EN skrivning pr. minut pr.
# isolate. Bliver den skruet ned, skalerer skrivningerne igen med trafikken.
check(f"_SEC_FLUSH_INTERVAL >= 60s (er {getattr(W, '_SEC_FLUSH_INTERVAL', None)})",
      float(getattr(W, "_SEC_FLUSH_INTERVAL", 0)) >= 60.0)

# --- 10) INVARIANT: observability er slaaet FRA i det der faktisk deployes --
# Cloudflares observability-introspektion er den bekraeftede aarsag til
# nedbruddet 2026-07-19. Den slaas fra i den GENEREREDE dist/wrangler.toml -
# dvs. en enkelt linjeaendring i scripts/build-pages.sh kan genindfoere
# aarsagen uden at nogen anden gate opdager det. Derfor tjekkes baade
# generatoren, rod-konfigurationen og (i CI) det faktiske build-output.
_OBS_SECTIONS = ("observability", "observability.logs", "observability.traces")


def _observability_flags(text: str) -> dict:
    """Minimal TOML-laesning: kun [observability*]-sektionernes enabled-flag.
    Bevidst uden tomllib, saa testen ogsaa kan koere paa aeldre python3 - og
    saa den kan laese heredoc'en i build-pages.sh, som ikke er gyldig TOML."""
    import re
    out, section = {}, None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^\[\[?([^\]\[]+)\]\]?$", s)
        if m:
            section = m.group(1).strip()
            continue
        m = re.match(r"^enabled\s*=\s*(true|false)\b", s)
        if m and section in _OBS_SECTIONS:
            out[section] = (m.group(1) == "true")
    return out


def _check_observability(label: str, path: str, required: bool) -> None:
    if not os.path.exists(path):
        if required:
            check(f"{label}: MANGLER ({path}) - kan ikke verificeres", False)
        else:
            print(f"  ---  {label}: ikke bygget her, springes over")
        return
    flags = _observability_flags(open(path, encoding="utf-8").read())
    for sec in _OBS_SECTIONS:
        check(f"{label}: [{sec}] enabled = false (er {flags.get(sec)!r})",
              flags.get(sec) is False)


# Generatoren: her ville en regression blive skrevet.
_check_observability("build-pages.sh", os.path.join(ROOT, "scripts", "build-pages.sh"), True)
# Rod-konfigurationen: bruges ikke af deploy-flowet (se kommentar i
# build-pages.sh), men skal aldrig kunne staa og sige noget andet.
_check_observability("wrangler.toml (rod)", os.path.join(ROOT, "wrangler.toml"), True)
# Selve build-outputtet. REQUIRE_DIST=1 saettes i deploy-workflows, hvor
# testen koeres EFTER build - saa en manglende dist/ er en fejl, ikke et spring.
_check_observability("dist/wrangler.toml (det der deployes)",
                     os.path.join(ROOT, "dist", "wrangler.toml"),
                     os.environ.get("REQUIRE_DIST") == "1")

print()
print("ALLE TESTS BESTAAET" if not fails else f"{len(fails)} FEJLEDE: {fails}")
sys.exit(1 if fails else 0)
