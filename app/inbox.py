"""Jarvis's own inbox, as a log the owner can see.

Reads the assistant mailbox (the Google account connected for Jarvis), never a
person's private mailbox. Listing does not mark anything read and does not
send. A mailbox that is not connected returns an empty log and says so.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from . import google_connection as google_conn

log = logging.getLogger("app.inbox")

LIMIT = 40
_HEADERS = ("From", "Subject", "Date")


def _ledger_path() -> Path:
    from . import config
    return Path(config.HERMES_HOME) / "state" / "jarvis_inbox_log.db"


def _ledger() -> sqlite3.Connection:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS inbox_mail (
            gmail_id TEXT PRIMARY KEY,
            thread_id TEXT,
            from_addr TEXT,
            subject TEXT,
            status TEXT NOT NULL,
            receipt TEXT,
            task_ids TEXT,
            note TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


def seen(gmail_id: str) -> bool:
    row = _ledger().execute("SELECT 1 FROM inbox_mail WHERE gmail_id=?", (gmail_id,)).fetchone()
    return row is not None


def remember(gmail_id: str, *, status: str, thread_id: str = "", from_addr: str = "",
             subject: str = "", receipt: str = "", task_ids: str = "", note: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = _ledger()
    conn.execute(
        """INSERT INTO inbox_mail (gmail_id, thread_id, from_addr, subject, status, receipt, task_ids, note, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(gmail_id) DO UPDATE SET
             status=excluded.status, receipt=excluded.receipt, task_ids=excluded.task_ids, note=excluded.note,
             from_addr=CASE WHEN excluded.from_addr != '' THEN excluded.from_addr ELSE inbox_mail.from_addr END,
             subject=CASE WHEN excluded.subject != '' THEN excluded.subject ELSE inbox_mail.subject END""",
        (gmail_id, thread_id, from_addr[:200], subject[:300], status, receipt, task_ids, note[:500], now),
    )
    conn.commit()


def meta_get(key: str) -> str:
    conn = _ledger()
    conn.execute("CREATE TABLE IF NOT EXISTS inbox_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    row = conn.execute("SELECT value FROM inbox_meta WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row and row["value"] else ""


def meta_set(key: str, value: str) -> None:
    conn = _ledger()
    conn.execute("CREATE TABLE IF NOT EXISTS inbox_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO inbox_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def handling_for(ids) -> Dict[str, str]:
    """Short line for the inbox tab, keyed by gmail id. Seeded mail has none."""
    want = [i for i in ids if i]
    if not want:
        return {}
    q = ",".join("?" * len(want))
    out = {}
    for row in _ledger().execute(
        f"SELECT gmail_id, status, note FROM inbox_mail WHERE gmail_id IN ({q})", want
    ):
        if row["status"] == "seeded":
            continue
        out[row["gmail_id"]] = row["note"] or row["status"]
    return out


def _assistant_address() -> str:
    try:
        from . import config
        import sys

        shared = str(config.SHARED)
        if shared not in sys.path:
            sys.path.insert(0, shared)
        import identity

        return (identity.load().get("assistant_address") or "").strip()
    except Exception:
        log.warning("inbox: could not read the assistant address", exc_info=True)
        return ""


def _header(msg: Dict[str, Any], name: str) -> str:
    want = name.lower()
    for header in ((msg.get("payload") or {}).get("headers") or []):
        if str(header.get("name") or "").lower() == want:
            return str(header.get("value") or "").strip()
    return ""


def _when(msg: Dict[str, Any]) -> str:
    raw = str(msg.get("internalDate") or "").strip()
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).isoformat()
    return ""


def shape_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    """One Gmail metadata resource, reduced to the log line."""
    mid = str(msg.get("id") or "")
    labels = msg.get("labelIds") or []
    return {
        "id": mid,
        "from": _header(msg, "From")[:200],
        "subject": _header(msg, "Subject")[:300] or "(no subject)",
        "date": _when(msg),
        "unread": "UNREAD" in labels,
        "snippet": str(msg.get("snippet") or "").strip()[:300],
    }


def _empty(status: str, mailbox: str, notice: str) -> Dict[str, Any]:
    return {
        "status": status,
        "mailbox": mailbox or None,
        "unread": 0,
        "messages": [],
        "notice": notice,
    }


def _list_gmail(creds) -> Dict[str, Any]:
    from googleapiclient.discovery import build

    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    listed = gmail.users().messages().list(
        userId="me", q="in:inbox", maxResults=LIMIT,
    ).execute()
    ids = [m["id"] for m in (listed.get("messages") or []) if m.get("id")]
    unread_listed = gmail.users().messages().list(
        userId="me", q="in:inbox is:unread", maxResults=1,
    ).execute()
    unread = int(unread_listed.get("resultSizeEstimate") or 0)

    found: Dict[str, Dict[str, Any]] = {}

    def _take(request_id, response, exception):
        if exception is not None:
            log.warning("inbox: one message could not be read (%s)", type(exception).__name__)
            return
        if isinstance(response, dict):
            found[request_id] = response

    if ids:
        batch = gmail.new_batch_http_request(callback=_take)
        for mid in ids:
            batch.add(
                gmail.users().messages().get(
                    userId="me", id=mid, format="metadata", metadataHeaders=list(_HEADERS),
                ),
                request_id=mid,
            )
        batch.execute()
    messages = [shape_message(found[mid]) for mid in ids if mid in found]
    notes = handling_for([m["id"] for m in messages])
    for m in messages:
        m["handling"] = notes.get(m["id"]) or ""
    page_unread = sum(1 for m in messages if m["unread"])
    return {"unread": max(unread, page_unread), "messages": messages}


def snapshot() -> Dict[str, Any]:
    """The log. Never raises for a missing mailbox or a failed read."""
    mailbox = _assistant_address()
    state = google_conn.status_public()
    status = state.get("status") or google_conn.STATUS_DISCONNECTED
    if state.get("google_email"):
        mailbox = state["google_email"]
    if status != google_conn.STATUS_CONNECTED:
        if status == google_conn.STATUS_NEEDS_REAUTH:
            notice = "Jarvis's mailbox needs to be connected again before this list can fill."
        else:
            notice = "Jarvis's mailbox is not connected, so this list is empty."
        return _empty(status if status in (google_conn.STATUS_DISCONNECTED, google_conn.STATUS_NEEDS_REAUTH)
                      else google_conn.STATUS_DISCONNECTED, mailbox, notice)
    try:
        creds = google_conn.build_credentials()
        listed = _list_gmail(creds)
    except google_conn.OAuthClientMissing:
        log.error("inbox: OAuth client missing; the inbox was not read")
        return _empty("unavailable", mailbox, "Jarvis could not read his inbox just now.")
    except google_conn.ConnectionCipherError:
        log.error("inbox: mailbox token could not be used")
        return _empty("needs_reauth", mailbox, "Jarvis's mailbox needs to be connected again before this list can fill.")
    except Exception as exc:
        name = type(exc).__name__
        if name in ("RefreshError", "HttpError") or getattr(getattr(exc, "resp", None), "status", None) in (401, 403):
            google_conn.mark_needs_reauth("inbox_read_failed")
            log.error("inbox: mailbox refused the read (%s)", name)
            return _empty("needs_reauth", mailbox, "Jarvis's mailbox needs to be connected again before this list can fill.")
        log.error("inbox: read failed (%s)", name)
        return _empty("unavailable", mailbox, "Jarvis could not read his inbox just now.")
    return {
        "status": "connected",
        "mailbox": mailbox or None,
        "unread": listed["unread"],
        "messages": listed["messages"],
        "notice": None,
    }
