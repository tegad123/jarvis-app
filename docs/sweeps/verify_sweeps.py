#!/usr/bin/env python3
"""The three sweeps, triggered against aged data, asserted from the artifacts.

EVERY ASSERTION IS MADE AGAINST SOMETHING THAT CHANGED ON DISK — a task row, an
outbox line, a feed event, a counter, a row in the sweeps store. No section
calls a predicate and declares victory: the question is never "would this
thread count as stale" but "did a task appear, and did it appear exactly once".

FOUR THINGS THIS SUITE INSISTS ON, each from a way a periodic job lies:
  * IDEMPOTENCY BY RE-RUNNING, not by reading the code. Every sweep is run
    twice back to back; the second run must produce nothing but a heartbeat.
  * THE ZERO CASE. A fresh box, nothing aged, every sweep runs — and every one
    still posts a heartbeat. A sweep that stays silent when there is nothing to
    do is indistinguishable from a sweep that is broken.
  * THE UNKNOWN ASSIGNEE, LOUDLY. A task whose assignee the registry cannot
    name must produce an alert, not a skip, and the task must stay visible.
  * CONFIG MOVES BEHAVIOUR. A threshold changed in the tenant's config, the
    same data, a different outcome — proof the number is not a constant.

Run:  /root/jarvis-app/.venv/bin/python docs/sweeps/verify_sweeps.py [-v]
"""

import argparse
import contextlib
import io
import json
import os
import pathlib
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

ap = argparse.ArgumentParser()
ap.add_argument("-v", "--verbose", action="store_true")
args = ap.parse_args()

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tasks"))
import env  # noqa: E402

env.apply()
env.reset()
# Sweeps keep state and mail under $HERMES_HOME; the box has to BE the home.
os.environ["HERMES_HOME"] = str(env.BOX)
(env.BOX / "state").mkdir(parents=True, exist_ok=True)
(env.BOX / "outbox").mkdir(parents=True, exist_ok=True)
os.environ["JARVIS_ALLOW_WRITES"] = "1"

TESTCO_SKILLS = env.TESTCO_SKILLS          # config: runtime.json, identity.json
SKILLS_CODE = env.SKILLS_CODE              # code under test
for p in (SKILLS_CODE / "sweeps" / "scripts", SKILLS_CODE / "_shared",
          SKILLS_CODE / "owner-email" / "scripts"):
    sys.path.insert(0, str(p))

# A scratch runtime with the sweeps block, so the suite never edits testco's.
SCRATCH_RUNTIME = env.BOX / "runtime.json"
BASE_CFG = json.loads((TESTCO_SKILLS / "_shared" / "runtime.json").read_text())
SWEEPS_CFG = {"enabled": True, "stale_email_hours": 24, "email_grace_days": 2,
              "task_quiet_hours": 24, "task_escalate_after": 3, "lead_quiet_days": 3,
              "hot_lead_quiet_days": 1, "lead_nudge_backoff_days": 3, "min_interval_minutes": 30}


def write_runtime(**overrides):
    cfg = json.loads(json.dumps(BASE_CFG))
    cfg["skills"]["sweeps"] = {**SWEEPS_CFG, **overrides}
    SCRATCH_RUNTIME.write_text(json.dumps(cfg, indent=1))
    os.environ["JARVIS_RUNTIME_CONFIG"] = str(SCRATCH_RUNTIME)
    import tenant_config as tc
    tc.reset_cache()


write_runtime()

from app import leads_store, tasks_store, threads  # noqa: E402
import identity  # noqa: E402
import notify  # noqa: E402
import outbound_boundary as ob  # noqa: E402
import app_counters  # noqa: E402
import sweep_core as core  # noqa: E402
import sweep_tick  # noqa: E402
import stale_email, task_followup, quiet_lead  # noqa: E402,F401

TZ = "America/Chicago"
from zoneinfo import ZoneInfo  # noqa: E402
today_local = datetime.now(ZoneInfo(TZ)).date()
NOW = datetime.now(timezone.utc)
ago = lambda **k: (NOW - timedelta(**k)).isoformat()  # noqa: E731

ok = []
def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))


