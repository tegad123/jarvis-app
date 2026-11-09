"""The one-time spreadsheet import, from a file the owner uploads in Settings.

The team's open-house tracker comes in once and the app becomes the place
agents work. Same header-text matching as the live-sheet importer
(leads_import.derive_map): a column is what its header says, never where it
sits.

TWO STEPS. `preview` reads the file and says what would happen — per tab, how
many leads, which agent each tab's leads go to, and every agent name the team
list can't place. `run` writes, with the owner's answer for each unplaced name
(a person, or nobody). Nothing is written by preview.

IDEMPOTENT. Each row's key is the sheet's own Lead ID; a row without one gets
a key from its tab, name and phone or email. Importing the same file twice
adds nothing new: a lead already brought in only has its EMPTY fields filled,
so what an agent has changed in the app since is never overwritten. Two rows sharing a Lead ID under
DIFFERENT names are a collision: the second is left out and listed, never
merged over the first.

.xlsx needs openpyxl; where it isn't installed the preview says so and asks for
a .csv (File → Download → CSV, one tab at a time) — said, and counted, never
silently read some other way.
"""
import csv
import io
import logging
import re
import secrets
import threading
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from . import leads_source, leads_store
from . import threads as threads_mod
from .leads_import import derive_map

log = logging.getLogger("app.leads_upload")

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20000
HEADER_SCAN_ROWS = 6
PENDING_TTL = 30 * 60
_pending: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


class UploadError(Exception):
    def __init__(self, error: str, detail: str):
        super().__init__(detail)
        self.error, self.detail = error, detail


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_tabs(filename: str, data: bytes) -> List[Tuple[str, List[List[str]]]]:
    if len(data) > MAX_BYTES:
        raise UploadError("too_large", "That file is over 10 MB.")
    name = (filename or "").lower()
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig", "replace")
        title = re.sub(r"\.csv$", "", filename or "Sheet", flags=re.I)
        return [(title, [[c.strip() for c in row] for row in csv.reader(io.StringIO(text))])]
    if name.endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl  # noqa: PLC0415
        except ImportError:
            threads_mod.bump_counter("leads_upload_xlsx_unavailable")
            log.error("leads_upload: openpyxl is not installed; .xlsx cannot be read here")
            raise UploadError("xlsx_unavailable", "This server can't read .xlsx files yet. Download each tab as "
                                                  ".csv (File → Download → CSV) and upload those.")
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise UploadError("unreadable", f"That file couldn't be opened as a spreadsheet ({type(exc).__name__}).")
        tabs = []
        for ws in wb.worksheets:
            rows = [[_cell(c) for c in r] for r in ws.iter_rows(values_only=True)]
            tabs.append((ws.title.strip(), rows[:MAX_ROWS]))
        return tabs
    raise UploadError("bad_type", "Upload a .xlsx or .csv file.")


def _header_row(rows: List[List[str]]) -> Optional[int]:
    """The first of the top rows with a lead-name column. The tracker has a
    merged banner above its header; other files don't."""
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        if "name" in derive_map(row)[0]:
            return i
    return None


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m/%d", "%b %d, %Y", "%B %d, %Y", "%b %d", "%B %d")


def parse_date(raw: str, today: date) -> Optional[str]:
    s = (raw or "").strip()
    if not s:
        return None
    s = s.split(" ")[0] if re.match(r"^\d{4}-\d{2}-\d{2} ", s) else s
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        if "%Y" not in fmt and "%y" not in fmt:
            d = d.replace(year=today.year)
        return d.isoformat()
    return None


_STATUS = {s.lower(): s for s in leads_store.STATUSES}
_TEMP = {t.lower(): t for t in leads_store.TEMPERATURES if t}


def _key(tab: str, name: str, phone: str, email: str) -> str:
    who = re.sub(r"\D", "", phone or "")[-10:] or (email or "").strip().lower()
    return f"upload:{leads_store.normalise_name(tab)}:{leads_store.normalise_name(name)}:{who}"


