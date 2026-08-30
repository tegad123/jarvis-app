"""Durable conversation threads and their message history.

STORAGE CHOICE: SQLite, not JSONL. The feed and the approvals queue are JSONL
because their records are IMMUTABLE EVENTS — you append one and never touch it
again. Threads are the opposite: `name` changes on rename, `archived` flips,
and `last_message_at` is rewritten on every single message. Modelling mutable
rows as an append-only log means either replaying the whole file with
last-write-wins on every read, or writing a compactor. Messages also want an
indexed per-thread query rather than a full-file scan.

SQLite gives all of that plus real durability across a service restart, which
is the entire point of this change, and it is already the local precedent —
task-manager/data/tasks.db and _shared/thread_registry.py are both SQLite.
WAL mode so a read during a write does not block.

NEVER HARD-DELETE. There is no DELETE statement anywhere in this module, by
design. Archiving sets a flag; messages are never removed. A conversation the
user archived is still a record of what the assistant was asked to do.
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

DB_PATH = Path(
    os.environ.get("JARVIS_THREADS_DB", str(config.HERMES_HOME / "state" / "jarvis_threads.db"))
).expanduser()

# The one thread that always exists. Seeded on first open; cannot be renamed,
# archived, or removed. The UI pins it above the channel list.
DIRECT_THREAD = "direct"
DIRECT_NAME = "Ask Jarvis"

# How much history we replay upstream per turn (bounds the bill), vs how much
# we keep (bounds nothing much — disk is cheap and the record is the point).
UPSTREAM_TURNS = 40

_local = threading.local()
_INIT_LOCK = threading.Lock()
_initialised = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    """One connection per thread; uvicorn runs handlers on a worker pool."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        _local.conn = conn
    return conn


