#!/usr/bin/env python3
"""The agent lock is enforced: flock on /root/.jarvis-agent-lock, one process tree.

    python3 docs/platform/verify_agent_lock.py [--canary-wrong-lock] [--live-upgrade-race]

"Another holder" is simulated on an extra lock (JARVIS_AGENT_LOCK, a temp
file) held by a sibling process, so the suite also runs while its own caller
holds the real lock. The real lock is always checked as well.

  1. the lock: 20 racers, exactly one wins; the holder is read from /proc/locks;
     a killed holder leaves no stale lock; a nested run inside the holder's tree
     is allowed, a sibling is refused; acquire/release are gone.
  2. the provisioner: while another process tree holds the lock, --upgrade,
     --reconfigure and --reconfigure --restart-approved on eddie are refused and
     eddie is untouched (same PIDs, no new upgrade snapshot).
  3. the systemctl guard: /usr/local/sbin/systemctl is the installed copy of
     scripts/agent_lock.py; reads pass straight through; a non-tenant unit
     restarts while the lock is held; a tenant restart (isocheck-app) is
     refused before anything stops, and logged; with the lock free it goes
     through (isocheck-app restarts and answers again).

--canary-wrong-lock runs a copy of the provisioner and of the guard whose
ensure() is a no-op, with a slug that does not exist for the provisioner: the
refusal checks must go red, with nothing changed on a real tenant beyond an
isocheck-app restart.

--live-upgrade-race (run by hand, not in the gate): a real --upgrade of eddie
holds the lock; a second --upgrade (eddie, then blake) started while it runs is
refused, and the first one completes healthy.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "scripts" / "agent_lock.py"
PROV = REPO / "scripts" / "provision_tenant.py"
PY = sys.executable
results = []
HOLDERS = []


def ck(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))


def main_pid(unit):
    return subprocess.run(["/usr/bin/systemctl", "show", unit, "-p", "MainPID", "--value"],
                          capture_output=True, text=True).stdout.strip()


def snapshots(slug):
    d = Path(f"/root/backups/{slug}")
    return sorted(p.name for p in d.glob("upgrade-*")) if d.exists() else []


def holder(lock: Path, seconds: int, who="holder"):
    env = dict(os.environ, JARVIS_AGENT_LOCK=str(lock))
    p = subprocess.Popen([PY, str(TOOL), "run", "--who", who, "--why", "verify", "--", "sleep", str(seconds)],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    HOLDERS.append(p)
    for _ in range(50):
        if subprocess.run([PY, str(TOOL), "status"], env=env, capture_output=True, text=True).stdout.count(
                f"{lock}: held"):
            break
        time.sleep(0.1)
    return p


def ping(port):
    try:
        return urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=5).status
    except Exception:
        return None


def main() -> int:
    canary = "--canary-wrong-lock" in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="verify-lock-"))
    lock = tmp / "lock"
    env = dict(os.environ, JARVIS_AGENT_LOCK=str(lock))

    def tool(*args, e=env):
        p = subprocess.run([PY, str(TOOL), *args], env=e, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    print("── 1. the lock")
    racers = [subprocess.Popen([PY, str(TOOL), "run", "--who", f"r{i}", "--why", "race", "--", "sleep", "2"],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in range(20)]
    rcs = [p.wait() for p in racers]
    ck(f"20 simultaneous runs: exactly one wins (rc0 {rcs.count(0)}, rc2 {rcs.count(2)})",
       rcs.count(0) == 1 and rcs.count(2) == 19)
    h = holder(lock, 30)
    rc, out = tool("status")
    ck("status names the holder pid from /proc/locks", f"held by pid {h.pid}" in out, out)
    rc, out = tool("run", "--who", "sibling", "--why", "v", "--", "true")
    ck("a sibling process tree is refused (rc 2, names the holder)", rc == 2 and f"pid {h.pid}" in out, out)
    h.kill()
    h.wait()
    time.sleep(0.3)
    rc, out = tool("status")
    ck("a killed holder leaves no stale lock", f"{lock}: free" in out, out)
    rc, out = tool("run", "--who", "outer", "--why", "v", "--", PY, str(TOOL), "run", "--who", "inner",
                   "--why", "v", "--", "true")
    ck("a nested run inside the holder's own tree is allowed", rc == 0, out)
    rc, out = tool("acquire", "--who", "lead", "--why", "old")
    ck("acquire is gone (rc 2, points at run)", rc == 2 and "run --who" in out, out)

    print("── 2. the provisioner refuses while another process tree holds the lock")
    manifest = REPO / "docs/tenants/eddie.yaml"
    prov, guard = PROV, "systemctl"
    if canary:
        cdir = tmp / "scripts"
        cdir.mkdir()
        src = TOOL.read_text()
        cut = src.rindex('if __name__ == "__main__":')
        (cdir / "agent_lock.py").write_text(src[:cut] + "ensure = lambda who, why: []\n\n\n" + src[cut:])
        (cdir / "agent_lock.py").chmod(0o755)
        (cdir / "provision_tenant.py").write_text(PROV.read_text())
        (cdir / "templates").symlink_to(REPO / "scripts" / "templates")
        (tmp / "systemctl").symlink_to(cdir / "agent_lock.py")
        bad = tmp / "lockcanary.yaml"
        bad.write_text(manifest.read_text().replace("slug: eddie", "slug: lockcanary", 1))
        manifest, prov, guard = bad, cdir / "provision_tenant.py", str(tmp / "systemctl")
        print("   [CANARY: provisioner and guard run with ensure() a no-op; slug lockcanary does not exist]")
    pids0 = (main_pid("eddie-app"), main_pid("eddie-gateway"))
    snaps0 = snapshots("eddie")
    h = holder(lock, 120)
    for flags in (["--upgrade"], ["--reconfigure"], ["--reconfigure", "--restart-approved"]):
        p = subprocess.run([PY, str(prov), "--manifest", str(manifest), *flags], env=env,
                           capture_output=True, text=True, timeout=300)
        out = (p.stdout + p.stderr).strip().splitlines()
        ck(f"{' '.join(flags)} refused (rc {p.returncode}): "
           f"{(out[-1] if out else '')[:90]}",
           p.returncode != 0 and any("agent lock is held by another process tree" in line for line in out))
    ck(f"eddie untouched: app/gateway PIDs {pids0} -> {(main_pid('eddie-app'), main_pid('eddie-gateway'))}",
       pids0 == (main_pid("eddie-app"), main_pid("eddie-gateway")))
    ck("no new upgrade snapshot for eddie", snapshots("eddie") == snaps0)

    print("── 3. the systemctl guard")
    which = subprocess.run(["sh", "-c", "command -v systemctl"], capture_output=True, text=True).stdout.strip()
    inst = Path("/usr/local/lib/jarvis/agent_lock.py")
    same = inst.is_file() and hashlib.sha256(inst.read_bytes()).digest() == hashlib.sha256(TOOL.read_bytes()).digest()
    ck(f"root's systemctl is the guard ({which} -> {os.path.realpath(which) if which else None})",
       os.path.realpath(which or "/") == str(inst))
    ck("the installed guard is this commit's agent_lock.py", same)
    r = subprocess.run([guard, "is-active", "isocheck-app"], env=env, capture_output=True, text=True)
    ck(f"a read passes straight through while the lock is held ({r.stdout.strip()})", r.stdout.strip() == "active")
    subprocess.run(["/usr/bin/systemd-run", "--quiet", "--unit=jarvis-lockcheck", "sleep", "300"], check=False)
    d0 = main_pid("jarvis-lockcheck")
    r = subprocess.run([guard, "restart", "jarvis-lockcheck"], env=env, capture_output=True, text=True)
    d1 = main_pid("jarvis-lockcheck")
    ck(f"a non-tenant unit restarts while the lock is held (rc {r.returncode}, pid {d0} -> {d1})",
       r.returncode == 0 and d0 != d1 and d1 not in ("", "0"))
    subprocess.run(["/usr/bin/systemctl", "stop", "jarvis-lockcheck"], check=False)
    since = time.strftime("%Y-%m-%d %H:%M:%S")
    i0 = main_pid("isocheck-app")
    r = subprocess.run([guard, "restart", "isocheck-app"], env=env, capture_output=True, text=True)
    i1 = main_pid("isocheck-app")
    ck(f"a tenant restart is refused while another tree holds the lock (rc {r.returncode})",
       r.returncode == 2 and "REFUSED systemctl restart isocheck-app" in r.stderr, r.stderr[-200:])
    ck(f"   and nothing stopped: isocheck-app pid {i0} -> {i1}", i0 == i1 and i1 not in ("", "0"))
    time.sleep(1)
    j = subprocess.run(["journalctl", "-t", "jarvis-agent-lock", "--since", since, "-o", "cat", "--no-pager"],
                       capture_output=True, text=True).stdout
    ck("   and the refusal is in the journal (tag jarvis-agent-lock)", "REFUSED systemctl restart isocheck-app" in j)
    h.kill()
    h.wait()
    time.sleep(0.3)
    r = subprocess.run(["systemctl", "restart", "isocheck-app"], env=env, capture_output=True, text=True)
    i2 = main_pid("isocheck-app")
    up = None
    for _ in range(30):
        up = ping(8740)
        if up == 200:
            break
        time.sleep(1)
    ck(f"with the lock free the restart goes through (rc {r.returncode}, pid {i1} -> {i2}, ping {up})",
       r.returncode == 0 and i2 != i1 and up == 200)

    if "--live-upgrade-race" in sys.argv:
        print("── 4. a second concurrent --upgrade is refused (real upgrade of eddie)")
        rc, out = tool("status", e=dict(os.environ))
        if "held by pid" in out:
            ck("the real lock is free before the race (run this section outside agent_lock.py run)", False, out)
            print(f"\n{sum(results)}/{len(results)} passed")
            return 1
        snaps0 = snapshots("eddie")
        first = subprocess.Popen([PY, str(PROV), "--manifest", str(REPO / "docs/tenants/eddie.yaml"), "--upgrade"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        rc, out = 0, ""
        for _ in range(100):
            rc, out = tool("status", e=dict(os.environ))
            if "held by pid" in out:
                break
            time.sleep(0.1)
        ck(f"the first --upgrade holds the real lock ({out.strip()[:80]})", f"held by pid {first.pid}" in out)
        for slug in ("eddie", "blake"):
            p = subprocess.run([PY, str(PROV), "--manifest", str(REPO / f"docs/tenants/{slug}.yaml"), "--upgrade"],
                               capture_output=True, text=True, timeout=120)
            ck(f"a second --upgrade of {slug} while it runs is refused (rc {p.returncode})",
               p.returncode != 0 and "agent lock is held by another process tree" in p.stdout + p.stderr)
        fout = first.communicate(timeout=900)[0]
        new = [s for s in snapshots("eddie") if s not in snaps0]
        ck(f"the first --upgrade completed (rc {first.returncode}), one snapshot {new}",
           first.returncode == 0 and len(new) == 1, fout[-300:])
        ck(f"eddie answers after it (ping {ping(8750)})", ping(8750) == 200)

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


def run_main() -> int:
    try:
        return main()
    finally:
        for p in HOLDERS:
            if p.poll() is None:
                p.kill()
                p.wait()


if __name__ == "__main__":
    sys.exit(run_main())
