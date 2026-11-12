"""Reading HAR MLS Matrix pages for app/mls.py.

Built against made-up pages (docs/mls/fixtures/) because no real Matrix page
may be opened from the build; docs/mls/FIXTURE-CAPTURE.md is how real pages
are saved, sanitised and checked against this file. When they differ, edit
the mapping tables here (COLUMNS, STATUS_WORDS, TYPE_WORDS, FILTER_LABELS),
not the parsers.

Pages arrive as the worker's `tables` reply: tables (headers and cell text),
label/value cards, embedded JSON blocks, short lists and a few signals. Columns
are found by header text, never by CSS class. A column the mapping does not
name is never read, which is what keeps listing agents' names, phones and
emails, showing instructions and remarks out of a Listing.
"""
import json
import random
import re
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .mls import Listing, Result

# header text (normalised by _norm) → Listing field
COLUMNS = {
    "address": "address", "street address": "address", "property address": "address", "addr": "address",
    "status": "status", "st": "status", "listing status": "status", "stat": "status",
    "list price": "list_price", "lp": "list_price", "list $": "list_price", "current price": "list_price",
    "asking price": "list_price", "orig lp": None,
    "sold price": "sold_price", "sp": "sold_price", "close price": "sold_price", "closed price": "sold_price",
    "sold $": "sold_price", "sales price": "sold_price",
    "list date": "list_date", "listing date": "list_date", "ld": "list_date",
    "pending date": "pending_date", "contract date": "pending_date", "option/pending date": "pending_date",
    "pend date": "pending_date",
    "sold date": "sold_date", "close date": "sold_date", "closed date": "sold_date", "cd": "sold_date",
    "beds": "beds", "bedrooms": "beds", "br": "beds", "# beds": "beds", "bd": "beds",
    "baths": "baths", "bathrooms": "baths", "ba": "baths", "bths f/h": "baths", "baths f/h": "baths",
    "sqft": "sqft", "sq ft": "sqft", "bldg sqft": "sqft", "building sqft": "sqft", "sqft (bldg)": "sqft",
    "living area": "sqft", "sqft bldg": "sqft",
    "year built": "year_built", "yr built": "year_built", "yb": "year_built", "yr blt": "year_built",
    "type": "property_type", "property type": "property_type", "prop type": "property_type", "ptyp": "property_type",
    "subdivision": "area", "area": "area", "market area": "area", "neighborhood": "area",
    "legal subdivision": "area", "subdiv": "area",
    "zip": "zip_code", "zip code": "zip_code", "postal code": "zip_code",
    "dom": "days_on_market", "days on market": "days_on_market", "cdom": None,
    "ml#": "mls_number", "mls #": "mls_number", "mls#": "mls_number", "mls number": "mls_number",
    "listing id": "mls_number",
    "distance": "distance_miles", "dist": "distance_miles", "miles": "distance_miles", "dist (mi)": "distance_miles",
}
STATUS_WORDS = {
    "a": "active", "act": "active", "active": "active",
    "p": "pending", "pend": "pending", "pending": "pending", "op": "pending", "option pending": "pending",
    "pending continue to show": "pending", "pcs": "pending", "active option contract": "pending",
    "s": "sold", "sld": "sold", "sold": "sold", "closed": "sold", "cl": "sold",
}
TYPE_WORDS = {
    "single-family": "single_family", "single family": "single_family", "sfr": "single_family",
    "single family detached": "single_family", "res": "single_family",
    "townhouse/condo": "townhouse_condo", "townhouse": "townhouse_condo", "condo": "townhouse_condo",
    "condominium": "townhouse_condo", "twn/cnd": "townhouse_condo", "high-rise": "townhouse_condo",
    "multi-family": "multi_family", "multi family": "multi_family", "duplex": "multi_family",
    "lots": "lot_land", "lot": "lot_land", "land": "lot_land", "lots/land": "lot_land",
}
INT_FIELDS = ("list_price", "sold_price", "beds", "sqft", "year_built", "days_on_market")

