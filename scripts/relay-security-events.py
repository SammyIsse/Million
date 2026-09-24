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
# busy: workeren svarede selv "travlt" (503 + X-MadShopper-Busy) i stedet for
# at rendere - CPU-budget, koe-loft eller ventetid (src/worker.py). Det
# erstatter 1102, saa Cloudflare-analytics viser "success" og intet andet
# tjek ser det: et for stramt kalibreret budget ville afvise rigtig trafik
# med alt groent. Taellingen er pr. forsoeg - klienterne proever selv igen
# 3-4 gange - saa én uheldig besoegende kan give en haandfuld. Revurdér
# taersklerne naar der er en uges data efter budgettet (15-09-2026).
ALERT_BUSY_PER_HOUR = 60
ALERT_BUSY_PER_DAY = 200
# Cloudflares egne fejlsider (se fetch_worker_invocations). Normal drift er 0;
# en enkelt 1102 kan ske ved et tilfaeldigt CPU-tungt kald, men 10 paa en time
# er et moenster - samtidighedstesten 15-09-2026 gav 6 paa ét minut.
ALERT_CPU_LIMIT_PER_HOUR = 10
ALERT_EXCEPTION_PER_HOUR = 10

# Vinduet alarmerne vurderer. Var "seneste time" - men workflowet er sat til
# hvert 15. minut og koerer i praksis kun hver 2.-6. time (GitHub-cron-
# forsinkelse, maalt 11-14/09-2026: 27 koersler paa 3 doegn), saa haendelser
# mellem to koersler blev ALDRIG vurderet. 24 t daekker forsinkelsen med
# margin; hver time-spand i vinduet vurderes for sig.
LOOKBACK_HOURS = 24

_BUCKET_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")

# ---------------------------------------------------------------------------
# Cloudflares egne invocation-tal
# ---------------------------------------------------------------------------
# security_events ovenfor ser KUN det Python-koden selv naar at taelle. De to
# fejl der faktisk vaelter sitet naar aldrig dertil: Error 1102 (CPU-graensen,
# status "exceededResources") afbryder isolaten midt i en request, og Error 1101
# ("scriptThrewException") opstaar ofte i runtimen foer Python koerer. Aggregatet
# i workerens hukommelse doer desuden med isolaten. Maalt 15-09-2026: en audit
# fik 20 fejl og en samtidighedstest 6x 1102 + 4 degraderede svar - D1 havde
# 0 server_error og 1 degraded. Cloudflares GraphQL-analytics taeller hver
# invocation uafhaengigt af workeren, og det koster intet i workeren selv
# (ingen observability, ingen logning pr. request).
CF_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"
WORKER_SCRIPT = os.environ.get("WORKER_SCRIPT") or "madshopper"
CF_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN") or ""
CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or ""
# Statusser der er normale og ikke skal regnes som fejl.
_OK_STATUSES = {"success", "clientDisconnected"}

_INVOCATIONS_QUERY = """
query($account: string, $script: string, $from: string, $to: string) {
  viewer {
    accounts(filter: {accountTag: $account}) {
      hours: workersInvocationsAdaptive(limit: 10000, filter: {
        scriptName: $script, datetime_geq: $from, datetime_leq: $to
      }) {
        sum { requests errors }
        quantiles { cpuTimeP50 cpuTimeP99 }
        dimensions { datetimeHour status }
      }
      problems: workersInvocationsAdaptive(limit: 10000, filter: {
        scriptName: $script, datetime_geq: $from, datetime_leq: $to,
        status_notin: ["success", "clientDisconnected"]
      }) {
        sum { requests }
        dimensions { datetimeMinute status }
      }
    }
  }
}
"""


