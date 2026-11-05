#!/usr/bin/env python3
"""Owner-only deletion of one tenant's local records.

Two synthetic tenants. The process under test is tenant A. Tenant B is another
directory this process is not configured to touch. Nothing here talks to a
live tenant or to the network.

Proves: a member and an admin cannot delete; GET does not delete; a missing
header, a cross-origin Origin, a wrong PIN and a wrong phrase delete nothing;
a failed attempt rolls back and its nonce cannot be replayed; a fresh nonce
removes A's rows and files; B and a non-data file in A's skills tree stay;
old sessions and every sign-in are refused; the morning job does not send;
the per-person deletion request is still not a deletion.
"""
import importlib.util
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "verify_lead_tracker", APP_ROOT / "docs" / "leads" / "verify_lead_tracker.py")
vlt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vlt)

CHILD = r'''
import json, os, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from fastapi.testclient import TestClient
from app import accounts, leads_store, tasks_store, threads, metrics, browser, imessage
import app.lead_followup as lead_followup
import app.text_followup as text_followup
import app.leads_import as leads_import
import app.main as main

box = Path(os.environ["HERMES_HOME"]).parents[0]
pins = {key: accounts.set_pin(key) for key in ("dana", "priya", "omar", "maya")}
lead = leads_store.create("Tenant A Lead", owner_person="omar", emit=False)
leads_store.add_touch(lead["lead_id"], "omar", "note", note="a note")
conn = leads_store._ensure()
conn.execute("INSERT INTO lead_send_log (log_id, at, external_ref, subject) VALUES ('sl1','t','SPN-1','subj')")
conn.commit()
tasks_store.create("Tenant A task", created_by="jarvis")
tc = threads._ensure()
tc.execute("INSERT INTO threads (thread_id, name, created_by, created_at) VALUES ('tA','Chat','user','2026-01-01T00:00:00+00:00')")
tc.execute("INSERT INTO messages (thread_id, role, content, ts) VALUES ('tA','user','hello from A','2026-01-01T00:00:00+00:00')")
tc.commit()
metrics._conn()
tc.execute("INSERT INTO counter_days (name, day, value) VALUES ('seed','2026-10-28',1)")
tc.execute("INSERT INTO measurements (name, value, ts, day, ref) VALUES ('latency',1,'2026-10-28T00:00:00+00:00','2026-10-28','m1')")
tc.commit()
browser._db().execute(
    "INSERT INTO browser_logins (person_key, site, username, secret_cipher, created_at, updated_at) "
    "VALUES ('omar','example.test','o',X'00','t','t')")
browser._db().commit()
imessage._db().execute(
    "INSERT INTO imessages (person_key, guid, is_from_me, sent_at, text_cipher) "
    "VALUES ('omar','g1',0,'t',X'00')")
imessage._db().commit()
accounts._ensure().execute("INSERT INTO google_connection (id, status) VALUES (1, 'connected')")
accounts._ensure().execute("INSERT INTO person_google (person_key, status) VALUES ('dana','connected')")
accounts._ensure().commit()
att = Path(os.environ["JARVIS_ATTACHMENTS_DIR"])
att.mkdir(parents=True, exist_ok=True)
(att / "note.txt").write_text("attachment-A")
spool = Path(os.environ["JARVIS_TRANSCRIBE_SPOOL"])
spool.mkdir(parents=True, exist_ok=True)
(spool / "audio.bin").write_text("audio-A")
Path(os.environ["JARVIS_ACTIVITY_FEED"]).write_text('{"event":"a"}\n')
Path(os.environ["JARVIS_TEAM_FILE"]).write_text('{"people":{}}\n')
ident = Path(os.environ["JARVIS_IDENTITY_CONFIG"])
(ident.parent / "channels.json").write_text('{"channels":[]}\n')
(ident.parent / "keep.txt").write_text("SKILL-CODE")
accounts.DB_PATH.with_name("lead_followup_state.json").write_text("{}\n")
accounts.DB_PATH.with_name("text_followup_state.json").write_text("{}\n")

platform_sql = """
CREATE TABLE actions (id integer primary key, detail text);
CREATE TABLE held_actions (action_id integer, action_json text);
CREATE TABLE approvals (id text, summary text);
CREATE TABLE grants (id text, user_id text);
CREATE TABLE policies (id integer, user_id text);
CREATE TABLE usage (day text, model text, requests integer);
"""
def seed_platform(path, marker):
    con = sqlite3.connect(path)
    con.executescript(platform_sql)
    con.execute("INSERT INTO actions (detail) VALUES (?)", (marker,))
    con.execute("INSERT INTO held_actions (action_id, action_json) VALUES (1, ?)", (marker,))
    con.execute("INSERT INTO approvals (id, summary) VALUES ('apr_aaaaaaaaaaaa', ?)", (marker,))
    con.execute("INSERT INTO grants (id, user_id) VALUES ('g', ?)", (marker,))
    con.execute("INSERT INTO policies (user_id) VALUES (?)", (marker,))
    con.execute("INSERT INTO usage (day, model, requests) VALUES ('2026-10-28','m',1)")
    con.commit()
    con.close()
seed_platform(os.environ["JARVIS_PLATFORM_SQLITE"], "platform-A")

b = Path(os.environ["TENANT_B"])
b.mkdir(parents=True)
seed_platform(b / "platform.sqlite", "platform-B")
con = sqlite3.connect(b / "leads.db")
con.execute("CREATE TABLE leads (name TEXT)")
con.execute("INSERT INTO leads (name) VALUES ('TENANT-B-KEEP')")
con.commit(); con.close()
(b / "keep.txt").write_text("TENANT-B-KEEP")
(b / "identity.json").write_text('{"people":{"other":{}}}')

def counts(path):
    if not Path(path).is_file():
        return 0
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    n = 0
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        n += con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    con.close()
    return n

dbs = {
    "accounts": os.environ["JARVIS_ACCOUNTS_DB"],
    "leads": os.environ["JARVIS_LEADS_DB"],
    "tasks": os.environ["JARVIS_TASKS_DB"],
    "threads": os.environ["JARVIS_THREADS_DB"],
    "platform": os.environ["JARVIS_PLATFORM_SQLITE"],
}
before = {k: counts(v) for k, v in dbs.items()}

c = TestClient(main.app)
calls = []
lead_followup.send_now = lambda *a, **k: calls.append("lead") or {"result": "sent"}
lead_followup.settings = lambda: {"enabled": True, "time_local": "10:00", "suggest": False}
lead_followup._people = lambda: ["omar"]
text_calls = []
text_followup.send_now = lambda *a, **k: text_calls.append("text") or {"result": "sent"}
text_followup.settings = lambda: {"enabled": True, "times_local": ["10:00"], "older_than_hours": 2, "lookback_days": 7}
text_followup._people = lambda: ["omar"]
now = datetime(2026, 10, 28, 15, 30, tzinfo=timezone.utc)
job_before = lead_followup.tick(now)
text_before = text_followup.tick(now)
import_before = leads_import.run()
HDR = {"X-Jarvis-Confirm": "delete-tenant"}
PHRASE = "delete alpha"

def login(who):
    return c.post("/api/login", json={"person_key": who, "pin": pins[who]})

out = {}
out["job_before"] = [len(calls), job_before.get("omar"), len(text_calls), text_before.get("omar"), import_before]
login("omar")
out["member"] = c.post("/api/account/tenant-deletion", headers=HDR,
    json={"confirm": PHRASE, "pin": pins["omar"], "nonce": "not-a-real-nonce"}).status_code
login("priya")
out["admin"] = c.post("/api/account/tenant-deletion/challenge", headers=HDR).status_code
r = c.post("/api/account/deletion-request", json={"confirm": "request-account-deletion", "pin": pins["priya"], "person_key": "omar"})
body = r.json()
out["personal"] = [r.status_code, body.get("deleted"), body.get("deletion"), counts(dbs["leads"]) >= before["leads"]]
login("maya")
maya = c.cookies.get("jarvis_session")
op = c.post("/api/login", json={"passphrase": "test-operator-pass"})
out["operator_before"] = op.status_code
login("dana")
snap = {k: counts(v) for k, v in dbs.items()}
out["get"] = c.get("/api/account/tenant-deletion").status_code
out["get_same"] = {k: counts(v) for k, v in dbs.items()} == snap
ch = c.post("/api/account/tenant-deletion/challenge", headers=HDR)
out["challenge"] = ch.status_code
nonce = ch.json().get("nonce")
phrase = ch.json().get("phrase")
out["no_header"] = c.post("/api/account/tenant-deletion", json={"confirm": phrase, "pin": pins["dana"], "nonce": nonce}).status_code
out["cross"] = c.post("/api/account/tenant-deletion", headers={**HDR, "Origin": "https://evil.example"},
    json={"confirm": phrase, "pin": pins["dana"], "nonce": nonce}).status_code
out["bad_pin"] = c.post("/api/account/tenant-deletion", headers=HDR,
    json={"confirm": phrase, "pin": "nope", "nonce": nonce}).status_code
out["bad_phrase"] = c.post("/api/account/tenant-deletion", headers=HDR,
    json={"confirm": "delete beta", "pin": pins["dana"], "nonce": nonce}).status_code
fail = Path(os.environ["HERMES_HOME"]) / "state" / "closure" / "failpoint"
fail.write_text("after_stage")
failed = c.post("/api/account/tenant-deletion", headers=HDR,
    json={"confirm": phrase, "pin": pins["dana"], "nonce": nonce})
out["rolled_back"] = [failed.status_code, "still here" in failed.json().get("detail",""), counts(dbs["leads"]) > 0,
                      (att / "note.txt").read_text() == "attachment-A", not (Path(os.environ["HERMES_HOME"]) / "state" / "closure" / "tombstone.json").exists()]
replay = c.post("/api/account/tenant-deletion", headers=HDR,
    json={"confirm": phrase, "pin": pins["dana"], "nonce": nonce})
out["replay"] = replay.status_code
ch2 = c.post("/api/account/tenant-deletion/challenge", headers=HDR)
done = c.post("/api/account/tenant-deletion", headers=HDR,
    json={"confirm": ch2.json()["phrase"], "pin": pins["dana"], "nonce": ch2.json()["nonce"]})
body = done.json()
out["done"] = [done.status_code, body.get("action"), body.get("deleted_local"), body.get("sign_in_blocked"),
               "Gmail mailbox was not deleted" in " ".join(body.get("still_exists") or []),
               "Backups" in " ".join(body.get("still_exists") or [])]
out["done_detail"] = body.get("detail")
out["after"] = {k: counts(v) for k, v in dbs.items()}
out["files_gone"] = not (att / "note.txt").exists() and not (spool / "audio.bin").exists() and not Path(os.environ["JARVIS_ACTIVITY_FEED"]).exists() and not ident.exists()
out["skill_kept"] = (ident.parent / "keep.txt").read_text() == "SKILL-CODE"
out["tenant_b"] = (b / "keep.txt").read_text() == "TENANT-B-KEEP" and counts(b / "leads.db") == 1 and counts(b / "platform.sqlite") > 0 and "other" in (b / "identity.json").read_text()
out["session"] = c.get("/api/session").status_code
c.cookies.set("jarvis_session", maya)
out["old_member_session"] = c.get("/api/session").status_code
out["login_owner"] = login("dana").status_code
out["login_member"] = login("maya").status_code
out["login_operator"] = c.post("/api/login", json={"passphrase": "test-operator-pass"}).status_code
out["login_detail"] = login("dana").json().get("detail")
job_after = lead_followup.tick(now)
text_after = text_followup.tick(now)
out["jobs_after"] = [len(calls), job_after.get("_"), len(text_calls), text_after.get("_"), leads_import.run()]
out["tombstone"] = (Path(os.environ["HERMES_HOME"]) / "state" / "closure" / "tombstone.json").is_file()
print("RESULT " + json.dumps(out))
'''


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="verify-tenant-delete-") as tmp:
        box = pathlib.Path(tmp)
        hermes = box / "hermes"
        shared = box / "skills" / "_shared"
        shared.mkdir(parents=True)
        for name, src in vlt.SHARED.items():
            (shared / name).write_text(src)
        (hermes / "state").mkdir(parents=True)
        team = {"people": {
            "dana": {"name": "Dana Owner", "display_name": "Dana", "roles": ["owner"]},
            "priya": {"name": "Priya Admin", "display_name": "Priya", "roles": ["admin"]},
            "omar": {"name": "Omar Agent", "display_name": "Omar", "roles": ["member"]},
            "maya": {"name": "Maya Agent", "display_name": "Maya", "roles": ["member"]},
        }}
        (shared / "identity.json").write_text(json.dumps(team))
        (shared / "runtime.json").write_text(json.dumps(
            {"client": {"timezone": "America/Chicago", "slug": "alpha"},
             "skills": {"leads": {"source": "store"}}}))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC", "HERMES_"))}
        extra = os.environ.get("LT_PYTHONPATH", "")
        env.update({
            "APP_ROOT": str(APP_ROOT),
            "PYTHONPATH": os.pathsep.join(x for x in (extra, str(APP_ROOT)) if x),
            "HERMES_HOME": str(hermes),
            "JARVIS_SKILLS_ROOT": str(box / "skills"),
            "LT_RUNTIME": str(shared / "runtime.json"),
            "LT_IDENTITY": str(shared / "identity.json"),
            "JARVIS_IDENTITY_CONFIG": str(shared / "identity.json"),
            "JARVIS_RUNTIME_CONFIG": str(shared / "runtime.json"),
            "JARVIS_LEADS_DB": str(hermes / "state" / "leads.db"),
            "JARVIS_ACCOUNTS_DB": str(hermes / "state" / "accounts.db"),
            "JARVIS_THREADS_DB": str(hermes / "state" / "threads.db"),
            "JARVIS_TASKS_DB": str(hermes / "state" / "tasks.db"),
            "JARVIS_ACTIVITY_FEED": str(hermes / "state" / "activity_feed.jsonl"),
            "JARVIS_TEAM_FILE": str(hermes / "state" / "team.json"),
            "JARVIS_ATTACHMENTS_DIR": str(hermes / "attachments"),
            "JARVIS_TRANSCRIBE_SPOOL": str(hermes / "spool"),
            "JARVIS_PLATFORM_SQLITE": str(hermes / "state" / "platform.sqlite"),
            "JARVIS_SWITCH_SENTINEL": "off",
            "APP_SESSION_SECRET": "verify-tenant-delete-session",
            "APP_COOKIE_SECURE": "0",
            "APP_TENANT_SLUG": "alpha",
            "APP_PASSPHRASE": "test-operator-pass",
            "JARVIS_TENANT_DELETE_TEST": "1",
            "TENANT_B": str(box / "tenant-b"),
        })
        proc = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(APP_ROOT),
                              capture_output=True, text=True, timeout=180)
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(proc.stdout[-2000:])
        print(proc.stderr[-4000:])
        return 1
    r = json.loads(line[-1][7:])
    ok = []

    def ck(name, cond, extra=""):
        ok.append(bool(cond))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" ({extra})" if extra and not cond else ""))

    ck("the morning job and the text job send before deletion, and the sheet import does not think the team is gone",
       r["job_before"][0] == 1 and r["job_before"][1] == "sent" and r["job_before"][2] == 1
       and r["job_before"][4] == 2, r["job_before"])
    ck("a member cannot delete the team", r["member"] == 403, r["member"])
    ck("an admin cannot start team deletion", r["admin"] == 403, r["admin"])
    ck("a personal deletion request is still not a deletion and leaves the leads",
       r["personal"] == [200, False, "pending", True], r["personal"])
    ck("an operator can sign in before deletion", r["operator_before"] == 200, r["operator_before"])
    ck("reading the description does not delete", r["get"] == 200 and r["get_same"] is True, r["get"])
    ck("a challenge is issued to the owner", r["challenge"] == 200)
    ck("a missing header and a cross-origin Origin are refused",
       r["no_header"] == 403 and r["cross"] == 403, [r["no_header"], r["cross"]])
    ck("a wrong PIN and a wrong phrase delete nothing",
       r["bad_pin"] == 401 and r["bad_phrase"] == 422, [r["bad_pin"], r["bad_phrase"]])
    ck("a failure puts the data and the attachment back and leaves sign-in on",
       r["rolled_back"] == [500, True, True, True, True], r["rolled_back"])
    ck("the used confirmation cannot be replayed", r["replay"] == 409, r["replay"])
    ck("the owner deletion reports local removal and the external limits",
       r["done"][0] == 200 and r["done"][1:] == ["team-deleted", True, True, True, True],
       [r["done"], r.get("done_detail")])
    ck("tenant A databases are empty", all(v == 0 for v in r["after"].values()), r["after"])
    ck("attachments, spool, feed and identity for A are gone, and skill code is not",
       r["files_gone"] is True and r["skill_kept"] is True, [r["files_gone"], r["skill_kept"]])
    ck("tenant B is unchanged", r["tenant_b"] is True, r["tenant_b"])
    ck("the current session, an old member session, and every sign-in are refused",
       r["session"] == 401 and r["old_member_session"] == 401 and r["login_owner"] == 401
       and r["login_member"] == 401 and r["login_operator"] == 401
       and r["login_detail"] == "This team was deleted. Sign-in is off.",
       [r["session"], r["old_member_session"], r["login_detail"]])
    ck("jobs do not run after deletion",
       r["jobs_after"] == [1, "tenant_closed", 1, "tenant_closed", 3], r["jobs_after"])
    ck("the closure marker remains", r["tombstone"] is True)
    print(f"\n{sum(ok)}/{len(ok)} passed")
    return 0 if ok and all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