# ── isolation, asked of the modules ──────────────────────────────────────────
env.assert_isolated()
for label, path in (("leads_store.DB_PATH", pathlib.Path(leads_store.DB_PATH)),
                    ("sweeps state", core.state_db_path()),
                    ("capture outbox", ob.outbox_path())):
    if not str(path.resolve()).startswith(str(env.BOX.resolve())):
        sys.exit(f"REFUSING: {label} resolves outside the box: {path}")
if not ob.active():
    sys.exit("REFUSING: the outbound boundary is inactive here; a nudge would really send.")

FEED = pathlib.Path(notify.feed_path())
OUTBOX = ob.outbox_path()
NAME = {k: identity.display_name_for(k) for k in ("dana", "omar", "priya")}
OMAR_ADDR, DANA_ADDR = identity.roster()["omar"], identity.roster()["dana"]


def fresh_box():
    """Everything the sweeps read or write, gone. Store connections re-opened."""
    env.reset()
    for extra in ("outbox/captured_mail.jsonl",):
        p = env.BOX / extra
        if p.exists():
            p.unlink()
    for mod in (tasks_store, leads_store, threads):
        mod._initialised = False
        try:
            mod._local.conn = None
        except Exception:
            pass
    core.reset_state_connection()
    app_counters._THREADS = None
    app_counters._LOAD_ERROR = None


def feed():
    if not FEED.exists():
        return []
    return [json.loads(l) for l in FEED.read_text().splitlines() if l.strip()]


def outbox():
    return ob.read_outbox()


SEEN = {}   # run-wide: the highest value each counter reached, across box resets


def counters():
    vals = {k: v["value"] for k, v in threads.all_counters().items()}
    for k, v in vals.items():
        SEEN[k] = max(SEEN.get(k, 0), v)
    return vals


def run(sweep, *extra):
    return sweep_tick.main(["--sweep", sweep, "--now", *extra])


def backdate_task(task_id, **delta):
    c = sqlite3.connect(str(tasks_store.DB_PATH))
    c.execute("UPDATE tasks SET updated_at = ? WHERE task_id = ?", (ago(**delta), task_id))
    c.commit(); c.close()


def heartbeat(events, sweep):
    hb = [e for e in events if e["kind"] == "info" and e["headline"].startswith("Swept ")
          and f"{sweep} ·" in (e.get("detail") or "")]
    return hb[-1] if hb else None


def counts_from(hb):
    d = hb.get("detail", "")
    import re
    return {k: int(v) for k, v in re.findall(r"(evaluated|flagged|excluded|skipped) (\d+)", d)}


print(f"sweeps — triggered against aged data\n  box={env.BOX}\n  today in {TZ}: {today_local}\n")

# ── 1. the zero case ─────────────────────────────────────────────────────────
print("── 1. NOTHING AGED — every sweep still says it ran ─────────────")
fresh_box()
b = len(feed()); c0 = counters()
for s in ("stale_email", "task_followup", "quiet_lead"):
    rc = run(s)
    ck(f"{s} ran clean", rc == 0, f"rc={rc}")
new = feed()[b:]
for s in ("stale_email", "task_followup", "quiet_lead"):
    hb = heartbeat(new, s)
    ck(f"{s} posted a heartbeat", hb is not None)
    if hb:
        ck("   saying nothing needed attention", "nothing needed attention" in hb["headline"], hb["headline"])
        ck("   and reporting what it evaluated", "evaluated" in hb["detail"] and counts_from(hb).get("flagged") == 0,
           hb["detail"][:80])
ck("no mail went anywhere", len(outbox()) == 0)
ck("the runs were counted", counters().get("sweep_runs", 0) == c0.get("sweep_runs", 0) + 3)

# ── 2. stale email → task ────────────────────────────────────────────────────
print("\n── 2. STALE EMAIL → TASK (slide 15) ────────────────────────────")
fresh_box()
def mk_thread(name, role, **age):
    ch = threads.create_thread(name, created_by="jarvis", creator="dana")
    threads.add_member(ch["thread_id"], "dana", added_by="dana")
    threads.append_messages(ch["thread_id"], {"role": role, "content": "…", "ts": ago(**age)})
    return ch["thread_id"]
