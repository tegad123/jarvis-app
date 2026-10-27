"""Texts waiting for a reply, emailed to the person at set local times.

For each person with a Mac connected: every conversation (group chats too)
whose newest message is from someone else, older than `older_than_hours` and
newer than `lookback_days`. At each of `times_local` (the tenant's timezone)
the person gets one email from their own Gmail to themselves: that list, or
"you're all caught up". broker.send_to_self is the only way it sends.

app.text_followup in tenant config: {enabled, times_local, older_than_hours,
lookback_days}. Off unless enabled.

ONE SLOT, NOT A BACKLOG: only the latest passed time is served, and not at
all once it is more than GRACE old (the app was down). Which slot each person
was served is a small JSON file beside the accounts database.
"""
import fcntl
import html
import json
import logging
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import accounts, broker, config, imessage
from . import threads as threads_mod

log = logging.getLogger("app.text_followup")

DEFAULTS = {"enabled": False, "times_local": ["07:00", "17:00"], "older_than_hours": 2, "lookback_days": 7}
GRACE = timedelta(hours=2)
TICK_SECONDS = 300
FIRST_TICK_SECONDS = 60
MAX_LISTED = 40
LINE_CHARS = 140
STATE_PATH = accounts.DB_PATH.with_name("text_followup_state.json")


def _tc():
    if str(config.SHARED) not in sys.path:
        sys.path.insert(0, str(config.SHARED))
    import tenant_config as tc  # noqa: PLC0415
    return tc


def settings() -> Dict[str, Any]:
    out = dict(DEFAULTS)
    try:
        raw = _tc().get("app.text_followup", {}) or {}
    except Exception as exc:
        threads_mod.bump_counter("text_followup_config_unreadable")
        log.error("text_followup: config unreadable (%s); reminders are off", type(exc).__name__)
        return {**out, "enabled": False}
    if isinstance(raw, dict):
        out.update({k: raw[k] for k in DEFAULTS if k in raw})
    return out


def _zone() -> Tuple[Any, str]:
    try:
        name = _tc().timezone_name()
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError, KeyError, Exception):
        threads_mod.bump_counter("text_followup_timezone_fallback")
        log.warning("text_followup: no usable tenant timezone; using UTC")
        return timezone.utc, "UTC"


def _slots(cfg) -> List[Tuple[int, int]]:
    out = []
    for item in cfg.get("times_local") or []:
        try:
            h, m = str(item).split(":")
            out.append((int(h), int(m)))
        except ValueError:
            continue
    return sorted(out)


def due_slot(now_local: datetime, cfg) -> Optional[datetime]:
    """The latest configured time that has passed, today or yesterday."""
    passed = [now_local.replace(year=d.year, month=d.month, day=d.day, hour=h, minute=m, second=0, microsecond=0)
              for d in (now_local.date(), now_local.date() - timedelta(days=1)) for h, m in _slots(cfg)]
    passed = [p for p in passed if p <= now_local]
    return max(passed) if passed else None


