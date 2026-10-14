"""App-native task storage — the source of truth for tasks on app-native tenants.

WHY THIS EXISTS (slides 12, 14, 15)
    "One shared task list that you and your team can see, edit, and add to."
    The task-manager SKILL has kept a per-tenant SQLite list for months, but
    the app never read it: the app saw tasks only as feed events, never as
    records, and there was no Tasks surface at all. This store is the app's
    own list, in the app's own shape, following the leads store (W1) discipline
    line for line. The skill keeps writing its legacy list for tenants who are
    not app-native; for tenants who are, it writes HERE through
    _shared/app_tasks.py — one list, two front doors.

THREE DESIGN DECISIONS WORTH THE WORDS

  1. THE PRIMARY KEY IS OURS. The legacy list mints TASK-YYYYMMDD-NNNN by
     COUNTING today's rows, which collides the moment a row is deleted and
     another added. An opaque id that never changes, plus a nullable
     `external_ref` pointing back at wherever a row came from, is what makes
     import idempotent (the leads lesson, verbatim).

  2. ARCHIVE IS A STATUS, NOT A COLUMN. The status vocabulary is closed and
     `archived` is one of its words, so there is exactly one place a task's
     state lives. NEVER HARD-DELETE — archive, same as leads and threads.

  3. "OVERDUE" IS COMPUTED, NEVER STORED, AND COMPUTED IN THE TENANT'S DAY.
     A deadline is a calendar day where the tenant lives. SQLite's date('now')
     is UTC, which is a different day for five hours of every evening in
     Chicago; a stored flag would go stale at midnight. So overdue is derived
     at read time from client.timezone, and the sort that puts overdue work
     first happens in Python for the same reason.

STANDING DESIGN RULES, applied at creation because that is when it is free:
    assignee    — a PERSON KEY, never an address; NULL when unassigned, never
                  guessed (the migration-002 discipline).
    created_by  — a person key, or 'jarvis' when the assistant made it.
    source      — how the task arrived (manual | chat | email | meeting), so
                  PROMPT 4's email path and PROMPT 5's meeting path are values,
                  not migrations.
    stale       — a column with a SETTER but no writer yet: PROMPT 4's sweep
                  owns marking work quiet. Shipping the column now means the
                  digest, the tab and the feed already know what to do with it.

THE STORE EMITS, NOT THE CALL SITE. The API, the chat bridge and the digest
all create and complete tasks; if each announced its own events they would
drift apart within a week. Every lifecycle event and every counter is bumped
in here, once, and a per-caller emit is a per-caller chance to forget.
"""

import os
import re
import secrets
import sqlite3
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from . import config

DB_PATH = Path(
    os.environ.get("JARVIS_TASKS_DB", str(config.HERMES_HOME / "state" / "jarvis_tasks.db"))
).expanduser()

# Closed vocabularies. The CHECK constraints below ARE the enum; these tuples
# exist so the API and the UI can validate and chip without a schema read.
SOURCES = ("manual", "chat", "email", "meeting")
PRIORITIES = ("high", "medium", "low")
STATUSES = ("open", "in_progress", "done", "archived")
ACTIVE = ("open", "in_progress")
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

# Slug -> the word a person reads, for the created headline.
SOURCE_DISPLAY = {"manual": "by hand", "chat": "from chat",
                  "email": "from an email", "meeting": "from a meeting"}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_local = threading.local()
_INIT_LOCK = threading.Lock()
_initialised = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return "tk_" + secrets.token_hex(6)


