#!/usr/bin/env python3
"""The classifier's failure announcement, verified by REINTRODUCING THE BUG.

Not a mocked exception — the actual NameError that ran silently for five days.
If this suite cannot make the original defect visible, it does not prove the
window is closed.

Isolated feed; the live one is never written.
"""
import sys
sys.exit("REFUSING: this script works on live (/root/jarvis-app or /root/.hermes); port it to the platform tree (VERIFY_APP_ROOT, VERIFY_SKILLS_ROOT) before running it")
import json, os, shutil, subprocess, sys
from pathlib import Path

SP = Path("/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad")
BOX = SP / "leadcap" / "failbox"
CLS = Path("/root/.hermes/skills/openclaw-imports/email-router/scripts/classify_email.py")
FEED = BOX / "feed.jsonl"

ok = []
def ck(n, c, x=""):
    ok.append(bool(c)); print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" — {x}" if x else ""))

if BOX.exists():
    shutil.rmtree(BOX)
BOX.mkdir(parents=True)

EMAIL = json.dumps({
    "from": '"Marcus Webb" <marcus.webb@mailbox.example>',
    "subject": "Inquiry from HAR.com - 4211 Oak Forest Dr",
    "body": "Is this still available? I would like to schedule a showing.",
    "to": "", "cc": "", "message_id": "fail-test-1",
})

def run():
    env = dict(os.environ, JARVIS_ACTIVITY_FEED=str(FEED), JARVIS_DEMO_SEED="1")
    return subprocess.run(["python3", str(CLS)], input=EMAIL.encode(),
                          capture_output=True, env=env, timeout=90)

def events():
    if not FEED.exists():
        return []
    return [json.loads(l) for l in FEED.read_text().splitlines() if l.strip()]

original = CLS.read_text()
try:
    print("\n── the bug, reintroduced verbatim ─────────────────────────────")
    broken = original.replace('{"role": "system", "content": _build_system_prompt()},',
                              '{"role": "system", "content": SYSTEM_PROMPT},')
    ck("the exact original defect could be reintroduced", broken != original)
    CLS.write_text(broken)

    r = run()
    ck("the classifier still fails loudly to its caller (exit != 0)", r.returncode != 0,
       f"rc={r.returncode}")
    ck("and the traceback is the ORIGINAL NameError, not a stand-in",
       b"NameError" in r.stderr and b"SYSTEM_PROMPT" in r.stderr)

    evs = events()
    warn = [e for e in evs if e.get("kind") == "warning"]
    ck("A FEED EVENT WAS EMITTED — the window is no longer silent", len(warn) == 1,
       f"{len(evs)} events, {len(warn)} warnings")
    if warn:
        e = warn[0]
        h = e.get("headline", "")
        print(f"        headline: {h}")
        print(f"        detail:   {e.get('detail','')[:110]}")
        ck("humanised: says what happened in plain English",
           "hit a snag" in h and "filed for review" in h)
        ck("names the email it choked on, so it is actionable",
           "Oak Forest" in h or "Marcus Webb" in h)
        ck("kind is warning, so the hero counts it as needing you",
           e.get("kind") == "warning")
        ck("NO jargon in the headline — no exception class, no category letters",
           not any(t in h for t in ("NameError", "Traceback", "Category", "classify_email",
                                    "None", "{", "Exception")), h)
        ck("the technical detail IS kept, on the detail line where it belongs",
           "NameError" in (e.get("detail") or ""))
        ck("routed-anyway is stated, so nobody thinks mail was lost",
           "still routed" in (e.get("detail") or ""))

    print("\n── the fixed classifier is silent (no false alarms) ───────────")
    CLS.write_text(original)
    FEED.unlink(missing_ok=True)
    r2 = run()
    ck("classification succeeds", r2.returncode == 0, f"rc={r2.returncode}")
    ck("it classifies as A", json.loads(r2.stdout).get("classification") == "A")
    warn2 = [e for e in events() if e.get("kind") == "warning"]
    ck("NO failure event on a healthy run — this is not a chatty log",
       len(warn2) == 0, f"{len(warn2)} warnings")
    ck("the normal classification event still fires",
       any(e.get("kind") == "email.classified" for e in events()))
finally:
    CLS.write_text(original)

print("\n── the live feed was never touched ────────────────────────────")
live = sum(1 for l in open("/root/.hermes/state/activity_feed.jsonl") if l.strip())
ck("live feed still 82 events", live == 82, str(live))

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