def parse(filename: str, data: bytes, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or leads_store.today_local()
    rows_out: List[Dict[str, Any]] = []
    tabs_out: List[Dict[str, Any]] = []
    seen: Dict[str, Tuple[str, str]] = {}
    co_listed, collisions, bad_dates = 0, [], 0
    for title, rows in read_tabs(filename, data):
        h = _header_row(rows)
        if h is None:
            tabs_out.append({"tab": title, "skipped": "no Lead Name column", "leads": 0})
            continue
        mapping, unmatched = derive_map(rows[h])
        n = 0
        for row in rows[h + 1:]:
            def cell(field: str) -> str:
                i = mapping.get(field)
                return (row[i] or "").strip() if i is not None and i < len(row) else ""
            name = cell("name")
            if not name:
                continue
            phone, email = cell("phone"), cell("email")
            ref = cell("external_ref") or _key(title, name, phone, email)
            prior = seen.get(ref)
            if prior is not None:
                if leads_store.normalise_name(prior[0]) == leads_store.normalise_name(name):
                    co_listed += 1
                else:
                    collisions.append({"ref": ref, "first": f"{prior[0]} ({prior[1]})",
                                       "second": f"{name} ({title})"})
                continue
            seen[ref] = (name, title)
            raw_next = cell("next_touch_at")
            nxt = parse_date(raw_next, today)
            if raw_next and not nxt:
                bad_dates += 1
            status_raw = cell("status")
            rows_out.append({
                "external_ref": ref, "tab": title, "agent_raw": cell("owner_raw") or title,
                "name": name[:200], "email": email[:320], "phone": phone[:64],
                "property": cell("property")[:300], "price_point": cell("price_point")[:64],
                "notes": cell("notes")[:4000], "follow_up": cell("follow_up")[:500],
                "status": _STATUS.get(status_raw.lower(), status_raw[:40] or "New"),
                "temperature": _TEMP.get(cell("temperature").lower(), ""),
                "next_touch_at": nxt, "last_activity_at": parse_date(cell("last_activity_at"), today),
            })
            n += 1
        tabs_out.append({"tab": title, "leads": n, "unmatched_headers": unmatched,
                         "columns": sorted(mapping)})
    agents: Dict[str, Dict[str, Any]] = {}
    for r in rows_out:
        a = agents.setdefault(r["agent_raw"], {"name": r["agent_raw"], "leads": 0,
                                              "person_key": leads_source.person_key_for(r["agent_raw"])})
        a["leads"] += 1
    return {"rows": rows_out, "tabs": tabs_out, "agents": sorted(agents.values(), key=lambda a: a["name"]),
            "co_listed": co_listed, "collisions": collisions, "bad_dates": bad_dates}


def _summary(parsed: Dict[str, Any]) -> Dict[str, Any]:
    refs = [r["external_ref"] for r in parsed["rows"]]
    existing = sum(1 for ref in refs if leads_store.get_by_external(ref))
    return {"leads": len(refs), "new": len(refs) - existing, "already_imported": existing,
            "tabs": parsed["tabs"], "agents": parsed["agents"], "co_listed": parsed["co_listed"],
            "collisions": parsed["collisions"], "bad_dates": parsed["bad_dates"],
            "due_now": sum(1 for r in parsed["rows"] if r["next_touch_at"]
                           and r["next_touch_at"] <= leads_store.today_local().isoformat())}


def preview(owner: str, filename: str, data: bytes) -> Dict[str, Any]:
    parsed = parse(filename, data)
    token = "imp_" + secrets.token_hex(8)
    now = time.time()
    with _lock:
        for k in [k for k, v in _pending.items() if v["expires"] < now]:
            _pending.pop(k, None)
        _pending[token] = {"owner": owner, "parsed": parsed, "expires": now + PENDING_TTL, "filename": filename}
    threads_mod.bump_counter("leads_upload_previewed")
    return {"token": token, **_summary(parsed)}


def run(owner: str, token: str, agents: Dict[str, Optional[str]], known: set) -> Dict[str, Any]:
    """Write the previewed rows. `agents` answers each unplaced name: a person
    key from `known`, or None for nobody. A name left unanswered is refused,
    so no lead is given to a guess."""
    with _lock:
        pending = _pending.get(token)
        if not pending or pending["owner"] != owner or pending["expires"] < time.time():
            raise UploadError("expired", "That preview has expired. Upload the file again.")
    parsed = pending["parsed"]
    owners: Dict[str, Optional[str]] = {}
    missing = []
    for a in parsed["agents"]:
        if a["name"] in agents:
            chosen = agents[a["name"]] or None
            if chosen is not None and chosen not in known:
                raise UploadError("unknown_person", f"{chosen!r} isn't on the team.")
            owners[a["name"]] = chosen
        elif a["person_key"]:
            owners[a["name"]] = a["person_key"]
        else:
            missing.append(a["name"])
    if missing:
        raise UploadError("unplaced_agents", "Say who these leads belong to first: " + ", ".join(missing))
    created = updated = 0
    for r in parsed["rows"]:
        fields = {k: r[k] for k in ("name", "email", "phone", "property", "price_point", "notes", "follow_up",
                                    "status", "temperature", "next_touch_at", "last_activity_at") if r[k]}
        fields["owner_person"] = owners[r["agent_raw"]]
        before = leads_store.get_by_external(r["external_ref"])
        if before:
            fill = {k: v for k, v in fields.items() if not before.get(k)}
            if fill:
                leads_store.update(before["lead_id"], **fill)
            updated += 1
        else:
            lead = leads_store.upsert_external(r["external_ref"], emit=False, **fields)
            created += 1
            leads_store.add_touch(lead["lead_id"], owner, "imported", note=r["follow_up"] or None,
                                  next_touch_at=lead.get("next_touch_at"))
    with _lock:
        _pending.pop(token, None)
    threads_mod.bump_counter("leads_upload_runs")
    threads_mod.bump_counter("leads_upload_created", created)
    threads_mod.bump_counter("leads_upload_updated", updated)
    log.info("leads_upload: %d created, %d updated", created, updated)
    leads_store._emit(f"Imported {created + updated} leads from the spreadsheet",
                      f"{created} new · {updated} updated", "lead.imported")
    return {"ok": True, "created": created, "updated": updated, "collisions": len(parsed["collisions"])}