T_STALE = mk_thread("Ravenscourt inquiry — unanswered", "user", hours=53)
T_FRESH = mk_thread("Bellhaven closing question", "user", hours=1)
T_ANSWERED = mk_thread("Copperbeam inspection", "user", hours=60)
threads.append_messages(T_ANSWERED, {"role": "assistant", "content": "Replied.", "ts": ago(hours=59)})
T_NEWSLETTER = mk_thread("Weekly market update", "user", hours=70)
T_IMPORTANT = mk_thread("118 Alderwood Ln", "user", hours=70)
notify.notify("emails", "x", headline="Filed a newsletter", kind="email.classified", skill="email-router",
              attachments=[{"type": "email", "subject": "Weekly market update — August", "classification": "D"}])
notify.notify("emails", "x", headline="Filed an email that needs a reply", kind="email.classified",
              skill="email-router",
              attachments=[{"type": "email", "subject": "Re: Sunday showing — 118 Alderwood Ln", "classification": "A"}])
b = len(feed()); c0 = counters(); ntasks = len(tasks_store.query())
rc = run("stale_email")
ck("the sweep ran", rc == 0, f"rc={rc}")
tasks = [t for t in tasks_store.query() if t["source"] == "email"]
ck("exactly the two unanswered, important-or-unknown threads became tasks",
   len(tasks) == 2, str([t["title"] for t in tasks]))
by_ref = {t["source_ref"]: t for t in tasks}
ck("the aged, unclassified thread converted", T_STALE in by_ref)
ck("the aged A-classified thread converted", T_IMPORTANT in by_ref)
ck("the D-classified thread did NOT", T_NEWSLETTER not in by_ref)
ck("the fresh thread did NOT", T_FRESH not in by_ref)
ck("the answered thread did NOT", T_ANSWERED not in by_ref)
t = by_ref.get(T_STALE, {})
ck("title is the thread's name", t.get("title") == "Ravenscourt inquiry — unanswered")
ck("source is email, source_ref is the thread id", t.get("source") == "email" and t.get("source_ref") == T_STALE)
ck(f"deadline is today + grace, in {TZ}", t.get("deadline") == (today_local + timedelta(days=2)).isoformat(),
   str(t.get("deadline")))
ck("assigned to the member the thread belongs to", t.get("assignee") == "dana")
ck("detail says since when", "Unanswered since" in (t.get("detail") or ""), (t.get("detail") or "")[:60])
ck("the A-classified one cites the classification",
   "needs a reply" in (by_ref.get(T_IMPORTANT, {}).get("detail") or "").lower()
   or "from the email on record" in (by_ref.get(T_IMPORTANT, {}).get("detail") or ""),
   (by_ref.get(T_IMPORTANT, {}).get("detail") or "")[:80])
new = feed()[b:]
created = [e for e in new if e["kind"] == "task.created"]
ck("the feed names each new task once", len(created) == 2, str([e["kind"] for e in new]))
ck("   and names the thread", any("Ravenscourt inquiry" in e["headline"] for e in created))
ck("   with the deadline in the detail", all("due 20" in e["detail"] for e in created), str([e["detail"][-30:] for e in created]))
ck("no second, sweep-authored conversion event", not any(e["kind"] not in ("task.created", "task.assigned", "info") for e in new))
hb = heartbeat(new, "stale_email")
ck("heartbeat counts what it evaluated", hb and counts_from(hb).get("evaluated") == 5, str(hb and hb["detail"][:90]))
ck("   and what it excluded", hb and counts_from(hb).get("excluded") == 1)
ck("conversions are counted", counters().get("threads_converted_to_tasks", 0) == c0.get("threads_converted_to_tasks", 0) + 2)
b2 = len(feed()); n2 = len(tasks_store.query())
run("stale_email")
ck("RE-RUN: no new task", len(tasks_store.query()) == n2)
ck("RE-RUN: nothing on the feed but a heartbeat",
   [e["kind"] for e in feed()[b2:]] == ["info"], str([e["kind"] for e in feed()[b2:]]))

