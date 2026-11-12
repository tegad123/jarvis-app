#!/usr/bin/env python3
"""The tasks role matrix, driven through the real API.

EVERY ASSERTION GOES THROUGH THE HTTP SURFACE and is then confirmed against the
STORE. That pairing is the point: a route can return 200 and write nothing, and
a store can hold the right row while the route that was supposed to refuse the
call let it through. The response says what the API claimed; the database says
what actually happened.

THE UI IS NEVER INVOLVED. Every 403 below is a crafted request — no button was
hidden, because a hidden button is a courtesy and this suite tests the control.

Usage:
  docs/tasks/tasks_sandbox.sh start
  python3 docs/tasks/verify_tasks_api.py [--base http://127.0.0.1:8711] [-v]
"""

import argparse
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--base", default=os.environ.get("TASKS_BASE", "http://127.0.0.1:8711"))
ap.add_argument("--tasks-db", default=os.environ.get(
    "TASKS_DB", "/tmp/claude-0/-root/tasks-sandbox/tasks.db"))
ap.add_argument("--feed", default=os.environ.get(
    "TASKS_FEED", "/tmp/claude-0/-root/tasks-sandbox/feed.jsonl"))
ap.add_argument("--pins", default="/opt/jarvis-verify-fixture/SEEDED_PINS.json")
ap.add_argument("-v", "--verbose", action="store_true")
args = ap.parse_args()

BASE = args.base.rstrip("/")
TASKS_DB = pathlib.Path(args.tasks_db)
FEED = pathlib.Path(args.feed)
JARDIR = pathlib.Path("/tmp/tasks-jars"); JARDIR.mkdir(exist_ok=True)

# The live tenant is never a target. A suite that writes tasks needs a
# disposable one, and :8700 is the customer's.
if BASE.endswith(":8700"):
    sys.exit("REFUSING: :8700 is the live tenant. Point this at the sandbox or testco.")

sys.path.insert(0, os.path.join(os.environ.get("VERIFY_SKILLS_ROOT")
                                or "/opt/jarvis-verify-fixture/skills/openclaw-imports", "_shared"))
os.environ.setdefault("JARVIS_IDENTITY_CONFIG",
                      "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json")
import identity  # noqa: E402

PINS = json.loads(pathlib.Path(args.pins).read_text())
if isinstance(next(iter(PINS.values())), dict):
    PINS = {k: v.get("pin") for k, v in PINS.items()}


def passphrase() -> str:
    value = os.environ.get("VERIFY_OPERATOR_PASSPHRASE")
    if not value:
        sys.exit("REFUSING: VERIFY_OPERATOR_PASSPHRASE is not set (value never printed)")
    return value


ok = []
def ck(name, cond, extra=""):
    ok.append(bool(cond))
    mark = "PASS" if cond else "FAIL"
    if not cond or args.verbose:
        print(f"  {mark}  {name}" + (f" — {extra}" if extra else ""))
    else:
        print(f"  {mark}  {name}")


def call(path, method="GET", body=None, jar=None):
    cmd = ["curl", "-s", "-o", "/tmp/tk_body", "-w", "%{http_code}", "-X", method, BASE + path]
    if jar:
        cmd += ["-b", jar, "-c", jar]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    code = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    try:
        data = json.loads(pathlib.Path("/tmp/tk_body").read_text())
    except Exception:
        data = {}
    return int(code or 0), data


def login(who):
    jar = str(JARDIR / f"{who}.jar")
    pathlib.Path(jar).unlink(missing_ok=True)
    body = {"passphrase": passphrase()} if who == "operator" else {"person_key": who, "pin": PINS[who]}
    code, d = call("/api/login", "POST", body, jar)
    if not d.get("ok"):
        sys.exit(f"login {who} failed: HTTP {code} {d}")
    return jar


def row(task_id):
    conn = sqlite3.connect(str(TASKS_DB)); conn.row_factory = sqlite3.Row
    try:
        r = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def feed_lines():
    if not FEED.exists():
        return []
    return [json.loads(l) for l in FEED.read_text().splitlines() if l.strip()]


CREATED = 0  # how many creates this run actually performed


def mk(title, jar, **kw):
    global CREATED
    code, d = call("/api/tasks", "POST", {"title": title, **kw}, jar)
    assert code == 201, f"seed create failed: {code} {d}"
    CREATED += 1
    return d["task"]["task_id"]


