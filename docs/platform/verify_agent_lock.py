#!/usr/bin/env python3
"""Verify scripts/agent_lock.py: one holder at a time, only the holder releases.

Runs against a temporary lock path (JARVIS_AGENT_LOCK); never touches the real
/root/.jarvis-agent-lock.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2] / "scripts" / "agent_lock.py"
results = []


def ck(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="verify-lock-"))
    lock = tmp / "lock"
    env = dict(os.environ, JARVIS_AGENT_LOCK=str(lock))

    def run(*args):
        p = subprocess.run([sys.executable, str(TOOL), *args], env=env, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    rc, out = run("status")
    ck("status on a free lock says free", rc == 0 and out.strip() == "free", out)
    rc, out = run("acquire", "--who", "lead", "--why", "verify")
    ck("first acquire succeeds (rc 0)", rc == 0, out)
    ck("lock file exists after acquire", lock.is_file())
    ck("lock file is 0600", lock.is_file() and (lock.stat().st_mode & 0o777) == 0o600)
    rc, out = run("acquire", "--who", "helper", "--why", "verify")
    ck("second acquire by another agent refused (rc 2)", rc == 2, out)
    ck("refusal names the holder", '"who": "lead"' in out, out)
    rc, out = run("acquire", "--who", "lead", "--why", "again")
    ck("re-acquire by the holder also refused (no silent re-entry)", rc == 2, out)
    rc, out = run("release", "--who", "helper")
    ck("release by a non-holder refused (rc 2)", rc == 2, out)
    ck("lock still held after the refused release", lock.is_file())
    rc, out = run("status")
    ck("status shows the holder", '"who": "lead"' in out, out)
    rc, out = run("release", "--who", "lead")
    ck("release by the holder succeeds (rc 0)", rc == 0, out)
    ck("lock file gone after release", not lock.exists())
    rc, out = run("release", "--who", "lead")
    ck("release when not held is loud (rc 3)", rc == 3 and "NOT HELD" in out, out)
    rc, out = run("acquire", "--who", "helper", "--why", "after")
    ck("acquire works again once released", rc == 0, out)
    run("release", "--who", "helper")

    print("── concurrency: 20 simultaneous acquires, exactly one wins")
    procs = [subprocess.Popen([sys.executable, str(TOOL), "acquire", "--who", f"a{i}", "--why", "race"],
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in range(20)]
    rcs = [p.wait() for p in procs]
    ck("exactly one acquire wins the race", rcs.count(0) == 1, str(rcs))
    ck("every loser gets rc 2", rcs.count(2) == 19, str(rcs))
    lock.unlink(missing_ok=True)
    tmp.rmdir()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
