#!/usr/bin/env python3
"""Henter ventende feedback fra D1 (pending_feedback) og sender den videre til
Google Sheet-webhooken. Kører periodisk via GitHub Actions — uden om Cloudflare
Workers' synkrone/tråd-begrænsninger, som gjorde at kaldet enten forsvandt
stille eller gav 503 når det blev forsøgt direkte fra Workeren."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import httpx

DB_NAME = "madshopper"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL")


def run_wrangler_sql(sql: str) -> list[dict]:
    # --file+--json returnerer kun udførelsesstatistik (ikke rækkedata) i denne
    # wrangler-version — --command giver de faktiske rækker.
    result = subprocess.run(
        ["npx", "wrangler@4", "d1", "execute", DB_NAME, "--remote", f"--command={sql}", "--json"],
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
    run_wrangler_sql(
        "CREATE TABLE IF NOT EXISTS pending_feedback ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, feedback_type TEXT, name TEXT, "
        "email TEXT, subject TEXT, message TEXT, page_url TEXT, created_at TEXT);"
    )


def _is_feedback_row(row: object) -> bool:
    """Kun rigtige D1-rækker — ikke wrangler-statistik ved fejl."""
    if not isinstance(row, dict):
        return False
    if row.get("id") is None:
        return False
    return bool((row.get("message") or "").strip())


def _row_payload(row: dict) -> dict:
    return {
        "type": row.get("feedback_type") or "feedback",
        "name": row.get("name") or "",
        "email": row.get("email") or "",
        "subject": row.get("subject") or "",
        "message": row.get("message") or "",
        "page_url": row.get("page_url") or "",
        "created_at": row.get("created_at") or "",
    }


def main() -> int:
    if not WEBHOOK_URL:
        print("GOOGLE_SHEET_WEBHOOK_URL ikke sat — afbryder.")
        return 1

    ensure_schema()
    rows = run_wrangler_sql("SELECT * FROM pending_feedback ORDER BY id ASC LIMIT 200;")
    valid = [r for r in rows if _is_feedback_row(r)]
    skipped = len(rows) - len(valid)
    if skipped:
        print(f"advarsel: sprang {skipped} ugyldig(e) række(r) over (mangler id/besked).")

    if not valid:
        print("Ingen ventende feedback.")
        return 0

    print(f"{len(valid)} ventende feedback-række(r) fundet.")
    sent_ids: list[int] = []
    for row in valid:
        rid = int(row["id"])
        payload = _row_payload(row)
        try:
            resp = httpx.post(WEBHOOK_URL, json=payload, timeout=15.0, follow_redirects=True)
            resp.raise_for_status()
            sent_ids.append(rid)
            print(f"  sendt id={rid} ({payload['type']!r}, {len(payload['message'])} tegn)")
        except Exception as e:
            print(f"  fejl ved id={rid}: {e}")

    if sent_ids:
        ids_sql = ",".join(str(i) for i in sent_ids)
        run_wrangler_sql(f"DELETE FROM pending_feedback WHERE id IN ({ids_sql});")
        print(f"Sendt og ryddet {len(sent_ids)} feedback-række(r).")

    failed = len(valid) - len(sent_ids)
    if failed:
        print(f"advarsel: {failed} række(r) fejlede og prøves igen ved næste kørsel.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