def _connect() -> sqlite3.Connection:
    """One connection per worker thread — uvicorn runs handlers on a pool."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        _local.conn = conn
    return conn


def init() -> None:
    """Create the schema. Idempotent."""
    global _initialised
    with _INIT_LOCK:
        conn = _connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id      TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                detail       TEXT,

                -- A PERSON KEY. NULL = unassigned, never guessed.
                assignee     TEXT,
                -- A person key, or 'jarvis' when the assistant made it.
                created_by   TEXT NOT NULL,

                source       TEXT NOT NULL DEFAULT 'manual'
                             CHECK (source IN ('manual','chat','email','meeting')),
                -- Message / thread / event id the task was derived from. An
                -- internal identifier: it lives here and in `detail`, never
                -- in a headline.
                source_ref   TEXT,

                priority     TEXT NOT NULL DEFAULT 'medium'
                             CHECK (priority IN ('high','medium','low')),
                -- ISO calendar day (YYYY-MM-DD) in the tenant's own zone.
                deadline     TEXT,
                status       TEXT NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open','in_progress','done','archived')),
                -- Set by the staleness sweep (PROMPT 4) through set_stale().
                stale        INTEGER NOT NULL DEFAULT 0,

                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                completed_at TEXT,

                -- Where this row came from, when it came from somewhere. NULL
                -- for app-native tasks. Unique so re-import cannot duplicate.
                external_ref TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_external
                ON tasks(external_ref) WHERE external_ref IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status, priority, deadline);
            CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee, status);
            CREATE INDEX IF NOT EXISTS idx_tasks_created  ON tasks(created_at);

            -- Digest positions, so a reply of "done 9" can be resolved against
            -- the digest that was actually sent. Lives beside the tasks it
            -- points at rather than in the legacy list, whose digest_map is
            -- keyed to the legacy ids.
            CREATE TABLE IF NOT EXISTS digest_map (
                digest_id      TEXT NOT NULL,
                recipient      TEXT,
                sent_at        TEXT,
                rfc_message_id TEXT NOT NULL,
                gmail_id       TEXT,
                position       INTEGER NOT NULL,
                task_id        TEXT,
                PRIMARY KEY (rfc_message_id, position)
            );
            CREATE INDEX IF NOT EXISTS idx_digest_map_digest ON digest_map(digest_id);

            -- A digest reply is acted on once. Gmail history can hand the
            -- poller the same message twice (a restart, a replayed history
            -- id); the second pass must find the claim and stop.
            CREATE TABLE IF NOT EXISTS digest_replies (
                reply_message_id TEXT PRIMARY KEY,
                claimed_at       TEXT NOT NULL
            );
            """
        )
        # FORWARD MIGRATIONS GO HERE, guarded on the live column set — the
        # same shape threads.py uses. There are none yet; the hook exists so
        # PROMPT 4/5 can add a column with three lines instead of a script.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
        _ = cols  # nothing to add yet
        conn.commit()
        _initialised = True


def _ensure() -> sqlite3.Connection:
    if not _initialised:
        init()
    return _connect()


class TaskError(Exception):
    pass


class TaskNotFound(TaskError):
    pass


# ── tenant facts, read lazily so the module imports with stdlib only ─────────
#
# The task-manager SKILL imports this module from the gateway's venv through
# _shared/app_tasks.py. Anything that touches _shared at import time would
# make that import order-sensitive, so identity and config are reached only
# from inside the functions that need them, and every one of them degrades.

def _shared():
    p = str(config.SHARED)
    if p not in sys.path:
        sys.path.insert(0, p)


def _tzinfo():
    """The tenant's zone, or UTC when config cannot say. Never raises."""
    try:
        _shared()
        import tenant_config as tc
        from zoneinfo import ZoneInfo
        name = tc.timezone_name()
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def tz_name() -> str:
    try:
        _shared()
        import tenant_config as tc
        return tc.timezone_name()
    except Exception:
        return "UTC"


def _today() -> date:
    return datetime.now(_tzinfo()).date()


def _display_name(person_key: Optional[str]) -> str:
    """THE STORE HOLDS KEYS; THE SCREEN SHOWS NAMES. Falls back to the key
    rather than to blank — an unresolvable person should still be visible as
    SOMETHING (leads_source._display_name, same reasoning)."""
    if not person_key:
        return ""
    if person_key == "jarvis":
        return "Jarvis"
    try:
        _shared()
        import identity
        return identity.display_name_for(person_key) or person_key
    except Exception:
        return person_key


def _person_ok(person_key: Optional[str]) -> bool:
    """Is this a key in the tenant's roster? Unknown registry -> refuse: an
    assignee the registry cannot name is a task nobody will ever see."""
    if person_key is None:
        return True
    try:
        _shared()
        import identity
        return person_key in identity.roster()
    except Exception:
        return False


# ── counters and feed, both in the store, both never raising ─────────────────

def _bump(name: str, by: int = 1) -> None:
    """Per-tenant usage counter. Lives in the tenant's own threads.db, which is
    what makes it per-tenant: HERMES_HOME decides which file that is."""
    try:
        from . import threads
        threads.bump_counter(name, by)
    except Exception:
        pass


