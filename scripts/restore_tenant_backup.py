#!/usr/bin/env python3
"""Restore a backup_tenant.py archive into a scratch directory.

Never restores onto /opt/{slug} or /root/.hermes. Round-trip verify:
copy archive files to --dest and sha256-compare one DB.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path


def die(msg: str, code: int = 2) -> None:
    print(f"restore REFUSED: {msg}", file=sys.stderr)
    sys.exit(code)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", type=Path, required=True,
                    help="one stamp dir under /root/backups/{slug}/")
    ap.add_argument("--dest", type=Path, required=True,
                    help="empty scratch directory (not /opt, not /root/.hermes)")
    ap.add_argument("--compare", default="jarvis_accounts.db",
                    help="DB filename to prove byte-identical round-trip")
    args = ap.parse_args()
    archive = args.archive.resolve()
    dest = args.dest.resolve()
    if not archive.is_dir():
        die(f"no archive at {archive}")
    forbidden = (Path("/opt"), Path("/root/.hermes"), Path("/root/jarvis-app"))
    for f in forbidden:
        try:
            dest.relative_to(f)
            die(f"refusing restore into {f}")
        except ValueError:
            pass
    if dest.exists() and any(dest.iterdir()):
        die(f"{dest} is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    for src in archive.iterdir():
        if src.name == "staging":
            continue
        target = dest / src.name
        if src.is_dir():
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)
    db = args.compare
    a = archive / db
    b = dest / db
    if not a.is_file() or not b.is_file():
        die(f"missing {db} in archive or dest")
    ha, hb = sha256(a), sha256(b)
    print(f"restore: {archive} -> {dest}")
    print(f"restore: {db} archive_sha256={ha}")
    print(f"restore: {db} dest_sha256={hb}")
    if ha != hb:
        print("FAIL: byte mismatch")
        return 1
    print(f"PASS: {db} round-tripped byte-identically")
    return 0


if __name__ == "__main__":
    sys.exit(main())
