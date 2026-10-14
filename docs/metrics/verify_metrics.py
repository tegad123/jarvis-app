#!/usr/bin/env python3
"""PROMPT 6 — counters & health surface, asserted by TRIGGERING each seam.

Every number here is produced by running the thing that produces it in
production: a task completed through the store, a showing answered by the
handler, a quiet-lead sweep, a digest reply through the poller's own reply
path. The assertion is on the MEASUREMENT that changed — never on the row
count of a table this suite just wrote to, and never on a function's return
value when a row can be read instead.

Runs inside the docs/tasks/env.py box: every store, the feed and sweeps.db
live under BOX; the live tenant and testco's real state are never touched.
The role check hits the verify sandbox (docs/tasks/tasks_sandbox.sh) on :8711.

Run:  /root/jarvis-app/.venv/bin/python docs/metrics/verify_metrics.py [-v] [--no-live]
"""

import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tasks"))
import env  # noqa: E402

env.apply()
os.environ["HERMES_HOME"] = str(env.BOX)          # sweeps.db and the outbox live under the home
os.environ["JARVIS_SHEET_FIXTURE"] = "/opt/jarvis-verify-fixture/fixtures/sheet_fixture.json"
BOX = env.BOX
for name in ("tasks.db", "threads.db", "leads.db", "feed.jsonl", "approvals.jsonl"):
    (BOX / name).unlink(missing_ok=True)
shutil.rmtree(BOX / "state", ignore_errors=True)
shutil.rmtree(BOX / "outbox", ignore_errors=True)

VERBOSE = "-v" in sys.argv
LIVE = "--no-live" not in sys.argv
PYBIN = "/root/jarvis-app/.venv/bin/python"
SK = env.SKILLS_CODE
FIXTURES = pathlib.Path("/opt/jarvis-verify-fixture/fixtures")
BASE = os.environ.get("VERIFY_BASE", "http://127.0.0.1:8711").rstrip("/")
if BASE.endswith(":8700"):
    sys.exit("REFUSING: :8700 is the live tenant.")

ok = []
skipped = []
def ck(name, cond, extra=""):
    ok.append(bool(cond))
    if not cond or VERBOSE:
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))
    else:
        print(f"  PASS  {name}")

from app import tasks_store, threads, metrics, leads_store  # noqa: E402
import tenant_config as tc  # noqa: E402
import notify  # noqa: E402
import identity  # noqa: E402
OWNER_ADDR = identity.owner_address()

resolved = env.assert_isolated()
assert str(pathlib.Path(notify.feed_path()).resolve()).startswith(str(BOX.resolve()))
tasks_store.init(); threads.init(); leads_store.init(); metrics._conn()   # the DDL is lazy

def feed():
    p = BOX / "feed.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

def tdb():
    c = sqlite3.connect(str(BOX / "threads.db")); c.row_factory = sqlite3.Row
    return c

def day_total(name):
    r = tdb().execute("SELECT COALESCE(SUM(value),0) v FROM counter_days WHERE name=? AND day=?",
                      (name, metrics.today())).fetchone()
    return int(r["v"])

def samples(name):
    return [dict(r) for r in tdb().execute(
        "SELECT * FROM measurements WHERE name=? ORDER BY id", (name,))]

print(f"metrics — triggered assert\n  box={BOX}  today={metrics.today()} ({metrics._tz()})\n")

# ── 1. the store ─────────────────────────────────────────────────────────────
print("── 1. store: a bump lands in today's bucket; a sample keeps no address ──")
before = day_total("verify_probe")
threads.bump_counter("verify_probe", 3)
ck("bump_counter writes today's daily bucket (+3)", day_total("verify_probe") == before + 3)
ck("the all-time counter still moves", threads.all_counters()["verify_probe"]["value"] >= 3)
metrics.record("verify_sample", 12.5, ref="dana@testco.example")
metrics.record("verify_sample", 7.0, ref="tk_abc123")
rows = samples("verify_sample")
ck("a measurement row carries name, value, ts, day, ref",
   len(rows) == 2 and rows[1]["value"] == 7.0 and rows[1]["day"] == metrics.today())
ck("an address handed as ref is REFUSED (stored as NULL), an internal id is kept",
   rows[0]["ref"] is None and rows[1]["ref"] == "tk_abc123")
ck("retention key: skills.metrics.retention_days read from tenant config (180 default)",
   metrics.retention_days() == 180, str(metrics.retention_days()))