def _emit(headline: str, detail: str, kind: str, task_id: str,
          assignee: Optional[str], owner_person: Optional[str]) -> None:
    """Humanised feed event. Never raises — the feed must not fail a write.

    The attachment carries `assignee` AND `owner_person` because W3 scopes the
    feed by who an event belongs to (app/feed.py:owner_keys reads both). A task
    with no assignee belongs to whoever created it; a task Jarvis created for
    nobody has no owner, and THAT EVENT IS OWNER/ADMIN-ONLY BY DESIGN — the
    feed's default is deny, and "we could not establish whose this is" is not
    the same as "everyone's".
    """
    try:
        _shared()
        import notify
        rec: Dict[str, Any] = {"type": "task", "task_id": task_id}
        if assignee:
            rec["assignee"] = assignee
        if owner_person and owner_person != "jarvis":
            rec["owner_person"] = owner_person
        notify.notify("tasks", headline, headline=headline, detail=detail,
                      kind=kind, skill="tasks", subject=None, attachments=[rec])
    except Exception:
        pass


# ── row shaping ───────────────────────────────────────────────────────────────

def _due_label(deadline: Optional[str], status: str, today: date) -> str:
    if not deadline or status not in ACTIVE:
        return ""
    try:
        d = date.fromisoformat(deadline)
    except ValueError:
        return ""
    delta = (d - today).days
    if delta < 0:
        n = -delta
        return f"Overdue by {n} day{'s' if n != 1 else ''}"
    if delta == 0:
        return "Due today"
    if delta == 1:
        return "Due tomorrow"
    if delta <= 14:
        return f"Due in {delta} days"
    return "Due " + d.strftime("%b %-d")


def _with_flags(d: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    today = today or _today()
    d["stale"] = bool(d.get("stale"))
    status = d.get("status") or "open"
    deadline = d.get("deadline")
    overdue = False
    if deadline and status in ACTIVE:
        try:
            overdue = date.fromisoformat(deadline) < today
        except ValueError:
            overdue = False
    d["is_overdue"] = overdue
    d["due_label"] = _due_label(deadline, status, today)
    try:
        start = datetime.fromisoformat(d["created_at"])
        end = datetime.fromisoformat(d["completed_at"]) if d.get("completed_at") \
            else datetime.now(timezone.utc)
        d["days_open"] = max(0, (end - start).days)
    except Exception:
        d["days_open"] = 0
    d["assignee_name"] = _display_name(d.get("assignee"))
    d["created_by_name"] = _display_name(d.get("created_by"))
    return d


def _row(r: sqlite3.Row, today: Optional[date] = None) -> Dict[str, Any]:
    return _with_flags(dict(r), today)


# ── validation ────────────────────────────────────────────────────────────────

def _check_priority(v: Optional[str]) -> str:
    v = (v or "medium").strip().lower()
    if v not in PRIORITIES:
        raise TaskError("Priority must be high, medium or low.")
    return v


def _check_deadline(v: Optional[str]) -> Optional[str]:
    if v is None or v == "":
        return None
    v = str(v).strip()
    if not _DATE_RE.match(v):
        raise TaskError("A deadline is a calendar day, written YYYY-MM-DD.")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise TaskError("That deadline is not a real calendar day.")
    return v


def _check_assignee(v: Optional[str]) -> Optional[str]:
    if v is None or v == "":
        return None
    v = str(v).strip()
    if not _person_ok(v):
        raise TaskError("No such person on this team.")
    return v


def normalise_title(title: str) -> str:
    """The key a duplicate check is made on: casefold, no punctuation, one
    space. Normalises FORM, never meaning — 'Call the bank' and 'Call the
    bank again' stay different tasks."""
    t = (title or "").casefold()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return " ".join(t.split())


# ── writes ────────────────────────────────────────────────────────────────────

EDITABLE = ("title", "detail", "priority", "deadline")


def create(title: str, created_by: str, **fields: Any) -> Dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise TaskError("A task needs a title.")
    created_by = (created_by or "").strip()
    if not created_by:
        raise TaskError("A task needs someone to have created it.")
    if created_by != "jarvis" and not _person_ok(created_by):
        raise TaskError("created_by must be a person on this team, or 'jarvis'.")

    source = (fields.get("source") or "manual").strip().lower()
    if source not in SOURCES:
        raise TaskError("source must be manual, chat, email or meeting.")
    priority = _check_priority(fields.get("priority"))
    deadline = _check_deadline(fields.get("deadline"))
    assignee = _check_assignee(fields.get("assignee"))
    detail = fields.get("detail")
    detail = (str(detail).strip() or None) if detail is not None else None

    now = _now()
    task_id = _new_id()
    conn = _ensure()
    conn.execute(
        "INSERT INTO tasks (task_id, title, detail, assignee, created_by, source, source_ref, "
        "priority, deadline, status, stale, created_at, updated_at, external_ref) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, ?, ?, ?)",
        (task_id, title, detail, assignee, created_by, source, fields.get("source_ref"),
         priority, deadline, now, now, fields.get("external_ref")),
    )
    conn.commit()
    _bump(f"tasks_created.{source}")

    # "Created a task: Call the title company — for Omar Reyes". The id, the
    # source slug and the priority word are internal: they live in `detail`.
    by_name = _display_name(created_by)
    headline = f"Created a task: {title}"
    if assignee:
        headline += f" — for {_display_name(assignee)}"
    detail_line = (f"{task_id} · {SOURCE_DISPLAY.get(source, source)} · priority {priority}"
                   f" · added by {by_name}")
    if deadline:
        detail_line += f" · due {deadline}"
    owner = assignee or (created_by if created_by != "jarvis" else None)
    _emit(headline, detail_line, "task.created", task_id, assignee, owner)

    # Delegation is its own event: "follow-through" on the feed means seeing
    # that the work moved from one person to another, not just that it exists.
    #
    # ONLY WHEN A PERSON DELEGATED IT. When Jarvis files a task for someone the
    # created headline already says "— for Omar Fennick" and the detail says
    # "added by Jarvis", so a second "Assigned … from Jarvis" line states the
    # same fact twice — two rows in the tenant's feed for one thing that
    # happened. A person handing work to someone else is the case worth its
    # own row.
    if assignee and assignee != created_by and created_by != "jarvis":
        _emit(f"Assigned {title} to {_display_name(assignee)}, from {by_name}",
              f"{task_id} · assignee {assignee} · by {created_by}",
              "task.assigned", task_id, assignee, assignee)
    return get(task_id)


def update(task_id: str, **fields: Any) -> Dict[str, Any]:
    """PARTIAL update over title/detail/priority/deadline. Only keys passed are
    touched — a whole-row write from the UI would silently revert a change the
    chat bridge made between the read and the save."""
    current = get(task_id)
    if current is None:
        raise TaskNotFound(task_id)
    sets: Dict[str, Any] = {}
    for k, v in fields.items():
        if k not in EDITABLE:
            continue
        if k == "title":
            v = (v or "").strip()
            if not v:
                raise TaskError("A task needs a title.")
        elif k == "priority":
            v = _check_priority(v)
        elif k == "deadline":
            v = _check_deadline(v)
        elif k == "detail":
            v = (str(v).strip() or None) if v is not None else None
        sets[k] = v
    if not sets:
        return current
    conn = _ensure()
    conn.execute(
        f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in sets)}, updated_at = ? WHERE task_id = ?",
        [*sets.values(), _now(), task_id],
    )
    conn.commit()
    return get(task_id)


