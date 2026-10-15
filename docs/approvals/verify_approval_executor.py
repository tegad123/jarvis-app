#!/usr/bin/env python3
import sys; sys.exit("RETIRED 2026-10-13: testco is retired (docs/platform/decisions.md); this script needs its running services or tree.")  # noqa: E702
"""
Approval executor — regression suite.

Part 1 (boxed, in-process): approvals.decide() against scratch request/decision
files, a scratch HERMES_HOME (so the outbox is the box's), testco's runtime
config (boundary active, suffix .example). Cases:
  approve send_email → gated_send captured it, decision record carries `send`
                       with state=captured and the outbox row is linked by run_id
  reject             → nothing executed, no outbox change, no `send` on the record
  gated_send failure → decision stays approved, `send.state == failed` with the
                       reason, ops alert on the feed (three shapes: the gate
                       raising, a recipient the boundary blocks, a preview-only
                       payload as live's owner-email files it)
  config-change      → still applied before recording (unchanged behaviour)
Part 2 (testco, :8710): approve the pending showing autoreply the injection
suite filed → captured send appears with decision linkage; a member cannot.

Usage: verify_approval_executor.py [--base http://127.0.0.1:8710] [-v]
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8710")
ap.add_argument("-v", "--verbose", action="store_true")
args = ap.parse_args()
if args.base.rstrip("/").endswith(":8700"):
    sys.exit("REFUSING: :8700 is the live tenant.")

BOX = pathlib.Path("/tmp/claude-0/-root/5856f8be-0b4c-4374-9aeb-3d70bf132d08/scratchpad/approvals-box")
shutil.rmtree(BOX, ignore_errors=True)
(BOX / "state").mkdir(parents=True)
TESTCO_SKILLS = "/opt/testco/skills/openclaw-imports"
os.environ.update({
    "HERMES_HOME": str(BOX),                       # outbox → BOX/outbox
    "JARVIS_SKILLS_ROOT": TESTCO_SKILLS,
    "JARVIS_RUNTIME_CONFIG": f"{TESTCO_SKILLS}/_shared/runtime.json",
    "JARVIS_IDENTITY_CONFIG": f"{TESTCO_SKILLS}/_shared/identity.json",
    "JARVIS_APPROVALS": str(BOX / "state" / "approvals.jsonl"),
    "JARVIS_APPROVAL_DECISIONS": str(BOX / "state" / "approvals_decisions.jsonl"),
    "JARVIS_ACTIVITY_FEED": str(BOX / "state" / "feed.jsonl"),
    "JARVIS_ALLOW_WRITES": "1",
})
sys.path.insert(0, os.environ.get("VERIFY_APP_ROOT") or str(pathlib.Path(__file__).resolve().parents[2]))
from app import approvals  # noqa: E402

ok = []
def ck(name, cond, extra=""):
    ok.append(bool(cond))
    if not cond or args.verbose:
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))
    else:
        print(f"  PASS  {name}")


def lines(p):
    p = pathlib.Path(p)
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


OUTBOX = BOX / "outbox" / "captured_mail.jsonl"
FEED = BOX / "state" / "feed.jsonl"
DEC = pathlib.Path(os.environ["JARVIS_APPROVAL_DECISIONS"])


def file_request(rid, payload, kind="action", summary="Send property info for 12 Test Ln to Sam Agent", skill="showingsmart-autoreply"):
    approvals._append_jsonl(approvals.REQUESTS_PATH, {
        "id": rid, "ts": approvals._iso_now(), "tenant": "testco", "skill": skill,
        "summary": summary, "payload": payload, "status": "pending", "kind": kind})


SEND = {"action": "send_email", "to": "sam.agent@mailbox.example", "subject": "Showing information for 12 Test Ln",
        "body": "Lockbox 1234. Park on the street.", "html_body": "<p>Lockbox 1234.</p>",
        "attachments": [], "reply_to": "dana@testco.example.com"}

print("\n=== boxed: approve send_email ===")
ck("box isolated: outbox under the box, not testco", str(OUTBOX).startswith(str(BOX)))
file_request("apr_t_approve", SEND)
o0 = len(lines(OUTBOX)); f0 = len(lines(FEED))
out = approvals.decide("apr_t_approve", "approved", "dana")
send = out.get("send") or {}
ck("decide() returns the send outcome", send.get("state") == "captured" and send.get("ok") is True, json.dumps(send)[:200])
rec = [d for d in lines(DEC) if d["id"] == "apr_t_approve"]
ck("decision record carries `send` (state, reason, to, subject)", rec and rec[-1].get("send", {}).get("state") == "captured" and rec[-1]["send"].get("to") in (SEND["to"], [SEND["to"]]), json.dumps(rec[-1] if rec else {})[:200])
cap = lines(OUTBOX)[o0:]
ck("exactly one mail captured by the boundary", len(cap) == 1, str(len(cap)))
ck("captured row is linked to the decision (run_id = approval id)", cap and cap[-1].get("run_id") == "apr_t_approve", json.dumps(cap[-1] if cap else {})[:200])
ck("captured row is the approved message (to, subject, body)", cap and cap[-1].get("to") == [SEND["to"]] and cap[-1].get("subject") == SEND["subject"] and SEND["body"] in (cap[-1].get("body") or ""), json.dumps(cap[-1] if cap else {})[:160])
ck("feed: 'Sent the approved email — <summary>'", any(e.get("headline", "").startswith("Sent the approved email — Send property info for 12 Test Ln") for e in lines(FEED)[f0:]), str([e.get("headline") for e in lines(FEED)[f0:]]))
ck("load_all overlays the send outcome on the item", (approvals.get("apr_t_approve") or {}).get("send", {}).get("state") == "captured")
try:
    approvals.decide("apr_t_approve", "approved", "dana"); twice = False
except ValueError:
    twice = True
ck("a second approve is refused (already approved) — no second send", twice and len(lines(OUTBOX)) == o0 + 1)

print("\n=== boxed: reject send_email ===")
file_request("apr_t_reject", SEND)
o0 = len(lines(OUTBOX)); f0 = len(lines(FEED))
out = approvals.decide("apr_t_reject", "rejected", "dana")
rec = [d for d in lines(DEC) if d["id"] == "apr_t_reject"]
ck("reject → nothing sent, nothing captured", len(lines(OUTBOX)) == o0)
ck("reject → decision record has no `send`", rec and "send" not in rec[-1] and rec[-1]["decision"] == "rejected", json.dumps(rec[-1] if rec else {}))
ck("reject → no feed line about sending", not any("approved email" in e.get("headline", "") for e in lines(FEED)[f0:]))

print("\n=== boxed: gated_send failure paths ===")
# (a) the gate raises
file_request("apr_t_raise", SEND)
o0 = len(lines(OUTBOX)); f0 = len(lines(FEED))
sg = approvals._send_gate()
orig = sg.gated_send
def boom(*a, **k):
    raise RuntimeError("SMTP unreachable (simulated)")
sg.gated_send = boom
try:
    out = approvals.decide("apr_t_raise", "approved", "dana")
finally:
    sg.gated_send = orig
rec = [d for d in lines(DEC) if d["id"] == "apr_t_raise"]
ck("gate raises → decision still recorded as approved", rec and rec[-1]["decision"] == "approved")
ck("gate raises → send.state == failed with the reason", rec and rec[-1].get("send", {}).get("state") == "failed" and "SMTP unreachable" in rec[-1]["send"].get("reason", ""), json.dumps(rec[-1].get("send") if rec else {})[:200])
ck("gate raises → nothing captured", len(lines(OUTBOX)) == o0)
alerts = [e for e in lines(FEED)[f0:] if e.get("kind") == "alert"]
ck("gate raises → ops ALERT on the feed naming the failure", any(e.get("headline", "").startswith("Couldn't send the approved email") and "SMTP unreachable" in e.get("detail", "") for e in alerts), str([(e.get("headline"), e.get("detail", "")[:80]) for e in alerts]))
ck("failure surfaces on the item via load_all", (approvals.get("apr_t_raise") or {}).get("send", {}).get("state") == "failed")

# (b) recipient outside the boundary → gate returns blocked
file_request("apr_t_blocked", {**SEND, "to": "someone@gmail.com"})
o0 = len(lines(OUTBOX)); f0 = len(lines(FEED))
out = approvals.decide("apr_t_blocked", "approved", "dana")
rec = [d for d in lines(DEC) if d["id"] == "apr_t_blocked"]
# the boundary REFUSES (raises RecipientRefused) rather than returning a blocked dict — either way it must land on the record as not-ok with the reason
ck("boundary refuses the recipient → send not ok, state blocked/failed, reason names the refusal", rec and rec[-1].get("send", {}).get("state") in ("blocked", "failed") and rec[-1]["send"].get("ok") is False and "Refused" in rec[-1]["send"].get("reason", "") + rec[-1]["send"].get("state", ""), json.dumps(rec[-1].get("send") if rec else {})[:200])
ck("blocked → nothing captured, nothing sent", len(lines(OUTBOX)) == o0)
ck("blocked → ops alert on the feed", any(e.get("headline", "").startswith("Couldn't send the approved email") for e in lines(FEED)[f0:] if e.get("kind") == "alert"))

# (c) preview-only payload, as live's owner-email files it
file_request("apr_t_preview", {"action": "send_email", "to": "sam.agent@mailbox.example", "subject": "Follow-up", "body_preview": "Hi Sam, just checking…"}, skill="owner-email", summary="Send a follow-up to Sam Agent")
o0 = len(lines(OUTBOX)); f0 = len(lines(FEED))
out = approvals.decide("apr_t_preview", "approved", "dana")
rec = [d for d in lines(DEC) if d["id"] == "apr_t_preview"]
ck("preview-only payload → failed, not sent from a preview", rec and rec[-1].get("send", {}).get("state") == "failed" and "preview" in rec[-1]["send"].get("reason", ""), json.dumps(rec[-1].get("send") if rec else {})[:200])
ck("preview-only → nothing captured", len(lines(OUTBOX)) == o0)

print("\n=== boxed: other kinds untouched ===")
file_request("apr_t_event", {"action": "create_event", "title": "Showing"}, summary="Add a showing to the calendar", skill="email-to-calendar")
o0 = len(lines(OUTBOX))
out = approvals.decide("apr_t_event", "approved", "dana")
rec = [d for d in lines(DEC) if d["id"] == "apr_t_event"]
ck("approving a non-send action executes nothing and records no `send`", rec and "send" not in rec[-1] and len(lines(OUTBOX)) == o0)

# ── Part 2: testco through the API ───────────────────────────────────────────
print("\n=== testco (:8710): approve the pending showing autoreply ===")
BASE = args.base.rstrip("/")
HOME = pathlib.Path("/opt/testco/hermes")
T_OUTBOX = HOME / "outbox" / "captured_mail.jsonl"
T_DEC = HOME / "state" / "approvals_decisions.jsonl"
T_FEED = HOME / "state" / "activity_feed.jsonl"
PINS = json.loads(pathlib.Path("/opt/testco/SEEDED_PINS.json").read_text())
SCR = BOX / "jars"; SCR.mkdir()


def call(path, method="GET", body=None, jar=None):
    out = SCR / "body.json"
    cmd = ["curl", "-s", "-o", str(out), "-w", "%{http_code}", "-X", method, BASE + path]
    if jar:
        cmd += ["-b", jar, "-c", jar]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    code = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    try:
        data = json.loads(out.read_text())
    except Exception:
        data = {}
    return int(code or 0), data


def login(who):
    jar = str(SCR / f"{who}.jar")
    code, d = call("/api/login", "POST", {"person_key": who, "pin": PINS[who]}, jar)
    if not d.get("ok"):
        sys.exit(f"login {who} failed: {code} {d}")
    return jar


def passphrase():
    for line in (HOME / ".env").read_text().splitlines():
        if line.strip().startswith("APP_PASSPHRASE="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


# make sure there is a pending showing approval to act on: inject a fresh one
subprocess.run(["/opt/testco/bin/testco-inject", "showing", "showingsmart_inbound.eml", "--fresh", "--origin", "verify-approvals"], capture_output=True, text=True, timeout=120)
pend = [a for a in lines(HOME / "state" / "approvals.jsonl") if a.get("skill") == "showingsmart-autoreply"]
latest = approvals._latest_decisions() if False else {d["id"]: d for d in lines(T_DEC)}
pend = [a for a in pend if a["id"] not in latest]
ck("a pending showing approval exists on testco", bool(pend), str(len(pend)))
rid = pend[-1]["id"] if pend else None
o0 = len(lines(T_OUTBOX)); f0 = len(lines(T_FEED))
code, d = call(f"/api/approvals/{rid}", "POST", {"decision": "approve"}, login("omar"))
ck("member cannot approve (403)", code == 403, str(code))
code, d = call(f"/api/approvals/{rid}", "POST", {"decision": "approve"}, login("dana"))
ck("owner approves → 200 with the send outcome in the response", code == 200 and d.get("approval", {}).get("send", {}).get("state") == "captured", f"{code} {json.dumps(d)[:220]}")
time.sleep(0.5)
cap = lines(T_OUTBOX)[o0:]
ck("captured send appears in testco's outbox, linked by run_id", len(cap) == 1 and cap[0].get("run_id") == rid and "918 Ravenscourt" in cap[0].get("subject", ""), json.dumps(cap[-1] if cap else {})[:200])
rec = [x for x in lines(T_DEC) if x["id"] == rid]
ck("testco decision record carries the send outcome", rec and rec[-1].get("send", {}).get("state") == "captured")
ck("feed: 'Sent the approved email — Send property info for 918 Ravenscourt Rd…'", any(e.get("headline", "").startswith("Sent the approved email — Send property info for 918 Ravenscourt Rd") for e in lines(T_FEED)[f0:]), str([e.get("headline") for e in lines(T_FEED)[f0:]][-3:]))
code, d = call("/api/approvals", jar=login("dana"))
item = next((a for a in d.get("approvals", d.get("items", [])) if a.get("id") == rid), None) if isinstance(d, dict) else None
ck("GET /api/approvals shows the item with its send state", item is not None and item.get("send", {}).get("state") == "captured", json.dumps(item)[:160] if item else str(d)[:120])
code, d = call(f"/api/operator/outbox?limit=1", jar=None)
ck("operator outbox route exists (401 without a session)", code == 401, str(code))

n = sum(ok)
print(f"\n{n}/{len(ok)} PASS")
sys.exit(0 if n == len(ok) else 1)
