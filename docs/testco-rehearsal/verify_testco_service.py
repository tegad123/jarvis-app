#!/usr/bin/env python3
"""TESTCO as a standing service — isolation asserted from OUTSIDE the process.

Reads what the RUNNING services actually opened (/proc/<pid>/environ and the
open file descriptors), not what the unit files say. A unit file is an
intention; lsof is evidence.
"""
import json, os, subprocess, sys
from pathlib import Path

ok, fails = [], []
def ck(n, c, x=""):
    ok.append(bool(c)); print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" — {x}" if x else ""))
    if not c: fails.append(n)

def pid_of(unit):
    out = subprocess.run(["systemctl", "show", unit, "-p", "MainPID", "--value"],
                         capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() and out != "0" else None

def environ(pid):
    raw = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace")
    return dict(kv.split("=", 1) for kv in raw.split("\0") if "=" in kv)

def open_files(pid):
    d = Path(f"/proc/{pid}/fd")
    out = []
    for fd in d.iterdir():
        try:
            out.append(os.readlink(fd))
        except OSError:
            pass
    return out

print("\n── units are up, enabled, and boot-surviving ──────────────────")
for unit in ("testco-gateway.service", "testco-app.service"):
    st = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True).stdout.strip()
    en = subprocess.run(["systemctl", "is-enabled", unit], capture_output=True, text=True).stdout.strip()
    ck(f"{unit} active", st == "active", st)
    ck(f"{unit} enabled (starts at boot)", en == "enabled", en)
    rs = subprocess.run(["systemctl", "show", unit, "-p", "Restart", "--value"],
                        capture_output=True, text=True).stdout.strip()
    ck(f"{unit} Restart=on-failure", rs == "on-failure", rs)

print("\n── naming is unambiguous in systemctl ─────────────────────────")
listed = subprocess.run(["systemctl", "list-units", "testco-*", "--no-pager", "--no-legend"],
                        capture_output=True, text=True).stdout
ck("`systemctl list-units 'testco-*'` shows exactly the testco stack",
   listed.count(".service") == 2 and "jarvis-app.service" not in listed,
   f"{listed.count('.service')} units")
live = subprocess.run(["systemctl", "list-units", "jarvis-*", "--no-pager", "--no-legend"],
                      capture_output=True, text=True).stdout
ck("and no testco unit appears under 'jarvis-*'", "testco" not in live)

print("\n── the RUNNING processes point at testco, per /proc ───────────")
gpid, apid = pid_of("testco-gateway.service"), pid_of("testco-app.service")
ck("both have a main PID", bool(gpid and apid), f"gateway={gpid} app={apid}")
for label, pid in (("gateway", gpid), ("app", apid)):
    env = environ(pid)
    ck(f"{label}: HERMES_HOME is the testco tree",
       env.get("HERMES_HOME") == "/opt/testco/hermes", env.get("HERMES_HOME"))
    ck(f"{label}: writes are guarded", env.get("JARVIS_ALLOW_WRITES") == "0",
       env.get("JARVIS_ALLOW_WRITES"))
    ck(f"{label}: feed redirected (PATTERN 3 — stores emit)",
       env.get("JARVIS_ACTIVITY_FEED", "").startswith("/opt/testco/"),
       env.get("JARVIS_ACTIVITY_FEED"))
    ck(f"{label}: nothing points into the scratchpad",
       not any("/tmp/claude-" in v for v in env.values()))

print("\n── NO open file descriptor reaches the live tenant ────────────")
LIVE = ("/root/.hermes/state", "/root/jarvis-app/data")
for label, pid in (("gateway", gpid), ("app", apid)):
    files = open_files(pid)
    leaked = [f for f in files if any(f.startswith(p) for p in LIVE)]
    ck(f"{label}: no fd under {LIVE[0]} or {LIVE[1]}", not leaked, str(leaked[:3]))
    testco = [f for f in files if f.startswith("/opt/testco/")]
    ck(f"{label}: it IS holding testco files open (so the check means something)",
       len(testco) > 0, f"{len(testco)} testco fds")

print("\n── the two tenants serve different data ───────────────────────")
def api(port, path, jar):
    return subprocess.run(["curl", "-s", "-b", jar, "-c", jar, f"http://127.0.0.1:{port}{path}"],
                          capture_output=True, text=True).stdout

SP = "/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad"
tpins = json.load(open("/opt/testco/SEEDED_PINS.json"))
tjar = f"{SP}/tsvc.txt"; Path(tjar).unlink(missing_ok=True)
subprocess.run(["curl", "-s", "-c", tjar, "-X", "POST", "http://127.0.0.1:8710/api/login",
                "-H", "Content-Type: application/json",
                "-d", json.dumps({"person_key": "dana", "pin": tpins["dana"]})],
               capture_output=True)
tl = json.loads(api(8710, "/api/leads", tjar))
ck("testco serves its own app-native leads", tl.get("source") == "store" and tl.get("count") == 40,
   f"source={tl.get('source')} count={tl.get('count')}")
tf = json.loads(api(8710, "/api/feed?limit=1000", tjar))
ck("testco feed is its own (90 seeded events)", tf.get("total") == 90, str(tf.get("total")))

live_feed = sum(1 for l in open("/root/.hermes/state/activity_feed.jsonl") if l.strip())
ck("LIVE feed still 82 — unchanged by any of this", live_feed == 82, str(live_feed))
ck("live app still healthy",
   '"ok":true' in subprocess.run(["curl","-s","-m","5","http://127.0.0.1:8700/api/ping"],
                                 capture_output=True, text=True).stdout)

print(f"\n{sum(ok)}/{len(ok)} passed")
if fails: print("FAILED: " + "; ".join(fails))
sys.exit(0 if all(ok) else 1)
