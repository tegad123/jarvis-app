#!/usr/bin/env python3
"""Each person's own web browser, end to end, without a tenant.

Starts the real egress proxy and browser worker (test mode: no systemd, no
private network; JARVIS_EGRESS_TEST_ALLOW lets it reach two local test sites,
which count as two different sites), then drives the
real gate in a child process against the verify sandbox's databases
(docs/tasks/tasks_sandbox.sh, started first). dana is the owner, omar a
member, priya an admin. The two sites are fresh loopback addresses each run.

Proves:
- every request names a person, and their pages, cookies and saved logins are
  theirs alone (dana's cookie never reaches omar);
- reading is free; acting needs that person's own 30-minute approval for that
  site: omar's approval does not let dana act, and does not cover site B;
- only omar can approve omar's browser approval: the owner can see it and
  reject it but not approve it, an admin cannot see it at all, and omar cannot
  see the tenant's own approvals (presence twin: the owner can);
- "Place order" is always asked about, approval or not;
- after reading a page, opening another site with data in the address is asked
  about (presence twin: the same site without data opens);
- a saved password reaches the site (presence twin) but never the reply, the
  ledger rows, the worker log or the egress log;
- egress refuses a private address (presence twin: the test sites load);
- while omar has taken over, the agent cannot use omar's browser; dana's still
  works; an operator session gets no browser;
- ledger delta: browser action rows for omar and dana, structure only.

--canary gives everyone dana's browser: the isolation checks must then FAIL.

Never :8700 or :8710. Prints no passwords (the test password is generated
here and only ever searched for).
"""
import http.server
import json
import os
import pathlib
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse

APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
SB = pathlib.Path(os.environ.get("TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox"))
SCR = pathlib.Path("/tmp/verify-browser")
BIN = os.environ.get("JARVIS_BROWSER_BIN", "/opt/jarvis/browser/chrome-headless-shell-1234/chrome-headless-shell")
if not os.access(BIN, os.X_OK):
    BIN = "/root/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell"
PASSWORD = "Pw-" + secrets.token_urlsafe(12)
EXFIL = "leak" + secrets.token_hex(6)
RECEIVED = []

ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" ({extra})" if extra else ""))


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def page(self, title, body, headers=()):
        data = f"<html><head><title>{title}</title></head><body>{body}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/set/"):
            who = path.split("/")[2]
            self.page("set", f"<p>set for {who}</p>", [("Set-Cookie", f"who={who}; Max-Age=3600; Path=/")])
        elif path == "/shop":
            self.page("Test shop", "<h1>Test shop</h1><button onclick=\"document.title='noted'\">Add note</button>"
                                   "<button>Place order</button>")
        elif path == "/login":
            self.page("Sign in", "<form method=post action=/login><label>Email <input name=username></label>"
                                 "<label>Password <input type=password name=password></label>"
                                 "<button type=submit>Sign in</button></form>")
        else:
            self.page("get", f"<p>cookie is {self.headers.get('Cookie') or 'none'}</p>")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode())
        RECEIVED.append((form.get("username") or [""])[0])
        RECEIVED.append((form.get("password") or [""])[0])
        self.page("Welcome", "<p>Welcome back</p>")


