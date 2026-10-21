#!/usr/bin/env python3
"""PIN login locks a person key after repeated wrong PINs.

Two modes.

Sandbox (default): the real app in-process (FastAPI TestClient) on a scratch
copy of the verify fixture's accounts, threads and feed. Never opens a
tenant's stores. The lock is shortened to a few seconds so expiry is proved.

  - the right PIN signs in (presence twin for every refusal below);
  - LOGIN_LOCK_FAILURES wrong PINs, each from a different client IP, lock the
    key: the last one answers 429 and the right PIN is then refused with 429;
  - the lock is logged and counted (login_pin_failed, login_locked,
    login_refused_locked);
  - a person key that does not exist locks the same way, so the lock never
    tells an attacker which keys are real;
  - a right PIN before the limit resets the count;
  - the lock lapses on its own and the right PIN signs in again.

  --canary-no-lock sets the limit out of reach: the lock checks must go red.

Live (--url): a running tenant, through whatever address is given (loopback
or the public hostname). Locks the given person key for real, for the
tenant's full lock time, so the person cannot sign in until it lapses.

    python3 docs/auth/verify_pin_lockout.py
    python3 docs/auth/verify_pin_lockout.py --canary-no-lock
    python3 docs/auth/verify_pin_lockout.py --url https://eddie.yamin.io \
        --person-key owner --pin-file /root/onboarding/eddie-owner-pin.txt \
        --unit eddie-app [--access-token-file F] [--wait-expiry]

--access-token-file holds a Cloudflare Access service token as two lines,
client id then client secret (root 600); it is sent, never printed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PASS = FAIL = 0


def check(label: str, ok: bool) -> bool:
    global PASS, FAIL
    PASS, FAIL = PASS + bool(ok), FAIL + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def wrong_pin(right: str) -> str:
    return "zzzz-zzzz-zzzz" if right != "zzzz-zzzz-zzzz" else "yyyy-yyyy-yyyy"


# ───────────────────────────────────────────────────────────────── sandbox

def sandbox(canary: bool) -> int:
    fixture = Path(os.environ.get("VERIFY_FIXTURE_ROOT", "/opt/jarvis-verify-fixture"))
    skills = fixture / "skills" / "openclaw-imports"
    src = fixture / "hermes" / "state" / "jarvis_accounts.db"
    if not src.is_file():
        sys.exit(f"REFUSING: no fixture accounts at {src}")
    box = Path(tempfile.mkdtemp(prefix="pin-lockout-"))
    (box / "hermes" / "state").mkdir(parents=True)
    s, d = sqlite3.connect(f"file:{src}?mode=ro", uri=True), sqlite3.connect(str(box / "accounts.db"))
    s.backup(d)
    s.close()
    d.close()
    lock_s = 3
    os.environ.update({
        "HERMES_HOME": str(box / "hermes"),
        "JARVIS_SKILLS_ROOT": str(skills),
        "JARVIS_IDENTITY_CONFIG": str(skills / "_shared" / "identity.json"),
        "JARVIS_ACCOUNTS_DB": str(box / "accounts.db"),
        "JARVIS_THREADS_DB": str(box / "threads.db"),
        "JARVIS_TASKS_DB": str(box / "tasks.db"),
        "JARVIS_ACTIVITY_FEED": str(box / "feed.jsonl"),
        "JARVIS_APPROVALS": str(box / "approvals.jsonl"),
        "APP_SESSION_SECRET": "verify-pin-lockout-session-secret",
        "APP_PASSPHRASE": "verify-pin-lockout-passphrase",
        "APP_COOKIE_SECURE": "0",
        "APP_LOGIN_LOCK_FAILURES": "10000" if canary else "5",
        "APP_LOGIN_LOCK_SECONDS": str(lock_s),
        "APP_LOGIN_MAX_FAILURES": "10",
        "JARVIS_SWITCH_SENTINEL": "off",
    })
    os.environ.pop("JARVIS_PG_DSN", None)
    sys.path.insert(0, os.environ.get("VERIFY_APP_ROOT") or str(Path(__file__).resolve().parents[2]))
    from fastapi.testclient import TestClient
    from app import accounts, config, threads
    import app.main as main

    for name, p in (("accounts", accounts.DB_PATH), ("threads", threads.DB_PATH)):
        if not str(Path(p).resolve()).startswith(str(box.resolve())):
            sys.exit(f"REFUSING: {name} store resolved outside the box: {p}")
    print(f"sandbox {box}; lock after {config.LOGIN_LOCK_FAILURES} wrong PINs for {config.LOGIN_LOCK_SECONDS}s"
          + ("  [CANARY: limit out of reach]" if canary else ""))
    pin = accounts.set_pin("dana")
    c = TestClient(main.app)
    ip = iter(f"198.51.100.{i}" for i in range(1, 250))

    def login(key: str, p: str):
        return c.post("/api/login", json={"person_key": key, "pin": p},
                      headers={"cf-connecting-ip": next(ip)})

    ctr = lambda n: int(((threads.all_counters() or {}).get(n) or {}).get("value") or 0)
    names = ("login_pin_failed", "login_locked", "login_refused_locked")

    print("── 1. the right PIN signs in")
    check("dana signs in with her PIN (200)", login("dana", pin).status_code == 200)

    print("── 2. wrong PINs from many addresses lock the person key")
    before = {n: ctr(n) for n in names}
    codes = [login("dana", wrong_pin(pin)).status_code for _ in range(5)]
    check(f"four wrong PINs answer 401, the fifth 429 locked (got {codes})", codes == [401] * 4 + [429])
    r = login("dana", pin)
    check(f"the right PIN is refused while locked (got {r.status_code}: "
          f"{r.json().get('detail') if r.status_code == 429 else '<detail withheld>'})",
          r.status_code == 429)
    delta = {n: ctr(n) - before[n] for n in names}
    check(f"counted: {delta}", delta == {"login_pin_failed": 5, "login_locked": 1, "login_refused_locked": 1})

    print("── 3. a person key that does not exist locks the same way")
    codes = [login("nobody-here", wrong_pin(pin)).status_code for _ in range(6)]
    check(f"five wrong tries then locked, same codes as a real key (got {codes})", codes == [401] * 4 + [429, 429])

    print("── 4. a right PIN before the limit resets the count")
    time.sleep(lock_s + 1)
    codes = [login("dana", wrong_pin(pin)).status_code for _ in range(4)]
    ok_in = login("dana", pin).status_code
    codes2 = [login("dana", wrong_pin(pin)).status_code for _ in range(4)]
    check(f"after the lock lapsed: 4 wrong, right PIN {ok_in}, 4 wrong again, never locked ({codes} {codes2})",
          codes == [401] * 4 and ok_in == 200 and codes2 == [401] * 4)

    print("── 5. the lock lapses on its own")
    codes = [login("dana", wrong_pin(pin)).status_code for _ in range(1)]
    locked = login("dana", pin).status_code
    time.sleep(lock_s + 1)
    after = login("dana", pin).status_code
    check(f"locked ({codes[-1]}, right PIN {locked}), then the right PIN signs in after {lock_s}s ({after})",
          codes[-1] == 429 and locked == 429 and after == 200)

    shutil.rmtree(box, ignore_errors=True)
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return 1 if FAIL else 0


# ──────────────────────────────────────────────────────────────────── live

def journal_count(unit: str, since: str, text: str) -> int:
    out = subprocess.run(["journalctl", "-u", unit, "--since", since, "--no-pager", "-o", "cat"],
                         capture_output=True, text=True).stdout
    return sum(1 for line in out.splitlines() if text in line)


def pin_from(path: Path) -> str:
    """The PIN out of accounts_cli set-pin output (the onboarding file), never printed."""
    import re
    text = path.read_text()
    m = re.search(r"PIN FOR \S+ .*?\n\s*\n\s+(\S+)\s*\n", text, re.S)
    if not m:
        sys.exit(f"no PIN found in {path} (expected accounts_cli set-pin output)")
    return m.group(1)


def live(a) -> int:
    import httpx
    pin = pin_from(Path(a.pin_file))
    headers = {}
    if a.access_token_file:
        # two lines, bare values or CF_ACCESS_CLIENT_ID=... / CF_ACCESS_CLIENT_SECRET=...
        cid, secret = [ln.split("=", 1)[-1].strip().strip('"') for ln in
                       Path(a.access_token_file).read_text().splitlines() if ln.strip()][:2]
        headers = {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": secret}
    c = httpx.Client(base_url=a.url.rstrip("/"), headers=headers, timeout=30, follow_redirects=False)
    since = time.strftime("%Y-%m-%d %H:%M:%S")
    login = lambda p: c.post("/api/login", json={"person_key": a.person_key, "pin": p})

    print(f"live {a.url}: person key {a.person_key!r}; this locks it for the tenant's full lock time")
    r = login(pin)
    check(f"the right PIN signs in ({r.status_code})", r.status_code == 200)
    if r.status_code != 200:
        print(f"\n{PASS}/{PASS + FAIL} passed")
        return 1
    cookie = "; ".join(f"{k}={v}" for k, v in r.cookies.items())
    c.cookies.clear()

    def counters():
        x = c.get("/api/counters", headers={"cookie": cookie})
        return (x.json().get("counters") or {}) if x.status_code == 200 else None

    names = ("login_pin_failed", "login_locked", "login_refused_locked")
    c0 = counters()
    check("counters readable with the session", c0 is not None)
    codes = [login(wrong_pin(pin)).status_code for _ in range(a.failures)]
    check(f"{a.failures - 1} wrong PINs answer 401, wrong PIN {a.failures} answers 429 locked (got {codes})",
          codes == [401] * (a.failures - 1) + [429])
    r = login(pin)
    check(f"the right PIN is refused while locked ({r.status_code}: "
          f"{r.json().get('detail') if r.status_code == 429 else '<detail withheld>'})",
          r.status_code == 429)
    c1 = counters()
    if c0 is not None and c1 is not None:
        delta = {n: int((c1.get(n) or {}).get("value") or 0) - int((c0.get(n) or {}).get("value") or 0)
                 for n in names}
        check(f"counted: {delta}", delta == {"login_pin_failed": a.failures, "login_locked": 1,
                                             "login_refused_locked": 1})
    if a.unit:
        time.sleep(1)
        n_lock = journal_count(a.unit, since, "login locked: person key")
        n_ref = journal_count(a.unit, since, "login refused: person key")
        check(f"{a.unit} journal logged the lock ({n_lock}) and the refusal ({n_ref})", n_lock == 1 and n_ref >= 1)
    if a.wait_expiry:
        print(f"   waiting {a.lock_seconds + 10}s for the lock to lapse")
        time.sleep(a.lock_seconds + 10)
        r = login(pin)
        check(f"after the lock lapsed, the right PIN signs in ({r.status_code})", r.status_code == 200)
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return 1 if FAIL else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canary-no-lock", action="store_true")
    ap.add_argument("--url")
    ap.add_argument("--person-key", default="owner")
    ap.add_argument("--pin-file")
    ap.add_argument("--unit")
    ap.add_argument("--access-token-file")
    ap.add_argument("--failures", type=int, default=5)
    ap.add_argument("--lock-seconds", type=int, default=900)
    ap.add_argument("--wait-expiry", action="store_true")
    a = ap.parse_args()
    if a.url:
        if not a.pin_file:
            sys.exit("--url needs --pin-file")
        return live(a)
    return sandbox(a.canary_no_lock)


if __name__ == "__main__":
    sys.exit(main())