print(f"tasks role matrix — {BASE}\n  store={TASKS_DB}\n  feed={FEED}\n")
J = {w: login(w) for w in ("dana", "priya", "omar", "operator")}
NAME = {w: identity.display_name_for(w) for w in ("dana", "priya", "omar")}
# Counters are asserted as a DELTA over this run, captured before the first
# create. The sandbox copies the tenant's threads.db, which already holds
# counts from earlier runs — an absolute threshold passes even with the bump
# deleted, and did exactly that.
C0 = {k: v["value"] for k, v in call("/api/counters", jar=J["dana"])[1]["counters"].items()}

# ── 1. create ────────────────────────────────────────────────────────────────
print("── 1. CREATE — any person may add to the shared list ──────────")
for who, want in (("dana", 201), ("priya", 201), ("omar", 201), ("operator", 403)):
    code, d = call("/api/tasks", "POST", {"title": f"Created by {who}"}, J[who])
    ck(f"{who} -> {code} (expected {want})", code == want, str(d.get("detail", ""))[:60])
    if code == 201:
        CREATED += 1
        r = row(d["task"]["task_id"])
        ck(f"   {who}'s row records them as the creator",
           r and r["created_by"] == who and r["source"] == "manual" and r["status"] == "open",
           str(r and (r["created_by"], r["source"], r["status"])))

before = len(feed_lines())
T_OMAR = mk("Chase the survey for 118 Alderwood", J["dana"], assignee="omar",
            priority="high", detail="Ask for the recorded plat.", deadline="2026-09-30")
T_DANA = mk("Confirm the closing time", J["dana"], assignee="dana", priority="medium")
T_FREE = mk("Order lockbox batteries", J["priya"], priority="low")

# ── 2. read ──────────────────────────────────────────────────────────────────
print("\n── 2. READ — team-visible; operator sees structure, not bodies ─")
L = {w: call("/api/tasks", jar=J[w])[1] for w in J}
ck("every role reads the list", all(l.get("source") == "store" for l in L.values()),
   str({w: l.get("source") for w, l in L.items()}))
n = L["dana"]["count"]
ck("the list is TEAM-VISIBLE — every role sees the same count",
   all(l["count"] == n for l in L.values()), str({w: l["count"] for w, l in L.items()}))
ck("a member sees a task assigned to someone else",
   any(t["task_id"] == T_DANA for t in L["omar"]["tasks"]))
ck("people are named, not keyed",
   any(t["assignee_name"] == NAME["omar"] for t in L["dana"]["tasks"]),
   str([t["assignee_name"] for t in L["dana"]["tasks"]][:3]))
ck("a person session sees detail bodies",
   any(t["detail"] for t in L["dana"]["tasks"]) and L["dana"]["bodies_visible"] is True)
ck("the OPERATOR gets every detail nulled",
   all(t["detail"] is None for t in L["operator"]["tasks"]),
   str([t["detail"] for t in L["operator"]["tasks"]][:3]))
ck("and the payload SAYS the bodies are hidden",
   L["operator"]["bodies_visible"] is False)
ck("the operator still sees the structure it is there to support",
   len(L["operator"]["tasks"]) == n and
   all(t["title"] and t["status"] for t in L["operator"]["tasks"]))
ck("the payload names the tenant's timezone",
   L["dana"].get("timezone") == "America/Chicago", str(L["dana"].get("timezone")))
_tzc = {k: v["value"] for k, v in call("/api/counters", jar=J["dana"])[1]["counters"].items()}
_tz_fb = _tzc.get("tasks_tz_fallback_utc", 0) - C0.get("tasks_tz_fallback_utc", 0)
if L["dana"].get("timezone") == "UTC":
    ck("a UTC fallback is counted, not silent", _tz_fb > 0, f"delta={_tz_fb}")
else:
    ck("no UTC fallback counted when the tenant zone resolves", _tz_fb == 0, f"delta={_tz_fb}")

# ── 2b. order ────────────────────────────────────────────────────────────────
print("\n── 2b. THE ORDER THE LIST COMES BACK IN ───────────────────────")
# The order is the product decision (owner, 2026-11-03): newest first, older
# tasks further down. Priority and lateness are labels on the row, not sort
# keys, so an old overdue high task must not climb above a newer one.
from datetime import date, timedelta  # noqa: E402
yesterday = (date.today() - timedelta(days=2)).isoformat()
O_OLD = mk("Zzz oldest, late and urgent", J["dana"], priority="high", deadline=yesterday)
time.sleep(0.01)
O_MID = mk("Zzz middle", J["dana"], priority="low")
time.sleep(0.01)
O_NEW = mk("Zzz newest, unimportant", J["dana"], priority="low")
order = [t["task_id"] for t in call("/api/tasks", jar=J["dana"])[1]["tasks"]]
pos = {tid: order.index(tid) for tid in (O_OLD, O_MID, O_NEW) if tid in order}
ck("the newest task comes first, even below an older urgent one in priority",
   pos.get(O_NEW, 99) < pos.get(O_MID, 99) < pos.get(O_OLD, 99), str(pos))
