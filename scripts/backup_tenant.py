#!/usr/bin/env python3
"""WAL-inclusive snapshot of a tenant's state into /root/backups/{slug}/.

Reference: .cursor/skills/verify-jarvis/SKILL.md ledger snapshot
(copy db+wal+shm, then open the copy — never the live file under a writer).

Does not install sqlite3. Uses the shared venv's sqlite3 module.
Does not write into /opt/{slug}. Default keep_daily=14.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SHARED_VENV_NOTE = "/root/jarvis-app/.venv/bin/python"

STATE_DBS = (
    "jarvis_leads.db",
    "jarvis_tasks.db",
    "jarvis_threads.db",
    "sweeps.db",
)
HERMES_DBS = ("state.db", "kanban.db")
JSONL_NAMES = (
    "activity_feed.jsonl",
    "approvals.jsonl",
    "approvals_decisions.jsonl",
)
DEFAULT_KEEP = 14
DEFAULT_ROOT = Path("/root/backups")


def die(msg: str, code: int = 2) -> None:
    print(f"backup REFUSED: {msg}", file=sys.stderr)
    sys.exit(code)


def snapshot_db(src: Path, dest_dir: Path) -> Path | None:
    """Copy db+wal+shm aside, then sqlite backup() into a consistent dest file."""
    if not src.is_file():
        return None
    staging = dest_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, staging / src.name)
    for suf in ("-wal", "-shm"):
        w = src.parent / (src.name + suf)
        if w.exists():
            shutil.copy2(w, staging / w.name)
    consistent = dest_dir / src.name
    src_copy = staging / src.name
    src_con = sqlite3.connect(f"file:{src_copy}?mode=ro", uri=True)
    try:
        dst_con = sqlite3.connect(str(consistent))
        try:
            src_con.backup(dst_con)
            dst_con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            dst_con.close()
    finally:
        src_con.close()
    return consistent


def db_sources(opt: Path) -> list[tuple[str, Path]]:
    """Where each database lives. The accounts and threads DBs can sit outside
    hermes/state (an app-only directory) and are then named by
    JARVIS_ACCOUNTS_DB / JARVIS_THREADS_DB."""
    hermes = opt / "hermes"
    state = hermes / "state"
    named = {"jarvis_accounts.db": os.environ.get("JARVIS_ACCOUNTS_DB"),
             "jarvis_threads.db": os.environ.get("JARVIS_THREADS_DB")}
    return ([("jarvis_accounts.db", Path(named["jarvis_accounts.db"] or state / "jarvis_accounts.db"))]
            + [(n, Path(named.get(n) or state / n)) for n in STATE_DBS]
            + [(n, hermes / n) for n in HERMES_DBS])


def rotate(dest_root: Path, keep: int) -> None:
    dirs = sorted([p for p in dest_root.iterdir() if p.is_dir() and p.name[:1].isdigit()],
                  reverse=True)
    for old in dirs[keep:]:
        shutil.rmtree(old)
        print(f"backup: rotated out {old}")


def backup_slug(slug: str, dest_root: Path, keep: int) -> Path:
    if slug in ("testco", "root", "live", "34dev"):
        # Backing up testco is allowed (read-only copy). live/root are not.
        if slug in ("root", "live", "34dev"):
            die(f"refusing to backup reserved slug {slug}")
    opt = Path(f"/opt/{slug}")
    state = opt / "hermes" / "state"
    if not state.is_dir():
        die(f"no state dir at {state}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_root / stamp
    dest.mkdir(parents=True, exist_ok=False)
    written = []
    missing = []
    for name, src in db_sources(opt):
        got = snapshot_db(src, dest)
        if got:
            written.append(got.name)
        else:
            missing.append(str(src))
            print(f"backup: MISSING {name} (not found at {src})")
    # JSONL as files (copytruncate-safe: copy, do not truncate the live file)
    for name in JSONL_NAMES:
        src = state / name
        if src.is_file():
            shutil.copy2(src, dest / name)
            written.append(name)
    outbox = opt / "hermes" / "outbox" / "captured_mail.jsonl"
    if outbox.is_file():
        (dest / "outbox").mkdir()
        shutil.copy2(outbox, dest / "outbox" / "captured_mail.jsonl")
        written.append("outbox/captured_mail.jsonl")
    manifest = {
        "slug": slug,
        "created_utc": stamp,
        "files": written,
        "missing": missing,
        "keep_daily": keep,
        "note": "DBs are consistent sqlite copies (WAL merged via backup()). "
                "staging/ holds the raw db+wal+shm copies.",
    }
    (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rotate(dest_root, keep)
    print(f"backup: wrote {dest}")
    print(f"backup: db_files={sum(1 for n in written if n.endswith('.db'))} "
          f"all_files={len(written)} missing={len(missing)}")
    for n in written:
        print(f"  {n}")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--dest", type=Path, default=None,
                    help="backup root (default /root/backups/{slug})")
    ap.add_argument("--keep-daily", type=int, default=DEFAULT_KEEP)
    args = ap.parse_args()
    dest_root = args.dest or (DEFAULT_ROOT / args.slug)
    dest_root.mkdir(parents=True, exist_ok=True)
    backup_slug(args.slug, dest_root, args.keep_daily)
    return 0


if __name__ == "__main__":
    sys.exit(main())
