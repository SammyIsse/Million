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
import re
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
# degraded: X-Data-Degraded (app.py::_set_response_headers) er svar med 200
# men ufuldstaendige data (fejlet D1-opslag, en Rema-only-cache uden
# butiksmatch osv.) - status 200 og uptime-tjekkets "MadShopper"-streng
# saa INTET af det. Normal drift er 0-faa (en transient fejl her og der);
# over 20 paa en time betyder et vedvarende problem, ikke et enkelt hik.
ALERT_RATE_LIMIT_PER_HOUR = 2000
ALERT_SERVER_ERROR_PER_HOUR = 50
ALERT_DEGRADED_PER_HOUR = 10
# Et sivende problem naar aldrig en time-taerskel ved lav trafik. Soegningen
# gav tomme resultater ved samtidige requests i over en maaned (rettet
# 14-09-2026) og loeb op i ca. 13 degraderede svar i doegnet - aldrig 20 paa
# én time, saa INGEN alarm. Efter render-laasen er normalen 0-faa; revurdér
# taersklen naar der er en uges data efter rettelsen.
ALERT_DEGRADED_PER_DAY = 10

# Vinduet alarmerne vurderer. Var "seneste time" - men workflowet er sat til
# hvert 15. minut og koerer i praksis kun hver 2.-6. time (GitHub-cron-
# forsinkelse, maalt 11-14/09-2026: 27 koersler paa 3 doegn), saa haendelser
# mellem to koersler blev ALDRIG vurderet. 24 t daekker forsinkelsen med
# margin; hver time-spand i vinduet vurderes for sig.
LOOKBACK_HOURS = 24

_BUCKET_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")


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
    # workerens _sec_flush skriver '?' som spand, hvis Date-kaldet fejler.
    # Én saadan raekke goer hele arkiverings-batchen ugyldig (timestamptz).
    return bool(_BUCKET_RE.match(str(row.get("bucket") or ""))) and bool(row.get("kind"))


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
            # on_conflict er noedvendig: uden den loeser PostgREST konflikten
            # paa primaernoeglen (id), og en genkoersel rammer i stedet
            # UNIQUE (bucket, kind, path) -> 409 / 23505. Det fejlede SAADAN
            # ved hver koersel fra 12-08 til 14-09-2026, saa D1 blev aldrig
            # ryddet og tallene i rapporten var loebende totaler.
            f"{SUPABASE_URL}/rest/v1/security_events?on_conflict=bucket,kind,path",
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

    # Opsummering pr. type, og for alarmvinduet (se LOOKBACK_HOURS) baade
    # samlet og pr. time-spand.
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M")
    totals: dict[str, int] = {}
    recent: dict[str, int] = {}
    per_hour: dict[tuple, int] = {}
    by_path: dict[tuple, int] = {}
    for r in rows:
        kind = str(r.get("kind"))
        n = int(r.get("events") or 0)
        bucket = str(r.get("bucket"))
        totals[kind] = totals.get(kind, 0) + n
        if bucket >= cutoff:
            recent[kind] = recent.get(kind, 0) + n
            hour_key = (kind, bucket[:13])
            per_hour[hour_key] = per_hour.get(hour_key, 0) + n
            by_path[(kind, str(r.get("path")))] = by_path.get((kind, str(r.get("path"))), 0) + n

    def worst_hour(kind: str) -> tuple[str, int]:
        hours = [(h, n) for (k, h), n in per_hour.items() if k == kind]
        return max(hours, key=lambda hn: hn[1]) if hours else ("-", 0)

    print(f"{len(rows)} aggregerede raekke(r) hentet fra D1.")
    for kind, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        hour, peak = worst_hour(kind)
        print(f"  {kind:14} {n:7} haendelser i alt  ({recent.get(kind, 0)} seneste "
              f"{LOOKBACK_HOURS} t, vaerste time {hour} UTC: {peak})")
    if by_path:
        print(f"  Top-stier de seneste {LOOKBACK_HOURS} t:")
        for (kind, path), n in sorted(by_path.items(), key=lambda kv: -kv[1])[:10]:
            print(f"    {kind:14} {path:24} {n}")

    archived = archive_to_supabase(rows)
    archive_attempted = bool(SUPABASE_URL and SUPABASE_KEY)

    # Ryd kun D1 for det vi rent faktisk fik arkiveret - ellers hellere
    # dubletter i naeste koersel end tabte spor.
    if archived:
        buckets = sorted({str(r["bucket"]) for r in rows})
        lo = buckets[0].replace("'", "")
        # Slet ALDRIG den indevaerende minut-bucket. Workerens _sec_flush
        # upserter additivt (events + excluded.events) hvert minut fra hver
        # isolate, og arkiveringen ovenfor tager sekunder. Slettede vi hele
        # intervallet op til og med sidste laeste bucket, ville taellinger
        # skrevet i mellemtiden forsvinde uarkiveret - og tabet er systematisk
        # stoerst netop under et angreb, hvor mange isolates flusher samtidig.
        # Vi stopper derfor ved sidste FULDT AFSLUTTEDE minut; resten bliver
        # staaende og kommer med naeste koersel.
        now_bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        hi = min(buckets[-1].replace("'", ""), now_bucket)
        if hi < lo:
            print("Kun raekker fra indevaerende minut - venter med at rydde i D1.")
        else:
            run_wrangler_sql(
                f"DELETE FROM security_events WHERE bucket >= '{lo}' AND bucket < '{hi}';"
            )
            print(f"Arkiveret i Supabase og ryddet i D1 ({lo} .. under {hi}).")
    else:
        print("Ikke arkiveret - raekkerne bliver staaende i D1 til naeste koersel.")

    alarms = []
    hour, peak = worst_hour("rate_limit")
    if peak > ALERT_RATE_LIMIT_PER_HOUR:
        alarms.append(
            f"{peak} rate-limit-afvisninger i timen {hour} UTC "
            f"(taerskel {ALERT_RATE_LIMIT_PER_HOUR}) - nogen hamrer paa sitet."
        )
    hour, peak = worst_hour("server_error")
    if peak > ALERT_SERVER_ERROR_PER_HOUR:
        alarms.append(
            f"{peak} serverfejl (5xx) i timen {hour} UTC "
            f"(taerskel {ALERT_SERVER_ERROR_PER_HOUR}) - fejlbolge, tjek seneste deploy."
        )
    hour, peak = worst_hour("degraded")
    degraded_day = recent.get("degraded", 0)
    if peak > ALERT_DEGRADED_PER_HOUR or degraded_day > ALERT_DEGRADED_PER_DAY:
        alarms.append(
            f"{degraded_day} degraderede svar (X-Data-Degraded) de seneste "
            f"{LOOKBACK_HOURS} t, vaerste time {hour} UTC: {peak} (taerskler "
            f"{ALERT_DEGRADED_PER_HOUR}/t og {ALERT_DEGRADED_PER_DAY}/doegn) - "
            f"rigtige besoegende fik tomme lister/soegninger, status er stadig "
            f"200. Se top-stierne ovenfor; kendte aarsager: isolate-kollision i "
            f"D1-broen (render-laasen i src/worker.py), D1-budget sprængt, "
            f"fejlet seed."
        )
    if archive_attempted and not archived:
        # Var kun en advarsel - og fejlede derfor tavst i over en maaned.
        # En overvaagning der ikke kan gemme sine data, er selv en fejl.
        alarms.append(
            "Arkivering af sikkerhedshaendelser til Supabase fejlede (se "
            "advarslen ovenfor). D1 ryddes ikke, og historikken gaar tabt."
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