old_row = next((t for t in call("/api/tasks", jar=J["dana"])[1]["tasks"] if t["task_id"] == O_OLD), {})
ck("the older task still carries its priority and overdue label",
   old_row.get("priority") == "high" and bool(old_row.get("due_label")),
   str({k: old_row.get(k) for k in ("priority", "due_label")}))
O_LATE, O_HIGH, O_LOW = O_OLD, O_MID, O_NEW
for tid in (O_LATE, O_HIGH, O_LOW):
    call(f"/api/tasks/{tid}/archive", "POST", None, J["dana"])

# ── 3. edit / assign / archive ───────────────────────────────────────────────
print("\n── 3. EDIT, ASSIGN, ARCHIVE — admin or owner ──────────────────")
for who, want in (("dana", 200), ("priya", 200), ("omar", 403), ("operator", 403)):
    code, d = call(f"/api/tasks/{T_FREE}", "PATCH", {"priority": "high"}, J[who])
    ck(f"edit as {who} -> {code} (expected {want})", code == want, str(d.get("detail", ""))[:70])
ck("   the edit that was allowed actually landed", row(T_FREE)["priority"] == "high")

for who, want in (("omar", 403), ("operator", 403), ("priya", 200)):
    code, d = call(f"/api/tasks/{T_FREE}/assign", "POST", {"assignee": "omar"}, J[who])
    ck(f"assign as {who} -> {code} (expected {want})", code == want, str(d.get("detail", ""))[:70])
ck("   the assignment landed in the store", row(T_FREE)["assignee"] == "omar")

# ── 4. status ────────────────────────────────────────────────────────────────
print("\n── 4. STATUS — the assignee, an admin, or the owner ───────────")
code, d = call(f"/api/tasks/{T_OMAR}/status", "POST", {"status": "in_progress"}, J["omar"])
ck("a member may move their OWN task", code == 200, str(d.get("detail", ""))[:70])
ck("   and the store agrees", row(T_OMAR)["status"] == "in_progress")
code, d = call(f"/api/tasks/{T_DANA}/status", "POST", {"status": "done"}, J["omar"])
ck("a member may NOT move someone else's task", code == 403, f"HTTP {code}")
ck("   and nothing changed in the store", row(T_DANA)["status"] == "open")
ck("   the refusal names the rule, not a rank",
   "assigned to" in str(d.get("detail", "")), str(d.get("detail"))[:80])
code, _ = call(f"/api/tasks/{T_DANA}/status", "POST", {"status": "done"}, J["priya"])
ck("an admin may close anyone's task", code == 200)
r = row(T_DANA)
ck("   done is recorded WITH a completion time",
   r["status"] == "done" and r["completed_at"], str((r["status"], r["completed_at"])))
code, _ = call(f"/api/tasks/{T_DANA}/status", "POST", {"status": "open"}, J["dana"])
ck("reopening clears the completion time",
   code == 200 and row(T_DANA)["completed_at"] is None, str(row(T_DANA)["completed_at"]))
code, _ = call(f"/api/tasks/{T_OMAR}/status", "POST", {"status": "done"}, J["operator"])
ck("the operator may not move any task", code == 403)

print("\n── 5. ARCHIVE ─────────────────────────────────────────────────")
for who, want in (("omar", 403), ("operator", 403), ("dana", 200)):
    code, _ = call(f"/api/tasks/{T_FREE}/archive", "POST", None, J[who])
    ck(f"archive as {who} -> {code} (expected {want})", code == want)
ck("   archived is a STATUS, and the row is still there",
   row(T_FREE)["status"] == "archived")
ck("   an archived task leaves the default list",
   not any(t["task_id"] == T_FREE for t in call("/api/tasks", jar=J["dana"])[1]["tasks"]))
ck("   and can still be read deliberately",
   any(t["task_id"] == T_FREE
       for t in call("/api/tasks?archived=true", jar=J["dana"])[1]["tasks"]))

