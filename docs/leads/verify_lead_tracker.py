#!/usr/bin/env python3
"""The open-house lead tracker, verified through the app's own routes.

Everything runs in a child process against a disposable box built here: temp
SQLite databases (leads, accounts, threads, tasks), a runtime.json and an
identity registry for a four-person team (dana owner, priya admin, omar and
maya members), and stand-in _shared modules (tenant_config, identity, notify,
and for the tool subprocess app_broker and authz). Sentinel is switched off in
the box, so there is no Postgres and no Gmail: the morning email's send is
captured at broker.send_to_self, and one check calls the real send_to_self to
prove it is the gate (it refuses while Sentinel is off). The self-only
recipient rule inside send_to_self is unchanged and is proven by
docs/imessage/verify_text_followup.py on the sandbox.

Checked, with the ledger (lead and timeline row counts, counters) before and after:
- cadence: Hot due tomorrow, Warm in 2 days, otherwise 3; 7 then 14 days after
  each logged contact; a missed call makes New into No Response, a reached one
  makes it Responded; a note moves nothing.
- scoping: a member lists, opens, edits, archives and logs only their own
  leads (someone else's is a 404 and unchanged); only owner or admin gives a
  lead away; the operator sees the timeline's shape and none of its notes.
- Jarvis's tools through /api/broker/leads AND through run_action.py: add with
  the default date, the duplicate check (phone formats, a teammate's lead told
  apart without its details), add_anyway, log by name, follow-ups (overdue and
  today, member can't widen to everyone, admin can), no turn, ended turn,
  Leads switched off.
- the morning email: one per agent with something due, one model call each,
  overdue then today, nobody else's leads, Not Interested left out, the same
  slot never twice, a late slot skipped, Leads switched off skipped, a model
  failure falls back to templates and says so (counted), hostile names
  escaped, the real send_to_self refuses while Sentinel is off, off by default.
- the import: preview writes nothing; tabs, co-listed rows, an ID collision,
  an unreadable date and an unplaced agent are reported; import refuses until
  every unplaced agent is answered, and refuses a key that names nobody;
  re-import adds nothing and never overwrites an agent's edit; one feed line,
  not one per lead; .csv works; a server without openpyxl says so; owner only.
- the provisioner's lead_followup block.

Canaries (each must make its named check FAIL):
  --canary-scope       members are treated as unscoped     -> "omar can't open maya's lead"
  --canary-cadence     hot leads get 3 days                -> "Hot: due tomorrow"
  --canary-dedupe      the duplicate finders find nothing  -> "duplicate: same phone, other format"
  --canary-idempotent  the importer can't see prior rows   -> "re-import adds nothing"
  --canary-escape      the email stops escaping            -> "hostile lead name shown as text"
  --canary-feed        every feed write raises             -> "no feed write failed"

Never :8700 or :8710; no network. Prints no fixture content beyond its own made-up names.
Usage: python3 docs/leads/verify_lead_tracker.py [--canary-...]
       LT_PYTHONPATH=/path/to/site-packages when the interpreter lacks fastapi.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" ({extra})" if extra and not cond else ""))


SHARED = {
    "tenant_config.py": r'''
import json, os
def _rt():
    return json.load(open(os.environ["LT_RUNTIME"]))
def get(path, default=None):
    cur = _rt()
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur
def timezone_name(): return get("client.timezone", "UTC")
def announce_mismatch(*a, **k): pass
def announce_assistant_mailbox_mismatch(*a, **k): pass
def enabled(*a, **k): return False
def gateway_url(*a, **k): return "http://127.0.0.1:9"
def require(path):
    v = get(path)
    if v is None: raise KeyError(path)
    return v
def reset_cache(): pass
''',
    "identity.py": r'''
import json, os
def load(force=False): return json.load(open(os.environ["LT_IDENTITY"]))
def _people(): return load().get("people") or {}
def resolve(raw):
    r = (raw or "").strip().lower()
    for k, p in _people().items():
        names = {k, (p.get("name") or "").lower(), (p.get("display_name") or "").lower()}
        names |= {n.split()[0] for n in list(names) if n}
        if r in names: return k
    return None
def display_name_for(k): return (_people().get(k) or {}).get("display_name")
def internal_domains(): return ["example.com"]
def owner_key(): return next((k for k, p in _people().items() if "owner" in (p.get("roles") or [])), None)
def owner_address(): return None
def roster(): return [{"person_key": k, **p} for k, p in _people().items()]
''',
    "notify.py": r'''
import json, os
def feed_path(): return os.environ["JARVIS_ACTIVITY_FEED"]
def notify(destination, message=None, **kw):
    with open(feed_path(), "a") as f:
        f.write(json.dumps({"skill": kw.get("skill"), "headline": kw.get("headline") or message, "kind": kw.get("kind"),
                            "attachments": kw.get("attachments")}) + "\n")
def read_feed(*a, **k): return []
''',
    "authz.py": r'''
DENIED_EXIT = 3
def require(*a, **k): return True
def check(*a, **k): return (True, "")
''',
    "app_broker.py": r'''
import os, sys
class BrokerError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}")
        self.status, self.body = status, body
_c = None
def call(path, payload, timeout=30):
    global _c
    if path == "counters":
        return {"ok": True}
    sys.path.insert(0, os.environ["APP_ROOT"])
    from fastapi.testclient import TestClient
    import app.main as m
    from app import actor_refs, broker, leads_store
    from datetime import date
    leads_store.today_local = lambda: date.fromisoformat(os.environ["LT_TODAY"])
    broker._authorize = lambda request: None
    _c = _c or TestClient(m.app)
    payload = dict(payload)
    if payload.get("ref") == "LT-REF":
        payload["ref"] = actor_refs.mint(os.environ["LT_PERSON"], "t-sub", os.environ["LT_ROLE"])
    r = _c.post("/api/broker/" + path, json=payload)
    if r.status_code >= 400:
        raise BrokerError(r.status_code, r.json())
    return r.json()
''',
}

CHILD = r'''
import io, json, os, subprocess, sys
from datetime import date, datetime, timezone
sys.path.insert(0, os.environ["APP_ROOT"])
CANARY = os.environ.get("CANARY", "")
from fastapi.testclient import TestClient
import app.main as main
from app import accounts, actor_refs, agent_status, auth, broker, lead_followup, lead_tools, leads_store, leads_upload
from app import threads as threads_mod

TODAY = date(2026, 10, 28)
leads_store.today_local = lambda: TODAY
if CANARY == "cadence":
    leads_store.CADENCE_FIRST = {"hot": 3, "warm": 2}
if CANARY == "scope":
    main._viewer = lambda sess: None
if CANARY == "dedupe":
    for f in ("find_by_email", "find_by_phone", "find_by_name"):
        setattr(leads_store, f, lambda *a, **k: [])
if CANARY == "escape":
    lead_followup._esc = lambda s: s
if CANARY == "feed":
    import notify
    notify.notify = lambda *a, **k: 1 / 0

S = {k: {"person_key": k, "role": r, "kind": "person"} for k, r in
     (("dana", "owner"), ("priya", "admin"), ("omar", "member"), ("maya", "member"))}
for k, s in S.items():
    accounts.set_pin(k, s["role"])
S["op"] = {"person_key": None, "role": "operator", "operator": "operator", "kind": "operator"}
who = {"v": "dana"}
main.app.dependency_overrides[auth.current_session] = lambda: S[who["v"]]
c = TestClient(main.app)
def as_(k):
    who["v"] = k
    return c
def db():
    return leads_store._ensure()
def ledger():
    return {"leads": db().execute("SELECT COUNT(*) FROM leads").fetchone()[0],
            "touches": db().execute("SELECT COUNT(*) FROM lead_touches").fetchone()[0]}
def ctr(n):
    return threads_mod.bump_counter(n, 0)
def feed():
    p = os.environ["JARVIS_ACTIVITY_FEED"]
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []
out = {}

# ── the screen's routes ──────────────────────────────────────────────────────
L0 = ledger()
r = as_("dana").post("/api/leads", json={"name": "Sarah Lee", "temperature": "Hot", "owner_person": "omar",
                                         "phone": "(512) 555-0101", "property": "1109 E 24th St"})
sarah = r.json().get("lead") or {}
out["create_hot"] = [r.status_code, sarah.get("next_touch_at"), sarah.get("owner_person"), sarah.get("status")]
r = as_("omar").post("/api/leads", json={"name": "Wes Warm", "temperature": "Warm"})
wes = r.json().get("lead") or {}
out["create_warm"] = [r.status_code, wes.get("next_touch_at"), wes.get("owner_person")]
r = as_("omar").post("/api/leads", json={"name": "Gift Lead", "owner_person": "maya"})
out["member_gives_away"] = r.status_code
r = as_("maya").post("/api/leads", json={"name": "Mia Cold"})
mia = r.json().get("lead") or {}
out["create_plain"] = [r.status_code, mia.get("next_touch_at"), mia.get("owner_person")]
out["feed_new_lead"] = any(e["headline"].startswith("New lead: Sarah Lee") for e in feed())

lst = as_("omar").get("/api/leads").json()
out["omar_list"] = sorted(l["name"] for l in lst["leads"])
out["omar_list_today"] = lst.get("today")
out["omar_open_mia"] = as_("omar").get(f"/api/leads/{mia['lead_id']}").status_code
out["omar_patch_mia"] = as_("omar").patch(f"/api/leads/{mia['lead_id']}", json={"status": "Not Interested"}).status_code
out["omar_archive_mia"] = as_("omar").post(f"/api/leads/{mia['lead_id']}/archive").status_code
out["omar_touch_mia"] = as_("omar").post(f"/api/leads/{mia['lead_id']}/touches", json={"kind": "call"}).status_code
m = leads_store.get(mia["lead_id"])
out["mia_untouched"] = [m["status"], m["archived"], len(leads_store.touches(mia["lead_id"]))]
out["admin_open_mia"] = as_("priya").get(f"/api/leads/{mia['lead_id']}").status_code
out["omar_open_own"] = as_("omar").get(f"/api/leads/{sarah['lead_id']}").status_code

r = as_("omar").post(f"/api/leads/{sarah['lead_id']}/touches", json={"kind": "call", "reached": False, "note": "left a voicemail"})
out["missed_call"] = [r.status_code, r.json()["lead"]["status"], r.json()["lead"]["next_follow_up"]]
r = as_("omar").post(f"/api/leads/{sarah['lead_id']}/touches", json={"kind": "text", "reached": True})
out["reached_text"] = [r.status_code, r.json()["lead"]["status"], r.json()["lead"]["next_follow_up"]]
r = as_("omar").post(f"/api/leads/{sarah['lead_id']}/touches", json={"kind": "note", "note": "pre-approved"})
out["note"] = [r.status_code, r.json()["lead"]["status"], r.json()["lead"]["next_follow_up"]]
r = as_("omar").post(f"/api/leads/{sarah['lead_id']}/touches", json={"kind": "call", "next_touch_at": "2026-12-01"})
out["given_date"] = r.json()["lead"]["next_follow_up"]
d = as_("omar").get(f"/api/leads/{sarah['lead_id']}").json()
out["timeline"] = [[t["kind"], t["by_name"], t["note"]] for t in d["timeline"]]
dop = as_("op").get(f"/api/leads/{sarah['lead_id']}").json()
out["op_timeline"] = [[t["kind"], t["note"]] for t in dop["timeline"]]
out["op_touch"] = as_("op").post(f"/api/leads/{sarah['lead_id']}/touches", json={"kind": "call"}).status_code
out["member_reassign_own"] = as_("omar").patch(f"/api/leads/{wes['lead_id']}", json={"owner_person": "maya"}).status_code
out["ledger_routes"] = [L0, ledger()]

# ── Jarvis's tools ───────────────────────────────────────────────────────────
broker._authorize = lambda request: None
ref = actor_refs.mint("omar", "t-omar", "member")
ref_admin = actor_refs.mint("priya", "t-priya", "admin")
def tool(rf, **body):
    r = c.post("/api/broker/leads", json={"ref": rf, **body})
    return r.status_code, r.json()
L1, A0 = ledger(), ctr("lead_tool_added")
s, b = tool(ref, op="add", name="Tina Open", phone="512-555-0199", property="1109 E 24th", temperature="Warm",
            notes="wants 3 bed")
tina = (b.get("lead") or {})
out["tool_add"] = [s, tina.get("agent"), tina.get("next_follow_up"),
                   (leads_store.get(tina.get("lead_id", "")) or {}).get("created_by"),
                   (leads_store.get(tina.get("lead_id", "")) or {}).get("source")]
s, b = tool(ref, op="add", name="S. Lee", phone="+1 512.555.0101")
out["tool_dup_phone"] = [s, b.get("error"), [x["name"] for x in b.get("matches") or []]]
s, b = tool(ref, op="add", name="Mia Cold")
out["tool_dup_other"] = [s, b.get("error"), b.get("matches"), b.get("others")]
s, b = tool(ref, op="add", name="Mia Cold", add_anyway=True)
out["tool_add_anyway"] = [s, (b.get("lead") or {}).get("agent")]
s, b = tool(ref, op="log", match="tina", kind="call", note="talked")
out["tool_log"] = [s, (b.get("lead") or {}).get("status"), (b.get("lead") or {}).get("next_follow_up")]
s, b = tool(ref, op="log", lead_id=mia["lead_id"], kind="call")
out["tool_log_other"] = [s, b.get("error")]
tool(ref, op="add", name="Olly Overdue", next_follow_up="2026-10-20", add_anyway=True)
tool(ref, op="add", name="Toby Today", next_follow_up="2026-10-28", add_anyway=True)
as_("maya").post("/api/leads", json={"name": "Mo Maya", "next_touch_at": "2026-10-28"})
as_("maya").post("/api/leads", json={"name": "Nina NotInt", "next_touch_at": "2026-10-28", "status": "Not Interested"})
s, b = tool(ref, op="followups")
out["tool_followups"] = [s, [x["name"] for x in b.get("overdue", [])], [x["name"] for x in b.get("due_today", [])]]
s, b = tool(ref, op="followups", everyone=True)
out["tool_followups_member_everyone"] = sorted(x["name"] for x in b.get("overdue", []) + b.get("due_today", []))
s, b = tool(ref_admin, op="followups", everyone=True)
out["tool_followups_admin_everyone"] = sorted(x["name"] for x in b.get("overdue", []) + b.get("due_today", []))
out["tool_no_ref"] = tool("", op="followups")[0]
F0 = ctr("feature_refused_leads")
accounts.set_feature("omar", "leads", False, by="dana")
out["tool_feature_off"] = [tool(ref, op="followups")[0], ctr("feature_refused_leads") - F0]
accounts.set_feature("omar", "leads", True, by="dana")
actor_refs.end(ref)
out["tool_ended_ref"] = tool(ref, op="followups")[0]
out["ledger_tools"] = [L1, ledger(), ctr("lead_tool_added") - A0]

env = {**os.environ, "JARVIS_ACTOR_MSG": "LT-REF", "LT_PERSON": "omar", "LT_ROLE": "member"}
ra = str(main.Path(os.environ["APP_ROOT"]) / "scripts" / "hermes_plugin" / "jarvis" / "run_action.py")
def run_action(name, args):
    p = subprocess.run([sys.executable, ra, name], input=json.dumps(args), env=env, capture_output=True, text=True,
                       timeout=120)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"_stderr": p.stderr[-800:], "_rc": p.returncode}
before = ledger()["leads"]
out["run_action_add"] = run_action("add_lead", {"name": "Rae Subprocess", "temperature": "Hot"})
out["run_action_dup"] = run_action("add_lead", {"name": "Rae Subprocess"})
out["run_action_list"] = run_action("list_my_followups", {})
out["run_action_leads_delta"] = ledger()["leads"] - before
out["run_action_row"] = [(r["owner_person"], r["created_by"]) for r in leads_store.find_by_name("Rae Subprocess")]

st = agent_status.status("member", "omar")
out["status_omar"] = [n in st["available"] for n in ("add_lead", "log_lead_contact", "list_my_followups")]
out["status_op"] = [n in agent_status.status("operator", "")["available"] for n in ("add_lead", "list_my_followups")]
out["status_render"] = "Leads: when they tell you about someone" in agent_status.render(st, "Omar")
out["feature_of"] = agent_status.feature_of("add_lead")

# ── the morning email ────────────────────────────────────────────────────────
sent, calls = [], []
real_send = broker.send_to_self
def fake_send(person, subject, text, kind, html=None):
    sent.append({"person": person, "subject": subject, "text": text, "kind": kind, "html": html or ""})
    return 200, {"ok": True}
broker.send_to_self = fake_send
def fake_model(prompt):
    calls.append(prompt)
    n = sum(1 for ln in prompt.splitlines() if ln[:1].isdigit() and ". {" in ln)
    return json.dumps({str(i): f"SUGGEST-{i} hello" for i in range(1, n + 1)}), {"input_tokens": 10, "output_tokens": 5}, "claude-haiku-5-5"
lead_followup._call_model = fake_model
lead_followup.STATE_PATH.unlink(missing_ok=True)
tz = lead_followup._zone()
def at(h, mi, day=28):
    return datetime(2026, 10, day, h, mi, tzinfo=tz).astimezone(timezone.utc)
out["settings"] = lead_followup.settings()
C0 = {k: ctr(k) for k in ("lead_followup_sent", "lead_followup_suggest_calls", "lead_followup_suggest_fallback",
                          "lead_followup_slot_missed", "lead_followup_nothing_due", "lead_followup_sentinel_off")}
out["tick1"] = lead_followup.tick(at(7, 5))
om = next((x for x in sent if x["person"] == "omar"), None)
ma = next((x for x in sent if x["person"] == "maya"), None)
out["mail_omar"] = om and {k: om[k] for k in ("subject", "kind")}
out["omar_text"] = om and om["text"]
out["omar_html_bits"] = om and ["Overdue (1)" in om["html"], "Today (1)" in om["html"], "SUGGEST-1 hello" in om["html"],
                                "SUGGEST-2 hello" in om["html"]]
out["maya_text"] = ma and ma["text"]
out["calls1"] = len(calls)
out["tick_again"] = [lead_followup.tick(at(7, 40)), len(sent), len(calls)]
accounts.set_feature("maya", "leads", False, by="dana")
out["tick_day2"] = lead_followup.tick(at(7, 5, day=29))
accounts.set_feature("maya", "leads", True, by="dana")
lead_followup.STATE_PATH.unlink(missing_ok=True)
n_before = len(sent)
out["tick_late"] = [lead_followup.tick(at(10, 30, day=30)), len(sent) - n_before]
def boom(prompt):
    raise RuntimeError("model down")
lead_followup._call_model = boom
fb = lead_followup.send_now("omar", at(8, 0))
out["fallback"] = [fb["result"], fb["suggested_by_model"], "couldn't write custom messages" in sent[-1]["text"],
                   "just following up" in sent[-1]["text"]]
out["nothing_due"] = lead_followup.send_now("priya", at(8, 0))["result"]
_, _, page = lead_followup.compose("Omar", [{"lead_id": "x", "name": "Eve <script>alert(1)</script>",
                                             "next_touch_at": "2026-10-20", "property": "<b>1 Main</b>"}],
                                   [], {"x": "hi <img src=x> & bye"}, True, at(7, 5), tz)
out["escape"] = {"raw": "<script>" in page or "<b>1 Main" in page or "<img" in page,
                 "escaped": "&lt;script&gt;" in page and "&lt;b&gt;1 Main" in page and "&amp; bye" in page}
broker.send_to_self = real_send
lead_followup._call_model = fake_model
n_before = len(sent)
real = lead_followup.send_now("omar", at(8, 0))
out["real_gate"] = [real["result"], len(sent) - n_before]
out["counters_mail"] = {k: ctr(k) - v for k, v in C0.items()}
os.environ["LT_RUNTIME"] = os.environ["LT_RUNTIME_OFF"]
out["off_by_default"] = [lead_followup.settings()["enabled"], lead_followup.tick(at(7, 5, day=31))]
os.environ["LT_RUNTIME"] = os.environ["LT_RUNTIME_ON"]

# ── the import ───────────────────────────────────────────────────────────────
import openpyxl
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Omar "
H = ["Lead ID", "Lead Name", "Phone", "Email", "Property Toured", "Lead Temperature", "Status Of Lead",
     "Follow-up Status", "Next Follow Up Date", "Assigned Agent", "Weird Col"]
ws.append(["OPEN HOUSE TRACKER"])
ws.append(H)
ws.append(["OMR-1", "Ivy Import", "512-555-0301", "", "12 Oak", "hot", "no response", "Emailed 6/15", "10/27/2026", "Omar", "x"])
ws.append(["OMR-2", "Jack Import", "", "jack@example.com", "14 Oak", "Warm", "Responded", "", datetime(2026, 11, 2), "Omar", ""])
ws.append(["OMR-3", "Kim Import", "", "", "", "", "", "", "someday", "Omar", ""])
ws.append(["", "Lou NoId", "512-555-0304", "", "", "", "", "", "", "Omar", ""])
ws2 = wb.create_sheet("Blake")
ws2.append(["Blake's leads"])
ws2.append(H)
ws2.append(["BLK-1", "Max Blake", "", "", "", "", "", "", "", "Blake", ""])
ws2.append(["OMR-1", "Ivy Import", "", "", "", "", "", "", "", "Blake", ""])
ws2.append(["OMR-2", "Different Person", "", "", "", "", "", "", "", "Blake", ""])
ws3 = wb.create_sheet("SEND LOG")
ws3.append(["when", "to", "what"])
buf = io.BytesIO()
wb.save(buf)
XLSX = buf.getvalue()
def up(k, name, data):
    return as_(k).post("/api/leads/import/preview", files={"file": (name, data, "application/octet-stream")})
L2, P0 = ledger(), ctr("leads_upload_previewed")
out["import_member"] = up("omar", "t.xlsx", XLSX).status_code
out["import_admin"] = up("priya", "t.xlsx", XLSX).status_code
pv = up("dana", "t.xlsx", XLSX).json()
out["preview"] = {k: pv.get(k) for k in ("leads", "new", "already_imported", "co_listed", "bad_dates", "due_now")}
out["preview_collisions"] = [x["ref"] for x in pv.get("collisions", [])]
out["preview_agents"] = sorted((a["name"], a["person_key"], a["leads"]) for a in pv.get("agents", []))
out["preview_tabs"] = [[t["tab"], t["leads"], t.get("skipped"), t.get("unmatched_headers")] for t in pv.get("tabs", [])]
out["ledger_preview"] = [L2, ledger(), ctr("leads_upload_previewed") - P0]
r = as_("dana").post("/api/leads/import/run", json={"token": pv["token"], "agents": {}})
out["run_unplaced"] = [r.status_code, r.json().get("error"), ledger() == L2]
r = as_("dana").post("/api/leads/import/run", json={"token": pv["token"], "agents": {"Blake": "zed"}})
out["run_unknown"] = [r.status_code, r.json().get("error")]
feed_before = len(feed())
r = as_("dana").post("/api/leads/import/run", json={"token": pv["token"], "agents": {"Blake": None}})
out["run1"] = [r.status_code, r.json().get("created"), r.json().get("updated")]
ivy = leads_store.get_by_external("OMR-1")
mx = leads_store.get_by_external("BLK-1")
jack = leads_store.get_by_external("OMR-2")
out["ivy"] = [ivy["owner_person"], ivy["next_touch_at"], ivy["status"], ivy["temperature"], ivy["follow_up"]]
out["jack_date"] = jack["next_touch_at"]
out["kim_date"] = (leads_store.get_by_external("OMR-3") or {}).get("next_touch_at")
out["max_owner"] = mx["owner_person"]
out["imported_touches"] = [t["kind"] for t in leads_store.touches(ivy["lead_id"])]
new_feed = feed()[feed_before:]
out["import_feed"] = [e["headline"] for e in new_feed]
out["ledger_run1"] = [L2, ledger()]
out["token_reuse"] = as_("dana").post("/api/leads/import/run", json={"token": pv["token"], "agents": {"Blake": None}}).json().get("error")
out["ivy_due_for_omar"] = any(l["name"] == "Ivy Import" and l["next_follow_up"] == "2026-10-27"
                              for l in as_("omar").get("/api/leads").json()["leads"])

as_("omar").patch(f"/api/leads/{ivy['lead_id']}", json={"status": "Responded"})
pv2 = up("dana", "t.xlsx", XLSX).json()
out["preview2"] = [pv2.get("new"), pv2.get("already_imported")]
if CANARY == "idempotent":
    leads_upload.leads_store.get_by_external = lambda ref: None
L3 = ledger()
r = as_("dana").post("/api/leads/import/run", json={"token": pv2["token"], "agents": {"Blake": "maya"}})
out["run2"] = [r.status_code, r.json().get("created"), r.json().get("updated"), L3["leads"], ledger()["leads"]]
leads_upload.leads_store.get_by_external = leads_store.get_by_external
out["ivy_after"] = (leads_store.get_by_external("OMR-1") or {}).get("status")
out["max_after"] = (leads_store.get_by_external("BLK-1") or {}).get("owner_person")

csv_bytes = b"Lead Name,Phone,Assigned Agent\nCara Csv,512-555-0401,Omar\n"
pc = up("dana", "Omar.csv", csv_bytes).json()
out["csv"] = [pc.get("leads"), [a["person_key"] for a in pc.get("agents", [])]]
X0 = ctr("leads_upload_xlsx_unavailable")
sys.modules["openpyxl"] = None
r = up("dana", "t.xlsx", XLSX)
del sys.modules["openpyxl"]
out["no_openpyxl"] = [r.status_code, r.json().get("error"), ctr("leads_upload_xlsx_unavailable") - X0]
out["bad_type"] = up("dana", "t.pdf", b"%PDF").json().get("error")

out["feed_emit_failed"] = ctr("leads_feed_emit_failed")
print("RESULT " + json.dumps(out, default=str))
'''


def provisioner() -> dict:
    code = r'''
import json, sys
sys.path.insert(0, "scripts")
import provision_tenant as pt
def die(msg): raise SystemExit("DIE " + msg)
pt.die = die
out = {"absent": pt.lead_followup({}), "on": pt.lead_followup({"lead_followup": {"enabled": True, "time_local": "06:30"}})}
for name, bad in (("time", {"time_local": "7am"}), ("key", {"emailed": True}), ("suggest", {"suggest": "yes"}),
                  ("yes_string", {"enabled": "yes"})):
    try:
        out[name] = pt.lead_followup({"lead_followup": bad})
    except SystemExit as exc:
        out[name] = str(exc)
print(json.dumps(out))
'''
    p = subprocess.run([sys.executable, "-c", code], cwd=str(APP_ROOT), capture_output=True, text=True)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        print(p.stderr[-1500:])
        return {}


def child(box: pathlib.Path) -> dict:
    shared = box / "skills" / "_shared"
    shared.mkdir(parents=True)
    for name, src in SHARED.items():
        (shared / name).write_text(src)
    (box / "hermes" / "state").mkdir(parents=True)
    team = {"people": {"dana": {"name": "Dana Owner", "display_name": "Dana", "roles": ["owner"]},
                       "priya": {"name": "Priya Admin", "display_name": "Priya", "roles": ["admin"]},
                       "omar": {"name": "Omar Agent", "display_name": "Omar", "roles": ["member"]},
                       "maya": {"name": "Maya Agent", "display_name": "Maya", "roles": ["member"]}}}
    (box / "identity.json").write_text(json.dumps(team))
    base = {"client": {"timezone": "America/Chicago", "slug": "lt"}, "skills": {"leads": {"source": "store"}}}
    (box / "runtime_on.json").write_text(json.dumps({**base, "app": {"lead_followup": {
        "enabled": True, "time_local": "07:00", "suggest": True}}}))
    (box / "runtime_off.json").write_text(json.dumps({**base, "app": {}}))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC", "HERMES_"))}
    extra = os.environ.get("LT_PYTHONPATH", "")
    env.update({
        "APP_ROOT": str(APP_ROOT), "PYTHONPATH": os.pathsep.join(x for x in (extra, str(APP_ROOT)) if x),
        "HERMES_HOME": str(box / "hermes"), "JARVIS_SKILLS_ROOT": str(box / "skills"),
        "LT_RUNTIME": str(box / "runtime_on.json"), "LT_RUNTIME_ON": str(box / "runtime_on.json"),
        "LT_RUNTIME_OFF": str(box / "runtime_off.json"), "LT_IDENTITY": str(box / "identity.json"),
        "JARVIS_LEADS_DB": str(box / "leads.db"), "JARVIS_ACCOUNTS_DB": str(box / "accounts.db"),
        "JARVIS_THREADS_DB": str(box / "threads.db"), "JARVIS_TASKS_DB": str(box / "tasks.db"),
        "JARVIS_ACTIVITY_FEED": str(box / "feed.jsonl"), "JARVIS_TEAM_FILE": str(box / "hermes" / "state" / "team.json"),
        "LT_TODAY": "2026-10-28", "JARVIS_SWITCH_SENTINEL": "off", "JARVIS_SWITCH_BROKER": "on", "JARVIS_SENTINEL_MODE": "enforce",
    })
    for flag in ("scope", "cadence", "dedupe", "idempotent", "escape", "feed"):
        if f"--canary-{flag}" in sys.argv:
            env["CANARY"] = flag
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(APP_ROOT), capture_output=True, text=True,
                       timeout=600)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(p.stdout[-1500:], p.stderr[-4000:])
        return {}
    return json.loads(line[-1][7:])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="verify-leads-") as tmp:
        r = child(pathlib.Path(tmp))
    if not r:
        ck("the child ran", False)
        return 1
    if os.environ.get("LT_DUMP"):
        print(json.dumps(r, indent=1))

    print("── cadence and the timeline ──")
    ck("Hot: due tomorrow, owner set by the owner, status New", r["create_hot"] == [201, "2026-10-29", "omar", "New"],
       r["create_hot"])
    ck("Warm: due in 2 days, a member's lead is their own", r["create_warm"] == [201, "2026-10-30", "omar"], r["create_warm"])
    ck("no temperature: due in 3 days", r["create_plain"] == [201, "2026-10-31", "maya"], r["create_plain"])
    ck("missed call: New -> No Response, next in 7 days", r["missed_call"] == [201, "No Response", "2026-11-04"],
       r["missed_call"])
    ck("reached by text: -> Responded, next in 14 days", r["reached_text"] == [201, "Responded", "2026-11-11"],
       r["reached_text"])
    ck("a note moves neither status nor date", r["note"] == [201, "Responded", "2026-11-11"], r["note"])
    ck("a date they give wins over the cadence", r["given_date"] == "2026-12-01", r["given_date"])
    ck("timeline: created, call, text, note, call; by Omar; the note text kept",
       [t[0] for t in r["timeline"]] == ["created", "call", "text", "note", "call"]
       and r["timeline"][1][1] == "Omar" and r["timeline"][1][2] == "left a voicemail", r["timeline"])
    ck("operator: the same five entries, every note withheld",
       [t[0] for t in r["op_timeline"]] == ["created", "call", "text", "note", "call"]
       and all(t[1] is None for t in r["op_timeline"]), r["op_timeline"])
    ck("operator can't log a contact (403)", r["op_touch"] == 403, r["op_touch"])
    ck("a new lead posts one feed line", r["feed_new_lead"] is True)
    ck("   no feed write failed (leads_feed_emit_failed 0)", r["feed_emit_failed"] == 0, r["feed_emit_failed"])

    print("\n── who reaches which lead ──")
    ck("omar's list: his two, not maya's", r["omar_list"] == ["Sarah Lee", "Wes Warm"], r["omar_list"])
    ck("   and the list carries today in the tenant's zone", r["omar_list_today"] == "2026-10-28", r["omar_list_today"])
    ck("omar can open his own lead (200)", r["omar_open_own"] == 200, r["omar_open_own"])
    ck("omar can't open maya's lead (404)", r["omar_open_mia"] == 404, r["omar_open_mia"])
    ck("   nor edit, archive or log on it (404 each)",
       [r["omar_patch_mia"], r["omar_archive_mia"], r["omar_touch_mia"]] == [404, 404, 404],
       [r["omar_patch_mia"], r["omar_archive_mia"], r["omar_touch_mia"]])
    ck("   and it is unchanged (New, not archived, one timeline entry)", r["mia_untouched"] == ["New", False, 1],
       r["mia_untouched"])
    ck("the admin opens maya's lead (200)", r["admin_open_mia"] == 200, r["admin_open_mia"])
    ck("a member can't give a lead to someone else (403 on create and on edit)",
       r["member_gives_away"] == 403 and r["member_reassign_own"] == 403,
       [r["member_gives_away"], r["member_reassign_own"]])
    a, b = r["ledger_routes"]
    ck(f"ledger: +3 leads, +7 timeline rows ({a} -> {b})",
       b["leads"] - a["leads"] == 3 and b["touches"] - a["touches"] == 7)

    print("\n── Jarvis's tools ──")
    ck("add_lead: Omar's, warm -> due in 2 days, created_by jarvis, source Open house",
       r["tool_add"] == [200, "Omar", "2026-10-30", "jarvis", "Open house"], r["tool_add"])
    ck("duplicate: same phone, other format -> not added, his match shown",
       r["tool_dup_phone"] == [409, "duplicate", ["Sarah Lee"]], r["tool_dup_phone"])
    ck("duplicate of a teammate's lead: refused without its details",
       r["tool_dup_other"] == [409, "duplicate", [], 1], r["tool_dup_other"])
    ck("add_anyway adds it, as his", r["tool_add_anyway"] == [200, "Omar"], r["tool_add_anyway"])
    ck("log_lead_contact by name: Responded, next in 7 days", r["tool_log"] == [200, "Responded", "2026-11-04"],
       r["tool_log"])
    ck("log on a teammate's lead: not found", r["tool_log_other"] == [404, "not_found"], r["tool_log_other"])
    ck("list_my_followups: Olly overdue, Toby today, nobody else's",
       r["tool_followups"] == [200, ["Olly Overdue"], ["Toby Today"]], r["tool_followups"])
    ck("   a member asking for everyone still gets only his",
       r["tool_followups_member_everyone"] == ["Olly Overdue", "Toby Today"], r["tool_followups_member_everyone"])
    ck("   an admin asking for everyone gets maya's too, not the Not Interested one",
       r["tool_followups_admin_everyone"] == ["Mo Maya", "Olly Overdue", "Toby Today"],
       r["tool_followups_admin_everyone"])
    ck("no turn -> 403; ended turn -> 403", r["tool_no_ref"] == 403 and r["tool_ended_ref"] == 403,
       [r["tool_no_ref"], r["tool_ended_ref"]])
    ck("Leads switched off for omar -> 403, counted once", r["tool_feature_off"] == [403, 1], r["tool_feature_off"])
    a, b, added = r["ledger_tools"]
    ck(f"ledger: +4 leads from the tools, lead_tool_added +4 ({a} -> {b})",
       b["leads"] - a["leads"] == 6 and added == 4, f"{a} -> {b}, added {added} (2 more are maya's via the API)")
    ra, rd = r["run_action_add"], r["run_action_dup"]
    ck("run_action.py add_lead: ok, Omar's, Hot", ra.get("ok") is True and (ra.get("lead") or {}).get("agent") == "Omar"
       and (ra.get("lead") or {}).get("temperature") == "Hot", ra)
    ck("   again: not added, told to ask", rd.get("duplicate") is True and "Not added" in str(rd.get("say")), rd)
    ck("   list_my_followups answers", r["run_action_list"].get("ok") is True, r["run_action_list"])
    ck("   ledger: exactly one row, Omar's, created by Jarvis",
       r["run_action_leads_delta"] == 1 and r["run_action_row"] == [["omar", "jarvis"]],
       [r["run_action_leads_delta"], r["run_action_row"]])
    ck("status block: omar has the three lead tools, the operator none",
       r["status_omar"] == [True, True, True] and r["status_op"] == [False, False], [r["status_omar"], r["status_op"]])
    ck("   tells the agent when to use them; the Leads switch governs them",
       r["status_render"] is True and r["feature_of"] == "leads")

    print("\n── the morning email ──")
    ck("reads app.lead_followup (on, 07:00, suggest)",
       r["settings"] == {"enabled": True, "time_local": "07:00", "suggest": True}, r["settings"])
    ck("7:05: omar and maya get one each; dana and priya have nothing due",
       r["tick1"] == {"omar": "sent", "maya": "sent"}, r["tick1"])
    mo = r["mail_omar"] or {}
    ck("omar's: '2 leads to follow up today (1 overdue)', ledger kind lead_followup.<slot>",
       mo.get("subject") == "2 leads to follow up today (1 overdue)"
       and mo.get("kind") == "lead_followup.2026-10-28T07:00", mo)
    t = r["omar_text"] or ""
    ck("   overdue first, then today", 0 <= t.find("Olly Overdue") < t.find("Toby Today"))
    ck("   nothing not yet due, nobody else's",
       all(x not in t for x in ("Sarah Lee", "Tina Open", "Wes Warm", "Mo Maya", "Mia Cold")))
    ck("   HTML: Overdue (1), Today (1), a suggested message per lead", r["omar_html_bits"] == [True] * 4,
       r["omar_html_bits"])
    mt = r["maya_text"] or ""
    ck("maya's: hers, and not the Not Interested one", "Mo Maya" in mt and "Nina NotInt" not in mt
       and "Olly" not in mt)
    ck("one model call per agent", r["calls1"] == 2, r["calls1"])
    ck("the same slot again: nothing sent, no model call", r["tick_again"] == [{}, 2, 2], r["tick_again"])
    ck("Leads switched off for maya: skipped; omar still served",
       r["tick_day2"].get("maya") == "leads_off" and r["tick_day2"].get("omar") == "sent", r["tick_day2"])
    ck("a slot more than 2 hours late is skipped, not sent",
       r["tick_late"][1] == 0 and set(r["tick_late"][0].values()) == {"missed"}, r["tick_late"])
    ck("model down: still sent, templates, and the email says so",
       r["fallback"] == ["sent", False, True, True], r["fallback"])
    ck("nobody due: nothing sent", r["nothing_due"] == "nothing_due", r["nothing_due"])
    ck("hostile lead name shown as text", r["escape"] == {"raw": False, "escaped": True}, r["escape"])
    ck("the real send_to_self is the gate: Sentinel off -> refused, nothing sent",
       r["real_gate"] == ["sentinel_off", 0], r["real_gate"])
    cm = r["counters_mail"]
    ck(f"counters: sent +4, model calls +5, fallback +1, missed +2, nothing_due +1, sentinel_off +1 ({cm})",
       cm == {"lead_followup_sent": 4, "lead_followup_suggest_calls": 5, "lead_followup_suggest_fallback": 1,
              "lead_followup_slot_missed": 2, "lead_followup_nothing_due": 1, "lead_followup_sentinel_off": 1})
    ck("off when app.lead_followup is absent", r["off_by_default"] == [False, {}], r["off_by_default"])

    print("\n── the spreadsheet import ──")
    ck("owner only: member 403, admin 403", r["import_member"] == 403 and r["import_admin"] == 403,
       [r["import_member"], r["import_admin"]])
    ck("preview: 5 leads, all new, 1 co-listed, 1 unreadable date, 1 overdue",
       r["preview"] == {"leads": 5, "new": 5, "already_imported": 0, "co_listed": 1, "bad_dates": 1, "due_now": 1},
       r["preview"])
    ck("   the reused ID under a different name is reported", r["preview_collisions"] == ["OMR-2"], r["preview_collisions"])
    ck("   Omar placed, Blake not", r["preview_agents"] == [["Blake", None, 1], ["Omar", "omar", 4]], r["preview_agents"])
    ck("   tabs: two read (header under a banner), SEND LOG skipped, odd column listed",
       r["preview_tabs"][0][:2] == ["Omar", 4] and r["preview_tabs"][0][3] == ["Weird Col"]
       and r["preview_tabs"][1][:2] == ["Blake", 1] and r["preview_tabs"][2][2] == "no Lead Name column",
       r["preview_tabs"])
    a, b, pc = r["ledger_preview"]
    ck("   preview wrote nothing (presence: previewed +1, the refused two don't count)", a == b and pc == 1, f"{a} -> {b}, {pc}")
    ck("import refuses until Blake is answered, writes nothing", r["run_unplaced"] == [422, "unplaced_agents", True],
       r["run_unplaced"])
    ck("import refuses a person who isn't on the team", r["run_unknown"] == [422, "unknown_person"], r["run_unknown"])
    ck("import: 5 created", r["run1"] == [200, 5, 0], r["run1"])
    ck("   Ivy: omar's, due 2026-10-27, No Response, Hot, her follow-up text kept",
       r["ivy"] == ["omar", "2026-10-27", "No Response", "Hot", "Emailed 6/15"], r["ivy"])
    ck("   a real date cell and an unreadable one", r["jack_date"] == "2026-11-02" and r["kim_date"] is None,
       [r["jack_date"], r["kim_date"]])
    ck("   Blake's lead belongs to nobody yet; each lead's timeline starts 'imported'",
       r["max_owner"] is None and r["imported_touches"] == ["imported"], [r["max_owner"], r["imported_touches"]])
    ck("   one feed line for the import, none per lead", r["import_feed"] == ["Imported 5 leads from the spreadsheet"],
       r["import_feed"])
    a, b = r["ledger_run1"]
    ck(f"   ledger: +5 leads, +5 timeline rows ({a} -> {b})",
       b["leads"] - a["leads"] == 5 and b["touches"] - a["touches"] == 5)
    ck("   the preview can't be imported twice", r["token_reuse"] == "expired", r["token_reuse"])
    ck("   omar sees Ivy on his list, due yesterday", r["ivy_due_for_omar"] is True)
    ck("re-import preview: 0 new, 5 already here", r["preview2"] == [0, 5], r["preview2"])
    ck("re-import adds nothing", r["run2"][:3] == [200, 0, 5] and r["run2"][3] == r["run2"][4], r["run2"])
    ck("   and keeps omar's edit (Responded), while filling Blake's owner (maya)",
       r["ivy_after"] == "Responded" and r["max_after"] == "maya", [r["ivy_after"], r["max_after"]])
    ck(".csv: header in the first row, agent placed", r["csv"] == [1, ["omar"]], r["csv"])
    ck("no openpyxl: says so (422 xlsx_unavailable), counted", r["no_openpyxl"] == [422, "xlsx_unavailable", 1],
       r["no_openpyxl"])
    ck("other files refused", r["bad_type"] == "bad_type", r["bad_type"])

    print("\n── the provisioner ──")
    p = provisioner()
    ck("absent: off, 07:00, suggestions on",
       p.get("absent") == {"enabled": False, "time_local": "07:00", "suggest": True}, p.get("absent"))
    ck("enabled: true turns it on at the given time", (p.get("on") or {}).get("enabled") is True
       and p["on"]["time_local"] == "06:30")
    ck("refuses a bad time, an unknown key, a non-boolean suggest",
       all(str(p.get(k, "")).startswith("DIE ") for k in ("time", "key", "suggest")))
    ck("enabled: 'yes' (not true) stays off", (p.get("yes_string") or {}).get("enabled") is False)

    print(f"\n{sum(ok)}/{len(ok)} passed")
    return 0 if ok and all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
