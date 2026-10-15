#!/usr/bin/env python3
"""MUTATION TEST for the W3 scoping.

The assertion IS the deliverable: a scoping suite that passes is worthless
unless it FAILS when the scoping is removed. Each mutation below deletes one
rule — a WHERE clause, a gate, a filter — and the suite must go red. A mutation
that survives means that rule is untested, whatever the green run said.

Restores every file in a finally block and restarts the service, so a crash
mid-run leaves the box as it found it.
"""
import sys
sys.exit("REFUSING: this script works on live (/root/jarvis-app or /root/.hermes); port it to the platform tree (VERIFY_APP_ROOT, VERIFY_SKILLS_ROOT) before running it")
import shutil, subprocess, sys, time
from pathlib import Path

APP = Path("/root/jarvis-app/app")
SP = "/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad"
SUITE = f"{SP}/verify_w3_scope.py"
SANDBOX = f"{SP}/w3_sandbox.sh"

MUTATIONS = [
    ("threads.py: drop the membership WHERE clause",
     '''            "(direct_for = ? OR (direct_for IS NULL AND thread_id IN "
            " (SELECT thread_id FROM thread_members WHERE person_key = ?)))"
        )
        args += [viewer, viewer]''',
     '''            "(direct_for = ? OR direct_for IS NULL)"
        )
        args += [viewer]'''),

    ("threads.py: can_see stops checking membership",
     "    return unscoped or is_member(thread_id, viewer)",
     "    return True"),

    ("threads.py: unscoped sessions get everyone's direct line back",
     '        where.append("(direct_for IS NULL OR direct_for = ?)")\n        args.append(viewer)',
     '        where.append("(1 = 1 OR direct_for = ?)")\n        args.append(viewer)'),

    ("feed.py: scope() stops filtering",
     "    if viewer is None:\n        return events\n    return [e for e in events if visible_to(e, viewer)]",
     "    return events"),

    ("feed.py: unattributed events become team-general (default-ALLOW)",
     "    return viewer in owner_keys(event)",
     "    keys = owner_keys(event)\n    return (not keys) or viewer in keys"),

    ("leads_source.py: drop the owner_person filter",
     "    rows = leads_store.query(owner_person=viewer)",
     "    rows = leads_store.query()"),

    ("auth.py: require_bodies becomes a no-op (operator reads messages)",
     '''    if is_operator(sess):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator access sees channels and membership, not message "
                   "content. Reading messages needs the owner to sign in.",
        )
    return sess''',
     "    return sess"),

    ("auth.py: require_owner accepts anyone",
     '''    if role_of(sess) != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the account owner can do that.",
        )
    return sess''',
     "    return sess"),

    ("auth.py: require_admin accepts anyone",
     '''    if role_of(sess) not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin or the owner can manage members.",
        )
    return sess''',
     "    return sess"),

    ("main.py: the list stops suppressing previews for an operator",
     "    bodies = not auth.is_operator(sess)",
     "    bodies = True"),
]

def restart():
    """A FRESH sandbox per mutation — never the live service.

    Fresh, not merely restarted: a mutation that removes a guard lets the
    suite's negative probes land as real writes, so the databases a mutated
    run leaves behind are corrupt by construction. Reusing them would make
    every later mutation fail on the wreckage of an earlier one and report a
    kill it did not earn. That is exactly what the first version of this file
    did — ten green kills, seven of them false.
    """
    subprocess.run([SANDBOX, "stop"], capture_output=True)
    r = subprocess.run([SANDBOX, "start"], capture_output=True, text=True)
    return "sandbox up" in r.stdout

def suite_passes():
    r = subprocess.run([sys.executable, SUITE], capture_output=True, text=True)
    return r.returncode == 0, r.stdout

def main():
    backups = {}
    survivors, killed = [], []
    try:
        # Sanity: the suite must be GREEN before any mutation, or "it went red"
        # proves nothing.
        assert restart(), "service did not come up"
        green, _ = suite_passes()
        print(f"baseline: {'GREEN' if green else 'RED'}")
        if not green:
            print("ABORT — baseline is red, mutations would be meaningless.")
            return 2

        for name, old, new in MUTATIONS:
            fname = name.split(":")[0]
            path = APP / fname
            src = path.read_text()
            if fname not in backups:
                backups[fname] = src
            if old not in src:
                print(f"  SKIP  {name} — anchor not found (test is stale!)")
                survivors.append(name + " [ANCHOR MISSING]")
                continue
            path.write_text(src.replace(old, new, 1))
            up = restart()
            ok, out = (suite_passes() if up else (False, "service failed to start"))
            path.write_text(backups[fname])
            if ok:
                print(f"  SURVIVED  {name}")
                survivors.append(name)
            else:
                red = [l.strip()[6:].split(" — ")[0] for l in out.splitlines() if "FAIL" in l]
                print(f"  killed    {name}")
                for r in red[:4]:
                    print(f"              red: {r[:78]}")
                if len(red) > 4:
                    print(f"              ... and {len(red)-4} more")
                killed.append(name)
    finally:
        for fname, src in backups.items():
            (APP / fname).write_text(src)
        restart()
        subprocess.run(["systemctl","restart","jarvis-app"], capture_output=True)

    print(f"\n{len(killed)}/{len(MUTATIONS)} mutations killed")
    if survivors:
        print("SURVIVORS (untested rules):")
        for s in survivors: print("  - " + s)
        return 1
    # And the tree must be exactly as we found it.
    final, _ = suite_passes()
    print(f"restored baseline: {'GREEN' if final else 'RED'}")
    return 0 if final else 1

sys.exit(main())