# ── 6. bad input ─────────────────────────────────────────────────────────────
print("\n── 6. REFUSALS THAT ARE NOT ABOUT ROLE ────────────────────────")
code, _ = call("/api/tasks/tk_nope/archive", "POST", None, J["dana"])
ck("unknown id -> 404", code == 404, f"HTTP {code}")
code, d = call("/api/tasks", "POST", {"title": "x", "assignee": "nobody-here"}, J["dana"])
ck("an assignee who is not on the team -> 422", code == 422, str(d.get("detail"))[:60])
code, _ = call("/api/tasks", "POST", {"title": "x", "priority": "urgent"}, J["dana"])
ck("a priority outside the vocabulary -> 422", code == 422, f"HTTP {code}")
code, _ = call("/api/tasks", "POST", {"title": "x", "deadline": "30-09-2026"}, J["dana"])
ck("a deadline that is not a calendar day -> 422", code == 422, f"HTTP {code}")
code, _ = call(f"/api/tasks/{T_OMAR}/status", "POST", {"status": "finished"}, J["dana"])
ck("a status outside the vocabulary -> 422", code == 422, f"HTTP {code}")

# ── 7. the feed ──────────────────────────────────────────────────────────────
print("\n── 7. THE FEED — follow-through is visible ────────────────────")
new = feed_lines()[before:]
kinds = [e["kind"] for e in new]
SEEDED = {T_OMAR, T_DANA, T_FREE}
def about(e):
    return {a.get("task_id") for a in (e.get("attachments") or [])} & SEEDED
# Scoped to the three tasks this section is about — the ordering section above
# creates its own, and counting those here would make the number drift every
# time the suite grows.
created = [e for e in new if e["kind"] == "task.created" and about(e)]
assigned = [e for e in new if e["kind"] == "task.assigned"]
completed = [e for e in new if e["kind"] == "task.completed"]
ck("exactly one created event per task created", len(created) == 3, str(kinds))
ck("assignment is its own event", len(assigned) >= 1, str(kinds))
a = assigned[0]
ck("   it names WHAT moved", "Chase the survey" in a["headline"], a["headline"])
ck("   WHO it went to", NAME["omar"] in a["headline"], a["headline"])
ck("   and WHO it came from", f"from {NAME['dana']}" in a["headline"], a["headline"])
ck("completion is announced", len(completed) >= 1, str(kinds))
ck("   as a plain sentence", completed[0]["headline"].startswith("Completed: "),
   completed[0]["headline"])
blob = " ".join(e.get("headline", "") for e in new)
ck("NO headline carries an address", "@" not in blob)
ck("NO headline carries a task id", "tk_" not in blob)
ck("NO headline carries a person KEY instead of a name",
   not any(f" {k}" in blob for k in ("omar", "dana", "priya")), blob[:90])
ck("NO headline carries a priority parenthetical",
   not any(w in blob.lower() for w in ("(high)", "(medium)", "(low)", "priority")))
ck("the id lives in the detail, where it belongs",
   all("tk_" in e.get("detail", "") for e in created))
ck("the assignment event carries the assignee, so W3 can scope it",
   a["attachments"][0].get("assignee") == "omar", str(a["attachments"]))

fq = {w: call("/api/feed", jar=J[w])[1] for w in ("omar", "dana")}
def has(events, tid):
    return any(any(att.get("task_id") == tid for att in (e.get("attachments") or []))
               for e in events.get("events", []))
ck("a member's feed carries the task assigned to THEM", has(fq["omar"], T_OMAR))
ck("and NOT a task belonging to someone else", not has(fq["omar"], T_DANA),
   "member saw another person's task event")

# ── 8. counters ──────────────────────────────────────────────────────────────
print("\n── 8. COUNTERS ────────────────────────────────────────────────")
c = call("/api/counters", jar=J["dana"])[1]["counters"]
vals = {k: v["value"] for k, v in c.items()}
d_manual = vals.get("tasks_created.manual", 0) - C0.get("tasks_created.manual", 0)
d_done = vals.get("tasks_completed", 0) - C0.get("tasks_completed", 0)
d_count = vals.get("tasks_completion_count", 0) - C0.get("tasks_completion_count", 0)
ck("every create this run was counted, and only those",
   d_manual == CREATED, f"counted {d_manual}, created {CREATED}")
ck("completions are counted", d_done >= 1, f"delta {d_done}")
ck("time-to-completion is accumulated alongside a count to divide by",
   d_count == d_done and "tasks_completion_seconds" in vals, f"{d_count} vs {d_done}")

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
