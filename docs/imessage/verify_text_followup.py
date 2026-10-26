#!/usr/bin/env python3
"""Texts waiting for a reply, emailed at set times (app/text_followup.py and
broker.send_to_self), against the verify sandbox's databases (:8711,
docs/tasks/tasks_sandbox.sh). Gmail is faked at googleapiclient; everything
else is the code under test: the accounts database, the Postgres actions
ledger, tenant config read from a runtime.json carrying app.text_followup.

Checked, each with the ledger (actions rows for skill text-followup, counters)
before and after:
- dana (owner, Mac + own Google) at 7:05 gets ONE email, from her own Gmail
  to her own address and nobody else: the 1-on-1 and the group chat whose last
  word is someone else's, 2h-7d old; not the one she answered, not the
  30-minute-old one, not the 9-day-old one, not omar's texts.
- omar (Mac, no Google) is skipped as not_connected: no email, no approval.
- the same slot again sends nothing; at 5:05 PM with everything answered dana
  gets "all caught up".
- send switched off for omar (now connected): nothing; dana still gets hers.
- a slot more than 2h late is skipped, not sent.
- practice mode, a policy block, and a second recipient each send nothing and
  leave a ledger row saying why.
- everything else from her Gmail still waits for her tap (the agent's
  personal broker route files an approval for a mail to her own address).
- the Settings route sends one now; the status says where reminders go.
- the provisioner's text_followup block: defaults, and refusals.

--canary: the 2-hour floor is set to 0, so the 30-minute-old conversation is
listed; "not the 30-minute-old one" must FAIL.

Never :8700. Prints no message text beyond the fixture's own made-up lines.
"""
import json
import os
import pathlib
import subprocess
import sys

BASE = os.environ.get("AUTH_BASE", "http://127.0.0.1:8711")
if BASE.endswith((":8700", ":8710")):
    sys.exit("REFUSING: live or testco port")
SB = pathlib.Path(os.environ.get("TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox"))
APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCR = pathlib.Path("/tmp/verify-text-followup")
SCR.mkdir(parents=True, exist_ok=True)

ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" ({extra})" if extra and not cond else ""))


