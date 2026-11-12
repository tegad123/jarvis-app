"""Owner-only removal of the business data this process stores for one tenant.

One Jarvis process is one tenant: its SQLite files, the Postgres database
named by JARVIS_PG_DSN, and the files under that tenant's home. This module
deletes those local records. It does not delete a Gmail mailbox, a backup, or
a copy Anthropic already has, and it does not touch another process's files.

Sign-in is blocked only after the local rows commit. A failure before that
rolls the databases back and puts moved files back. A challenge nonce is
single-use either way, so a captured request cannot be replayed.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import accounts, attachments, config, leads_store, tasks_store, threads

log = logging.getLogger("app.tenant_delete")

CONFIRM_HEADER = "x-jarvis-confirm"
CONFIRM_VALUE = "delete-tenant"
_SLUG_RE = re.compile(r"[a-z][a-z0-9-]{1,30}")
_TABLE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CONFIG_NAMES = {"identity.json", "channels.json", "runtime.json"}
PLATFORM_TABLES = ("held_actions", "grants", "approvals", "policies", "usage", "actions")

# Always true of this process. The response lists these even when every local
# row was removed, so the screen cannot call that a deletion of Google,
# backups, or another company.
EXTERNAL_LIMITS = (
    "The Gmail mailbox was not deleted or disconnected at Google. Tokens stored "
    "by this app are removed; mail already sent is still in that mailbox.",
    "Backups, host environment files, and prompts already sent to Anthropic are "
    "not deleted by this action.",
    "Another tenant's files are not on this process and were not touched.",
)


class TenantDeleteError(Exception):
    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.message = message
        self.status = status


def phrase_for(slug: str) -> str:
    return f"delete {slug}"


def slug() -> str:
    return config.tenant_slug()


def closed() -> bool:
    path = _closure_dir() / "tombstone.json"
    return path.is_file()


def impact() -> Dict[str, Any]:
    """What the next deletion would mean. No side effects."""
    name = slug()
    if not _SLUG_RE.fullmatch(name):
        raise TenantDeleteError(
            "This deployment has no tenant slug, so team deletion is refused.", 422)
    return {
        "action": "delete-team",
        "slug": name,
        "phrase": phrase_for(name),
        "removes": [
            "Every person's sign-in on this team",
            "Leads, contact history, and the import send log stored here",
            "Tasks and digest records stored here",
            "Channels, messages, and files stored for this team",
            "This app's copies of Google tokens, saved site logins, and iMessage text",
            "Browser profiles (sign-ins saved in the browser), connected sites, and MLS search results",
            "The activity feed and follow-up schedules stored here",
        ],
        "not_removed": list(EXTERNAL_LIMITS) + [
            "A closure marker stays so sign-in remains off. It has no leads or messages.",
            "If the Postgres role cannot delete the append-only audit log, those rows stay "
            "and the result says so.",
        ],
    }


def issue_challenge(person_key: str) -> Dict[str, Any]:
    info = impact()
    nonce = secrets.token_urlsafe(24)
    expires = int(time.time()) + 600
    conn = _closure_db()
    conn.execute(
        "INSERT INTO challenges (nonce, person_key, expires_at, consumed_at) VALUES (?,?,?,NULL)",
        (nonce, person_key, expires),
    )
    conn.commit()
    return {**info, "nonce": nonce, "expires_in": 600}


def erase(person_key: str, nonce: str, phrase: str) -> Dict[str, Any]:
    """Delete this process's tenant records. The nonce is consumed first."""
    info = impact()
    if phrase != info["phrase"]:
        raise TenantDeleteError(
            "Type the tenant phrase exactly. Nothing was deleted.", 422)
    if not _consume(nonce, person_key):
        raise TenantDeleteError(
            "That confirmation has already been used or has expired. Nothing was deleted.", 409)
    layout = _layout()
    failpoint = _take_failpoint(layout["closure"])
    still: List[str] = list(EXTERNAL_LIMITS)
    try:
        removed, platform_left = _delete_local(layout, failpoint)
    except TenantDeleteError:
        raise
    except Exception as exc:
        log.exception("tenant_delete: rolled back (%s)", type(exc).__name__)
        extra = f" ({type(exc).__name__}: {exc})" if os.environ.get("JARVIS_TENANT_DELETE_TEST") == "1" else ""
        raise TenantDeleteError(
            "Deletion stopped before it was saved. This team's data is still here." + extra, 500)
    still.extend(platform_left)
    from . import browser  # noqa: PLC0415
    profiles = browser.erase_all()
    if profiles == "erased":
        removed.append("browser_profiles")
    elif profiles == "failed":
        still.append("The browser profiles (sign-ins saved in the browser) could not be removed. "
                     "Sign-in is off; the operator's teardown removes them.")
    still.append("A closure marker stays so sign-in remains off. It has no leads or messages.")
    log.warning("tenant_delete: local records removed for tenant %s by %s", info["slug"], person_key)
    return {
        "ok": True,
        "action": "team-deleted",
        "deleted_local": True,
        "sign_in_blocked": True,
        "removed": removed,
        "still_exists": still,
        "detail": "This team's records stored by this Jarvis were deleted, and sign-in is off. "
                  "The list of what still exists is in still_exists. This is not a deletion "
                  "of the Gmail mailbox, backups, or another tenant.",
    }


