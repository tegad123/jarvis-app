#!/usr/bin/env python3
"""Install the shared Chromium every tenant's browser runs.

Copies the Chromium headless shell already on this host (downloaded by
Playwright into /root/.cache, which tenant users cannot reach) to
/opt/jarvis/browser, root-owned and read-only to everyone else, then proves
an unprivileged user can start it. Nothing is downloaded. Re-running with the
copy in place only re-checks it.

--headed also copies the full Chromium of the same revision (for
`browser_mode: headed`) and writes the managed policy every headed browser
must run under (app/browser_launch.POLICY: no file://, no file pickers, no
downloads). A headed browser refuses to start without that policy.

    python3 scripts/install_browser_runtime.py [--headed]
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import browser_launch  # noqa: E402
from provision_tenant import (BROWSER_BIN, BROWSER_HEADED_BIN, BROWSER_HEADED_RUNTIME,  # noqa: E402
                              BROWSER_POLICY_FILE, BROWSER_RUNTIME)

SOURCE = Path("/root/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64")
HEADED_SOURCE = Path("/root/.cache/ms-playwright/chromium-1234/chrome-linux64")


def lock_down(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        for name in [dirpath] + [os.path.join(dirpath, f) for f in filenames]:
            st = os.lstat(name)
            os.lchown(name, 0, 0)
            if not os.path.islink(name):
                os.chmod(name, (st.st_mode & 0o755) | 0o444 | (0o111 if st.st_mode & 0o111 else 0))


def check(runtime: Path, binary: Path) -> None:
    r = subprocess.run(["runuser", "-u", "nobody", "--", str(binary), "--version"],
                       capture_output=True, text=True, cwd="/", timeout=30)
    if r.returncode != 0 or "Chrom" not in r.stdout:
        sys.exit(f"REFUSED: {binary} does not run as an unprivileged user (rc={r.returncode}): "
                 f"{(r.stderr or r.stdout).strip()[:300]}")
    writable = [str(p) for p in runtime.rglob("*") if not p.is_symlink() and p.stat().st_mode & 0o022]
    if writable or os.stat(runtime).st_mode & 0o022:
        sys.exit(f"REFUSED: group- or world-writable files under {runtime}: {writable[:5]}")
    print(f"browser runtime ok: {binary} ({r.stdout.strip()}), root-owned, not writable by others")


def install(source: Path, runtime: Path, binary: Path) -> None:
    if not binary.exists():
        if not (source / binary.name).is_file():
            sys.exit(f"REFUSED: no Chromium at {source}; nothing is downloaded by this script")
        runtime.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        os.chmod(runtime.parent, 0o755)
        tmp = runtime.with_name(runtime.name + ".partial")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(source, tmp, symlinks=True)
        lock_down(tmp)
        os.rename(tmp, runtime)
        print(f"copied {source} to {runtime}")
    check(runtime, binary)


def install_policy(path: Path) -> None:
    """Root-owned, world-readable (the browser user reads it), never writable
    by anyone else. Rewritten every run so it always matches the code."""
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    tmp = path.with_suffix(".partial")
    tmp.write_text(json.dumps(browser_launch.POLICY, indent=2) + "\n")
    os.chown(tmp, 0, 0)
    os.chmod(tmp, 0o644)
    os.rename(tmp, path)
    problem = browser_launch.policy_problem(str(path))
    if problem:
        sys.exit(f"REFUSED: {problem}")
    print(f"managed policy ok: {path}")


def main() -> None:
    if os.geteuid() != 0:
        sys.exit("REFUSED: run as root")
    install(SOURCE, BROWSER_RUNTIME, BROWSER_BIN)
    if "--headed" in sys.argv[1:]:
        install(HEADED_SOURCE, BROWSER_HEADED_RUNTIME, BROWSER_HEADED_BIN)
        install_policy(BROWSER_POLICY_FILE)


if __name__ == "__main__":
    main()
