#!/usr/bin/env python3
"""Mutation harness for the tasks permission checks and the digest query.

The API suite passed 63/63 and the digest suite 30/30 on their first complete
runs. A suite that has never been red has not been shown to be CAPABLE of red.
Each mutation below deletes exactly one guard, and the suite must fail for it.

RULES, EACH FROM A ROUND THAT WENT WRONG BEFORE:
  * A red baseline aborts — mutations against an already-failing suite say
    nothing about the mutations.
  * A MUTANT MUST COMPILE. A rewrite that leaves a syntax error kills the run
    rather than the bug, which tests the parser; that is reported INVALID.
  * A missing anchor is a SKIP, loudly: the source moved and this mutation has
    silently been testing nothing.
  * FRESH SANDBOX PER MUTANT. A mutated run leaves rows behind that are corrupt
    by construction, and the next mutant inherits them.
  * If every kill reports the SAME first failure, the harness is suspect — that
    is a broken rig failing identically regardless of mutation.

Run:  python3 docs/tasks/mutate_tasks.py
"""

import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path("/root/jarvis-app")
SKILLS = Path("/opt/testco/skills/openclaw-imports")
DOCS = APP / "docs" / "tasks"
# The app venv: chat.py imports httpx, which the system interpreter lacks.
PYBIN = str(APP / ".venv" / "bin" / "python")

MAIN = APP / "app" / "main.py"
AUTH = APP / "app" / "auth.py"
STORE = APP / "app" / "tasks_store.py"
SOURCE = APP / "app" / "tasks_source.py"
DIGEST = SKILLS / "task-manager" / "scripts" / "generate_digest.py"

API = "api"
DIG = "digest"
HON = "honesty"

CHAT = APP / "app" / "chat.py"

