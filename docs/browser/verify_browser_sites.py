#!/usr/bin/env python3
"""Connected sites (step 9 of docs/plans/browser-har-comps.md), end to end.

Real egress, a real (headless) worker, two fixture sites on loopback that set
a session cookie each, and the app's routes through FastAPI's TestClient
against the verify sandbox (docs/tasks/tasks_sandbox.sh, started first).

Proves:
- "I'm signed in" records the site of the page the browser is on, and only
  that; with nothing open it is refused;
- the list carries no username, password or cookie (its keys, asserted);
- one person's list never holds another's rows;
- Sign out removes that site's cookie from the profile, the row, and the
  read allowance; the other site's cookie stays (presence twin), and so does
  the other person's;
- Stop always allowing removes only the allowance, the sign-in stays;
- a site not in the list is 404 and changes nothing; counters move by the
  exact amounts.
--canary makes sign-out skip the worker: the cookie checks must FAIL.
Never :8700 or :8710.
"""
import http.server
import json
import os
import pathlib
import secrets
import sqlite3
import subprocess
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import verify_browser_headed as h  # noqa: E402

SB = pathlib.Path(os.environ.get("TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox"))
SCR = pathlib.Path("/tmp/verify-browser-sites")
HEADLESS = "/root/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell"
CANARY = "--canary" in sys.argv

CHILD = r'''
import json, os, sys
sys.path.insert(0, os.environ["APP_ROOT"])
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import auth, browser, platform_store as store, sentinel, threads
if os.environ.get("CANARY") == "1":
    browser.call = (lambda real: lambda op, person, timeout=100, **kw:
                    {"ok": True, "cookies_removed": 0} if op == "forget_site" else real(op, person, timeout, **kw))(browser.call)
app = FastAPI(); app.include_router(browser.router)
SESS = {}
app.dependency_overrides[auth.current_session] = lambda: SESS
c = TestClient(app)
S1, S2 = os.environ["SITE1"], os.environ["SITE2"]
K1, K2 = S1.split("//")[1].split(":")[0], S2.split("//")[1].split(":")[0]
def be(who):
    SESS.clear(); SESS.update({"person_key": who, "role": "owner" if who == "dana" else "member"})
def page(who, url):
    return browser.call("open", who, url=url).get("snapshot", "")
def count(n):
    return threads.bump_counter(n, 0)
def allowance(who, site):
    aid = store.create_approval(origin="seed", user_id=who, kind="action", skill="jarvis", summary="verify read",
                                action={"type": "browser_read", "site": site}, status="approved",
                                decided_at=sentinel.utcnow(), decided_by=who)
    act = sentinel.Action(connector=browser.READ_CONNECTOR, method="read", cls=sentinel.EXTERNAL_SEND,
                          destinations=(f"@{site}",), skill="jarvis", user=who)
    g = sentinel.grant_from_approval(aid, act, sentinel.SCOPE_ALWAYS, sentinel.utcnow())
    store.create_grant(g, approval_id=aid, action_class=sentinel.EXTERNAL_SEND, created_by=who)
    return g.id
out = {}
# Each run's fixture sites are new addresses; an earlier run's rows would make "only hers" two rows.
conn = browser._db(); conn.execute("DELETE FROM browser_sites WHERE person_key IN ('omar','dana')"); conn.commit()
c0 = {n: count(n) for n in ("browser_site_connected", "browser_site_signed_out", "browser_read_revoked")}
be("omar")
browser.call("close", "omar"); browser.call("close", "dana")
out["confirm_nothing_open"] = c.post("/api/browser/sites/confirm").status_code
page("omar", S1 + "/session/omar")
r = c.post("/api/browser/sites/confirm")
out["confirm1"] = [r.status_code, r.json()]
page("omar", S2 + "/session/omar")
be("dana")
page("dana", S2 + "/session/dana")
out["confirm_dana"] = c.post("/api/browser/sites/confirm").json()
be("omar")
gid = allowance("omar", K1)
out["omar_list"] = c.get("/api/browser/sites").json()["sites"]
be("dana")
out["dana_list"] = c.get("/api/browser/sites").json()["sites"]
be("omar")
out["before_s1"] = page("omar", S1 + "/get")
out["before_s2"] = page("omar", S2 + "/get")
out["not_mine"] = c.delete(f"/api/browser/sites/{K2}").status_code
r = c.delete(f"/api/browser/sites/{K1}")
out["sign_out"] = [r.status_code, r.json()]
out["after_s1"] = page("omar", S1 + "/get")
out["after_s2"] = page("omar", S2 + "/get")
out["dana_s2"] = page("dana", S2 + "/get")
out["grant_after"] = [g.id for g in store.active_grants("omar", browser.READ_CONNECTOR, "jarvis") if g.id == gid]
out["again"] = c.delete(f"/api/browser/sites/{K1}").status_code
gid2 = allowance("omar", K2)
out["list_allow_only"] = c.get("/api/browser/sites").json()["sites"]
r = c.delete(f"/api/browser/sites/{K2}/reading")
out["stop_reading"] = [r.status_code, r.json()]
out["stop_again"] = c.delete(f"/api/browser/sites/{K2}/reading").status_code
out["list_end"] = c.get("/api/browser/sites").json()["sites"]
out["s2_still"] = page("omar", S2 + "/get")
out["deltas"] = {n: count(n) - v for n, v in c0.items()}
out["k"] = [K1, K2]
print("RESULT " + json.dumps(out))
'''


