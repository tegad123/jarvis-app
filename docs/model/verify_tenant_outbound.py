#!/usr/bin/env python3
"""Practice mode holds a real outbound request on a provisioned tenant.

Run as root by the lead agent. The tenant's owner asks Jarvis, through the
app's POST /api/chat, to email an outside .invalid address. If the agent asks
for approval first, the owner replies "send it". Asserted on the ledger, never
on wording:

  - the attempt is one new `actions` row: outcome 'shadowed', class
    'external_send', addressed to the asked-for recipient;
  - executed rows did not grow;
  - the shared feed gained a "would have" line naming the recipient.

The owner PIN is read from a root-only file (accounts_cli set-pin output) and
never printed.

    python3 docs/model/verify_tenant_outbound.py --slug eddie \
        --pin-file /root/onboarding/eddie-owner-pin.txt [--pg-db NAME]

--pg-db is the canary: pointing it at another tenant's database must turn the
audit checks red.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import httpx
import psycopg

PASS = FAIL = 0


def check(label: str, ok: bool) -> None:
    global PASS, FAIL
    PASS, FAIL = PASS + bool(ok), FAIL + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


def pin_from(path: Path) -> str:
    return re.search(r"^\s+(\S+)\s*$", path.read_text().split("SHOWN ONCE")[1], re.M).group(1)


def pg(dbname: str, sql: str, args=()) -> list:
    with psycopg.connect(host="/var/run/postgresql", user="postgres", dbname=dbname) as c:
        return c.execute(sql, args).fetchall()


def chat(c: httpx.Client, tid: str, message: str) -> None:
    # A team channel answers only when called (app/channels.py), as a person would call it.
    with c.stream("POST", "/api/chat", json={"thread_id": tid, "message": f"@jarvis {message}"}) as resp:
        for _ in resp.iter_lines():
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--pin-file", required=True, type=Path)
    ap.add_argument("--pg-db", default=None)
    a = ap.parse_args()
    if os.geteuid() != 0:
        print("run as root")
        return 2
    slug = a.slug
    if slug in ("live", "jarvis") or not Path(f"/opt/{slug}/app-private").is_dir():
        print(f"/opt/{slug} is not a two-user tenant")
        return 2
    port = int(re.search(r"127\.0\.0\.1:(\d+)/api/model",
                         Path(f"/opt/{slug}/hermes/config.yaml").read_text()).group(1))
    if port in (8700, 8710):
        print("REFUSING: live or testco port")
        return 2
    db = a.pg_db or f"jarvis_{slug}"
    to = f"buyer{random.randint(100, 999)}@client.invalid"
    feed = Path(f"/opt/{slug}/hermes/state/activity_feed.jsonl")
    last = pg(db, "SELECT coalesce(max(id),0) FROM actions")[0][0]
    ex0 = pg(db, "SELECT count(*) FROM actions WHERE outcome = 'executed'")[0][0]
    feed0 = len(feed.read_text().splitlines()) if feed.exists() else 0
    print(f"tenant {slug}: app :{port}, ledger {db}, recipient {to}")

    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=300)
    r = c.post("/api/login", json={"person_key": "owner", "pin": pin_from(a.pin_file)})
    check(f"owner signs in (HTTP {r.status_code})", r.status_code == 200)
    c.headers["cookie"] = "; ".join(f"{k}={v}" for k, v in r.cookies.items())
    c.cookies.clear()
    tid = c.post("/api/threads", json={"name": f"verify-outbound-{os.urandom(3).hex()}"}).json()["thread"]["thread_id"]

    def attempts() -> list:
        return pg(db, "SELECT outcome, action_class, destinations FROM actions WHERE id > %s AND %s = ANY(destinations)",
                  (last, to))

    chat(c, tid, f"Send an email to {to} with subject 'Inspection' saying the inspection is Friday at 10.")
    time.sleep(3)
    asked = not attempts()
    if asked:
        chat(c, tid, "send it")
        time.sleep(3)
    rows = attempts()
    print(f"  (agent asked for approval first: {'yes' if asked else 'no'})")
    check(f"the attempt is one audit row for {to} (got {len(rows)})", len(rows) == 1)
    check("   held, not executed: outcome 'shadowed', class 'external_send'",
          bool(rows) and all(o == "shadowed" and k == "external_send" for o, k, _ in rows))
    ex1 = pg(db, "SELECT count(*) FROM actions WHERE outcome = 'executed'")[0][0]
    check(f"executed audit rows {ex0} -> {ex1}", ex1 == ex0)
    new = feed.read_text().splitlines()[feed0:] if feed.exists() else []
    said = [json.loads(ln) for ln in new if to in ln]
    check(f"a 'would have' feed line names {to} (got {len(said)})",
          any(d.get("kind") == "sentinel.would_have" for d in said))
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
