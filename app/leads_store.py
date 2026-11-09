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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

DB_PATH = Path(
    os.environ.get("JARVIS_LEADS_DB", str(config.HERMES_HOME / "state" / "jarvis_leads.db"))
).expanduser()

# The sheet's vocabularies are already the product's — they are what the people
# using this actually say. Kept closed so the UI can chip them safely.
STATUSES = ("New", "No Response", "Responded", "Not Interested")

# Slug -> the word a person reads. Auto-capture (_shared/lead_capture.py)
# writes the slug; this is the only place it becomes prose.
SOURCE_DISPLAY = {
    "zillow": "Zillow", "har": "HAR", "realtor": "Realtor.com",
    "redfin": "Redfin", "site": "the website", "email": "email",
}
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
            -- Auto-capture dedupes on address first, then on a normalised name.
            -- Indexed rather than scanned because the connector runs on every
            -- inbound inquiry, and a table scan per email is a cost that only
            -- shows up once a tenant is busy.
            CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
            CREATE INDEX IF NOT EXISTS idx_leads_name  ON leads(name);

            -- The timeline: every call, text, meeting and note, oldest first.
            -- Append-only; the lead row carries the current state, this
            -- carries how it got there.
            CREATE TABLE IF NOT EXISTS lead_touches (
                touch_id       TEXT PRIMARY KEY,
                lead_id        TEXT NOT NULL REFERENCES leads(lead_id),
                at             TEXT NOT NULL,
                by_person      TEXT,
                kind           TEXT NOT NULL,
                note           TEXT,
                reached        INTEGER,
                next_touch_at  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_touches_lead ON lead_touches(lead_id, at);
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
    except Exception as exc:
        try:
            from . import threads as threads_mod  # noqa: PLC0415
            threads_mod.bump_counter("leads_feed_emit_failed")
        except Exception:
            pass
        import logging  # noqa: PLC0415
        logging.getLogger("app.leads_store").warning(
            "leads: feed event %s not posted (%s)", kind, type(exc).__name__)


def create(name: str, created_by: str = "user", emit: bool = True, **fields: Any) -> Dict[str, Any]:
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
    #
    # AND IT IS THE DISPLAY NAME, NOT THE SLUG. Auto-capture stores a lowercase
    # key ("har", "zillow") because that is a stable value to query on; putting
    # it straight into a sentence produced "New lead: Marcus Webb from har",
    # which is exactly the telemetry voice the humanisation pass removed. The
    # store owns this headline, so the store owns the wording. A source it does
    # not recognise is title-cased rather than dropped — an unknown portal
    # should read a little plain, never wrong.
    src = SOURCE_DISPLAY.get((data.get("source") or "").strip().lower(),
                             (data.get("source") or "").strip().title())
    who = "Jarvis" if created_by == "jarvis" else "You"
    headline = f"New lead: {name}" + (f" from {src}" if src else "")
    if emit:
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


def upsert_external(external_ref: str, emit: bool = True, **fields: Any) -> Dict[str, Any]:
    """Import path: create by external_ref, or refresh the existing row.

    This is what makes re-running the importer safe — the second run updates
    what it already brought in instead of duplicating every lead.
    """
    conn = _ensure()
    row = conn.execute("SELECT lead_id FROM leads WHERE external_ref = ?",
                       (external_ref,)).fetchone()
    if row:
        return update(row["lead_id"], **fields)
    return create(created_by=fields.pop("created_by", "user"), emit=emit,
                  external_ref=external_ref, **fields)


# ── reads ─────────────────────────────────────────────────────────────────────

def get(lead_id: str) -> Optional[Dict[str, Any]]:
    r = _ensure().execute("SELECT * FROM leads WHERE lead_id = ?", (lead_id,)).fetchone()
    return _row(r) if r else None


def get_by_external(external_ref: str) -> Optional[Dict[str, Any]]:
    r = _ensure().execute("SELECT * FROM leads WHERE external_ref = ?",
                          (external_ref,)).fetchone()
    return _row(r) if r else None


def normalise_name(name: str) -> str:
    """The key a name match is made on.

    Casefold, drop punctuation, collapse whitespace, and strip the trailing
    noise real inquiry forms produce — "Marcus Webb (Zillow)", "Marcus Webb Jr.",
    "MARCUS WEBB + wife". Deliberately conservative: it normalises FORM, never
    identity. "Marc Webb" and "Marcus Webb" stay different people, because
    guessing they are the same is how you merge two clients into one row.
    """
    import re as _re
    n = (name or "").casefold()
    n = _re.sub(r"\((?:[^)]*)\)", " ", n)          # trailing "(zillow)"
    n = _re.sub(r"\+.*$", " ", n)                   # "+ wife", "+ husband"
    n = _re.sub(r"\b(jr|sr|ii|iii|md|phd)\b\.?", " ", n)
    n = _re.sub(r"[^a-z0-9 ]+", " ", n)
    return " ".join(n.split())


def find_by_email(email: str, archived: bool = False) -> List[Dict[str, Any]]:
    """Every lead carrying this address. A LIST, not one row, on purpose —
    the caller has to decide what two rows with one address mean, and that
    decision is the whole dedupe question."""
    addr = (email or "").strip().lower()
    if not addr:
        return []
    rows = _ensure().execute(
        "SELECT * FROM leads WHERE archived = ? AND LOWER(email) = ? "
        "ORDER BY created_at ASC",
        (1 if archived else 0, addr),
    ).fetchall()
    return [_row(r) for r in rows]


def find_by_name(name: str, archived: bool = False) -> List[Dict[str, Any]]:
    """Leads whose name normalises to the same key. See normalise_name."""
    key = normalise_name(name)
    if not key:
        return []
    out = []
    for r in _ensure().execute(
        "SELECT * FROM leads WHERE archived = ? ORDER BY created_at ASC",
        (1 if archived else 0,),
    ).fetchall():
        row = _row(r)
        if normalise_name(row.get("name") or "") == key:
            out.append(row)
    return out


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


def find_by_phone(phone: str, archived: bool = False) -> List[Dict[str, Any]]:
    """Leads whose phone has the same last ten digits. "(512) 555-0101" and
    "+1 512.555.0101" are one number; formatting is never identity."""
    import re as _re
    key = _re.sub(r"\D", "", phone or "")[-10:]
    if len(key) < 7:
        return []
    return [r for r in query(archived=archived)
            if _re.sub(r"\D", "", r.get("phone") or "")[-10:] == key]


# ── follow-up cadence and the timeline ───────────────────────────────────────

# A contact is something that happened between the agent and the lead; a note
# is the agent writing something down. Only contacts move the cadence.
CONTACT_KINDS = ("call", "text", "email", "met", "showing")
TOUCH_KINDS = CONTACT_KINDS + ("note", "created", "imported")

# Days to the next follow-up. The first one by temperature (a hot lead is
# called tomorrow); after each logged contact, the next step of the ladder.
CADENCE_FIRST = {"hot": 1, "warm": 2}
CADENCE_FIRST_OTHER = 3
CADENCE_LATER = (7, 14, 30)


def cadence_days(temperature: Optional[str], contacts_logged: int) -> int:
    if contacts_logged <= 0:
        return CADENCE_FIRST.get((temperature or "").strip().lower(), CADENCE_FIRST_OTHER)
    return CADENCE_LATER[min(contacts_logged, len(CADENCE_LATER)) - 1]


def today_local() -> date:
    """Today in the tenant's timezone — a follow-up date is a day on the
    agent's calendar, not a UTC instant. Falls back to UTC, counted."""
    from zoneinfo import ZoneInfo  # noqa: PLC0415
    try:
        if str(config.SHARED) not in sys.path:
            sys.path.insert(0, str(config.SHARED))
        import tenant_config as tc  # noqa: PLC0415
        return datetime.now(ZoneInfo(tc.timezone_name())).date()
    except Exception as exc:
        from . import threads as threads_mod  # noqa: PLC0415
        threads_mod.bump_counter("leads_timezone_fallback")
        import logging  # noqa: PLC0415
        logging.getLogger("app.leads_store").warning(
            "leads: no usable tenant timezone (%s); follow-up dates use UTC", type(exc).__name__)
        return datetime.now(timezone.utc).date()


def _date_after(days: int, today: Optional[date] = None) -> str:
    return ((today or today_local()) + timedelta(days=days)).isoformat()


def add_touch(lead_id: str, by: Optional[str], kind: str, note: Optional[str] = None,
              reached: Optional[bool] = None, next_touch_at: Optional[str] = None) -> Dict[str, Any]:
    if kind not in TOUCH_KINDS:
        raise LeadError(f"kind must be one of {', '.join(TOUCH_KINDS)}.")
    if get(lead_id) is None:
        raise LeadNotFound(lead_id)
    touch_id = "lt_" + secrets.token_hex(6)
    conn = _ensure()
    conn.execute(
        "INSERT INTO lead_touches (touch_id, lead_id, at, by_person, kind, note, reached, next_touch_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (touch_id, lead_id, _now(), by, kind, (note or "").strip() or None,
         None if reached is None else (1 if reached else 0), next_touch_at),
    )
    conn.commit()
    r = conn.execute("SELECT * FROM lead_touches WHERE touch_id = ?", (touch_id,)).fetchone()
    return _touch_row(r)