def fetch_worker_invocations(hours: int) -> dict:
    """Invocations pr. time og status + problem-invocations pr. minut.

    Kaster ved enhver fejl: et tjek der ikke kan maale, skal fejle - aldrig
    staa groent (se CLAUDE.md § Verifikation)."""
    if not (CF_API_TOKEN and CF_ACCOUNT_ID):
        raise RuntimeError("CLOUDFLARE_API_TOKEN/CLOUDFLARE_ACCOUNT_ID mangler")
    now = datetime.now(timezone.utc)
    variables = {
        "account": CF_ACCOUNT_ID,
        "script": WORKER_SCRIPT,
        "from": (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    resp = httpx.post(
        CF_GRAPHQL_URL,
        headers={"Authorization": f"Bearer {CF_API_TOKEN}",
                 "Content-Type": "application/json"},
        content=json.dumps({"query": _INVOCATIONS_QUERY, "variables": variables}),
        timeout=30.0,
    )
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or data.get("errors"):
        raise RuntimeError(
            f"GraphQL-analytics svarede {resp.status_code}: "
            f"{json.dumps(data.get('errors') or data)[:400]} - kraever at "
            "CLOUDFLARE_API_TOKEN har 'Account Analytics: Read'"
        )
    accounts = ((data.get("data") or {}).get("viewer") or {}).get("accounts") or []
    if not accounts:
        raise RuntimeError("GraphQL-analytics returnerede ingen konto - forkert CLOUDFLARE_ACCOUNT_ID?")
    return accounts[0]


_CPU_DETAIL_QUERY = """
query($account: string, $script: string, $from: string, $to: string) {
  viewer {
    accounts(filter: {accountTag: $account}) {
      workersInvocationsAdaptive(limit: 10000, filter: {
        scriptName: $script, datetime_geq: $from, datetime_leq: $to
      }) {
        sum { requests }
        quantiles { cpuTimeP50 cpuTimeP90 cpuTimeP99 }
        dimensions { datetimeMinute status }
      }
    }
  }
}
"""


def print_cpu_detail(start: str, end: str) -> None:
    """CPU-kvantiler pr. minut i et vindue - til maalinger af en bestemt
    rutetype (send en serie af ét slags kald i ét minut, laes minuttet her).
    Workers kan ikke selv maale CPU (timere staar stille under beregning), saa
    Cloudflares taelling er den eneste maaling af CPU paa edge. Koeres via
    workflow_dispatch-inputtene i security-monitor.yml."""
    resp = httpx.post(
        CF_GRAPHQL_URL,
        headers={"Authorization": f"Bearer {CF_API_TOKEN}",
                 "Content-Type": "application/json"},
        content=json.dumps({"query": _CPU_DETAIL_QUERY, "variables": {
            "account": CF_ACCOUNT_ID, "script": WORKER_SCRIPT, "from": start, "to": end}}),
        timeout=30.0,
    )
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or data.get("errors"):
        print(f"CPU-detalje fejlede {resp.status_code}: {json.dumps(data.get('errors') or data)[:400]}")
        return
    rows = data["data"]["viewer"]["accounts"][0]["workersInvocationsAdaptive"]
    print(f"\nCPU pr. minut {start} .. {end} (millisekunder):")
    for row in sorted(rows, key=lambda r: (r["dimensions"]["datetimeMinute"], r["dimensions"]["status"])):
        q = row.get("quantiles") or {}
        ms = lambda v: f"{(v or 0) / 1000:7.0f}"  # noqa: E731
        print(f"  {row['dimensions']['datetimeMinute'][:16]}  {row['dimensions']['status']:20} "
              f"{row['sum']['requests']:4} req  p50={ms(q.get('cpuTimeP50'))} "
              f"p90={ms(q.get('cpuTimeP90'))} p99={ms(q.get('cpuTimeP99'))}")


def summarize_invocations(acct: dict) -> tuple[dict, dict]:
    """(problemer pr. time {(time, status): n}, pr. minut {(minut, status): n}).
    Skriver en kompakt rapport undervejs."""
    per_hour: dict[str, dict] = {}
    for row in acct.get("hours") or []:
        dims = row.get("dimensions") or {}
        hour = str(dims.get("datetimeHour") or "?")[:13]
        status = str(dims.get("status") or "?")
        s = row.get("sum") or {}
        q = row.get("quantiles") or {}
        h = per_hour.setdefault(hour, {"statuses": {}, "cpu": {}})
        h["statuses"][status] = h["statuses"].get(status, 0) + int(s.get("requests") or 0)
        if status == "success":
            h["cpu"] = {"p50": q.get("cpuTimeP50"), "p99": q.get("cpuTimeP99")}

    problems_hour: dict[tuple, int] = {}
    total_req = 0
    print(f"\nCloudflare-invocations for '{WORKER_SCRIPT}' (cpu i mikrosekunder, success):")
    for hour in sorted(per_hour):
        h = per_hour[hour]
        total = sum(h["statuses"].values())
        total_req += total
        bad = {k: v for k, v in h["statuses"].items() if k not in _OK_STATUSES}
        for k, v in bad.items():
            problems_hour[(hour, k)] = v
        cpu = h["cpu"]
        print(f"  {hour}  {total:6} req  cpu p50={cpu.get('p50')} p99={cpu.get('p99')}"
              + (f"  FEJL {bad}" if bad else ""))
    if not per_hour:
        print("  (ingen invocations i vinduet)")

    problems_minute: dict[tuple, int] = {}
    for row in acct.get("problems") or []:
        dims = row.get("dimensions") or {}
        minute = str(dims.get("datetimeMinute") or "?")[:16]
        status = str(dims.get("status") or "?")
        problems_minute[(minute, status)] = problems_minute.get((minute, status), 0) + int(
            (row.get("sum") or {}).get("requests") or 0)
    if problems_minute:
        print("  Fejl pr. minut:")
        for (minute, status), n in sorted(problems_minute.items()):
            print(f"    {minute}  {status:22} {n}")
    return problems_hour, problems_minute


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


def check_d1_events() -> list[str]:
    ensure_schema()
    rows = [r for r in run_wrangler_sql(
        "SELECT bucket, kind, path, events FROM security_events "
        "ORDER BY bucket ASC LIMIT 5000;"
    ) if _valid(r)]

    if not rows:
        print("\nIngen sikkerhedshaendelser i D1 siden sidst.")
        return []

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
    hour, peak = worst_hour("busy")
    busy_day = recent.get("busy", 0)
    if peak > ALERT_BUSY_PER_HOUR or busy_day > ALERT_BUSY_PER_DAY:
        alarms.append(
            f"{busy_day} \"travlt\"-svar (X-MadShopper-Busy) de seneste "
            f"{LOOKBACK_HOURS} t, vaerste time {hour} UTC: {peak} (taerskler "
            f"{ALERT_BUSY_PER_HOUR}/t og {ALERT_BUSY_PER_DAY}/doegn) - workeren "
            f"afviser renders. Se top-stierne ovenfor; er det ikke et angreb, "
            f"er _CPU_BUDGET_* i src/worker.py sat for stramt."
        )
    if archive_attempted and not archived:
        # Var kun en advarsel - og fejlede derfor tavst i over en maaned.
        # En overvaagning der ikke kan gemme sine data, er selv en fejl.
        alarms.append(
            "Arkivering af sikkerhedshaendelser til Supabase fejlede (se "
            "advarslen ovenfor). D1 ryddes ikke, og historikken gaar tabt."
        )

    return alarms


def check_worker_invocations() -> list[str]:
    try:
        acct = fetch_worker_invocations(LOOKBACK_HOURS)
    except Exception as e:
        return [f"Cloudflare-analytics kunne ikke hentes: {e}"]
    problems_hour, _ = summarize_invocations(acct)
    alarms = []
    for kind, label, limit in (
        ("exceededResources", "Error 1102 (CPU-graensen)", ALERT_CPU_LIMIT_PER_HOUR),
        ("scriptThrewException", "Error 1101 (uncaught exception)", ALERT_EXCEPTION_PER_HOUR),
    ):
        hours = [(h, n) for (h, k), n in problems_hour.items() if k == kind]
        if not hours:
            continue
        hour, peak = max(hours, key=lambda hn: hn[1])
        if peak > limit:
            alarms.append(
                f"{peak} x {label} i timen {hour} UTC (taerskel {limit}/t) - "
                f"besoegende fik Cloudflares fejlside. Se 'Fejl pr. minut' ovenfor."
            )
    other = sorted({k for (_, k) in problems_hour} - {"exceededResources", "scriptThrewException"})
    if other:
        print(f"  Oevrige ikke-success-statusser (ingen alarm): {other}")
    return alarms


def main() -> int:
    detail_from = (os.environ.get("CPU_DETAIL_FROM") or "").strip()
    detail_to = (os.environ.get("CPU_DETAIL_TO") or "").strip()
    if detail_from and detail_to:
        print_cpu_detail(detail_from, detail_to)
    alarms = check_worker_invocations() + check_d1_events()
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
