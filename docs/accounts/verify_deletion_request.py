#!/usr/bin/env python3
"""A deletion *request* turns off one person's sign-in. It does not delete data.

Runs in a child process against temp SQLite files and the lead-tracker stubs.
Never a live tenant. Prints one RESULT line.

Checks:
- a member cannot turn off someone else
- a wrong confirmation or a wrong PIN changes nothing
- the person's own request sets deleted false, deactivates, and records pending
- the old session is refused and the old PIN cannot sign in
- the other person's lead and task are still there, and so is the requester's
- the only owner cannot turn themselves off, and their session still works
- an admin can turn off another person without turning off the admin or the leads
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "verify_lead_tracker", APP_ROOT / "docs" / "leads" / "verify_lead_tracker.py")
vlt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vlt)

CHILD = r'''
import json
from fastapi.testclient import TestClient
from app import accounts, leads_store, tasks_store
import app.main as main

pins = {key: accounts.set_pin(key) for key in ("dana", "priya", "omar", "maya")}
leads_store.create("Omar Lead", owner_person="omar", emit=False)
leads_store.create("Maya Lead", owner_person="maya", emit=False)
tasks_store.create("Omar task", created_by="jarvis")
tasks_store.create("Maya task", created_by="jarvis")

def names(store, field):
    return sorted((row.get("name") or row.get("title")) for row in store)

c = TestClient(main.app)
CONFIRM = "request-account-deletion"

def login(who):
    return c.post("/api/login", json={"person_key": who, "pin": pins[who]})

def ask(pin, person=None, confirm=CONFIRM):
    body = {"confirm": confirm, "pin": pin}
    if person:
        body["person_key"] = person
    return c.post("/api/account/deletion-request", json=body)

out = {}
login("omar")
out["member_other"] = ask(pins["omar"], "maya").status_code
out["maya_enabled"] = accounts.get("maya")["disabled"] == 0
out["bad_confirm"] = ask(pins["omar"], confirm="please").status_code
out["bad_pin"] = ask("nope").status_code
out["omar_enabled_before"] = accounts.get("omar")["disabled"] == 0
r = ask(pins["omar"])
body = r.json()
out["self"] = [r.status_code, body.get("deleted"), body.get("deactivated"), body.get("deletion"),
               "not deleted" in (body.get("detail") or "")]
out["session_after"] = c.get("/api/session").status_code
out["login_after"] = login("omar").status_code
out["leads"] = sorted(row["name"] for row in leads_store.query())
out["tasks"] = sorted(row["title"] for row in tasks_store.query())
out["requests"] = c.app and accounts._ensure().execute(
    "SELECT status, person_key FROM deletion_requests").fetchall()
out["requests"] = [[row["status"], row["person_key"]] for row in out["requests"]]
out["pin_cleared"] = accounts.get("omar")["pin_hash"] is None

login("maya")
out["maya_session"] = c.get("/api/session").json().get("person_key")

login("dana")
r = ask(pins["dana"])
out["sole_owner"] = [r.status_code, accounts.get("dana")["disabled"] == 0, c.get("/api/session").status_code]

login("priya")
r = ask(pins["priya"], "maya")
body = r.json()
out["admin_other"] = [r.status_code, body.get("deleted"), accounts.get("maya")["disabled"] == 1,
                      accounts.get("priya")["disabled"] == 0]
out["leads_after"] = sorted(row["name"] for row in leads_store.query())
out["tasks_after"] = sorted(row["title"] for row in tasks_store.query())
print("RESULT " + json.dumps(out))
'''


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="verify-deletion-") as tmp:
        box = pathlib.Path(tmp)
        shared = box / "skills" / "_shared"
        shared.mkdir(parents=True)
        for name, src in vlt.SHARED.items():
            (shared / name).write_text(src)
        (box / "hermes" / "state").mkdir(parents=True)
        team = {"people": {
            "dana": {"name": "Dana Owner", "display_name": "Dana", "roles": ["owner"]},
            "priya": {"name": "Priya Admin", "display_name": "Priya", "roles": ["admin"]},
            "omar": {"name": "Omar Agent", "display_name": "Omar", "roles": ["member"]},
            "maya": {"name": "Maya Agent", "display_name": "Maya", "roles": ["member"]},
        }}
        (box / "identity.json").write_text(json.dumps(team))
        (box / "runtime.json").write_text(json.dumps(
            {"client": {"timezone": "America/Chicago", "slug": "lt"},
             "skills": {"leads": {"source": "store"}}}))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC", "HERMES_"))}
        extra = os.environ.get("LT_PYTHONPATH", "")
        env.update({
            "APP_ROOT": str(APP_ROOT),
            "PYTHONPATH": os.pathsep.join(x for x in (extra, str(APP_ROOT)) if x),
            "HERMES_HOME": str(box / "hermes"),
            "JARVIS_SKILLS_ROOT": str(box / "skills"),
            "LT_RUNTIME": str(box / "runtime.json"),
            "LT_IDENTITY": str(box / "identity.json"),
            "JARVIS_LEADS_DB": str(box / "leads.db"),
            "JARVIS_ACCOUNTS_DB": str(box / "accounts.db"),
            "JARVIS_THREADS_DB": str(box / "threads.db"),
            "JARVIS_TASKS_DB": str(box / "tasks.db"),
            "JARVIS_ACTIVITY_FEED": str(box / "feed.jsonl"),
            "JARVIS_TEAM_FILE": str(box / "hermes" / "state" / "team.json"),
            "JARVIS_SWITCH_SENTINEL": "off",
            "APP_SESSION_SECRET": "verify-deletion-session-secret",
            "APP_COOKIE_SECURE": "0",
        })
        proc = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(APP_ROOT),
                              capture_output=True, text=True, timeout=180)
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(proc.stdout[-1500:], proc.stderr[-2500:])
        return 1
    r = json.loads(line[-1][7:])
    ok = []

    def ck(name, cond, extra=""):
        ok.append(bool(cond))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" ({extra})" if extra and not cond else ""))

    ck("a member cannot turn off someone else", r["member_other"] == 403 and r["maya_enabled"] is True,
       r["member_other"])
    ck("a wrong confirmation changes nothing", r["bad_confirm"] == 422 and r["omar_enabled_before"] is True)
    ck("a wrong PIN changes nothing", r["bad_pin"] == 401 and r["omar_enabled_before"] is True)
    ck("the request is pending and not a deletion",
       r["self"] == [200, False, True, "pending", True], r["self"])
    ck("the old session is refused and the old PIN cannot sign in",
       r["session_after"] == 401 and r["login_after"] == 401, [r["session_after"], r["login_after"]])
    ck("the PIN secret is cleared", r["pin_cleared"] is True)
    ck("both people's leads and tasks are still there",
       r["leads"] == ["Maya Lead", "Omar Lead"] and r["tasks"] == ["Maya task", "Omar task"],
       [r["leads"], r["tasks"]])
    ck("the other person can still sign in", r["maya_session"] == "maya", r["maya_session"])
    ck("the only owner is refused and stays signed in",
       r["sole_owner"] == [422, True, 200], r["sole_owner"])
    ck("an admin can turn off someone else without a deletion or touching the admin",
       r["admin_other"] == [200, False, True, True], r["admin_other"])
    ck("after that, the same leads and tasks are still there",
       r["leads_after"] == ["Maya Lead", "Omar Lead"] and r["tasks_after"] == ["Maya task", "Omar task"],
       [r["leads_after"], r["tasks_after"]])
    print(f"\n{sum(ok)}/{len(ok)} passed")
    return 0 if ok and all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
