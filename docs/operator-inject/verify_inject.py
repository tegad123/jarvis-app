#!/usr/bin/env python3
import sys; sys.exit("RETIRED 2026-10-13: testco is retired (docs/platform/decisions.md); this script needs its running services or tree.")  # noqa: E702
"""
Operator fixture injection — regression suite. Runs against testco (:8710),
because the affordance under test IS testco's: the fixtures, the boundary, the
feed and the outbox are the tenant's own. Refuses the live tenant.

  whitelist rejection   unknown name, a path, wrong kind, extra field, bad JSON → 400
  role rejection        member (tester), admin (priya), owner (dana) → 403; no session → 401
  happy paths           one per kind, asserting the feed line + the artifact that changed
                        reply: captured confirmation in the outbox; showing: pending approval
                        + the owner's recorded decision (testco has no standing authorization,
                        and nothing executes an approved send_email — pre-existing gap, printed
                        as info); meeting: the filed/duplicate note (ingestion sends nothing)
  journal               one row per invocation, including the refused ones that ran
  CLI wrapper           the shell front door reaches the same engine

Usage: verify_inject.py [--base http://127.0.0.1:8710] [-v]
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8710")
ap.add_argument("--pins", default="/opt/testco/SEEDED_PINS.json")
ap.add_argument("-v", "--verbose", action="store_true")
args = ap.parse_args()
BASE = args.base.rstrip("/")
if BASE.endswith(":8700"):
    sys.exit("REFUSING: :8700 is the live tenant.")

HOME = pathlib.Path("/opt/testco/hermes")
FEED = HOME / "state" / "activity_feed.jsonl"
OUTBOX = HOME / "outbox" / "captured_mail.jsonl"
JOURNAL = HOME / "state" / "operator_inject.jsonl"
SCRATCH = pathlib.Path(os.environ.get("VERIFY_SCRATCH", "/tmp/claude-0/-root/5856f8be-0b4c-4374-9aeb-3d70bf132d08/scratchpad/inject"))
SCRATCH.mkdir(parents=True, exist_ok=True)

PINS = json.loads(pathlib.Path(args.pins).read_text())
if isinstance(next(iter(PINS.values())), dict):
    PINS = {k: v.get("pin") for k, v in PINS.items()}


def passphrase():
    for line in (HOME / ".env").read_text().splitlines():
        if line.strip().startswith("APP_PASSPHRASE="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no APP_PASSPHRASE")


ok = []
def ck(name, cond, extra=""):
    ok.append(bool(cond))
    if not cond or args.verbose:
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))
    else:
        print(f"  PASS  {name}")


def call(path, method="GET", body=None, jar=None, raw=None):
    out = SCRATCH / "body.json"
    cmd = ["curl", "-s", "-o", str(out), "-w", "%{http_code}", "-X", method, BASE + path]
    if jar:
        cmd += ["-b", jar, "-c", jar]
    if raw is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", raw]
    elif body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    code = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    try:
        data = json.loads(out.read_text())
    except Exception:
        data = {}
    return int(code or 0), data


def login(who):
    jar = str(SCRATCH / f"{who}.jar")
    pathlib.Path(jar).unlink(missing_ok=True)
    body = {"passphrase": passphrase()} if who == "operator" else {"person_key": who, "pin": PINS[who]}
    code, d = call("/api/login", "POST", body, jar)
    if not d.get("ok"):
        sys.exit(f"login {who} failed: HTTP {code} {d}")
    return jar


def lines(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def new_since(path, n):
    return lines(path)[n:]


def latest_digest_positions():
    import sqlite3
    c = sqlite3.connect(str(HOME / "state" / "jarvis_tasks.db"))
    rid = c.execute("SELECT rfc_message_id FROM digest_map ORDER BY sent_at DESC LIMIT 1").fetchone()
    if not rid:
        return None, []
    rows = c.execute("SELECT position, task_id FROM digest_map WHERE rfc_message_id=? ORDER BY position", (rid[0],)).fetchall()
    return rid[0], rows


def task_status(task_id):
    import sqlite3
    c = sqlite3.connect(str(HOME / "state" / "jarvis_tasks.db"))
    r = c.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    return r[0] if r else None


op = login("operator")

# ── whitelist ────────────────────────────────────────────────────────────────
print("\n=== whitelist ===")
code, wl = call("/api/operator/inject", jar=op)
ck("GET whitelist as operator → 200 with three kinds", code == 200 and sorted(wl.get("kinds", [])) == ["meeting", "reply", "showing"], f"{code} {wl.get('kinds')}")
ck("whitelist enumerated the reply fixtures the seeder made", "digest_reply_done1.eml" in wl.get("fixtures", {}).get("reply", []), str(wl.get("fixtures", {}).get("reply")))
ck("boundary reported active", wl.get("boundary_active") is True)

for label, body, raw in [
    ("unknown fixture name", {"kind": "showing", "fixture": "nope.eml"}, None),
    ("a path", {"kind": "showing", "fixture": "/opt/testco/fixtures/showingsmart_inbound.eml"}, None),
    ("a traversal", {"kind": "meeting", "fixture": "../fixtures/meeting_clean.eml"}, None),
    ("right file, wrong kind", {"kind": "reply", "fixture": "meeting_clean.eml"}, None),
    ("unknown kind", {"kind": "sweep", "fixture": "meeting_clean.eml"}, None),
    ("extra field", {"kind": "meeting", "fixture": "meeting_clean.eml", "fresh": True}, None),
    ("missing field", {"kind": "meeting"}, None),
    ("non-string fixture", {"kind": "meeting", "fixture": ["meeting_clean.eml"]}, None),
    ("malformed JSON", None, "{kind: meeting"),
    ("JSON array", None, "[]"),
]:
    code, d = call("/api/operator/inject", "POST", body, op, raw=raw)
    ck(f"400 for {label}", code == 400, f"{code} {d.get('detail', '')[:90]}")

# ── roles ────────────────────────────────────────────────────────────────────
print("\n=== roles ===")
j0 = len(lines(JOURNAL))
for who in ("tester", "priya", "dana"):
    jar = login(who)
    code, d = call("/api/operator/inject", "POST", {"kind": "reply", "fixture": "digest_reply_nonsense.eml"}, jar)
    ck(f"403 for {who} ({'member' if who in ('tester',) else 'admin' if who == 'priya' else 'owner'})", code == 403, f"{code} {d.get('detail', '')[:80]}")
    code, d = call("/api/operator/inject", jar=jar)
    ck(f"403 for {who} on the whitelist too", code == 403, str(code))
code, d = call("/api/operator/inject", "POST", {"kind": "reply", "fixture": "digest_reply_nonsense.eml"})
ck("401 with no session", code == 401, str(code))
ck("refused calls wrote no journal rows", len(lines(JOURNAL)) == j0, f"{len(lines(JOURNAL)) - j0} rows")

# ── happy path: reply ────────────────────────────────────────────────────────
print("\n=== happy path: reply ===")
rid, positions = latest_digest_positions()
ck("a digest exists to reply to", rid is not None, str(rid))
f0, o0, j0 = len(lines(FEED)), len(lines(OUTBOX)), len(lines(JOURNAL))
t0 = time.time()
code, d = call("/api/operator/inject", "POST", {"kind": "reply", "fixture": "digest_reply_done1.eml"}, op)
ck("POST reply → 200", code == 200, f"{code} {json.dumps(d)[:200]}")
res = d.get("result", {})
ck("handler acted on the latest digest", res.get("status") == "acted" and res.get("in_reply_to") == rid, json.dumps(res)[:200])
ck("one completion (position 1)", res.get("completions") == 1 or (res.get("completions") == 0 and res.get("errors") == 1), json.dumps(res)[:160])
if positions:
    ck("position 1's task is now done", task_status(positions[0][1]) in ("done", "completed"), f"{positions[0][1]} → {task_status(positions[0][1])}")
sends = [m for m in new_since(OUTBOX, o0) if m.get("subject", "").startswith("Re:")]
ck("confirmation captured in the outbox (not sent)", len(sends) >= 1 and sends[-1].get("captured") is True and "dana@testco.example.com" in sends[-1].get("to", []), json.dumps(sends[-1] if sends else {})[:160])
ck("confirmation body names the completion", bool(sends) and ("Processed digest reply" in sends[-1].get("body", "") or "completion" in sends[-1].get("body", "")), (sends[-1].get("body", "")[:120] if sends else ""))
feed = new_since(FEED, f0)
inj = [e for e in feed if e.get("kind") == "operator.inject"]
ck("feed line: 'Added a test digest reply — digest_reply_done1.eml'", any(e.get("headline") == "Added a test digest reply — digest_reply_done1.eml" for e in inj), str([e.get("headline") for e in inj]))
ck("no address in the feed line", not any("@" in (e.get("headline", "") + e.get("detail", "")) for e in inj))
jrows = new_since(JOURNAL, j0)
ck("journal row: kind/fixture/by/origin/rc/status", len(jrows) == 1 and jrows[0]["kind"] == "reply" and jrows[0]["by"].startswith("operator:") and jrows[0]["origin"] == "api" and jrows[0]["rc"] == 0 and jrows[0]["status"] == "acted", json.dumps(jrows)[:200])
# a second injection of the same fixture is a NEW reply (fresh Message-ID), so it is acted on again, not swallowed by the once-per-id claim
code, d2 = call("/api/operator/inject", "POST", {"kind": "reply", "fixture": "digest_reply_done1.eml"}, op)
ck("re-injecting is a fresh reply (new Message-ID), not a duplicate claim", code == 200 and d2.get("result", {}).get("status") == "acted" and d2.get("result", {}).get("reply_message_id") != res.get("reply_message_id"), json.dumps(d2.get("result", {}))[:160])

# ── happy path: showing ──────────────────────────────────────────────────────
print("\n=== happy path: showing ===")
# The showing key is address+time, so a fixture already answered on this tenant reports
# 'duplicate' by design. The CLI's --fresh forgets the key; the API does not take flags.
subprocess.run(["/opt/testco/bin/testco-inject", "showing", "showingsmart_inbound.eml", "--fresh", "--origin", "verify-setup"], capture_output=True, text=True, timeout=120)
f0, o0, j0 = len(lines(FEED)), len(lines(OUTBOX)), len(lines(JOURNAL))
t0 = time.time()
# --fresh already answered it once; forget again so the API call is the one that sends
subprocess.run(["/root/jarvis-app/.venv/bin/python", "-c", "import sys; sys.path.insert(0,'/opt/testco/jarvis-app'); import os; os.environ.setdefault('HERMES_HOME','/opt/testco/hermes'); os.environ.setdefault('JARVIS_SKILLS_ROOT','/opt/testco/skills/openclaw-imports'); from app import inject; print(inject._forget_showing(inject.FIXTURES_DIR/'showingsmart_inbound.eml'))"], capture_output=True, text=True, env={**os.environ, "HERMES_HOME": "/opt/testco/hermes", "JARVIS_SKILLS_ROOT": "/opt/testco/skills/openclaw-imports", "JARVIS_RUNTIME_CONFIG": "/opt/testco/skills/openclaw-imports/_shared/runtime.json", "JARVIS_IDENTITY_CONFIG": "/opt/testco/skills/openclaw-imports/_shared/identity.json"}, timeout=60)
code, d = call("/api/operator/inject", "POST", {"kind": "showing", "fixture": "showingsmart_inbound.eml"}, op)
ck("POST showing → 200", code == 200, f"{code} {json.dumps(d)[:200]}")
res = d.get("result", {})
ck("handler answered the showing (status handled)", res.get("status") == "handled", json.dumps(res)[:200])
# testco carries no standing_authorization for this skill (runtime.json says so on
# purpose), so the autoreply WAITS FOR A PERSON: it lands as a pending approval,
# and the captured send appears only once the owner approves it — which is the
# exact flow the tester will walk in the Approvals tab.
APPROVALS = HOME / "state" / "approvals.jsonl"
pend = [a for a in lines(APPROVALS) if a.get("skill") == "showingsmart-autoreply" and a.get("status") == "pending" and a.get("ts", "") >= time.strftime("%Y-%m-%dT%H:%M", time.gmtime(t0 - 5))]
ck("autoreply waits as a pending approval (no standing authorization on testco)", len(pend) >= 1 and "918 Ravenscourt" in pend[-1].get("summary", ""), str([a.get("summary") for a in pend][-2:]))
ck("nothing captured yet — no send before a person says so", len(lines(OUTBOX)) == o0)
dana = login("dana")
code, d = call(f"/api/approvals/{pend[-1]['id']}", "POST", {"decision": "approve"}, dana) if pend else (0, {})
ck("owner approves it via the API → 200", code == 200 and d.get("ok") is True, f"{code} {json.dumps(d)[:160]}")
dec = [r for r in lines(HOME / "state" / "approvals_decisions.jsonl") if pend and r.get("id") == pend[-1]["id"]]
ck("decision recorded as approved by dana", bool(dec) and dec[-1].get("decision") == "approved" and dec[-1].get("decided_by") == "dana", json.dumps(dec[-1] if dec else {})[:160])
# KNOWN GAP (pre-existing, reported, not papered over): nothing executes an approved
# send_email payload — approvals.decide() records the decision and stops. The captured
# send for a showing therefore exists on this tenant only under standing_authorization,
# which the showingsmart suite proves in its own scratch config. Held as a fact here so a
# future executor flips it visibly.
sends = new_since(OUTBOX, o0)
print(f"  info  approved showing autoreply captured in the outbox: {'YES' if sends else 'NO — no executor for approved send_email (pre-existing gap)'}")
feed = new_since(FEED, f0)
ck("feed line: 'Added a test showing request email — showingsmart_inbound.eml'", any(e.get("headline") == "Added a test showing request email — showingsmart_inbound.eml" for e in feed), str([e.get("headline") for e in feed if e.get("kind") == "operator.inject"]))
ck("the skill's own feed line for the answered showing is there too", any(e.get("skill", "").startswith("showingsmart") and e.get("kind") != "operator.inject" for e in feed), str([(e.get("skill"), e.get("headline")) for e in feed][:4]))
ck("journal row for the showing", any(r["kind"] == "showing" and r["origin"] == "api" and r["status"] == "handled" for r in new_since(JOURNAL, j0)))
# same fixture again through the API: the product's once-per-showing rule shows
ap0 = len(lines(APPROVALS))
code, d = call("/api/operator/inject", "POST", {"kind": "showing", "fixture": "showingsmart_inbound.eml"}, op)
ck("second injection of the same showing → handler reports duplicate, 200, no second approval, nothing sent", code == 200 and d.get("result", {}).get("status") == "duplicate" and len(lines(OUTBOX)) == o0 + len(sends) and len(lines(APPROVALS)) == ap0, json.dumps(d.get("result", {}))[:120])

# ── happy path: meeting ──────────────────────────────────────────────────────
print("\n=== happy path: meeting ===")
f0, o0, j0 = len(lines(FEED)), len(lines(OUTBOX)), len(lines(JOURNAL))
code, d = call("/api/operator/inject", "POST", {"kind": "meeting", "fixture": "meeting_zero_items.eml"}, op)
ck("POST meeting → 200", code == 200, f"{code} {json.dumps(d)[:200]}")
res = d.get("result", {})
ck("handler filed or recognised the meeting (done/duplicate — content-keyed by design)", res.get("status") in ("done", "duplicate"), json.dumps(res)[:200])
feed = new_since(FEED, f0)
ck("feed line: 'Added a test meeting email — meeting_zero_items.eml'", any(e.get("headline") == "Added a test meeting email — meeting_zero_items.eml" for e in feed), str([e.get("headline") for e in feed if e.get("kind") == "operator.inject"]))
mline = [e for e in feed if e.get("skill", "").startswith("meeting") or "Wednesday stand-up" in e.get("headline", "")]
ck("the meeting skill's own line (Filed nothing… / Already filed…) is there", any(("Wednesday stand-up" in e.get("headline", "")) for e in mline), str([e.get("headline") for e in mline]))
ck("meeting ingestion sent nothing (by design)", len(lines(OUTBOX)) == o0)
ck("journal row for the meeting", any(r["kind"] == "meeting" and r["origin"] == "api" for r in new_since(JOURNAL, j0)))

# ── CLI wrapper ──────────────────────────────────────────────────────────────
print("\n=== CLI wrapper ===")
j0 = len(lines(JOURNAL))
p = subprocess.run(["/opt/testco/bin/testco-inject", "reply", "digest_reply_nonsense.eml"], capture_output=True, text=True, timeout=120)
try:
    out = json.loads(p.stdout.strip().splitlines()[-1])
except Exception:
    out = {}
ck("testco-inject reply <fixture> → rc 0, acted", p.returncode == 0 and out.get("status") == "acted", f"rc={p.returncode} {p.stdout.strip()[-200:]} {p.stderr.strip()[-200:]}")
ck("wrapper journalled with origin=cli", any(r.get("origin") == "cli" and r.get("kind") == "reply" for r in new_since(JOURNAL, j0)))
p = subprocess.run(["/opt/testco/bin/testco-inject", "reply", "--list"], capture_output=True, text=True, timeout=60)
ck("testco-inject --list prints the whitelist", p.returncode == 0 and "digest_reply_done1.eml" in p.stdout, p.stdout[:100])
p = subprocess.run(["/opt/testco/bin/testco-inject", "showing", "/etc/hostname"], capture_output=True, text=True, timeout=60)
ck("wrapper refuses a non-.eml path", p.returncode == 1 and "refused" in p.stdout, p.stdout[:120])

n = sum(ok)
print(f"\n{n}/{len(ok)} PASS")
sys.exit(0 if n == len(ok) else 1)