# (label, file, old, new, which suite must go red)
MUTATIONS = [
    ("auth: personhood gate defeated", AUTH,
     '    if person_of(sess) is None:\n        raise HTTPException(\n            status_code=status.HTTP_403_FORBIDDEN,\n            detail="Operator access can look, not change. Sign in as yourself to edit leads.",\n        )\n    return sess',
     '    return sess', API),
    ("main: create stops checking for a person", MAIN,
     '    auth.require_person(sess)\n    _tasks_writable()\n    try:\n        return {"ok": True, "task": tasks_store.create(',
     '    _tasks_writable()\n    try:\n        return {"ok": True, "task": tasks_store.create(', API),
    ("auth: task-admin gate defeated", AUTH,
     '    if role_of(sess) not in ("owner", "admin"):\n        raise HTTPException(\n            status_code=status.HTTP_403_FORBIDDEN,\n            detail="Only an admin or the owner can edit, assign or archive tasks.",\n        )\n    return sess',
     '    return sess', API),
    ("main: edit no longer needs admin", MAIN,
     'def patch_task(task_id: str, body: TaskPatch, sess: dict = Depends(auth.current_session)):\n    auth.require_person(sess)\n    auth.require_task_admin(sess)',
     'def patch_task(task_id: str, body: TaskPatch, sess: dict = Depends(auth.current_session)):\n    auth.require_person(sess)', API),
    ("main: assign no longer needs admin", MAIN,
     'def assign_task(task_id: str, body: TaskAssign, sess: dict = Depends(auth.current_session)):\n    auth.require_person(sess)\n    auth.require_task_admin(sess)',
     'def assign_task(task_id: str, body: TaskAssign, sess: dict = Depends(auth.current_session)):\n    auth.require_person(sess)', API),
    ("main: archive no longer needs admin", MAIN,
     'def archive_task(task_id: str, sess: dict = Depends(auth.current_session)):\n    auth.require_person(sess)\n    auth.require_task_admin(sess)',
     'def archive_task(task_id: str, sess: dict = Depends(auth.current_session)):\n    auth.require_person(sess)', API),
    ("main: anyone may move anyone's task", MAIN,
     '    if task.get("assignee") and task["assignee"] == auth.person_of(sess):\n        return sess\n    raise HTTPException(',
     '    return sess\n    raise HTTPException(', API),
    ("main: the operator gets message bodies", MAIN,
     '    bodies = not auth.is_operator(sess)\n    return tasks_src.load(bodies=bodies',
     '    bodies = True\n    return tasks_src.load(bodies=bodies', API),
    ("source: claims bodies are visible while nulling them", SOURCE,
     '    return {"tasks": rows, "count": len(rows), "counts": tasks_store.counts(),\n            "source": "store", "bodies_visible": bodies,',
     '    return {"tasks": rows, "count": len(rows), "counts": tasks_store.counts(),\n            "source": "store", "bodies_visible": True,', API),
    ("store: the archive filter is dropped", STORE,
     'sql = ["SELECT * FROM tasks WHERE status " + ("= \'archived\'" if archived else "!= \'archived\'")]',
     'sql = ["SELECT * FROM tasks WHERE status IS NOT NULL"]', API),
    ("store: assignment stops naming who it is for", STORE,
     '        rec: Dict[str, Any] = {"type": "task", "task_id": task_id}\n        if assignee:\n            rec["assignee"] = assignee',
     '        rec: Dict[str, Any] = {"type": "task", "task_id": task_id}\n        if False:\n            rec["assignee"] = assignee', API),
    ("store: completion time is not recorded", STORE,
     "        conn.execute(\"UPDATE tasks SET status = 'done', completed_at = ?, updated_at = ? \"\n                     \"WHERE task_id = ?\", (now, now, task_id))",
     "        conn.execute(\"UPDATE tasks SET status = 'done', updated_at = ? \"\n                     \"WHERE task_id = ?\", (now, task_id))", API),
    ("store: creations are no longer counted", STORE,
     '    _bump(f"tasks_created.{source}")',
     '    pass', API),
    ("store: the digest query stops filtering by status", STORE,
     "        rows = _app_tasks.open_tasks()", None, DIG),  # see DIGEST_ONLY below
    ("digest: finished work is included", DIGEST,
     "        rows = _app_tasks.open_tasks()",
     "        rows = _app_tasks._store().query()", DIG),
    # API, not DIG: the digest re-buckets by priority into fixed sections, so
    # inverting the rank does not move it there. The list ORDER is what this
    # breaks, and that is asserted over the API.
    ("store: priority order is inverted", STORE,
     'PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}',
     'PRIORITY_RANK = {"high": 2, "medium": 1, "low": 0}', API),
    ("store: nothing is ever overdue", STORE,
     '            overdue = date.fromisoformat(deadline) < today',
     '            overdue = False', DIG),
    ("digest: the STALE marker is suppressed", DIGEST,
     "    if r['stale']:\n        marks.append('⚠️ <strong>STALE</strong>')",
     "    if False:\n        marks.append('⚠️ <strong>STALE</strong>')", DIG),
    ("digest: delivery is not counted", DIGEST,
     "    _bump_digest_counter()",
     "    pass", DIG),

    # ── the honesty seam (PROMPT 3b) ─────────────────────────────────────────
    ("chat: the turn stops checking itself", CHAT,
     "    if not _tasks_verifiable():\n        return None",
     "    if True:\n        return None", HON),
    ("chat: the receipt is a fixed string, not the row", CHAT,
     "    if len(rows) == 1:\n        r = rows[0]\n        return f\"\\u2713 Tracked: {r['title']} \\u2014 {r['task_id']}\"",
     "    if len(rows) == 1:\n        return \"\\u2713 Tracked.\"", HON),
    ("chat: a fabricated claim is passed along", CHAT,
     "    if claimed:\n        _bump(\"intent_claim_unbacked\")",
     "    if False:\n        _bump(\"intent_claim_unbacked\")", HON),
    ("chat: misses are not counted, so the hit-rate flatters", CHAT,
     '        _bump("chat_task_created" if rows else "chat_task_missed")',
     '        _bump("chat_task_created") if rows else None', HON),
    ("chat: an offer counts as a claim", CHAT,
     "    claimed = bool(_TASK_CLAIM.search(reply or \"\"))",
     "    claimed = True", HON),
]
# The 14th entry duplicates the 15th's anchor; drop it rather than ship a
# mutation that can never apply.
MUTATIONS = [m for m in MUTATIONS if m[3] is not None]