# What app/mls.py's flow types into or ticks on the search page, by the
# field's label as the page names it (normalised).
FILTER_LABELS = {
    "area": ("area", "subdivision", "market area", "neighborhood", "area/subdivision"),
    "zip_codes": ("zip", "zip code", "zip codes", "postal code"),
    "address": ("address", "street address", "subject address"),
    "radius_miles": ("radius", "radius (miles)", "within miles", "distance (miles)"),
    "price_min": ("min price", "price min", "price from", "minimum price"),
    "price_max": ("max price", "price max", "price to", "maximum price"),
    "beds_min": ("min beds", "beds min", "bedrooms min", "beds from"),
    "beds_max": ("max beds", "beds max", "bedrooms max", "beds to"),
    "baths_min": ("min baths", "baths min", "bathrooms min", "baths from"),
    "baths_max": ("max baths", "baths max", "bathrooms max", "baths to"),
    "sqft_min": ("min sqft", "sqft min", "min sq ft", "sqft from"),
    "sqft_max": ("max sqft", "sqft max", "max sq ft", "sqft to"),
    "year_built_min": ("year built min", "min year built", "year built from"),
    "year_built_max": ("year built max", "max year built", "year built to"),
    "date_from": ("date from", "status date from", "from date", "change date from"),
    "date_to": ("date to", "status date to", "to date", "change date to"),
}
STATUS_BOXES = {"active": ("active",), "pending": ("pending", "option pending"), "sold": ("sold", "closed")}
TYPE_BOXES = {"single_family": ("single-family", "single family"), "townhouse_condo": ("townhouse/condo",),
              "multi_family": ("multi-family", "multi family"), "lot_land": ("lots", "lots/land", "land")}

BLOCKED_TEXT = re.compile(r"access denied|unusual activity|too many requests|rate limit|temporarily blocked|"
                          r"your access has been (suspended|restricted)", re.I)
CAPTCHA_TEXT = re.compile(r"verify (that )?you are (a )?human|are you a robot|i'?m not a robot|security check|"
                          r"complete the challenge|press and hold", re.I)
TWO_FACTOR_TEXT = re.compile(r"verification code|one[- ]time (pass)?code|two[- ](factor|step)|authenticator app|"
                             r"enter the code (we|that was) sent", re.I)
LOGIN_URL = re.compile(r"/(login|signin|sign-in|sso|auth|account/login)\b", re.I)
EMPTY_TEXT = re.compile(r"\bno (results|listings|matches|properties)( were)? found\b|\b0 (results|listings|matches)\b|"
                        r"your search returned no", re.I)
AMBIGUOUS_TEXT = re.compile(r"did you mean|matches more than one|more than one (area|property|match)|"
                            r"select (the|an?) (area|property)", re.I)


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower().rstrip(":")


def _int(s: str) -> Optional[int]:
    t = _norm(s).replace("$", "").replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([km])?", t)
    if not m:
        return None
    v = float(m.group(1)) * {"k": 1000, "m": 1_000_000}.get(m.group(2) or "", 1)
    return int(round(v))


def _baths(s: str) -> Optional[float]:
    t = _norm(s)
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", t)
    if m:
        return int(m.group(1)) + 0.5 * int(m.group(2))
    m = re.fullmatch(r"\d+(\.\d+)?", t)
    return float(t) if m else None


def _date(s: str) -> Optional[str]:
    t = _norm(s)
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", t)
    try:
        if m:
            y = int(m.group(3))
            return date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2))).isoformat()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
            return date.fromisoformat(t).isoformat()
    except ValueError:
        return None
    return None