CHILD = r'''
import base64, json, os, sys
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
sys.path.insert(0, os.environ["APP_ROOT"])
import googleapiclient.discovery as disc
from app import accounts, broker, google_connection, imessage, platform_db, text_followup
from app import threads as threads_mod

sent = []
class _Svc:
    def __init__(self, path=(), kw=None): self.path, self.kw = path, kw or {}
    def __getattr__(self, name): return lambda *a, **k: _Svc(self.path + (name,), k or self.kw)
    def execute(self):
        if self.path[-1] == "send":
            sent.append(message_from_bytes(base64.urlsafe_b64decode(self.kw["body"]["raw"] + "==")))
        return {"id": f"fake-{len(sent)}"}
disc.build = lambda *a, **k: _Svc()
google_connection.person_credentials = lambda person_key: object()
if os.environ.get("CANARY") == "1":
    real_settings = text_followup.settings
    text_followup.settings = lambda: {**real_settings(), "older_than_hours": 0}

PEOPLE = ("dana", "omar")
OWN = os.environ["OWN"]
conn = imessage._db()
def clean():
    for p in PEOPLE:
        conn.execute("DELETE FROM imessages WHERE person_key=? AND guid LIKE 'tf-%'", (p,))
        conn.execute("DELETE FROM mac_devices WHERE device_id LIKE 'tf-%'")
        conn.execute("DELETE FROM person_google WHERE person_key=? AND (google_email = ? OR google_email = 'omar.own@example.com')", (p, OWN))
    conn.commit()
    text_followup.STATE_PATH.unlink(missing_ok=True)
clean()
out = {"pre_person_google": [r[0] for r in conn.execute("SELECT person_key FROM person_google")],
       "pre_macs": [r[0] for r in conn.execute("SELECT person_key FROM mac_devices WHERE revoked_at IS NULL")]}

tz, tz_name = text_followup._zone()
out["tz"] = tz_name
day = datetime.now(tz).date() - timedelta(days=1)
def at(h, m, d=0):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz).astimezone(timezone.utc) + timedelta(days=d)
now = at(7, 5)
for p in PEOPLE:
    conn.execute("INSERT INTO mac_devices (device_id, person_key, name, token_hash, created_at) VALUES (?,?,?,?,?)",
                 (f"tf-{p}", p, "Fixture Mac", f"tf-hash-{p}", (now - timedelta(days=20)).isoformat()))
conn.commit()
def msg(guid, chat, sender, mine, ago, text):
    return {"guid": f"tf-{guid}", "chat": chat, "sender": sender, "is_from_me": mine,
            "sent_at": (now - ago).isoformat(), "text": text}
H, M, D = (lambda n: timedelta(hours=n)), (lambda n: timedelta(minutes=n)), (lambda n: timedelta(days=n))
real_now = imessage._now
imessage._now = lambda: now
imessage.store_batch("dana", "tf-dana", [
    msg("a1", "Jane Buyer", "Jane Buyer", False, H(5), "earlier line from Jane"),
    msg("a2", "Jane Buyer", "Jane Buyer", False, H(3), "Can we see the Elm St house Thursday?\nsecond line"),
    msg("b1", "Bob Seller", "Bob Seller", False, H(6), "Offer is in"),
    msg("b2", "Bob Seller", "me", True, H(4), "ANSWERED-BOB great, thanks"),
    msg("c1", "Cara New", "Cara New", False, M(30), "TOO-NEW just texted"),
    msg("d1", "Dan Old", "Dan Old", False, D(9), "TOO-OLD from last week"),
    msg("e1", "Land deals team", "Priya", False, H(5), "Group: who is walking the lot Friday?"),
])
imessage.store_batch("omar", "tf-omar", [msg("o1", "Omar Client", "Omar Client", False, H(4), "OMAR-ONLY text")])
imessage._now = real_now

google_connection.put_person("dana", google_email=OWN,
                             scopes=" ".join(google_connection.PERSON_SCOPES), token_cipher=b"fixture")
cfg = {**text_followup.settings()}
out["settings"] = cfg

def ledger():
    with platform_db.connect() as c:
        rows = c.execute("SELECT outcome, destinations FROM actions WHERE skill = 'text-followup' ORDER BY id").fetchall()
    return [[r["outcome"], list(r["destinations"] or [])] for r in rows]
def counter(name):
    return threads_mod.bump_counter(name, 0)
def pending():
    from app import approvals
    return approvals.query(status="pending")["count"]

L0, P0 = ledger(), pending()
C0 = {k: counter(k) for k in ("text_followup_sent", "text_followup_not_connected", "text_followup_feature_off",
                              "text_followup_slot_missed", "broker_self_send_executed", "broker_self_send_refused")}
out["waiting"] = [w["chat"] for w in text_followup.waiting("dana", now)]
out["tick1"] = text_followup.tick(now)
m = sent[0] if sent else None
body = (m.get_payload(decode=True) or b"").decode() if m is not None and not m.is_multipart() else \
    (next((p.get_payload(decode=True) for p in m.walk() if p.get_content_type() == "text/plain"), b"").decode() if m else "")
out["mail1"] = {"n": len(sent), "to": m and m.get_all("To"), "from": m and m.get("From"), "cc": m and m.get_all("Cc"),
                "bcc": m and m.get_all("Bcc"), "subject": m and m.get("Subject"), "body": body}
L1, P1 = ledger(), pending()
out["ledger1"] = L1[len(L0):]
out["pending1"] = P1 - P0
out["counters1"] = {k: counter(k) - v for k, v in C0.items()}

out["tick_again"] = [text_followup.tick(at(7, 10)), len(sent)]

imessage._now = lambda: at(16, 0)
imessage.store_batch("dana", "tf-dana", [
    {"guid": "tf-a3", "chat": "Jane Buyer", "sender": "me", "is_from_me": True, "sent_at": at(16, 0).isoformat(), "text": "Yes, 2pm"},
    {"guid": "tf-e2", "chat": "Land deals team", "sender": "me", "is_from_me": True, "sent_at": at(16, 0).isoformat(), "text": "I will"},
    {"guid": "tf-c2", "chat": "Cara New", "sender": "me", "is_from_me": True, "sent_at": at(16, 0).isoformat(), "text": "Hi Cara"},
])
imessage._now = real_now
out["tick_pm"] = text_followup.tick(at(17, 5))
pm = sent[-1] if len(sent) == 2 else None
out["mail_pm"] = {"n": len(sent), "subject": pm and pm.get("Subject"), "to": pm and pm.get_all("To")}

google_connection.put_person("omar", google_email="omar.own@example.com",
                             scopes=" ".join(google_connection.PERSON_SCOPES), token_cipher=b"fixture")
accounts.set_feature("omar", "send", False, by="dana")
before = len(sent)
out["tick_off"] = text_followup.tick(at(7, 5, d=1))
out["off_sends"] = [len(sent) - before, [x.get("To") for x in sent[before:]]]
accounts.set_feature("omar", "send", True, by="dana")

text_followup.STATE_PATH.unlink(missing_ok=True)
before = len(sent)
out["tick_late"] = [text_followup.tick(at(10, 30, d=1)), len(sent) - before]

before, L2 = len(sent), ledger()
real_mode = broker.sentinel_mode
broker.sentinel_mode = lambda: broker.SHADOW
out["shadow"] = [text_followup.send_now("dana", at(11, 0, d=1))["result"], len(sent) - before]
broker.sentinel_mode = real_mode
real_policy = broker.store.policy_modes
broker.store.policy_modes = lambda *a, **k: ("block", None)
out["blocked"] = [text_followup.send_now("dana", at(11, 0, d=1))["result"], len(sent) - before]
broker.store.policy_modes = real_policy
real_rcpt = broker._recipients
broker._recipients = lambda params: (real_rcpt(params)[0] + ["someone@else.example"], None)
out["second_rcpt"] = [text_followup.send_now("dana", at(11, 0, d=1))["result"], len(sent) - before]
broker._recipients = real_rcpt
out["refusal_rows"] = [r[0] for r in ledger()[len(L2):]]

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import actor_refs, auth
broker._authorize = lambda request: None
api = FastAPI(); api.include_router(broker.router); api.include_router(imessage.settings_router)
api.dependency_overrides[auth.current_session] = lambda: {"person_key": "dana", "role": "owner", "kind": "person"}
c = TestClient(api)
ref = actor_refs.mint("dana", "t-dana", "owner")
from email.message import EmailMessage
em = EmailMessage(); em["To"], em["Subject"] = OWN, "note to self"; em.set_content("x")
before, p0 = len(sent), pending()
r = c.post("/api/broker/google/person", json={"ref": ref, "api": "gmail", "version": "v1", "method": "users.messages.send",
           "params": {"userId": "me", "body": {"raw": base64.urlsafe_b64encode(em.as_bytes()).decode()}}})
out["agent_self_mail"] = [r.status_code, (r.json() or {}).get("error"), len(sent) - before, pending() - p0]
out["agent_self_mail_detail"] = (r.json() or {}).get("detail")
from app import approvals, platform_store
out["rate_reminders"] = platform_store.rates(datetime.now(timezone.utc)).per_recipient_today.get(OWN, 0)
aid = (r.json() or {}).get("approval_id")
if aid:
    approvals.decide(aid, "approved", "dana")
out["rate_after_agent"] = [len(sent) - before, platform_store.rates(datetime.now(timezone.utc)).per_recipient_today.get(OWN, 0)]
actor_refs.end(ref)
out["routes"] = sorted(getattr(x, "path", "") for x in broker.router.routes)
st = c.get("/api/imessage/status").json().get("reminder") or {}
out["status"] = {k: st.get(k) for k in ("enabled", "times_local", "to", "google_status", "send_on")}
before = len(sent)
r = c.post("/api/imessage/reminder/email")
out["send_route"] = [r.status_code, r.json(), len(sent) - before, sent[-1].get_all("To") if len(sent) > before else None]
api.dependency_overrides[auth.current_session] = lambda: {"person_key": "priya", "role": "admin", "kind": "person"}
r = c.post("/api/imessage/reminder/email")
out["send_route_unconnected"] = [r.status_code, len(sent) - before - 1]

clean()
out["post_person_google"] = [r[0] for r in conn.execute("SELECT person_key FROM person_google")]
print("RESULT " + json.dumps(out, default=str))
'''