def reconcile() -> None:
    """Finish or undo a deletion that the process did not get to the end of.

    A tombstone with rows still in a database is a failed attempt: put moved
    files back and remove the tombstone so the team is not locked out of data
    that is still there. A tombstone with empty databases stays.
    """
    try:
        layout = _layout()
    except Exception:
        log.error("tenant_delete: could not read paths during reconcile")
        return
    tomb = layout["closure"] / "tombstone.json"
    trash = layout["closure"] / "trash"
    has_rows = any(_has_rows(path) for path in layout["databases"])
    if tomb.is_file() and has_rows:
        _restore_trash(trash)
        tomb.unlink(missing_ok=True)
        log.error("tenant_delete: a deletion did not finish; sign-in is on again and files were put back")
        return
    if tomb.is_file():
        _unlink_tree(trash)


def _delete_local(layout: Dict[str, Any], failpoint: Optional[str]) -> tuple:
    conns = _business_connections()
    platform = _open_platform(layout)
    begun = []
    try:
        for label, conn in conns:
            if conn.in_transaction:
                conn.commit()
            # foreign_keys cannot be changed inside a transaction, and the
            # stores turn it on. Turn it off first so child rows can go in
            # the same pass as the parents.
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("BEGIN IMMEDIATE")
            begun.append(conn)
            _wipe_sqlite(conn)
        platform_left = []
        if platform is not None:
            platform_left = _wipe_platform(platform)
        staged = _stage_files(layout)
        if failpoint == "after_stage":
            raise RuntimeError("test failpoint after_stage")
        _write_tombstone(layout["closure"])
        for conn in begun:
            conn.commit()
        begun = []
        if platform is not None and not platform_left:
            platform.commit()
        elif platform is not None:
            platform.rollback()
        _unlink_tree(layout["closure"] / "trash")
        _clear_memory()
        removed = [label for label, _ in conns]
        if platform is not None and not platform_left:
            removed.append("platform")
        return removed, platform_left
    except Exception:
        for conn in begun:
            try:
                conn.rollback()
            except Exception:
                log.exception("tenant_delete: rollback failed")
        if platform is not None:
            try:
                platform.rollback()
            except Exception:
                log.exception("tenant_delete: platform rollback failed")
        _restore_trash(layout["closure"] / "trash")
        tomb = layout["closure"] / "tombstone.json"
        if tomb.is_file() and any(_has_rows(path) for path in layout["databases"]):
            tomb.unlink()
        raise
    finally:
        if platform is not None:
            platform.close()


