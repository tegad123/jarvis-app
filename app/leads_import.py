"""One-time importer: a tenant's lead Sheet -> the app's native store.

WHY THIS DERIVES THE COLUMN MAP INSTEAD OF READING ONE
    A stored column map is the artefact that keeps being wrong. This tree has
    now produced the same defect twice:
      * the original audit recorded Lead ID at column P. P is Last Nudge Date;
        the real column is N. app/leads.py was corrected against the live sheet.
      * the Phase 3 sweep then wrote `column_map.lead_id = "P"` into runtime.json
        anyway, from the audit, without cross-checking the code that had already
        fixed it. Nothing consumed it, so nothing broke — a loaded gun, not a
        fired one.
    The spec's instruction is explicit: "verify headers, never trust a stored
    map". So this reads the LIVE HEADER ROW and matches on header TEXT. There is
    no column map in config any more, and this importer would ignore one if
    there were.

    A live read also picks up what no stored map had: this sheet has a
    'Thread ID' column at Q that appears in no audit, no config, and no code.

WHAT IT REFUSES TO GUESS
    Headers it cannot match are REPORTED, not mapped by position. An owner it
    cannot resolve to a person key is left NULL and listed — the same discipline
    as migration 002, for the same reason: a guessed principal silently
    attributes someone's work to the wrong person.

IDEMPOTENT via external_ref (the sheet's own Lead ID). Re-running updates what
it already brought in rather than duplicating every lead.

Usage:
    python3 -m app.leads_import --dry-run     # derive + report, write nothing
    python3 -m app.leads_import --execute
"""

import argparse
import sys
from typing import Any, Dict, List, Optional, Tuple

from . import config, leads_store

if str(config.SHARED) not in sys.path:
    sys.path.insert(0, str(config.SHARED))

import identity  # noqa: E402
import tenant_config as tc  # noqa: E402

# Header TEXT -> store field. Matching is case/space-insensitive; several
# spellings map to one field because tenants word these differently.
HEADER_FIELDS: Dict[str, Tuple[str, ...]] = {
    "name": ("lead name", "name", "client name"),
    "property": ("property toured", "property", "address"),
    "price_point": ("price point", "price", "budget"),
    "notes": ("notes", "note"),
    "temperature": ("lead temperature", "temperature", "temp"),
    "follow_up": ("follow-up status", "follow up status", "followup status"),
    "status": ("status of lead", "lead status", "status"),
    "next_touch_at": ("next follow up date", "next follow-up date", "next touch"),
    "owner_raw": ("assigned agent", "agent", "owner"),
    "email": ("email", "e-mail", "email address"),
    "phone": ("phone", "phone number", "mobile"),
    "external_ref": ("lead id", "leadid", "id"),
    "last_activity_at": ("date", "created", "date added"),
    "thread_id": ("thread id", "channel id"),
}

# Row 0 is a merged title banner on this sheet; the header row is row 1.
HEADER_ROW_INDEX = 1


def _norm(h: str) -> str:
    return " ".join((h or "").strip().lower().split())


def derive_map(header: List[str]) -> Tuple[Dict[str, int], List[str]]:
    """(field -> column index, unmatched header texts). Position is never assumed."""
    mapping: Dict[str, int] = {}
    unmatched: List[str] = []
    for idx, raw in enumerate(header):
        h = _norm(raw)
        if not h:
            continue
        hit = next((field for field, names in HEADER_FIELDS.items() if h in names), None)
        # First match wins: a sheet with two "Notes" columns keeps the leftmost
        # rather than silently letting the rightmost overwrite it.
        if hit and hit not in mapping:
            mapping[hit] = idx
        elif not hit:
            unmatched.append(raw.strip())
    return mapping, unmatched


def _col_letter(i: int) -> str:
    return chr(65 + i) if i < 26 else f"col{i}"