def set_status(task_id: str, status: str, by: str) -> Dict[str, Any]:
    """open | in_progress | done. Archiving is set_archived(); it is a status
    too, but a different kind of decision, so it has its own front door."""
    status = (status or "").strip().lower()
    if status not in ("open", "in_progress", "done"):
        raise TaskError("Status must be open, in_progress or done.")
    current = get(task_id)
    if current is None:
        raise TaskNotFound(task_id)
    now = _now()
    conn = _ensure()
    if status == "done":
        conn.execute("UPDATE tasks SET status = 'done', completed_at = ?, updated_at = ? "
                     "WHERE task_id = ?", (now, now, task_id))
        conn.commit()
        if current["status"] != "done":
            _bump("tasks_completed")
            try:
                opened = datetime.fromisoformat(current["created_at"])
                secs = int((datetime.fromisoformat(now) - opened).total_seconds())
                _bump("tasks_completion_seconds", max(0, secs))
                _bump("tasks_completion_count")
            except Exception:
                pass
            # The "zero missed deadlines" number: done on or before the day it
            # was due (tenant-local date compare; a deadline is a date, not a
            # moment), late, or no deadline to judge by.
            try:
                dl = (current.get("deadline") or "")[:10]
                if not dl:
                    _bump("tasks_completed_no_deadline")
                elif _today().isoformat() <= dl:
                    _bump("tasks_completed_on_time")
                else:
                    _bump("tasks_completed_late")
            except Exception:
                pass
            days = get(task_id)["days_open"]
            _emit(f"Completed: {current['title']}",
                  f"{task_id} · completed by {by} · open {days} day{'s' if days != 1 else ''}",
                  "task.completed", task_id, current.get("assignee"),
                  current.get("assignee") or (current["created_by"]
                                              if current["created_by"] != "jarvis" else None))
    else:
        # Leaving `done` clears completed_at: a reopened task is open again,
        # not a finished one with a stale timestamp. No feed event for a
        # status nudge — it is not something Jarvis did.
        conn.execute("UPDATE tasks SET status = ?, completed_at = NULL, updated_at = ? "
                     "WHERE task_id = ?", (status, now, task_id))
        conn.commit()
    return get(task_id)