old_day = (datetime.now(metrics._tz()).date() - timedelta(days=200)).isoformat()
with tdb() as c:
    c.execute("INSERT INTO counter_days (name, day, value) VALUES ('verify_old', ?, 9)", (old_day,))
    c.execute("INSERT INTO measurements (name, value, ts, day, ref) VALUES ('verify_old', 1, ?, ?, NULL)",
              (old_day + "T00:00:00+00:00", old_day))
metrics._pruned = False
threads.bump_counter("verify_probe")
c = tdb()
ck("rows older than the retention window are pruned on the next write (200 days > 180)",
   c.execute("SELECT COUNT(*) FROM counter_days WHERE name='verify_old'").fetchone()[0] == 0
   and c.execute("SELECT COUNT(*) FROM measurements WHERE name='verify_old'").fetchone()[0] == 0)
ck("a row inside the window survives the prune", day_total("verify_probe") == before + 4)

# ── 2. deadlines ─────────────────────────────────────────────────────────────
print("\n── 2. deadlines: completed before / after / without a deadline ──────────")
today = datetime.now(metrics._tz()).date()
base = {n: day_total(n) for n in ("tasks_completed_on_time", "tasks_completed_late",
                                  "tasks_completed_no_deadline", "tasks_completed")}
t_on = tasks_store.create("Verify on-time", created_by="dana", deadline=(today + timedelta(days=2)).isoformat())
t_late = tasks_store.create("Verify late", created_by="dana", deadline=(today - timedelta(days=1)).isoformat())
t_none = tasks_store.create("Verify undated", created_by="dana")
for t in (t_on, t_late, t_none):
    tasks_store.set_status(t["task_id"], "done", by="dana")
ck("done 2 days before deadline → tasks_completed_on_time +1",
   day_total("tasks_completed_on_time") == base["tasks_completed_on_time"] + 1)
ck("done a day after deadline → tasks_completed_late +1",
   day_total("tasks_completed_late") == base["tasks_completed_late"] + 1)
ck("done with no deadline → tasks_completed_no_deadline +1 (not silently on-time)",
   day_total("tasks_completed_no_deadline") == base["tasks_completed_no_deadline"] + 1)
ck("tasks_completed +3 in the same period bucket", day_total("tasks_completed") == base["tasks_completed"] + 3)
tasks_store.set_status(t_on["task_id"], "done", by="dana")
ck("re-marking a done task done does not count twice",
   day_total("tasks_completed_on_time") == base["tasks_completed_on_time"] + 1)
t_edge = tasks_store.create("Verify due today", created_by="dana", deadline=today.isoformat())
tasks_store.set_status(t_edge["task_id"], "done", by="dana")
ck("due today, done today → on time (date compare in the tenant's zone, not UTC)",
   day_total("tasks_completed_on_time") == base["tasks_completed_on_time"] + 2)

# ── 3. response latency ──────────────────────────────────────────────────────
print("\n── 3. response latency: showing received-at → reply sent-at ─────────────")
from email import policy
from email import message_from_binary_file
scratch = pathlib.Path(tempfile.mkdtemp(prefix="metrics-ss-"))
rt = scratch / "runtime.json"; idn = scratch / "identity.json"
shutil.copy(os.environ["JARVIS_RUNTIME_CONFIG"], rt); shutil.copy(env.TESTCO_SKILLS / "_shared" / "identity.json", idn)
cfg = json.loads(rt.read_text()); cfg["skills"].setdefault("showingsmart-autoreply", {})["standing_authorization"] = True
rt.write_text(json.dumps(cfg))
ss_env = dict(os.environ, JARVIS_RUNTIME_CONFIG=str(rt), JARVIS_IDENTITY_CONFIG=str(idn))
with open(FIXTURES / "showingsmart_inbound.eml", "rb") as fh:
    msg = message_from_binary_file(fh, policy=policy.default)
received = (datetime.now(timezone.utc) - timedelta(seconds=95)).isoformat()
payload = {"from": msg.get("From", ""), "subject": msg.get("Subject", ""), "body": msg.get_content(),
           "to": msg.get("To", ""), "cc": msg.get("Cc", ""), "message_id": f"<metrics-{os.getpid()}@verify>",
           "received_at": received}
state = SK / "showingsmart-autoreply" / "data" / "processed_showings.json"
saved_state = state.read_bytes() if state.exists() else None
if state.exists():
    state.unlink()
cal = SK / "task-manager" / "data" / "calendar_events.json"
saved_cal = cal.read_bytes() if cal.exists() else None
if cal.exists():
    log = json.loads(cal.read_text()); cal.write_text(json.dumps({k: v for k, v in log.items() if not k.startswith("showing:")}))
