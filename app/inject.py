"""
Operator fixture injection — the one way an inbound email reaches a tenant
that has no Gmail poller.

WHY. On testco nothing feeds the three email-driven handlers (ShowingSmart
autoreply, meeting-intake's email path, digest reply resolution): there is no
poller, no webhook, and Gmail push is deferred to cutover. Until now those
paths ran only inside the verify suites. This module is the seam the tester
can actually pull: a whitelisted fixture goes through the SAME handler script
the poller would call, with the same env, and the result lands where the
tester can see it — the feed, the capture outbox, the task list.

TWO FRONT DOORS, ONE ENGINE:
  * POST /api/operator/inject  {kind, fixture}      — operator session only,
    fixture MUST be in the whitelist enumerated from the fixtures directory
    when the app started. No paths, no uploads.
  * /opt/testco/bin/testco-inject <kind> <fixture|path.eml> — the operator
    shell wrapper; may pass an arbitrary .eml path, and `--fresh` (showing
    only) forgets the showing's once-per-showing key first.

REFUSES on a tenant whose outbound boundary is not active. An injected
showing request answers by email; on the live tenant that would be a real
send to a real agent. The boundary check is the whole safety story, so it is
made here, once, before any kind is dispatched.

EVERY INVOCATION IS JOURNALLED to <HERMES_HOME>/state/operator_inject.jsonl
(who, what, when, outcome) and announced on the feed as one line.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

FIXTURES_DIR = Path(os.environ.get("JARVIS_FIXTURES_DIR", "/opt/testco/fixtures"))
JOURNAL = config.HERMES_HOME / "state" / "operator_inject.jsonl"
STAGING = config.HERMES_HOME / "state" / "inject"

# kind → (fixture filename prefix, handler script relative to SKILLS_ROOT, argv builder)
KINDS: Dict[str, Dict[str, Any]] = {
    "showing": {
        "prefix": "showingsmart_inbound",
        "script": "showingsmart-autoreply/scripts/handle_showing_request.py",
        "argv": lambda eml, run_id: ["--eml", eml, "--run-id", run_id],
        "word": "showing request email",
    },
    "meeting": {
        "prefix": "meeting_",
        "script": "meeting-intake/scripts/handle_meeting.py",
        "argv": lambda eml, run_id: ["--eml", eml, "--origin", "email", "--run-id", run_id],
        "word": "meeting email",
    },
    "reply": {
        "prefix": "digest_reply_",
        "script": "gmail-push-trigger/scripts/poll_gmail_history.py",
        "argv": lambda eml, run_id: ["--reply-eml", eml],
        "word": "digest reply",
    },
}

_FIXTURE_NAME = re.compile(r"^[A-Za-z0-9_\-]+\.eml$")


def enumerate_whitelist(directory: Path = FIXTURES_DIR) -> Dict[str, List[str]]:
    """{kind: [fixture names]} — plain filenames only, matched by prefix."""
    out: Dict[str, List[str]] = {k: [] for k in KINDS}
    if not directory.is_dir():
        return out
    for p in sorted(directory.iterdir()):
        if not p.is_file() or not _FIXTURE_NAME.match(p.name):
            continue
        for kind, spec in KINDS.items():
            if p.name.startswith(spec["prefix"]):
                out[kind].append(p.name)
    return out


# Enumerated ONCE, at import (= app start). A fixture dropped into the
# directory later is not injectable until the app restarts — that is the
# point: the set of things the API can run is fixed when the process starts.
WHITELIST: Dict[str, List[str]] = enumerate_whitelist()


class InjectError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _shared():
    p = str(config.SHARED)
    if p not in sys.path:
        sys.path.insert(0, p)


def boundary_active() -> bool:
    try:
        _shared()
        import outbound_boundary  # type: ignore
        return bool(outbound_boundary.active())
    except Exception:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _journal(row: Dict[str, Any]) -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _announce(kind: str, fixture: str, by: str, run_id: str, status: str, rc: int) -> None:
    try:
        _shared()
        import notify  # type: ignore
        notify.notify(
            "ops",
            kind="operator.inject",
            headline=f"Added a test {KINDS[kind]['word']} — {fixture}",
            detail=f"operator injection · by {by} · run {run_id} · handler {status} (rc {rc})",
            skill="operator-inject",
        )
    except Exception as exc:  # the journal row is the record; the feed line is a courtesy
        print(f"[inject] feed announce failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def _stage(kind: str, source: Path, run_id: str) -> Path:
    """Copy the .eml into the tenant home with a fresh Message-ID, so each
    injection is a distinct message (a reply is claimed once per id; a
    duplicate showing is still a duplicate, on purpose — that key is the
    showing itself, not the mail)."""
    import email
    from email import policy

    msg = email.message_from_bytes(source.read_bytes(), policy=policy.default)
    domain = "testco.example.com"
    try:
        _shared()
        import identity  # type: ignore
        dom = (identity.owner_address() or "").split("@")[-1]
        if dom:
            domain = dom
    except Exception:
        pass
    del msg["Message-ID"]
    msg["Message-ID"] = f"<inj-{run_id}@{domain}>"
    STAGING.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(STAGING, 0o700)
    except Exception:
        pass
    staged = STAGING / f"{run_id}-{kind}.eml"
    staged.write_bytes(bytes(msg))
    os.chmod(staged, 0o600)
    return staged


def _forget_showing(eml: Path) -> Optional[str]:
    """--fresh: drop the once-per-showing key this fixture would hit, so the
    handler treats it as a new request. Returns the key forgotten, if any."""
    scripts = config.SKILLS_ROOT / "showingsmart-autoreply" / "scripts"
    code = (
        "import sys, json; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
        "import handle_showing_request as h, showing_state as s, showing_parse as sp\n"
        "import tenant_config as tc\n"
        "from datetime import datetime, timezone\n"
        "class A: eml=%r; run_id=''\n"
        "p = h.read_payload(A())\n"
        "try:\n"
        "    from zoneinfo import ZoneInfo; tz = ZoneInfo(tc.require('client.timezone'))\n"
        "except Exception:\n"
        "    tz = timezone.utc\n"
        "parsed = sp.parse(p['subject'], p['body'], now=datetime.now(tz), tz=tz)\n"
        "key = s.showing_key(parsed.address, parsed.start)\n"
        "d = s.load_processed()\n"
        "if key in d:\n"
        "    d.pop(key); path = s.store_path(); tmp = path.with_suffix('.tmp')\n"
        "    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False)); import os; os.replace(tmp, path)\n"
        "    print(key)\n"
    ) % (str(scripts), str(config.SHARED), str(eml))
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=dict(os.environ), timeout=60)
    if proc.returncode != 0:
        raise InjectError(f"could not reset the showing key: {proc.stderr.strip()[-300:]}", 500)
    return proc.stdout.strip() or None


def resolve(kind: str, fixture: str, *, allow_path: bool = False) -> Path:
    if kind not in KINDS:
        raise InjectError(f"kind must be one of {sorted(KINDS)}.")
    if not isinstance(fixture, str) or not fixture:
        raise InjectError("fixture is required.")
    if fixture in WHITELIST[kind]:
        return FIXTURES_DIR / fixture
    if allow_path:
        p = Path(fixture).expanduser()
        if p.is_file() and p.suffix == ".eml":
            return p.resolve()
        raise InjectError(f"{fixture}: not a whitelisted fixture and not a readable .eml path.")
    raise InjectError(
        f"fixture must be one of {WHITELIST[kind] or ['(none enumerated at startup)']} for kind '{kind}'.")


def run(kind: str, fixture: str, *, by: str, origin: str, allow_path: bool = False,
        fresh: bool = False, timeout: int = 400) -> Dict[str, Any]:
    """Inject one fixture. Raises InjectError (with .status) for anything the
    caller did wrong or that this tenant must not do; returns the outcome dict
    otherwise — a handler that flagged (rc 2) is still a successful injection."""
    if not boundary_active():
        raise InjectError("Refused: the outbound boundary is not active on this tenant, "
                          "so an injected email could cause a real send.", 409)
    source = resolve(kind, fixture, allow_path=allow_path)
    run_id = f"inj_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    spec = KINDS[kind]
    script = config.SKILLS_ROOT / spec["script"]
    if not script.is_file():
        raise InjectError(f"handler missing: {script}", 500)

    forgot = None
    if fresh and kind == "showing":
        forgot = _forget_showing(source)

    staged = _stage(kind, source, run_id)
    argv = [sys.executable, str(script), *spec["argv"](str(staged), run_id)]
    started = _now()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, env=dict(os.environ),
                              timeout=timeout, cwd=str(script.parent))
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = 124, "", f"handler timed out after {timeout}s"

    last = (out.strip().splitlines() or [""])[-1]
    try:
        result = json.loads(last)
    except Exception:
        result = {"status": "unparsed", "stdout_tail": last[:300]}
    status = str(result.get("status", "unknown"))

    row = {
        "ts": started, "run_id": run_id, "kind": kind, "fixture": source.name,
        "path": str(source) if allow_path and source.parent != FIXTURES_DIR else None,
        "by": by, "origin": origin, "fresh_key_forgotten": forgot,
        "rc": rc, "status": status, "result": result,
        "stderr_tail": err.strip()[-500:] if rc not in (0, 2) else "",
    }
    _journal(row)
    _announce(kind, source.name, by, run_id, status, rc)
    return {k: v for k, v in row.items() if k != "path" or v}


def _cli(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="testco-inject", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=sorted(KINDS))
    ap.add_argument("fixture", nargs="?", help="whitelisted fixture name, or a path to an .eml")
    ap.add_argument("--fresh", action="store_true",
                    help="showing only: forget the once-per-showing key first")
    ap.add_argument("--list", action="store_true", help="print the whitelist and exit")
    ap.add_argument("--origin", default="cli")
    ap.add_argument("--by", default=os.environ.get("SUDO_USER") or os.environ.get("USER") or "operator")
    a = ap.parse_args(argv)
    if a.list:
        print(json.dumps(WHITELIST, indent=2))
        return 0
    if not a.fixture:
        ap.error("fixture is required (or --list)")
    try:
        res = run(a.kind, a.fixture, by=a.by, origin=a.origin, allow_path=True, fresh=a.fresh)
    except InjectError as exc:
        print(json.dumps({"status": "refused", "reason": str(exc), "http": exc.status}))
        return 1
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["rc"] in (0, 2) else 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