def assign(task_id: str, assignee: Optional[str], by: str) -> Dict[str, Any]:
    current = get(task_id)
    if current is None:
        raise TaskNotFound(task_id)
    assignee = _check_assignee(assignee)
    conn = _ensure()
    conn.execute("UPDATE tasks SET assignee = ?, updated_at = ? WHERE task_id = ?",
                 (assignee, _now(), task_id))
    conn.commit()
    if assignee and assignee != current.get("assignee"):
        by_name = _display_name(by)
        headline = f"Assigned {current['title']} to {_display_name(assignee)}"
        if by != assignee:
            headline += f", from {by_name}"
        _emit(headline, f"{task_id} · assignee {assignee} · by {by}",
              "task.assigned", task_id, assignee, assignee)
    return get(task_id)


def set_archived(task_id: str, archived: bool = True, by: Optional[str] = None) -> Dict[str, Any]:
    current = get(task_id)
    if current is None:
        raise TaskNotFound(task_id)
    conn = _ensure()
    if archived:
        conn.execute("UPDATE tasks SET status = 'archived', updated_at = ? WHERE task_id = ?",
                     (_now(), task_id))
    else:
        conn.execute("UPDATE tasks SET status = 'open', updated_at = ? WHERE task_id = ?",
                     (_now(), task_id))
    conn.commit()
    return get(task_id)


def set_stale(task_id: str, stale: bool = True, by: str = "jarvis") -> Dict[str, Any]:
    """The staleness sweep's entry point (PROMPT 4). Emits only on the flip to
    quiet — a sweep that re-marks the same task every night must not re-post."""
    current = get(task_id)
    if current is None:
        raise TaskNotFound(task_id)
    conn = _ensure()
    conn.execute("UPDATE tasks SET stale = ? WHERE task_id = ?", (1 if stale else 0, task_id))
    conn.commit()
    if stale and not current["stale"]:
        _emit(f"{current['title']} has gone quiet",
              f"{task_id} · no activity since {current['updated_at'][:10]} · flagged by {by}",
              "task.stale", task_id, current.get("assignee"),
              current.get("assignee") or (current["created_by"]
                                          if current["created_by"] != "jarvis" else None))
    return get(task_id)