CHILD = r'''
import json, os, re, sys
sys.path.insert(0, os.environ["APP_ROOT"])
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import actor_refs, approvals, auth, broker, browser, platform_store as store
broker._authorize = lambda request: None
A, B, PW, EXFIL = os.environ["SITE_A"], os.environ["SITE_B"], os.environ["PW"], os.environ["EXFIL"]
if os.environ.get("CANARY") == "1":
    real = browser.call
    browser.call = lambda op, person, timeout=100, **kw: real(op, "dana", timeout, **kw)
app = FastAPI(); app.include_router(browser.broker_router); app.include_router(browser.router)
SESS = {}
app.dependency_overrides[auth.current_session] = lambda: SESS
c = TestClient(app)
refs = {"dana": actor_refs.mint("dana", "t-dana", "owner"), "omar": actor_refs.mint("omar", "t-omar", "member")}
out = {}
def go(who, op, **kw):
    r = c.post("/api/broker/browser", json={"ref": refs[who], "op": op, **kw},
               headers={"x-jarvis-run": f"verify-browser-{who}-{os.getpid()}"})
    return r.status_code, r.json()
def ref(snapshot, name):
    m = re.search(r"\[(e\d+)\] \w+ \"" + re.escape(name) + "\"", snapshot or "")
    return m.group(1) if m else None
def as_person(key, role):
    SESS.clear(); SESS.update({"person_key": key, "role": role})
def browser_rows():
    return [r for r in store.recent_actions(500) if r.get("connector") == "browser"]
rows0 = {r["id"] for r in browser_rows()}

out["bad_ref"] = c.post("/api/broker/browser", json={"ref": "app:" + "x" * 32, "op": "read"}).status_code
code, r = go("dana", "open", url=f"{A}/set/dana")
out["dana_set"] = [code, r.get("ok")]
code, r = go("omar", "open", url=f"{A}/get")
out["omar_cookie"] = [code, (r.get("snapshot") or "")[-80:]]
code, r = go("dana", "open", url=f"{A}/get")
out["dana_cookie"] = [code, (r.get("snapshot") or "")[-80:]]

code, r = go("omar", "open", url=f"{A}/shop")
note, order = ref(r.get("snapshot"), "Add note"), ref(r.get("snapshot"), "Place order")
out["omar_shop"] = [code, bool(note), bool(order)]
out["omar_click_no_grant"] = list(go("omar", "click", element=note))
code, r = go("omar", "request_access", task="Add a note on the test shop", site=A)
aid = r.get("approval_id")
out["request"] = [code, r.get("error"), bool(aid)]
row = store.get_approval(aid) if aid else {}
out["filed_for"] = (row or {}).get("user_id")
tenant_aid = store.create_approval(origin="producer", user_id="dana", kind="action", skill="verify-browser",
                                   summary="verify-browser: a tenant approval", action={"action": "noop"},
                                   reason="verify")
def seen(person, role):
    return {i["id"]: i for i in approvals.query(status="pending", person=person, role=role)["approvals"]}
q = seen("dana", "owner")
out["owner_view"] = [aid in q, (q.get(aid) or {}).get("can_approve"), (q.get(aid) or {}).get("can_reject"),
                     tenant_aid in q]
q = seen("omar", "member")
out["member_view"] = [aid in q, (q.get(aid) or {}).get("can_approve"), tenant_aid in q,
                      all(i.get("for_person") == "omar" for i in q.values())]
q = seen("priya", "admin")
out["admin_view"] = [aid in q, tenant_aid in q]
def decide(who, role, decision):
    try:
        approvals.decide(aid, decision, who, scope=approvals.default_scope(aid), role=role)
        return "decided"
    except PermissionError:
        return "refused"
    except LookupError:
        return "not_found"
out["owner_approve"] = decide("dana", "owner", "approved")
out["admin_reject"] = decide("priya", "admin", "rejected")
out["omar_approve"] = decide("omar", "member", "approved")
approvals.decide(tenant_aid, "rejected", "dana")
code, r = go("omar", "click", element=note)
out["omar_click_granted"] = [code, r.get("ok"), r.get("error")]
code, r = go("dana", "open", url=f"{A}/shop")
out["dana_click_on_omars_grant"] = list(go("dana", "click", element=ref(r.get("snapshot"), "Add note")))
code, r = go("omar", "open", url=f"{A}/shop")
code, r = go("omar", "click", element=ref(r.get("snapshot"), "Place order"))
out["place_order"] = [code, r.get("error"), (store.get_approval(r.get("approval_id") or "") or {}).get("action_class")]

code, r = go("omar", "open", url=f"{B}/shop")
out["site_b_open"] = [code, r.get("ok")]
out["site_b_click"] = list(go("omar", "click", element=ref(r.get("snapshot"), "Add note")))
code, r = go("omar", "open", url=f"{A}/get?d={EXFIL}")
out["exfil_same_site"] = [code, r.get("error")]
code, r = go("omar", "open", url=f"{B}/get?d={EXFIL}")
out["exfil_new_site"] = [code, r.get("error")]

before = len(browser.list_logins("omar"))
browser.save_login("omar", browser.site_key(A), "omar@example.com", PW)
sites = lambda who: [x["site"] for x in browser.list_logins(who)]
out["logins"] = [browser.site_key(A) in sites("omar"), browser.site_key(A) in sites("dana"),
                 len(browser.list_logins("omar")) - before]
go("omar", "open", url=f"{A}/login")
code, r = go("omar", "login")
replies = [json.dumps(r)]
out["login"] = [code, r.get("ok"), r.get("error"), r.get("title")]
as_person("omar", "member")
replies.append(json.dumps(c.get("/api/browser/logins").json()))

c.post("/api/browser/takeover", json={"on": True})
out["agent_while_taken_over"] = go("omar", "read")[0]
out["dana_while_omar_taken_over"] = go("dana", "read")[0]
c.post("/api/browser/takeover", json={"on": False})
out["agent_after_hand_back"] = go("omar", "read")[0]
as_person(None, "operator")
out["operator"] = [c.get("/api/browser/status").status_code, c.get("/api/browser/logins").status_code,
                   c.post("/api/browser/takeover", json={"on": True}).status_code]

new = [r for r in browser_rows() if r["id"] not in rows0]
dump = json.dumps(new, default=str)
out["rows"] = {"new": len(new), "omar": sum(1 for r in new if r.get("user_id") == "omar"),
               "dana": sum(1 for r in new if r.get("user_id") == "dana"),
               "password_in_rows": PW in dump, "exfil_in_rows": EXFIL in dump,
               "password_in_replies": any(PW in x for x in replies)}
print("RESULT " + json.dumps(out))
'''