def main() -> int:
    for k in ("AUTH_BASE", "APP_PORT"):
        if os.environ.get(k, "").endswith(("8700", "8710")):
            sys.exit("REFUSING: live or testco port")
    subprocess.run(["rm", "-rf", str(SCR)], check=True)
    SCR.mkdir(parents=True)
    sites, servers = [], []
    for n in (21, 22):
        ip = f"127.{secrets.randbelow(250) + 3}.0.{n}"
        srv = http.server.ThreadingHTTPServer((ip, 0), h.Site)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        sites.append(f"http://{ip}:{srv.server_address[1]}")
    base = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC", "DISPLAY"))}
    eg = subprocess.Popen([sys.executable, "-I", str(ROOT / "app" / "browser_egress.py")],
                          env={**base, "JARVIS_EGRESS_LISTEN": str(SCR / "egress.sock"),
                               "JARVIS_EGRESS_TEST_ALLOW": ",".join(s.split("//")[1] for s in sites)},
                          stdout=open(SCR / "egress.log", "w"), stderr=subprocess.STDOUT)
    h.wait_for(SCR / "egress.sock")
    w = subprocess.Popen([sys.executable, "-I", str(ROOT / "app" / "browser_worker.py")],
                         env={**base, "JARVIS_BROWSER_BIN": HEADLESS, "JARVIS_BROWSER_PROFILE": str(SCR / "profiles"),
                              "JARVIS_EGRESS_SOCKET": str(SCR / "egress.sock"),
                              "JARVIS_BROWSER_LISTEN": str(SCR / "w.sock"), "JARVIS_BROWSER_TEST": "1",
                              "JARVIS_BROWSER_MAX_OPEN": "3"},
                         stdout=open(SCR / "w.log", "w"), stderr=subprocess.STDOUT)
    h.wait_for(SCR / "w.sock")
    try:
        env = dict(base)
        env.update({
            "APP_ROOT": str(ROOT), "APP_GOOGLE_TOKEN_KEY": (SB / "token_key").read_text().strip(),
            "HERMES_HOME": str(SB / "hermes"), "JARVIS_APP_ROOT": str(ROOT),
            "JARVIS_SKILLS_ROOT": "/opt/jarvis-verify-fixture/skills/openclaw-imports",
            "JARVIS_RUNTIME_CONFIG": str(SB / "runtime.json"),
            "JARVIS_IDENTITY_CONFIG": "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json",
            "JARVIS_ACCOUNTS_DB": str(SB / "accounts.db"), "JARVIS_THREADS_DB": str(SB / "threads.db"),
            "JARVIS_TASKS_DB": str(SB / "tasks.db"), "JARVIS_LEADS_DB": str(SB / "leads.db"),
            "JARVIS_ACTIVITY_FEED": str(SB / "feed.jsonl"), "JARVIS_PG_DSN": (SB / "pg_dsn").read_text().strip(),
            "JARVIS_SWITCH_BROKER": "on", "JARVIS_SWITCH_SENTINEL": "on", "JARVIS_SENTINEL_MODE": "enforce",
            "JARVIS_BROWSER": "on", "JARVIS_BROWSER_SOCKET": str(SCR / "w.sock"),
            "SITE1": sites[0], "SITE2": sites[1], "CANARY": "1" if CANARY else "0"})
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
        w.terminate()
        eg.terminate()
        w.wait(20)
        for s in servers:
            s.shutdown()
    k1, k2 = o["k"]
    rows = sqlite3.connect(f"file:{SB / 'accounts.db'}?mode=ro", uri=True).execute(
        "SELECT person_key, site FROM browser_sites WHERE site IN (?,?) ORDER BY person_key", (k1, k2)).fetchall()
    keys = {"site", "state", "connected_at", "last_used_at", "read_allowance", "saved_login"}

    print("I'm signed in:")
    h.ck("with nothing open, refused", o["confirm_nothing_open"] == 409, o["confirm_nothing_open"])
    h.ck("records the site of the page omar's browser is on", o["confirm1"][0] == 200
         and o["confirm1"][1]["site"]["site"] == k1 and o["confirm1"][1]["site"]["state"] == "connected")
    print("the list:")
    h.ck("its rows carry exactly site, state, times and two yes/no flags (no username, password, cookie)",
         all(set(r) == keys for r in o["omar_list"] + o["dana_list"]), [sorted(r) for r in o["omar_list"]])
    h.ck("omar sees his one site, with the read allowance", [(r["site"], r["read_allowance"]) for r in o["omar_list"]]
         == [(k1, True)], o["omar_list"])
    h.ck("dana sees only hers", [r["site"] for r in o["dana_list"]] == [k2], o["dana_list"])
    print("sign out:")
    h.ck("presence first: omar's browser sends the cookie to both sites", "sess=omar" in o["before_s1"]
         and "sess=omar" in o["before_s2"])
    h.ck("omar cannot sign out of a site only dana has", o["not_mine"] == 404)
    h.ck("sign out answers with what it did", o["sign_out"][0] == 200 and o["sign_out"][1].get("cookies_removed", 0) >= 1
         and o["sign_out"][1].get("allowances_revoked") == 1, o["sign_out"])
    h.ck("the site's cookie is gone from omar's profile", "cookie is none" in o["after_s1"], o["after_s1"][-60:])
    h.ck("   twin: the other site's cookie stays", "sess=omar" in o["after_s2"], o["after_s2"][-60:])
    h.ck("   and dana's browser is untouched", "sess=dana" in o["dana_s2"], o["dana_s2"][-60:])
    h.ck("the row is gone; dana's stays", rows == [("dana", k2)], rows)
    h.ck("the read allowance is revoked", o["grant_after"] == [])
    h.ck("signing out again is 404", o["again"] == 404)
    print("stop always allowing:")
    h.ck("an allowance with no sign-in still lists, as an allowance", [(r["site"], r["state"], r["read_allowance"])
         for r in o["list_allow_only"]] == [(k2, None, True)], o["list_allow_only"])
    h.ck("stopping it revokes one grant", o["stop_reading"] == [200, {"ok": True, "revoked": 1}], o["stop_reading"])
    h.ck("   again: 404", o["stop_again"] == 404)
    h.ck("   the list is empty and the sign-in cookie stays", o["list_end"] == [] and "sess=omar" in o["s2_still"])
    print("ledger:")
    h.ck("connected +2, signed out +1, read revoked +2", o["deltas"] == {"browser_site_connected": 2,
         "browser_site_signed_out": 1, "browser_read_revoked": 2}, o["deltas"])
    h.ck("the worker logged the site and counts, never a cookie", '"op": "forget_site"' in wlog
         and "sess=omar" not in wlog)
    print(f"{sum(h.ok)}/{len(h.ok)} passed" + (" (canary: the cookie checks MUST fail)" if CANARY else ""))
    return 0 if all(h.ok) else 1


if __name__ == "__main__":
    sys.exit(main())
