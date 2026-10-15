#!/usr/bin/env python3
"""Mutation check for the operator lead-write gate.

Three mutations, one per layer, because the claim has three independent parts
and a suite that only catches one of them is not testing the rule:

  1. require_person stops raising          -> the API gate itself
  2. main.py stops calling it on PATCH     -> a single unguarded route
  3. the UI's canEdit drops the person test -> the reflection

Number 2 matters most: it is the realistic regression. Someone adds a fourth
lead route and forgets the line, and a suite that only mutates the helper would
stay green.

Restores every file in a finally block and rebuilds, so a crash leaves the box
as it found it.
"""
import sys
sys.exit("REFUSING: this script works on live (/root/jarvis-app or /root/.hermes); port it to the platform tree (VERIFY_APP_ROOT, VERIFY_SKILLS_ROOT) before running it")
import subprocess, sys, time
from pathlib import Path

APP = Path("/root/jarvis-app/app")
FE = Path("/root/jarvis-app/frontend/src")
SP = "/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad"
SUITE = [ "node", f"{SP}/verify/verifyLeadsUI.mjs",
          "--store", "http://127.0.0.1:8701", "--pins", f"{SP}/pins.json" ]

MUT = [
    ("app/auth.py", "the gate itself stops raising",
     '''    if person_of(sess) is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator access can look, not change. Sign in as yourself to edit leads.",
        )
    return sess''',
     "    return sess"),

    ("app/main.py", "PATCH forgets to call the gate",
     '''def patch_lead(lead_id: str, body: LeadPatch, sess: dict = Depends(auth.current_session)):
    auth.require_person(sess)''',
     '''def patch_lead(lead_id: str, body: LeadPatch, sess: dict = Depends(auth.current_session)):'''),

    # NOTE the `void personKey`. Simply deleting the clause leaves the prop
    # unused, which is a TS6133 COMPILE ERROR, not a behaviour change — the
    # first version of this mutation crashed the build and proved nothing
    # about the assertions. A mutation has to produce a WORKING program that
    # is wrong, or it is testing the compiler rather than the suite.
    ("frontend/src/screens/Leads.tsx", "the UI stops reflecting it",
     "  const canEdit = data?.source === 'store' && personKey !== null",
     "  void personKey\n  const canEdit = data?.source === 'store'"),
]

ROOT = Path("/root/jarvis-app")


def rebuild(frontend: bool):
    if frontend:
        r = subprocess.run(["npm", "run", "build"], cwd=ROOT / "frontend",
                           capture_output=True, text=True)
        if r.returncode != 0:
            # Not a kill. A mutation that will not compile tested nothing.
            print("    (build FAILED — mutation is invalid, not killed)")
            print("    " + (r.stdout or r.stderr).strip().splitlines()[-1][:100])
            return False
    subprocess.run(["systemctl", "restart", "jarvis-app"], check=True)
    subprocess.run([f"{SP}/w3_sandbox.sh", "stop"], capture_output=True)
    r = subprocess.run([f"{SP}/w3_sandbox.sh", "start"], capture_output=True, text=True)
    return "sandbox up" in r.stdout


def run():
    r = subprocess.run(SUITE, capture_output=True, text=True)
    return r.returncode == 0, r.stdout


def main():
    backups = {f: (ROOT / f).read_text() for f, _, _, _ in MUT}
    survivors, killed = [], []
    try:
        assert rebuild(False), "sandbox did not start"
        green, _ = run()
        print(f"baseline: {'GREEN' if green else 'RED'}")
        if not green:
            print("ABORT — baseline red, mutations meaningless.")
            return 2

        for fname, name, old, new in MUT:
            path = ROOT / fname
            src = path.read_text()
            if old not in src:
                print(f"  SKIP  {name} — anchor missing (test is stale!)")
                survivors.append(name + " [ANCHOR MISSING]")
                continue
            path.write_text(src.replace(old, new, 1))
            up = rebuild(fname.startswith("frontend"))
            ok, out = run() if up else (False, "sandbox failed")
            path.write_text(backups[fname])
            if ok:
                print(f"  SURVIVED  {name}")
                survivors.append(name)
            else:
                red = [l.strip()[6:].split(" — ")[0] for l in out.splitlines() if "FAIL" in l]
                print(f"  killed    {name}")
                for r_ in red[:4]:
                    print(f"              red: {r_[:72]}")
    finally:
        for f, src in backups.items():
            (ROOT / f).write_text(src)
        rebuild(True)

    print(f"\n{len(killed) if killed else len(MUT)-len(survivors)}/{len(MUT)} mutations killed")
    if survivors:
        print("SURVIVORS:")
        for s in survivors:
            print("  - " + s)
        return 1
    final, _ = run()
    print(f"restored baseline: {'GREEN' if final else 'RED'}")
    return 0 if final else 1


sys.exit(main())
