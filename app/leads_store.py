"""App-native lead storage — the source of truth for leads.

WHY THIS EXISTS (tenant-two cluster spec, Workstream 1)
    Leads lived in a Google Sheet: per-agent tabs, a human-assigned ID scheme,
    trailing spaces in tab titles, a merged banner above the header row. That is
    fine for the tenant who built it and impossible to onboard a new one into.
    Storage moves into the app; Sheets demotes to an optional connector.

    THE OWNER STAYS SHEET-BACKED until he chooses otherwise. This store is for
    fresh tenants and for the importer to be ready when he does.

TWO DESIGN DECISIONS WORTH THE WORDS

  1. THE PRIMARY KEY IS OURS, AND `external_ref` POINTS HOME.
     The sheet's IDs are per-agent prefixed (MRT-/ALX-/BLK-/TGA-/SPN-) and
     assigned by a human process, so they can collide across tenants and be
     re-issued when a lead changes agent. An opaque app id that never changes,
     plus a nullable pointer back to wherever the row came from, is what makes
     import idempotent and an export round-trip possible.

  2. STATUS AND FOLLOW-UP ARE SEPARATE COLUMNS, because the sheet proves they
     are different kinds of thing. `Status Of Lead` is a small closed
     vocabulary; `Follow-up Status` is free text per agent ("Emailed 6/15",
     "Called on 7/9"). Collapsing them would either invent structure the tenant
     never had or throw away the agent's own words. `follow_up` is never chipped
     in the UI for the same reason.

STANDING DESIGN RULES, applied at creation because that is when it is free:
    owner_person — a PERSON KEY, not an address; NULL when the registry cannot
                   answer, never guessed (the migration-002 discipline).
    thread_id    — the deal channel, nullable until one exists.
    Both exist so Workstream 3's scoping is a WHERE clause, not a migration.

NEVER HARD-DELETE: archive instead, same as threads.
"""

import os
import secrets
import sys
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

DB_PATH = Path(
    os.environ.get("JARVIS_LEADS_DB", str(config.HERMES_HOME / "state" / "jarvis_leads.db"))
).expanduser()

# The sheet's vocabularies are already the product's — they are what the people
# using this actually say. Kept closed so the UI can chip them safely.
STATUSES = ("New", "No Response", "Responded", "Not Interested")
TEMPERATURES = ("Hot", "Warm", "Cold", "")

