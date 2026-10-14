#!/usr/bin/env python3
"""An approved send_email is executed from its full body, through the platform
database's approval queue.

  boxed, enforce: seed -> approve -> captured mail has the whole message
  boxed, shadow:  the same request approved is held, not captured; the held
                  proposal keeps the whole body and the feed says "would have"
  API (own sandbox on 8719): a regenerated request renders with its full
                  body for the owner and executes through POST /api/approvals

    /opt/jarvis/venv/bin/python docs/approvals/verify_fullbody_fixture.py
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve()
APP = os.environ.get("VERIFY_APP_ROOT") or str(HERE.parents[2])
CFG = "/opt/jarvis-verify-fixture/skills/openclaw-imports"   # config: the frozen fixture (read-only)
SK = os.environ.get("VERIFY_SKILLS_ROOT") or CFG              # code under test
PY = sys.executable
PORT = 8719
VDB = "jarvis_verify_fullbody"
BOX = pathlib.Path("/tmp/claude-0/-root/fullbody-box")
shutil.rmtree(BOX, ignore_errors=True)
(BOX / "state").mkdir(parents=True)

sys.path.insert(0, str(HERE.parents[1] / "broker"))
sys.path.insert(0, str(HERE.parents[1] / "sentinel"))
import verify_broker as vb  # noqa: E402
import verify_sentinel_broker as vsb  # noqa: E402

dsn = subprocess.run([PY, f"{APP}/scripts/pg_tenant.py", "verify-db", "create", VDB],
                     capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
os.environ.update({"HERMES_HOME": str(BOX), "JARVIS_SKILLS_ROOT": SK, "JARVIS_RUNTIME_CONFIG": f"{CFG}/_shared/runtime.json",
                   "JARVIS_IDENTITY_CONFIG": f"{CFG}/_shared/identity.json", "JARVIS_ACTIVITY_FEED": str(BOX / "state/feed.jsonl"),
                   "JARVIS_THREADS_DB": str(BOX / "state/threads.db"), "JARVIS_ALLOW_WRITES": "1",
                   "JARVIS_PG_DSN": dsn, "JARVIS_SENTINEL_MODE": "enforce"})
sys.path.insert(0, APP)
from app import approvals, platform_store  # noqa: E402

ok = []


def ck(n, c, x=""):
    ok.append(bool(c))
    print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" — {x}" if x and not c else ""))


def lines(p):
    p = pathlib.Path(p)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


print("=== boxed, enforce: seed → approve ===")
ck("seed files the four fixtures", approvals.seed() == 4)
w = [a for a in platform_store.list_approvals() if a["summary"].startswith("Send a follow-up to William")]
ck("seeded fixture present, pending, origin seed", len(w) == 1 and w[0]["status"] == "pending" and w[0]["origin"] == "seed")
pl = w[0]["action_json"]
ck("payload carries the full body, no body_preview",
   "body" in pl and "body_preview" not in pl and pl["body"].endswith("Best,\nSpencer"), str(sorted(pl)))
ck("seed is idempotent", approvals.seed() == 0)
out = approvals.decide(w[0]["id"], "approved", "dana")
send = out.get("send") or {}
ck("approve → captured (boundary; wmchenry@example.com is inside .example.com)", send.get("state") == "captured",
   json.dumps(out, default=str)[:300])
cap = lines(BOX / "outbox/captured_mail.jsonl")
ck("captured mail body is the WHOLE message", cap and cap[-1]["body"] == pl["body"] and "second walk-" in cap[-1]["body"],
   cap[-1]["body"][-60:] if cap else "")
ck("…once", len(cap) == 1)
ck("audit: the approval was recorded as approved",
   any(a["event"] == "approval" and a["outcome"] == "approved" and a["approval_id"] == w[0]["id"]
       for a in platform_store.recent_actions(50)))

print("=== boxed, shadow: the same request is held, with its whole body ===")
os.environ["JARVIS_SENTINEL_MODE"] = "shadow"
rid = platform_store.create_approval(origin="producer", user_id="owner", kind="action", skill="owner-email",
                                     summary=w[0]["summary"], action=pl, reason="verify shadow")
out = approvals.decide(rid, "approved", "dana")
send = out.get("send") or {}
ck("approve in shadow → held", send.get("state") == "held", json.dumps(send, default=str)[:300])
ck("…nothing captured", len(lines(BOX / "outbox/captured_mail.jsonl")) == 1)
held = platform_store.get_held(send.get("action_id") or 0) if send.get("action_id") else None
ck("held proposal keeps the whole body", held and (held["action_json"].get("content") or {}).get("body") == pl["body"], str(held)[:200])
ck("…and names its source record (approval:<id>)",
   held and (held["source_kind"], held["source_id"]) == ("approval", rid), str(held)[:200])
wh = [f for f in lines(BOX / "state/feed.jsonl") if f.get("kind") == "sentinel.would_have"]
ck("feed: one 'would have' line for it", len(wh) == 1 and "would have" in wh[0].get("headline", ""), str(wh)[:300])
ck("…which carries no body", wh and pl["body"][:40] not in json.dumps(wh))
os.environ["JARVIS_SENTINEL_MODE"] = "enforce"

print("=== API: a regenerated request on the sandbox queue ===")
env = None
os.environ.pop("JARVIS_PG_DSN", None)
try:
    box, env = vb.start_sandbox("fullbody", PORT, "1", "http://127.0.0.1:9/")
    os.environ["JARVIS_PG_DSN"] = (box / "pg_dsn").read_text().strip()
    rid = platform_store.create_approval(origin="producer", user_id="owner", kind="action", skill="owner-email",
                                         summary="Send a follow-up to William Mchenry about 609 25th St", action=pl)
    dana = vsb.Session(PORT, "dana")
    code, d = dana.call("/api/approvals")
    it = next((a for a in d.get("approvals", []) if a.get("id") == rid), None)
    ck("GET /api/approvals returns it pending with the full body in payload",
       it and it["status"] == "pending" and it["payload"].get("body") == pl["body"], str(it or d)[:160])
    o0 = len(lines(box / "hermes/outbox/captured_mail.jsonl"))
    code, d = dana.call(f"/api/approvals/{rid}", {"decision": "approve"})
    st = ((d.get("approval") or {}).get("send") or {}).get("state")
    ck("owner approves → 200, send captured", code == 200 and st == "captured", f"{code} {json.dumps(d)[:200]}")
    cap = lines(box / "hermes/outbox/captured_mail.jsonl")[o0:]
    ck("sandbox outbox row: full body, run_id = approval id",
       len(cap) == 1 and cap[0]["body"] == pl["body"] and cap[0].get("run_id") == rid, str(cap)[:200])
finally:
    if env:
        vb.stop_sandbox(env)
    subprocess.run([PY, f"{APP}/scripts/pg_tenant.py", "verify-db", "drop", VDB], capture_output=True)

print(f"\n{sum(ok)}/{len(ok)} PASS")
sys.exit(0 if all(ok) else 1)
