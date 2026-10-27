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

import logging
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

log = logging.getLogger("app.threads")

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
        # messages.actions: JSON array of the app's own chat controls shown
        # under an assistant reply (app/agent_status.CHAT_ACTIONS), so a
        # reload can draw them again. Never model text.
        if "actions" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN actions TEXT")
        # messages.author: the person_key of whoever wrote a 'user' row (human
        # approval 2026-10-27). NULL on assistant rows and on every row written
        # before it existed. ASSIGN, NEVER GUESS: older rows stay NULL and the
        # screen says "Earlier message" rather than naming someone who may not
        # have written it.
        if "author" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN author TEXT")

        # How far each person has read each thread: the sidebar's unread dot.
        # A position, not content — never a copy of what was said.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS thread_reads (
                   thread_id    TEXT NOT NULL REFERENCES threads(thread_id),
                   person_key   TEXT NOT NULL,
                   last_read_id INTEGER NOT NULL DEFAULT 0,
                   updated_at   TEXT NOT NULL,
                   PRIMARY KEY (thread_id, person_key)
               )"""
        )
        # Workspace choices an admin makes for channels (app/channels.py).
        conn.execute(
            """CREATE TABLE IF NOT EXISTS channel_settings (
                   key        TEXT PRIMARY KEY,
                   value      TEXT NOT NULL,
                   updated_by TEXT,
                   updated_at TEXT NOT NULL
               )"""
        )

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
        # Sidebar sections (Listings, Buyers, …). A category is structure, not
        # access: who sees a channel is still only thread_members.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS channel_categories (
                   category_id TEXT PRIMARY KEY,
                   name        TEXT NOT NULL,
                   position    INTEGER NOT NULL,
                   created_by  TEXT,
                   created_at  TEXT NOT NULL
               )"""
        )
        if "category_id" not in tcols:
            conn.execute("ALTER TABLE threads ADD COLUMN category_id TEXT")
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
        _seed_categories(conn)
        _seed_reads(conn)


READS_SEEDED_FLAG = "thread_reads_seeded"


def _seed_reads(conn: sqlite3.Connection) -> None:
    """Once, when unread tracking arrives: everyone is caught up on what was
    already there. Without this every old channel would light up as unread on
    the first load after the upgrade, which says something that is not true."""
    if conn.execute("SELECT 1 FROM counters WHERE name = ?", (READS_SEEDED_FLAG,)).fetchone():
        return
    now = _now()
    with conn:
        n = conn.execute(
            """INSERT OR IGNORE INTO thread_reads (thread_id, person_key, last_read_id, updated_at)
               SELECT m.thread_id, m.person_key,
                      COALESCE((SELECT MAX(id) FROM messages WHERE thread_id = m.thread_id), 0), ?
                 FROM thread_members m""", (now,)).rowcount
        n += conn.execute(
            """INSERT OR IGNORE INTO thread_reads (thread_id, person_key, last_read_id, updated_at)
               SELECT t.thread_id, t.direct_for,
                      COALESCE((SELECT MAX(id) FROM messages WHERE thread_id = t.thread_id), 0), ?
                 FROM threads t WHERE t.direct_for IS NOT NULL""", (now,)).rowcount
        conn.execute("INSERT INTO counters (name, value, updated_at) VALUES (?, 1, ?)", (READS_SEEDED_FLAG, now))
    log.info("threads: unread tracking seeded %d read positions at the latest message", n)


SEEDED_FLAG = "channel_categories_seeded"


