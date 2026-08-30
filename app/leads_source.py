"""Which lead source serves the screen — and one response contract for both.

WHY A SEAM RATHER THAN A SWAP
    Leads have two possible homes during the transition: the tenant's Google
    Sheet (app/leads.py, read-only) and the app's own store (leads_store.py,
    read-write). The OWNER STAYS SHEET-BACKED by decision; fresh tenants start
    app-native. Both must therefore work at the same time, on the same screen.

    So the response contract is fixed here and both readers are mapped onto it.
    The Leads screen never learns which one answered — it only gets a `source`
    string it can show. Switching a tenant over becomes a config change rather
    than a frontend release.

CHOOSING
    JARVIS_LEADS_SOURCE env  ->  explicit override, for testing and cutover
    skills.leads.source      ->  tenant config
    otherwise                ->  'sheet' when a sheet_id is configured, else 'store'
    The fallback is deliberately conservative: a tenant who has a sheet keeps
    reading it until someone decides otherwise.
"""

import os
import sys
from typing import Any, Dict, List, Optional

from . import config, leads as sheet_leads, leads_store

if str(config.SHARED) not in sys.path:
    sys.path.insert(0, str(config.SHARED))

import tenant_config as tc  # noqa: E402

TEMPERATURE_RANK = {"hot": 3, "warm": 2, "cold": 1}


def source() -> str:
    explicit = (os.environ.get("JARVIS_LEADS_SOURCE") or "").strip().lower()
    if explicit in ("sheet", "store"):
        return explicit
    configured = (tc.get("skills.leads.source", "") or "").strip().lower()
    if configured in ("sheet", "store"):
        return configured
    return "sheet" if tc.get("skills.lead-reply.sheet_id", "") else "store"


def _display_name(person_key: Optional[str]) -> str:
    """A person key rendered the way a human reads it.

    THE STORE HOLDS KEYS; THE SCREEN SHOWS NAMES. owner_person is deliberately
    a key — that is the migration-002 discipline and it is what makes W3's
    scoping a WHERE clause. But the sheet reader's `agent` column holds a
    DISPLAY name ("Martin"), and the screen renders whichever it is given. So
    an app-native tenant was reading "· omar" where a sheet-backed one read
    "· Martin": the same field, two registers, decided by which backend the
    tenant happens to be on.

    Falls back to the key rather than to blank — an unresolvable owner should
    still be visible as SOMETHING, because "no owner shown" and "owner we
    cannot name" are different problems and the screen should not merge them.
    """
    if not person_key:
        return ""
    try:
        import sys as _sys
        if str(config.SHARED) not in _sys.path:
            _sys.path.insert(0, str(config.SHARED))
        import identity as _identity
        return _identity.display_name_for(person_key) or person_key
    except Exception:
        return person_key


def _to_screen_row(lead: Dict[str, Any]) -> Dict[str, Any]:
    """A store row in the shape the screen already renders.

    The sheet reader's row is the contract because the screen was built against
    it; mapping the store onto it (rather than the reverse) means the UI needs
    no change to read app-native leads.
    """
    temp = (lead.get("temperature") or "").strip()
    status = (lead.get("status") or "").strip()
    return {
        "lead_id": lead["lead_id"],
        "name": lead.get("name") or "",
        "property": lead.get("property") or "",
        "price": lead.get("price_point") or "",
        "date": (lead.get("last_activity_at") or lead.get("created_at") or "")[:10],
        "temperature": temp,
        "temperature_rank": TEMPERATURE_RANK.get(temp.lower(), 0),
        "status": status,
        "follow_up": lead.get("follow_up") or "",
        "next_follow_up": (lead.get("next_touch_at") or "")[:10],
        "last_nudge": "",
        "agent": _display_name(lead.get("owner_person")),
        # `tab` stays the KEY: it is the grouping value, and grouping on a
        # display name would merge two people who share one.
        "tab": lead.get("owner_person") or "",
        "is_new": status == "New",
        "is_hot": temp.lower() == "hot",
        "needs_touch": status == "No Response",
        # Fields the sheet has no notion of, carried so the UI can grow into
        # them without another contract change.
        "created_by": lead.get("created_by") or "user",
        "thread_id": lead.get("thread_id"),
        "archived": bool(lead.get("archived")),
    }


def _owned_by(row: Dict[str, Any], viewer: str) -> bool:
    """Does this screen row belong to `viewer`?

    `agent` is the sheet's tab name ("Martin") and the store's owner_person key
    ("martin") — identity.resolve flattens both onto the same key, which is why
    the two sources can share one filter.
    """
    raw = (row.get("agent") or row.get("tab") or "").strip()
    if not raw:
        return False          # unassigned is not everyone's; see feed.py
    try:
        import sys as _sys
        if str(config.SHARED) not in _sys.path:
            _sys.path.insert(0, str(config.SHARED))
        import identity as _identity
        return _identity.resolve(raw) == viewer
    except Exception:
        return False


def load(viewer: Optional[str] = None) -> Dict[str, Any]:
    """The screen's payload, from whichever source is configured.

    `viewer=None` is an unscoped session (owner, admin, break-glass operator).
    A person key restricts the payload to leads assigned to them — for the
    store that is a real WHERE clause, for the sheet it is the same predicate
    applied in Python because the sheet has no query language.
    """
    src = source()
    if src == "sheet":
        payload = sheet_leads.load()
        payload["source"] = "sheet"
        if viewer is not None:
            kept = [l for l in payload.get("leads", []) if _owned_by(l, viewer)]
            payload["leads"] = kept
            payload["count"] = len(kept)
            payload["tabs"] = sorted({l["agent"] for l in kept if l.get("agent")})
            payload["counts"] = {
                "all": len(kept),
                "needs_touch": sum(1 for l in kept if l.get("needs_touch")),
                "hot": sum(1 for l in kept if l.get("is_hot")),
                "new": sum(1 for l in kept if l.get("is_new")),
            }
        return payload

    # THE WHERE CLAUSE. owner_person=None means no filter, which is what
    # leads_store.query() already does for an unscoped session.
    rows = leads_store.query(owner_person=viewer)
    screen = [_to_screen_row(r) for r in rows]
    screen.sort(key=lambda l: (-l["temperature_rank"], not l["needs_touch"], l["date"]))
    counts = leads_store.counts()
    return {
        "leads": screen,
        "count": len(screen),
        # The store has no tabs; owners stand in so the UI's grouping still has
        # something meaningful to show.
        "tabs": sorted({l["agent"] for l in screen if l["agent"]}),
        "sheet_id": "",
        "sheet_title": "",
        "counts": ({k: counts[k] for k in ("all", "needs_touch", "hot", "new")}
                   if viewer is None else {
                       "all": len(screen),
                       "needs_touch": sum(1 for l in screen if l["needs_touch"]),
                       "hot": sum(1 for l in screen if l["is_hot"]),
                       "new": sum(1 for l in screen if l["is_new"]),
                   }),
        "source": "store",
    }
