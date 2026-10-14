#!/usr/bin/env python3
"""Verify the app-side broker end to end (app/broker.py + skills broker mode).

Two sandbox apps (docs/tasks/tasks_sandbox.sh) on private ports, both aimed at a
local fake Google (JARVIS_GOOGLE_API_ENDPOINT, honoured in mock mode only):
  A  writes OFF  (shadow/blocked posture)
  B  writes ON   (the sandbox's .example recipient boundary)

The skill side runs as a separate process with JARVIS_APP_BROKER_URL, no token
key, and an accounts/threads path it cannot open, so anything that works there
went through the broker. Canary: the same call with the broker URL unset fails.

Env: VERIFY_APP_ROOT, VERIFY_SKILLS_ROOT (default /root/jarvis-skills-platform).
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve()
APP = Path(os.environ.get("VERIFY_APP_ROOT") or HERE.parents[2])
SKILLS = Path(os.environ.get("VERIFY_SKILLS_ROOT") or "/root/jarvis-skills-platform")
PY = sys.executable
SANDBOX = APP / "docs/tasks/tasks_sandbox.sh"
FIXTURE_IDENTITY = Path(os.environ.get("VERIFY_FIXTURE_ROOT") or "/opt/jarvis-verify-fixture") / "skills/openclaw-imports/_shared/identity.json"
results = []


def ck(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))


class FakeGoogle(BaseHTTPRequestHandler):
    seen = []

    def _reply(self, code, body):
        out = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _record(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        FakeGoogle.seen.append({"method": self.command, "path": self.path.split("?")[0],
                                "auth": self.headers.get("Authorization", ""), "body": body})

    def do_GET(self):  # noqa: N802
        self._record()
        p = self.path.split("?")[0]
        if p.endswith("/gmail/v1/users/me/profile"):
            return self._reply(200, {"emailAddress": "assistant@fixture.example", "messagesTotal": 3,
                                     "threadsTotal": 2, "historyId": "100"})
        if p.endswith("/gmail/v1/users/me/messages"):
            return self._reply(200, {"messages": [{"id": "m1", "threadId": "t1"}], "resultSizeEstimate": 1})
        if p.endswith("/gmail/v1/users/me/messages/m1"):
            return self._reply(200, {"id": "m1", "threadId": "t1", "snippet": "fixture snippet", "payload": {
                "headers": [{"name": "From", "value": "a@fixture.example"}, {"name": "Subject", "value": "Hi"},
                            {"name": "Date", "value": "Tue, 13 Oct 2026 10:00:00 +0000"}]}})
        if "/calendars/" in p and p.endswith("/events/ev1"):
            return self._reply(200, {"id": "ev1", "summary": "Broker insert"})
        if p.endswith("/gmail/v1/users/me/messages/m404"):
            return self._reply(404, {"error": {"code": 404, "message": "Requested entity was not found.",
                                               "status": "NOT_FOUND"}})
        # api_endpoint replaces Calendar's whole base URL, servicePath included.
        if "/calendars/" in p and p.endswith("/events"):
            return self._reply(200, {"kind": "calendar#events", "items": [{"id": "e1", "summary": "Showing"}]})
        return self._reply(404, {"error": {"code": 404, "message": "unknown fake path " + p}})

    def do_POST(self):  # noqa: N802
        self._record()
        p = self.path.split("?")[0]
        if p.endswith("/gmail/v1/users/me/messages/send"):
            return self._reply(200, {"id": "sent1", "threadId": "t9", "labelIds": ["SENT"]})
        if "/calendars/" in p and p.endswith("/events"):
            return self._reply(200, {"id": "ev1", "summary": "Broker insert"})
        return self._reply(404, {"error": {"code": 404, "message": "unknown fake path " + p}})

    def log_message(self, *a):
        pass


def env_value(path: Path, key: str) -> str:
    for ln in path.read_text().splitlines():
        if ln.startswith(key + "="):
            return ln.split("=", 1)[1].strip()
    return ""


def http(port, path, payload=None, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/broker/{path}",
                                 data=None if payload is None else json.dumps(payload).encode(),
                                 method="GET" if payload is None else "POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def counter(db: Path, name: str) -> int:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = c.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()
    return int(row[0]) if row else 0


SKILL_PROBE = r"""
import json, os, sys
sys.path.insert(0, os.environ["SKILLS"] + "/_shared")
import google_creds as g, app_counters, app_channels
out = {}
def run(name, fn):
    try:
        out[name] = {"ok": fn()}
    except Exception as e:
        out[name] = {"raised": type(e).__name__, "status": getattr(getattr(e, "resp", None), "status", None),
                     "msg": str(e)[:200]}