def _seed_categories(conn: sqlite3.Connection) -> None:
    """The tenant's starting sections (app.channels.categories), once. After
    that they are the admins' to rename, reorder and delete; a later config
    change never brings back one an admin removed."""
    if conn.execute("SELECT 1 FROM counters WHERE name = ?", (SEEDED_FLAG,)).fetchone():
        return
    try:
        if str(config.SHARED) not in sys.path:
            sys.path.insert(0, str(config.SHARED))
        import tenant_config as tc  # noqa: PLC0415
        names = tc.get("app.channels.categories", []) or []
    except Exception as exc:
        log.error("threads: could not read app.channels.categories (%s); no sections seeded", exc)
        return
    if not isinstance(names, list) or not names:
        return
    import uuid
    with conn:
        for i, name in enumerate(n for n in names if isinstance(n, str) and n.strip()):
            conn.execute(
                "INSERT INTO channel_categories (category_id, name, position, created_by, created_at) "
                "VALUES (?,?,?,?,?)", (f"cat_{uuid.uuid4().hex[:10]}", name.strip()[:60], i, "setup", _now()))
        conn.execute("INSERT INTO counters (name, value, updated_at) VALUES (?, 1, ?)", (SEEDED_FLAG, _now()))
    log.info("threads: seeded %d channel categories from tenant config", len(names))


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
        "category_id": row["category_id"] if "category_id" in row.keys() else None,
    }


# ------------------------------------------------------------------ categories


class CategoryNotFound(ThreadError):
    pass


def categories() -> List[Dict[str, Any]]:
    conn = _ensure()
    return [dict(r) for r in conn.execute(
        "SELECT category_id, name, position FROM channel_categories ORDER BY position, created_at")]


def _category_exists(conn: sqlite3.Connection, category_id: str) -> bool:
    return conn.execute("SELECT 1 FROM channel_categories WHERE category_id = ?",
                        (category_id,)).fetchone() is not None


def create_category(name: str, by: Optional[str]) -> Dict[str, Any]:
    name = (name or "").strip()[:60]
    if not name:
        raise ValueError("Category name is required.")
    conn = _ensure()
    if conn.execute("SELECT 1 FROM channel_categories WHERE lower(name) = lower(?)", (name,)).fetchone():
        raise ValueError("There's already a category with that name.")
    import uuid
    cid = f"cat_{uuid.uuid4().hex[:10]}"
    pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM channel_categories").fetchone()[0]
    with conn:
        conn.execute("INSERT INTO channel_categories (category_id, name, position, created_by, created_at) "
                     "VALUES (?,?,?,?,?)", (cid, name, pos, by, _now()))
    return {"category_id": cid, "name": name, "position": pos}


def update_category(category_id: str, name: Optional[str] = None,
                    position: Optional[int] = None) -> Dict[str, Any]:
    conn = _ensure()
    if not _category_exists(conn, category_id):
        raise CategoryNotFound(category_id)
    with conn:
        if name is not None:
            name = name.strip()[:60]
            if not name:
                raise ValueError("Category name is required.")
            if conn.execute("SELECT 1 FROM channel_categories WHERE lower(name) = lower(?) AND category_id != ?",
                            (name, category_id)).fetchone():
                raise ValueError("There's already a category with that name.")
            conn.execute("UPDATE channel_categories SET name = ? WHERE category_id = ?", (name, category_id))
        if position is not None:
            order = [r["category_id"] for r in conn.execute(
                "SELECT category_id FROM channel_categories ORDER BY position, created_at")]
            order.remove(category_id)
            order.insert(max(0, min(int(position), len(order))), category_id)
            for i, cid in enumerate(order):
                conn.execute("UPDATE channel_categories SET position = ? WHERE category_id = ?", (i, cid))
    return next(c for c in categories() if c["category_id"] == category_id)


def delete_category(category_id: str) -> int:
    """Remove a section. Its channels stay, ungrouped; nothing else changes.
    Returns how many channels were moved out."""
    conn = _ensure()
    if not _category_exists(conn, category_id):
        raise CategoryNotFound(category_id)
    with conn:
        moved = conn.execute("UPDATE threads SET category_id = NULL WHERE category_id = ?",
                             (category_id,)).rowcount
        conn.execute("DELETE FROM channel_categories WHERE category_id = ?", (category_id,))
    return moved