def _business_connections() -> List[tuple]:
    return [
        ("accounts", accounts._ensure()),
        ("leads", leads_store._ensure()),
        ("tasks", tasks_store._ensure()),
        ("threads", threads._ensure()),
    ]


def _wipe_sqlite(conn: sqlite3.Connection) -> List[str]:
    names = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for name in names:
        if not _TABLE_RE.fullmatch(name):
            raise TenantDeleteError("A table name in this database is not one this wipe will touch.")
        conn.execute(f'DELETE FROM "{name}"')
    return names


def _open_platform(layout: Dict[str, Any]):
    path = layout.get("platform_sqlite")
    dsn = layout.get("platform_dsn")
    if path:
        conn = sqlite3.connect(str(path))
        conn.isolation_level = None  # we issue BEGIN ourselves
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        return conn
    if not dsn:
        return None
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415
    conn = psycopg.connect(dsn, autocommit=False, row_factory=dict_row, connect_timeout=5)
    return conn


def _wipe_platform(conn) -> List[str]:
    """Delete platform rows. A privilege or append-only failure leaves the
    platform transaction for the caller to roll back, and is reported instead
    of being called a successful deletion."""
    try:
        for name in PLATFORM_TABLES:
            conn.execute(f"DELETE FROM {name}")
    except Exception as exc:
        return [
            "Platform tables were not deleted "
            f"({type(exc).__name__}). The app role cannot remove the append-only audit log. "
            "Dropping that database is a separate operational step, and this result does not claim it was done."
        ]
    return []


def _stage_files(layout: Dict[str, Any]) -> List[Path]:
    trash = layout["closure"] / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    moved = []
    for path in layout["files"] + layout["directories"]:
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink():
            raise TenantDeleteError(f"Refusing to follow a symlink at {path.name}.")
        dest = trash / f"{len(moved)}-{path.name}"
        path.rename(dest)
        moved.append(path)
        (trash / f"{len(moved)-1}-{path.name}.from").write_text(str(path))
    return moved


def _restore_trash(trash: Path) -> None:
    if not trash.is_dir():
        return
    for marker in sorted(trash.glob("*.from")):
        dest = Path(marker.read_text())
        src = marker.with_suffix("")
        # marker is "0-name.from", src should be "0-name"
        src = Path(str(marker)[: -len(".from")])
        if src.exists() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
        marker.unlink(missing_ok=True)
    _unlink_tree(trash)


def _unlink_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        for child in sorted(path.iterdir(), reverse=True):
            _unlink_tree(child)
        path.rmdir()
        return
    path.unlink()


def _write_tombstone(closure: Path) -> None:
    closure.mkdir(parents=True, exist_ok=True)
    target = closure / "tombstone.json"
    tmp = closure / "tombstone.json.tmp"
    tmp.write_text('{"closed":true,"kind":"team"}\n')
    os.replace(tmp, target)


def _clear_memory() -> None:
    try:
        from . import browser, leads_upload  # noqa: PLC0415
        browser._needs_you.clear()
        leads_upload._pending.clear()
    except Exception:
        log.error("tenant_delete: in-memory import tokens could not be cleared")