_local = threading.local()
_INIT_LOCK = threading.Lock()
_initialised = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return "ld_" + secrets.token_hex(6)


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
            CREATE TABLE IF NOT EXISTS leads (
                lead_id        TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                email          TEXT,
                phone          TEXT,
                property       TEXT,
                price_point    TEXT,
                source         TEXT,
                notes          TEXT,

                status         TEXT NOT NULL DEFAULT 'New',
                temperature    TEXT NOT NULL DEFAULT '',
                -- FREE TEXT, per agent. Never chipped in the UI.
                follow_up      TEXT,

                -- Standing design rules: identity and container, written now so
                -- Workstream 3 is a WHERE clause rather than a migration.
                owner_person   TEXT,
                thread_id      TEXT,

                last_activity_at TEXT,
                next_touch_at    TEXT,

                created_by     TEXT NOT NULL DEFAULT 'user'
                               CHECK (created_by IN ('user','jarvis')),
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                archived       INTEGER NOT NULL DEFAULT 0,

                -- Where this row came from, when it came from somewhere. NULL
                -- for app-native leads. Unique so re-import cannot duplicate.
                external_ref   TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_external
                ON leads(external_ref) WHERE external_ref IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_leads_active
                ON leads(archived, next_touch_at);
            CREATE INDEX IF NOT EXISTS idx_leads_owner  ON leads(owner_person);
            CREATE INDEX IF NOT EXISTS idx_leads_thread ON leads(thread_id);
            """
        )
        conn.commit()
        _initialised = True


def _ensure() -> sqlite3.Connection:
    if not _initialised:
        init()
    return _connect()


class LeadError(Exception):
    pass


class LeadNotFound(LeadError):
    pass


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["archived"] = bool(d["archived"])
    return d


# ── writes ────────────────────────────────────────────────────────────────────

FIELDS = ("name", "email", "phone", "property", "price_point", "source", "notes",
          "status", "temperature", "follow_up", "owner_person", "thread_id",
          "last_activity_at", "next_touch_at", "external_ref")


def _emit(headline: str, detail: str, kind: str,
          owner_person: Optional[str] = None, lead_id: Optional[str] = None) -> None:
    """Humanised feed event. Never raises — the feed must not fail a write.

    Lives in the store rather than at each call site so the API, the importer
    and any skill all emit the same way; a per-caller emit is a per-caller
    chance to forget.

    The attachment carries `owner_person` because W3 scopes the feed by who an
    event belongs to (app/feed.py:owner_keys). An event with no owner is
    invisible to members by default, so omitting this would silently hide a
    member's own leads from them.
    """
    try:
        sys.path.insert(0, str(config.SHARED))
        import notify
        att = None
        if owner_person or lead_id:
            rec = {"type": "lead"}
            if owner_person:
                rec["owner_person"] = owner_person
            if lead_id:
                rec["lead_id"] = lead_id
            att = [rec]
        notify.notify("leads", headline, headline=headline, detail=detail,
                      kind=kind, skill="leads", attachments=att)
    except Exception:
        pass


def create(name: str, created_by: str = "user", **fields: Any) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise LeadError("A lead needs a name.")
    if created_by not in ("user", "jarvis"):
        raise LeadError("created_by must be 'user' or 'jarvis'.")

    data = {k: fields.get(k) for k in FIELDS}
    data["name"] = name
    data.setdefault("status", "New")
    if not data.get("status"):
        data["status"] = "New"
    if data.get("temperature") is None:
        data["temperature"] = ""

    now = _now()
    lead_id = _new_id()
    conn = _ensure()
    cols = ", ".join(["lead_id", "created_by", "created_at", "updated_at", *FIELDS])
    marks = ", ".join(["?"] * (4 + len(FIELDS)))
    conn.execute(
        f"INSERT INTO leads ({cols}) VALUES ({marks})",
        [lead_id, created_by, now, now, *[data[k] for k in FIELDS]],
    )
    conn.commit()

    # "New lead: Marcus Webb from Zillow" — the spec's example shape. Source is
    # only mentioned when there is one; "from None" is not a sentence.
    src = (data.get("source") or "").strip()
    who = "Jarvis" if created_by == "jarvis" else "You"
    headline = f"New lead: {name}" + (f" from {src}" if src else "")
    _emit(headline, f"{lead_id} · added by {who}", "lead.created",
          owner_person=data.get("owner_person"), lead_id=lead_id)
    return get(lead_id)


def update(lead_id: str, **fields: Any) -> Dict[str, Any]:
    """PARTIAL update. Only the keys passed are touched.

    Field-level on purpose: a whole-row write from the UI would silently revert
    a change a skill made between the read and the save.
    """
    current = get(lead_id)
    if current is None:
        raise LeadNotFound(lead_id)
    sets = {k: v for k, v in fields.items() if k in FIELDS}
    if not sets:
        return current
    conn = _ensure()
    conn.execute(
        f"UPDATE leads SET {', '.join(f'{k} = ?' for k in sets)}, updated_at = ? "
        f"WHERE lead_id = ?",
        [*sets.values(), _now(), lead_id],
    )
    conn.commit()
    return get(lead_id)


def set_archived(lead_id: str, archived: bool = True) -> Dict[str, Any]:
    if get(lead_id) is None:
        raise LeadNotFound(lead_id)
    conn = _ensure()
    conn.execute("UPDATE leads SET archived = ?, updated_at = ? WHERE lead_id = ?",
                 (1 if archived else 0, _now(), lead_id))
    conn.commit()
    return get(lead_id)


def upsert_external(external_ref: str, **fields: Any) -> Dict[str, Any]:
    """Import path: create by external_ref, or refresh the existing row.

    This is what makes re-running the importer safe — the second run updates
    what it already brought in instead of duplicating every lead.
    """
    conn = _ensure()
    row = conn.execute("SELECT lead_id FROM leads WHERE external_ref = ?",
                       (external_ref,)).fetchone()
    if row:
        return update(row["lead_id"], **fields)
    return create(created_by=fields.pop("created_by", "user"),
                  external_ref=external_ref, **fields)


# ── reads ─────────────────────────────────────────────────────────────────────

def get(lead_id: str) -> Optional[Dict[str, Any]]:
    r = _ensure().execute("SELECT * FROM leads WHERE lead_id = ?", (lead_id,)).fetchone()
    return _row(r) if r else None


def get_by_external(external_ref: str) -> Optional[Dict[str, Any]]:
    r = _ensure().execute("SELECT * FROM leads WHERE external_ref = ?",
                          (external_ref,)).fetchone()
    return _row(r) if r else None


def query(status: Optional[str] = None, temperature: Optional[str] = None,
          owner_person: Optional[str] = None, archived: bool = False,
          needs_touch: Optional[bool] = None) -> List[Dict[str, Any]]:
    sql = ["SELECT * FROM leads WHERE archived = ?"]
    args: List[Any] = [1 if archived else 0]
    if status:
        sql.append("AND status = ?"); args.append(status)
    if temperature:
        sql.append("AND temperature = ?"); args.append(temperature)
    if owner_person:
        sql.append("AND owner_person = ?"); args.append(owner_person)
    if needs_touch:
        # Same definition the sheet-backed screen used: waiting on the other
        # party and not written off.
        sql.append("AND status = 'No Response'")
    sql.append("ORDER BY COALESCE(next_touch_at, last_activity_at, created_at) DESC")
    rows = _ensure().execute(" ".join(sql), args).fetchall()
    return [_row(r) for r in rows]


def counts() -> Dict[str, int]:
    conn = _ensure()
    one = lambda q, a=(): conn.execute(q, a).fetchone()[0]  # noqa: E731
    return {
        "all": one("SELECT COUNT(*) FROM leads WHERE archived = 0"),
        "needs_touch": one("SELECT COUNT(*) FROM leads WHERE archived = 0 AND status = 'No Response'"),
        "hot": one("SELECT COUNT(*) FROM leads WHERE archived = 0 AND temperature = 'Hot'"),
        "new": one("SELECT COUNT(*) FROM leads WHERE archived = 0 AND status = 'New'"),
        "archived": one("SELECT COUNT(*) FROM leads WHERE archived = 1"),
    }
