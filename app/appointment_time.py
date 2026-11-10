"""Turn a clock time plus a stated day into an America/Chicago start.

Used when a text confirms an appointment. Relative words are resolved against
the timestamp of the message that contains them, not against "now", and never
against a guessed day. No day in the new text, the task title, or the recent
thread means no event.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")
DEFAULT_DURATION = timedelta(hours=1)

_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_WD = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_MON = "|".join(sorted(set(_MONTHS), key=len, reverse=True))

_CLOCK = re.compile(
    r"\b(?:(?P<h12>\d{1,2})(?::(?P<m12>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)|"
    r"(?P<h24>\d{1,2}):(?P<m24>\d{2})|"
    r"(?P<word>noon|midnight))\b",
    re.I,
)
_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_MONTH_DAY = re.compile(
    rf"\b(?P<mon>{_MON})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<year>20\d{{2}}))?\b",
    re.I,
)
_NUM_DAY = re.compile(r"\b(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?\b")
_TOMORROW = re.compile(r"\btomorrow(?:\s+(?:night|evening|afternoon|morning))?\b", re.I)
_TONIGHT = re.compile(r"\btonight\b", re.I)
_TODAY = re.compile(r"\btoday\b", re.I)
_NEXT_WD = re.compile(rf"\bnext\s+(?P<wd>{_WD})\b", re.I)
_THIS_WD = re.compile(rf"\bthis\s+(?P<wd>{_WD})\b", re.I)
_BARE_WD = re.compile(rf"\b(?P<wd>{_WD})\b", re.I)
_FOR = re.compile(r"\bfor\s+(\d+(?:\.\d+)?)\s*(hours?|hrs?|minutes?|mins?)\b", re.I)
_UNTIL = re.compile(
    r"\buntil\s+(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?|\d{1,2}:\d{2}|noon|midnight)\b",
    re.I,
)

TextAt = Tuple[str, datetime]


def as_chicago(sent_at: datetime) -> datetime:
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    return sent_at.astimezone(CHICAGO)


def local_label(sent_at: datetime) -> str:
    """What the model is told: the message's own local moment."""
    local = as_chicago(sent_at)
    return local.strftime("%A %Y-%m-%d %I:%M %p %Z")


def clock_time(text: str) -> Optional[Tuple[int, int]]:
    match = _CLOCK.search(text or "")
    if not match:
        return None
    if match.group("word"):
        return (12, 0) if match.group("word").lower() == "noon" else (0, 0)
    if match.group("ap"):
        hour = int(match.group("h12"))
        minute = int(match.group("m12") or 0)
        if not (1 <= hour <= 12 and 0 <= minute <= 59):
            return None
        ap = match.group("ap").lower().replace(".", "")
        if ap == "pm" and hour != 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
        return hour, minute
    hour = int(match.group("h24"))
    minute = int(match.group("m24") or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def stated_day(text: str, sent_at: datetime, clock: Optional[Tuple[int, int]] = None) -> Optional[datetime]:
    """The calendar day this text names, resolved against its own timestamp.

    Returns None when the text does not name a day. The returned value is
    midnight America/Chicago on that day (the clock is applied separately).
    """
    local = as_chicago(sent_at)
    day = _day_only(text or "", local, clock)
    if day is None:
        return None
    return datetime(day.year, day.month, day.day, tzinfo=CHICAGO)


def _day_only(text: str, local: datetime, clock: Optional[Tuple[int, int]]):
    iso = _ISO.search(text)
    if iso:
        return _safe_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    named = _MONTH_DAY.search(text)
    if named:
        year = int(named.group("year") or local.year)
        month = _month_number(named.group("mon"))
        found = _safe_date(year, month, int(named.group("day")))
        if found and not named.group("year") and found < local.date():
            found = _safe_date(year + 1, month, int(named.group("day")))
        return found
    num = _NUM_DAY.search(text)
    if num:
        year = local.year
        if num.group("y"):
            year = int(num.group("y"))
            if year < 100:
                year += 2000
        found = _safe_date(year, int(num.group("m")), int(num.group("d")))
        if found and not num.group("y") and found < local.date():
            found = _safe_date(year + 1, int(num.group("m")), int(num.group("d")))
        return found
    if _TOMORROW.search(text):
        return local.date() + timedelta(days=1)
    if _TONIGHT.search(text) or _TODAY.search(text):
        return local.date()
    nxt = _NEXT_WD.search(text)
    if nxt:
        return _weekday_date(local, _WEEKDAYS[nxt.group("wd").lower()], force_next=True, clock=clock)
    this = _THIS_WD.search(text)
    if this:
        return _weekday_date(local, _WEEKDAYS[this.group("wd").lower()], force_next=False, clock=clock)
    bare = _BARE_WD.search(text)
    if bare:
        return _weekday_date(local, _WEEKDAYS[bare.group("wd").lower()], force_next=False, clock=clock)
    return None


def _month_number(token: str) -> int:
    key = token.lower().rstrip(".")
    if key in _MONTHS:
        return _MONTHS[key]
    return _MONTHS[key[:3]]


def _safe_date(year: int, month: int, day: int):
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def _weekday_date(local: datetime, weekday: int, *, force_next: bool, clock: Optional[Tuple[int, int]]):
    """Bare weekday: the soonest one, including today when the clock is still ahead.

    'next <weekday>' skips that soonest one. Said on Monday, 'Tuesday' is
    tomorrow and 'next Tuesday' is the Tuesday after that.
    """
    delta = (weekday - local.weekday()) % 7
    if delta == 0 and clock is not None:
        hour, minute = clock
        if (local.hour, local.minute) >= (hour, minute):
            delta = 7
    if force_next:
        delta += 7
    return local.date() + timedelta(days=delta)


def _duration(text: str, start: datetime) -> timedelta:
    match = _FOR.search(text or "")
    if match:
        n = float(match.group(1))
        unit = match.group(2).lower()
        if unit.startswith("min"):
            return timedelta(minutes=n)
        return timedelta(hours=n)
    until = _UNTIL.search(text or "")
    if until:
        clock = clock_time(until.group(1))
        if clock:
            end = start.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
            if end <= start:
                end += timedelta(days=1)
            return end - start
    return DEFAULT_DURATION


def resolve_appointment(
    new_text: str,
    new_sent: datetime,
    prior: Optional[Sequence[TextAt]] = None,
    task_titles: Optional[Sequence[str]] = None,
) -> Optional[Tuple[datetime, datetime]]:
    """ISO-ready start and end, or None when the day cannot be determined.

    The clock comes from the newest place that states one (the new text, then
    a task title, then older messages). The day comes from the newest place
    that states one, resolved against that message's own timestamp. A task
    title is dated with the new message.
    """
    pieces: List[TextAt] = [(new_text or "", new_sent)]
    for title in task_titles or []:
        if title:
            pieces.append((title, new_sent))
    for text, sent in prior or []:
        if text:
            pieces.append((text, sent))

    clock = None
    clock_text = ""
    day_local = None
    for text, sent in pieces:
        found = clock_time(text)
        if found and clock is None:
            clock = found
            clock_text = text
        if day_local is None:
            named = stated_day(text, sent, clock)
            if named is not None:
                day_local = named
        if clock and day_local:
            break
    if clock is None or day_local is None:
        return None
    hour, minute = clock
    start = day_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    span = _duration(clock_text, start)
    return start, start + span