def _value(fld: str, raw: Any) -> Any:
    s = str(raw if raw is not None else "").strip()
    if not s or s in ("-", "—", "n/a", "N/A"):
        return None
    if fld in INT_FIELDS:
        return _int(s)
    if fld == "baths":
        return _baths(s)
    if fld == "distance_miles":
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(mi|miles)?", _norm(s))
        return float(m.group(1)) if m else None
    if fld.endswith("_date"):
        return _date(s)
    if fld == "status":
        return STATUS_WORDS.get(_norm(s))
    if fld == "property_type":
        return TYPE_WORDS.get(_norm(s))
    if fld == "zip_code":
        m = re.search(r"\b(\d{5})\b", s)
        return m.group(1) if m else None
    return re.sub(r"\s+", " ", s)[:200]


def _row(pairs: List[Tuple[str, Any]]) -> Optional[Listing]:
    """Labelled values to a Listing; None without an address. The first
    column that maps to a field wins; unmapped labels are never read."""
    vals: Dict[str, Any] = {}
    for label, raw in pairs:
        fld = COLUMNS.get(_norm(label))
        if fld and fld not in vals:
            vals[fld] = _value(fld, raw)
    if not vals.get("address"):
        return None
    return Listing(**{"status": None, **vals})


def _from_tables(tables: List[Dict[str, Any]]) -> Tuple[List[Listing], int]:
    best: Tuple[int, List[Listing], int] = (0, [], 0)
    for t in tables:
        headers = t.get("headers") or []
        mapped = sum(1 for h in headers if COLUMNS.get(_norm(h)))
        if "address" not in {COLUMNS.get(_norm(h)) for h in headers} or mapped < 2:
            continue
        rows, skipped = [], 0
        for cells in t.get("rows") or []:
            r = _row(list(zip(headers, cells)))
            if r:
                rows.append(r)
            else:
                skipped += 1
        if mapped > best[0]:
            best = (mapped, rows, skipped)
    return best[1], best[2]


def _from_cards(cards: List[List[List[str]]]) -> Tuple[List[Listing], int]:
    rows, skipped = [], 0
    for card in cards:
        pairs = [(p[0], p[1]) for p in card if len(p) == 2]
        if sum(1 for lbl, _ in pairs if COLUMNS.get(_norm(lbl))) < 2:
            continue
        r = _row(pairs)
        if r:
            rows.append(r)
        else:
            skipped += 1
    return rows, skipped


def _from_data(blocks: List[str]) -> Tuple[List[Listing], int]:
    rows, skipped = [], 0
    for b in blocks:
        try:
            v = json.loads(b)
        except ValueError:
            continue
        items = v.get("listings") if isinstance(v, dict) else None
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            r = _row(list(it.items()))
            if r:
                rows.append(r)
            else:
                skipped += 1
    return rows, skipped


def parse(page: Dict[str, Any]) -> Tuple[List[Listing], int]:
    """(rows, rows skipped for having no address). Embedded data first, then
    a table whose headers map, then labelled cards."""
    for found in (_from_data(page.get("data") or []), _from_tables(page.get("tables") or []),
                  _from_cards(page.get("cards") or [])):
        if found[0]:
            return found
    return [], 0


def _choices(page: Dict[str, Any]) -> List[str]:
    for t in page.get("tables") or []:
        if t.get("rows"):
            return [r[0] for r in t["rows"] if r and r[0]][:20]
    for lst in page.get("lists") or []:
        if lst:
            return [x for x in lst if x][:20]
    return []


