#!/usr/bin/env python3
"""No verify, canary or helper script may work on live.

Live is /root/jarvis-app (its code, data and venv) and /root/.hermes. A script
here once imported live's code (Python wrote bytecode into live's tree), and
two canaries mutated live source files in place. This scan fails on any script
under docs/ or scripts/ that:

  - puts live on sys.path,
  - binds a working path (APP, SRC, SHARED, CLS, ...) to live code or state,
  - runs live's venv interpreter,

unless the script refuses to start (a top-level `sys.exit("REFUSING`).
Guard tuples that name live in order to refuse it (FORBIDDEN, LIVE, ...) are
allowed. A canary file proves each rule fires.

    python3 docs/platform/verify_no_live_targets.py
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE = r"/root/(?:jarvis-app(?:/|['\"])|\.hermes/(?:skills|state))"
RULES = {
    "live on sys.path": re.compile(rf"sys\.path\.(?:insert|append)\([^)]*['\"]{LIVE}"),
    "working path bound to live": re.compile(
        rf"^\s*(?!FORBID|LIVE|NEVER|BANNED)[A-Z][A-Z0-9_]*\s*=\s*.*['\"]{LIVE}"),
    "runs live's venv": re.compile(r"/root/jarvis-app/\.venv/bin/python"),
}
REFUSES = re.compile(r"^(?:sys\.exit\(|echo |console\.error\()\s*['\"]REFUSING", re.M)
SKIP = {Path(__file__).resolve()}


def scan(paths) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for p in paths:
        if p.resolve() in SKIP or not p.is_file():
            continue
        text = p.read_text(errors="replace")
        if REFUSES.search(text):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if s.startswith(("#", "//", "Run:", "*")):
                continue
            for name, rx in RULES.items():
                if rx.search(line):
                    hits.setdefault(str(p.relative_to(p.anchor) if not p.is_relative_to(ROOT)
                                        else p.relative_to(ROOT)), []).append(f"{n}: {name}: {s[:90]}")
    return hits


def files(root: Path):
    for sub in ("docs", "scripts"):
        for ext in ("*.py", "*.sh", "*.mjs"):
            yield from (root / sub).rglob(ext)


def main() -> int:
    ok = True
    hits = scan(files(ROOT))
    print(f"  {'PASS' if not hits else 'FAIL'}  no script under docs/ or scripts/ works on live "
          f"({len(hits)} file(s) flagged)")
    for f, lines in sorted(hits.items()):
        print(f"        {f}")
        for ln in lines:
            print(f"          {ln}")
    ok &= not hits

    with tempfile.TemporaryDirectory() as d:
        cases = {
            "path.py": 'import sys\nsys.path.insert(0, "/root/jarvis-app")\n',
            "bind.py": "APP = '/root/jarvis-app'\n",
            "venv.sh": "/root/jarvis-app/.venv/bin/python -m app.main\n",
            "state.py": 'SHARED = Path("/root/.hermes/skills/openclaw-imports/_shared")\n',
        }
        twins = {
            "guard.py": 'FORBIDDEN = (Path("/root/.hermes"), Path("/root/jarvis-app/data"))\n',
            "refuses.py": 'import sys\nsys.exit("REFUSING: ported later")\nAPP = "/root/jarvis-app"\n',
            "refuses.sh": 'echo "REFUSING: ported later" >&2; exit 2\n/root/jarvis-app/.venv/bin/python x\n',
            "platform.py": 'APP = "/root/jarvis-platform"\n',
        }
        for name, body in {**cases, **twins}.items():
            (Path(d) / name).write_text(body)
        got = scan(Path(d).iterdir())
        flagged = {Path(k).name for k in got}
        c = flagged == set(cases)
        print(f"  {'PASS' if c else 'FAIL'}  canary: each rule fires on its case and not on guards, "
              f"refusing scripts or the platform tree (flagged {sorted(flagged)})")
        ok &= c
    print(f"\n{2 if ok else int(not hits) + int(c)}/2 passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
