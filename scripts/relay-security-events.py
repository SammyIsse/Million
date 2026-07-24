#!/usr/bin/env python3
"""Henter sikkerhedshaendelser fra D1 (security_events), arkiverer dem i
Supabase og ALARMERER hvis noget ser ud som et angreb.

Hvorfor scriptet findes: Workers-observability er permanent slaaet fra (dens
introspektion var selv aarsag til nedbruddet 2026-07-19), saa der er ingen
request- eller fejllog i produktion. src/worker.py taeller i stedet de
interessante haendelser (429 fra rate limiteren, 5xx fra appen), aggregeret pr.
minut, og skriver dem til D1. Dette script loefter dem videre og faar
GitHub til at sende mail, naar en taerskel overskrides - praecis samme
alarmkanal som uptime-check.yml bruger (et fejlende scheduled workflow).

Exit-kode 1 = alarm. Det er DEN der udloeser mailen; skriv derfor aldrig
scriptet om til at sluge fejl.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import httpx

DB_NAME = "madshopper"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = os.environ.get("DEPLOY_KEY") or ""

# Taerskler pr. time. Saettes hoejt nok til at normal trafik aldrig rammer dem,
# og lavt nok til at et reelt misbrugsmoenster gor det.
#
# 429: rate limiteren tillader 150 req/min pr. IP = 9.000/time for EN enkelt
# flittig bruger, foer der overhovedet afvises noget. At se 2.000 AFVISTE
# requests paa en time betyder at nogen har ligget langt over graensen laenge.
# 5xx: normal drift er 0. 50 paa en time er en reel fejlbolge - det var
# praecis signaturen paa nedbruddet 2026-07-19.
ALERT_RATE_LIMIT_PER_HOUR = 2000
ALERT_SERVER_ERROR_PER_HOUR = 50


def run_wrangler_sql(sql: str) -> list[dict]:
    """Samme kaldemoenster som relay-feedback-to-sheet.py: --command (ikke
    --file) er det eneste der returnerer raekkedata i denne wrangler-version."""
    result = subprocess.run(
        ["npx", "wrangler@4", "d1", "execute", DB_NAME, "--remote",
         f"--command={sql}", "--json"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    stdout = result.stdout
    json_start = stdout.find("[")
    if json_start == -1:
        print("wrangler-output uden JSON:", stdout, result.stderr, file=sys.stderr)
        raise RuntimeError("Kunne ikke finde JSON i wrangler d1 execute-output")
    payload = json.loads(stdout[json_start:])
    return payload[0].get("results", []) if payload else []


def ensure_schema() -> None:
    """Samme skema som src/worker.py opretter. Her ogsaa, saa foerste koersel
    virker uanset om workeren naaede at skrive noget endnu."""
    run_wrangler_sql(
        "CREATE TABLE IF NOT EXISTS security_events ("
        "bucket TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL, "
        "events INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (bucket, kind, path));"
    )


def _valid(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    return bool(row.get("bucket")) and bool(row.get("kind"))


def archive_to_supabase(rows: list[dict]) -> bool:
    """Bedste indsats: arkivering maa aldrig staa i vejen for alarmen."""
    if not (SUPABASE_URL and SUPABASE_KEY and rows):
        return False
    payload = [
        {
            # D1 gemmer minut-spanden som 'YYYY-MM-DDTHH:MM' (UTC).
            "bucket": f"{r['bucket']}:00+00:00",
            "kind": str(r.get("kind"))[:32],
            "path": str(r.get("path") or "")[:120],
            "events": int(r.get("events") or 0),
        }
        for r in rows
    ]
    try:
        resp = httpx.post(
            f"{SUPABASE_URL}/rest/v1/security_events",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                # Genkoersler skal ikke fejle paa en spand vi allerede har.
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            content=json.dumps(payload),
            timeout=30.0,
        )
        if resp.status_code not in (200, 201, 204):
            print(f"advarsel: Supabase-arkivering gav {resp.status_code}: "
                  f"{resp.text[:200]} - koer scripts/supabase-hardening.sql",
                  file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"advarsel: Supabase-arkivering fejlede: {e}", file=sys.stderr)
        return False


def main() -> int:
    ensure_schema()
    rows = [r for r in run_wrangler_sql(
        "SELECT bucket, kind, path, events FROM security_events "
        "ORDER BY bucket ASC LIMIT 5000;"
    ) if _valid(r)]

    if not rows:
        print("Ingen sikkerhedshaendelser siden sidst - alt roligt.")
        return 0

    # Opsummering pr. type, og separat for den seneste time (alarmvinduet).
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    totals: dict[str, int] = {}
    recent: dict[str, int] = {}
    by_path: dict[tuple, int] = {}
    for r in rows:
        kind = str(r.get("kind"))
        n = int(r.get("events") or 0)
        totals[kind] = totals.get(kind, 0) + n
        if str(r.get("bucket")) >= cutoff:
            recent[kind] = recent.get(kind, 0) + n
            by_path[(kind, str(r.get("path")))] = by_path.get((kind, str(r.get("path"))), 0) + n

    print(f"{len(rows)} aggregerede raekke(r) hentet fra D1.")
    for kind, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {kind:14} {n:7} haendelser i alt  ({recent.get(kind, 0)} seneste time)")
    if by_path:
        print("  Top-stier den seneste time:")
        for (kind, path), n in sorted(by_path.items(), key=lambda kv: -kv[1])[:10]:
            print(f"    {kind:14} {path:24} {n}")

    archived = archive_to_supabase(rows)

    # Ryd kun D1 for det vi rent faktisk fik arkiveret - ellers hellere
    # dubletter i naeste koersel end tabte spor.
    if archived:
        buckets = sorted({str(r["bucket"]) for r in rows})
        lo, hi = buckets[0].replace("'", ""), buckets[-1].replace("'", "")
        run_wrangler_sql(
            f"DELETE FROM security_events WHERE bucket >= '{lo}' AND bucket <= '{hi}';"
        )
        print(f"Arkiveret i Supabase og ryddet i D1 ({lo} .. {hi}).")
    else:
        print("Ikke arkiveret - raekkerne bliver staaende i D1 til naeste koersel.")

    alarms = []
    if recent.get("rate_limit", 0) > ALERT_RATE_LIMIT_PER_HOUR:
        alarms.append(
            f"{recent['rate_limit']} rate-limit-afvisninger den seneste time "
            f"(taerskel {ALERT_RATE_LIMIT_PER_HOUR}) - nogen hamrer paa sitet."
        )
    if recent.get("server_error", 0) > ALERT_SERVER_ERROR_PER_HOUR:
        alarms.append(
            f"{recent['server_error']} serverfejl (5xx) den seneste time "
            f"(taerskel {ALERT_SERVER_ERROR_PER_HOUR}) - fejlbolge, tjek seneste deploy."
        )

    if alarms:
        print("\n=== ALARM ===", file=sys.stderr)
        for a in alarms:
            print("  " + a, file=sys.stderr)
        print("Se Cloudflare Security Analytics for kilde-IP'er og lande.", file=sys.stderr)
        return 1

    print("Under alle taerskler - ingen alarm.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