run("source", g.source)
run("profile", lambda: g.gmail().users().getProfile(userId="me").execute())
run("list", lambda: g.gmail().users().messages().list(userId="me", q="from:x").execute())
run("missing", lambda: g.gmail().users().messages().get(userId="me", id="m404").execute())
run("events", lambda: g.calendar().events().list(calendarId="primary").execute())
run("credentials", lambda: g.credentials() and "returned")
run("gspread", lambda: g.gspread_client() and "returned")
import base64
raw = base64.urlsafe_b64encode(b"To: " + os.environ["TO"].encode() + b"\r\nSubject: s\r\n\r\nhi").decode()
run("send", lambda: g.gmail().users().messages().send(userId="me", body={"raw": raw}).execute())
run("bump", lambda: app_counters.bump("verify_broker_skill_bump") or "done")
run("channel1", lambda: app_channels.ensure_deal_channel("Verify Broker Deal"))
run("channel2", lambda: app_channels.ensure_deal_channel("verify broker deal"))
sys.path.insert(0, os.environ["APPROOT"])
from app import threads
run("app_threads_bump", lambda: threads.bump_counter("verify_broker_app_threads_bump"))
print(json.dumps(out, default=str))
"""

# The rewritten callers, invoked as the skills invoke them.
CALLERS_PROBE = r"""
import json, os, sys
S = os.environ["SKILLS"]
sys.path[:0] = [S + "/_shared", S + "/meeting-briefing", S + "/task-manager/scripts"]
import lib_google, create_calendar_event as cce
out = {}
def run(name, fn):
    try:
        out[name] = {"ok": fn()}
    except Exception as e:
        out[name] = {"raised": type(e).__name__, "msg": str(e)[:200]}
run("list_events", lambda: lib_google.list_events("primary", "2026-10-13T00:00:00Z", "2026-10-14T00:00:00Z"))
run("search", lambda: lib_google.search_messages("from:a", 5))
run("create_captured", lambda: list(cce.create_calendar_event("Broker capture", date="2026-10-14")))
run("insert", lambda: cce._calendar().events().insert(calendarId=cce.CALENDAR_ID,
    body={"summary": "Broker insert", "start": {"date": "2026-10-14"}, "end": {"date": "2026-10-14"}}).execute())
