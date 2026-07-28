#!/usr/bin/env python3
"""Henter AGGREGEREDE Workers- og zone-metrikker fra Cloudflares GraphQL
Analytics API - fejlrate, CPU-tid og HTTP-statuskoder pr. time.

Hvorfor scriptet findes: Workers-observability er permanent slaaet fra i
produktion (dens introspektion var selv aarsagen til nedbruddet 2026-07-19),
saa der findes ingen request- eller fejllog at kigge i naar sitet opfoerer sig
underligt. Ved nedbruddet 2026-07-28 kostede det fem timers gaetteri.

Analytics-API'et er en ANDEN kilde end observability: tallene er forud-
aggregerede paa Cloudflares side og hentes udefra bagefter. Det logger ingenting
pr. request og roerer ikke workeren - det er derfor sikkert at bruge i
produktion, i modsaetning til at slaa observability til.

Hvad de to afsnit svarer paa:
  Workers  - hvad gjorde VORES kode? (requests, exceptions, exceededCpu, CPU-tid)
  Zone     - hvad SAA den besoegende? (edge-statuskoder, inkl. 5xx der aldrig
             naaede workeren, og 1101/1102 der bliver til 500 ude i kanten)

Brug:
  python scripts/cf-analytics.py                 # sidste 24 timer, begge workers
  python scripts/cf-analytics.py --hours 6       # kortere vindue
  python scripts/cf-analytics.py --worker madshopper-dev
  python scripts/cf-analytics.py --hours 72 --no-zone

Kraever CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID (og CLOUDFLARE_ZONE_ID for
zone-afsnittet) - samme navne som resten af repoet, se .env og
security-monitor.yml. Tokenet skal have laeseret til Account Analytics og
Zone Analytics.

Scriptet er ren laesning og returnerer altid 0, medmindre selve opslaget
fejler. Det er et diagnoseredskab, ikke en alarm - alarmen er
scripts/relay-security-events.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"

# Produktions- og staging-workeren, jf. WORKER_NAME i scripts/build-pages.sh.
DEFAULT_WORKERS = ["madshopper", "madshopper-dev"]

# Cloudflares egne udfald. "ok" er det eneste gode; resten er hver sin
# fejlklasse og fortjener at staa hver for sig i outputtet.
STATUS_LABELS = {
    "ok": "ok",
    "success": "ok",
    "scriptThrewException": "exception (1101)",
    "exception": "exception (1101)",
    "exceededCpu": "CPU-loft (1102)",
    "exceededMemory": "hukommelsesloft",
    "canceled": "afbrudt",
    "clientDisconnected": "klient koblet af",
    "responseStreamDisconnected": "stream afbrudt",
    "unknown": "ukendt",
}

WORKERS_QUERY = """
query($accountTag: String!, $scriptName: String!, $start: Time!, $end: Time!) {
  viewer {
    accounts(filter: { accountTag: $accountTag }) {
      workersInvocationsAdaptive(
        limit: 10000
        filter: { scriptName: $scriptName, datetime_geq: $start, datetime_leq: $end }
      ) {
        sum { requests errors subrequests }
        quantiles { cpuTimeP50 cpuTimeP99 }
        dimensions { datetime scriptName status }
      }
    }
  }
}
"""

ZONE_QUERY = """
query($zoneTag: String!, $start: Time!, $end: Time!) {
  viewer {
    zones(filter: { zoneTag: $zoneTag }) {
      httpRequestsAdaptiveGroups(
        limit: 10000
        filter: { datetime_geq: $start, datetime_leq: $end }
      ) {
        count
        dimensions { datetimeHour edgeResponseStatus }
      }
    }
  }
}
"""


def graphql(token: str, query: str, variables: dict) -> dict:
    """Et enkelt GraphQL-kald. GraphQL svarer 200 selv paa fejl, saa 'errors'
    i kroppen skal tjekkes eksplicit - ellers ser en tom rapport ud som
    'ingen trafik' i stedet for 'forkert token'."""
    resp = httpx.post(
        GRAPHQL_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        content=json.dumps({"query": query, "variables": variables}),
        timeout=60.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    payload = resp.json()
    if payload.get("errors"):
        msgs = "; ".join(str(e.get("message", e)) for e in payload["errors"])
        raise RuntimeError(msgs)
    return payload.get("data") or {}


def hour_bucket(dt_str: str) -> str:
    """'2026-07-28T14:23:00Z' -> '2026-07-28 14'. Vi grupperer selv i stedet
    for at bede API'et om en datetimeHour-dimension, saa samme kald virker
    uanset granulariteten i det adaptive datasaet."""
    return dt_str.replace("T", " ")[:13]


def report_worker(token: str, account_id: str, script: str,
                  start: str, end: str, hours: int) -> None:
    data = graphql(token, WORKERS_QUERY, {
        "accountTag": account_id, "scriptName": script,
        "start": start, "end": end,
    })
    accounts = (data.get("viewer") or {}).get("accounts") or []
    rows = accounts[0].get("workersInvocationsAdaptive", []) if accounts else []

    print(f"\n=== Worker: {script} (seneste {hours} timer) ===")
    if not rows:
        print("  Ingen data. Enten ingen trafik, eller tokenet mangler "
              "Account Analytics-laeseret.")
        return

    per_hour: dict[str, dict[str, float]] = defaultdict(
        lambda: {"requests": 0, "errors": 0, "cpu_p99": 0.0})
    per_status: dict[str, int] = defaultdict(int)
    total_req = total_err = total_sub = 0
    cpu_p50_max = cpu_p99_max = 0.0

    for row in rows:
        s = row.get("sum") or {}
        q = row.get("quantiles") or {}
        d = row.get("dimensions") or {}
        req = int(s.get("requests") or 0)
        err = int(s.get("errors") or 0)
        total_req += req
        total_err += err
        total_sub += int(s.get("subrequests") or 0)

        status = str(d.get("status") or "unknown")
        per_status[status] += req

        p50 = float(q.get("cpuTimeP50") or 0.0)
        p99 = float(q.get("cpuTimeP99") or 0.0)
        cpu_p50_max = max(cpu_p50_max, p50)
        cpu_p99_max = max(cpu_p99_max, p99)

        b = per_hour[hour_bucket(str(d.get("datetime") or ""))]
        b["requests"] += req
        b["errors"] += err
        b["cpu_p99"] = max(b["cpu_p99"], p99)

    rate = (total_err / total_req * 100) if total_req else 0.0
    print(f"  {total_req:>8} requests, {total_err} fejl ({rate:.2f}%), "
          f"{total_sub} subrequests")
    # CPU-tid er mikrosekunder. 1102 rammer ved 30 s wall / CPU-loftet, men
    # allerede en p99 der kryber opad er signalet fra 2026-07-19.
    print(f"  CPU-tid (vaerste spand): p50 {cpu_p50_max/1000:.1f} ms, "
          f"p99 {cpu_p99_max/1000:.1f} ms")

    print("  Udfald:")
    for status, n in sorted(per_status.items(), key=lambda kv: -kv[1]):
        share = (n / total_req * 100) if total_req else 0.0
        label = STATUS_LABELS.get(status, status)
        flag = "" if status in ("ok", "success") else "   <-- ikke ok"
        print(f"    {label:22} {n:>8} ({share:5.1f}%){flag}")

    print("  Pr. time:")
    print(f"    {'time (UTC)':<16} {'requests':>9} {'fejl':>6} {'fejl%':>7} "
          f"{'CPU p99':>9}")
    for bucket in sorted(per_hour):
        b = per_hour[bucket]
        req = int(b["requests"])
        err = int(b["errors"])
        pct = (err / req * 100) if req else 0.0
        mark = "  <--" if err else ""
        print(f"    {bucket:<16} {req:>9} {err:>6} {pct:>6.2f}% "
              f"{b['cpu_p99']/1000:>8.1f} ms{mark}")


def report_zone(token: str, zone_id: str, start: str, end: str,
                hours: int) -> None:
    """Bedste indsats: zone-afsnittet maa aldrig vaelte worker-afsnittet, som
    er det vigtigste. Et token uden Zone Analytics er en almindelig tilstand."""
    try:
        data = graphql(token, ZONE_QUERY, {
            "zoneTag": zone_id, "start": start, "end": end,
        })
    except RuntimeError as e:
        print(f"\n=== Zone (edge-statuskoder) ===\n  Sprunget over: {e}")
        return

    zones = (data.get("viewer") or {}).get("zones") or []
    rows = zones[0].get("httpRequestsAdaptiveGroups", []) if zones else []

    print(f"\n=== Zone: edge-statuskoder (seneste {hours} timer) ===")
    if not rows:
        print("  Ingen data.")
        return

    per_class: dict[str, int] = defaultdict(int)
    per_code: dict[int, int] = defaultdict(int)
    errors_per_hour: dict[str, int] = defaultdict(int)
    total = 0

    for row in rows:
        d = row.get("dimensions") or {}
        n = int(row.get("count") or 0)
        code = int(d.get("edgeResponseStatus") or 0)
        total += n
        per_code[code] += n
        per_class[f"{code // 100}xx"] += n
        if code >= 500:
            errors_per_hour[str(d.get("datetimeHour") or "")[:13]] += n

    print(f"  {total} requests i alt")
    for klass in sorted(per_class):
        share = (per_class[klass] / total * 100) if total else 0.0
        print(f"    {klass:>5} {per_class[klass]:>9} ({share:5.1f}%)")

    server_errors = {c: n for c, n in per_code.items() if c >= 500}
    if server_errors:
        print("  Serverfejl fordelt paa kode:")
        for code, n in sorted(server_errors.items()):
            print(f"    {code:>5} {n:>9}")
        print("  Serverfejl pr. time (UTC):")
        for bucket in sorted(errors_per_hour):
            print(f"    {bucket.replace('T', ' '):<16} {errors_per_hour[bucket]:>6}")
    else:
        print("  Ingen 5xx i vinduet.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Aggregerede Cloudflare-metrikker for MadShopper.")
    ap.add_argument("--hours", type=int, default=24,
                    help="stoerrelse paa tidsvinduet i timer (standard 24)")
    ap.add_argument("--worker", action="append", metavar="NAVN",
                    help="worker der skal rapporteres (kan gentages); "
                         "standard: madshopper og madshopper-dev")
    ap.add_argument("--no-zone", action="store_true",
                    help="spring zonens edge-statuskoder over")
    args = ap.parse_args()

    token = os.environ.get("CLOUDFLARE_API_TOKEN") or ""
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or ""
    zone_id = os.environ.get("CLOUDFLARE_ZONE_ID") or ""

    if not token or not account_id:
        print("Mangler CLOUDFLARE_API_TOKEN og/eller CLOUDFLARE_ACCOUNT_ID "
              "(saet dem i .env eller i miljoeet).", file=sys.stderr)
        return 2

    # API'et daekker en maaned ad gangen, tre maaneder tilbage - saa selv et
    # meget stort --hours er lovligt, men vi holder os til det brugeren bad om.
    hours = max(1, args.hours)
    end_dt = datetime.now(timezone.utc).replace(microsecond=0)
    start_dt = end_dt - timedelta(hours=hours)
    start = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Vindue: {start} .. {end} (UTC)")

    for script in (args.worker or DEFAULT_WORKERS):
        try:
            report_worker(token, account_id, script, start, end, hours)
        except RuntimeError as e:
            # Flush foerst, ellers lander stderr-linjen foer stdout-rapporten.
            sys.stdout.flush()
            print(f"\n=== Worker: {script} ===\n  Opslag fejlede: {e}\n"
                  "  Tokenet skal have 'Account Analytics: Read'.",
                  file=sys.stderr)
            sys.stderr.flush()

    if not args.no_zone:
        if zone_id:
            report_zone(token, zone_id, start, end, hours)
        else:
            print("\n=== Zone ===\n  Sprunget over: CLOUDFLARE_ZONE_ID ikke sat.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