def run_suite(which: str, sandbox_box: Path):
    env = dict(os.environ)
    if which == API:
        env["TASKS_BASE"] = f"http://127.0.0.1:{os.environ.get('TASKS_PORT', '8711')}"
        env["TASKS_DB"] = str(Path(os.environ.get(
            "TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox")) / "tasks.db")
        env["TASKS_FEED"] = str(Path(os.environ.get(
            "TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox")) / "feed.jsonl")
        script = DOCS / "verify_tasks_api.py"
    elif which == HON:
        env["TASKS_BOX"] = str(sandbox_box)
        script = DOCS / "verify_chat_honesty.py"
    else:
        env["TASKS_BOX"] = str(sandbox_box)
        script = DOCS / "verify_tasks_digest.py"
    p = subprocess.run([PYBIN, str(script)], capture_output=True, text=True,
                       env=env, timeout=900)
    out = p.stdout or ""
    fails = [l.strip() for l in out.splitlines() if l.strip().startswith("FAIL")]
    return p.returncode, fails, out


def start_api_sandbox():
    subprocess.run(["bash", str(DOCS / "tasks_sandbox.sh"), "stop"],
                   capture_output=True, text=True)
    p = subprocess.run(["bash", str(DOCS / "tasks_sandbox.sh"), "start"],
                       capture_output=True, text=True, timeout=120)
    return p.returncode == 0


def main() -> int:
    print("mutation harness — every mutation MUST be killed\n")

    originals = {f: f.read_text() for f in {m[1] for m in MUTATIONS}}
    box = Path(tempfile.mkdtemp(prefix="tk-mut-"))

    try:
        if not start_api_sandbox():
            print("ABORT — could not start the API sandbox.")
            return 1
        rc_api, fails_api, out_api = run_suite(API, box)
        rc_dig, fails_dig, out_dig = run_suite(DIG, box)
        rc_hon, fails_hon, out_hon = run_suite(HON, box)
        if rc_api != 0 or rc_dig != 0 or rc_hon != 0:
            print("ABORT — baseline is red, mutations would be meaningless.")
            for line in (fails_api + fails_dig + fails_hon)[:6]:
                print("   " + line)
            return 1
        n_api = len([l for l in out_api.splitlines() if l.strip().startswith("PASS")])
        n_dig = len([l for l in out_dig.splitlines() if l.strip().startswith("PASS")])
        n_hon = len([l for l in out_hon.splitlines() if l.strip().startswith("PASS")])
        print(f"baseline: GREEN (api {n_api}, digest {n_dig}, honesty {n_hon})\n")

        killed = survived = skipped = invalid = 0
        first_fails = []

        for label, path, old, new, which in MUTATIONS:
            src = originals[path]
            if old not in src:
                print(f"  SKIP      {label}\n              anchor not found in {path.name} "
                      f"— the source moved and this mutation is testing nothing")
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
                if which == API:
                    start_api_sandbox()
                else:
                    shutil.rmtree(box, ignore_errors=True); box.mkdir(parents=True)
                rc, fails, _ = run_suite(which, box)
            finally:
                path.write_text(src)

            if rc != 0:
                print(f"  killed    {label}")
                for f in fails[:2]:
                    print(f"              red: {f[:92]}")
                first_fails.append(fails[0][:92] if fails else "(no FAIL line)")
                killed += 1
            else:
                print(f"  SURVIVED  {label}  <-- the suite did NOT catch this")
                survived += 1

        total = len(MUTATIONS)
        print(f"\n{killed}/{total} killed"
              + (f", {survived} survived" if survived else "")
              + (f", {skipped} skipped" if skipped else "")
              + (f", {invalid} invalid" if invalid else ""))

        if killed >= 3 and len(set(first_fails)) == 1:
            print(f"HARNESS SUSPECT — every kill reported the same first failure "
                  f"({first_fails[0]}). That is a broken rig, not {killed} caught bugs.")
            return 1
        if killed >= 3:
            print(f"harness sanity: {len(set(first_fails))} distinct first-failures "
                  f"across {killed} kills")

        return 0 if (survived == 0 and skipped == 0 and invalid == 0 and killed == total) else 1
    finally:
        for f, text in originals.items():
            f.write_text(text)
        shutil.rmtree(box, ignore_errors=True)
        subprocess.run(["bash", str(DOCS / "tasks_sandbox.sh"), "stop"],
                       capture_output=True, text=True)
        print("restored every mutated file")


if __name__ == "__main__":
    sys.exit(main())