def label(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{hour}{'' if dt.minute == 0 else ':%02d' % dt.minute} {'AM' if dt.hour < 12 else 'PM'}"


# ── what is waiting ──────────────────────────────────────────────────────────

def waiting(person: str, now: Optional[datetime] = None, cfg: Optional[dict] = None) -> List[Dict[str, Any]]:
    """Conversations whose newest message is someone else's, inside the window.
    Newest first. Message text is the first line only."""
    cfg = cfg or settings()
    now = now or datetime.now(timezone.utc)
    newest = (now - timedelta(hours=float(cfg["older_than_hours"]))).isoformat()
    oldest = (now - timedelta(days=float(cfg["lookback_days"]))).isoformat()
    f = imessage._fernet()
    rows = imessage._db().execute(
        "SELECT chat, sender, is_from_me, sent_at, text_cipher FROM imessages i WHERE person_key = ? "
        "AND sent_at = (SELECT MAX(sent_at) FROM imessages j WHERE j.person_key = i.person_key AND j.chat = i.chat) "
        "ORDER BY sent_at DESC", (person,)).fetchall()
    out, seen = [], set()
    for r in rows:
        if r["chat"] in seen:
            continue
        seen.add(r["chat"])
        if r["is_from_me"] or not (oldest <= r["sent_at"] <= newest):
            continue
        try:
            text = f.decrypt(r["text_cipher"]).decode("utf-8", "replace")
        except Exception:
            threads_mod.bump_counter("text_followup_decrypt_failed")
            text = ""
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if len(first) > LINE_CHARS:
            first = first[:LINE_CHARS - 1].rstrip() + "…"
        out.append({"chat": r["chat"] or r["sender"], "sender": r["sender"], "sent_at": r["sent_at"], "line": first})
    return out


def _ago(sent: datetime, now: datetime) -> str:
    hours = int((now - sent).total_seconds() // 3600)
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


FOOTNOTE = "Jarvis reads your texts to make this list; it can't send one."
SECTIONS = (("Waiting less than a day", "#e3f2fd", 0, 24, "under a day"),
            ("Waiting 1–3 days", "#fff9c4", 24, 72, "for 1–3 days"),
            ("Waiting 3+ days", "#fce4ec", 72, None, "for 3+ days"))
_esc = html.escape


def _who_html(it: Dict[str, Any]) -> str:
    if it["sender"] and it["sender"] != it["chat"]:
        return (f'<strong>{_esc(it["chat"])}</strong><br>'
                f'<small style="color:#888;">{_esc(it["sender"])} &middot; group chat</small>')
    return f'<strong>{_esc(it["chat"])}</strong>'


def _table_html(rows, header_color: str, now: datetime, tz, position: int) -> Tuple[str, int]:
    out = (f'<table border="1" cellpadding="8" cellspacing="0" '
           f'style="border-collapse:collapse;width:100%;margin-bottom:20px;border-color:#ddd;">'
           f'<thead style="background-color:{header_color};"><tr>'
           f'<th align="center" style="width:6%;font-size:0.85em;">#</th>'
           f'<th align="left" style="width:62%;">Conversation</th>'
           f'<th align="left" style="width:20%;">Received</th>'
           f'<th align="left" style="width:12%;">Waiting</th>'
           f'</tr></thead><tbody>')
    for it in rows:
        sent = datetime.fromisoformat(it["sent_at"])
        line = (f'<br><em style="color:#555;">&ldquo;{_esc(it["line"])}&rdquo;</em>' if it["line"] else "")
        out += (f'<tr><td align="center" style="font-weight:bold;color:#555;">#{position}</td>'
                f'<td>{_who_html(it)}{line}</td>'
                f'<td>{_esc(sent.astimezone(tz).strftime("%a %-I:%M %p"))}</td>'
                f'<td>{_esc(_ago(sent, now))}</td></tr>')
        position += 1
    return out + "</tbody></table>", position


def _html(items: List[Dict[str, Any]], slot_label: str, now: datetime, tz) -> str:
    n = len(items)
    day = now.astimezone(tz).strftime("%A, %b %-d")
    head = (f'<h1 style="color:#1565c0;font-size:22px;">&#128172; Texts Waiting for Your Reply '
            f'&mdash; {_esc(slot_label)}, {_esc(day)}</h1>')
    if not items:
        body = ('<p style="font-size:16px;"><strong>You\'re all caught up.</strong> '
                'No conversation is waiting on a reply from you.</p>')
    else:
        ages = [(now - datetime.fromisoformat(it["sent_at"])).total_seconds() / 3600 for it in items]
        counts = [sum(1 for a in ages if lo <= a and (hi is None or a < hi)) for _, _, lo, hi, _ in SECTIONS]
        parts = [f"{c} {short}" for (_, _, _, _, short), c in zip(SECTIONS, counts) if c]
        body = (f'<p style="font-size:16px;"><strong>{n} conversation{"s" if n != 1 else ""}</strong> '
                f'waiting for your reply &middot; {_esc(" · ".join(parts))}</p>'
                f'<hr style="border:none;border-top:2px solid #eee;margin:20px 0;">')
        listed = list(zip(items[:MAX_LISTED], ages))
        position = 1
        for name, color, lo, hi, _ in SECTIONS:
            rows = [it for it, a in listed if lo <= a and (hi is None or a < hi)]
            if not rows:
                continue
            body += f'<h2 style="font-size:17px;margin:16px 0 8px;">{_esc(name)} ({len(rows)})</h2>'
            table, position = _table_html(rows, color, now, tz, position)
            body += table
        if n > MAX_LISTED:
            body += (f'<p style="color:#888;font-style:italic;">&hellip;and {n - MAX_LISTED} more, '
                     f'waiting longest.</p>')
    return (f'<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:760px;'
            f'margin:0 auto;padding:16px;color:#222;">{head}{body}'
            f'<hr style="border:none;border-top:1px solid #ddd;margin:20px 0;">'
            f'<p style="color:#666;font-size:13px;">Reply from your phone or your Mac. {_esc(FOOTNOTE)}</p>'
            f'<p style="color:#888;font-size:0.85em;">Jarvis reply reminders &mdash; '
            f'{_esc(now.astimezone(tz).strftime("%Y-%m-%d %-I:%M %p"))}</p></body></html>')


def compose(items: List[Dict[str, Any]], slot_label: str, now: datetime, tz) -> Tuple[str, str, str]:
    """Subject, plain text, and the HTML version of the same list."""
    page = _html(items, slot_label, now, tz)
    if not items:
        return (f"{slot_label}: you're all caught up on texts",
                f"No conversation is waiting on a reply from you.\n\n{FOOTNOTE}\n", page)
    n = len(items)
    lines = [f"{n} conversation{'s' if n != 1 else ''} {'are' if n != 1 else 'is'} waiting for your reply:", ""]
    for it in items[:MAX_LISTED]:
        sent = datetime.fromisoformat(it["sent_at"])
        who = it["chat"]
        if it["sender"] and it["sender"] != it["chat"]:
            who = f"{it['sender']} in {it['chat']}"
        lines.append(f"• {who} · {_ago(sent, now)} ({sent.astimezone(tz).strftime('%a %-I:%M %p')})")
        if it["line"]:
            lines.append(f"  “{it['line']}”")
    if n > MAX_LISTED:
        lines.append(f"…and {n - MAX_LISTED} more.")
    lines += ["", f"Reply from your phone or your Mac. {FOOTNOTE}"]
    return f"{slot_label}: {n} text{'s' if n != 1 else ''} waiting for your reply", "\n".join(lines) + "\n", page


def send_now(person: str, now: Optional[datetime] = None, kind: str = "now",
             slot: Optional[datetime] = None) -> Dict[str, Any]:
    tz, _ = _zone()
    now = now or datetime.now(timezone.utc)
    items = waiting(person, now)
    subject, body, page = compose(items, label((slot or now).astimezone(tz)), now, tz)
    status, content = broker.send_to_self(person, subject, body, kind, html=page)
    result = "sent" if status == 200 else (content.get("error") or f"http_{status}")
    threads_mod.bump_counter("text_followup_sent" if status == 200 else f"text_followup_{result}")
    if status != 200:
        log.warning("text_followup: reminder for a person not sent (%s)", result)
    return {"result": result, "waiting": len(items), "status": status}


# ── the schedule ─────────────────────────────────────────────────────────────

def _people() -> List[str]:
    rows = imessage._db().execute(
        "SELECT DISTINCT person_key FROM mac_devices WHERE revoked_at IS NULL").fetchall()
    return [r["person_key"] for r in rows]


def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text())
    except FileNotFoundError:
        return {}
    except Exception:
        threads_mod.bump_counter("text_followup_state_unreadable")
        log.error("text_followup: state file unreadable; starting it again")
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_PATH)