run("read_back", lambda: cce.read_back_event("ev1"))
print(json.dumps(out, default=str))
"""


def skill_probe(port, key, to, broker=True, probe=SKILL_PROBE, extra=None):
    tmp = Path(tempfile.mkdtemp(prefix="broker-skill-"))
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp), "HERMES_HOME": str(tmp), "SKILLS": str(SKILLS),
           "APPROOT": str(APP), "JARVIS_APP_ROOT": str(APP), "JARVIS_SKILLS_ROOT": str(SKILLS),
           "JARVIS_ACCOUNTS_DB": str(tmp / "no-such-dir" / "accounts.db"),
           "JARVIS_THREADS_DB": str(tmp / "no-such-dir" / "threads.db"),
           "API_SERVER_KEY": key, "TO": to, "PYTHONDONTWRITEBYTECODE": "1"}
    if broker:
        env["JARVIS_APP_BROKER_URL"] = f"http://127.0.0.1:{port}"
    env.update(extra or {})
    p = subprocess.run([PY, "-c", probe], env=env, capture_output=True, text=True, timeout=180)
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"_error": (p.stderr or p.stdout)[-800:]}


def start_sandbox(name, port, writes, endpoint):
    box = Path(f"/tmp/claude-0/-root/broker-sandbox-{name}")
    env = dict(os.environ, TASKS_SANDBOX=str(box), TASKS_PORT=str(port), VERIFY_ALLOW_WRITES=writes,
               VERIFY_GOOGLE_API_ENDPOINT=endpoint, VERIFY_APP_ROOT=str(APP), VERIFY_SKILLS_ROOT=str(SKILLS))
    p = subprocess.run(["bash", str(SANDBOX), "start"], env=env, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        sys.exit(f"sandbox {name} did not start: {p.stdout[-500:]} {p.stderr[-500:]}")
    seed = (f"import sys; sys.path.insert(0, {str(APP)!r}); from app import google_connection as gc\n"
            "gc.put_connected(google_email='assistant@fixture.example', scopes='https://www.googleapis.com/auth/gmail.modify',"
            " token_cipher=gc.encrypt_tokens({'refresh_token': 'verify-refresh', 'access_token': 'verify-access',"
            " 'expiry': '2099-01-01T00:00:00+00:00', 'scopes': 'https://www.googleapis.com/auth/gmail.modify'}),"
            " connected_by='owner')")
    s = subprocess.run([PY, "-c", seed], env=dict(os.environ, JARVIS_ACCOUNTS_DB=str(box / "accounts.db"),
                       JARVIS_THREADS_DB=str(box / "threads.db"), HERMES_HOME=str(box / "hermes"),
                       JARVIS_SKILLS_ROOT=str(SKILLS), APP_GOOGLE_TOKEN_KEY=(box / "token_key").read_text().strip()),
                       capture_output=True, text=True)
    if s.returncode != 0:
        sys.exit(f"seeding {name} failed: {s.stderr[-500:]}")
    return box, env


def stop_sandbox(env):
    subprocess.run(["bash", str(SANDBOX), "stop"], env=env, capture_output=True)


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), FakeGoogle)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{srv.server_address[1]}/"
    sandboxes = []
    try:
        box, env = start_sandbox("a", 8712, "0", endpoint)
        sandboxes.append(env)
        key = env_value(box / "hermes/.env", "API_SERVER_KEY")
        auth = {"Authorization": f"Bearer {key}"}
        tdb = box / "threads.db"
        print("── A (writes off): broker auth")
        ck("API_SERVER_KEY present in the sandbox", bool(key))
        ck("no key → 401", http(8712, "google/status")[0] == 401)
        ck("wrong key → 401", http(8712, "google/status", headers={"Authorization": "Bearer nope"})[0] == 401)
        ck("right key → 200", http(8712, "google/status", headers=auth)[0] == 200)
        ck("forwarded (tunnel) request refused even with the key → 403",
           http(8712, "google/status", headers={**auth, "CF-Connecting-IP": "203.0.113.9"})[0] == 403)
        r0 = counter(tdb, "broker_google_refused_method")
        code, body = http(8712, "google", {"api": "gmail", "version": "v1", "method": "users.settings.delegates.create",
                                            "params": {"userId": "me"}}, auth)
        ck("method outside the allowlist → 403", code == 403, str(body))
        ck("…counted (broker_google_refused_method +1)", counter(tdb, "broker_google_refused_method") - r0 == 1)

        print("── A: skills in broker mode (no token key, no store access)")
        FakeGoogle.seen.clear()
        w0 = counter(tdb, "broker_google_write_refused")
        b0, a0 = counter(tdb, "verify_broker_skill_bump"), counter(tdb, "verify_broker_app_threads_bump")
        out = skill_probe(8712, key, "someone@fixture.example")
        ck("probe ran", "_error" not in out, str(out.get("_error")))
        ck("source() = tenant_connection (asked the app)", out.get("source", {}).get("ok") == "tenant_connection",
           str(out.get("source")))
        ck("gmail getProfile returns Google's JSON", (out.get("profile", {}).get("ok") or {}).get("emailAddress")
           == "assistant@fixture.example", str(out.get("profile")))
        ck("gmail messages.list returns Google's JSON",
           (out.get("list", {}).get("ok") or {}).get("messages") == [{"id": "m1", "threadId": "t1"}])
        ck("Google 404 comes back as HttpError status 404",
           out.get("missing", {}).get("raised") == "HttpError" and out["missing"].get("status") == 404,
           str(out.get("missing")))
        ck("calendar events.list returns Google's JSON",
           (out.get("events", {}).get("ok") or {}).get("items") == [{"id": "e1", "summary": "Showing"}], str(out.get("events")))
        ck("credentials() refused: TokensAreAppSide", out.get("credentials", {}).get("raised") == "TokensAreAppSide",
           str(out.get("credentials")))
        ck("gspread_client() refused: TokensAreAppSide", out.get("gspread", {}).get("raised") == "TokensAreAppSide")
        ck("send with writes off raises WritesDisabled", out.get("send", {}).get("raised") == "WritesDisabled",
           str(out.get("send")))
        ck("…counted (broker_google_write_refused +1)", counter(tdb, "broker_google_write_refused") - w0 == 1)
        gets = [s for s in FakeGoogle.seen if s["method"] == "GET"]
        ck("fake Google saw the reads with the tenant's stored access token",
           len(gets) >= 4 and all(s["auth"] == "Bearer verify-access" for s in gets), str(gets)[:300])
        ck("fake Google saw no POST (the send never left the app)", not [s for s in FakeGoogle.seen if s["method"] == "POST"])
        ck("app_counters.bump landed in the app's store (+1)", counter(tdb, "verify_broker_skill_bump") - b0 == 1)
        ck("app.threads.bump_counter in broker mode landed in the app's store (+1)",
           counter(tdb, "verify_broker_app_threads_bump") - a0 == 1, str(out.get("app_threads_bump")))
        c1, c2 = out.get("channel1", {}).get("ok") or {}, out.get("channel2", {}).get("ok") or {}
        ck("ensure_deal_channel created a channel through the broker", bool(c1.get("thread_id") or c1.get("id")),
           str(out.get("channel1"))[:200])
        ck("…and is idempotent by name (same channel the second time)",
           c1 and (c1.get("thread_id") or c1.get("id")) == (c2.get("thread_id") or c2.get("id")))
        rows = sqlite3.connect(f"file:{tdb}?mode=ro", uri=True).execute(
            "SELECT COUNT(*) FROM threads WHERE lower(name)='verify broker deal'").fetchone()[0]
        ck("exactly one such channel row in the app's store", rows == 1, str(rows))

        print("── A: canary — the same probe without the broker URL cannot reach Google")
        FakeGoogle.seen.clear()
        out = skill_probe(8712, key, "someone@fixture.example", broker=False)
        ck("without the broker, getProfile fails", "raised" in out.get("profile", {}), str(out.get("profile")))
        ck("without the broker, fake Google saw nothing", not FakeGoogle.seen)

        print("── A: not connected → GoogleNotConnected through the broker")
        sqlite3.connect(box / "accounts.db").execute("DELETE FROM google_connection").connection.commit()
        out = skill_probe(8712, key, "someone@fixture.example")
        ck("source() = not_connected", out.get("source", {}).get("ok") == "not_connected", str(out.get("source")))
        ck("getProfile raises GoogleNotConnected", out.get("profile", {}).get("raised") == "GoogleNotConnected",
           str(out.get("profile")))

        box_b, env_b = start_sandbox("b", 8713, "1", endpoint)
        sandboxes.append(env_b)
        key_b = env_value(box_b / "hermes/.env", "API_SERVER_KEY")
        print("── B (writes on, .example boundary)")
        FakeGoogle.seen.clear()
        out = skill_probe(8713, key_b, "someone@fixture.example")
        ck("send inside the boundary reaches Google", (out.get("send", {}).get("ok") or {}).get("id") == "sent1",
           str(out.get("send")))
        posts = [s for s in FakeGoogle.seen if s["method"] == "POST"]
        ck("fake Google saw exactly one send", len(posts) == 1, str(len(posts)))
        FakeGoogle.seen.clear()
        rr0 = counter(box_b / "threads.db", "broker_google_recipient_refused")
        out = skill_probe(8713, key_b, "someone@gmail.com")
        ck("send outside the boundary refused", "raised" in out.get("send", {}), str(out.get("send")))
        ck("…counted (broker_google_recipient_refused +1)",
           counter(box_b / "threads.db", "broker_google_recipient_refused") - rr0 == 1)
        ck("…and fake Google saw no send", not [s for s in FakeGoogle.seen if s["method"] == "POST"])

        print("── B: the rewritten callers (meeting-briefing lib_google, task-manager calendar)")
        FakeGoogle.seen.clear()
        out = skill_probe(8713, key_b, "", probe=CALLERS_PROBE, extra={
            "JARVIS_ALLOW_WRITES": "1", "JARVIS_RUNTIME_CONFIG": str(box_b / "runtime.json"),
            "JARVIS_IDENTITY_CONFIG": str(FIXTURE_IDENTITY)})
        ck("callers probe ran", "_error" not in out, str(out.get("_error")))
        ev = out.get("list_events", {}).get("ok") or []
        ck("lib_google.list_events maps the broker's events", [e.get("id") for e in ev] == ["e1"],
           str(out.get("list_events"))[:200])
        ms = out.get("search", {}).get("ok") or []
        ck("lib_google.search_messages reads metadata through the broker",
           len(ms) == 1 and ms[0].get("subject") == "Hi" and ms[0].get("thread_id") == "t1",
           str(out.get("search"))[:200])
        cap = out.get("create_captured", {}).get("ok") or []
        ck("create_calendar_event on a boundary tenant is captured, not written",
           len(cap) == 2 and cap[1] is None, str(out.get("create_captured"))[:200])
        ck("_calendar().events().insert reaches Google through the broker",
           (out.get("insert", {}).get("ok") or {}).get("id") == "ev1", str(out.get("insert"))[:200])
        ck("read_back_event returns the event", (out.get("read_back", {}).get("ok") or {}).get("id") == "ev1",
           str(out.get("read_back"))[:200])
        cal_posts = [x for x in FakeGoogle.seen if x["method"] == "POST" and "/calendars/" in x["path"]]
        ck("fake Google saw exactly one calendar insert (the direct one, not the captured one)",
           len(cal_posts) == 1, str(len(cal_posts)))
        ck("every caller request carried the tenant's stored token",
           FakeGoogle.seen and all(x["auth"] == "Bearer verify-access" for x in FakeGoogle.seen))
    finally:
        for env in sandboxes:
            stop_sandbox(env)
        srv.shutdown()
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
