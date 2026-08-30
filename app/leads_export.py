"""Export app-native leads back out to a tenant's Google Sheet.

WHAT THIS IS FOR
    Some tenants have VA workflows, dashboards or habits built on a sheet.
    Moving storage into the app should not force them to abandon those, so the
    app can keep writing rows out. Optional, disconnectable, OFF BY DEFAULT.

WHY IT IS WRITE-GATED
    This is the one part of Workstream 1 that writes a surface the CUSTOMER
    owns and another system may also be writing. That is exactly what
    _shared/write_guard.py exists for, so every write path here calls
    block_if_readonly() first. On a box that has not been through the cutover
    checklist, export raises rather than touching the sheet.

WHY IT WRITES BY HEADER, NEVER BY LETTER
    The live sheet has columns inserted since the tooling was written: what the
    old instructions call "N (Next Follow-Up Date)" is now the LEAD ID, and
    "Q (Nudge Count)" is now Thread ID. Writing by letter would overwrite a
    lead's primary identifier with a date. Every column is located by matching
    its HEADER TEXT, and a header that cannot be found is reported, never
    guessed at by position.

WHY IT TOUCHES ONLY THE COLUMNS IT OWNS
    Column Q ("Thread ID") is populated on a handful of rows by something
    OUTSIDE this box — it holds literal Gmail-thread-shaped ids, there is no
    formula behind it, and nothing in this repository writes the sheet at all.
    Export therefore updates only the fields it is the source of truth for and
    leaves every other cell alone, so it cannot fight whatever is maintaining
    that column.
"""

import sys
from typing import Any, Dict, List, Optional, Tuple

from . import config, leads_store

if str(config.SHARED) not in sys.path:
    sys.path.insert(0, str(config.SHARED))

import google_creds  # noqa: E402
import notify as _notify  # noqa: E402
import tenant_config as tc  # noqa: E402
from write_guard import block_if_readonly  # noqa: E402

HEADER_ROW_INDEX = 1

# Store field -> the header texts that mean it. Export owns these columns.
# Deliberately narrower than the importer's map: we write back only what the
# app is authoritative for.
OWNED_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "name": ("lead name", "name"),
    "status": ("status of lead", "lead status"),
    "temperature": ("lead temperature", "temperature"),
    "follow_up": ("follow-up status", "follow up status"),
    "next_touch_at": ("next follow up date", "next follow-up date"),
    "email": ("email", "e-mail"),
    "phone": ("phone", "phone number"),
}
KEY_HEADERS = ("lead id", "leadid")


class ExportError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(tc.get("skills.leads.export_enabled", False))


def _norm(h: str) -> str:
    return " ".join((h or "").strip().lower().split())


def _locate(header: List[str]) -> Tuple[Dict[str, int], Optional[int]]:
    """(owned field -> column index, key column index). By header text only."""
    cols: Dict[str, int] = {}
    key_idx: Optional[int] = None
    for idx, raw in enumerate(header):
        h = _norm(raw)
        if not h:
            continue
        if key_idx is None and h in KEY_HEADERS:
            key_idx = idx
            continue
        for field, names in OWNED_COLUMNS.items():
            if h in names and field not in cols:
                cols[field] = idx
    return cols, key_idx


def _emit(headline: str, detail: str) -> None:
    try:
        _notify.notify("leads", headline, headline=headline, detail=detail,
                       kind="lead.exported", skill="leads")
    except Exception:
        pass


def export(sheet_id: str, dry_run: bool = True) -> Dict[str, Any]:
    """Push app-native leads to `sheet_id`. Returns a summary.

    `sheet_id` is REQUIRED and explicit — there is no default and no fallback to
    the configured tenant sheet, so an export cannot land on the live sheet
    because someone forgot an argument.
    """
    if not enabled():
        raise ExportError(
            "Sheet export is disabled for this tenant "
            "(skills.leads.export_enabled). It ships off by default."
        )
    if not sheet_id:
        raise ExportError("An explicit target sheet id is required.")

    if not dry_run:
        # THE GATE. A customer-owned surface that another system may also write.
        block_if_readonly("write the lead sheet (export)")

    gc = google_creds.gspread_client()
    sh = gc.open_by_key(sheet_id)

    rows = leads_store.query()
    by_ref = {r["external_ref"]: r for r in rows if r.get("external_ref")}
    written = skipped = 0
    missing_headers: List[str] = []

    for ws in sh.worksheets():
        values = ws.get_all_values()
        if len(values) <= HEADER_ROW_INDEX:
            continue
        cols, key_idx = _locate(values[HEADER_ROW_INDEX])
        if key_idx is None or not cols:
            continue
        if len(cols) < len(OWNED_COLUMNS):
            missing = sorted(set(OWNED_COLUMNS) - set(cols))
            missing_headers.append(f"{ws.title.strip()}: {missing}")

        updates = []
        for r_i, row in enumerate(values[HEADER_ROW_INDEX + 1:], start=HEADER_ROW_INDEX + 2):
            ref = (row[key_idx] or "").strip() if key_idx < len(row) else ""
            lead = by_ref.get(ref)
            if not ref or not lead:
                skipped += 1
                continue
            for field, c_i in cols.items():
                new = lead.get(field)
                new = "" if new is None else str(new)
                old = row[c_i] if c_i < len(row) else ""
                if new != (old or ""):
                    updates.append({"range": _a1(r_i, c_i), "values": [[new]]})
        if updates and not dry_run:
            # Cell-level updates only: never a whole-row write, so columns this
            # export does not own (Thread ID among them) are untouched.
            ws.batch_update(updates)
        written += len(updates)

    summary = {"sheet_id": sheet_id, "cells": written, "rows_skipped": skipped,
               "missing_headers": missing_headers, "dry_run": dry_run}
    if not dry_run:
        _emit(f"Exported {written} lead update{'' if written == 1 else 's'} to the sheet",
              f"sheet {sheet_id} · {skipped} row(s) not app-managed")
    return summary


def _a1(row: int, col: int) -> str:
    letters = ""
    c = col
    while True:
        letters = chr(65 + c % 26) + letters
        c = c // 26 - 1
        if c < 0:
            break
    return f"{letters}{row}"