def tick(now: Optional[datetime] = None) -> Dict[str, str]:
    """Serve the latest passed slot to everyone not yet served it. Returns
    {person: what happened} for this tick."""
    cfg = settings()
    if not cfg.get("enabled"):
        return {}
    tz, _ = _zone()
    now = now or datetime.now(timezone.utc)
    due = due_slot(now.astimezone(tz), cfg)
    if due is None:
        return {}
    key = due.strftime("%Y-%m-%dT%H:%M")
    done: Dict[str, str] = {}
    with open(STATE_PATH.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _load_state()
        for person in _people():
            mine = state.get(person) or {}
            if mine.get("slot") == key:
                continue
            if not (accounts.feature_on(person, "imessage")):
                done[person] = "imessage_off"
            elif now - due.astimezone(timezone.utc) > GRACE:
                threads_mod.bump_counter("text_followup_slot_missed")
                log.warning("text_followup: the %s reminder was more than %s late; skipped", label(due), GRACE)
                done[person] = "missed"
            else:
                done[person] = send_now(person, now, kind=key, slot=due)["result"]
                if done[person] not in ("sent", "not_connected", "feature_off", "shadowed", "writes_disabled",
                                        "sentinel_denied", "recipient_refused", "not_self"):
                    continue
            state[person] = {"slot": key, "result": done[person], "at": now.isoformat()}
        _save_state(state)
    return done


def last(person: str) -> Optional[Dict[str, Any]]:
    return _load_state().get(person)


def _loop() -> None:
    import time  # noqa: PLC0415
    time.sleep(FIRST_TICK_SECONDS)
    while True:
        try:
            tick()
        except Exception as exc:
            threads_mod.bump_counter("text_followup_tick_failed")
            log.exception("text_followup: tick failed (%s)", type(exc).__name__)
        time.sleep(TICK_SECONDS)


def start() -> None:
    if not settings().get("enabled"):
        log.info("text_followup: reply reminders are off for this account")
        return
    threading.Thread(target=_loop, name="text-followup", daemon=True).start()
    log.info("text_followup: reply reminders on at %s", ", ".join(settings()["times_local"]))