# ── 3. task follow-up ────────────────────────────────────────────────────────
print("\n── 3. TASK FOLLOW-UP UNTIL DONE (slide 14) ─────────────────────")
fresh_box()
T_QUIET = tasks_store.create("Chase the payoff letter", created_by="dana", assignee="omar",
                             deadline=(today_local + timedelta(days=3)).isoformat())["task_id"]
backdate_task(T_QUIET, hours=30)
T_FRESHT = tasks_store.create("Book the photographer", created_by="dana", assignee="omar")["task_id"]
T_NOBODY = tasks_store.create("Sort the archive", created_by="dana")["task_id"]
backdate_task(T_NOBODY, hours=80)
b = len(feed()); c0 = counters(); o0 = len(outbox())
rc = run("task_followup")
ck("the sweep ran", rc == 0, f"rc={rc}")
box = outbox()[o0:]
ck("exactly one reminder went out", len(box) == 1, str(len(box)))
m = box[-1] if box else {}
ck("to the ASSIGNEE's team address", m.get("to") == [OMAR_ADDR], str(m.get("to")))
ck("restating the task", m.get("subject") == "Still open: Chase the payoff letter", str(m.get("subject")))
ck("   and its deadline", "Due 20" in (m.get("body") or ""), (m.get("body") or "")[:60])
ck("   naming the person", NAME["omar"] in (m.get("body") or ""))
ck("tagged with the run", (m.get("run_id") or "").startswith("sw_"))
ck("captured, not sent", m.get("captured") is True)
ck("the task is marked quiet", tasks_store.get(T_QUIET)["stale"] is True)
ck("the fresh task was left alone", tasks_store.get(T_FRESHT)["stale"] is False)
ck("the unassigned task is not a candidate", tasks_store.get(T_NOBODY)["stale"] is False)
new = feed()[b:]
ck("one 'gone quiet' event, from the store", len([e for e in new if e["kind"] == "task.stale"]) == 1)
hb = heartbeat(new, "task_followup")
ck("heartbeat: 2 evaluated, 1 flagged", hb and counts_from(hb).get("evaluated") == 2 and counts_from(hb).get("flagged") == 1,
   str(hb and hb["detail"][:80]))
ck("the nudge is counted by sweep", counters().get("nudges_sent.task_followup", 0) == c0.get("nudges_sent.task_followup", 0) + 1)
row = core._db().execute("SELECT * FROM nudges WHERE sweep='task_followup' AND target=?", (T_QUIET,)).fetchone()
ck("the nudge is keyed (target, reason, window)", row and row["reason"] == "nudge" and row["window_key"] == "1",
   str(dict(row) if row else None))
o1 = len(outbox()); b1 = len(feed())
run("task_followup")
ck("RE-RUN: no second reminder", len(outbox()) == o1)
ck("RE-RUN: only a heartbeat", [e["kind"] for e in feed()[b1:]] == ["info"])
tasks_store.set_status(T_QUIET, "in_progress", by="omar")
o2 = len(outbox())
run("task_followup")
ck("REVIVAL: a touch clears quiet", tasks_store.get(T_QUIET)["stale"] is False)
ck("   and sends nothing", len(outbox()) == o2)

# ── 4. the unknown assignee — MANDATORY ──────────────────────────────────────
print("\n── 4. THE ASSIGNEE THE REGISTRY CANNOT NAME — loud, never a skip ─")
fresh_box()
T_BLAKE = tasks_store.create("Return the lockbox to the seller", created_by="dana", assignee="omar")["task_id"]
c = sqlite3.connect(str(tasks_store.DB_PATH))
c.execute("UPDATE tasks SET assignee='bartholomew-quill', updated_at=? WHERE task_id=?", (ago(hours=30), T_BLAKE))
c.commit(); c.close()
b = len(feed()); c0 = counters(); o0 = len(outbox())
rc = run("task_followup")
ck("the sweep ran to completion", rc == 0, f"rc={rc}")
new = feed()[b:]
flags = [e for e in new if e["kind"] == "alert" and "Can't follow up" in e["headline"]]
ck("ONE loud flag on the feed", len(flags) == 1, str([e["headline"][:60] for e in new]))
if flags:
    ck("   it names the task", "Return the lockbox" in flags[0]["headline"], flags[0]["headline"])
    ck("   and says why", "isn't on the team" in flags[0]["headline"])
    ck("   with the unknown name in the detail", "bartholomew-quill" in flags[0]["detail"], flags[0]["detail"][:80])
