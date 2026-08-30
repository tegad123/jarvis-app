"""The box the task harnesses run inside.

SETTING AN ENV VAR PROVES NOTHING; THE MODULES ARE ASKED. apply() sets every
store path BEFORE the first import, because these modules resolve their paths
at import time; assert_isolated() then re-reads the RESOLVED value back out of
each module and refuses to run if any of them escaped. That check exists
because isolating the database is not isolating the side effects — the task
store emits feed events, and a harness that redirects only the DB writes its
fake tasks into the customer's real feed.

Pointed at TESTCO's config, not the live tenant's: tasks are an app-native
feature and 34dev is sheet-backed, so a harness aimed there would exercise the
legacy branch and pass by testing nothing.
"""

import os
import sys
from pathlib import Path

BOX = Path(os.environ.get("TASKS_BOX", "/tmp/claude-0/-root/tasks-box"))
TESTCO_HOME = Path("/opt/testco/hermes")
TESTCO_SKILLS = Path("/opt/testco/skills/openclaw-imports")
APP = Path("/root/jarvis-app")

FORBIDDEN = (Path("/root/.hermes/state"), TESTCO_HOME / "state",
             TESTCO_SKILLS / "task-manager" / "data")


def apply() -> None:
    BOX.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "HERMES_HOME": str(TESTCO_HOME),
        "JARVIS_SKILLS_ROOT": str(TESTCO_SKILLS),
        "JARVIS_RUNTIME_CONFIG": str(TESTCO_SKILLS / "_shared" / "runtime.json"),
        "JARVIS_IDENTITY_CONFIG": str(TESTCO_SKILLS / "_shared" / "identity.json"),
        "JARVIS_TASKS_DB": str(BOX / "tasks.db"),
        "JARVIS_THREADS_DB": str(BOX / "threads.db"),
        "JARVIS_LEADS_DB": str(BOX / "leads.db"),
        "JARVIS_ACTIVITY_FEED": str(BOX / "feed.jsonl"),
        "JARVIS_APPROVALS": str(BOX / "approvals.jsonl"),
        "JARVIS_TASKS_SOURCE": "store",
        # Tasks are OUR store, not a shared surface, so the guard is not what
        # gates them — but the digest mails, and that must stay armed only
        # where the harness means it to be.
        "JARVIS_ALLOW_WRITES": os.environ.get("JARVIS_ALLOW_WRITES", "1"),
    })
    for p in (str(APP), str(TESTCO_SKILLS / "_shared")):
        if p not in sys.path:
            sys.path.insert(0, p)


def assert_isolated() -> dict:
    from app import tasks_store
    import notify
    resolved = {
        "tasks_store.DB_PATH": Path(tasks_store.DB_PATH),
        "notify.feed_path()": Path(notify.feed_path()),
    }
    try:
        from app import threads
        resolved["threads.DB_PATH"] = Path(threads.DB_PATH)
    except Exception:
        pass
    bad = []
    for name, path in resolved.items():
        rp = str(path.resolve())
        if any(rp.startswith(str(f.resolve())) for f in FORBIDDEN):
            bad.append(f"{name} -> {path}")
        elif not rp.startswith(str(BOX.resolve())):
            bad.append(f"{name} -> {path} (outside the box)")
    if bad:
        sys.exit("REFUSING: paths escaped the box:\n  " + "\n  ".join(bad))
    return resolved


def reset() -> None:
    """Empty the box's stores between sections, so 'one new event' means one."""
    for name in ("tasks.db", "feed.jsonl", "threads.db", "leads.db", "sweeps.db"):
        p = BOX / name
        if p.exists():
            p.unlink()
        for suffix in ("-wal", "-shm"):
            q = BOX / (name + suffix)
            if q.exists():
                q.unlink()
