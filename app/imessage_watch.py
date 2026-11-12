"""Turn Spencer's new texts into tasks, the way new email does.

Only a text he sends, in which he commits to do something, becomes a task on
his list. A text that names a meeting and a clock time is filed on his
calendar and waits in Approvals. Nothing is sent back into the thread.

The first run records whatever is already synced and leaves it alone.
"""

import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from . import broker, config, tasks_store
from .inbox_watch import _when, parse_plan

log = logging.getLogger("app.imessage_watch")

TICK_SECONDS = 45
FIRST_TICK_SECONDS = 25
PERSON = "owner"
_TAPBACK = re.compile(r"^(Liked|Loved|Disliked|Laughed at|Emphasized|Questioned) ", re.I)
_TIME_HINT = re.compile(r"\b(\d{1,2}(:\d{2})?\s*(am|pm)?|noon|midnight|meet|meeting|call|o'clock)\b", re.I)
_PROMPT = (
    "You read one new text message from Spencer's phone. Spencer is the person marked me. "
    "Reply with JSON only, no markdown. "
    'Shape: {"tasks":[{"title":"","deadline":""}],"calendar":null}. '
    "tasks are things Spencer just said he will do. "
    "A short agreement such as sounds good, ok, thank you, or just sent counts only when the "
    "recent messages asked for something specific, and then the task is that thing. "
    "If he did not commit to anything, tasks is empty. Do not turn other people's requests "
    "into tasks unless his new message agrees to do them. deadline is YYYY-MM-DD or empty. "
    "calendar is an object with title, start, end, and attendees only when a meeting is "
    "confirmed or asked for AND a clock time is given. start and end are ISO datetimes. "
    "attendees are email addresses actually written in the messages. "
    "If the time is missing, calendar is null. Do not invent a time or a date."
)