ck("the task is STILL VISIBLE", any(t["task_id"] == T_BLAKE for t in tasks_store.query()))
ck("   and not marked quiet — nobody was asked", tasks_store.get(T_BLAKE)["stale"] is False)
ck("no reminder went to anyone", len(outbox()) == o0)
ck("it was counted", counters().get("unresolvable_assignees", 0) == c0.get("unresolvable_assignees", 0) + 1)
hb = heartbeat(new, "task_followup")
ck("the heartbeat still fired", hb is not None)

# ── 5. masking ───────────────────────────────────────────────────────────────
print("\n── 5. AN OUTSIDE ADDRESS NEVER REACHES THE TENANT SURFACE ──────")
fresh_box()
ident = json.loads((TESTCO_SKILLS / "_shared" / "identity.json").read_text())
ident["people"]["blake"] = {"name": "Blake Dennison", "display_name": "Blake",
                            "roles": ["internal"], "email": ["blake.dennison@gmail.com"]}
SCRATCH_ID = env.BOX / "identity.json"
SCRATCH_ID.write_text(json.dumps(ident, indent=1))
identity.CONFIG_PATH = SCRATCH_ID
identity.load(force=True)
T_OUT = tasks_store.create("Send the HOA packet", created_by="dana", assignee="blake")["task_id"]
backdate_task(T_OUT, hours=30)
b = len(feed()); o0 = len(outbox())
err = io.StringIO()
with contextlib.redirect_stderr(err):
    rc = run("task_followup")
new = feed()[b:]
blob = " ".join((e.get("headline", "") + " " + e.get("detail", "")) for e in new)
ck("the refusal is announced", any("isn't a team address" in e["headline"] for e in new), str([e["headline"][:70] for e in new]))
ck("the address is MASKED on the feed", "b***@***.com" in blob, blob[:120])
ck("   and never appears in full", "blake.dennison@gmail.com" not in blob)
ck("the full address went to the journal", "blake.dennison@gmail.com" in err.getvalue())
ck("nothing was sent", len(outbox()) == o0)
ck("the boundary was never reached — no boundary refusal event",
   not any("outside the test tenant" in e["headline"] for e in new))
identity.CONFIG_PATH = TESTCO_SKILLS / "_shared" / "identity.json"
identity.load(force=True)

# ── 6. quiet leads ───────────────────────────────────────────────────────────
print("\n── 6. QUIET-LEAD NUDGE (slide 16) ──────────────────────────────")
fresh_box()
def mk_lead(name, **kw):
    return leads_store.create(name, created_by="jarvis", **kw)["lead_id"]
L_QUIET = mk_lead("Wilhelmina Ashgrove", property="1408 Thornbury Row", status="No Response",
                  temperature="Warm", owner_person="dana", last_activity_at=ago(days=41), next_touch_at=ago(days=27))
L_HOT = mk_lead("Marcus Webb", property="2 Bellhaven Ct", status="Responded", temperature="Hot",
                owner_person="dana", last_activity_at=ago(hours=30))
L_WARM30 = mk_lead("Ada Whitfield", property="9 Copperbeam Dr", status="New", temperature="Warm",
                   owner_person="dana", last_activity_at=ago(hours=30))
L_DEAD = mk_lead("Cleo Marsh", status="Not Interested", owner_person="dana", last_activity_at=ago(days=40))
L_NOBODY = mk_lead("Dev Raman", status="New", last_activity_at=ago(days=40))
L_PLANNED = mk_lead("Esi Boateng", property="5 Kestrel Row", status="No Response", temperature="Warm",
                    owner_person="dana", last_activity_at=ago(days=20), next_touch_at=(NOW + timedelta(days=4)).isoformat())
