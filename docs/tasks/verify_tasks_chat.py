#!/usr/bin/env python3
"""A task asked for in chat becomes a task in the app's list.

TWO SECTIONS, AND THE SPLIT IS DELIBERATE.

  (a) THE REAL ASK ROUND TRIP. A message goes in through POST /api/chat on a
      live tenant, reaches the gateway, and the model decides whether to run
      the task-manager skill. Nothing is stubbed, which is the only way to
      learn whether the deck's sentence ("just tell it") is actually true.
      It costs a model call and it is not deterministic, so it asserts on the
      OUTCOME — a row appeared, and the transcript says so — never on wording.

  (b) THE DETERMINISTIC SKILL PATH. add_task.py invoked directly, in a box.
      This is where the exact assertions live: which store the row landed in,
      the source, the source_ref, who got the credit, the dedupe, the feed
      event, the counter. A model that phrases things differently must not be
      able to turn those red.

Neither section calls the extractor and declares victory: (a) asserts the row,
(b) asserts the row. --stub skips (a) and says so LOUDLY — a skipped round trip
is reported as SKIPPED, never as a pass, because "we did not check" and "it
works" must never look the same in this output.

Usage:
  python3 docs/tasks/verify_tasks_chat.py [--base http://127.0.0.1:8710] [--stub]
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
ap.add_argument("--base", default=os.environ.get("TASKS_BASE", "http://127.0.0.1:8710"))
ap.add_argument("--pins", default="/opt/testco/SEEDED_PINS.json")
ap.add_argument("--stub", action="store_true", help="skip the real model round trip")
ap.add_argument("--timeout", type=int, default=120)
args = ap.parse_args()

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import env  # noqa: E402

BASE = args.base.rstrip("/")
if BASE.endswith(":8700"):
    sys.exit("REFUSING: :8700 is the live tenant.")

ok = []
skipped = []
def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))


def skip(name, why):
    skipped.append(name)
    print(f"  SKIP  {name} — {why}")


# ── (a) the real Ask round trip ──────────────────────────────────────────────
print("── (a) THE REAL ASK ROUND TRIP (live gateway, real model) ─────")
LIVE_TASKS = pathlib.Path("/opt/testco/hermes/state/jarvis_tasks.db")
# Unique per run so the skill cannot DUPLICATE-SKIP against the seeded
# Aug-26 Alderwood task (tk_913e2ee0e84f). Matching below keys on this
# token, not "alderwood", so that old row cannot false-green the check.
RUN_TOKEN = os.urandom(4).hex()
PHRASE = f"Track this: call the title company about 118 Alderwood run {RUN_TOKEN}"

if args.stub:
    skip("a task asked for in the Ask tab appears in the list",
         "--stub: the model round trip did NOT run, so nothing about chat "
         "creation was verified here")
else:
    PINS = json.loads(pathlib.Path(args.pins).read_text())
    if isinstance(next(iter(PINS.values())), dict):
        PINS = {k: v.get("pin") for k, v in PINS.items()}
    jar = "/tmp/tk-chat.jar"
    pathlib.Path(jar).unlink(missing_ok=True)

    def call(path, method="GET", body=None, raw=False):
        cmd = ["curl", "-s", "-o", "/tmp/tk_chat_body", "-w", "%{http_code}",
               "-X", method, BASE + path, "-b", jar, "-c", jar]
        if body is not None:
            cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
        if raw:
            cmd += ["-m", str(args.timeout)]
        code = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
        text = pathlib.Path("/tmp/tk_chat_body").read_text()
        try:
            return int(code or 0), json.loads(text)
        except Exception:
            return int(code or 0), text

    code, d = call("/api/login", "POST", {"person_key": "dana", "pin": PINS["dana"]})
    ck("signed in as the owner", code == 200 and d.get("ok"), f"HTTP {code}")

    def rows():
        if not LIVE_TASKS.exists():
            return []
        conn = sqlite3.connect(str(LIVE_TASKS)); conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM tasks WHERE source = 'chat' ORDER BY created_at DESC")]
        finally:
            conn.close()

    before_ids = {r["task_id"] for r in rows()}
    # A fresh channel — not `direct`, which has weeks of test history that
    # makes the model turn miss the 120s cap (history=26, 0 tool calls).
    code, created = call("/api/threads", "POST",
                         {"name": f"verify-tasks-chat-{RUN_TOKEN}"})
    tid = None
    if code == 201 and isinstance(created, dict):
        tid = (created.get("thread") or {}).get("thread_id")
    ck("found a thread to talk in", tid is not None, str(tid))

    if tid:
        started = time.time()
        code, _ = call("/api/chat", "POST", {"thread_id": tid, "message": PHRASE}, raw=True)
        ck("the message was accepted", code == 200, f"HTTP {code}")
        found = None
        while time.time() - started < args.timeout:
            for r in rows():
                if r["task_id"] not in before_ids and RUN_TOKEN in (r["title"] or "").lower():
                    found = r; break
            if found:
                break
            time.sleep(3)
        ck("a task from CHAT appeared in the app's list", found is not None,
           "the model did not invoke the task skill within the timeout" if not found
           else f"{found['task_id']} · {found['title']}")
        if found:
            ck("   it is recorded as having come from chat", found["source"] == "chat")
            ck("   it carries a reference back to where it was asked for",
               bool(found["source_ref"]), str(found["source_ref"]))
            code, hist = call(f"/api/chat/history?thread_id={tid}")
            msgs = hist.get("messages") or []
            last = " ".join(m.get("content", "") for m in msgs[-3:] if m.get("role") == "assistant")
            ck("   and the transcript confirms it back to the person",
               RUN_TOKEN in last.lower() or "task" in last.lower(), last[:110])

# ── (b) the deterministic skill path ─────────────────────────────────────────
print("\n── (b) THE SKILL PATH, EXACTLY (boxed, no model) ──────────────")
env.apply()
env.reset()
paths = env.assert_isolated()
print(f"  box: {env.BOX}")

from app import tasks_store, threads as threads_mod  # noqa: E402

ADD = "/opt/testco/skills/openclaw-imports/task-manager/scripts/add_task.py"
DONE = "/opt/testco/skills/openclaw-imports/task-manager/scripts/complete_task.py"
LEGACY = pathlib.Path("/opt/testco/skills/openclaw-imports/task-manager/data/tasks.db")
MSG_ID = "slack:C0TEST:1724700000.000100"
TITLE = "Call the title company about 118 Alderwood"


def legacy_count():
    if not LEGACY.exists():
        return 0
    conn = sqlite3.connect(str(LEGACY))
    try:
        return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    finally:
        conn.close()


def run(script, *argv):
    return subprocess.run([sys.executable, script, *argv], capture_output=True,
                          text=True, env=dict(os.environ), timeout=90)


legacy_before = legacy_count()
feed = pathlib.Path(os.environ["JARVIS_ACTIVITY_FEED"])
p = run(ADD, "--title", TITLE, "--source", "chat", "--platform", "slack",
        "--actor", "dana@testco.example.com", "--source-message-id", MSG_ID,
        "--priority", "high", "--notes", "Ask for the payoff letter.")
tid_out = (p.stdout or "").strip().splitlines()[-1] if p.stdout.strip() else ""
ck("the skill wrote a task and printed its id", p.returncode == 0 and tid_out.startswith("tk_"),
   f"rc={p.returncode} out={tid_out!r} err={(p.stderr or '')[:120]}")

row = tasks_store.get(tid_out) if tid_out.startswith("tk_") else None
ck("it landed in the APP's store", row is not None, str(paths["tasks_store.DB_PATH"]))
if row:
    ck("   source says it came from chat", row["source"] == "chat", row["source"])
    ck("   source_ref is the message it was asked in, exactly",
       row["source_ref"] == MSG_ID, str(row["source_ref"]))
    ck("   the person who asked gets the credit", row["created_by"] == "dana",
       row["created_by"])
    ck("   the assignee is a person KEY, not an address",
       row["assignee"] == "dana", str(row["assignee"]))
    ck("   the notes became the task's detail",
       (row["detail"] or "").startswith("Ask for"), str(row["detail"])[:40])
ck("the LEGACY list was not touched", legacy_count() == legacy_before,
   f"{legacy_before} -> {legacy_count()}")

def read_events():
    if not feed.exists():
        return []
    return [json.loads(l) for l in feed.read_text().splitlines() if l.strip()]


events = read_events()
created = [e for e in events if e["kind"] == "task.created"]
ck("exactly ONE created event — the store emits, the script does not",
   len(created) == 1, str([e["kind"] for e in events]))
if created:
    ck("   the headline is a sentence, with no id in it",
       created[0]["headline"].startswith("Created a task:") and "tk_" not in created[0]["headline"],
       created[0]["headline"])
    ck("   and no platform id leaked into it", "slack:" not in created[0]["headline"])

vals = {k: v["value"] for k, v in threads_mod.all_counters().items()}
ck("a chat-sourced creation is counted as such",
   vals.get("tasks_created.chat", 0) == 1, str(vals))

p2 = run(ADD, "--title", TITLE, "--source", "chat", "--platform", "slack",
         "--actor", "dana@testco.example.com", "--source-message-id", MSG_ID)
second = (p2.stdout or "").strip().splitlines()[-1] if (p2.stdout or "").strip() else ""
# Compared as a real id, not by suffix: "" endswith "" is True, and that made
# two FAILED runs look like a working dedupe the first time this ran.
ck("asking twice does not make two tasks",
   bool(tid_out) and second == tid_out, f"{tid_out!r} then {second!r}")
ck("   and no second event was posted",
   len([e for e in read_events() if e["kind"] == "task.created"]) == 1,
   str([e["kind"] for e in read_events()]))

p3 = run(ADD, "--title", "Something from outside", "--source", "chat",
         "--actor", "stranger@other.test")
ck("someone outside the team cannot file a task", p3.returncode != 0, f"rc={p3.returncode}")
ck("   and nothing was written for them",
   not any(t["title"] == "Something from outside" for t in tasks_store.query()))

p4 = run(DONE, "--task-id", tid_out, "--actor", "dana@testco.example.com")
ck("the skill can complete a task in the app's list", p4.returncode == 0,
   (p4.stdout or p4.stderr or "")[:80])
done_row = tasks_store.get(tid_out)
ck("   the store records it done, with a completion time",
   done_row["status"] == "done" and done_row["completed_at"], str(done_row["status"]))
events = read_events()
ck("   and the completion is announced once",
   len([e for e in events if e["kind"] == "task.completed"]) == 1)
vals = {k: v["value"] for k, v in threads_mod.all_counters().items()}
ck("   completion is counted", vals.get("tasks_completed", 0) == 1, str(vals))

print(f"\n{sum(ok)}/{len(ok)} passed" + (f", {len(skipped)} SKIPPED" if skipped else ""))
if skipped:
    print("  NOT VERIFIED: " + "; ".join(skipped))
sys.exit(0 if all(ok) else 1)
