"""Leads — read-only view of the tenant's open-house lead tracker.

The sheet stays the write surface (spec: "No editing in v1 — the sheet remains
the write surface"). This module only reads, through _shared/google_creds.py so
credentials resolve exactly as they do for every skill: the tenant's stored
Google connection, or a "reconnect Google" refusal.

SHEET SHAPE — read off the live sheet, not assumed:

  * Per-agent tabs, one per person. Tab titles carry TRAILING SPACES
    ('Spencer ', 'Alex ', 'Tega ', 'OLD LEADS ') — always .strip() before
    comparing, or every lookup silently misses.
  * Row 0 is a merged title banner; the header row is at INDEX 1.
  * Columns A-P: Date, Lead Name, Property Toured, Price Point, Notes,
    Lead Temperature, Follow-up Status, Status Of Lead, Next Follow Up Date,
    Assigned Agent, Email, Phone, Internal Agent Email, Lead ID, Nudge Count,
    Last Nudge Date.
    (Lead ID is column N. An earlier audit recorded it as P; P is Last Nudge
    Date. Corrected here against the live sheet.)
  * 'SEND LOG' is a different shape entirely (no Lead Name) — excluded.
  * 'OLD LEADS ' is an archive whose columns diverge after L — excluded.

Vocabularies that actually occur:
  Lead Temperature  Hot | Warm | Cold | ''      (blank is common)
  Status Of Lead    No Response | Responded | Not Interested | ''
  Follow-up Status  free text — 'New', 'Contacted', 'Emailed 6/15',
                    'Called on 7/9'. NOT an enum; never chip it.
"""

import os
import sys
from typing import Any, Dict, List, Optional

from . import config

if str(config.SHARED) not in sys.path:
    sys.path.insert(0, str(config.SHARED))

# The tenant's lead tracker. Provisioning is meant to supply this via
# skills.lead-reply.sheet_id; that slot is still empty for this client, so the
# known sheet is the fallback and the env var is the override.
DEFAULT_SHEET_ID = "1XU_BDhJy6-ABY0ul1DzPUdWCRHSNj8f2BTTC4fMHg2U"

# Active agent tabs only.
ACTIVE_TABS = ("Spencer", "Martin", "Alex", "Tega", "Blake")
EXCLUDED_TABS = ("SEND LOG", "OLD LEADS")

HEADER_ROW_INDEX = 1

COL = {
    "date": 0,          # A
    "name": 1,          # B
    "property": 2,      # C
    "price": 3,         # D
    "notes": 4,         # E
    "temperature": 5,   # F
    "follow_up": 6,     # G
    "status": 7,        # H
    "next_follow_up": 8,  # I
    "agent": 9,         # J
    "email": 10,        # K
    "phone": 11,        # L
    "lead_id": 13,      # N
    "nudge_count": 14,  # O
    "last_nudge": 15,   # P
}

TEMPERATURE_RANK = {"hot": 3, "warm": 2, "cold": 1}


class LeadsUnavailable(RuntimeError):
    """The sheet could not be read. Carries an operator-readable reason."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


def sheet_id() -> str:
    return (
        os.environ.get("JARVIS_LEADS_SHEET_ID")
        or _configured_sheet_id()
        or DEFAULT_SHEET_ID
    )


def _configured_sheet_id() -> Optional[str]:
    """skills.lead-reply.sheet_id from the client config, when provisioned."""
    try:
        import json

        path = config.SHARED / "client-config.json"
        if not path.exists():
            return None
        with open(path) as fh:
            cfg = json.load(fh)
        value = ((cfg.get("skills") or {}).get("lead-reply") or {}).get("sheet_id")
        return value.strip() if isinstance(value, str) and value.strip() else None
    except Exception:
        return None


def _cell(row: List[str], key: str) -> str:
    idx = COL[key]
    return (row[idx] if idx < len(row) else "").strip()


def _needs_touch(status: str) -> bool:
    """
    Contacted, never replied — the leads actually owed a follow-up.

    Deliberately excludes blanks (never contacted at all) and 'Not Interested'
    (dead). This is a business rule, not something the sheet states; it is
    defined here so the chip means one specific thing.
    """
    return status.lower() == "no response"


def _build_lead(row: List[str], tab: str) -> Optional[Dict[str, Any]]:
    name = _cell(row, "name")
    if not name:
        return None
    temperature = _cell(row, "temperature")
    status = _cell(row, "status")
    follow_up = _cell(row, "follow_up")
    return {
        "lead_id": _cell(row, "lead_id"),
        "name": name,
        "property": _cell(row, "property"),
        "price": _cell(row, "price"),
        "date": _cell(row, "date"),
        "temperature": temperature,
        "temperature_rank": TEMPERATURE_RANK.get(temperature.lower(), 0),
        "status": status,
        "follow_up": follow_up,
        "next_follow_up": _cell(row, "next_follow_up"),
        "last_nudge": _cell(row, "last_nudge"),
        # J is often blank; the tab name is the reliable owner.
        "agent": _cell(row, "agent") or tab,
        "tab": tab,
        "is_new": follow_up.lower() == "new",
        "is_hot": temperature.lower() == "hot",
        "needs_touch": _needs_touch(status),
    }


def load() -> Dict[str, Any]:
    """
    Every lead across the active agent tabs.

    Raises LeadsUnavailable with a human-readable reason on any credential or
    network failure — the caller turns that into an honest error state rather
    than an empty table that reads as "no leads".
    """
    try:
        import google_creds
    except ImportError as exc:
        raise LeadsUnavailable(
            "Lead tracker is not configured on this server.",
            f"google_creds unavailable: {exc}",
        ) from exc

    try:
        client = google_creds.gspread_client()
    except google_creds.GoogleNotConnected as exc:
        raise LeadsUnavailable(google_creds.RECONNECT_MESSAGE, exc.reason) from exc
    except Exception as exc:
        raise LeadsUnavailable(
            "Jarvis could not authenticate with Google.",
            f"{exc.__class__.__name__}: {exc}",
        ) from exc

    sid = sheet_id()
    try:
        sh = client.open_by_key(sid)
        worksheets = sh.worksheets()
    except Exception as exc:
        raise LeadsUnavailable(
            "Jarvis could not open the lead tracker.",
            f"{exc.__class__.__name__}: {exc}",
        ) from exc

    leads: List[Dict[str, Any]] = []
    tabs_read: List[str] = []
    for ws in worksheets:
        # Trailing spaces in tab titles are real; strip before every comparison.
        title = ws.title.strip()
        if title in EXCLUDED_TABS or title not in ACTIVE_TABS:
            continue
        try:
            values = ws.get_all_values()
        except Exception as exc:
            raise LeadsUnavailable(
                "Jarvis could not read a tab of the lead tracker.",
                f"{title}: {exc.__class__.__name__}: {exc}",
            ) from exc
        tabs_read.append(title)
        for row in values[HEADER_ROW_INDEX + 1 :]:
            lead = _build_lead(row, title)
            if lead:
                leads.append(lead)

    # Hottest first, then the ones owed a follow-up, then most recent.
    leads.sort(
        key=lambda l: (-l["temperature_rank"], not l["needs_touch"], l["date"]),
        reverse=False,
    )

    return {
        "leads": leads,
        "count": len(leads),
        "tabs": tabs_read,
        "sheet_id": sid,
        "sheet_title": sh.title.strip(),
        "counts": {
            "all": len(leads),
            "needs_touch": sum(1 for l in leads if l["needs_touch"]),
            "hot": sum(1 for l in leads if l["is_hot"]),
            "new": sum(1 for l in leads if l["is_new"]),
        },
    }