L_ARCH = mk_lead("Old Copy", status="No Response", owner_person="dana", last_activity_at=ago(days=90))
leads_store.set_archived(L_ARCH, True)
b = len(feed()); c0 = counters(); o0 = len(outbox())
rc = run("quiet_lead")
ck("the sweep ran", rc == 0, f"rc={rc}")
box = outbox()[o0:]
ck("exactly two nudges went out — the quiet Warm one and the Hot one", len(box) == 2, str([m["subject"] for m in box]))
ck("both to the AGENT, never the lead", all(m["to"] == [DANA_ADDR] for m in box), str([m["to"] for m in box]))
subj = " | ".join(m["subject"] for m in box)
ck("naming the lead", "Wilhelmina Ashgrove" in subj and "Marcus Webb" in subj, subj)
body = " ".join(m["body"] for m in box)
ck("naming the property and days quiet", "1408 Thornbury Row" in body and "Quiet for 41 days" in body)
q = leads_store.get(L_QUIET)
ck(f"the lead got a follow-up date of today ({TZ})", q["next_touch_at"] == today_local.isoformat(), str(q["next_touch_at"]))
ck("   and an activity line the tab shows", q["follow_up"] == f"Gone quiet — nudged {NAME['dana']}", str(q["follow_up"]))
ck("   with its status untouched", q["status"] == "No Response")
ck("a Warm lead quiet for 30h is NOT nudged", not any("Ada Whitfield" in m["subject"] for m in box))
ck("a lead with a FUTURE scheduled touch is NOT nudged", not any("Esi Boateng" in m["subject"] for m in box))
ck("a dead lead is NOT nudged", not any("Cleo Marsh" in m["subject"] for m in box))
ck("an archived lead is NOT nudged", not any("Old Copy" in m["subject"] for m in box))
new = feed()[b:]
qe = [e for e in new if e["kind"] == "lead.quiet"]
ck("the feed mirrors both nudges", len(qe) == 2, str([e["kind"] for e in new]))
ck("   in a sentence naming lead, property and agent",
   any("Wilhelmina Ashgrove at 1408 Thornbury Row has gone quiet — nudged" in e["headline"] for e in qe),
   str([e["headline"] for e in qe][:1]))
ck("   scoped to the agent", all(e["attachments"][0].get("owner_person") == "dana" for e in qe))
ck("quiet leads are counted", counters().get("leads_flagged_quiet", 0) == c0.get("leads_flagged_quiet", 0) + 2)
ck("nudges are counted by sweep", counters().get("nudges_sent.quiet_lead", 0) == c0.get("nudges_sent.quiet_lead", 0) + 2)
o1 = len(outbox())
run("quiet_lead")
ck("RE-RUN: nothing new", len(outbox()) == o1)
leads_store.update(L_QUIET, last_activity_at=NOW.isoformat())
o2 = len(outbox())
run("quiet_lead")
ck("A TOUCHED LEAD resets cleanly — no nudge", len(outbox()) == o2)

# ── 7. config is the source of truth ─────────────────────────────────────────
print("\n── 7. A THRESHOLD CHANGED IN CONFIG CHANGES BEHAVIOUR ──────────")
fresh_box()
T_30 = tasks_store.create("Confirm the closing time", created_by="dana", assignee="omar")["task_id"]
backdate_task(T_30, hours=30)
write_runtime(task_quiet_hours=240)
b = len(feed()); o0 = len(outbox())
run("task_followup")
ck("with a 240h window the 30h task is NOT quiet", len(outbox()) == o0 and tasks_store.get(T_30)["stale"] is False)
hb = heartbeat(feed()[b:], "task_followup")
ck("   and the heartbeat says so", hb and "nothing needed attention" in hb["headline"])
write_runtime()
run("task_followup")
ck("with the default window it IS", len(outbox()) == o0 + 1 and tasks_store.get(T_30)["stale"] is True)

# ── 8. failure announces ─────────────────────────────────────────────────────
print("\n── 8. A SWEEP THAT DIES SAYS SO ────────────────────────────────")
fresh_box()
b = len(feed()); c0 = counters()
orig_run = task_followup.run
def boom(ctx):
    raise RuntimeError("simulated store outage")
task_followup.run = lambda ctx: (ctx.fail(RuntimeError("simulated store outage")), (_ for _ in ()).throw(RuntimeError("x")))
err = io.StringIO()
with contextlib.redirect_stderr(err):
    rc = run("task_followup")
