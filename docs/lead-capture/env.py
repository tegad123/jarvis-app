"""Point this process at a DISPOSABLE lead store. Never the live one.

Same discipline as the W3 sandbox and the testco seeder: set every path BEFORE
any app import, then re-read the RESOLVED paths back out of the modules and
REFUSE if any escaped. Setting an env var proves nothing; the modules are asked.

PATTERN 3 applies with force here — leads_store.create() emits a feed event, so
JARVIS_ACTIVITY_FEED is redirected alongside the database. That exact omission
has now cost this project three times.
"""
import sys
sys.exit("REFUSING: this script works on live (/root/jarvis-app or /root/.hermes); port it to the platform tree (VERIFY_APP_ROOT, VERIFY_SKILLS_ROOT) before running it")
import os, sys
from pathlib import Path

SP = Path("/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad")
BOX = SP / "leadcap" / "box"
SKILLS = Path("/root/.hermes/skills/openclaw-imports")
FORBIDDEN = (Path("/root/.hermes/state"), Path("/root/jarvis-app/data"))


def apply(source="store"):
    BOX.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "JARVIS_LEADS_DB": str(BOX / "leads.db"),
        "JARVIS_ACTIVITY_FEED": str(BOX / "feed.jsonl"),
        "JARVIS_THREADS_DB": str(BOX / "threads.db"),
        "JARVIS_LEADS_SOURCE": source,
        "JARVIS_DEMO_SEED": "1",
        "JARVIS_ALLOW_WRITES": "0",
    })
    sys.path.insert(0, os.environ.get("VERIFY_APP_ROOT") or str(Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(SKILLS / "_shared"))


def assert_isolated():
    from app import leads_store
    import notify
    resolved = {"leads_store.DB_PATH": Path(leads_store.DB_PATH),
                "notify.feed_path()": Path(notify.feed_path())}
    bad = [f"{k} -> {v}" for k, v in resolved.items()
           if any(str(v.resolve()).startswith(str(f.resolve())) for f in FORBIDDEN)
           or not str(v.resolve()).startswith(str(BOX.resolve()))]
    if bad:
        sys.exit("REFUSING: paths escaped the box:\n  " + "\n  ".join(bad))
    return resolved


def reset():
    import shutil
    if BOX.exists():
        shutil.rmtree(BOX)
    BOX.mkdir(parents=True, exist_ok=True)