def run(dry_run: bool = True) -> int:
    sheet_id = tc.get("skills.lead-reply.sheet_id", "")
    if not sheet_id:
        print("No skills.lead-reply.sheet_id configured — nothing to import.", file=sys.stderr)
        return 2
    exclude = {t.strip().upper() for t in tc.get("skills.lead-reply.exclude_tabs", []) or []}

    import google_creds  # noqa: PLC0415  (only this live-sheet path needs it; the upload importer must not)
    gc = google_creds.gspread_client()
    sh = gc.open_by_key(sheet_id)

    print(f"sheet: {sh.title.strip()}")
    print(f"excluded tabs (structural, from config): {sorted(exclude) or '(none)'}\n")

    imported = updated = skipped = 0
    unresolved: Dict[str, int] = {}
    # external_ref -> (name, tab) of the first row that claimed it. A repeated
    # id is NOT automatically an error: this sheet co-lists a lead on two
    # agents' tabs, and collapsing those is correct. A repeat with a DIFFERENT
    # name is a genuine id collision and must be shouted about, because the
    # merge would silently destroy one of the two leads.
    seen_refs: Dict[str, Tuple[str, str]] = {}
    co_listed: List[str] = []
    collisions: List[str] = []

    for ws in sh.worksheets():
        title = ws.title.strip()          # titles carry trailing spaces
        if title.upper() in exclude:
            print(f"-- {title}: excluded")
            continue
        values = ws.get_all_values()
        if len(values) <= HEADER_ROW_INDEX:
            print(f"-- {title}: no header row, skipped")
            continue

        mapping, unmatched = derive_map(values[HEADER_ROW_INDEX])
        if "name" not in mapping:
            # No lead-name column means this is not a lead tab (the SEND LOG
            # shape). Detected by STRUCTURE, so a tenant who names it something
            # else is still handled.
            print(f"-- {title}: no lead-name column — not a lead tab, skipped")
            continue

        print(f"-- {title}: derived column map")
        for field, idx in sorted(mapping.items(), key=lambda kv: kv[1]):
            print(f"     {_col_letter(idx):>4} [{idx:2}]  {field}")
        if unmatched:
            print(f"     unmatched headers (left alone, never guessed): {unmatched}")

        for row in values[HEADER_ROW_INDEX + 1:]:
            def cell(field: str) -> Optional[str]:
                i = mapping.get(field)
                if i is None or i >= len(row):
                    return None
                v = (row[i] or "").strip()
                return v or None

            name = cell("name")
            if not name:
                skipped += 1
                continue

            owner_raw = cell("owner_raw") or title
            owner_key = None
            try:
                owner_key = identity.resolve(owner_raw)
            except Exception:
                owner_key = None
            if not owner_key:
                unresolved[owner_raw] = unresolved.get(owner_raw, 0) + 1

            fields: Dict[str, Any] = {
                "name": name,
                "email": cell("email"),
                "phone": cell("phone"),
                "property": cell("property"),
                "price_point": cell("price_point"),
                "notes": cell("notes"),
                "status": cell("status") or "New",
                "temperature": cell("temperature") or "",
                "follow_up": cell("follow_up"),
                "owner_person": owner_key,
                "thread_id": cell("thread_id"),
                "last_activity_at": cell("last_activity_at"),
                "next_touch_at": cell("next_touch_at"),
            }
            ref = cell("external_ref")
            if ref:
                prior = seen_refs.get(ref)
                if prior is None:
                    seen_refs[ref] = (name, title)
                elif prior[0].strip().lower() == name.strip().lower():
                    co_listed.append(f"{ref}: {name} on {prior[1]} and {title}")
                else:
                    collisions.append(
                        f"{ref}: {prior[0]!r} on {prior[1]} vs {name!r} on {title}")
            if dry_run:
                imported += 1
                continue
            if ref:
                before = leads_store.get_by_external(ref)
                leads_store.upsert_external(ref, **fields)
                if before:
                    updated += 1
                else:
                    imported += 1
            else:
                # No sheet id: cannot be matched on a re-run, so it is created
                # once and flagged rather than silently duplicated later.
                leads_store.create(**fields)
                imported += 1

    print(f"\n{'WOULD IMPORT' if dry_run else 'imported'}: {imported}"
          f"   {'' if dry_run else f'updated: {updated}   '}skipped (no name): {skipped}")
    if co_listed:
        print(f"\nCO-LISTED LEADS ({len(co_listed)}) — the same lead on two agents' tabs, sharing "
              f"one id. Merged into a single lead, which is why the store holds fewer rows than "
              f"the sheet has lines:")
        for c in co_listed:
            print(f"    {c}")
    if collisions:
        print(f"\n!! ID COLLISIONS ({len(collisions)}) — the same id used for DIFFERENT people. "
              f"These merged and one of each pair has been overwritten. Fix the sheet, then "
              f"re-import:")
        for c in collisions:
            print(f"    {c}")
    if unresolved:
        print("\nUNRESOLVED OWNERS — left NULL, never guessed. Each is a provisioning gap:")
        for who, n in sorted(unresolved.items()):
            print(f"    {who:32} ({n} lead(s)) — add to identity.people, then re-run")
    if dry_run:
        print("\nDRY RUN — nothing written.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