task_followup.run = orig_run
new = feed()[b:]
snag = [e for e in new if e["kind"] == "alert" and "hit a snag" in e["headline"]]
ck("LAYER 1: the sweep announced its own failure", len(snag) == 1, str([e["headline"][:60] for e in new]))
ck("   as a plain sentence", snag and snag[0]["headline"].startswith("The task sweep hit a snag"), snag and snag[0]["headline"])
ck("   with the error class in the detail, not the message",
   snag and "RuntimeError" in snag[0]["detail"] and "simulated" not in snag[0]["detail"])
ck("   and the exit code says failed", rc == 1, f"rc={rc}")
ck("   no heartbeat pretended it ran", heartbeat(new, "task_followup") is None)
ck("the failure is counted", counters().get("sweep_failures", 0) == c0.get("sweep_failures", 0) + 1)
r = core._db().execute("SELECT status, error FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
ck("the run record says failed", r and r["status"] == "failed" and r["error"] == "RuntimeError", str(dict(r) if r else None))
b = len(feed()); c0 = counters()
orig_load = sweep_tick._load
sweep_tick._load = lambda name: (_ for _ in ()).throw(ImportError("no module named nothing"))
with contextlib.redirect_stderr(io.StringIO()):
    rc = run("quiet_lead")
sweep_tick._load = orig_load
new = feed()[b:]
ck("LAYER 2: a crash before the sweep even starts is announced by the runner",
   any("hit a snag" in e["headline"] for e in new) and rc == 1, str([e["headline"][:60] for e in new]))
ck("   and counted", counters().get("sweep_failures", 0) == c0.get("sweep_failures", 0) + 1)

# ── 9. escalation ────────────────────────────────────────────────────────────
print("\n── 9. AFTER N UNANSWERED REMINDERS, THE OWNER HEARS ────────────")
fresh_box()
T_ESC = tasks_store.create("File the quarterly returns", created_by="dana", assignee="omar")["task_id"]
c0 = counters(); b = len(feed())
for h in (30, 54, 78):
    backdate_task(T_ESC, hours=h)
    run("task_followup")
box = outbox()
to_omar = [m for m in box if m["to"] == [OMAR_ADDR]]
to_owner = [m for m in box if m["to"] == [DANA_ADDR]]
ck("three reminders reached the assignee", len(to_omar) == 3, str(len(to_omar)))
ck("exactly ONE escalation reached the owner", len(to_owner) == 1, str([m["subject"] for m in to_owner]))
ck("   derived from the registry, not written down", to_owner and to_owner[0]["to"] == [identity.roster()[identity.owner_key()]])
ck("   and it says how many reminders went unanswered", to_owner and "after 3 reminders" in to_owner[0]["subject"])
esc = [e for e in feed()[b:] if e["kind"] == "alert" and e["headline"].startswith("Escalating")]
ck("the feed carries the escalation", len(esc) == 1, str([e["headline"] for e in esc]))
ck("   naming who and how many", esc and NAME["omar"] in esc[0]["headline"] and "3 reminders" in esc[0]["headline"])
ck("   scoped to the owner", esc and esc[0]["attachments"][0].get("owner_person") == "dana")
ck("escalations are counted", counters().get("escalations", 0) == c0.get("escalations", 0) + 1)
backdate_task(T_ESC, hours=102)
run("task_followup")
ck("a 4th reminder does NOT re-escalate", len([m for m in outbox() if m["to"] == [DANA_ADDR]]) == 1)

# ── 10. the counters this prompt registers ───────────────────────────────────
print("\n── 10. COUNTERS ────────────────────────────────────────────────")
# fresh_box() wipes threads.db between sections, so the live table only holds
# the last section's bumps. The per-section deltas above are the real proof
# each counter moves; this just confirms every name this prompt registers was
# seen moving at some point in the run.
counters()
vals = SEEN
for name in ("sweep_runs", "sweep_failures", "nudges_sent.task_followup", "nudges_sent.quiet_lead",
             "threads_converted_to_tasks", "leads_flagged_quiet", "escalations", "unresolvable_assignees"):
    ck(f"{name} is registered and moved", vals.get(name, 0) > 0, str(vals.get(name)))

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
