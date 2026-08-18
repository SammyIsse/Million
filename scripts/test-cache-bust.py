#!/usr/bin/env python3
"""Håndhæver ?v=-reglen for statiske assets.

Kør: python3 scripts/test-cache-bust.py [base-ref]

BAGGRUNDEN, kort: browseren henter /static/css/styles.css og
/static/js/{script,auth}.js med en ?v=<n>-forespørgselsstreng, og de serveres
`immutable`. Ændrer man en af filerne UDEN at hælde ?v= op i
templates/base.html, bliver den gamle udgave ved med at køre hos alle
besøgende - potentielt i timer. Det er ikke teoretisk: da Turnstile-hooken
blev slået til, brød login for alle, netop fordi klienterne kørte videre på
den gamle auth.js (commit c7c0efd), og reglen er sidenhen glemt mindst to
gange mere og først fanget af et menneske bagefter.

En regel der kun står i CLAUDE.md bliver glemt. Denne test gør den til en
gate - samme rolle som scripts/test-security-logging.py spiller for
observability-invarianten.

REGLEN: rører diffen en af de overvågede assets, SKAL samme diff også ændre
den pågældende fils ?v=-tal i templates/base.html.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_HTML = ROOT / "templates" / "base.html"

# asset-sti i repoet -> hvordan den refereres i base.html
WATCHED = {
    "static/css/styles.css": "css/styles.css",
    "static/js/script.js": "js/script.js",
    "static/js/auth.js": "js/auth.js",
}

fails: list[str] = []


def check(label: str, ok: bool) -> None:
    print(("  OK   " if ok else "  FEJL ") + label)
    if not ok:
        fails.append(label)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout


def versions_in(text: str) -> dict[str, set[str]]:
    """{'js/auth.js': {'24'}} - alle ?v=-tal pr. asset i en base.html-tekst."""
    out: dict[str, set[str]] = {}
    for path in WATCHED.values():
        pattern = re.escape(path) + r"'\s*\)\s*\}\}\?v=(\d+)"
        out[path] = set(re.findall(pattern, text))
    return out


def main() -> int:
    base_ref = sys.argv[1] if len(sys.argv) > 1 else "HEAD~1"

    # --- 1) Hver asset har præcis ÉN version i base.html ---------------------
    # styles.css står to gange (preload + stylesheet). Står de to med hvert
    # sit tal, henter browseren filen TO gange og bruger den ene - præcis den
    # slags stille spild en gate skal fange.
    current = versions_in(BASE_HTML.read_text(encoding="utf-8"))
    for path, found in current.items():
        check(f"{path}: præcis én ?v=-værdi i base.html (fandt {sorted(found) or 'ingen'})",
              len(found) == 1)

    # --- 2) Ændret asset => ændret ?v= i samme diff --------------------------
    changed = set(git("diff", "--name-only", base_ref, "HEAD").split())
    if not changed:
        print(f"\n(ingen ændringer mod {base_ref} - springer diff-kontrollen over)")
    else:
        old_html = git("show", f"{base_ref}:templates/base.html")
        old = versions_in(old_html) if old_html else {}
        for asset, path in WATCHED.items():
            if asset not in changed:
                continue
            before = old.get(path, set())
            after = current.get(path, set())
            check(
                f"{asset} er ændret => ?v= er hævet ({sorted(before) or '?'} -> {sorted(after) or '?'})",
                bool(before) and bool(after) and before != after,
            )

    print()
    if fails:
        print(f"{len(fails)} KONTROL(LER) FEJLEDE")
        print("Ret templates/base.html: hæv ?v= for den/de ændrede filer.")
        return 1
    print("ALLE TESTS BESTAAET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
