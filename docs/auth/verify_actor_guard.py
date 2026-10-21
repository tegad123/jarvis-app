#!/usr/bin/env python3
"""A tool cannot supply its own actor: forged, asserted or stripped.

Runs the tenant's real skills authz (`authz.require(..., "task.add")`) as the
tenant's agent user, with the gateway's broker settings, never a mock, and
never writes a task: authz.require only decides and logs.

Inside the gateway: the probe process is moved into `<slug>-gateway.service`'s
cgroup first, where every tool the gateway runs lives (the kernel sets it; a
tool cannot leave it). Cases:

  forged      JARVIS_ACTOR_MSG=app:<random>          -> denied (the app never issued it)
  email-ref   JARVIS_ACTOR_MSG=app:<owner email>     -> denied (not an app reference)
  person      no reference, --actor <owner email>    -> denied (self-asserted person)
  system      no reference, --actor <assistant>      -> denied while an app chat turn runs
                                                        (--during-turn: waits for one)
Presence twins, outside the gateway (CLI/timer rules unchanged):
  person      --actor <owner email>                  -> allowed
  forged      JARVIS_ACTOR_MSG=app:<random>          -> denied (forgery fails everywhere)

Each denial must leave its DENY line in authz.log, and the broker counters must
show the forged lookups reaching the app (broker_actor_unknown +3).

    python3 docs/auth/verify_actor_guard.py --slug tega [--during-turn]
    ... --canary-no-guard   inside-gateway cases run outside it: 'person' must turn green-for-allow
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PASS = FAIL = 0


def check(label: str, ok: bool) -> bool:
    global PASS, FAIL
    PASS, FAIL = PASS + bool(ok), FAIL + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


PROBE = r'''
import sys
sys.path.insert(0, sys.argv[1])
import authz
actor = sys.argv[2] or None
try:
    key = authz.require(actor, "task.add", sys.argv[3])
    print("ALLOW", key)
except SystemExit as e:
    print("EXIT", e.code)
'''


def gateway_env(slug: str) -> dict:
    pid = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", f"{slug}-gateway.service"],
                         capture_output=True, text=True).stdout.strip()
    raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    env = dict(kv.decode().split("=", 1) for kv in raw if b"=" in kv)
    keep = ("PATH", "HOME", "HERMES_HOME", "JARVIS_APP_BROKER_URL", "JARVIS_SKILLS_ROOT",
            "JARVIS_IDENTITY_CONFIG", "JARVIS_RUNTIME_CONFIG", "JARVIS_TASK_ENFORCEMENT")
    return {k: env[k] for k in keep if k in env}


def probe(slug: str, base_env: dict, actor: str, ref: str, target: str, in_gateway: bool) -> str:
    shared = f"/opt/{slug}/skills/openclaw-imports/_shared"
    env = dict(base_env)
    if ref:
        env["JARVIS_ACTOR_MSG"] = ref
    inner = ["/usr/sbin/runuser", "-u", f"jarvis-{slug}-agent", "--", "env", "-i",
             *[f"{k}={v}" for k, v in env.items()], "/opt/jarvis/venv/bin/python", "-c", PROBE,
             shared, actor, target]
    if in_gateway:
        procs = f"/sys/fs/cgroup/system.slice/{slug}-gateway.service/cgroup.procs"
        cmd = ["/bin/sh", "-c", f'echo $$ > {procs} && exec "$@"', "sh", *inner]
    else:
        cmd = inner
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd="/")
    return (r.stdout.strip().splitlines() or [f"rc={r.returncode} {r.stderr.strip()[-200:]}"])[-1]


def counter(slug: str, name: str) -> int:
    c = sqlite3.connect(f"file:/opt/{slug}/app-private/jarvis_threads.db?mode=ro", uri=True)
    try:
        row = c.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
        return int(row[0]) if row else 0
    finally:
        c.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--during-turn", action="store_true",
                    help="wait for an app chat turn to be running before the system case")
    ap.add_argument("--canary-no-guard", action="store_true")
    a = ap.parse_args()
    if os.geteuid() != 0:
        return print("run as root") or 2
    slug = a.slug
    if slug in ("live", "jarvis") or not Path(f"/opt/{slug}/app-private").is_dir():
        return print(f"/opt/{slug} is not a two-user tenant") or 2
    ident = json.loads(Path(f"/opt/{slug}/skills/openclaw-imports/_shared/identity.json").read_text())
    owner_email = ident["people"]["owner"]["email"][0]
    assistant = ident["assistant_address"]
    base = gateway_env(slug)
    check(f"the agent's broker is on ({'JARVIS_APP_BROKER_URL' in base})", "JARVIS_APP_BROKER_URL" in base)
    log = Path(f"/opt/{slug}/skills/openclaw-imports/task-manager/data/authz.log")
    n0 = len(log.read_text().splitlines()) if log.exists() else 0
    u0 = counter(slug, "broker_actor_unknown")
    tag = f"verify-actor-guard-{secrets.token_hex(3)}"
    inside = not a.canary_no_guard

    print("Inside the gateway" + (" (CANARY: run outside it)" if not inside else ""))
    out = probe(slug, base, "", "app:" + secrets.token_urlsafe(24), f"{tag}-forged", inside)
    check(f"   forged app reference -> denied ({out})", out == "EXIT 2")
    out = probe(slug, base, "", f"app:{owner_email}", f"{tag}-emailref", inside)
    check(f"   an email in place of a reference -> denied ({out})", out == "EXIT 2")
    out = probe(slug, base, owner_email, "", f"{tag}-person", inside)
    check(f"   no reference, --actor {owner_email} -> denied ({out})", out == "EXIT 2")
    if a.during_turn:
        deadline, active = time.time() + 120, 0
        while time.time() < deadline and not active:
            r = subprocess.run(["/usr/sbin/runuser", "-u", f"jarvis-{slug}-agent", "--", "env", "-i",
                                *[f"{k}={v}" for k, v in base.items()], "/opt/jarvis/venv/bin/python", "-c",
                                "import sys;sys.path.insert(0,sys.argv[1]);import app_broker;"
                                "print(app_broker.call('actor/active').get('active'))",
                                f"/opt/{slug}/skills/openclaw-imports/_shared"],
                               capture_output=True, text=True, cwd="/")
            active = int((r.stdout.strip() or "0"))
            if not active:
                time.sleep(0.5)
        check(f"   an app chat turn is running (active={active})", active > 0)
        out = probe(slug, base, assistant, "", f"{tag}-system", inside)
        check(f"   no reference, --actor {assistant} during the turn -> denied ({out})", out == "EXIT 2")

    print("Outside the gateway (presence twins)")
    out = probe(slug, base, owner_email, "", f"{tag}-cli-person", False)
    check(f"   --actor {owner_email} from a CLI -> allowed as before ({out})", out == "ALLOW owner")
    out = probe(slug, base, "", "app:" + secrets.token_urlsafe(24), f"{tag}-cli-forged", False)
    check(f"   forged app reference from a CLI -> denied ({out})", out == "EXIT 2")

    lines = [ln for ln in log.read_text().splitlines()[n0:] if tag in ln]
    denies = [ln for ln in lines if "| DENY |" in ln]
    expect = 4 + (1 if a.during_turn else 0) if inside else None
    if inside:
        check(f"every refusal is in authz.log ({len(denies)} DENY lines, expected {expect})", len(denies) == expect)
    du = counter(slug, "broker_actor_unknown") - u0
    check(f"the forged lookups reached the app: broker_actor_unknown +{du} (expected 3)", du == 3)
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
