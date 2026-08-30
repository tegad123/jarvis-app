"""Bridge to _shared/notify.py — the app's only source of activity events.

The app does not parse activity_feed.jsonl itself. notify.read_feed() owns the
file format (including skipping malformed lines), so reading through it keeps
one implementation of "what an event is" as the feed grows adapters.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config

if str(config.SHARED) not in sys.path:
    sys.path.insert(0, str(config.SHARED))

import notify as _notify  # noqa: E402  (path must be set first)
import identity as _identity  # noqa: E402
import tenant_config as _tc  # noqa: E402


# ── who an event belongs to ───────────────────────────────────────────────────
#
# WORKSTREAM 3 SCOPING. Owner, admin and the break-glass operator read the whole
# feed. A member reads THEIR OWN work plus whatever the tenant has declared
# team-wide — and nothing else.
#
# THE DEFAULT IS DENY. `owner_keys()` returning an empty set does NOT mean
# "everyone"; it means "we could not establish whose this is", and a member does
# not see it. That matters concretely here: the 56 email events on this box carry
# no assignee because they came out of the OWNER'S mailbox. Treating
# unattributed as public would hand a member the owner's inbox, which is exactly
# the failure this scoping exists to prevent.
#
# The escape hatch is config, not code: a tenant lists the destinations that are
# genuinely everybody's business. Empty default, so a tenant who never sets it
# is closed rather than open.


def team_destinations() -> frozenset:
    """Destinations every member may read. Tenant-declared; empty by default."""
    try:
        vals = _tc.get("app.feed.team_destinations", []) or []
    except Exception:
        vals = []
    return frozenset(str(v) for v in vals)


def owner_keys(event: Dict[str, Any]) -> set:
    """Person keys this event belongs to, resolved through the registry.

    Reads the attachment fields the emitters actually write — `assignee` (an
    address, from task-manager) and `owner_person` (already a key, from the
    leads store). Both go through identity.resolve so an address, a display
    name or a key all land on the same person.
    """
    keys = set()
    for att in event.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        for field in ("owner_person", "assignee", "assignee_name"):
            raw = att.get(field)
            if not raw:
                continue
            try:
                key = _identity.resolve(str(raw))
            except Exception:
                key = None
            if key:
                keys.add(key)
    return keys


def visible_to(event: Dict[str, Any], viewer: Optional[str]) -> bool:
    """`viewer=None` means an unscoped session and never reaches here."""
    if viewer is None:
        return False
    if (event.get("destination") or "") in team_destinations():
        return True
    return viewer in owner_keys(event)


def scope(events: List[Dict[str, Any]], viewer: Optional[str]) -> List[Dict[str, Any]]:
    """THE WHERE CLAUSE for the feed. viewer=None (sees-everything) is a no-op."""
    if viewer is None:
        return events
    return [e for e in events if visible_to(e, viewer)]


def feed_path() -> Path:
    return _notify.feed_path()


def load_events(skill: Optional[str] = None,
                kind: Optional[str] = None,
                limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Oldest-first, as notify.read_feed() returns them."""
    return _notify.read_feed(limit=limit, skill=skill, kind=kind)


def query(limit: int = 50,
          skill: Optional[str] = None,
          kind: Optional[str] = None,
          destination: Optional[str] = None,
          since: Optional[str] = None,
          viewer: Optional[str] = None) -> Dict[str, Any]:
    """Filtered, newest-first slice of the feed for /api/feed.

    `total` counts everything matching the filters, so a client can tell a short
    page from an exhausted feed.
    """
    events = scope(load_events(skill=skill, kind=kind), viewer)

    if destination:
        events = [e for e in events if e.get("destination") == destination]
    if since:
        # String comparison is safe here: every ts is written by
        # datetime.isoformat() on a UTC-aware value, so the encoding is uniform.
        events = [e for e in events if (e.get("ts") or "") > since]

    total = len(events)
    page = list(reversed(events[-limit:])) if limit else list(reversed(events))
    return {
        "events": page,
        "count": len(page),
        "total": total,
        "order": "newest_first",
        "feed_path": str(feed_path()),
    }


def _resolve_tz(tz_name: Optional[str]) -> Tuple[Any, str]:
    """A tzinfo for the caller's day boundary, falling back to UTC.

    A bad zone name degrades to UTC rather than 500-ing: a summary that is a few
    hours off is worth more to the caller than no summary at all, and the
    response says which zone was actually used so the difference is visible.
    """
    if tz_name:
        try:
            return ZoneInfo(tz_name), tz_name
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            pass
    return timezone.utc, "UTC"


def _parse_ts(ts: Any) -> Optional[datetime]:
    """Feed timestamps are datetime.isoformat() on UTC-aware values."""
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def summary(tz_name: Optional[str] = None,
            viewer: Optional[str] = None) -> Dict[str, Any]:
    """Today's event counts by kind, computed over the WHOLE feed.

    WHY THIS EXISTS: the hero says "TODAY", and the client used to compute that
    from the same 50-event page it renders. Once the feed passed 50 events the
    window stopped being the day — adding events could make a count go DOWN as
    older ones fell out of the page. Counting has to happen where the whole feed
    is, so it happens here.

    Counts are keyed by RAW kind. The kind -> display-word and kind -> "needs
    you" mappings stay in the frontend's lib/kinds.ts, which is the one place
    that owns presentation vocabulary; duplicating them server-side would give
    two answers to the same question.

    `tz_name` is the caller's IANA zone. "Today" is only meaningful in some
    timezone, and there is no tenant timezone in config; the client knows its
    own, so it sends it. Absent or invalid -> UTC.
    """
    tz, tz_used = _resolve_tz(tz_name)
    now = datetime.now(tz)
    today = now.date()

    # Scoped BEFORE counting: the hero must agree with the list under it, or
    # a member reads "3 today" over two rows and assumes the app is broken.
    events = scope(load_events(), viewer)
    counts: Dict[str, int] = {}
    total = 0
    for e in events:
        dt = _parse_ts(e.get("ts"))
        if dt is None or dt.astimezone(tz).date() != today:
            continue
        kind = e.get("kind") or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
        total += 1

    return {
        "date": today.isoformat(),
        "timezone": tz_used,
        "total": total,
        "counts": counts,
        "feed_total": len(events),
        "generated_at": now.isoformat(),
    }
