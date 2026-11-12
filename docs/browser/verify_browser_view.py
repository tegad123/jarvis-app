#!/usr/bin/env python3
"""The live view's WebSocket (/api/browser/view), end to end, without a tenant.

Real egress, a real headed worker (and a headless one), and the real route
driven through FastAPI's TestClient with real signed session cookies against
the verify sandbox's accounts (docs/tasks/tasks_sandbox.sh, started first).
Runs in a private mount namespace so the headed browser's managed policy can
sit at Chromium's compiled-in path; the host's /etc is not written.

Proves (step 6 of docs/plans/browser-har-comps.md), each refusal with its
counter moving by exactly one, and the presence case beside them:
- refused: browser off; no session; the operator; a wrong Origin; the
  browser switched off for the person; a headless browser; not taken over;
  a second viewer while one is open;
- omar, having taken over, gets his own display's RFB greeting;
- scoping: with dana's and omar's browsers both running, omar's socket opens
  omar's view and never dana's, and ?person=dana changes nothing;
- handing back closes the socket and stops x11vnc.

--canary lets the route skip the takeover check: the "not taken over" check
must then FAIL. Never :8700 or :8710.
"""
import json
import os
import pathlib
import secrets
import subprocess
import sys
import threading
import http.server

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "app"))
import browser_launch as bl  # noqa: E402
import verify_browser_headed as h  # noqa: E402

SB = pathlib.Path(os.environ.get("TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox"))
SCR = pathlib.Path("/tmp/verify-browser-view")
HEADLESS = "/root/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell"
ORIGIN = "https://view-test.jarvis.example"
CANARY = "--canary" in sys.argv

CHILD = r'''
import json, os, sys, time
sys.path.insert(0, os.environ["APP_ROOT"])
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app import accounts, auth, browser, config, threads
if os.environ.get("CANARY") == "1":
    browser.taken_over = lambda person: True
app = FastAPI(); app.include_router(browser.router)
SESS = {}
app.dependency_overrides[auth.current_session] = lambda: SESS
c = TestClient(app)
ORIGIN, SITE = os.environ["ORIGIN"], os.environ["SITE"]
def cookie(sess):
    return {config.COOKIE_NAME: auth._serializer().dumps(sess)}
OMAR = cookie({"person_key": "omar", "role": "member"})
DANA = cookie({"person_key": "dana", "role": "owner"})
OPER = cookie({"role": "operator", "operator": "operator"})
def count(name):
    return threads.bump_counter(name, 0)
def attempt(cookies=None, origin=ORIGIN, path="/api/browser/view"):
    c.cookies.clear()
    for k, v in (cookies or {}).items():
        c.cookies.set(k, v)
    try:
        with c.websocket_connect(path, headers={"origin": origin} if origin else {}, subprotocols=["binary"]) as ws:
            data = ws.receive_bytes()
            return "open", data[:12].decode("latin-1")
    except WebSocketDisconnect as exc:
        return "refused", exc.code
def refusal(why, **kw):
    before = count(f"browser_view_refused_{why}")
    got = attempt(**kw)
    return [got[0], got[1], count(f"browser_view_refused_{why}") - before]
def takeover(who, on):
    SESS.clear(); SESS.update({"person_key": who, "role": "owner" if who == "dana" else "member"})
    return c.post("/api/browser/takeover", json={"on": on}).status_code
out = {}
os.environ["JARVIS_BROWSER"] = "off"
out["browser_off"] = refusal("browser_off", cookies=OMAR)
os.environ["JARVIS_BROWSER"] = "on"
browser.call("open", "omar", url=SITE + "/get")
browser.call("open", "dana", url=SITE + "/get")
out["no_session"] = refusal("no_session")
out["operator"] = refusal("not_a_person", cookies=OPER)
out["origin_other"] = refusal("origin", cookies=OMAR, origin="https://evil.example")
out["origin_none"] = refusal("origin", cookies=OMAR, origin=None)
accounts.set_feature("omar", "browser", False, "dana")
out["feature_off"] = refusal("feature_off", cookies=OMAR)
accounts.set_feature("omar", "browser", True, "dana")
out["not_taken_over"] = refusal("not_taken_over", cookies=OMAR)
if os.environ.get("CANARY") == "1":
    print("RESULT " + json.dumps(out)); sys.exit(0)
real_sock = os.environ["JARVIS_BROWSER_SOCKET"]
os.environ["JARVIS_BROWSER_SOCKET"] = os.environ["HEADLESS_SOCKET"]
browser.call("open", "omar", url=SITE + "/get")
takeover("omar", True)
out["not_headed"] = refusal("not_headed", cookies=OMAR)
takeover("omar", False)
os.environ["JARVIS_BROWSER_SOCKET"] = real_sock
takeover("omar", True)
opened0 = count("browser_view_opened")
c.cookies.clear(); c.cookies.set(config.COOKIE_NAME, OMAR[config.COOKIE_NAME])
with c.websocket_connect("/api/browser/view?person=dana", headers={"origin": ORIGIN}, subprotocols=["binary"]) as ws:
    out["omar_view"] = ws.receive_bytes()[:12].decode("latin-1")
    out["omar_status"] = browser.call("status", "omar")
    out["dana_status"] = browser.call("status", "dana")
    out["second"] = refusal("already_viewing", cookies=OMAR)
    c.cookies.set(config.COOKIE_NAME, OMAR[config.COOKIE_NAME])
    t0 = time.time()
    takeover("omar", False)
    try:
        while True:
            ws.receive_bytes()
    except WebSocketDisconnect as exc:
        out["hand_back"] = [round(time.time() - t0, 1), exc.code]
out["opened_delta"] = count("browser_view_opened") - opened0
time.sleep(0.5)
out["after"] = browser.call("status", "omar")
print("RESULT " + json.dumps(out))
'''