b_ans = day_total("showings_answered"); b_lat = len(samples("response_latency_seconds"))
proc = subprocess.run([PYBIN, str(SK / "showingsmart-autoreply" / "scripts" / "handle_showing_request.py"),
                       "--run-id", "metrics-verify"], input=json.dumps(payload).encode(),
                      capture_output=True, timeout=180, env=ss_env)
out = (proc.stdout or b"").decode().strip().splitlines()
res = json.loads(out[-1]) if out else {}
if VERBOSE:
    print("        handler:", res.get("status"), (proc.stderr or b"")[-300:].decode())
# put the tenant's real dedupe state back exactly as it was
if saved_state is not None: state.write_bytes(saved_state)
elif state.exists(): state.unlink()
if saved_cal is not None: cal.write_bytes(saved_cal)
lat = samples("response_latency_seconds")[b_lat:]
ck("the handler answered the showing (status handled, mail captured under the box's home)",
   res.get("status") == "handled" and res.get("send", {}).get("captured") is True, str(res)[:160])
ck("showings_answered +1 in today's bucket", day_total("showings_answered") == b_ans + 1)
ck("one response_latency_seconds sample, ≈95 s (received-at → sent-at), not a fixed number",
   len(lat) == 1 and 90 <= lat[0]["value"] <= 150, str([r["value"] for r in lat]))
ck("the sample's ref is the showing key, not the agent's address",
   len(lat) == 1 and lat[0]["ref"] == res.get("key") and "@" not in (lat[0]["ref"] or ""))
addr = res.get("address", "918 Ravenscourt")
ck("no raw address anywhere in the metrics tables",
   not any(addr in json.dumps(dict(r)) for r in tdb().execute("SELECT * FROM measurements"))
   and not any(addr in r[0] for r in tdb().execute("SELECT name FROM counter_days")))
b_lat2 = len(samples("response_latency_seconds"))
payload2 = dict(payload); payload2.pop("received_at")
if state.exists():
    state.unlink()
subprocess.run([PYBIN, str(SK / "showingsmart-autoreply" / "scripts" / "handle_showing_request.py"),
                "--run-id", "metrics-verify-2"], input=json.dumps(payload2).encode(),
               capture_output=True, timeout=180, env=ss_env)
if saved_state is not None: state.write_bytes(saved_state)
elif state.exists(): state.unlink()
if saved_cal is not None: cal.write_bytes(saved_cal)
ck("a payload with no received-at records NO latency sample (nothing invented)",
   len(samples("response_latency_seconds")) == b_lat2)

# ── 4. leads revived after a nudge ───────────────────────────────────────────
print("\n── 4. leads: touched within 48 h of a nudge counts as revived ───────────")
sys.path.insert(0, str(SK / "sweeps" / "scripts"))
import sweep_core as core  # noqa: E402
import quiet_lead  # noqa: E402
core.reset_state_connection()
now = datetime.now(timezone.utc)
L1 = leads_store.create("Verify Revived", owner_person="dana", last_activity_at=(now - timedelta(hours=1)).isoformat())
L2 = leads_store.create("Verify Late", owner_person="dana", last_activity_at=(now - timedelta(hours=2)).isoformat())
L3 = leads_store.create("Verify Untouched", owner_person="dana", last_activity_at=(now - timedelta(hours=1)).isoformat())
with core._db() as c:
    for lead, sent in ((L1, now - timedelta(hours=10)), (L2, now - timedelta(hours=80)), (L3, now + timedelta(minutes=5))):
        c.execute("INSERT INTO nudges (sweep, target, reason, window_key, sent_at, run_id, recipient_key) "
                  "VALUES ('quiet_lead', ?, 'nudge', '0', ?, 'seed', 'dana')", (lead["lead_id"], sent.isoformat()))
b = {n: day_total(n) for n in ("leads_revived_after_nudge", "leads_touched_late_after_nudge")}
b_rev = len(samples("lead_revival_hours"))
ctx = core.SweepRun("quiet_lead"); quiet_lead.run(ctx)
ck("sweep ran clean", not ctx.failed)
ck("touched 9 h after the nudge → leads_revived_after_nudge +1",
   day_total("leads_revived_after_nudge") == b["leads_revived_after_nudge"] + 1)
ck("touched 78 h after the nudge → leads_touched_late_after_nudge +1 (not revived)",
   day_total("leads_touched_late_after_nudge") == b["leads_touched_late_after_nudge"] + 1)
