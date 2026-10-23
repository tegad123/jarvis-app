#!/usr/bin/env python3
"""Install the shared Chromium every tenant's browser runs.

Copies the Chromium headless shell already on this host (downloaded by
Playwright into /root/.cache, which tenant users cannot reach) to
/opt/jarvis/browser, root-owned and read-only to everyone else, then proves
an unprivileged user can start it. Nothing is downloaded. Re-running with the
copy in place only re-checks it.

    python3 scripts/install_browser_runtime.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provision_tenant import BROWSER_BIN, BROWSER_RUNTIME  # noqa: E402

SOURCE = Path("/root/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64")


def lock_down(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        for name in [dirpath] + [os.path.join(dirpath, f) for f in filenames]:
            st = os.lstat(name)
            os.lchown(name, 0, 0)
            if not os.path.islink(name):
                os.chmod(name, (st.st_mode & 0o755) | 0o444 | (0o111 if st.st_mode & 0o111 else 0))


def check() -> None:
    r = subprocess.run(["runuser", "-u", "nobody", "--", str(BROWSER_BIN), "--version"],
                       capture_output=True, text=True, cwd="/", timeout=30)
    if r.returncode != 0 or "Chrom" not in r.stdout:
        sys.exit(f"REFUSED: {BROWSER_BIN} does not run as an unprivileged user (rc={r.returncode}): "
                 f"{(r.stderr or r.stdout).strip()[:300]}")
    writable = [str(p) for p in BROWSER_RUNTIME.rglob("*") if not p.is_symlink() and p.stat().st_mode & 0o022]
    if writable or os.stat(BROWSER_RUNTIME).st_mode & 0o022:
        sys.exit(f"REFUSED: group- or world-writable files under {BROWSER_RUNTIME}: {writable[:5]}")
    print(f"browser runtime ok: {BROWSER_BIN} ({r.stdout.strip()}), root-owned, not writable by others")


def main() -> None:
    if os.geteuid() != 0:
        sys.exit("REFUSED: run as root")
    if not BROWSER_BIN.exists():
        if not (SOURCE / "chrome-headless-shell").is_file():
            sys.exit(f"REFUSED: no Chromium at {SOURCE}; nothing is downloaded by this script")
        BROWSER_RUNTIME.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        os.chmod(BROWSER_RUNTIME.parent, 0o755)
        tmp = BROWSER_RUNTIME.with_name(BROWSER_RUNTIME.name + ".partial")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(SOURCE, tmp, symlinks=True)
        lock_down(tmp)
        os.rename(tmp, BROWSER_RUNTIME)
        print(f"copied {SOURCE} to {BROWSER_RUNTIME}")
    check()


if __name__ == "__main__":
    main()
