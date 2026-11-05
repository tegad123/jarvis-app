#!/usr/bin/env python3
"""A throwaway app for verify_leads_ui.mjs: this checkout's app and static/
bundle, on loopback, against a box of its own.

Same stand-in _shared modules and four-person team as verify_lead_tracker.py
(dana owner, priya admin, omar and maya members). Sentinel is off, so no send
path can fire. Each person gets a PIN, written to <box>/pins.json (mode 600)
for the browser check to sign in with; nothing prints them. The follow-up
clock is pinned to LT_TODAY so "due today" doesn't depend on when it runs.

Seeded: omar — Sarah Lee (Hot, due today), Olly Overdue (due 3 days ago),
Wes Later (due in 5 days); maya — Mo Maya (due today).

    python3 docs/leads/leads_ui_server.py BOX PORT
"""
import json
import os
import pathlib
import secrets
import sys
from datetime import date, timedelta

HERE = pathlib.Path(__file__).resolve().parent
APP_ROOT = HERE.parents[1]
if str(APP_ROOT).startswith("/root/jarvis-app"):
    sys.exit("REFUSING: this is live's tree")

box, port = pathlib.Path(sys.argv[1]).resolve(), int(sys.argv[2])
today = date.fromisoformat(os.environ.get("LT_TODAY", "2026-10-28"))

sys.path.insert(0, str(HERE))
from verify_lead_tracker import SHARED  # noqa: E402

shared = box / "skills" / "_shared"
shared.mkdir(parents=True, exist_ok=True)
for name, src in SHARED.items():
    (shared / name).write_text(src)
(box / "hermes" / "state").mkdir(parents=True, exist_ok=True)
team = {"people": {"dana": {"name": "Dana Owner", "display_name": "Dana", "roles": ["owner"]},
                   "priya": {"name": "Priya Admin", "display_name": "Priya", "roles": ["admin"]},
                   "omar": {"name": "Omar Agent", "display_name": "Omar", "roles": ["member"]},
                   "maya": {"name": "Maya Agent", "display_name": "Maya", "roles": ["member"]}}}
(box / "identity.json").write_text(json.dumps(team))
(box / "runtime.json").write_text(json.dumps({
    "client": {"timezone": "America/Chicago", "slug": "lt"}, "skills": {"leads": {"source": "store"}}, "app": {}}))

for k in [k for k in os.environ if k.startswith(("JARVIS_", "ANTHROPIC", "HERMES_", "APP_"))]:
    del os.environ[k]
os.environ.update({
    "HERMES_HOME": str(box / "hermes"), "JARVIS_SKILLS_ROOT": str(box / "skills"),
    "LT_RUNTIME": str(box / "runtime.json"), "LT_IDENTITY": str(box / "identity.json"),
    "JARVIS_LEADS_DB": str(box / "leads.db"), "JARVIS_ACCOUNTS_DB": str(box / "accounts.db"),
    "JARVIS_THREADS_DB": str(box / "threads.db"), "JARVIS_TASKS_DB": str(box / "tasks.db"),
    "JARVIS_ACTIVITY_FEED": str(box / "feed.jsonl"), "JARVIS_TEAM_FILE": str(box / "hermes" / "state" / "team.json"),
    "JARVIS_SWITCH_SENTINEL": "off", "HERMES_API_URL": "http://127.0.0.1:9",
    "APP_SESSION_SECRET": secrets.token_urlsafe(32), "APP_COOKIE_SECURE": "0",
})
sys.path.insert(0, str(APP_ROOT))

import uvicorn  # noqa: E402

import app.main as main  # noqa: E402
from app import accounts, leads_store  # noqa: E402

leads_store.today_local = lambda: today

pins = {k: accounts.set_pin(k, r) for k, r in (("dana", "owner"), ("priya", "admin"), ("omar", "member"),
                                               ("maya", "member"))}
pin_file = box / "pins.json"
pin_file.write_text(json.dumps(pins))
os.chmod(pin_file, 0o600)

if not leads_store.find_by_name("Sarah Lee"):
    def seed(name, owner, due, **f):
        lead = leads_store.create_with_cadence(name, by=owner, owner_person=owner, today=today, **f)
        leads_store.update(lead["lead_id"], next_touch_at=due.isoformat())
    seed("Sarah Lee", "omar", today, temperature="Hot", phone="(512) 555-0101", property="1109 E 24th St")
    seed("Olly Overdue", "omar", today - timedelta(days=3), temperature="Warm")
    seed("Wes Later", "omar", today + timedelta(days=5))
    seed("Mo Maya", "maya", today, temperature="Hot")

uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning")
