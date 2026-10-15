#!/usr/bin/env python3
"""Mutation harness for PROMPT 6: counters, measurements, the seams, the fold.

The suite went 60/60 on its third run. A suite that has never been red has not
been shown capable of red. Each mutation removes exactly one guard or flips one
predicate; verify_metrics.py (--no-live: the live role check cannot see a
mutated file, so the gate is asserted in-process instead) must go red for it.

RULES (each from a round that went wrong before): a red baseline aborts; a
mutant must COMPILE or it is INVALID; a missing anchor is a loud SKIP that
counts as failure; fresh box per mutant; identical first failures across every
kill mean the harness is broken, not that it caught bugs.

Run:  /opt/jarvis/venv/bin/python docs/metrics/mutate_metrics.py
"""

import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path(os.environ.get("VERIFY_APP_ROOT") or str(Path(__file__).resolve().parents[2]))
SK = Path("/opt/jarvis-verify-fixture/skills/openclaw-imports")
PYBIN = str(APP / ".venv" / "bin" / "python")
SUITE = APP / "docs" / "metrics" / "verify_metrics.py"

METRICS = APP / "app" / "metrics.py"
THREADS = APP / "app" / "threads.py"
STORE = APP / "app" / "tasks_store.py"
MAIN = APP / "app" / "main.py"
HANDLER = SK / "showingsmart-autoreply" / "scripts" / "handle_showing_request.py"
QUIET = SK / "sweeps" / "scripts" / "quiet_lead.py"
POLLER = SK / "gmail-push-trigger" / "scripts" / "poll_gmail_history.py"
MONTHLY = SK / "sweeps" / "scripts" / "monthly_report.py"

MUTATIONS = [
    ("store: a bump no longer lands in the daily bucket", THREADS,
     "        metrics.bump_day(name, by)\n", "        pass\n"),
    ("store: an address handed as ref is stored", METRICS,
     '        if ref and "@" in str(ref):\n', '        if False and "@" in str(ref):\n'),
    ("store: retention never prunes", METRICS,
     "    cutoff = (datetime.now(_tz()).date() - timedelta(days=retention_days())).isoformat()",
     "    cutoff = (datetime.now(_tz()).date() - timedelta(days=retention_days() * 100)).isoformat()"),
    ("store: the failure announces on EVERY bump", METRICS,
     "    if _announced_failure:\n        return\n", "    if False:\n        return\n"),
    ("deadlines: late and on-time are swapped", STORE,
     "                elif now[:10] <= dl:\n", "                elif now[:10] > dl:\n"),
    ("latency: a payload with no received-at is given a number anyway", HANDLER,
     '        secs = _latency_seconds(payload.get("received_at"))\n',
     '        secs = _latency_seconds(payload.get("received_at")) or 95\n'),
    ("leads: every touch after a nudge counts as revived, however late", QUIET,
     '        core.app_counter("leads_revived_after_nudge" if hours <= horizon\n',
     '        core.app_counter("leads_revived_after_nudge" if True\n'),
    ("leads: a revival is counted again on every sweep", QUIET,
     '        if core.already_nudged(SWEEP, n["target"], "revived", verdict_key):\n            continue\n',
     '        if False:\n            continue\n'),
    ("digest: the reply path ignores the app store", POLLER,
     "        rows = at.digest_positions(rfc_message_id)\n        if rows:\n            return rows\n",
     "        rows = at.digest_positions(rfc_message_id)\n        if False:\n            return rows\n"),
    ("digest: a replayed reply is acted on twice", POLLER,
     "    if _at is not None and not _at.claim_digest_reply(reply_msg_id):\n",
     "    if _at is not None and False:\n"),
    ("digest: a store task's priority change goes to the legacy script", POLLER,
     '            if _at is not None and str(task_id).startswith("tk_"):\n',
     '            if False:\n'),
    ("summary: a member can read the whole team's numbers", MAIN,
     "    if not (auth.sees_everything(sess) or auth.is_operator(sess)):\n        raise HTTPException(status_code=403",
     "    if False:\n        raise HTTPException(status_code=403"),
    ("monthly: the same month posts every day", MONTHLY,
     '    if not a.now and st.get("reported") == key:\n', '    if False:\n'),
]


def run_suite(box):
    env = dict(os.environ, TASKS_BOX=str(box))
    p = subprocess.run([PYBIN, str(SUITE), "--no-live"], capture_output=True, text=True,
                       env=env, timeout=900, cwd=str(APP))
    out = p.stdout or ""
    fails = [l.strip() for l in out.splitlines() if l.strip().startswith("FAIL")]
    return p.returncode, fails, out


def main() -> int:
    print("mutation harness — every mutation MUST be killed\n")
    originals = {f: f.read_text() for f in {m[1] for m in MUTATIONS}}
    box = Path(tempfile.mkdtemp(prefix="metrics-mut-"))
    try:
        rc, fails, out = run_suite(box)
        if rc != 0:
            print("ABORT — baseline is red, mutations would be meaningless.")
            for line in fails[:6]:
                print("   " + line)
            return 1
        n = len([l for l in out.splitlines() if l.strip().startswith("PASS")])
        print(f"baseline: GREEN ({n} passed, --no-live)\n")

        killed = survived = skipped = invalid = 0
        first = []
        for label, path, old, new in MUTATIONS:
            src = originals[path]
            if old not in src:
                print(f"  SKIP      {label}\n              anchor not found in {path.name} — the "
                      f"source moved and this mutation is testing nothing")
                skipped += 1
                continue
            path.write_text(src.replace(old, new, 1))
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                print(f"  INVALID   {label}\n              mutant does not compile "
                      f"({str(exc).splitlines()[0][:80]})")
                invalid += 1
                path.write_text(src)
                continue
            try:
                shutil.rmtree(box, ignore_errors=True); box.mkdir(parents=True)
                rc, fails, _ = run_suite(box)
            finally:
                path.write_text(src)
            if rc != 0:
                print(f"  killed    {label}")
                for f in fails[:2]:
                    print(f"              red: {f[:92]}")
                first.append(fails[0][:92] if fails else "(no FAIL line)")
                killed += 1
            else:
                print(f"  SURVIVED  {label}  <-- the suite did NOT catch this")
                survived += 1

        total = len(MUTATIONS)
        print(f"\n{killed}/{total} killed"
              + (f", {survived} survived" if survived else "")
              + (f", {skipped} skipped" if skipped else "")
              + (f", {invalid} invalid" if invalid else ""))
        if killed >= 3 and len(set(first)) == 1:
            print(f"HARNESS SUSPECT — every kill reported the same first failure ({first[0]}).")
            return 1
        if killed >= 3:
            print(f"harness sanity: {len(set(first))} distinct first-failures across {killed} kills")
        return 0 if (survived == skipped == invalid == 0 and killed == total) else 1
    finally:
        for f, text in originals.items():
            f.write_text(text)
        shutil.rmtree(box, ignore_errors=True)
        print("restored every mutated file")


if __name__ == "__main__":
    sys.exit(main())