def classify(page: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """(class, details). Stops are checked before anything is read, so a
    sign-in page that also shows a table is still a sign-in page. A page the
    reader does not recognise is "unknown", never "empty"."""
    sig = page.get("signals") or {}
    text = str(sig.get("text") or "")
    code = page.get("http_status") or 0
    if code in (403, 429) or BLOCKED_TEXT.search(text):
        return "blocked", {"http_status": code}
    if sig.get("captcha_frame") or CAPTCHA_TEXT.search(text):
        return "captcha", {}
    if sig.get("otp_field") or TWO_FACTOR_TEXT.search(text):
        return "two_factor", {}
    if page.get("login") in ("username_only", "submitted") or sig.get("password_field") \
            or LOGIN_URL.search(str(page.get("url") or "")):
        return "login", {}
    if AMBIGUOUS_TEXT.search(text):
        return "ambiguous", {"choices": _choices(page)}
    rows, skipped = parse(page)
    if rows:
        return "results", {"rows": rows, "skipped": skipped}
    if EMPTY_TEXT.search(text):
        return "empty", {}
    if search_fields(str(page.get("snapshot") or "")):
        return "search", {}
    return "unknown", {}


SNAP_LINE = re.compile(r'\[(e\d+)\] (\w+) "([^"]*)"')


def controls(snapshot: str) -> List[Tuple[str, str, str]]:
    """(ref, role, normalised name) for each element in a worker snapshot."""
    return [(m.group(1), m.group(2), _norm(m.group(3))) for m in SNAP_LINE.finditer(snapshot)]


def search_fields(snapshot: str) -> Dict[str, str]:
    """Filter name → element ref for the search fields this page has."""
    found: Dict[str, str] = {}
    for ref, role, name in controls(snapshot):
        if role not in ("textbox", "searchbox", "combobox", "spinbutton"):
            continue
        for fld, labels in FILTER_LABELS.items():
            if name in labels and fld not in found:
                found[fld] = ref
    return found if len(found) >= 2 else {}


def boxes(snapshot: str, table: Dict[str, Tuple[str, ...]]) -> Dict[str, str]:
    """Value → checkbox ref, for STATUS_BOXES or TYPE_BOXES."""
    found: Dict[str, str] = {}
    for ref, role, name in controls(snapshot):
        if role != "checkbox":
            continue
        for value, labels in table.items():
            if name in labels and value not in found:
                found[value] = ref
    return found


# ── the page flow ────────────────────────────────────────────────────────────

SEARCH_BUTTON_NAMES = ("search", "search now", "show results", "view results", "results")
NEXT_NAMES = ("next", "next page", "next ›", "next »", "›", "»")
STOP_SAY = {
    "login": "Matrix is asking you to sign in. Tap Take over, sign in yourself, then tap I'm done and ask again.",
    "captcha": "Matrix is showing a human check. Tap Take over and complete it yourself, then ask again.",
    "two_factor": "Matrix is asking for a verification code. Tap Take over and enter it yourself, then ask again.",
    "taken_over": "You're using the browser yourself right now. Hand it back, then ask again.",
    "allow_reading": "Allow Jarvis to keep reading {site} (the card in the browser window), then ask again.",
}
_sleep = time.sleep
_uniform = random.uniform


class Stop(Exception):
    def __init__(self, result: Result):
        super().__init__(result.state)
        self.result = result


def _fmt(fld: str, v: Any) -> str:
    if fld.startswith("date"):
        d = date.fromisoformat(v)
        return f"{d.month:02d}/{d.day:02d}/{d.year}"
    if fld == "zip_codes":
        return ", ".join(v)
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _checked(snapshot: str, ref: str) -> bool:
    m = re.search(r"\[" + re.escape(ref) + r"\] checkbox \"[^\"]*\"([^\n]*)", snapshot)
    return bool(m and "[checked]" in m.group(1))


def _named(snapshot: str, roles: Tuple[str, ...], names: Tuple[str, ...]) -> Optional[str]:
    return next((ref for ref, role, name in controls(snapshot) if role in roles and name in names), None)


def read(q, budget, ctx: Dict[str, Any], gate=None) -> Result:
    """One search on Matrix through the person's own browser. Every page load
    draws on the budget and waits a random pause first; every gesture goes
    through browser.gate() as the person, so the ledger, Take over and the
    read allowance all apply. Never retries: any stop ends the run."""
    from . import browser  # noqa: PLC0415
    g = gate or browser.gate
    person, s, gctx = ctx["person"], ctx["settings"], ctx["gate_ctx"]

    def call(op: str, args: Dict[str, Any], loads: bool) -> Dict[str, Any]:
        if loads:
            budget.draw()
            lo, hi = s["pause_seconds"]
            _sleep(_uniform(lo, hi))
        st, body = g(op, args, gctx, person)
        if st == 200:
            return body
        err = body.get("error")
        if err == "taken_over":
            raise Stop(Result("needs_person", stop_kind="taken_over", detail=STOP_SAY["taken_over"]))
        if err == "approval_required" and body.get("reading"):
            site = body.get("site") or s["site"]
            raise Stop(Result("needs_person", stop_kind="allow_reading",
                              detail=STOP_SAY["allow_reading"].format(site=site)))
        if err == "browser_unavailable":
            raise Stop(Result("failed", stop_kind="browser_unavailable", detail="The browser isn't running right now."))
        if st == 502:
            return body
        raise Stop(Result("failed", detail=f"The browser refused a step of the search ({err or st})."))

    def look() -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        page = call("tables", {"snapshot": True}, False)
        cls, det = classify(page)
        if cls in ("login", "captcha", "two_factor"):
            site = browser.site_key(page.get("url") or "") or s["site"]
            browser.mark_site(person, site, "needs_signin")
            browser._ask_person(person, STOP_SAY[cls], gctx, site, f"mls_{cls}")
            raise Stop(Result("needs_person", stop_kind=cls, detail=STOP_SAY[cls]))
        if cls == "blocked":
            raise Stop(Result("blocked", stop_kind="blocked",
                              detail="Matrix refused or slowed the search. Searches stop for a while; nothing was retried."))
        if cls == "ambiguous":
            raise Stop(Result("ambiguous", choices=det.get("choices") or [],
                              detail="The area or address matches more than one thing on Matrix."))
        if cls == "unknown":
            raise Stop(Result("unreadable", detail="Matrix showed a page the reader doesn't recognise; nothing was guessed."))
        return cls, page, det

    try:
        call("open", {"url": s["start_url"]}, True)
        cls, page, _ = look()
        if cls != "search":
            raise Stop(Result("unreadable", detail="The Matrix start page isn't the search form."))
        snap = page.get("snapshot") or ""
        for fld in FILTER_LABELS:
            v = getattr(q, fld, None)
            if v in (None, (), ""):
                continue
            ref = search_fields(snap).get(fld)
            if not ref:
                raise Stop(Result("unreadable", detail=f"The search page has no field for {fld.replace('_', ' ')}."))
            snap = call("type", {"element": ref, "text": _fmt(fld, v)}, False).get("snapshot") or snap
        for wanted, table, label in ((set(q.statuses), STATUS_BOXES, "status"),
                                     (set(q.property_types), TYPE_BOXES, "property type")):
            if not wanted:
                continue
            refs = boxes(snap, table)
            missing = wanted - set(refs)
            if missing:
                raise Stop(Result("unreadable", detail=f"The search page has no {label} box for {sorted(missing)[0]}."))
            for value, ref in refs.items():
                if (value in wanted) != _checked(snap, ref):
                    snap = call("click", {"element": ref}, False).get("snapshot") or snap
        button = _named(snap, ("button",), SEARCH_BUTTON_NAMES)
        if not button:
            raise Stop(Result("unreadable", detail="The search page has no Search button."))
        call("click", {"element": button}, True)
        cls, page, det = look()
        rows: List[Listing] = list(det.get("rows") or [])
        truncated = False
        while cls == "results":
            nxt = _named(page.get("snapshot") or "", ("link", "button"), NEXT_NAMES)
            if not nxt:
                break
            if len(rows) >= q.max_results or budget.used >= budget.limit:
                truncated = True
                break
            call("click", {"element": nxt}, True)
            cls, page, det = look()
            rows += det.get("rows") or []
        if cls == "search":
            raise Stop(Result("unreadable", detail="Matrix went back to the search form instead of showing results."))
        return Result("done", listings=rows, truncated=truncated)
    except Stop as stop:
        return stop.result
