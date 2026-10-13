#!/usr/bin/env python3
"""The digest is a rendering of the store, and it is asserted from the artifact.

WHAT THIS ASSERTS ON: the line the outbound boundary wrote to the capture
outbox. Not the function's return value, not a rendered string held in memory —
the thing that would have been the email. On testco the boundary is active, so
"the digest was delivered" and "a record of exactly what would have gone out"
are the same artifact.

DATES ARE NAMED IN THE TENANT'S ZONE. A deadline is a calendar day where the
customer works; comparing it against a UTC `date` passes for eleven hours and
lies for five, and that exact bug has shipped on this box before. Every date
this suite builds is built in America/Chicago and says so.

Usage:
  python3 docs/tasks/verify_tasks_digest.py [-v]
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta

ap = argparse.ArgumentParser()
ap.add_argument("-v", "--verbose", action="store_true")
args = ap.parse_args()

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import env  # noqa: E402

env.apply()
env.reset()
# The digest MAILS, so the box needs its own outbox as well as its own store.
os.environ["HERMES_HOME"] = str(env.BOX)
(env.BOX / "state").mkdir(parents=True, exist_ok=True)
(env.BOX / "outbox").mkdir(parents=True, exist_ok=True)
os.environ["JARVIS_ALLOW_WRITES"] = "1"

sys.path.insert(0, str(env.SKILLS_CODE / "task-manager" / "scripts"))
sys.path.insert(0, str(env.SKILLS_CODE / "_shared"))

from app import tasks_store, threads as threads_mod  # noqa: E402
import identity  # noqa: E402
import outbound_boundary as ob  # noqa: E402
import notify  # noqa: E402

TZ = "America/Chicago"
from zoneinfo import ZoneInfo  # noqa: E402
today_local = datetime.now(ZoneInfo(TZ)).date()

ok = []
def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))


print("digest — rendered from the app's task store")
print(f"  box={env.BOX}\n  outbox={ob.outbox_path()}\n  today in {TZ}: {today_local}\n")
if not str(ob.outbox_path()).startswith(str(env.BOX)):
    sys.exit(f"REFUSING: the outbox resolves outside the box: {ob.outbox_path()}")
if not ob.active():
    sys.exit("REFUSING: the outbound boundary is inactive here; a digest would really send.")

# ── seed ──────────────────────────────────────────────────────────────────────
OVERDUE = f"Chase the payoff letter (overdue, {TZ})"
tasks_store.create(OVERDUE, created_by="dana", assignee="omar", priority="high",
                   deadline=(today_local - timedelta(days=2)).isoformat())
tasks_store.create("Prepare the Kestrel Row packet", created_by="dana",
                   assignee="dana", priority="high")
quiet = tasks_store.create("Order lockbox batteries", created_by="priya", priority="medium")
tasks_store.set_stale(quiet["task_id"], True)
tasks_store.create("Update the listing photos", created_by="dana",
                   assignee="priya", priority="medium",
                   deadline=(today_local + timedelta(days=5)).isoformat())
tasks_store.create("File the quarterly returns", created_by="dana", priority="low")
finished = tasks_store.create("Already handled", created_by="dana", assignee="dana")
tasks_store.set_status(finished["task_id"], "done", by="dana")

before_box = len(ob.read_outbox())
feed = pathlib.Path(notify.feed_path())
before_feed = len(feed.read_text().splitlines()) if feed.exists() else 0
before_digests = threads_mod.all_counters().get("digests_delivered", {}).get("value", 0)

# ── run through the scheduler, the way the timer will ────────────────────────
import digest_tick  # noqa: E402
rc = digest_tick.main(["--now"])
ck("the digest ran", rc == 0, f"rc={rc}")

box = ob.read_outbox()
ck("exactly one message reached the outbox", len(box) == before_box + 1,
   f"{before_box} -> {len(box)}")
rec = box[-1] if box else {}
body = rec.get("body") or ""
html = rec.get("html_body") or ""

print("\n── who it went to ─────────────────────────────────────────────")
ck("addressed to the tenant's owner", rec.get("to") == [identity.owner_address()],
   str(rec.get("to")))
ck("captured, not sent — the boundary held", rec.get("captured") is True)
ck("the subject counts the open work", "5 Open Tasks" in (rec.get("subject") or ""),
   str(rec.get("subject")))

print("\n── what it says ───────────────────────────────────────────────")
ck("the header counts what is open", "5 open tasks" in body, body.splitlines()[1][:70])
ck("and how much has gone quiet", "1 gone quiet" in body, body.splitlines()[1][:70])
ck("finished work is NOT in the digest", "Already handled" not in body)
i_high, i_med, i_low = (body.find("--- HIGH"), body.find("--- MEDIUM"), body.find("--- LOW"))
ck("sections run high, then medium, then low",
   -1 < i_high < i_med < i_low, f"{i_high}/{i_med}/{i_low}")
ck("the overdue task is marked OVERDUE", "OVERDUE" in body)
ck("   and it is the one that is actually overdue",
   "OVERDUE" in body[body.find(OVERDUE) - 60:body.find(OVERDUE) + 10], "")
ck("the quiet task is marked STALE", "[STALE]" in body)
ck("   and ONLY that one is", body.count("[STALE]") == 1, str(body.count("[STALE]")))
ck("every task names who it is for",
   "Omar Fennick" in body and "Dana Whitlock" in body and "Priya Umberdown" in body)
ck("days open are shown", "d open" in body)
# BOTH RENDERINGS OR NEITHER. The plain text and the HTML are built in
# separate loops, so a marker can be dropped from one and survive in the other
# — and most mail clients show the HTML.
ck("the html marks the quiet task too", "STALE" in html)
ck("the html marks the overdue one too", "OVERDUE" in html)
ck("the html twin carries the same sections",
   html.find("HIGH PRIORITY") < html.find("MEDIUM PRIORITY") < html.find("LOW PRIORITY"))

print("\n── positions, so a reply can resolve against them ─────────────")
conn = __import__("sqlite3").connect(str(tasks_store.DB_PATH))
rows = conn.execute("SELECT position, task_id FROM digest_map ORDER BY position").fetchall()
conn.close()
ck("one position per open task", len(rows) == 5, str(len(rows)))
ck("numbered from one, without gaps", [r[0] for r in rows] == [1, 2, 3, 4, 5],
   str([r[0] for r in rows]))
ck("position 1 is the overdue high-priority task",
   rows and tasks_store.get(rows[0][1])["title"] == OVERDUE,
   tasks_store.get(rows[0][1])["title"] if rows else "")

print("\n── what the tenant sees afterwards ────────────────────────────")
events = [json.loads(l) for l in feed.read_text().splitlines()[before_feed:] if l.strip()]
sent = [e for e in events if e["kind"] == "digest.sent"]
ck("the digest is announced on the feed", len(sent) == 1, str([e["kind"] for e in events]))
if sent:
    h = sent[0]["headline"]
    ck("   it says what went out, in a sentence", h.startswith("Sent the ") and "digest" in h, h)
    ck("   it counts the work", "5 open" in h and "1 gone quiet" in h, h)
    ck("   no address in the headline", "@" not in h, h)
    ck("   no digest id in the headline", "digest-" not in h, h)
    ck("   the id is in the detail, where it belongs", "digest-" in sent[0]["detail"])
after = threads_mod.all_counters().get("digests_delivered", {}).get("value", 0)
ck("delivery is counted", after == before_digests + 1, f"{before_digests} -> {after}")

print("\n── the schedule ───────────────────────────────────────────────")
state = json.loads((env.BOX / "state" / "digest_last_run.json").read_text())
ck("the run was recorded", state.get("result") == "forced", str(state))
ck("   naming the tenant's zone", state.get("timezone") == TZ, str(state.get("timezone")))
ck("a forced run does NOT consume the real slot", "last_slot" not in state, str(state))
ck("and the next real slot is still pending",
   digest_tick.main(["--dry-run"]) == 0)

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