def main_ns() -> int:
    subprocess.run(["mount", "-t", "tmpfs", "-o", "mode=755", "tmpfs", "/etc/opt"], check=True)
    managed = pathlib.Path("/etc/opt/chrome_for_testing/policies/managed")
    managed.mkdir(parents=True)
    (managed / "jarvis.json").write_text(json.dumps(bl.POLICY))
    vnc = h.x11vnc_bin()
    if not vnc:
        print("x11vnc missing: cannot prove the view")
        return 1
    subprocess.run(["rm", "-rf", str(SCR)], check=True)
    SCR.mkdir(parents=True)
    ip = f"127.{secrets.randbelow(250) + 3}.0.11"
    srv = http.server.ThreadingHTTPServer((ip, 0), h.Site)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    site = f"http://{ip}:{port}"
    base = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC", "DISPLAY"))}
    eg = subprocess.Popen([sys.executable, "-I", str(ROOT / "app" / "browser_egress.py")],
                          env={**base, "JARVIS_EGRESS_LISTEN": str(SCR / "egress.sock"),
                               "JARVIS_EGRESS_TEST_ALLOW": f"{ip}:{port}"},
                          stdout=open(SCR / "egress.log", "w"), stderr=subprocess.STDOUT)
    h.wait_for(SCR / "egress.sock")
    workers = []
    for name, mode, binary in (("w", "headed", h.FULL), ("hl", "headless", HEADLESS)):
        env = {**base, "JARVIS_BROWSER_BIN": binary, "JARVIS_BROWSER_PROFILE": str(SCR / f"profiles-{name}"),
               "JARVIS_EGRESS_SOCKET": str(SCR / "egress.sock"), "JARVIS_BROWSER_LISTEN": str(SCR / f"{name}.sock"),
               "JARVIS_BROWSER_TEST": "1", "JARVIS_BROWSER_MAX_OPEN": "3", "JARVIS_BROWSER_MODE": mode,
               "JARVIS_X11VNC_BIN": vnc,
               "JARVIS_BROWSER_POLICY": str(managed / "jarvis.json")}
        workers.append(subprocess.Popen([sys.executable, "-I", str(ROOT / "app" / "browser_worker.py")], env=env,
                                        stdout=open(SCR / f"{name}.log", "w"), stderr=subprocess.STDOUT))
        h.wait_for(SCR / f"{name}.sock")
    try:
        env = dict(base)
        env.update({
            "APP_ROOT": str(ROOT), "APP_GOOGLE_TOKEN_KEY": (SB / "token_key").read_text().strip(),
            "APP_PUBLIC_ORIGIN": ORIGIN, "APP_COOKIE_SECURE": "0",
            "HERMES_HOME": str(SB / "hermes"), "JARVIS_APP_ROOT": str(ROOT),
            "JARVIS_SKILLS_ROOT": "/opt/jarvis-verify-fixture/skills/openclaw-imports",
            "JARVIS_RUNTIME_CONFIG": str(SB / "runtime.json"),
            "JARVIS_IDENTITY_CONFIG": "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json",
            "JARVIS_ACCOUNTS_DB": str(SB / "accounts.db"), "JARVIS_THREADS_DB": str(SB / "threads.db"),
            "JARVIS_TASKS_DB": str(SB / "tasks.db"), "JARVIS_LEADS_DB": str(SB / "leads.db"),
            "JARVIS_ACTIVITY_FEED": str(SB / "feed.jsonl"), "JARVIS_PG_DSN": (SB / "pg_dsn").read_text().strip(),
            "JARVIS_SWITCH_BROKER": "on", "JARVIS_SWITCH_SENTINEL": "on", "JARVIS_SENTINEL_MODE": "enforce",
            "JARVIS_BROWSER": "on", "JARVIS_BROWSER_SOCKET": str(SCR / "w.sock"),
            "HEADLESS_SOCKET": str(SCR / "hl.sock"), "ORIGIN": ORIGIN, "SITE": site,
            "CANARY": "1" if CANARY else "0"})
        p = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=300)
        line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
        if not line:
            print(p.stderr[-3000:])
            h.ck("child ran", False)
            return 1
        o = json.loads(line[-1][7:])
        wlog = (SCR / "w.log").read_text()
    finally:
        for w in workers:
            w.terminate()
        eg.terminate()
        for w in workers:
            w.wait(20)
        srv.shutdown()

    def refused(key, code=4403):
        return o[key][0] == "refused" and o[key][1] == code and o[key][2] == 1

    print("each refusal, its counter +1:")
    if CANARY:
        h.ck("omar has not taken over", refused("not_taken_over"), o["not_taken_over"])
        print(f"{sum(h.ok)}/{len(h.ok)} passed (canary: the check above MUST fail)")
        return 0 if not all(h.ok) else 1
    h.ck("browser switched off", refused("browser_off"), o["browser_off"])
    h.ck("no session", refused("no_session", 4401), o["no_session"])
    h.ck("the break-glass operator", refused("operator"), o["operator"])
    h.ck("another site's Origin", refused("origin_other"), o["origin_other"])
    h.ck("no Origin at all", refused("origin_none"), o["origin_none"])
    h.ck("the browser switched off for omar", refused("feature_off"), o["feature_off"])
    h.ck("omar's browser is headless", refused("not_headed"), o["not_headed"])
    h.ck("omar has not taken over", refused("not_taken_over"), o["not_taken_over"])
    h.ck("a second viewer while one is open", refused("second"), o["second"])
    print("the presence case and scoping:")
    h.ck("omar, having taken over, gets his display's RFB greeting", o["omar_view"].startswith("RFB 003."),
         o["omar_view"])
    h.ck("   opened counter +1", o["opened_delta"] == 1, o["opened_delta"])
    h.ck("omar's browser is the one being viewed", o["omar_status"].get("viewing") is True)
    h.ck("dana's is not, though ?person=dana was asked for", o["dana_status"].get("viewing") is False
         and o["dana_status"].get("running") is True)
    opens = [json.loads(ln) for ln in wlog.splitlines() if '"op": "view_open"' in ln]
    h.ck("the worker opened a view for omar only", [x["person"] for x in opens] == ["omar"], opens)
    h.ck("handing back closes the socket within seconds", o["hand_back"][0] <= 5, o["hand_back"])
    h.ck("   and the worker stopped omar's x11vnc", o["after"].get("viewing") is False
         and '"op": "view_stopped", "person": "omar"' in wlog)
    print(f"{sum(h.ok)}/{len(h.ok)} passed" + (" (canary: 'not taken over' MUST fail)" if CANARY else ""))
    return 0 if all(h.ok) else 1


def main() -> int:
    for k in ("AUTH_BASE", "APP_PORT"):
        if os.environ.get(k, "").endswith(("8700", "8710")):
            sys.exit("REFUSING: live or testco port")
    if os.environ.get("_VERIFY_VIEW_NS") == "1":
        return main_ns()
    if os.geteuid() != 0:
        sys.exit("REFUSED: run as root (needs a private mount namespace)")
    return subprocess.run(["unshare", "-m", "--propagation", "private", sys.executable, *sys.argv],
                          env={**os.environ, "_VERIFY_VIEW_NS": "1"}).returncode


if __name__ == "__main__":
    sys.exit(main())