def wait_for(path, seconds=10):
    end = time.time() + seconds
    while time.time() < end and not os.path.exists(path):
        time.sleep(0.1)
    return os.path.exists(path)


def worker_call(req):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(60)
    s.connect(str(SCR / "worker.sock"))
    s.sendall(json.dumps(req).encode() + b"\n")
    return json.loads(s.makefile().readline() or "{}")


def main() -> int:
    for k in ("AUTH_BASE", "APP_PORT"):
        v = os.environ.get(k, "")
        if v.endswith(("8700", "8710")):
            sys.exit("REFUSING: live or testco port")
    canary = "--canary" in sys.argv
    SCR.mkdir(parents=True, exist_ok=True)
    for f in ("egress.sock", "worker.sock"):
        (SCR / f).unlink(missing_ok=True)
    subprocess.run(["rm", "-rf", str(SCR / "profiles")], check=True)
    # Fresh addresses every run: an approval lasts 30 minutes and is per site,
    # so a rerun on the same sites would find the last run's approval.
    third = secrets.randbelow(250) + 3
    ip_a, ip_b = f"127.{third}.0.1", f"127.{third}.0.2"
    sa = http.server.ThreadingHTTPServer((ip_a, 0), Site)
    port = sa.server_address[1]
    sb = http.server.ThreadingHTTPServer((ip_b, port), Site)
    for s in (sa, sb):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    site_a, site_b = f"http://{ip_a}:{port}", f"http://{ip_b}:{port}"
    base = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC"))}
    eg = subprocess.Popen([sys.executable, "-I", str(APP_ROOT / "app" / "browser_egress.py")],
                          env={**base, "JARVIS_EGRESS_LISTEN": str(SCR / "egress.sock"),
                               "JARVIS_EGRESS_TEST_ALLOW": f"{ip_a}:{port},{ip_b}:{port}"},
                          stdout=open(SCR / "egress.log", "w"), stderr=subprocess.STDOUT)
    wait_for(SCR / "egress.sock")
    wk = subprocess.Popen([sys.executable, "-I", str(APP_ROOT / "app" / "browser_worker.py")],
                          env={**base, "JARVIS_BROWSER_BIN": BIN, "JARVIS_BROWSER_PROFILE": str(SCR / "profiles"),
                               "JARVIS_EGRESS_SOCKET": str(SCR / "egress.sock"),
                               "JARVIS_BROWSER_LISTEN": str(SCR / "worker.sock"), "JARVIS_BROWSER_TEST": "1",
                               "JARVIS_BROWSER_MAX_OPEN": "2"},
                          stdout=open(SCR / "worker.log", "w"), stderr=subprocess.STDOUT)
    wait_for(SCR / "worker.sock")
    try:
        print("egress (real proxy, real Chromium):")
        r = worker_call({"op": "open", "person": "omar", "url": "http://10.0.0.1/"})
        ck("a private address is blocked", bool(r.get("blocked")) or r.get("ok") is False, str(r.get("blocked"))[:80])
        r = worker_call({"op": "open", "person": "omar", "url": f"{site_a}/get"})
        ck("presence twin: the test site loads through the same proxy", r.get("ok") and not r.get("blocked"))
        ck("a request without a person is refused", worker_call({"op": "status"}).get("ok") is False)

        env = dict(base)
        env.update({
            "APP_ROOT": str(APP_ROOT), "APP_GOOGLE_TOKEN_KEY": (SB / "token_key").read_text().strip(),
            "HERMES_HOME": str(SB / "hermes"), "JARVIS_APP_ROOT": str(APP_ROOT),
            "JARVIS_SKILLS_ROOT": os.environ.get("VERIFY_SKILLS_ROOT",
                                                 "/opt/jarvis-verify-fixture/skills/openclaw-imports"),
            "JARVIS_RUNTIME_CONFIG": str(SB / "runtime.json"),
            "JARVIS_IDENTITY_CONFIG": "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json",
            "JARVIS_ACCOUNTS_DB": str(SB / "accounts.db"), "JARVIS_THREADS_DB": str(SB / "threads.db"),
            "JARVIS_TASKS_DB": str(SB / "tasks.db"), "JARVIS_LEADS_DB": str(SB / "leads.db"),
            "JARVIS_ACTIVITY_FEED": str(SB / "feed.jsonl"), "JARVIS_PG_DSN": (SB / "pg_dsn").read_text().strip(),
            "JARVIS_SWITCH_BROKER": "on", "JARVIS_SWITCH_SENTINEL": "on", "JARVIS_SENTINEL_MODE": "enforce",
            "JARVIS_ALLOW_WRITES": "1", "JARVIS_BROWSER": "on", "JARVIS_BROWSER_SOCKET": str(SCR / "worker.sock"),
            "SITE_A": site_a, "SITE_B": site_b, "PW": PASSWORD, "EXFIL": EXFIL,
        })
        if canary:
            env["CANARY"] = "1"
        p = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(APP_ROOT), capture_output=True,
                           text=True, timeout=600)
        line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
        if not line:
            print(p.stderr[-2500:])
            ck("child ran", False)
            return 1
        o = json.loads(line[-1][7:])
    finally:
        wk.terminate()
        eg.terminate()
        wk.wait(15)
        eg.wait(15)
        sa.shutdown()
        sb.shutdown()

    print("each person's own browser:")
    ck("an unknown chat turn gets no browser", o["bad_ref"] == 403, o["bad_ref"])
    ck("omar does not see dana's cookie", "who=dana" not in o["omar_cookie"][1], o["omar_cookie"][1])
    ck("presence twin: dana sees her own cookie", "who=dana" in o["dana_cookie"][1], o["dana_cookie"][1])
    print("approvals are the person's own:")
    ck("acting with no approval is held", o["omar_click_no_grant"][0] == 403
       and o["omar_click_no_grant"][1].get("error") == "needs_task_approval", o["omar_click_no_grant"])
    ck("the request is filed for omar", o["request"][2] and o["filed_for"] == "omar", o["filed_for"])
    ck("the owner sees it, may reject but not approve it", o["owner_view"][:3] == [True, False, True], o["owner_view"])
    ck("presence twin: the owner sees the tenant's own approval", o["owner_view"][3] is True)
    ck("omar sees his own, may approve it, and not the tenant's", o["member_view"] == [True, True, False, True],
       o["member_view"])
    ck("an admin sees neither", o["admin_view"] == [False, False], o["admin_view"])
    ck("the owner's approve is refused", o["owner_approve"] == "refused", o["owner_approve"])
    ck("the admin's reject finds nothing", o["admin_reject"] == "not_found", o["admin_reject"])
    ck("omar's approve is accepted", o["omar_approve"] == "decided", o["omar_approve"])
    ck("omar can now act on that site", o["omar_click_granted"][:2] == [200, True], o["omar_click_granted"])
    ck("omar's approval does not let dana act", o["dana_click_on_omars_grant"][0] == 403
       and o["dana_click_on_omars_grant"][1].get("error") == "needs_task_approval", o["dana_click_on_omars_grant"])
    ck("nor does it cover another site", o["site_b_open"][0] == 200 and o["site_b_click"][0] == 403
       and o["site_b_click"][1].get("error") == "needs_task_approval", o["site_b_click"])
    ck("'Place order' is asked about despite the approval", o["place_order"][:2] == [403, "approval_required"]
       and o["place_order"][2] == "locked", o["place_order"])
    ck("after reading, data in a new site's address is asked about", o["exfil_new_site"] == [403, "approval_required"],
       o["exfil_new_site"])
    ck("presence twin: the same address on the approved site opens", o["exfil_same_site"][0] == 200,
       o["exfil_same_site"])
    print("saved logins:")
    ck("the login is omar's alone (+1 for omar, none for dana)", o["logins"] == [True, False, 1], o["logins"])
    ck("signing in worked", o["login"][:2] == [200, True] and o["login"][3] == "Welcome", o["login"])
    ck("presence twin: the site received the saved password", PASSWORD in RECEIVED)
    ck("the password is not in any reply", not o["rows"]["password_in_replies"])
    ck("the password is not in the ledger rows", not o["rows"]["password_in_rows"])
    logs = (SCR / "worker.log").read_text() + (SCR / "egress.log").read_text()
    ck("the password is not in the worker or egress log", PASSWORD not in logs)
    ck("the exfil data is not in the ledger rows", not o["rows"]["exfil_in_rows"])
    print("takeover and the operator:")
    ck("while omar has taken over, the agent can't use his browser", o["agent_while_taken_over"] == 409,
       o["agent_while_taken_over"])
    ck("dana's browser still works meanwhile", o["dana_while_omar_taken_over"] == 200, o["dana_while_omar_taken_over"])
    ck("after handing back, the agent can again", o["agent_after_hand_back"] == 200, o["agent_after_hand_back"])
    ck("an operator session gets no browser", all(x == 403 for x in o["operator"]), o["operator"])
    print("ledger delta:")
    egress = [json.loads(ln) for ln in (SCR / "egress.log").read_text().splitlines() if ln.startswith("{")]
    ck("egress logged the blocked private address", any(e.get("host") == "10.0.0.1" and e.get("verdict") == "blocked"
                                                        for e in egress), len(egress))
    ck(f"browser rows: +{o['rows']['new']} (omar {o['rows']['omar']}, dana {o['rows']['dana']})",
       o["rows"]["omar"] >= 10 and o["rows"]["dana"] >= 3)
    print(f"{sum(ok)}/{len(ok)} passed" + (" (canary: some MUST fail)" if canary else ""))
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