def _layout() -> Dict[str, Any]:
    hermes = config.HERMES_HOME.resolve()
    skills = config.SKILLS_ROOT.resolve()
    roots = [hermes]
    if skills != hermes:
        roots.append(skills)
    closure = _closure_dir()
    databases = [accounts.DB_PATH, leads_store.DB_PATH, tasks_store.DB_PATH, threads.DB_PATH]
    for path in databases:
        _require_under(path, [hermes], kind="database")
    files = []
    for key in ("JARVIS_IDENTITY_CONFIG", "JARVIS_RUNTIME_CONFIG", "JARVIS_ACTIVITY_FEED", "JARVIS_TEAM_FILE"):
        raw = os.environ.get(key, "").strip()
        if raw:
            files.append(Path(raw).expanduser())
    ident = os.environ.get("JARVIS_IDENTITY_CONFIG", "").strip()
    if ident:
        files.append(Path(ident).expanduser().with_name("channels.json"))
    files.append(accounts.DB_PATH.with_name("lead_followup_state.json"))
    files.append(accounts.DB_PATH.with_name("text_followup_state.json"))
    files.append(accounts.DB_PATH.with_name("text_followup_state.json.lock"))
    files.append(accounts.DB_PATH.with_name("lead_followup_state.json.lock"))
    kept_files = []
    for path in files:
        if path.name not in _CONFIG_NAMES and path.name not in {
            "activity_feed.jsonl", "team.json", "lead_followup_state.json",
            "text_followup_state.json", "lead_followup_state.json.lock",
            "text_followup_state.json.lock",
        }:
            continue
        if _is_under(path, roots):
            kept_files.append(path)
    directories = []
    for path in (attachments.DATA_DIR, _spool_dir()):
        if path.exists() or True:
            if _is_under(path, [hermes]) and path.resolve() != hermes:
                directories.append(path)
    platform_sqlite = os.environ.get("JARVIS_PLATFORM_SQLITE", "").strip()
    platform_path = Path(platform_sqlite).expanduser() if platform_sqlite else None
    if platform_path is not None:
        _require_under(platform_path, [hermes], kind="database")
    return {
        "roots": roots,
        "closure": closure,
        "databases": databases,
        "files": kept_files,
        "directories": directories,
        "platform_sqlite": platform_path,
        "platform_dsn": os.environ.get("JARVIS_PG_DSN", "").strip(),
    }


def _spool_dir() -> Path:
    raw = os.environ.get("JARVIS_TRANSCRIBE_SPOOL", "").strip()
    if not raw:
        return config.HERMES_HOME / "state" / "transcribe-spool"
    return Path(raw).expanduser()


def _closure_dir() -> Path:
    raw = os.environ.get("JARVIS_CLOSURE_DIR", "").strip()
    path = Path(raw).expanduser() if raw else config.HERMES_HOME / "state" / "closure"
    return path


def _closure_db() -> sqlite3.Connection:
    directory = _closure_dir()
    directory.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(directory / "challenges.sqlite"))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS challenges (
               nonce TEXT PRIMARY KEY,
               person_key TEXT NOT NULL,
               expires_at INTEGER NOT NULL,
               consumed_at TEXT
           )"""
    )
    return conn


def _consume(nonce: str, person_key: str) -> bool:
    conn = _closure_db()
    now = int(time.time())
    cur = conn.execute(
        "UPDATE challenges SET consumed_at=? WHERE nonce=? AND person_key=? "
        "AND consumed_at IS NULL AND expires_at>?",
        (str(now), nonce, person_key, now),
    )
    conn.commit()
    return cur.rowcount == 1


def _take_failpoint(closure: Path) -> Optional[str]:
    """A one-shot test hook. Ignored unless the test env is set, so a request
    cannot ask for it."""
    if os.environ.get("JARVIS_TENANT_DELETE_TEST") != "1":
        return None
    path = closure / "failpoint"
    if not path.is_file():
        return None
    value = path.read_text().strip()
    path.unlink()
    return value or None


def _require_under(path: Path, roots: Sequence[Path], kind: str) -> None:
    if not _is_under(path, roots):
        raise TenantDeleteError(f"Refusing to touch a {kind} outside this tenant's directories.")


def _is_under(path: Path, roots: Sequence[Path]) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        return False
    if resolved == Path(resolved.anchor):
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _has_rows(path: Path) -> bool:
    if not path.is_file():
        return False
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for name in names:
            if not _TABLE_RE.fullmatch(name):
                continue
            if conn.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone():
                return True
        return False
    finally:
        conn.close()