def set_category(thread_id: str, category_id: Optional[str]) -> Dict[str, Any]:
    t = get_thread(thread_id)
    if t is None:
        raise ThreadNotFound(thread_id)
    if t["direct_for"]:
        raise ThreadProtected("The direct line isn't in a category.")
    conn = _ensure()
    if category_id is not None and not _category_exists(conn, category_id):
        raise CategoryNotFound(category_id)
    with conn:
        conn.execute("UPDATE threads SET category_id = ? WHERE thread_id = ?", (category_id, thread_id))
    return get_thread(thread_id)  # type: ignore[return-value]


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


def get_history(thread_id: str, limit: Optional[int] = None,
                after_id: int = 0) -> List[Dict[str, Any]]:
    """Oldest-first messages for a thread; with after_id, only newer ones
    (a channel's poll asks for what arrived since the last id it holds)."""
    conn = _ensure()
    if after_id:
        rows = conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? AND id > ? ORDER BY id ASC",
            (thread_id, int(after_id)),
        ).fetchall()
    elif limit:
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
        raw_acts = r["actions"] if "actions" in r.keys() else None
        try:
            acts = _json.loads(raw_acts) if raw_acts else []
        except Exception:
            acts = []
        out.append(
            {
                "id": r["id"],
                "thread_id": r["thread_id"],
                "author": r["author"] if "author" in r.keys() else None,
                "role": r["role"],
                "content": r["content"],
                "ts": r["ts"],
                "partial": bool(r["partial"]),
                "attachments": ids,
                "actions": acts,
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
    return {"role": row["role"], "content": row["content"], "ts": row["ts"],
            "author": row["author"] if "author" in row.keys() else None}


def max_message_id(thread_id: str) -> int:
    conn = _ensure()
    row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM messages WHERE thread_id = ?",
                       (thread_id,)).fetchone()
    return int(row[0])


# --------------------------------------------------------------------- unread


def mark_read(thread_id: str, person_key: str, upto_id: int) -> int:
    """Move this person's read position forward, never back, and never past
    the thread's own last message (an id from another thread means nothing
    here). Returns the position now stored."""
    conn = _ensure()
    upto = max(0, min(int(upto_id), max_message_id(thread_id)))
    with conn:
        conn.execute(
            """INSERT INTO thread_reads (thread_id, person_key, last_read_id, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(thread_id, person_key) DO UPDATE SET
                 last_read_id = MAX(last_read_id, excluded.last_read_id),
                 updated_at = excluded.updated_at""",
            (thread_id, person_key, upto, _now()),
        )
    row = conn.execute("SELECT last_read_id FROM thread_reads WHERE thread_id = ? AND person_key = ?",
                       (thread_id, person_key)).fetchone()
    return int(row["last_read_id"])


def _set_read(conn: sqlite3.Connection, thread_id: str, person_key: str) -> None:
    """Caught up as of now: a new member, a new channel, a new direct line."""
    conn.execute(
        """INSERT INTO thread_reads (thread_id, person_key, last_read_id, updated_at)
           VALUES (?, ?, COALESCE((SELECT MAX(id) FROM messages WHERE thread_id = ?), 0), ?)
           ON CONFLICT(thread_id, person_key) DO UPDATE SET
             last_read_id = excluded.last_read_id, updated_at = excluded.updated_at""",
        (thread_id, person_key, thread_id, _now()),
    )


def unread(person_key: Optional[str], thread_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """{thread_id: {"count", "messages"}} for the threads this person has a
    read position in: what arrived after it, not written by them.

    A thread with no position (an admin looking at a channel they are not in)
    has nothing unread: unread belongs to the threads you are part of.
    `messages` is the unread rows' text and author, newest last and capped, so
    the caller can count mentions without a second query."""
    if not person_key or not thread_ids:
        return {}
    conn = _ensure()
    marks = ",".join("?" for _ in thread_ids)
    pos = {r["thread_id"]: r["last_read_id"] for r in conn.execute(
        f"SELECT thread_id, last_read_id FROM thread_reads WHERE person_key = ? AND thread_id IN ({marks})",
        (person_key, *thread_ids))}
    out: Dict[str, Dict[str, Any]] = {}
    for tid, last in pos.items():
        rows = conn.execute(
            "SELECT role, content, author FROM messages WHERE thread_id = ? AND id > ? "
            "AND (author IS NULL OR author != ?) ORDER BY id DESC LIMIT 200",
            (tid, last, person_key)).fetchall()
        if rows:
            out[tid] = {"count": len(rows), "messages": [dict(r) for r in reversed(rows)]}
    return out


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
        added = conn.execute(
            "INSERT OR IGNORE INTO thread_members (thread_id, person_key, added_by, added_at) "
            "VALUES (?, ?, ?, ?)",
            (thread_id, person_key, added_by, _now()),
        ).rowcount
        # They see the whole history (see the schema) but start caught up: being
        # added is not a pile of unread messages someone else already handled.
        if added:
            _set_read(conn, thread_id, person_key)
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
        _set_read(conn, thread_id, person_key)
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
                  creator: Optional[str] = None,
                  category_id: Optional[str] = None) -> Dict[str, Any]:
    """New channel. `created_by` decides whether the UI shows the spark mark."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Channel name is required.")
    if created_by not in ("user", "jarvis"):
        raise ValueError("created_by must be 'user' or 'jarvis'.")

    conn = _ensure()
    if category_id is not None and not _category_exists(conn, category_id):
        raise CategoryNotFound(category_id)
    # Human-readable, collision-proof id. Not derived from the name, because a
    # rename must not change identity.
    import uuid

    thread_id = f"ch_{uuid.uuid4().hex[:12]}"
    with conn:
        conn.execute(
            "INSERT INTO threads (thread_id, name, created_by, created_at, archived, category_id) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (thread_id, name[:120], created_by, _now(), category_id),
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
            _set_read(conn, thread_id, creator)
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


def append_messages(thread_id: str, *messages: Dict[str, Any]) -> List[int]:
    """Append a turn and bump the thread's activity clock, in one transaction.
    Returns the new rows' ids, in order."""
    if not messages:
        return []
    conn = _ensure()
    ids: List[int] = []
    with conn:  # implicit transaction
        latest = None
        for m in messages:
            ts = m.get("ts") or _now()
            latest = ts
            import json as _json

            att = m.get("attachments") or []
            acts = m.get("actions") or []
            cur = conn.execute(
                "INSERT INTO messages (thread_id, role, content, ts, partial, attachments, actions, author) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    m["role"],
                    m["content"],
                    ts,
                    1 if m.get("partial") else 0,
                    _json.dumps(att) if att else None,
                    _json.dumps(acts) if acts else None,
                    # Only a person writes a 'user' row; Jarvis is never an author.
                    m.get("author") if m["role"] == "user" else None,
                ),
            )
            ids.append(int(cur.lastrowid))
        if latest:
            conn.execute(
                "UPDATE threads SET last_message_at = ? WHERE thread_id = ?",
                (latest, thread_id),
            )
    return ids


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
    results slide built on silent zeros is worse than one missing bump.

    In broker mode (JARVIS_APP_BROKER_URL, the gateway's processes on a
    two-user tenant) this store is app-only, so the bump goes to the app."""
    if os.environ.get("JARVIS_APP_BROKER_URL"):
        return _bump_via_broker(name, by)
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


def _bump_via_broker(name: str, by: int) -> int:
    try:
        if str(config.SHARED) not in sys.path:
            sys.path.insert(0, str(config.SHARED))
        import app_broker  # noqa: PLC0415
        return int(app_broker.call("counters", {"op": "bump", "name": name, "by": by}).get("value") or 0)
    except Exception as exc:
        log.error("threads: broker counter bump failed (%s): %s", name, exc)
        return 0


def all_counters() -> Dict[str, Any]:
    conn = _ensure()
    return {
        r["name"]: {"value": r["value"], "updated_at": r["updated_at"]}
        for r in conn.execute("SELECT * FROM counters ORDER BY name")
    }
