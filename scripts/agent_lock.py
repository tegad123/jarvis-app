#!/usr/bin/env python3
"""The server-change lock, enforced by the kernel: one process tree at a time.

    agent_lock.py run --who NAME --why TEXT -- CMD [ARGS...]
        hold the lock while CMD runs (rc 2 if another process tree holds it)
    agent_lock.py status
        "free", or the holder: pid from /proc/locks, plus what it said it is

The lock is flock(2) on /root/.jarvis-agent-lock. The kernel drops it when the
holder exits, so there is no stale lock to break. Who holds it comes from
/proc/locks, never from the file's text (that is only the holder's label).

A process may change the server if it takes the lock itself, or if the holder
is one of its ancestors (a rollout script run under `run`, the provisioner it
starts, the systemctl calls the provisioner makes). Anything outside that tree
is refused: knowing the protocol or the holder's name is not enough.

JARVIS_AGENT_LOCK names an extra lock to satisfy as well, for verify scripts.
The real lock is always checked too, so the variable cannot be used to bypass it.

Installed as /usr/local/sbin/systemctl (a symlink to the installed copy at
/usr/local/lib/jarvis/agent_lock.py) the same file gates start, stop, restart,
reload, kill, enable, disable, mask and unmask of tenant units; every other
call, and every non-tenant unit, goes straight to /usr/bin/systemctl. systemd's
own starts (boot, Restart=) never pass through here.
"""
from __future__ import annotations

import fcntl
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REAL_LOCK = "/root/.jarvis-agent-lock"
REAL_SYSTEMCTL = "/usr/bin/systemctl"
GATED_VERBS = {"start", "stop", "restart", "try-restart", "reload", "reload-or-restart",
               "try-reload-or-restart", "condrestart", "force-reload", "kill",
               "enable", "disable", "reenable", "mask", "unmask"}


class Held(Exception):
    def __init__(self, path: str, pid: int, label: dict):
        self.path, self.pid, self.label = path, pid, label
        super().__init__(f"{path} is held by pid {pid} ({json.dumps(label)})")


def lock_paths() -> list:
    extra = os.environ.get("JARVIS_AGENT_LOCK")
    return [REAL_LOCK] + ([extra] if extra and extra != REAL_LOCK else [])


def _open(path: str) -> int:
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    os.fchmod(fd, 0o600)
    return fd


