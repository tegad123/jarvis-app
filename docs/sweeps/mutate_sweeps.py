#!/usr/bin/env python3
"""Mutation harness for the sweeps' staleness predicates and shared guards.

The sweeps suite went 119/119 on its second run. A suite that is green is not
yet a suite that can be red. Each mutation below deletes exactly one predicate
or guard in the sweep code and the suite must fail for it; the GUARD mutation
plants a direct transport call and the send-path inventory must go red.

RULES (each from a round that went wrong before):
  * a red baseline aborts;
  * a mutant must COMPILE — a syntax error tests the parser, reported INVALID;
  * a missing anchor is a loud SKIP, and counts as a failure;
  * fresh box per mutant — a mutated run leaves corrupt state behind;
  * if every kill shares one first failure, the harness is suspect.

Run:  python3 docs/sweeps/mutate_sweeps.py
"""

import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path(os.environ.get("VERIFY_APP_ROOT") or str(Path(__file__).resolve().parents[2]))
SKILLS = Path("/opt/jarvis-verify-fixture/skills/openclaw-imports")
DOCS = APP / "docs" / "sweeps"
PYBIN = str(APP / ".venv" / "bin" / "python")

CORE = SKILLS / "sweeps" / "scripts" / "sweep_core.py"
STALE = SKILLS / "sweeps" / "scripts" / "stale_email.py"
FOLLOW = SKILLS / "sweeps" / "scripts" / "task_followup.py"
LEAD = SKILLS / "sweeps" / "scripts" / "quiet_lead.py"

SWEEP = "sweep"
GUARD = "guard"

MUTATIONS = [
    ("stale_email: the window is ignored", STALE,
     "        if last_ts is None or last_ts >= cutoff:\n            continue",
     "        if last_ts is None:\n            continue", SWEEP),
    ("stale_email: answered threads convert too", STALE,
     '        if not last or last["role"] != "user":\n            continue',
     "        if not last:\n            continue", SWEEP),
    ("stale_email: the category exclusion is dropped", STALE,
     "        if cat and cat[0] in _EXCLUDE:",
     "        if False:", SWEEP),
    ("stale_email: already-converted check dropped", STALE,
     '        if tasks_store.find_by_source_ref("email", t["thread_id"]):',
     "        if False:", SWEEP),
    ("task_followup: the quiet predicate is inverted", FOLLOW,
     "        if age < quiet_after:",
     "        if age >= quiet_after:", SWEEP),
    ("task_followup: already_nudged is dropped", FOLLOW,
     '        if already_nudged(SWEEP, t["task_id"], "nudge", window):\n            continue',
     "        pass", SWEEP),
    ("task_followup: the unknown assignee becomes a silent skip", FOLLOW,
     '            unresolvable(SWEEP, f"task {t[\'task_id\']}", t["title"], t["assignee"])',
     "            pass", SWEEP),
    ("task_followup: revival is dropped", FOLLOW,
     '            if t["stale"] and not ctx.dry_run:\n                ts.set_stale(t["task_id"], False, by="jarvis")',
     "            pass", SWEEP),
    ("task_followup: escalation never fires", FOLLOW,
     "        if n >= esc_after:",
     "        if False:", SWEEP),
    ("quiet_lead: the threshold is ignored", LEAD,
     "        if days_quiet < threshold:\n            continue",
     "        pass", SWEEP),
    ("quiet_lead: hot leads wait as long as warm ones", LEAD,
     '        threshold = hot if (L.get("temperature") or "").strip() == "Hot" else qd',
     "        threshold = qd", SWEEP),
    ("quiet_lead: a scheduled follow-up no longer counts as a plan", LEAD,
     "        if nta and nta > today.isoformat():\n            continue   # a scheduled follow-up is a plan, not silence",
     "        pass", SWEEP),
    ("quiet_lead: already_nudged is dropped", LEAD,
     '        if already_nudged(SWEEP, L["lead_id"], "nudge", window):\n            continue',
     "        pass", SWEEP),
    ("core: the internal-domain refusal is dropped", CORE,
     "    if not _is_internal(addr):",
     "    if False:", SWEEP),
    ("core: masking is bypassed", CORE,
     "        notify.notify(destination, headline, headline=mask_text(headline),\n                      detail=mask_text(detail), kind=kind, skill=SKILL, attachments=atts)",
     "        notify.notify(destination, headline, headline=headline,\n                      detail=detail, kind=kind, skill=SKILL, attachments=atts)", SWEEP),
    ("core: the heartbeat is suppressed", CORE,
     '        announce("ops", head, detail=detail, kind="info")\n        app_counter("sweep_runs")',
     '        app_counter("sweep_runs")', SWEEP),
    ("core: a failure goes silent", CORE,
     '        announce("ops", head, detail=detail, kind="alert")\n        app_counter("sweep_failures")',
     '        app_counter("sweep_failures")', SWEEP),
    ("core: nothing is counted", CORE,
     "def app_counter(name: str, by: int = 1) -> None:\n    app_counters.bump(name, by)",
     "def app_counter(name: str, by: int = 1) -> None:\n    return None", SWEEP),
    ("guard: a sweep grows a direct transport call", LEAD,
     'SWEEP = "quiet_lead"',
     'SWEEP = "quiet_lead"\nimport smtplib\n_direct = smtplib.SMTP', GUARD),
]


def run_suite(which, box):
    env = dict(os.environ)
    if which == GUARD:
        env["JARVIS_SKILLS_ROOT"] = str(SKILLS)
        script = SKILLS / "owner-email" / "tests" / "verify_send_paths.py"
    else:
        env["TASKS_BOX"] = str(box)
        script = DOCS / "verify_sweeps.py"
    p = subprocess.run([PYBIN, str(script)], capture_output=True, text=True, env=env, timeout=900)
    out = p.stdout or ""
    fails = [l.strip() for l in out.splitlines() if l.strip().startswith("FAIL")]
    return p.returncode, fails, out


def main() -> int:
    print("mutation harness — every mutation MUST be killed\n")
    originals = {f: f.read_text() for f in {m[1] for m in MUTATIONS}}
    box = Path(tempfile.mkdtemp(prefix="sw-mut-"))
    try:
        rc_s, f_s, out_s = run_suite(SWEEP, box)
        rc_g, f_g, out_g = run_suite(GUARD, box)
        if rc_s != 0 or rc_g != 0:
            print("ABORT — baseline is red, mutations would be meaningless.")
            for line in (f_s + f_g)[:6]:
                print("   " + line)
            return 1
        n_s = len([l for l in out_s.splitlines() if l.strip().startswith("PASS")])
        n_g = len([l for l in out_g.splitlines() if l.strip().startswith("PASS")])
        print(f"baseline: GREEN (sweeps {n_s}, guard {n_g})\n")

        killed = survived = skipped = invalid = 0
        first = []
        for label, path, old, new, which in MUTATIONS:
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
                rc, fails, _ = run_suite(which, box)
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