rev = samples("lead_revival_hours")[b_rev:]
by_ref = {r["ref"]: r["value"] for r in rev}
ck("two lead_revival_hours samples, ≈9 h and ≈78 h, ref = lead id",
   len(rev) == 2 and 8.5 <= by_ref.get(L1["lead_id"], 0) <= 9.5 and 77 <= by_ref.get(L2["lead_id"], 0) <= 79,
   str([(r["value"], r["ref"]) for r in rev]))
ctx2 = core.SweepRun("quiet_lead"); quiet_lead.run(ctx2)
ck("a second sweep does not count the same revival twice",
   day_total("leads_revived_after_nudge") == b["leads_revived_after_nudge"] + 1
   and len(samples("lead_revival_hours")) == b_rev + 2)
ck("a lead not touched since its nudge is left for a later run (no verdict row)",
   not core.already_nudged("quiet_lead", L3["lead_id"], "revived", "0@" + (now + timedelta(minutes=5)).isoformat()[:19]))

# ── 5. digest reply-resolution for a store tenant (folded scope) ─────────────
print("\n── 5. digest reply: 'done N' / 'priority N' resolve against the app store ──")
sys.path.insert(0, str(SK / "gmail-push-trigger" / "scripts"))
import importlib
poller = importlib.import_module("poll_gmail_history")
tc.reset_cache()
d1 = tasks_store.create("Verify digest one", created_by="dana", assignee="dana")
d2 = tasks_store.create("Verify digest two", created_by="dana", assignee="dana")
rfc = f"<digest-metrics-{os.getpid()}@testco.example>"
tasks_store.record_digest("dg_verify", "dana", rfc, None, [(1, d1["task_id"]), (2, d2["task_id"])])
rows = poller.find_digest_by_rfc_message_id(rfc)
ck("the poller resolves the Message-ID against jarvis_tasks.db.digest_map (store tenant)",
   [(r[0], r[1]) for r in rows] == [(1, d1["task_id"]), (2, d2["task_id"])], str(rows))
b = {n: day_total(n) for n in ("digest_replies_resolved", "digest_replies_unmatched", "tasks_completed")}
r = poller.process_digest_reply("done 1\npriority 2 high\ndone 7\n", rows, None, "reply-msg-A", rfc,
                                "Re: Your tasks", OWNER_ADDR)
ck("'done 1' completed the store row (status read back from the row, not the reply)",
   tasks_store.get(d1["task_id"])["status"] == "done")
ck("'priority 2 high' changed the store row's priority", tasks_store.get(d2["task_id"])["priority"] == "high")
ck("digest_replies_resolved +2, digest_replies_unmatched +1 (position 7 is not in this digest)",
   day_total("digest_replies_resolved") == b["digest_replies_resolved"] + 2
   and day_total("digest_replies_unmatched") == b["digest_replies_unmatched"] + 1)
ck("the completion went through the store (tasks_completed +1) with the replier as actor",
   day_total("tasks_completed") == b["tasks_completed"] + 1 and r.get("completions") == 1)
tasks_store.update(d2["task_id"], priority="low")
r2 = poller.process_digest_reply("done 1\npriority 2 high\n", rows, None, "reply-msg-A", rfc, "Re: Your tasks", OWNER_ADDR)
ck("the same reply message id a second time is a no-op (idempotent on the reply id)",
   r2.get("duplicate") is True and tasks_store.get(d2["task_id"])["priority"] == "low"
   and day_total("digest_replies_resolved") == b["digest_replies_resolved"] + 2)
os.environ["JARVIS_TASKS_SOURCE"] = "legacy"; tc.reset_cache()
import app_tasks; ck("a legacy tenant never reaches the app store from the reply path",
                     poller._app_tasks_mod() is None and poller.find_digest_by_rfc_message_id(rfc) == [])
os.environ["JARVIS_TASKS_SOURCE"] = "store"; tc.reset_cache()