def record_digest(digest_id: str, recipient: str, rfc_message_id: str,
                  gmail_id: Optional[str], positions: Iterable[Tuple[int, str]]) -> None:
    conn = _ensure()
    now = _now()
    for position, task_id in positions:
        conn.execute(
            "INSERT OR REPLACE INTO digest_map "
            "(digest_id, recipient, sent_at, rfc_message_id, gmail_id, position, task_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (digest_id, recipient, now, rfc_message_id, gmail_id, position, task_id))
    conn.commit()


def latest_digest_rfc_message_id() -> Optional[str]:
    """The Message-ID of the most recently sent digest, or None if none has
    been sent. What a test reply threads onto when it says "the latest one"."""
    row = _ensure().execute(
        "SELECT rfc_message_id FROM digest_map ORDER BY sent_at DESC LIMIT 1").fetchone()
    return row["rfc_message_id"] if row else None


def digest_positions(rfc_message_id: str) -> List[Tuple[int, str, str]]:
    """(position, task_id, digest_id) for the digest sent under this RFC
    Message-ID — what a "done 3" reply resolves against for a store tenant."""
    rows = _ensure().execute(
        "SELECT position, task_id, digest_id FROM digest_map WHERE rfc_message_id = ? "
        "ORDER BY position", (rfc_message_id,)).fetchall()
    return [(int(r["position"]), r["task_id"], r["digest_id"]) for r in rows]


def claim_digest_reply(reply_message_id: str) -> bool:
    """True the first time this reply is seen; False ever after."""
    if not reply_message_id:
        return True
    conn = _ensure()
    cur = conn.execute("INSERT OR IGNORE INTO digest_replies (reply_message_id, claimed_at) "
                       "VALUES (?, ?)", (reply_message_id, _now()))
    conn.commit()
    return cur.rowcount == 1


# ── reads ─────────────────────────────────────────────────────────────────────

def get(task_id: str) -> Optional[Dict[str, Any]]:
    r = _ensure().execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return _row(r) if r else None


def find_by_source_ref(source: str, source_ref: str) -> List[Dict[str, Any]]:
    """Every task derived from this source record, ANY status.

    The sweep's "already converted" check. Any status on purpose: a thread
    whose task was done or archived has still been converted, and converting
    it again would hand the team the same work twice.
    """
    rows = _ensure().execute(
        "SELECT * FROM tasks WHERE source = ? AND source_ref = ? ORDER BY created_at ASC",
        (source, source_ref)).fetchall()
    return [_row(r) for r in rows]


def find_open_by_title(title: str) -> List[Dict[str, Any]]:
    """Active tasks whose title normalises to the same key — the chat bridge's
    duplicate check. A LIST: the caller decides what a match means."""
    key = normalise_title(title)
    if not key:
        return []
    out = []
    for r in _ensure().execute(
        "SELECT * FROM tasks WHERE status IN ('open','in_progress') ORDER BY created_at ASC"
    ).fetchall():
        row = _row(r)
        if normalise_title(row["title"]) == key:
            out.append(row)
    return out


def created_since(ts: str, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """Tasks written since `ts` (an ISO UTC timestamp), oldest first.

    Deliberately NOT built on query(): this has to see a task that was created
    AND archived inside the same window, because the question it answers is
    "did anything get written just now", not "what is on the list".
    """
    sql = "SELECT * FROM tasks WHERE created_at >= ?"
    args: List[Any] = [ts]
    if source:
        sql += " AND source = ?"
        args.append(source)
    sql += " ORDER BY created_at ASC"
    return [_row(r) for r in _ensure().execute(sql, args).fetchall()]


def query(status: Union[None, str, Sequence[str]] = None,
          priority: Optional[str] = None, assignee: Optional[str] = None,
          stale: Optional[bool] = None, archived: bool = False) -> List[Dict[str, Any]]:
    """The team list. Filters are SQL; the ORDER is Python, because "overdue"
    is a fact about the tenant's calendar day and SQLite cannot know it."""
    sql = ["SELECT * FROM tasks WHERE status " + ("= 'archived'" if archived else "!= 'archived'")]
    args: List[Any] = []
    if status:
        wanted = [status] if isinstance(status, str) else list(status)
        sql.append(f"AND status IN ({', '.join('?' for _ in wanted)})")
        args.extend(wanted)
    if priority:
        sql.append("AND priority = ?"); args.append(priority)
    if assignee:
        sql.append("AND assignee = ?"); args.append(assignee)
    if stale is not None:
        sql.append("AND stale = ?"); args.append(1 if stale else 0)
    sql.append("ORDER BY created_at ASC")
    today = _today()
    rows = [_row(r, today) for r in _ensure().execute(" ".join(sql), args).fetchall()]
    rows.sort(key=lambda t: (
        t["status"] == "done",                 # finished work sinks
        not t["is_overdue"],                   # overdue first
        PRIORITY_RANK.get(t["priority"], 1),   # then priority
        t.get("deadline") or "9999-12-31",     # then soonest deadline
        t["created_at"],                       # then age
    ))
    return rows


def counts() -> Dict[str, int]:
    conn = _ensure()
    one = lambda q, a=(): conn.execute(q, a).fetchone()[0]  # noqa: E731
    today = _today().isoformat()
    return {
        "open": one("SELECT COUNT(*) FROM tasks WHERE status = 'open'"),
        "in_progress": one("SELECT COUNT(*) FROM tasks WHERE status = 'in_progress'"),
        "done": one("SELECT COUNT(*) FROM tasks WHERE status = 'done'"),
        "stale": one("SELECT COUNT(*) FROM tasks WHERE stale = 1 AND status IN ('open','in_progress')"),
        "overdue": one("SELECT COUNT(*) FROM tasks WHERE deadline < ? AND status IN ('open','in_progress')",
                       (today,)),
        "archived": one("SELECT COUNT(*) FROM tasks WHERE status = 'archived'"),
    }
