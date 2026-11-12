#!/usr/bin/env python3
"""Phone app sign-in (app/devices.py) and web/app parity.

Sandbox only: the real app in-process (FastAPI TestClient) on a scratch copy
of the verify fixture's accounts, with Google mocked. Never opens a tenant's
stores and sends nothing.

  - a PIN signs a phone in, and its token is the same person and role as the
    web cookie for that person;
  - a bearer that is not an app token falls through to the cookie (the
    gateway's API key keeps working), and alone is refused;
  - a task written with the phone token is read back with the web cookie,
    finished on the web, and read back done on the phone, with the same
    completed_at: one row, two clients;
  - a member token is refused on an admin route, and an owner token passes
    the same route (the refusal is real);
  - refresh rotates: the old refresh token is refused after one use;
  - signing out, and signing the phone out from the web, each end the token
    and the refresh token on the next request;
  - turning a person off ends their phone token and refresh;
  - an access token expires on its own;
  - /app/start: no session goes to Google, Google comes back to /app/start,
    and the app's address gets a one-time code; a wrong verifier spends the
    code, the right verifier then fails, and a fresh code with the right
    verifier signs in; a foreign redirect and an operator session are refused;
  - PIN sign-in on a phone is refused from the public side when the tenant is
    Google-only;
  - counters and app_devices rows, before and after.

--canary-no-revoke makes signing out do nothing: the sign-out checks must go red.

    python3 docs/mobile/verify_app_signin.py
    python3 docs/mobile/verify_app_signin.py --canary-no-revoke
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PASS = FAIL = 0


def check(label: str, ok: bool) -> bool:
    global PASS, FAIL
    PASS, FAIL = PASS + bool(ok), FAIL + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canary-no-revoke", action="store_true")
    a = ap.parse_args()

    fixture = Path(os.environ.get("VERIFY_FIXTURE_ROOT", "/opt/jarvis-verify-fixture"))
    skills = fixture / "skills" / "openclaw-imports"
    src = fixture / "hermes" / "state" / "jarvis_accounts.db"
    if not src.is_file():
        sys.exit(f"REFUSING: no fixture accounts at {src}")
    box = Path(tempfile.mkdtemp(prefix="app-signin-"))
    (box / "hermes" / "state").mkdir(parents=True)
    s, d = sqlite3.connect(f"file:{src}?mode=ro", uri=True), sqlite3.connect(str(box / "accounts.db"))
    s.backup(d)
    s.close()
    d.close()
    os.environ.update({
        "HERMES_HOME": str(box / "hermes"),
        "JARVIS_SKILLS_ROOT": str(skills),
        "JARVIS_IDENTITY_CONFIG": str(skills / "_shared" / "identity.json"),
        "JARVIS_ACCOUNTS_DB": str(box / "accounts.db"),
        "JARVIS_THREADS_DB": str(box / "threads.db"),
        "JARVIS_TASKS_DB": str(box / "tasks.db"),
        "JARVIS_TASKS_SOURCE": "store",
        "JARVIS_ACTIVITY_FEED": str(box / "feed.jsonl"),
        "JARVIS_APPROVALS": str(box / "approvals.jsonl"),
        "APP_SESSION_SECRET": "verify-app-signin-session-secret",
        "APP_PASSPHRASE": "verify-app-signin-passphrase",
        "APP_COOKIE_SECURE": "0",
        "APP_PUBLIC_ORIGIN": "http://testserver",
        "APP_GOOGLE_OAUTH_MOCK": "1",
        "APP_TENANT_SLUG": "verifyco",
        "APP_SIGNIN": "pin",
        "APP_LOGIN_MAX_FAILURES": "1000",
        "JARVIS_SWITCH_SENTINEL": "off",
    })
    for k in ("JARVIS_PG_DSN", "APP_OAUTH_FRONT", "JARVIS_APP_BROKER_URL"):
        os.environ.pop(k, None)
    sys.path.insert(0, os.environ.get("VERIFY_APP_ROOT") or str(Path(__file__).resolve().parents[2]))
    from fastapi.testclient import TestClient
    from app import accounts, devices, tasks_store, threads
    import app.main as main

    for name, p in (("accounts", accounts.DB_PATH), ("threads", threads.DB_PATH), ("tasks", tasks_store.DB_PATH)):
        if not str(Path(p).resolve()).startswith(str(box.resolve())):
            sys.exit(f"REFUSING: {name} store resolved outside the box: {p}")
    if a.canary_no_revoke:
        devices.revoke = lambda device_id, by: True
    print(f"sandbox {box}" + ("  [CANARY: sign-out does nothing]" if a.canary_no_revoke else ""))

    pins = {k: accounts.set_pin(k) for k in ("dana", "omar")}
    ctr = lambda n: int(((threads.all_counters() or {}).get(n) or {}).get("value") or 0)
    names = ("app_device_signed_in", "app_device_signed_out", "app_refreshed", "app_refresh_refused",
             "app_refresh_account_off", "app_token_device_revoked", "app_code_refused",
             "app_code_verifier_refused", "app_start_code_sent")
    c0 = {n: ctr(n) for n in names}
    rows = lambda: sqlite3.connect(str(box / "accounts.db")).execute(
        "SELECT count(*), sum(revoked_at IS NOT NULL) FROM app_devices").fetchone()
    web = TestClient(main.app)
    phone = TestClient(main.app)

    def web_login(key: str) -> TestClient:
        cl = TestClient(main.app)
        r = cl.post("/api/login", json={"person_key": key, "pin": pins[key]})
        assert r.status_code == 200, r.text
        return cl

    def app_login(key: str, name: str = "Test iPhone") -> dict:
        r = phone.post("/api/app/login", json={"person_key": key, "pin": pins[key], "device_name": name,
                                                "platform": "ios"})
        return r.json() if r.status_code == 200 else {"_status": r.status_code}

    bearer = lambda t: {"Authorization": f"Bearer {t['access_token']}"}

    print("── 1. a PIN signs a phone in; the token is the same person as the web cookie")
    t = app_login("dana")
    check("POST /api/app/login answers tokens", "access_token" in t and t.get("access_token", "").startswith("ja1."))
    r_app = phone.get("/api/session", headers=bearer(t)).json()
    web = web_login("dana")
    r_web = web.get("/api/session").json()
    check(f"phone and web sessions name the same person and role ({r_app.get('person_key')}/{r_app.get('role')})",
          (r_app.get("person_key"), r_app.get("role")) == (r_web.get("person_key"), r_web.get("role")) == ("dana", "owner"))
    r = phone.post("/api/app/login", json={"person_key": "dana", "pin": "zzzz-zzzz-zzzz", "platform": "ios"})
    check(f"a wrong PIN on the phone is refused like the web ({r.status_code})", r.status_code == 401)

    print("── 2. a bearer that is not an app token falls through to the cookie")
    r = web.get("/api/session", headers={"Authorization": "Bearer not-an-app-token"})
    check(f"cookie plus a foreign bearer still signs in ({r.status_code})", r.status_code == 200)
    r = TestClient(main.app).get("/api/session", headers={"Authorization": "Bearer not-an-app-token"})
    check(f"a foreign bearer alone is refused ({r.status_code})", r.status_code == 401)
    r = TestClient(main.app).get("/api/session", headers={"Authorization": "Bearer ja1.forged"})
    check(f"a forged app token is refused ({r.status_code})", r.status_code == 401)

    print("── 3. one task, two clients")
    r = phone.post("/api/tasks", json={"title": "Phone parity task", "priority": "high"}, headers=bearer(t))
    check(f"phone creates a task ({r.status_code})", r.status_code == 201)
    tid = (r.json().get("task") or {}).get("task_id")
    on_web = next((x for x in web.get("/api/tasks").json().get("tasks", []) if x["task_id"] == tid), None)
    check("the web reads the phone's task", bool(on_web) and on_web["title"] == "Phone parity task")
    r = web.post(f"/api/tasks/{tid}/status", json={"status": "done"})
    check(f"the web finishes it ({r.status_code})", r.status_code == 200)
    done_web = (r.json().get("task") or {}).get("completed_at")
    lst = phone.get("/api/tasks", params={"status": "done"}, headers=bearer(t)).json().get("tasks", [])
    on_phone = next((x for x in lst if x["task_id"] == tid), None)
    check("the phone reads it done, with the same completed_at",
          bool(on_phone) and on_phone["status"] == "done" and on_phone.get("completed_at") == done_web and bool(done_web))
    n = sqlite3.connect(str(box / "tasks.db")).execute(
        "SELECT count(*) FROM tasks WHERE title = 'Phone parity task'").fetchone()[0]
    check(f"one row in the tasks store ({n})", n == 1)

    print("── 4. role gates hold on the phone")
    m = app_login("omar", "Omar's phone")
    r2 = phone.post("/api/tasks", json={"title": "Member gate task"}, headers=bearer(t))
    gid = (r2.json().get("task") or {}).get("task_id")
    r = phone.post(f"/api/tasks/{gid}/archive", headers=bearer(m))
    check(f"a member token cannot archive ({r.status_code})", r.status_code == 403)
    r = phone.post(f"/api/tasks/{gid}/archive", headers=bearer(t))
    check(f"the owner token can, on the same route ({r.status_code})", r.status_code == 200)

    print("── 5. refresh rotates")
    r = phone.post("/api/app/refresh", json={"device_id": t["device_id"], "refresh_token": t["refresh_token"]})
    t2 = r.json() if r.status_code == 200 else {}
    check(f"refresh answers a new pair ({r.status_code})", r.status_code == 200 and t2.get("refresh_token") != t["refresh_token"])
    r = phone.post("/api/app/refresh", json={"device_id": t["device_id"], "refresh_token": t["refresh_token"]})
    check(f"the old refresh token is refused after one use ({r.status_code})", r.status_code == 401)
    check("the new access token works", phone.get("/api/session", headers=bearer(t2)).status_code == 200)

    print("── 6. signing out ends the phone")
    r = phone.post("/api/app/signout", headers=bearer(t2))
    check(f"sign-out answers ok ({r.status_code})", r.status_code == 200)
    r = phone.get("/api/session", headers=bearer(t2))
    check(f"the access token is refused after sign-out ({r.status_code})", r.status_code == 401)
    r = phone.post("/api/app/refresh", json={"device_id": t2["device_id"], "refresh_token": t2["refresh_token"]})
    check(f"the refresh token is refused after sign-out ({r.status_code})", r.status_code == 401)

    print("── 7. the web signs a phone out")
    t3 = app_login("dana", "Dana's iPad")
    listed = web.get("/api/devices").json().get("devices", [])
    check("the web lists the phone", any(x["device_id"] == t3["device_id"] for x in listed))
    r = web.delete(f"/api/devices/{t3['device_id']}")
    check(f"the web signs it out ({r.status_code})", r.status_code == 200)
    r = phone.get("/api/session", headers=bearer(t3))
    check(f"that phone's token is refused next request ({r.status_code})", r.status_code == 401)
    r = web_login("omar").delete(f"/api/devices/{m['device_id']}")
    check(f"a member can sign out their own phone ({r.status_code})", r.status_code == 200)

    print("── 8. turning a person off ends their phone")
    m2 = app_login("omar", "Omar's second phone")
    check("omar's new phone works", phone.get("/api/session", headers=bearer(m2)).status_code == 200)
    accounts.set_disabled("omar", True)
    r = phone.get("/api/session", headers=bearer(m2))
    check(f"omar's token is refused once he is off ({r.status_code})", r.status_code == 401)
    r = phone.post("/api/app/refresh", json={"device_id": m2["device_id"], "refresh_token": m2["refresh_token"]})
    check(f"omar's refresh is refused once he is off ({r.status_code})", r.status_code == 401)
    accounts.set_disabled("omar", False)

    print("── 9. an access token expires on its own")
    t4 = app_login("dana", "Dana's old phone")
    real = devices.ACCESS_SECONDS
    devices.ACCESS_SECONDS = 1
    time.sleep(2.2)
    r = phone.get("/api/session", headers=bearer(t4))
    devices.ACCESS_SECONDS = real
    check(f"a stale access token is refused ({r.status_code}: {r.json().get('detail')})",
          r.status_code == 401 and r.json().get("detail") == "Session expired.")
    r = phone.post("/api/app/refresh", json={"device_id": t4["device_id"], "refresh_token": t4["refresh_token"]})
    check(f"and refresh brings it back ({r.status_code})", r.status_code == 200)

    print("── 10. /app/start hands the phone a one-time code through Google")
    sheet = TestClient(main.app)
    dana_email = accounts.get("dana")["email"]

    def through_google(challenge: str) -> dict:
        r = sheet.get("/app/start", params={"challenge": challenge, "redirect": "jarvis://auth"},
                      follow_redirects=False)
        out = {"first": r.status_code, "to_google": r.headers.get("location") == "/api/auth/google/start"}
        r = sheet.get("/api/auth/google/start", follow_redirects=False)
        mock = urlparse(r.headers.get("location", ""))
        r = sheet.get(mock.path, params={**{k: v[0] for k, v in parse_qs(mock.query).items()}, "email": dana_email},
                      follow_redirects=False)
        cb = urlparse(r.headers.get("location", ""))
        r = sheet.get(cb.path, params={k: v[0] for k, v in parse_qs(cb.query).items()}, follow_redirects=False)
        out["back_to_start"] = r.headers.get("location") == "/app/start"
        r = sheet.get("/app/start", follow_redirects=False)
        loc = urlparse(r.headers.get("location", ""))
        out["scheme"] = f"{loc.scheme}://{loc.netloc}"
        out["query"] = {k: v[0] for k, v in parse_qs(loc.query).items()}
        return out

    v1, ch1 = pkce()
    g = through_google(ch1)
    check(f"no session: /app/start goes to Google ({g['first']})", g["first"] == 302 and g["to_google"])
    check("Google comes back to /app/start", g["back_to_start"])
    check(f"the app's address gets a code and the tenant ({g['scheme']})",
          g["scheme"] == "jarvis://auth" and bool(g["query"].get("code")) and g["query"].get("tenant") == "verifyco")
    wrong, _ = pkce()
    r = phone.post("/api/app/exchange", json={"code": g["query"]["code"], "verifier": wrong, "platform": "ios"})
    check(f"a wrong verifier is refused ({r.status_code})", r.status_code == 401)
    r = phone.post("/api/app/exchange", json={"code": g["query"]["code"], "verifier": v1, "platform": "ios"})
    check(f"that code is spent; the right verifier no longer works ({r.status_code})", r.status_code == 401)
    sheet.cookies.clear()
    v2, ch2 = pkce()
    g = through_google(ch2)
    r = phone.post("/api/app/exchange", json={"code": g["query"]["code"], "verifier": v2, "platform": "ios",
                                              "device_name": "Dana's iPhone"})
    t5 = r.json() if r.status_code == 200 else {}
    check(f"a fresh code with the right verifier signs in ({r.status_code})",
          r.status_code == 200 and t5.get("person_key") == "dana")
    r = phone.post("/api/app/exchange", json={"code": g["query"]["code"], "verifier": v2, "platform": "ios"})
    check(f"the same code cannot be used twice ({r.status_code})", r.status_code == 401)
    r = sheet.get("/app/start", params={"challenge": ch2, "redirect": "https://evil.example/cb"},
                  follow_redirects=False)
    check(f"a foreign redirect is refused ({r.status_code})", r.status_code == 400)
    op = TestClient(main.app)
    op.post("/api/login", json={"passphrase": "verify-app-signin-passphrase"})
    r = op.get("/app/start", params={"challenge": ch2, "redirect": "jarvis://auth"}, follow_redirects=False)
    check(f"an operator session cannot sign in a phone ({r.status_code})", r.status_code == 403)
    r = web.get("/", follow_redirects=False)
    check(f"the web app itself still loads ({r.status_code})", r.status_code == 200)

    print("── 11. Google-only tenants refuse a phone PIN from the public side")
    os.environ["APP_SIGNIN"] = "google"
    r = phone.post("/api/app/login", json={"person_key": "dana", "pin": pins["dana"], "platform": "ios"},
                   headers={"x-forwarded-for": "198.51.100.7"})
    check(f"refused from the public side ({r.status_code})", r.status_code == 403)
    r = phone.post("/api/app/login", json={"person_key": "dana", "pin": pins["dana"], "platform": "ios"})
    check(f"loopback still signs in (presence twin, {r.status_code})", r.status_code == 200)
    os.environ["APP_SIGNIN"] = "pin"

    print("── 12. ledger")
    c1 = {n: ctr(n) for n in names}
    delta = {n: c1[n] - c0[n] for n in names}
    print(f"   counters: {delta}")
    want = {"app_device_signed_in": 7, "app_device_signed_out": 3, "app_refreshed": 2, "app_refresh_refused": 2,
            "app_refresh_account_off": 1, "app_token_device_revoked": 2, "app_code_refused": 2,
            "app_code_verifier_refused": 1, "app_start_code_sent": 2}
    check("each counter moved by exactly what this run did", delta == want)
    total, revoked = rows()
    print(f"   app_devices: {total} rows, {revoked or 0} signed out")
    check("app_devices has the 7 phones signed in and the 3 signed out", (total, revoked or 0) == (7, 3))

    shutil.rmtree(box, ignore_errors=True)
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