def init() -> None:
    """Create the schema and seed the direct thread. Idempotent."""
    global _initialised
    with _INIT_LOCK:
        conn = _connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS threads (
                thread_id       TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                created_by      TEXT NOT NULL CHECK (created_by IN ('user','jarvis')),
                created_at      TEXT NOT NULL,
                last_message_at TEXT,
                archived        INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS messages (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL REFERENCES threads(thread_id),
                role      TEXT NOT NULL CHECK (role IN ('user','assistant')),
                content   TEXT NOT NULL,
                ts        TEXT NOT NULL,
                partial   INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, id);
            CREATE INDEX IF NOT EXISTS idx_threads_active
                ON threads(archived, last_message_at DESC);

            CREATE TABLE IF NOT EXISTS attachments (
                id        TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL REFERENCES threads(thread_id),
                filename  TEXT NOT NULL,
                mime      TEXT NOT NULL,
                size      INTEGER NOT NULL,
                ts        TEXT NOT NULL,
                path      TEXT NOT NULL,
                -- 1 when the bytes are inlined to the model, 0 when we only
                -- store them (documents; see app/attachments.py).
                readable  INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_attachments_thread
                ON attachments(thread_id, ts);

            -- WHO MAY SEE A CHANNEL. Membership is a row, so scoping is a
            -- WHERE clause rather than a rule re-derived in every handler.
            -- person_key is the join to identity.people, same as everywhere.
            --
            -- FULL HISTORY ON JOIN, by design: there is no `from_message_id`
            -- column and adding one later would be a behaviour change, not a
            -- migration. A deal channel is a case file — someone added to the
            -- deal needs what happened before they arrived, and a half-visible
            -- transcript is worse than none because you cannot tell it is half.
            CREATE TABLE IF NOT EXISTS thread_members (
                thread_id  TEXT NOT NULL REFERENCES threads(thread_id),
                person_key TEXT NOT NULL,
                added_by   TEXT,
                added_at   TEXT NOT NULL,
                PRIMARY KEY (thread_id, person_key)
            );

            CREATE INDEX IF NOT EXISTS idx_members_person
                ON thread_members(person_key, thread_id);

            -- Lightweight usage counters. Deliberately NOT in the activity
            -- feed: the feed is the customer's record of what Jarvis did, and
            -- product telemetry has no business in it. These exist so the
            -- "resize to 4 cores and run Kokoro locally" decision can be made
            -- from observed usage rather than a guess (docs/tts-investigation.md).
            CREATE TABLE IF NOT EXISTS counters (
                name       TEXT PRIMARY KEY,
                value      INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            );
            """
        )
        # messages.attachments holds a JSON array of attachment ids. Added
        # after the fact, so guard the ALTER for existing databases.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        if "attachments" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN attachments TEXT")

        # direct_for: the person whose private line this is; NULL for channels.
        # A direct line is not a channel with one member — membership is
        # grantable and a direct line must never be, so it is a different
        # column and not a row in thread_members.
        tcols = {r["name"] for r in conn.execute("PRAGMA table_info(threads)")}
        if "direct_for" not in tcols:
            conn.execute("ALTER TABLE threads ADD COLUMN direct_for TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_threads_direct_for "
                "ON threads(direct_for) WHERE direct_for IS NOT NULL"
            )
        conn.execute(
            """
            INSERT OR IGNORE INTO threads
                (thread_id, name, created_by, created_at, archived)
            VALUES (?, ?, 'jarvis', ?, 0)
            """,
            (DIRECT_THREAD, DIRECT_NAME, _now()),
        )
        conn.commit()
        _initialised = True
        _migrate_to_owner(conn)


def _migrate_to_owner(conn: sqlite3.Connection) -> None:
    """Give this box's pre-membership history an owner. Idempotent.

    Everything here predates per-user auth, so it was all created by, and for,
    the one person who had the passphrase. Two assignments:

      1. the original 'direct' thread becomes the OWNER's direct line;
      2. every existing channel gets the owner as a member.

    ASSIGN, NEVER GUESS: if the registry cannot name an owner we leave the rows
    alone rather than handing them to whoever sorts first. Unassigned threads
    are then invisible to members and visible to owner/admin — which is the
    safe direction to fail.
    """
    owner = _owner_key()
    if not owner:
        return
    conn.execute(
        "UPDATE threads SET direct_for = ? WHERE thread_id = ? AND direct_for IS NULL",
        (owner, DIRECT_THREAD),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO thread_members (thread_id, person_key, added_by, added_at)
        SELECT thread_id, ?, 'migration', ? FROM threads WHERE direct_for IS NULL
        """,
        (owner, _now()),
    )
    conn.commit()


def _owner_key() -> Optional[str]:
    """The registry's owner. Never raises — auth must survive a broken registry."""
    try:
        import sys
        if str(config.SHARED) not in sys.path:
            sys.path.insert(0, str(config.SHARED))
        import identity  # noqa: PLC0415
        return identity.owner_key()
    except Exception:
        return None


def _ensure() -> sqlite3.Connection:
    if not _initialised:
        init()
    return _connect()


class ThreadError(Exception):
    """Base for the two refusals callers must distinguish."""


class ThreadNotFound(ThreadError):
    pass


class ThreadProtected(ThreadError):
    """The direct line refuses rename/archive."""


def _row_to_thread(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "thread_id": row["thread_id"],
        "name": row["name"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "last_message_at": row["last_message_at"],
        "archived": bool(row["archived"]),
        "direct_for": row["direct_for"] if "direct_for" in row.keys() else None,
        # A direct line is one identified by direct_for, NOT by the legacy
        # id 'direct' — that id is now just the owner's line, kept because
        # its messages point at it.
        "is_direct": bool(row["direct_for"]) if "direct_for" in row.keys()
        else row["thread_id"] == DIRECT_THREAD,
    }


# --------------------------------------------------------------------- reads


def list_threads(include_archived: bool = False,
                 viewer: Optional[str] = None,
                 unscoped: bool = False) -> List[Dict[str, Any]]:
    """
    Direct first, then most-recently-active first.

    Threads with no messages yet sort after ones that have them (COALESCE to
    created_at would put a brand-new empty channel above a busy old one purely
    because it was made later; using '' keeps unused channels at the bottom
    where they belong).

    SCOPING, in two independent parts.

    CHANNELS follow `unscoped`. True — owner, admin, break-glass operator — and
    every channel is listed. False, and it is THE WHERE CLAUSE: channels the
    viewer is a member of. Note what is absent: no "or created it", no "or it
    has no members yet". A channel nobody was added to is invisible to a
    member, which is the safe direction to fail.

    DIRECT LINES FOLLOW NOBODY. A person's private line to the assistant is
    visible to that person and to no one else — not to an admin, not to the
    owner, not to break-glass. `unscoped` does not open it and there is no
    role that does.

    That is a deliberate choice and worth the words, because it is the one
    place this module refuses a privilege the org chart would grant. The
    screen calls it "Your direct line. Always here." If an owner can read it,
    that sentence is a lie, and a member who believed it typed things into a
    box that was never private. The same reasoning the operator body-gate
    rests on applies with more force here, not less: reading someone's private
    conversation should require their consent, which means a PIN reset they
    are present for — auditable — rather than a silent read from a role they
    never saw.
    """
    conn = _ensure()
    where, args = [], []
    if not include_archived:
        where.append("archived = 0")
    if unscoped:
        # Channels: all. Direct lines: only the viewer's own (none, for an
        # operator, who has no person — which is correct, not a gap).
        where.append("(direct_for IS NULL OR direct_for = ?)")
        args.append(viewer)
    else:
        where.append(
            "(direct_for = ? OR (direct_for IS NULL AND thread_id IN "
            " (SELECT thread_id FROM thread_members WHERE person_key = ?)))"
        )
        args += [viewer, viewer]
    sql = "SELECT * FROM threads"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += """
        ORDER BY (direct_for IS NOT NULL) DESC,
                 COALESCE(last_message_at, '') DESC,
                 created_at DESC
    """
    rows = conn.execute(sql, args).fetchall()
    return [_row_to_thread(r) for r in rows]


def get_thread(thread_id: str) -> Optional[Dict[str, Any]]:
    conn = _ensure()
    row = conn.execute(
        "SELECT * FROM threads WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    return _row_to_thread(row) if row else None


def thread_exists(thread_id: str, *, allow_archived: bool = True) -> bool:
    t = get_thread(thread_id)
    if t is None:
        return False
    return allow_archived or not t["archived"]


def get_history(thread_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Oldest-first messages for a thread."""
    conn = _ensure()
    if limit:
        rows = conn.execute(
            "SELECT * FROM (SELECT * FROM messages WHERE thread_id = ? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (thread_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? ORDER BY id ASC", (thread_id,)
        ).fetchall()
    import json as _json

    out = []
    for r in rows:
        raw = r["attachments"] if "attachments" in r.keys() else None
        try:
            ids = _json.loads(raw) if raw else []
        except Exception:
            ids = []
        out.append(
            {
                "thread_id": r["thread_id"],
                "role": r["role"],
                "content": r["content"],
                "ts": r["ts"],
                "partial": bool(r["partial"]),
                "attachments": ids,
            }
        )
    return out


def last_message(thread_id: str) -> Optional[Dict[str, Any]]:
    conn = _ensure()
    row = conn.execute(
        "SELECT * FROM messages WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
        (thread_id,),
    ).fetchone()
    if not row:
        return None
    return {"role": row["role"], "content": row["content"], "ts": row["ts"]}


# ---------------------------------------------------------------- membership


def members(thread_id: str) -> List[Dict[str, Any]]:
    """Who is in this channel. Structure, not content — safe for an operator."""
    conn = _ensure()
    rows = conn.execute(
        "SELECT person_key, added_by, added_at FROM thread_members "
        "WHERE thread_id = ? ORDER BY added_at ASC",
        (thread_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def is_member(thread_id: str, person_key: Optional[str]) -> bool:
    if not person_key:
        return False
    conn = _ensure()
    row = conn.execute(
        "SELECT 1 FROM thread_members WHERE thread_id = ? AND person_key = ?",
        (thread_id, person_key),
    ).fetchone()
    return row is not None


def can_see(thread_id: str, viewer: Optional[str], unscoped: bool = False) -> bool:
    """Does this session reach this thread?

    Mirrors list_threads exactly, and must keep mirroring it: a channel that
    appears in the list and then 404s on open is worse than either behaviour
    on its own. `unscoped` opens channels and NEVER opens a direct line.
    """
    t = get_thread(thread_id)
    if t is None:
        return False
    if t["direct_for"]:
        return viewer is not None and t["direct_for"] == viewer
    return unscoped or is_member(thread_id, viewer)


def add_member(thread_id: str, person_key: str, added_by: Optional[str] = None) -> Dict[str, Any]:
    """Add someone to a channel. They get the whole history — see the schema.

    Direct lines refuse. The whole point of a private line to the assistant is
    that it cannot be shared, so this is a refusal in the store rather than a
    check in the handler.
    """
    t = get_thread(thread_id)
    if t is None:
        raise ThreadNotFound(thread_id)
    if t["direct_for"]:
        raise ThreadProtected("A direct line cannot have members.")
    conn = _ensure()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO thread_members (thread_id, person_key, added_by, added_at) "
            "VALUES (?, ?, ?, ?)",
            (thread_id, person_key, added_by, _now()),
        )
    return {"thread_id": thread_id, "person_key": person_key, "members": members(thread_id)}


def remove_member(thread_id: str, person_key: str) -> Dict[str, Any]:
    """Revoke access.

    The one DELETE in this module, and it is deliberate: membership is a
    PERMISSION, not a record of what happened. Removing it takes away future
    reach and destroys nothing — every message the person could see is still
    there, and the feed still records what they did. The never-hard-delete rule
    at the top of this file is about content, and this is not content.
    """
    conn = _ensure()
    with conn:
        conn.execute(
            "DELETE FROM thread_members WHERE thread_id = ? AND person_key = ?",
            (thread_id, person_key),
        )
    return {"thread_id": thread_id, "members": members(thread_id)}


def ensure_direct(person_key: str) -> Dict[str, Any]:
    """This person's private line, created on first sight. Idempotent.

    Called on every login rather than at account creation, so a person who was
    added to the registry outside the app still gets one.
    """
    conn = _ensure()
    row = conn.execute(
        "SELECT * FROM threads WHERE direct_for = ?", (person_key,)
    ).fetchone()
    if row:
        return _row_to_thread(row)

    import uuid

    thread_id = f"dm_{uuid.uuid4().hex[:12]}"
    with conn:
        conn.execute(
            "INSERT INTO threads (thread_id, name, created_by, created_at, archived, direct_for) "
            "VALUES (?, ?, 'jarvis', ?, 0, ?)",
            (thread_id, DIRECT_NAME, _now(), person_key),
        )
    return get_thread(thread_id)  # type: ignore[return-value]


def direct_thread_id(person_key: Optional[str]) -> Optional[str]:
    if not person_key:
        return None
    conn = _ensure()
    row = conn.execute(
        "SELECT thread_id FROM threads WHERE direct_for = ?", (person_key,)
    ).fetchone()
    return row["thread_id"] if row else None


# -------------------------------------------------------------------- writes


def create_thread(name: str, created_by: str = "user",
                  creator: Optional[str] = None) -> Dict[str, Any]:
    """New channel. `created_by` decides whether the UI shows the spark mark."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Channel name is required.")
    if created_by not in ("user", "jarvis"):
        raise ValueError("created_by must be 'user' or 'jarvis'.")

    conn = _ensure()
    # Human-readable, collision-proof id. Not derived from the name, because a
    # rename must not change identity.
    import uuid

    thread_id = f"ch_{uuid.uuid4().hex[:12]}"
    with conn:
        conn.execute(
            "INSERT INTO threads (thread_id, name, created_by, created_at, archived) "
            "VALUES (?, ?, ?, ?, 0)",
            (thread_id, name[:120], created_by, _now()),
        )
        # The creator is a member, or they would immediately lose sight of the
        # channel they just made. `creator` is None when Jarvis opens a deal
        # channel on its own — those start empty and are populated by an admin.
        if creator:
            conn.execute(
                "INSERT OR IGNORE INTO thread_members "
                "(thread_id, person_key, added_by, added_at) VALUES (?, ?, ?, ?)",
                (thread_id, creator, creator, _now()),
            )
    return get_thread(thread_id)  # type: ignore[return-value]


def rename_thread(thread_id: str, name: str) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("Channel name is required.")
    t = get_thread(thread_id)
    if t and t["direct_for"]:
        raise ThreadProtected("The direct line cannot be renamed.")
    conn = _ensure()
    cur = conn.execute(
        "UPDATE threads SET name = ? WHERE thread_id = ?", (name[:120], thread_id)
    )
    if cur.rowcount == 0:
        raise ThreadNotFound(thread_id)
    conn.commit()
    return get_thread(thread_id)  # type: ignore[return-value]


def set_archived(thread_id: str, archived: bool = True) -> Dict[str, Any]:
    """
    Flip the archive flag. This is the ONLY removal mechanism — nothing in this
    module deletes a thread or a message.
    """
    t = get_thread(thread_id)
    if t and t["direct_for"]:
        raise ThreadProtected("The direct line cannot be archived.")
    conn = _ensure()
    cur = conn.execute(
        "UPDATE threads SET archived = ? WHERE thread_id = ?",
        (1 if archived else 0, thread_id),
    )
    if cur.rowcount == 0:
        raise ThreadNotFound(thread_id)
    conn.commit()
    return get_thread(thread_id)  # type: ignore[return-value]


def append_messages(thread_id: str, *messages: Dict[str, Any]) -> None:
    """Append a turn and bump the thread's activity clock, in one transaction."""
    if not messages:
        return
    conn = _ensure()
    with conn:  # implicit transaction
        latest = None
        for m in messages:
            ts = m.get("ts") or _now()
            latest = ts
            import json as _json

            att = m.get("attachments") or []
            conn.execute(
                "INSERT INTO messages (thread_id, role, content, ts, partial, attachments) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    m["role"],
                    m["content"],
                    ts,
                    1 if m.get("partial") else 0,
                    _json.dumps(att) if att else None,
                ),
            )
        if latest:
            conn.execute(
                "UPDATE threads SET last_message_at = ? WHERE thread_id = ?",
                (latest, thread_id),
            )


def counts() -> Dict[str, int]:
    conn = _ensure()
    row = conn.execute(
        "SELECT "
        " (SELECT COUNT(*) FROM threads WHERE archived = 0) AS active,"
        " (SELECT COUNT(*) FROM threads WHERE archived = 1) AS archived,"
        " (SELECT COUNT(*) FROM threads WHERE archived = 0 AND direct_for IS NULL) AS channels,"
        " (SELECT COUNT(*) FROM messages) AS messages",
    ).fetchone()
    return {
        "active": row["active"],
        "archived": row["archived"],
        "channels": row["channels"],
        "messages": row["messages"],
    }


# ------------------------------------------------------------------ counters


def bump_counter(name: str, by: int = 1) -> int:
    """Increment a usage counter and return the new value. Never raises: a
    counter must not fail the work it counts. When the store itself cannot be
    reached the failure is announced ONCE per process (app/metrics.py) — a
    results slide built on silent zeros is worse than one missing bump."""
    try:
        conn = _ensure()
        with conn:
            conn.execute(
                "INSERT INTO counters (name, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = value + excluded.value, "
                "updated_at = excluded.updated_at",
                (name, by, _now()),
            )
        row = conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
    except Exception as exc:
        try:
            from . import metrics
            metrics._announce_once(exc)
        except Exception:
            pass
        return 0
    # The same bump lands in a daily bucket so "this month" and "last 7 days"
    # exist. bump_day never raises; it announces its own failure the same way.
    try:
        from . import metrics
        metrics.bump_day(name, by)
    except Exception:
        pass
    return int(row["value"]) if row else 0


def all_counters() -> Dict[str, Any]:
    conn = _ensure()
    return {
        r["name"]: {"value": r["value"], "updated_at": r["updated_at"]}
        for r in conn.execute("SELECT * FROM counters ORDER BY name")
    }
