#!/usr/bin/env python3
"""Mutation check for the dedupe — the part the brief called the hard part.

Six mutations. Each removes one rule the dedupe depends on, and the suite must
go red on the assertion ABOUT THAT RULE. Uses --stub-classify: the classifier
is not under test here and a real call per email per mutation is pure cost.
"""
import os, subprocess, sys
from pathlib import Path

sys.exit("REFUSING: this canary mutates files under /root/.hermes (live) and runs a stale "
         "scratchpad suite; port it to the platform skills tree before using it.")

SHARED = Path("/root/.hermes/skills/openclaw-imports/_shared")
APP = Path(os.environ.get("VERIFY_APP_ROOT") or str(Path(__file__).resolve().parents[2])) / "app"
SP = "/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad"
SUITE = ["/opt/jarvis/venv/bin/python", f"{SP}/leadcap/verify_lead_capture.py",
         "--stub-classify"]

MUT = [
    (SHARED / "lead_capture.py", "dedupe never looks anything up (always creates)",
     '''    if addr:
        hits = ls.find_by_email(addr)
        if hits:
            return hits[0], "email"''',
     '''    if addr:
        pass'''),

    (SHARED / "lead_capture.py", "name fallback removed (portal relays duplicate)",
     '''    if name:
        hits = ls.find_by_name(name)
        if hits:
            return hits[0], "name"''',
     '''    if name:
        pass'''),

    (SHARED / "lead_capture.py", "collisions merge instead of flagging",
     "    return not (old in new or new in old)",
     "    return False"),

    # NOTE: removing the `old == new` early return is an EQUIVALENT MUTANT —
    # when the names are equal, `old in new` is true anyway, so the containment
    # test already returns False. It survived, correctly, and proved nothing.
    # The real mutation of this rule drops the containment test itself, which
    # is what turns a partner being added into a flagged collision.
    (SHARED / "lead_capture.py", "collision check too eager (partners flagged)",
     "    return not (old in new or new in old)",
     "    return old != new"),

    (SHARED / "lead_capture.py", "the lead-shaped test is dropped (every A is a lead)",
     '''    if source != "email":
        return True, source, f"recognised lead source: {source}"
    if _INQUIRY.search(blob):
        return True, "email", "inquiry language in subject/body"
    return False, "", "external, but no lead source or inquiry language"''',
     '''    return True, source, "category A"'''),

    (APP / "leads_store.py", "name normalisation stops normalising",
     '    return " ".join(n.split())',
     '    return (name or "")'),
]


def run():
    r = subprocess.run(SUITE, capture_output=True, text=True)
    return r.returncode == 0, r.stdout


def main():
    backups = {p: p.read_text() for p, _, _, _ in MUT}
    survivors = []
    try:
        green, _ = run()
        print(f"baseline: {'GREEN' if green else 'RED'}")
        if not green:
            print("ABORT — baseline red.")
            return 2
        for path, name, old, new in MUT:
            src = path.read_text()
            if old not in src:
                print(f"  SKIP  {name} — anchor missing (test is stale!)")
                survivors.append(name + " [ANCHOR MISSING]")
                continue
            path.write_text(src.replace(old, new, 1))
            ok, out = run()
            path.write_text(backups[path])
            if ok:
                print(f"  SURVIVED  {name}")
                survivors.append(name)
            else:
                red = [l.strip()[6:].split(" — ")[0] for l in out.splitlines() if "FAIL" in l]
                print(f"  killed    {name}")
                for r_ in red[:3]:
                    print(f"              red: {r_[:70]}")
                if len(red) > 3:
                    print(f"              ... and {len(red)-3} more")
    finally:
        for path, src in backups.items():
            path.write_text(src)

    print(f"\n{len(MUT)-len(survivors)}/{len(MUT)} mutations killed")
    if survivors:
        print("SURVIVORS (untested rules):")
        for s in survivors:
            print("  - " + s)
        return 1
    final, _ = run()
    print(f"restored baseline: {'GREEN' if final else 'RED'}")
    return 0 if final else 1


sys.exit(main())
