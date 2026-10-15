#!/usr/bin/env python3
"""A tenant's agent chats: the real Ask round trip on a provisioned tenant.

Run as root by the lead agent. Nothing is stubbed: a message goes in through
the tenant app's POST /api/chat as its owner, reaches the tenant's Hermes
gateway, the gateway calls Claude through the app's model proxy, and the model
decides to run the task-manager skill. Asserted on outcomes, never wording:

  - the reply streams back and the transcript holds an assistant turn;
  - a task with source 'chat' and the asked-for street appears in the
    tenant's own tasks database (an internal record: allowed in practice mode);
  - the model usage ledger in the tenant's Postgres database grew (requests,
    tokens and a priced cost for the agent model), so the cost guard sees it;
  - nothing left Jarvis: no audit row with outcome 'executed' was added.

The owner PIN is read from a root-only file (accounts_cli set-pin output) and
never printed.

    python3 docs/model/verify_tenant_chat.py --slug isocheck \
        --pin-file /root/onboarding/isocheck-owner-pin.txt [--tasks-db PATH] [--pg-db NAME]

--tasks-db and --pg-db exist for canaries: pointing either at another tenant
must turn the matching checks red.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

import httpx
import psycopg
import yaml

PASS = FAIL = 0


def check(label: str, ok: bool) -> bool:
    global PASS, FAIL
    PASS, FAIL = PASS + bool(ok), FAIL + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def runtime(slug: str) -> dict:
    return json.loads(Path(f"/opt/{slug}/skills/openclaw-imports/_shared/runtime.json").read_text())


def pin_from(path: Path) -> str:
    text = path.read_text()
    m = re.search(r"PIN FOR \S+ .*?\n\s*\n\s+(\S+)\s*\n", text, re.S)
    if not m:
        sys.exit(f"no PIN found in {path} (expected accounts_cli set-pin output)")
    return m.group(1)


def chat_rows(db: Path) -> list[dict]:
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM tasks WHERE source = 'chat'")]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def pg(dbname: str, sql: str, args=()) -> list:
    with psycopg.connect(host="/var/run/postgresql", user="postgres", dbname=dbname) as c:
        return c.execute(sql, args).fetchall()


def usage(dbname: str, model: str) -> tuple:
    rows = pg(dbname, "SELECT coalesce(sum(requests),0), coalesce(sum(input_tokens + output_tokens),0), "
                      "coalesce(sum(cost_usd),0)::float, coalesce(sum(unpriced_requests),0) FROM usage "
                      "WHERE model = %s AND purpose = 'agent' AND day = (now() AT TIME ZONE 'UTC')::date",
              (model,))
    return tuple(rows[0])


def executed(dbname: str) -> int:
    return pg(dbname, "SELECT count(*) FROM actions WHERE outcome = 'executed'")[0][0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--pin-file", required=True, type=Path)
    ap.add_argument("--person", default="owner")
    ap.add_argument("--tasks-db", type=Path, default=None)
    ap.add_argument("--pg-db", default=None)
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()
    if os.geteuid() != 0:
        print("run as root")
        return 2
    slug = a.slug
    if slug in ("live", "jarvis") or not Path(f"/opt/{slug}/app-private").is_dir():
        print(f"/opt/{slug} is not a two-user tenant")
        return 2
    rt = runtime(slug)
    port = int(re.search(r"127\.0\.0\.1:(\d+)/api/model",
                         Path(f"/opt/{slug}/hermes/config.yaml").read_text()).group(1))
    if port in (8700, 8710):
        print("REFUSING: live or testco port")
        return 2
    model = (rt.get("models") or {}).get("agent") or ""
    tasks_db = a.tasks_db or Path(rt["paths"]["tasks_db"])
    dbname = a.pg_db or f"jarvis_{slug}"
    print(f"tenant {slug}: app :{port}, agent model {model}, tasks {tasks_db}, ledger {dbname}")

    street = f"{random.randint(1000, 9999)} {random.choice(('Meridian St', 'Harborline Ave', 'Copperleaf Dr'))}"
    phrase = f"Track this: {random.choice(('order the survey for', 'schedule the inspection for'))} {street}"
    before_ids = {r["task_id"] for r in chat_rows(tasks_db)}
    u0, x0 = usage(dbname, model), executed(dbname)

    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=a.timeout)
    r = c.post("/api/login", json={"person_key": a.person, "pin": pin_from(a.pin_file)})
    check(f"owner signs in to the tenant app (HTTP {r.status_code})", r.status_code == 200)
    c.headers["cookie"] = "; ".join(f"{k}={v}" for k, v in r.cookies.items())
    c.cookies.clear()
    r = c.post("/api/threads", json={"name": f"verify-chat-{os.urandom(3).hex()}"})
    tid = ((r.json() if r.status_code == 201 else {}).get("thread") or {}).get("thread_id")
    check(f"a fresh thread to talk in (HTTP {r.status_code})", bool(tid))
    if not tid:
        print(f"\n{PASS}/{PASS + FAIL} passed")
        return 1

    started = time.time()
    events, text, verified = [], "", []
    with c.stream("POST", "/api/chat", json={"thread_id": tid, "message": phrase}) as resp:
        check(f"the message is accepted (HTTP {resp.status_code})", resp.status_code == 200)
        for line in resp.iter_lines():
            if line.strip():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                events.append(ev.get("type"))
                if ev.get("type") == "verified":
                    verified.append(ev)
                text += ev.get("delta") or ev.get("text") or ev.get("content") or ""
    kinds = sorted({k for k in events if k})
    check(f"the reply streams back with no error event (event types {kinds})",
          bool(events) and "error" not in kinds)

    check(f"the app verified the model's task claim against the store ({[v.get('ok') for v in verified]})",
          bool(verified) and all(v.get("ok") for v in verified))
    found = None
    while time.time() - started < a.timeout and not found:
        found = next((r for r in chat_rows(tasks_db)
                      if r["task_id"] not in before_ids and street.lower() in (r["title"] or "").lower()), None)
        if not found:
            time.sleep(3)
    check("a task asked for in chat appears in the tenant's own task list"
          + (f" ({found['title']})" if found else " (none within the timeout)"), found is not None)
    if found:
        check("   recorded as coming from chat, with a reference back", found["source"] == "chat"
              and bool(found.get("source_ref")))
    hist = c.get("/api/chat/history", params={"thread_id": tid}).json().get("messages") or []
    said = " ".join(m.get("content", "") for m in hist if m.get("role") == "assistant")
    check(f"the transcript holds the assistant's answer ({len(said)} chars)", len(said) > 0)

    u1, x1 = usage(dbname, model), executed(dbname)
    d_req, d_tok, d_cost = u1[0] - u0[0], u1[1] - u0[1], u1[2] - u0[2]
    check(f"model usage ledger grew for {model}: +{d_req} request(s), +{d_tok} tokens", d_req >= 1 and d_tok > 0)
    check(f"   and it is priced, not unpriced: +${d_cost:.6f}, unpriced +{u1[3] - u0[3]}",
          d_cost > 0 and u1[3] == u0[3])
    check(f"nothing left Jarvis: executed audit rows {x0} -> {x1}", x1 == x0)
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