def _ledger() -> sqlite3.Connection:
    path = config.HERMES_HOME / "state" / "jarvis_imessage_log.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS seen (
            guid TEXT PRIMARY KEY, status TEXT NOT NULL, task_ids TEXT, note TEXT, created_at TEXT NOT NULL)"""
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    return conn


def _seen(guid: str) -> bool:
    return _ledger().execute("SELECT 1 FROM seen WHERE guid=?", (guid,)).fetchone() is not None


def _mark(guid: str, status: str, task_ids: str = "", note: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = _ledger()
    conn.execute(
        """INSERT INTO seen (guid, status, task_ids, note, created_at) VALUES (?,?,?,?,?)
           ON CONFLICT(guid) DO UPDATE SET status=excluded.status, task_ids=excluded.task_ids, note=excluded.note""",
        (guid, status, task_ids, note[:300], now),
    )
    conn.commit()


def _meta(key: str) -> str:
    row = _ledger().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row and row["value"] else ""


def _set_meta(key: str, value: str) -> None:
    conn = _ledger()
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def _guids() -> List[str]:
    from . import imessage
    return [str(r["guid"]) for r in imessage._db().execute(
        "SELECT guid FROM imessages WHERE person_key=?", (PERSON,))]


def _rows(since: str = "") -> List[sqlite3.Row]:
    from . import imessage
    sql = "SELECT guid, chat, sender, is_from_me, sent_at, text_cipher FROM imessages WHERE person_key=?"
    args: List[Any] = [PERSON]
    if since:
        sql += " AND sent_at>=?"
        args.append(since)
    sql += " ORDER BY sent_at"
    return list(imessage._db().execute(sql, args))


def _text(cipher: bytes) -> str:
    from . import imessage
    return imessage._fernet().decrypt(cipher).decode()


def _context(chat: str, sent_at: str) -> str:
    from . import imessage
    f = imessage._fernet()
    rows = imessage._db().execute(
        """SELECT sender, is_from_me, text_cipher FROM imessages
           WHERE person_key=? AND chat=? AND sent_at<? ORDER BY sent_at DESC LIMIT 6""",
        (PERSON, chat, sent_at),
    ).fetchall()
    lines = []
    for row in reversed(rows):
        who = "me" if row["is_from_me"] else (row["sender"] or "them")
        try:
            body = f.decrypt(row["text_cipher"]).decode()
        except Exception:
            continue
        lines.append(f"{who}: {body[:500]}")
    return "\n".join(lines)


def _extract(chat: str, new_text: str, prior: str, from_me: bool) -> Dict[str, Any]:
    key = os.environ.get("API_SERVER_KEY", "")
    if not key:
        raise RuntimeError("no model key")
    who = "me" if from_me else "someone else"
    payload = {
        "model": "claude-sonnet-5-5",
        "max_tokens": 700,
        "system": _PROMPT,
        "messages": [{"role": "user", "content":
                      f"Chat: {chat}\n\nRecent messages:\n{prior[:6000]}\n\nNew message from {who}:\n{new_text[:2000]}"}],
    }
    resp = httpx.post(
        "http://127.0.0.1:8810/api/model/anthropic/v1/messages",
        json=payload,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = [b.get("text") or "" for b in data.get("content") or [] if isinstance(b, dict)]
    plan = parse_plan("".join(parts))
    if not from_me:
        plan["tasks"] = []
    return plan


def _add_tasks(guid: str, chat: str, tasks: List[Dict[str, str]]) -> List[str]:
    ids = []
    for i, task in enumerate(tasks):
        deadline = task["deadline"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", task["deadline"]) else None
        try:
            row = tasks_store.create(
                task["title"], "jarvis", source="chat", assignee="owner",
                detail=f"From Spencer's texts, chat: {chat}"[:2000],
                deadline=deadline, source_ref=guid, external_ref=f"imessage:{guid}:{i}",
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                continue
            log.error("imessage_watch: task was not filed (%s)", type(exc).__name__)
            continue
        ids.append(row["task_id"])
    return ids


def _calendar(guid: str, chat: str, cal: Optional[Dict[str, Any]]) -> str:
    if not cal:
        return ""
    start = _when(str(cal.get("start") or ""))
    if not start:
        return ""
    end = _when(str(cal.get("end") or ""))
    if not end:
        end = (datetime.fromisoformat(start) + timedelta(minutes=30)).isoformat()
    title = str(cal.get("title") or "Meeting")[:200]
    body = {
        "summary": title,
        "description": f"From Spencer's texts, chat: {chat}"[:2000],
        "start": {"dateTime": start, "timeZone": "America/Chicago"},
        "end": {"dateTime": end, "timeZone": "America/Chicago"},
    }
    params: Dict[str, Any] = {"calendarId": "primary", "body": body}
    status, content = broker.gate_google(
        "calendar", "v3", "events.insert", params,
        {"run": "", "session": "", "task": "", "skill": "imessage", "source": f"imessage:{guid}"},
        person="owner",
    )
    if status == 200 or content.get("error") == "approval_required":
        log.info("imessage_watch: calendar invite for a text is %s", "on the calendar" if status == 200 else "waiting in Approvals")
        return "calendar"
    log.error("imessage_watch: calendar was not filed (%s)", content.get("error") or status)
    return ""


def _handle(row: sqlite3.Row) -> None:
    guid = str(row["guid"])
    try:
        text = _text(row["text_cipher"]).strip()
    except Exception as exc:
        log.error("imessage_watch: could not read a text (%s)", type(exc).__name__)
        _mark(guid, "failed", note="could not read")
        return
    if not text or _TAPBACK.match(text):
        _mark(guid, "skipped", note="not a commitment")
        return
    from_me = bool(row["is_from_me"])
    if not from_me:
        from . import text_followup
        if text_followup.is_automated(str(row["sender"] or ""), text, str(row["chat"] or "")):
            _mark(guid, "skipped", note="automated text")
            return
    if not from_me and not _TIME_HINT.search(text):
        _mark(guid, "skipped", note="not from Spencer")
        return
    chat = str(row["chat"] or "")
    try:
        plan = _extract(chat, text, _context(chat, str(row["sent_at"])), from_me)
    except Exception as exc:
        log.error("imessage_watch: could not read what the text committed to (%s)", type(exc).__name__)
        _mark(guid, "failed", note="could not read the commitment")
        return
    task_ids = _add_tasks(guid, chat, plan["tasks"]) if from_me else []
    cal = _calendar(guid, chat, plan["calendar"])
    note = []
    if task_ids:
        note.append(f"{len(task_ids)} task(s)")
    if cal:
        note.append("calendar")
    _mark(guid, "handled", task_ids=",".join(task_ids), note=", ".join(note) or "nothing to file")
    if task_ids or cal:
        log.info("imessage_watch: filed %s from a text in %s", ", ".join(note), chat[:80] or "a chat")


def tick() -> None:
    cutoff = _meta("watermark")
    if not cutoff:
        present = _guids()
        now = datetime.now(timezone.utc).isoformat()
        conn = _ledger()
        conn.executemany(
            "INSERT INTO seen (guid, status, task_ids, note, created_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(guid) DO NOTHING",
            [(guid, "seeded", "", "", now) for guid in present],
        )
        conn.commit()
        _set_meta("watermark", now)
        log.info("imessage_watch: left %d texts already on the Mac alone; only new ones are read", len(present))
        return
    fresh = [row for row in _rows(cutoff) if not _seen(str(row["guid"]))]
    for row in fresh[:5]:
        arrived = str(row["sent_at"] or "")
        if arrived < cutoff:
            _mark(str(row["guid"]), "seeded")
            continue
        try:
            _handle(row)
        except Exception as exc:
            log.error("imessage_watch: a text was not handled (%s)", type(exc).__name__)
            _mark(str(row["guid"]), "failed", note="handling failed")


def _loop() -> None:
    time.sleep(FIRST_TICK_SECONDS)
    while True:
        try:
            tick()
        except Exception as exc:
            log.exception("imessage_watch: tick failed (%s)", type(exc).__name__)
        time.sleep(TICK_SECONDS)


def start() -> None:
    threading.Thread(target=_loop, name="imessage-watch", daemon=True).start()
    log.info("imessage_watch: watching Spencer's texts for commitments")