OWN = f"dana.tf{os.getpid()}x{int(__import__('time').time())}@example.com"


def child() -> dict:
    skills = os.environ.get("VERIFY_SKILLS_ROOT", "/opt/jarvis-verify-fixture/skills/openclaw-imports")
    rt = json.loads((SB / "runtime.json").read_text())
    rt.setdefault("app", {})["text_followup"] = {"enabled": True, "times_local": ["07:00", "17:00"],
                                                 "older_than_hours": 2, "lookback_days": 7}
    rt_path = SCR / "runtime.json"
    rt_path.write_text(json.dumps(rt))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC"))}
    env.update({
        "APP_ROOT": str(APP_ROOT), "APP_GOOGLE_OAUTH_MOCK": "1",
        "APP_GOOGLE_TOKEN_KEY": (SB / "token_key").read_text().strip(),
        "HERMES_HOME": str(SB / "hermes"), "JARVIS_SKILLS_ROOT": skills, "JARVIS_APP_ROOT": str(APP_ROOT),
        "JARVIS_RUNTIME_CONFIG": str(rt_path),
        "JARVIS_IDENTITY_CONFIG": "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json",
        "JARVIS_ACCOUNTS_DB": str(SB / "accounts.db"), "JARVIS_THREADS_DB": str(SB / "threads.db"),
        "JARVIS_TASKS_DB": str(SB / "tasks.db"), "JARVIS_LEADS_DB": str(SB / "leads.db"),
        "JARVIS_ACTIVITY_FEED": str(SB / "feed.jsonl"), "JARVIS_PG_DSN": (SB / "pg_dsn").read_text().strip(),
        "JARVIS_SWITCH_BROKER": "on", "JARVIS_SWITCH_SENTINEL": "on",
        "JARVIS_SENTINEL_MODE": "enforce", "JARVIS_ALLOW_WRITES": "1",
        "JARVIS_TEAM_FILE": str(SB / "hermes" / "state" / "team.json"), "OWN": OWN,
    })
    if "--canary" in sys.argv:
        env["CANARY"] = "1"
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(APP_ROOT),
                       capture_output=True, text=True, timeout=180)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(p.stdout[-1500:], p.stderr[-3000:])
        return {}
    return json.loads(line[-1][7:])


