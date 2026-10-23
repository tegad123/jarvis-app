#!/usr/bin/env python3
"""Each person's own Google (chat), against the verify sandbox (:8711,
docs/tasks/tasks_sandbox.sh: mock OAuth, throwaway token key, fixture copies).

HTTP, through the running sandbox app:
- dana (owner) connects her own Google with the mock: person_google 0 -> 1 for
  dana only; the assistant mailbox row (google_connection) does not move.
- omar (member) may start a personal connect (302); his status stays
  disconnected while dana's is connected (presence twin); his status has no
  "assistant" key, dana's has one.
- a partial grant (1 of 2 permissions) stores nothing.
- a connect started by omar and finished in dana's session is refused and
  stores nothing for either.
- status JSON carries no token-ish keys or cipher blobs.
- omar's disconnect leaves dana's row; dana's disconnect removes only hers.

In process (a child importing the code under test against the sandbox's
accounts database, broker auth stubbed because loopback auth is not what is
under test here):
- the per-person broker endpoint answers for the person a running turn's
  reference names: dana connected, omar not, a made-up or ended reference
  refused (no_turn);
- a read for dana is built on dana's own credentials, never the assistant's;
  omar's read is 409 not_connected although dana is connected;
- a method outside the personal allowlist (gmail users.watch) is 403;
- an approved personal write runs with the person it was proposed for.

--canary breaks the person filter in the child (everyone gets dana's grant):
omar's read must then FAIL.

Never :8700. Never prints PINs or tokens.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

BASE = os.environ.get("AUTH_BASE", "http://127.0.0.1:8711").rstrip("/")
if BASE.endswith(":8700") or BASE.endswith(":8710"):
    sys.exit("REFUSING: live or testco port")
SB = pathlib.Path(os.environ.get("TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox"))
ACCOUNTS = pathlib.Path(os.environ.get("JARVIS_ACCOUNTS_DB", str(SB / "accounts.db")))
PINS = json.loads(pathlib.Path("/opt/jarvis-verify-fixture/SEEDED_PINS.json").read_text())
PINS = {k: (v.get("pin") if isinstance(v, dict) else v) for k, v in PINS.items()}
APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCR = pathlib.Path("/tmp/verify-person-google")
SCR.mkdir(parents=True, exist_ok=True)
TOKENISH = ("token", "refresh", "refresh_token", "access_token", "client_secret", "token_cipher", "cipher")

ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" ({extra})" if extra else ""))


def curl(path, jar=None, method="GET", body=None):
    out, hdr = SCR / "body", SCR / "hdr"
    out.unlink(missing_ok=True)
    hdr.unlink(missing_ok=True)
    cmd = ["curl", "-s", "-D", str(hdr), "-o", str(out), "-w", "%{http_code}", "-X", method, "--max-redirs", "0",
           path if path.startswith("http") else BASE + path]
    if jar:
        cmd += ["-b", jar, "-c", jar]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    code = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    text = out.read_text() if out.exists() else ""
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        data = text
    return int(code or 0), data, (hdr.read_text() if hdr.exists() else "")


def location(heads):
    for line in heads.splitlines():
        if line.lower().startswith("location:"):
            loc = line.split(":", 1)[1].strip()
            return BASE + loc if loc.startswith("/") else loc
    return ""


def rows():
    """{table: {key: (status, email)}} from a WAL-inclusive snapshot. Never cipher."""
    dst = SCR / "accounts-snap.db"
    src = sqlite3.connect(str(ACCOUNTS))
    d = sqlite3.connect(str(dst))
    src.backup(d)
    src.close()
    d.close()
    con = sqlite3.connect(str(dst))
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out = {"person_google": {}, "google_connection": {}}
    if "person_google" in tables:
        out["person_google"] = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
            "SELECT person_key, status, google_email, connected_at FROM person_google")}
    if "google_connection" in tables:
        out["google_connection"] = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT id, status, google_email FROM google_connection")}
    con.close()
    return out


def login(who):
    jar = str(SCR / f"{who}.jar")
    pathlib.Path(jar).unlink(missing_ok=True)
    code, d, _ = curl("/api/login", jar, "POST", {"person_key": who, "pin": PINS[who]})
    ck(f"{who} signs in (HTTP {code}, role {d.get('role') if isinstance(d, dict) else None})", code == 200)
    return jar


def start(jar):
    code, _, heads = curl("/api/google/connect/start", jar)
    return code, location(heads)


def authorize(loc, jar, email, grant="full"):
    parsed = urlparse(loc)
    q = parse_qs(parsed.query)
    q["email"], q["grant"] = [email], [grant]
    code, _, heads = curl(urlunparse(parsed._replace(query=urlencode(q, doseq=True))), jar)
    return location(heads)


def swap_session(state_jar, session_jar, dst):
    """state_jar's OAuth state cookie with session_jar's jarvis_session."""
    def lines(p, keep_session):
        out = []
        for line in pathlib.Path(p).read_text().splitlines():
            raw = line.lstrip()
            raw = raw[len("#HttpOnly_"):] if raw.startswith("#HttpOnly_") else raw
            parts = raw.split("\t")
            if len(parts) >= 6 and (parts[5] == "jarvis_session") == keep_session:
                out.append(line)
        return out
    pathlib.Path(dst).write_text("\n".join(lines(state_jar, False) + lines(session_jar, True)) + "\n")


def tokenish(obj):
    if isinstance(obj, dict):
        return [k for k in obj if str(k).lower() in TOKENISH or str(k).lower().endswith("_token")] + \
            [x for v in obj.values() for x in tokenish(v)]
    return []


CHILD = r'''
import json, os, sys
sys.path.insert(0, os.environ["APP_ROOT"])
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import actor_refs, broker, google_connection
broker._authorize = lambda request: None
seen = []
calls = []
class _Svc:
    def __init__(self, creds, path=()): self.creds, self.path = creds, path
    def __getattr__(self, name): return lambda *a, **k: _Svc(self.creds, self.path + (name,))
    def execute(self):
        calls.append([".".join(self.path), getattr(self.creds, "token", None)])
        return {"items": [], "id": "fake-1"}
def fake_build(api, version, credentials=None, **kw):
    seen.append(getattr(credentials, "token", None))
    return _Svc(credentials)
import googleapiclient.discovery as disc
disc.build = fake_build
if os.environ.get("CANARY") == "1":
    real = google_connection.person_credentials
    google_connection.person_credentials = lambda person_key: real("dana")
app = FastAPI(); app.include_router(broker.router)
c = TestClient(app)
rd = actor_refs.mint("dana", "t-dana", "owner")
ro = actor_refs.mint("omar", "t-omar", "member")
out = {}
out["status_dana"] = c.post("/api/broker/google/person/status", json={"ref": rd}).json()
out["status_omar"] = c.post("/api/broker/google/person/status", json={"ref": ro}).json()
out["status_bogus"] = c.post("/api/broker/google/person/status", json={"ref": "app:" + "x" * 32}).json()
r = c.post("/api/broker/google/person", json={"ref": rd, "api": "calendar", "version": "v3",
           "method": "events.list", "params": {"calendarId": "primary"}})
dana_token = google_connection.person_credentials("dana").token
out["read_dana"] = [r.status_code, bool(seen) and seen[-1] == dana_token, (seen[-1] or "")[:12] if seen else None]
r = c.post("/api/broker/google/person", json={"ref": ro, "api": "calendar", "version": "v3",
           "method": "events.list", "params": {"calendarId": "primary"}})
out["read_omar"] = [r.status_code, (r.json() or {}).get("error")]
r = c.post("/api/broker/google/person", json={"ref": rd, "api": "gmail", "version": "v1",
           "method": "users.watch", "params": {"userId": "me"}})
out["watch"] = r.status_code
import base64
from email.message import EmailMessage
from app import approvals, platform_store, sentinel
def raw_mail(to, subject, text):
    m = EmailMessage(); m["To"], m["Subject"] = to, subject; m.set_content(text)
    return base64.urlsafe_b64encode(m.as_bytes()).decode("ascii")
def post(ref, api, version, method, params):
    r = c.post("/api/broker/google/person", json={"ref": ref, "api": api, "version": version, "method": method,
                                                  "params": params})
    return r.status_code, r.json()
def sends():
    return [x for x in calls if x[0] == "users.messages.send"]
pending0 = approvals.query(status="pending")["count"]
code, body = post(rd, "gmail", "v1", "users.messages.send",
                  {"userId": "me", "body": {"raw": raw_mail("client@example.com", "Running late", "Hi Jane,\nTen minutes late.")}})
aid = body.get("approval_id")
out["send_filed"] = [code, body.get("error"), bool(aid), len(sends())]
shown = (approvals.get(aid) or {}).get("display") or {} if aid else {}
out["send_display"] = {k: shown.get(k) for k in ("to", "subject", "message", "from_account")}
code, body = post(rd, "calendar", "v3", "events.insert",
                  {"calendarId": "primary", "body": {"summary": "Meeting with Jane", "start": {"dateTime": "2026-10-30T14:00:00-05:00"},
                                                      "end": {"dateTime": "2026-10-30T15:00:00-05:00"}}})
cal_aid = body.get("approval_id")
out["cal_filed"] = [code, body.get("error"), bool(cal_aid),
                    len([x for x in calls if x[0] == "events.insert"]), sentinel.DEFAULTS[sentinel.INTERNAL_WRITE]]
code, body = post(ro, "gmail", "v1", "users.messages.send",
                  {"userId": "me", "body": {"raw": raw_mail("client@example.com", "x", "y")}})
out["member_send"] = [code, body.get("error"), approvals.query(status="pending")["count"] - pending0]
out["scopes_offered"] = sorted(((approvals.query(status="pending", person="dana")["approvals"] or [{}])[0]
                                .get("callbacks") or {}).get("approve", {}).keys())
approvals.decide(aid, "approved", "dana")
out["after_approve"] = [len(sends()), [t == dana_token for _n, t in sends()]]
try:
    approvals.decide(aid, "approved", "dana"); out["reapprove"] = "ran again"
except ValueError as exc:
    out["reapprove"] = "refused"
approvals.decide(cal_aid, "rejected", "dana")
out["after_reject"] = len([x for x in calls if x[0] == "events.insert"])
code, body = post(rd, "gmail", "v1", "users.messages.send",
                  {"userId": "me", "body": {"raw": raw_mail("client@example.com", "Again", "z")}})
aid2 = body.get("approval_id")
before = len(sends())
res = broker.execute_approved(platform_store.get_approval(aid2), "always", "dana")
out["wide_scope"] = [res.get("grant_id"), len(sends()) - before,
                     len(platform_store.active_grants(broker.OWNER, "gmail", ""))]
captured = {}
broker.gate_google = lambda *a, **k: (captured.update(k) or (200, {"action_id": 1}))
broker.switches.on = lambda name: True
item = {"id": 1, "action_json": {"type": "google", "api": "calendar", "version": "v3", "method": "events.insert",
                                 "params": {}, "person": "dana"}}
broker.execute_approved(item, "once", "dana")
out["approved_person"] = captured.get("person")
item["action_json"].pop("person")
broker.execute_approved(item, "once", "dana")
out["approved_tenant"] = captured.get("person")
actor_refs.end(rd)
out["status_ended"] = c.post("/api/broker/google/person/status", json={"ref": rd}).json()
print("RESULT " + json.dumps(out))
'''


def child() -> dict:
    skills = os.environ.get("VERIFY_SKILLS_ROOT", "/opt/jarvis-verify-fixture/skills/openclaw-imports")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC"))}
    env.update({
        "APP_ROOT": str(APP_ROOT), "APP_GOOGLE_OAUTH_MOCK": "1",
        "APP_GOOGLE_TOKEN_KEY": (SB / "token_key").read_text().strip(),
        "HERMES_HOME": str(SB / "hermes"), "JARVIS_SKILLS_ROOT": skills, "JARVIS_APP_ROOT": str(APP_ROOT),
        "JARVIS_RUNTIME_CONFIG": str(SB / "runtime.json"),
        "JARVIS_IDENTITY_CONFIG": "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json",
        "JARVIS_ACCOUNTS_DB": str(ACCOUNTS), "JARVIS_THREADS_DB": str(SB / "threads.db"),
        "JARVIS_TASKS_DB": str(SB / "tasks.db"), "JARVIS_LEADS_DB": str(SB / "leads.db"),
        "JARVIS_ACTIVITY_FEED": str(SB / "feed.jsonl"), "JARVIS_PG_DSN": (SB / "pg_dsn").read_text().strip(),
        "JARVIS_SWITCH_BROKER": "on", "JARVIS_SWITCH_SENTINEL": "on",
        "JARVIS_SENTINEL_MODE": "enforce", "JARVIS_ALLOW_WRITES": "1",
    })
    if "--canary" in sys.argv:
        env["CANARY"] = "1"
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(APP_ROOT),
                       capture_output=True, text=True, timeout=120)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(p.stderr[-1500:])
        return {}
    return json.loads(line[-1][7:])


def main() -> int:
    r0 = rows()
    for k in list(r0["person_google"]):
        print(f"  note: person_google already holds {k}; the sandbox should start clean")
    dana, omar = login("dana"), login("omar")

    code, st, _ = curl("/api/google/connection", omar)
    ck(f"omar's status is his own and disconnected, with no assistant mailbox (HTTP {code})",
       code == 200 and st.get("status") == "disconnected" and "assistant" not in st)
    code, st, _ = curl("/api/google/connection", dana)
    ck(f"dana (owner) also sees the assistant mailbox status (HTTP {code})", code == 200 and "assistant" in st)

    code, loc = start(dana)
    ck(f"dana starts a personal connect (HTTP {code})", code == 302 and "purpose=connect_person" in loc)
    cb = authorize(loc, dana, "dana.own@example.com")
    code, _, _ = curl(cb, dana)
    r1 = rows()
    ck(f"dana's own Google is stored for dana only (callback HTTP {code}; person_google {sorted(r1['person_google'])})",
       code == 302 and r1["person_google"].get("dana", (None, None))[:2] == ("connected", "dana.own@example.com")
       and "omar" not in r1["person_google"])
    ck(f"   the assistant mailbox did not move ({r0['google_connection']} -> {r1['google_connection']})",
       r1["google_connection"] == r0["google_connection"])

    code, st, _ = curl("/api/google/connection", dana)
    ck(f"dana's status: connected as dana.own@example.com (presence twin)",
       code == 200 and st.get("status") == "connected" and st.get("google_email") == "dana.own@example.com")
    bad = tokenish(st)
    ck(f"   status JSON has no token-ish keys ({bad or 'none'})", not bad and "gAAAAA" not in json.dumps(st))
    code, st, _ = curl("/api/google/connection", omar)
    ck(f"omar still sees only his own: disconnected (HTTP {code}, {st.get('status')})",
       code == 200 and st.get("status") == "disconnected" and st.get("google_email") is None)

    code, loc = start(omar)
    ck(f"omar (member) may start his own connect (HTTP {code})", code == 302)
    cb = authorize(loc, omar, "omar.own@example.com", grant="partial")
    code, body, _ = curl(cb, omar)
    ck(f"a partial grant stores nothing (HTTP {code})", code == 403 and "omar" not in rows()["person_google"])

    code, loc = start(omar)
    cb = authorize(loc, omar, "omar.own@example.com")
    mixed = str(SCR / "mixed.jar")
    swap_session(omar, dana, mixed)
    before = rows()["person_google"].get("dana")
    code, _, _ = curl(cb, mixed)
    r2 = rows()["person_google"]
    ck(f"a connect started by omar and finished in dana's session is refused (HTTP {code})",
       code == 403 and "omar" not in r2 and r2.get("dana") == before)

    res = child()
    print(f"  in process: {json.dumps(res)}")
    ck("the broker answers for the person the turn's reference names (dana connected, omar not)",
       res.get("status_dana", {}).get("status") == "connected"
       and res.get("status_omar", {}).get("status") == "disconnected")
    ck("   a made-up reference gets nothing (no_turn)", res.get("status_bogus", {}).get("why") == "no_turn")
    ck("dana's read is built on dana's own credentials",
       (res.get("read_dana") or [0, False])[0] == 200 and res["read_dana"][1] is True
       and str(res["read_dana"][2]).startswith("mock-access"))
    ck("omar's read is refused not_connected although dana is connected",
       res.get("read_omar") == [409, "not_connected"])
    ck(f"a method outside the personal allowlist is refused (users.watch HTTP {res.get('watch')})",
       res.get("watch") == 403)
    ck(f"an approved personal write runs for its person ({res.get('approved_person')}), "
       f"a tenant one for the mailbox ({res.get('approved_tenant')})",
       res.get("approved_person") == "dana" and res.get("approved_tenant") is None)
    ck("   an ended turn's reference gets nothing", res.get("status_ended", {}).get("why") == "no_turn")

    print("\n── live mode: every personal write waits for the owner's tap ──")
    ck(f"an email from dana's Gmail files an approval and sends nothing ({res.get('send_filed')})",
       res.get("send_filed") == [403, "approval_required", True, 0])
    d = res.get("send_display") or {}
    ck(f"   the Approvals card reads as an email: to, subject, the whole message, from her own Google ({d})",
       d.get("to") == "client@example.com" and d.get("subject") == "Running late"
       and "Ten minutes late." in (d.get("message") or "") and d.get("from_account") == "your own Google")
    ck(f"a calendar event with no guests also waits, although the default for it is auto ({res.get('cal_filed')})",
       res.get("cal_filed") == [403, "approval_required", True, 0, "auto"])
    ck(f"a member's personal email is refused, and files nothing ({res.get('member_send')})",
       res.get("member_send") == [403, "owner_only", 2])
    ck(f"   the card offers approve-once only, never a standing grant ({res.get('scopes_offered')})",
       res.get("scopes_offered") == ["once"])
    ck(f"approving sends exactly one email, on dana's own credentials ({res.get('after_approve')})",
       res.get("after_approve") == [1, [True]])
    ck(f"   approving it again is refused ({res.get('reapprove')})", res.get("reapprove") == "refused")
    ck(f"a rejected event is never added ({res.get('after_reject')} inserts)", res.get("after_reject") == 0)
    ck(f"a wider scope asked for a personal action runs it once and makes no grant ({res.get('wide_scope')})",
       res.get("wide_scope") == [None, 1, 0])

    code, _, _ = curl("/api/google/connection/disconnect", omar, "POST")
    ck(f"omar's disconnect leaves dana's row (HTTP {code})", code == 200 and "dana" in rows()["person_google"])
    code, _, _ = curl("/api/google/connection/disconnect", dana, "POST")
    r3 = rows()
    ck(f"dana's disconnect removes hers only (HTTP {code}; person_google {sorted(r3['person_google'])})",
       code == 200 and "dana" not in r3["person_google"] and r3["google_connection"] == r0["google_connection"])
    print(f"\n{sum(ok)}/{len(ok)} passed")
    return 0 if ok and all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