def _touch_row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["reached"] = None if d["reached"] is None else bool(d["reached"])
    return d


def touches(lead_id: str) -> List[Dict[str, Any]]:
    rows = _ensure().execute(
        "SELECT * FROM lead_touches WHERE lead_id = ? ORDER BY at ASC, rowid ASC", (lead_id,)).fetchall()
    return [_touch_row(r) for r in rows]


def contacts_logged(lead_id: str) -> int:
    marks = ",".join("?" * len(CONTACT_KINDS))
    return _ensure().execute(
        f"SELECT COUNT(*) FROM lead_touches WHERE lead_id = ? AND kind IN ({marks})",
        (lead_id, *CONTACT_KINDS)).fetchone()[0]


def create_with_cadence(name: str, by: Optional[str], created_by: str = "user", today=None,
                        **fields: Any) -> Dict[str, Any]:
    """A new lead as an agent adds one: owner, a first follow-up date by
    temperature when none was given, and a 'created' entry on its timeline."""
    if not fields.get("next_touch_at"):
        fields["next_touch_at"] = _date_after(cadence_days(fields.get("temperature"), 0), today)
    lead = create(name, created_by=created_by, **fields)
    add_touch(lead["lead_id"], by, "created", note=fields.get("notes"),
              next_touch_at=lead.get("next_touch_at"))
    return get(lead["lead_id"])