def provisioner() -> dict:
    code = r'''
import json, sys
sys.path.insert(0, "scripts")
import provision_tenant as pt
def die(msg): raise SystemExit("DIE " + msg)
pt.die = die
out = {"absent": pt.text_followup({}),
       "on": pt.text_followup({"text_followup": {"enabled": True, "times_local": ["17:00", "07:00"]}})}
for name, bad in (("time", {"times_local": ["7am"]}), ("key", {"emailed": True}), ("days", {"lookback_days": 0}),
                  ("yes_string", {"enabled": "yes"})):
    try:
        out[name] = pt.text_followup({"text_followup": bad})
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


def main() -> int:
    r = child()
    if not r:
        ck("the child ran", False)
        return 1
    print(f"  (timezone {r.get('tz')}; settings {r.get('settings')})")
    ck("the reminder reads app.text_followup from tenant config (enabled, 7:00 and 17:00)",
       r["settings"].get("enabled") is True and r["settings"].get("times_local") == ["07:00", "17:00"])

    print("\n── 7:05 AM ──")
    w = r["waiting"]
    ck("waiting: the 1-on-1 and the group chat whose last word is someone else's",
       sorted(w) == ["Jane Buyer", "Land deals team"], w)
    m = r["mail1"]
    ck("ONE email, at the 7 AM slot", m["n"] == 1 and (m["subject"] or "").startswith("7 AM: 2 texts"), m["subject"])
    ck("   to her own address only, from her own Gmail, no Cc or Bcc",
       m["to"] == [OWN] and m["from"] == OWN and not m["cc"] and not m["bcc"],
       f"to={m['to']} from={m['from']} cc={m['cc']} bcc={m['bcc']}")
    b = m["body"]
    ck("   lists Jane's newest message, first line only", "Can we see the Elm St house Thursday?" in b
       and "second line" not in b and "earlier line" not in b)
    ck("   lists the group chat as who-in-which", "Priya in Land deals team" in b and "walking the lot" in b)
    ck("   not the conversation she answered", "Bob Seller" not in b and "ANSWERED-BOB" not in b)
    ck("   not the 30-minute-old one", "Cara New" not in b and "TOO-NEW" not in b)
    ck("   not the 9-day-old one", "Dan Old" not in b and "TOO-OLD" not in b)
    ck("   not another person's texts", "OMAR" not in b and "Omar Client" not in b)
    ck(f"ledger: 2 rows for her (allowed, executed), to her address only ({r['ledger1']})",
       r["ledger1"] == [["allowed", [OWN]], ["executed", [OWN]]])
    ck(f"   no approval was filed ({r['pending1']})", r["pending1"] == 0)
    c1 = r["counters1"]
    ck(f"counters: sent +1, self-send executed +1, omar not_connected +1 ({c1})",
       c1["text_followup_sent"] == 1 and c1["broker_self_send_executed"] == 1 and c1["text_followup_not_connected"] == 1)
    ck(f"omar (Mac, no Google) is skipped as not_connected ({r['tick1']})",
       r["tick1"] == {"dana": "sent", "omar": "not_connected"})

    print("\n── the same slot, then 5 PM ──")
    ck(f"the same slot again sends nothing ({r['tick_again']})", r["tick_again"] == [{}, 1])
    pm = r["mail_pm"]
    ck(f"5:05 PM with everything answered: one 'all caught up' email ({pm['subject']})",
       pm["n"] == 2 and pm["subject"] == "5 PM: you're all caught up on texts" and pm["to"] == [OWN])

    print("\n── switches, late slots, refusals ──")
    ck(f"send switched off for omar: nothing for him, dana still gets hers ({r['tick_off']}, {r['off_sends']})",
       r["tick_off"] == {"dana": "sent", "omar": "feature_off"} and r["off_sends"] == [1, [OWN]])
    ck(f"a slot more than 2 hours late is skipped, not sent ({r['tick_late']})",
       r["tick_late"][1] == 0 and set(r["tick_late"][0].values()) == {"missed"})
    ck(f"practice mode sends nothing ({r['shadow']})", r["shadow"] == ["shadowed", 0])
    ck(f"a policy block sends nothing ({r['blocked']})", r["blocked"] == ["sentinel_denied", 0])
    ck(f"a second recipient is refused ({r['second_rcpt']})", r["second_rcpt"] == ["not_self", 0])
    ck(f"   each refusal left a ledger row saying why ({r['refusal_rows']})", r["refusal_rows"] == ["shadowed", "denied"])

    print("\n── nothing else changed ──")
    print(f"  (agent route said: {r.get('agent_self_mail_detail')})")
    ck(f"the agent's own route, mailing her own address, still waits for her tap ({r['agent_self_mail']})",
       r["agent_self_mail"][:3] == [403, "approval_required", 0] and r["agent_self_mail"][3] == 1)
    ck(f"   reminders don't use up the 3-a-day per-recipient limit ({r.get('rate_reminders')} counted after "
       f"{r['mail_pm']['n'] + 1} reminders)", r.get("rate_reminders") == 0)
    ck(f"   presence twin: once she approves the agent's mail it is sent and counts ({r.get('rate_after_agent')})",
       r.get("rate_after_agent") == [1, 1])
    ck("   no broker route reaches the reminder", not any("remind" in p or "self" in p for p in r["routes"]), r["routes"])
    st = r["status"]
    ck(f"Settings status: on, 7 AM and 5 PM, to her own Gmail ({st})",
       st == {"enabled": True, "times_local": ["07:00", "17:00"], "to": OWN,
              "google_status": "connected", "send_on": True})
    sr = r["send_route"]
    ck(f"'Email me now' sends one, to her ({sr})", sr[0] == 200 and sr[2] == 1 and sr[3] == [OWN])
    ck(f"   someone without Google is told to connect it, nothing sent ({r['send_route_unconnected']})",
       r["send_route_unconnected"] == [409, 0])
    ck("fixture cleaned up (no person_google rows it added)",
       sorted(r["post_person_google"]) == sorted(r["pre_person_google"]))

    print("\n── the provisioner ──")
    p = provisioner()
    ck("absent: off, 7:00 and 17:00, 2h, 7 days",
       p.get("absent") == {"enabled": False, "times_local": ["07:00", "17:00"], "older_than_hours": 2, "lookback_days": 7})
    ck("enabled: true turns it on, times sorted", (p.get("on") or {}).get("enabled") is True
       and p["on"]["times_local"] == ["07:00", "17:00"])
    ck("refuses a bad time, an unknown key, 0 days", all(str(p.get(k, "")).startswith("DIE ") for k in ("time", "key", "days")),
       {k: p.get(k) for k in ("time", "key", "days")})
    ck("enabled: 'yes' (not true) stays off", (p.get("yes_string") or {}).get("enabled") is False)

    print(f"\n{sum(ok)}/{len(ok)} passed")
    return 0 if ok and all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
