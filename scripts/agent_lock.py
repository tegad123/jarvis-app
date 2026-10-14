#!/usr/bin/env python3
"""The server-change lock: one agent at a time changes the server.

    agent_lock.py acquire --who NAME --why TEXT   # rc 0 taken, 2 held by someone
    agent_lock.py release --who NAME              # rc 0 released, 2 not yours, 3 not held
    agent_lock.py status                          # prints holder JSON or "free"

The lock is a file created with O_EXCL, so two agents cannot both take it.
A stale lock is never broken automatically: whoever finds one asks the human.
Path: $JARVIS_AGENT_LOCK, default /root/.jarvis-agent-lock.
"""
import argparse
import json
import os
import socket
import sys
from datetime import datetime, timezone

LOCK = os.environ.get("JARVIS_AGENT_LOCK", "/root/.jarvis-agent-lock")


def _holder():
    try:
        with open(LOCK, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        return {"who": "<unreadable lock>", "error": type(exc).__name__}


def acquire(who: str, why: str) -> int:
    info = {"who": who, "why": why, "pid": os.getppid(), "host": socket.gethostname(),
            "acquired_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        print(f"HELD: {json.dumps(_holder())}", file=sys.stderr)
        return 2
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f)
    print(f"acquired: {json.dumps(info)}")
    return 0


def release(who: str) -> int:
    h = _holder()
    if h is None:
        print("NOT HELD: nothing to release", file=sys.stderr)
        return 3
    if h.get("who") != who:
        print(f"NOT YOURS: held by {json.dumps(h)}", file=sys.stderr)
        return 2
    os.unlink(LOCK)
    print(f"released: {who}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("acquire")
    a.add_argument("--who", required=True)
    a.add_argument("--why", required=True)
    r = sub.add_parser("release")
    r.add_argument("--who", required=True)
    sub.add_parser("status")
    args = ap.parse_args()
    if args.cmd == "acquire":
        return acquire(args.who, args.why)
    if args.cmd == "release":
        return release(args.who)
    h = _holder()
    print(json.dumps(h) if h else "free")
    return 0


if __name__ == "__main__":
    sys.exit(main())