def log_contact(lead_id: str, by: Optional[str], kind: str, note: Optional[str] = None,
                reached: bool = True, next_touch_at: Optional[str] = None,
                today=None) -> Dict[str, Any]:
    """Record a call, text, email or meeting, and move the lead on.

    Reaching them makes a New or No Response lead Responded; trying and not
    reaching a New lead makes it No Response. The next follow-up is the date
    given, or the next step of the cadence; a Not Interested lead gets none
    unless one is given. A note is written down and moves nothing.
    """
    lead = get(lead_id)
    if lead is None:
        raise LeadNotFound(lead_id)
    if kind == "note":
        touch = add_touch(lead_id, by, "note", note=note)
        return {"lead": lead, "touch": touch}
    if kind not in CONTACT_KINDS:
        raise LeadError(f"kind must be one of {', '.join(CONTACT_KINDS + ('note',))}.")
    status = lead.get("status") or "New"
    if reached and status in ("New", "No Response"):
        status = "Responded"
    elif not reached and status == "New":
        status = "No Response"
    if next_touch_at:
        nxt = next_touch_at[:10]
    elif status == "Not Interested":
        nxt = None
    else:
        nxt = _date_after(cadence_days(lead.get("temperature"), contacts_logged(lead_id) + 1), today)
    touch = add_touch(lead_id, by, kind, note=note, reached=reached, next_touch_at=nxt)
    updated = update(lead_id, status=status, next_touch_at=nxt, last_activity_at=_now())
    return {"lead": updated, "touch": touch}


def due(owner_person: Optional[str] = None, today=None) -> List[Dict[str, Any]]:
    """Leads whose follow-up date is today or earlier, oldest date first.
    Not Interested and archived leads are never due."""
    day = (today or today_local()).isoformat()
    sql = ["SELECT * FROM leads WHERE archived = 0 AND status != 'Not Interested' "
           "AND next_touch_at IS NOT NULL AND next_touch_at != '' AND substr(next_touch_at, 1, 10) <= ?"]
    args: List[Any] = [day]
    if owner_person:
        sql.append("AND owner_person = ?"); args.append(owner_person)
    sql.append("ORDER BY substr(next_touch_at, 1, 10) ASC, CASE temperature WHEN 'Hot' THEN 0 "
               "WHEN 'Warm' THEN 1 ELSE 2 END, name ASC")
    return [_row(r) for r in _ensure().execute(" ".join(sql), args).fetchall()]


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