# ── 6. metrics store down ────────────────────────────────────────────────────
print("\n── 6. store down: the operation succeeds, ONE announcement per run ──────")
down = pathlib.Path(tempfile.mkdtemp(prefix="metrics-down-"))
blocker = down / "threads.db"; blocker.mkdir()   # a DIRECTORY where the db file must be → sqlite cannot open
code = r'''
import os, sys, json
sys.path.insert(0, os.environ["JARVIS_APP_ROOT"]); sys.path.insert(0, os.path.join(os.environ["JARVIS_SKILLS_ROOT"], "_shared"))
from app import tasks_store
t = tasks_store.create("Verify while metrics down", created_by="dana", deadline="2030-01-01")
tasks_store.set_status(t["task_id"], "done", by="dana")
t2 = tasks_store.create("Verify second while down", created_by="dana")
tasks_store.set_status(t2["task_id"], "done", by="dana")
print(json.dumps({"t": t["task_id"], "status": tasks_store.get(t["task_id"])["status"],
                  "status2": tasks_store.get(t2["task_id"])["status"]}))
'''
n_feed = len(feed())
p = subprocess.run([PYBIN, "-c", code], capture_output=True, text=True,
                   env=dict(os.environ, JARVIS_THREADS_DB=str(blocker)))
outj = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
ck("both tasks were created and completed with the metrics store unreachable",
   p.returncode == 0 and outj.get("status") == "done" and outj.get("status2") == "done", p.stderr[-200:])
ck("the task row is in the tasks store (the operation, not the counter, is what matters)",
   tasks_store.get(outj.get("t", "")) is not None)
ann = [e for e in feed()[n_feed:] if "not being recorded" in (e.get("headline") or "")]
ck("exactly ONE 'Usage numbers are not being recorded' feed event across several failed bumps",
   len(ann) == 1, f"{len(ann)} events")
ck("the announcement names no path, no address, and says the work still went through",
   len(ann) == 1 and "@" not in json.dumps(ann[0]) and "/" not in ann[0]["headline"]
   and "still went through" in (ann[0].get("detail") or ""))
ck("stderr carries the cause for the operator's journal", "[metrics] store unreachable" in p.stderr
   or "unable to open" in p.stderr, p.stderr[-160:])

# ── 7. the monthly feed line ─────────────────────────────────────────────────
print("\n── 7. monthly line: humanised, from measured numbers, once per month ────")
ym = datetime.now(metrics._tz()).strftime("%Y-%m")
mr = SK / "sweeps" / "scripts" / "monthly_report.py"
p = subprocess.run([PYBIN, str(mr), "--for", ym, "--dry-run"], capture_output=True, text=True, env=dict(os.environ))
dry = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
head = dry.get("headline", "")
ck("dry run builds the line without posting", dry.get("status") == "dry_run" and len(feed()) == len(feed()))
ck("line reads 'This month in numbers: N showings answered, median response …; N tasks completed, N before deadline; N leads nudged back'",
   re.search(r"^\w+ in numbers: 2 showings answered, median response \d+ (seconds|min \d\d s); "
             r"\d+ tasks completed, \d+ before deadline; 1 lead nudged back( of \d+ gone quiet)?$", head) is not None, head)
ck("the median is the measured latency (≈95 s → '1 min 35 s'), not a slogan",
   re.search(r"median response 1 min (3[0-9]|4[0-9]|5[0-9]) s", head) is not None, head)
ck("no address, no lead name, no task title, no property in the line",
   "@" not in head + dry.get("detail", "") and "Verify" not in head + dry.get("detail", "")
   and "Ravenscourt" not in head + dry.get("detail", ""))
n_feed = len(feed()); b_posted = day_total("monthly_reports_posted")
p = subprocess.run([PYBIN, str(mr), "--for", ym, "--now"], capture_output=True, text=True, env=dict(os.environ))
posted = [e for e in feed()[n_feed:] if e.get("kind") == "metrics.monthly"]
ck("posting lands ONE metrics.monthly event on the feed with that headline",
   len(posted) == 1 and posted[0]["headline"] == head, p.stderr[-200:])
ck("state file records the month; a plain re-run says already_reported",
   json.loads((BOX / "state" / "metrics_monthly_last.json").read_text())["reported"] == ym
   and "already_reported" in subprocess.run([PYBIN, str(mr), "--for", ym], capture_output=True, text=True,
                                            env=dict(os.environ)).stdout)
ck("monthly_reports_posted +1", day_total("monthly_reports_posted") == b_posted + 1)

# ── 8. the summary, in-process and over HTTP as four identities ──────────────
print("\n── 8. /api/health/summary: shape in the box, roles on live testco ───────")
s = metrics.summary()
ck("summary carries period, retention_days, counters{month,last_7d,last_30d,all_time}, measurements, sweeps",
   set(s) >= {"period", "retention_days", "counters", "measurements", "sweeps"}
   and s["period"]["month"] == ym and s["retention_days"] == 180
   and set(s["counters"]["tasks_completed"]) == {"month", "last_7d", "last_30d", "all_time"})
