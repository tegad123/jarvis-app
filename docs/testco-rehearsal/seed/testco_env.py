"""Point this process at the TESTCO CLONE and nothing else.

THE ISOLATION PATTERN FROM W3, APPLIED BEFORE ANY IMPORT. Every module in the
app resolves its path AT IMPORT TIME from an env var with a fallback to
~/.hermes. So the env has to be set before `from app import ...` runs, and the
guard has to check the RESOLVED paths afterwards rather than trusting that
setting them worked.

Two lessons are baked in here:
  * PATTERN 3 — isolating the database is not isolating the side effects. Every
    store in this product EMITS, so JARVIS_ACTIVITY_FEED is set alongside the
    DBs, and so are the approvals files and the attachments directory.
  * PATTERN 5 — a disposable target, not merely an isolated one. This is a
    clone tree; nothing here writes to /root/.hermes or /root/jarvis-app/data.

The guard REFUSES rather than warns. A seeder that half-writes into the live
tenant is the exact failure the golden-image pipeline exists to prevent.
"""
import os
import sys
from pathlib import Path

# /opt/testco, NOT the scratchpad. The rehearsal ran from a session-scoped
# temp dir; /tmp is wiped at boot (`D /tmp` in tmpfiles.d), so nothing that has
# to survive a reboot can live there. The tenant moved when it became a
# standing service.
CLONE = Path("/opt/testco")
HOME = CLONE / "hermes"
SKILLS = CLONE / "skills" / "openclaw-imports"
APP = CLONE / "jarvis-app"
STATE = HOME / "state"

# Anything under these is the LIVE tenant. Touching one is a bug, not a choice.
FORBIDDEN = (Path("/root/.hermes"), Path("/root/jarvis-app/data"),
             Path("/root/jarvis-app/static"))


def apply() -> dict:
    if not CLONE.is_dir():
        sys.exit(f"REFUSING: no testco clone at {CLONE}")
    STATE.mkdir(parents=True, exist_ok=True)

    env = {
        "HERMES_HOME":              str(HOME),
        "JARVIS_SKILLS_ROOT":       str(SKILLS),
        "JARVIS_ACTIVITY_FEED":     str(STATE / "activity_feed.jsonl"),
        "JARVIS_THREADS_DB":        str(STATE / "jarvis_threads.db"),
        "JARVIS_LEADS_DB":          str(STATE / "jarvis_leads.db"),
        "JARVIS_ACCOUNTS_DB":       str(STATE / "jarvis_accounts.db"),
        "JARVIS_APPROVALS":         str(STATE / "approvals.jsonl"),
        "JARVIS_APPROVAL_DECISIONS": str(STATE / "approvals_decisions.jsonl"),
        "JARVIS_ATTACHMENTS_DIR":   str(APP / "data" / "attachments"),
        "JARVIS_IDENTITY_CONFIG":   str(SKILLS / "_shared" / "identity.json"),
        "JARVIS_RUNTIME_CONFIG":    str(SKILLS / "_shared" / "runtime.json"),
        "JARVIS_CHANNELS_CONFIG":   str(SKILLS / "_shared" / "channels.json"),
        # Every event this process writes is marked. notify.build_event applies
        # the marker centrally, so nothing downstream can mistake a seeded
        # event for a real one — including a purge audit reading the feed later.
        "JARVIS_DEMO_SEED":         "1",
        # Belt and braces: the box is dev and stays dev.
        "JARVIS_ALLOW_WRITES":      "0",
    }
    os.environ.update(env)
    sys.path.insert(0, str(APP))
    sys.path.insert(0, str(SKILLS / "_shared"))
    return env


def assert_isolated() -> None:
    """Check the RESOLVED paths, after the app modules have computed them.

    Setting an env var proves nothing on its own — a module that reads a
    different variable name, or that resolved its path at import before we got
    here, would sail straight through. So ask the modules themselves.
    """
    from app import accounts, approvals, attachments, config, leads_store, threads
    import notify

    resolved = {
        "config.HERMES_HOME":   Path(config.HERMES_HOME),
        "config.SKILLS_ROOT":   Path(config.SKILLS_ROOT),
        "threads.DB_PATH":      Path(threads.DB_PATH),
        "leads_store.DB_PATH":  Path(leads_store.DB_PATH),
        "accounts.DB_PATH":     Path(accounts.DB_PATH),
        "approvals.REQUESTS":   Path(approvals.REQUESTS_PATH),
        "approvals.DECISIONS":  Path(approvals.DECISIONS_PATH),
        "attachments.DIR":      Path(attachments.DATA_DIR),
        "notify.feed_path()":   Path(notify.feed_path()),
    }
    bad = []
    for name, path in resolved.items():
        p = path.resolve()
        if any(str(p).startswith(str(f.resolve())) for f in FORBIDDEN):
            bad.append(f"{name} -> {p}  [LIVE TENANT]")
        elif not str(p).startswith(str(CLONE.resolve())):
            bad.append(f"{name} -> {p}  [outside the clone]")
    if bad:
        sys.exit("REFUSING TO SEED — these resolved outside the clone:\n  " + "\n  ".join(bad))

    if not notify._demo_seeding():
        sys.exit("REFUSING TO SEED — JARVIS_DEMO_SEED is not in effect; events would be unmarked.")

    return resolved