def holder_pid(path: str):
    """The pid holding an flock on path, from /proc/locks; None when free."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    want = (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)
    with open("/proc/locks") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 6 or parts[1] != "FLOCK" or parts[1] == "->":
                continue
            try:
                maj, mnr, ino = parts[5].split(":")
                if (int(maj, 16), int(mnr, 16), int(ino)) == want:
                    return int(parts[4])
            except ValueError:
                continue
    return None


def label(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text() or "{}")
    except (OSError, ValueError):
        return {}


def ancestors(pid: int = None) -> list:
    out, p = [], pid or os.getpid()
    while p > 1:
        try:
            with open(f"/proc/{p}/stat") as f:
                p = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
        out.append(p)
    return out


def _take(path: str, who: str, why: str):
    """Take the lock on path, or return None when an ancestor already holds it."""
    pid = holder_pid(path)
    if pid is not None and (pid == os.getpid() or pid in ancestors()):
        return None
    fd = _open(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        pid = holder_pid(path)
        raise Held(path, pid or -1, label(path))
    info = {"who": who, "why": why, "pid": os.getpid(), "host": socket.gethostname(),
            "argv": " ".join(sys.argv)[:300],
            "acquired_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    os.ftruncate(fd, 0)
    os.pwrite(fd, json.dumps(info).encode(), 0)
    return fd


def ensure(who: str, why: str) -> list:
    """Hold every lock in lock_paths() for the rest of this process, or raise Held.

    Returns the descriptors taken here (keep them open; the kernel releases them
    on exit). Locks already held by an ancestor are not taken again."""
    fds = []
    try:
        for path in lock_paths():
            fd = _take(path, who, why)
            if fd is not None:
                fds.append(fd)
    except Held:
        for fd in fds:
            os.close(fd)
        raise
    return fds


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)
    try:
        subprocess.run(["logger", "-t", "jarvis-agent-lock", msg], check=False, timeout=5)
    except Exception:
        pass


# ───────────────────────────────────────────────────────────── systemctl guard

def tenant_slugs() -> set:
    out = set()
    for d in Path("/opt").iterdir():
        if (d / "jarvis-app").is_dir() and (d / "hermes").is_dir() \
                and Path(f"/etc/systemd/system/{d.name}-app.service").exists():
            out.add(d.name)
    return out


def _gated_units(argv: list) -> list:
    words = [a for a in argv if not a.startswith("-")]
    if not words or words[0] not in GATED_VERBS:
        return []
    slugs = tenant_slugs()
    hits = []
    for unit in words[1:]:
        if any(c in unit for c in "*?["):
            hits.append(unit)
        elif any(unit == s or unit.startswith(s + "-") for s in slugs):
            hits.append(unit)
    return hits


def systemctl_guard(argv: list) -> int:
    try:
        units = _gated_units(argv)
    except Exception as exc:
        words = [a for a in argv if not a.startswith("-")]
        if words and words[0] in GATED_VERBS:
            _log(f"REFUSED systemctl {' '.join(argv)}: the agent-lock guard failed ({exc!r}); "
                 f"use {REAL_SYSTEMCTL} directly if you are a human")
            return 2
        units = []
    if not units:
        os.execv(REAL_SYSTEMCTL, [REAL_SYSTEMCTL, *argv])
    try:
        fds = ensure("systemctl", " ".join(argv)[:200])
    except Held as h:
        _log(f"REFUSED systemctl {' '.join(argv)}: tenant unit(s) {units} and {h}")
        return 2
    _log(f"allowed systemctl {' '.join(argv)} (agent lock held by this process tree)")
    rc = subprocess.run([REAL_SYSTEMCTL, *argv]).returncode
    for fd in fds:
        os.close(fd)
    return rc


# ──────────────────────────────────────────────────────────────────────── CLI

def cmd_run(who: str, why: str, cmd: list) -> int:
    if not cmd:
        print("run needs a command after --", file=sys.stderr)
        return 64
    try:
        ensure(who, why)
    except Held as h:
        print(f"HELD: {h}", file=sys.stderr)
        return 2
    return subprocess.run(cmd).returncode


def cmd_status() -> int:
    for path in lock_paths():
        pid = holder_pid(path)
        print(f"{path}: free" if pid is None else f"{path}: held by pid {pid} {json.dumps(label(path))}")
    return 0


def main(argv: list) -> int:
    if os.path.basename(sys.argv[0]) == "systemctl":
        return systemctl_guard(argv)
    if argv and argv[0] in ("acquire", "release"):
        print("acquire/release are gone: the lock is held by a running process. "
              "Use: agent_lock.py run --who NAME --why TEXT -- CMD", file=sys.stderr)
        return 2
    if argv[:1] == ["status"]:
        return cmd_status()
    if argv[:1] == ["run"]:
        rest = argv[1:]
        who = why = None
        cmd = []
        i = 0
        while i < len(rest):
            if rest[i] == "--":
                cmd = rest[i + 1:]
                break
            if rest[i] == "--who" and i + 1 < len(rest):
                who, i = rest[i + 1], i + 2
                continue
            if rest[i] == "--why" and i + 1 < len(rest):
                why, i = rest[i + 1], i + 2
                continue
            print(f"unknown argument {rest[i]!r}", file=sys.stderr)
            return 64
        if not who or not why:
            print("run needs --who and --why", file=sys.stderr)
            return 64
        return cmd_run(who, why, cmd)
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