ck("current-period counters reflect what this run triggered",
   s["counters"]["showings_answered"]["month"] >= 2 and s["counters"]["digest_replies_resolved"]["last_7d"] >= 2
   and s["counters"]["leads_revived_after_nudge"]["last_30d"] >= 1)
ck("latency stats in the summary: count 1 (the second showing carried no received-at), median ≈95 s",
   s["measurements"]["response_latency_seconds"]["last_30d"]["count"] == 1
   and 90 <= s["measurements"]["response_latency_seconds"]["last_30d"]["median"] <= 150)
ck("sweep failure count is on the summary", "failures_30d" in s["sweeps"])
ck("the whole summary carries no address and no lead/task name",
   "@" not in json.dumps(s) and "Verify" not in json.dumps(s))

# The gate itself, in-process: the live check below sees the RUNNING copy of
# the app, which a mutation of this tree cannot reach. Importing main runs
# its boot checks; this box has no platform database, so Sentinel is off.
os.environ["JARVIS_SWITCH_SENTINEL"] = "off"
from app import main as app_main  # noqa: E402
from fastapi import HTTPException  # noqa: E402
def gate(sess):
    try:
        return 200 if isinstance(app_main.get_health_summary(sess), dict) else 0
    except HTTPException as exc:
        return exc.status_code
ck("gate in-process: owner 200 · admin 200 · operator 200 · member 403",
   [gate({"role": r, "person_key": k}) for r, k in
    (("owner", "dana"), ("admin", "priya"), ("operator", None), ("member", "omar"))] == [200, 200, 200, 403])

if LIVE:
    PINS = json.loads(pathlib.Path("/opt/jarvis-verify-fixture/SEEDED_PINS.json").read_text())
    if isinstance(next(iter(PINS.values())), dict):
        PINS = {k: v.get("pin") for k, v in PINS.items()}
    passphrase = os.environ.get("VERIFY_OPERATOR_PASSPHRASE", "")
    ck("live: VERIFY_OPERATOR_PASSPHRASE is set (value never printed)", bool(passphrase))
    jars = pathlib.Path(tempfile.mkdtemp(prefix="metrics-jars-"))
    def call(path, method="GET", body=None, jar=None):
        cmd = ["curl", "-s", "-o", str(jars / "body"), "-w", "%{http_code}", "-X", method, BASE + path]
        if jar: cmd += ["-b", jar, "-c", jar]
        if body is not None: cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
        code = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
        try: data = json.loads((jars / "body").read_text())
        except Exception: data = {}
        return int(code or 0), data
    def login(who):
        jar = str(jars / f"{who}.jar")
        body = {"passphrase": passphrase} if who == "operator" else {"person_key": who, "pin": PINS[who]}
        code, d = call("/api/login", "POST", body, jar)
        return jar if d.get("ok") else None
    code, _ = call("/api/health/summary")
    ck("live: unauthenticated → 401", code == 401, str(code))
    expect = {"dana": 200, "priya": 200, "omar": 403, "operator": 200}
    got = {}
    for who, want in expect.items():
        jar = login(who)
        if jar is None:
            ck(f"live: login as {who}", False); continue
        code, d = call("/api/health/summary", jar=jar)
        got[who] = code
        ck(f"live: {who} → {want}", code == want,
           f"got {code}" + (f" · {d.get('detail')}" if code != 200 else ""))
        if code == 200:
            ck(f"live: {who}'s body has counters/period/sweeps and no address",
               set(d) >= {"counters", "period", "sweeps"} and "@" not in json.dumps(d))
    ck("live: member is refused with a sentence, not a stack", got.get("omar") == 403)
    LIVE_RESULT = got
else:
    skipped.append("live role check (--no-live)")
    LIVE_RESULT = {}

# ── 9. isolation held ────────────────────────────────────────────────────────
print("\n── 9. isolation ──────────────────────────────────────────────────────")
ck("every store resolved under the box", all(str(p.resolve()).startswith(str(BOX.resolve())) for p in resolved.values()))
ck("the box's outbox captured the showing mails (nothing left the box's home)",
   (BOX / "outbox" / "captured_mail.jsonl").exists())

print(f"\n{sum(ok)}/{len(ok)} passed" + (f" · NOT VERIFIED: {', '.join(skipped)}" if skipped else ""))
if LIVE_RESULT:
    print("role results:", LIVE_RESULT)
sys.exit(0 if all(ok) else 1)
