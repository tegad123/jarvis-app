"""Watch Jarvis's inbox and treat each new email like a message to him.

The first time this runs it records whatever is already in the inbox and does
not answer it. Mail that arrives after that gets a receipt, any concrete tasks
go on Spencer's list (the one the daily digest reads), and a calendar invite
is filed when the email names a time. Nothing is marked handled until the
attempt is recorded, so a crash retries it instead of dropping it.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from email.utils import parseaddr
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx

from . import broker, inbox, tasks_store

log = logging.getLogger("app.inbox_watch")

TICK_SECONDS = 45
FIRST_TICK_SECONDS = 20
CHICAGO = ZoneInfo("America/Chicago")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_AUTO_LOCAL = ("noreply", "no-reply", "mailer-daemon", "notifications", "notification", "donotreply", "do-not-reply")
_PROMPT = (
    "You read one email that was sent to Jarvis, an assistant. "
    "Reply with JSON only, no markdown. "
    'Shape: {"tasks":[{"title":"","deadline":""}],"calendar":null}. '
    "tasks are concrete things the sender asked to be done. deadline is YYYY-MM-DD or empty. "
    "calendar is an object with title, start, end, and attendees only when the email asks for "
    "a calendar invite AND gives a clock time. start and end are ISO datetimes. "
    "attendees are email addresses named in the mail. If the time is missing, calendar is null. "
    "If nothing was asked, tasks is an empty list and calendar is null. Do not invent a time."
)


def _plain(payload: Dict[str, Any]) -> str:
    mime = str(payload.get("mimeType") or "")
    body = payload.get("body") or {}
    data = body.get("data")
    if mime == "text/plain" and data:
        import base64
        try:
            return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
        except Exception:
            return ""
    for part in payload.get("parts") or []:
        found = _plain(part)
        if found:
            return found
    return ""


def skip_reason(sender: str, headers: Dict[str, str], assistant: str) -> str:
    """Why this message gets recorded but not answered. Empty means answer it."""
    addr = parseaddr(sender or "")[1].strip().lower()
    if not addr:
        return "no sender"
    if assistant and addr == assistant.strip().lower():
        return "it is Jarvis's own message"
    local = addr.split("@", 1)[0]
    if local in _AUTO_LOCAL or "noreply" in local or "no-reply" in local or "mailer-daemon" in local:
        return "automated sender"
    auto = (headers.get("auto-submitted") or "").strip().lower()
    if auto and auto != "no":
        return "automatic mail"
    prec = (headers.get("precedence") or "").strip().lower()
    if prec in ("bulk", "list", "junk"):
        return "a mailing list"
    return ""


def _headers(msg: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for header in ((msg.get("payload") or {}).get("headers") or []):
        name = str(header.get("name") or "").lower()
        if name:
            out[name] = str(header.get("value") or "")
    return out


def _when(value: str) -> Optional[str]:
    text = (value or "").strip()
    if not text or "T" not in text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CHICAGO)
    return dt.isoformat()


def parse_plan(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json")
    data = json.loads(text[start:end + 1])
    tasks = []
    for item in data.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if title:
            tasks.append({"title": title[:200], "deadline": str(item.get("deadline") or "").strip()[:10]})
    cal = data.get("calendar") if isinstance(data.get("calendar"), dict) else None
    return {"tasks": tasks[:8], "calendar": cal}


def _extract(sender: str, subject: str, body: str) -> Dict[str, Any]:
    key = os.environ.get("API_SERVER_KEY", "")
    if not key:
        raise RuntimeError("no model key")
    payload = {
        "model": "claude-sonnet-5-5",
        "max_tokens": 700,
        "system": _PROMPT,
        "messages": [{"role": "user", "content": f"From: {sender}\nSubject: {subject}\n\n{body[:8000]}"}],
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
    return parse_plan("".join(parts))


def _add_tasks(gmail_id: str, subject: str, sender: str, tasks: List[Dict[str, str]]) -> List[str]:
    ids = []
    for i, task in enumerate(tasks):
        deadline = task["deadline"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", task["deadline"]) else None
        try:
            row = tasks_store.create(
                task["title"], "jarvis", source="email", assignee="owner",
                detail=f"From {sender}, subject: {subject}"[:2000],
                deadline=deadline, source_ref=gmail_id, external_ref=f"gmail:{gmail_id}:{i}",
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                continue
            log.error("inbox_watch: task was not filed (%s)", type(exc).__name__)
            continue
        ids.append(row["task_id"])
    return ids


def _calendar(gmail_id: str, subject: str, sender: str, cal: Optional[Dict[str, Any]]) -> str:
    if not cal:
        return ""
    start = _when(str(cal.get("start") or ""))
    if not start:
        return "They asked for a calendar invite, but there was no time, so none was made."
    end = _when(str(cal.get("end") or ""))
    if not end:
        end = (datetime.fromisoformat(start) + timedelta(minutes=30)).isoformat()
    attendees = []
    for bit in cal.get("attendees") or []:
        found = _EMAIL.findall(str(bit))
        attendees.extend(a.lower() for a in found)
    attendees = list(dict.fromkeys(attendees))[:20]
    title = str(cal.get("title") or subject or "Invite")[:200]
    body = {
        "summary": title,
        "description": f"Asked for in an email to Jarvis from {sender}.\n{subject}"[:2000],
        "start": {"dateTime": start, "timeZone": "America/Chicago"},
        "end": {"dateTime": end, "timeZone": "America/Chicago"},
    }
    params: Dict[str, Any] = {"calendarId": "primary", "body": body}
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
        params["sendUpdates"] = "all"
    status, content = broker.gate_google(
        "calendar", "v3", "events.insert", params,
        {"run": "", "session": "", "task": "", "skill": "inbox", "source": f"gmail:{gmail_id}"},
        person="owner",
    )
    if status == 200:
        return f"Added to Spencer's calendar: {title}."
    if content.get("error") == "approval_required":
        return f"A calendar invite for {title} is waiting in Approvals."
    log.error("inbox_watch: calendar was not filed (%s)", content.get("error") or status)
    return "The calendar invite could not be filed."


def _receipt(sender: str, subject: str, thread_id: str, message_id: str, gmail_id: str, lines: List[str]) -> str:
    sub = subject.strip() or "(no subject)"
    if not sub.lower().startswith("re:"):
        sub = "Re: " + sub
    text = "Got it. I received your email.\n\n" + "\n".join(lines)
    status, content = broker.send_inbox_receipt(
        sender, sub, text, thread_id=thread_id, in_reply_to=message_id, gmail_id=gmail_id,
    )
    if status == 200:
        return "sent"
    log.error("inbox_watch: receipt not sent (%s)", content.get("error") or status)
    return content.get("error") or "failed"


def _handle(gmail, msg: Dict[str, Any], assistant: str) -> None:
    gid = str(msg.get("id") or "")
    headers = _headers(msg)
    sender = headers.get("from") or ""
    subject = (headers.get("subject") or "").strip()
    thread_id = str(msg.get("threadId") or "")
    reason = skip_reason(sender, headers, assistant)
    if reason:
        inbox.remember(gid, status="skipped", thread_id=thread_id, from_addr=sender,
                       subject=subject, note=f"Seen, not answered: {reason}.")
        log.info("inbox_watch: saw %s and did not answer (%s)", gid, reason)
        return
    body = _plain(msg.get("payload") or {}) or str(msg.get("snippet") or "")
    lines = []
    task_ids: List[str] = []
    try:
        plan = _extract(sender, subject, body)
    except Exception as exc:
        log.error("inbox_watch: could not read what the email asked (%s)", type(exc).__name__)
        plan = {"tasks": [], "calendar": None}
        lines.append("I couldn't pull tasks out of it just now. It's on the Inbox tab.")
    else:
        task_ids = _add_tasks(gid, subject, sender, plan["tasks"])
        if task_ids:
            lines.append(f"I added {len(task_ids)} item(s) to Spencer's list.")
        cal_line = _calendar(gid, subject, sender, plan["calendar"])
        if cal_line:
            lines.append(cal_line)
        if not lines:
            lines.append("Nothing in it needed a task or a calendar invite.")
    receipt = _receipt(sender, subject, thread_id, headers.get("message-id") or "", gid, lines)
    note = " ".join(lines)
    if receipt == "sent":
        note = "Replied. " + note
    else:
        note = f"Reply not sent ({receipt}). " + note
    inbox.remember(gid, status="handled", thread_id=thread_id, from_addr=sender, subject=subject,
                   receipt=receipt, task_ids=",".join(task_ids), note=note)
    try:
        broker.gate_google(
            "gmail", "v1", "users.messages.modify",
            {"userId": "me", "id": gid, "body": {"removeLabelIds": ["UNREAD"]}},
            {"run": "", "session": "", "task": "", "skill": "inbox", "source": f"gmail:{gid}"},
        )
    except Exception as exc:
        log.error("inbox_watch: could not mark %s read (%s)", gid, type(exc).__name__)


def _gmail():
    import sys
    from googleapiclient.discovery import build
    from . import config
    shared = str(config.SHARED)
    if shared not in sys.path:
        sys.path.insert(0, shared)
    import google_creds
    return build("gmail", "v1", credentials=google_creds.credentials(), cache_discovery=False)


def _inbox_ids(gmail, *, pages: int) -> List[str]:
    ids: List[str] = []
    token = None
    for _ in range(pages):
        kwargs = {"userId": "me", "q": "in:inbox", "maxResults": 100}
        if token:
            kwargs["pageToken"] = token
        listed = gmail.users().messages().list(**kwargs).execute()
        ids.extend(m["id"] for m in listed.get("messages") or [] if m.get("id"))
        token = listed.get("nextPageToken")
        if not token:
            break
    return ids


def _new_ids(gmail) -> List[str]:
    return [i for i in _inbox_ids(gmail, pages=1) if not inbox.seen(i)]


def tick() -> None:
    from . import google_connection
    state = google_connection.status_public()
    if state.get("status") != google_connection.STATUS_CONNECTED:
        return
    gmail = _gmail()
    assistant = str(state.get("google_email") or "")
    # Mail already sitting in the inbox is recorded and left alone. Only a
    # message that arrives after the watermark gets a reply.
    cutoff = inbox.meta_get("watermark_ms")
    if not cutoff:
        present = _inbox_ids(gmail, pages=10)
        for gid in present:
            if not inbox.seen(gid):
                inbox.remember(gid, status="seeded", note="")
        cutoff = str(int(time.time() * 1000))
        inbox.meta_set("watermark_ms", cutoff)
        log.info("inbox_watch: watermarked %d messages already in the inbox; only mail that arrives after this is handled", len(present))
        return
    fresh = _new_ids(gmail)
    if not fresh:
        return
    for gid in fresh[:5]:
        try:
            msg = gmail.users().messages().get(userId="me", id=gid, format="full").execute()
            arrived = int(msg.get("internalDate") or 0)
            if arrived and arrived < int(cutoff):
                headers = _headers(msg)
                inbox.remember(gid, status="seeded", thread_id=str(msg.get("threadId") or ""),
                               from_addr=headers.get("from") or "", subject=headers.get("subject") or "", note="")
                log.info("inbox_watch: left a message alone; it was already in the inbox")
                continue
            _handle(gmail, msg, assistant)
        except Exception as exc:
            log.error("inbox_watch: %s was not handled (%s)", gid, type(exc).__name__)
            inbox.remember(gid, status="failed", note="Seen, but handling failed. It will be retried only if this row is cleared.")


def _loop() -> None:
    time.sleep(FIRST_TICK_SECONDS)
    while True:
        try:
            tick()
        except Exception as exc:
            log.exception("inbox_watch: tick failed (%s)", type(exc).__name__)
        time.sleep(TICK_SECONDS)


def start() -> None:
    threading.Thread(target=_loop, name="inbox-watch", daemon=True).start()
    log.info("inbox_watch: watching Jarvis's inbox")
