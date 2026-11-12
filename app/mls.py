"""MLS search: property searches on HAR MLS Matrix, run through the asking
person's own signed-in browser (docs/plans/browser-har-comps.md).

There is no MLS switch. A person may search exactly when available_for()
finds every condition in plan section 7 true, walked in that order; the first
that fails is the answer and bumps mls_refused_<why>.

A search is a run: one mls_runs row, which is also the only counter the daily
caps read. The reader (app/mls_matrix.py) loads pages; everything that can be
checked without the site, the filters, the row cap and the summary, is
re-done here on what it returns.
"""
import json
import logging
import re
import statistics
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from . import accounts
from . import actor_refs
from . import auth
from . import broker
from . import browser
from . import config
from . import mls_limits
from . import sentinel
from . import threads as threads_mod

log = logging.getLogger("app.mls")

UNAVAILABLE = {
    "browser_off": "The web browser isn't on for this team.",
    "not_headed": "MLS search needs the browser in its live mode, so you can sign in to Matrix yourself.",
    "config_invalid": "The MLS search settings for this team aren't valid. Ask your operator.",
    "not_configured": "MLS search isn't set up for this team yet (no Matrix start page). Ask your operator.",
    "not_a_person": "MLS search runs for a signed-in person only.",
    "browser_feature_off": "Your web browser is switched off. Ask your admin.",
}


def _tc():
    if str(config.SHARED) not in sys.path:
        sys.path.insert(0, str(config.SHARED))
    import tenant_config as tc  # noqa: PLC0415
    return tc


def settings() -> Tuple[Dict[str, Any], bool]:
    """(app.mls with defaults, valid). An unreadable or invalid block is
    announced and reads as unavailable; nothing is clamped."""
    try:
        raw = _tc().get("app.mls", None)
    except Exception as exc:
        threads_mod.bump_counter("mls_config_unreadable")
        log.error("mls: config unreadable (%s); MLS search is unavailable", type(exc).__name__)
        return dict(mls_limits.DEFAULTS), False
    out, bad = mls_limits.check(raw)
    if bad:
        threads_mod.bump_counter("mls_config_invalid")
        log.error("mls: app.mls refused (%d problems); MLS search is unavailable", len(bad))
        return out, False
    return out, True


def browser_mode() -> str:
    try:
        mode = (_tc().get("app.browser", {}) or {}).get("mode")
    except Exception as exc:
        threads_mod.bump_counter("mls_config_unreadable")
        log.error("mls: app.browser unreadable (%s); treated as headless", type(exc).__name__)
        return "headless"
    return mode if mode in ("headless", "headed") else "headless"


def available_for(person: Optional[str], *, count: bool = True) -> Optional[str]:
    """None when this person may search; otherwise the first condition that is off."""
    why = _first_off(person)
    if why and count:
        threads_mod.bump_counter(f"mls_refused_{why}")
    return why


def _first_off(person: Optional[str]) -> Optional[str]:
    if not browser.enabled():
        return "browser_off"
    if browser_mode() != "headed":
        return "not_headed"
    s, valid = settings()
    if not valid:
        return "config_invalid"
    if not s["start_url"]:
        return "not_configured"
    if not person:
        return "not_a_person"
    if not accounts.feature_on(person, "browser"):
        return "browser_feature_off"
    return None


# ── shapes ───────────────────────────────────────────────────────────────────

STATUSES = ("active", "pending", "sold")
PROPERTY_TYPES = ("single_family", "townhouse_condo", "multi_family", "lot_land")
HARD_MAX_RESULTS = 100
DEFAULT_RESULTS = 25
DEFAULT_RADIUS = 1.0
MAX_MONTHS_BACK = 36
STALE_RUN = timedelta(minutes=10)
ZIP = re.compile(r"\d{5}")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
RANGES = {
    "price": (0, 100_000_000, int),
    "beds": (0, 12, int),
    "baths": (0, 12, float),
    "sqft": (200, 20000, int),
    "year_built": (1800, None, int),
}
PARAMS = {"statuses", "area", "zip_codes", "address", "radius_miles", "property_types", "date_from", "date_to",
          "subject_sqft", "max_results", *(f"{k}_{e}" for k in RANGES for e in ("min", "max"))}


class BadRequest(ValueError):
    pass


@dataclass(frozen=True)
class Query:
    statuses: Tuple[str, ...] = ("active",)
    area: Optional[str] = None
    zip_codes: Tuple[str, ...] = ()
    address: Optional[str] = None
    radius_miles: Optional[float] = None
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    beds_min: Optional[int] = None
    beds_max: Optional[int] = None
    baths_min: Optional[float] = None
    baths_max: Optional[float] = None
    sqft_min: Optional[int] = None
    sqft_max: Optional[int] = None
    year_built_min: Optional[int] = None
    year_built_max: Optional[int] = None
    property_types: Tuple[str, ...] = ()
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    subject_sqft: Optional[int] = None
    max_results: int = DEFAULT_RESULTS

    def given(self) -> Dict[str, Any]:
        """Only the filters the person gave, for the run row and the card."""
        d = asdict(self)
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()
                if v not in (None, (), []) and not (k == "max_results" and v == DEFAULT_RESULTS)}


@dataclass(frozen=True)
class Listing:
    address: str
    status: Optional[str]
    list_price: Optional[int] = None
    sold_price: Optional[int] = None
    list_date: Optional[str] = None
    pending_date: Optional[str] = None
    sold_date: Optional[str] = None
    beds: Optional[int] = None
    baths: Optional[float] = None
    sqft: Optional[int] = None
    year_built: Optional[int] = None
    property_type: Optional[str] = None
    area: Optional[str] = None
    zip_code: Optional[str] = None
    days_on_market: Optional[int] = None
    mls_number: Optional[str] = None
    distance_miles: Optional[float] = None

    def price(self) -> Optional[int]:
        return self.sold_price if self.status == "sold" else self.list_price

    def status_date(self) -> Optional[str]:
        return {"sold": self.sold_date, "pending": self.pending_date, "active": self.list_date}.get(self.status or "")


@dataclass
class Result:
    state: str                        # done | needs_person | blocked | unreadable | ambiguous | failed
    listings: List[Listing] = field(default_factory=list)
    page_loads: int = 0
    truncated: bool = False
    detail: str = ""
    stop_kind: Optional[str] = None   # login | captcha | two_factor | blocked
    choices: List[str] = field(default_factory=list)


def _num(name: str, v: Any, lo: float, hi: Optional[float], kind) -> Any:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (kind is int and v != int(v)):
        raise BadRequest(f"{name} must be a {'whole ' if kind is int else ''}number")
    if v < lo or (hi is not None and v > hi):
        raise BadRequest(f"{name} must be {lo} to {hi}" if hi is not None else f"{name} must be at least {lo}")
    return kind(v)


def _choices(name: str, v: Any, allowed: Tuple[str, ...]) -> Tuple[str, ...]:
    if v is None:
        return ()
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise BadRequest(f"{name} must be a list")
    if len(set(v)) != len(v):
        raise BadRequest(f"{name} has a repeat")
    bad = [x for x in v if x not in allowed]
    if bad:
        raise BadRequest(f"{name} must be from {', '.join(allowed)}")
    return tuple(v)


def _text(name: str, v: Any, lo: int, hi: int) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str) or not lo <= len(v.strip()) <= hi or any(ord(c) < 32 for c in v):
        raise BadRequest(f"{name} must be {lo} to {hi} characters on one line")
    return " ".join(v.split())


def parse_query(params: Dict[str, Any], today: date, ceiling: int = HARD_MAX_RESULTS) -> Query:
    """The tool's parameters as a Query, or BadRequest naming the first problem.
    `ceiling` is the tenant's max_results; the hard limit is 100 either way."""
    unknown = set(params) - PARAMS
    if unknown:
        raise BadRequest(f"unknown parameter {sorted(unknown)[0]}")
    p = params
    statuses = _choices("statuses", p.get("statuses"), STATUSES) or ("active",)
    area = _text("area", p.get("area"), 2, 80)
    address = _text("address", p.get("address"), 5, 120)
    zips = p.get("zip_codes")
    if zips is not None:
        if not isinstance(zips, list) or not all(isinstance(z, str) and ZIP.fullmatch(z) for z in zips) \
                or not 1 <= len(zips) <= 10 or len(set(zips)) != len(zips):
            raise BadRequest("zip_codes must be 1 to 10 different five-digit codes")
    zips = tuple(zips or ())
    if not (area or zips or address):
        raise BadRequest("give a place: an area, zip codes or an address")
    radius = _num("radius_miles", p.get("radius_miles"), 0.25, 5, float)
    if radius is not None and not address:
        raise BadRequest("radius_miles needs an address")
    if address and radius is None:
        radius = DEFAULT_RADIUS
    vals: Dict[str, Any] = {}
    for k, (lo, hi, kind) in RANGES.items():
        top = today.year + 1 if k == "year_built" else hi
        a = _num(f"{k}_min", p.get(f"{k}_min"), lo, top, kind)
        b = _num(f"{k}_max", p.get(f"{k}_max"), lo, top, kind)
        if a is not None and b is not None and a > b:
            raise BadRequest(f"{k}_min is above {k}_max")
        vals[f"{k}_min"], vals[f"{k}_max"] = a, b
    dates = []
    for k in ("date_from", "date_to"):
        v = p.get(k)
        if v is not None:
            try:
                d = date.fromisoformat(v) if isinstance(v, str) and DAY.fullmatch(v) else None
            except ValueError:
                d = None
            if d is None:
                raise BadRequest(f"{k} must be a date like 2026-05-01")
            if d > today:
                raise BadRequest(f"{k} can't be in the future")
            if d < _months_back(today, MAX_MONTHS_BACK):
                raise BadRequest(f"{k} can't be more than {MAX_MONTHS_BACK} months back")
        dates.append(v)
    if dates[0] and dates[1] and dates[0] > dates[1]:
        raise BadRequest("date_from is after date_to")
    subject = _num("subject_sqft", p.get("subject_sqft"), 200, 20000, int)
    top = min(ceiling, HARD_MAX_RESULTS)
    n = _num("max_results", p.get("max_results"), 1, HARD_MAX_RESULTS, int)
    n = min(n if n is not None else DEFAULT_RESULTS, top)
    return Query(statuses=statuses, area=area, zip_codes=zips, address=address, radius_miles=radius,
                 property_types=_choices("property_types", p.get("property_types"), PROPERTY_TYPES),
                 date_from=dates[0], date_to=dates[1], subject_sqft=subject, max_results=n, **vals)


def _months_back(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    return date(y, m + 1, min(d.day, 28))


# ── the re-check, the row cap and the summary ───────────────────────────────

def recheck(q: Query, rows: List[Listing]) -> Tuple[List[Listing], int, int]:
    """(kept, dropped, unverified). A row is dropped when a field it has
    fails a filter; kept but unverified when it lacks a field a filter needs.
    The area is the site's own filter and can't be re-checked by name."""
    kept: List[Listing] = []
    dropped = unverified = 0
    for r in rows:
        fails, missing = _row_check(q, r)
        if fails:
            dropped += 1
            continue
        if missing:
            unverified += 1
        kept.append(r)
    return kept, dropped, unverified


def _row_check(q: Query, r: Listing) -> Tuple[bool, bool]:
    missing = False

    def within(v, lo, hi) -> Optional[bool]:
        nonlocal missing
        if lo is None and hi is None:
            return True
        if v is None:
            missing = True
            return True
        return (lo is None or v >= lo) and (hi is None or v <= hi)

    if r.status is None:
        missing = True
    elif r.status not in q.statuses:
        return True, missing
    if q.zip_codes:
        if r.zip_code is None:
            missing = True
        elif r.zip_code not in q.zip_codes:
            return True, missing
    if q.property_types:
        if r.property_type is None:
            missing = True
        elif r.property_type not in q.property_types:
            return True, missing
    checks = [
        within(r.price(), q.price_min, q.price_max),
        within(r.beds, q.beds_min, q.beds_max),
        within(r.baths, q.baths_min, q.baths_max),
        within(r.sqft, q.sqft_min, q.sqft_max),
        within(r.year_built, q.year_built_min, q.year_built_max),
        within(r.status_date(), q.date_from, q.date_to),
        within(r.distance_miles, None, q.radius_miles) if q.radius_miles is not None else True,
    ]
    return (not all(checks)), missing


def per_sqft(r: Listing) -> Optional[int]:
    p = r.price()
    return round(p / r.sqft) if p and r.sqft else None


def _quartiles(vals: List[float]) -> Tuple[float, float]:
    if len(vals) == 1:
        return vals[0], vals[0]
    qs = statistics.quantiles(sorted(vals), n=4, method="inclusive")
    return qs[0], qs[2]


def summarize(q: Query, rows: List[Listing]) -> Dict[str, Any]:
    by_status = {s: sum(1 for r in rows if r.status == s) for s in STATUSES if any(r.status == s for r in rows)}
    prices = [r.price() for r in rows if r.price()]
    psf = [per_sqft(r) for r in rows if per_sqft(r)]
    dom = [r.days_on_market for r in rows if r.days_on_market is not None]
    out: Dict[str, Any] = {"by_status": by_status}
    if prices:
        lo, hi = _quartiles(prices)
        out.update(median_price=round(statistics.median(prices)), price_low=round(lo), price_high=round(hi))
    if psf:
        out["median_price_per_sqft"] = round(statistics.median(psf))
    if dom:
        out["median_days_on_market"] = round(statistics.median(dom))
    statuses = set(by_status)
    kind = ("sold prices" if statuses == {"sold"} else "list prices" if "sold" not in statuses
            else "sold prices for sold listings, list prices otherwise")
    words = ", ".join(f"{n} {s}" for s, n in by_status.items()) or "no"
    note = f"{words} listing{'s' if len(rows) != 1 else ''}. Prices are {kind}."
    comps = statuses == {"sold"} and q.address and q.subject_sqft
    if comps:
        if len(psf) >= 3:
            lo, hi = _quartiles(psf)
            out.update(suggested_low=round(lo * q.subject_sqft, -3), suggested_high=round(hi * q.subject_sqft, -3),
                       price_per_sqft_low=round(lo), price_per_sqft_high=round(hi))
            note += f" Suggested range from {len(psf)} sales. Not an appraisal."
        else:
            note += " Too few sales with a size to suggest a range."
    out["note"] = note
    return out


def listing_out(r: Listing) -> Dict[str, Any]:
    return {**asdict(r), "price_per_sqft": per_sqft(r)}


# ── runs, caps and the budget ────────────────────────────────────────────────

class BudgetExhausted(RuntimeError):
    pass


class Budget:
    """Page loads this run may make. The reader draws before every load; the
    draw after the last allowed one raises, so a reader can't overrun it."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0

    def draw(self) -> None:
        if self.used >= self.limit:
            threads_mod.bump_counter("mls_budget_exhausted")
            raise BudgetExhausted(f"page budget of {self.limit} used")
        self.used += 1


Reader = Callable[[Query, Budget, Dict[str, Any]], Result]
_start_lock = threading.Lock()


def _db():
    conn = accounts._ensure()
    conn.execute("""CREATE TABLE IF NOT EXISTS mls_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, person_key TEXT NOT NULL, thread_id TEXT NOT NULL,
        query_json TEXT NOT NULL, state TEXT NOT NULL, stop_kind TEXT,
        page_loads INTEGER NOT NULL DEFAULT 0, result_count INTEGER NOT NULL DEFAULT 0, result_json TEXT,
        started_at TEXT NOT NULL, finished_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS mls_runs_person ON mls_runs (person_key, started_at)")
    return conn


def _now() -> datetime:
    return sentinel.utcnow()


def _zone():
    from zoneinfo import ZoneInfo  # noqa: PLC0415
    try:
        return ZoneInfo(_tc().timezone_name())
    except Exception as exc:
        threads_mod.bump_counter("mls_timezone_fallback")
        log.warning("mls: no usable tenant timezone (%s); daily caps use UTC", type(exc).__name__)
        return timezone.utc


def day_start(now: datetime) -> datetime:
    """The start of the tenant's local day containing `now`, in UTC."""
    local = now.astimezone(_zone())
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


class Refused(Exception):
    def __init__(self, status: int, error: str, **extra):
        super().__init__(error)
        self.status, self.error, self.extra = status, error, extra


def _fail_stale(conn, now: datetime) -> None:
    cur = conn.execute("UPDATE mls_runs SET state = 'failed', finished_at = ? WHERE state = 'running' AND started_at < ?",
                       (now.isoformat(), (now - STALE_RUN).isoformat()))
    if cur.rowcount:
        threads_mod.bump_counter("mls_stale_failed", cur.rowcount)
        log.error("mls: %d run(s) still running after %s marked failed", cur.rowcount, STALE_RUN)


def _clear_expired(conn, now: datetime, keep_days: int) -> None:
    cur = conn.execute("UPDATE mls_runs SET result_json = NULL WHERE result_json IS NOT NULL AND finished_at < ?",
                       ((now - timedelta(days=keep_days)).isoformat(),))
    if cur.rowcount:
        threads_mod.bump_counter("mls_results_cleared", cur.rowcount)
        log.info("mls: cleared the results of %d run(s) older than %d days", cur.rowcount, keep_days)


def start_run(person: str, thread_id: str, q: Query, s: Dict[str, Any]) -> Tuple[int, int]:
    """Check the caps and create the run row: (run id, page budget). Refused
    names the cap that stopped it. The row counts against the day from now,
    however the run ends."""
    now = _now()
    with _start_lock:
        conn = _db()
        with conn:
            _fail_stale(conn, now)
            _clear_expired(conn, now, s["keep_days"])
        if conn.execute("SELECT 1 FROM mls_runs WHERE state = 'running' LIMIT 1").fetchone():
            threads_mod.bump_counter("mls_refused_busy")
            raise Refused(409, "busy")
        blocked = conn.execute("SELECT finished_at FROM mls_runs WHERE person_key = ? AND state = 'blocked' "
                               "ORDER BY id DESC LIMIT 1", (person,)).fetchone()
        if blocked and blocked["finished_at"]:
            until = datetime.fromisoformat(blocked["finished_at"]) + timedelta(hours=s["cooldown_hours_after_block"])
            if until > now:
                threads_mod.bump_counter("mls_refused_cooldown")
                raise Refused(409, "cooling_off", until=until.isoformat())
        start = day_start(now)
        runs, pages = conn.execute("SELECT COUNT(*), COALESCE(SUM(page_loads), 0) FROM mls_runs "
                                   "WHERE person_key = ? AND started_at >= ?", (person, start.isoformat())).fetchone()
        resets = (start.astimezone(_zone()) + timedelta(days=1)).replace(hour=0).astimezone(timezone.utc)
        if runs >= s["max_requests_per_person_per_day"] or pages >= s["max_page_loads_per_person_per_day"]:
            threads_mod.bump_counter("mls_refused_daily_cap")
            raise Refused(429, "daily_cap", resets_at=resets.isoformat())
        budget = min(s["max_page_loads_per_request"], s["max_page_loads_per_person_per_day"] - pages)
        with conn:
            cur = conn.execute("INSERT INTO mls_runs (person_key, thread_id, query_json, state, started_at) "
                               "VALUES (?, ?, ?, 'running', ?)",
                               (person, thread_id, json.dumps(q.given(), sort_keys=True), now.isoformat()))
    threads_mod.bump_counter("mls_started")
    return cur.lastrowid, budget


def finish_run(run_id: int, q: Query, res: Result) -> Dict[str, Any]:
    """Re-check, cap and summarise what the reader returned, store it on the
    run, and return the tool's reply."""
    out: Dict[str, Any] = {"run_id": run_id, "state": res.state, "page_loads": res.page_loads,
                           "query": q.given()}
    result_json = None
    count = 0
    if res.state == "done":
        kept, dropped, unverified = recheck(q, res.listings)
        truncated = res.truncated or len(kept) > q.max_results
        kept = kept[:q.max_results]
        count = len(kept)
        if dropped:
            threads_mod.bump_counter("mls_rows_dropped", dropped)
        if unverified:
            threads_mod.bump_counter("mls_rows_unverified", unverified)
        body = {"count": count, "truncated": truncated, "dropped": dropped, "unverified": unverified,
                "summary": summarize(q, kept), "listings": [listing_out(r) for r in kept]}
        out.update(ok=True, **body)
        result_json = json.dumps({"query": q.given(), **body})
    else:
        out.update(ok=False, detail=res.detail)
        if res.stop_kind:
            out["kind"] = res.stop_kind
        if res.choices:
            out["choices"] = [str(c)[:120] for c in res.choices[:20]]
        result_json = json.dumps({k: out[k] for k in ("detail", "kind", "choices") if k in out})
    conn = _db()
    with conn:
        conn.execute("UPDATE mls_runs SET state = ?, stop_kind = ?, page_loads = ?, result_count = ?, result_json = ?, "
                     "finished_at = ? WHERE id = ?",
                     (res.state, res.stop_kind, res.page_loads, count, result_json, _now().isoformat(), run_id))
    threads_mod.bump_counter(f"mls_{res.state}")
    log.info("mls: run %d ended %s after %d page loads, %d rows", run_id, res.state, res.page_loads, count)
    return out


def run_search(person: str, thread_id: str, q: Query, reader: Reader, s: Dict[str, Any],
               ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One search, start to finish, in the calling thread."""
    run_id, limit = start_run(person, thread_id, q, s)
    return execute(run_id, limit, person, q, reader, s, ctx)


def execute(run_id: int, limit: int, person: str, q: Query, reader: Reader, s: Dict[str, Any],
            ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    budget = Budget(limit)
    try:
        res = reader(q, budget, {**(ctx or {}), "person": person, "run_id": run_id, "settings": s})
    except BudgetExhausted:
        res = Result("failed", detail="The page limit for one search was reached before the results were read.")
    except Exception as exc:
        threads_mod.bump_counter("mls_reader_error")
        log.error("mls: run %d reader raised %s", run_id, type(exc).__name__)
        res = Result("failed", detail="The search stopped on an error.")
    res.page_loads = budget.used
    return finish_run(run_id, q, res)


def run_row(run_id: int, person: str) -> Optional[Dict[str, Any]]:
    """The run, only for the person who started it."""
    row = _db().execute("SELECT * FROM mls_runs WHERE id = ? AND person_key = ?", (run_id, person)).fetchone()
    return dict(row) if row else None


def reply(run: Dict[str, Any]) -> Dict[str, Any]:
    """The tool's answer for a stored run."""
    out: Dict[str, Any] = {"run_id": run["id"], "state": run["state"], "page_loads": run["page_loads"],
                           "query": json.loads(run["query_json"] or "{}")}
    if run["state"] == "running":
        return {**out, "ok": True}
    body = json.loads(run["result_json"]) if run["result_json"] else None
    if run["state"] == "done":
        if body is None:
            return {**out, "ok": False, "expired": True, "detail": "These results have expired."}
        return {**out, "ok": True, **{k: v for k, v in body.items() if k != "query"}}
    return {**out, "ok": False, **(body or {})}


# ── the broker door for the Ask tools ────────────────────────────────────────

broker_router = APIRouter(prefix="/api/broker")
router = APIRouter(prefix="/api/mls")
WAIT_S = 100.0
_events: Dict[int, threading.Event] = {}
_events_lock = threading.Lock()
# A finished run's HTTP status and error, so the agent's tool tells the
# person the right thing (plan section 8).
STATE_HTTP = {"needs_person": (409, "needs_person"), "ambiguous": (409, "ambiguous"),
              "unreadable": (502, "unreadable"), "blocked": (409, "blocked"), "failed": (502, "failed")}


def _err(status: int, error: str, detail: str = "", **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail, **extra})


def _today() -> date:
    return _now().astimezone(_zone()).date()


def _event(run_id: int) -> threading.Event:
    with _events_lock:
        return _events.setdefault(run_id, threading.Event())


def _background(run_id: int, limit: int, person: str, q: Query, s: Dict[str, Any], gctx: Dict[str, str]) -> None:
    from . import mls_matrix  # noqa: PLC0415
    try:
        execute(run_id, limit, person, q, mls_matrix.read, s, {"gate_ctx": gctx})
    except Exception as exc:
        threads_mod.bump_counter("mls_run_crashed")
        log.error("mls: run %d crashed (%s)", run_id, type(exc).__name__)
        conn = _db()
        with conn:
            conn.execute("UPDATE mls_runs SET state = 'failed', finished_at = ? WHERE id = ? AND state = 'running'",
                         (_now().isoformat(), run_id))
    finally:
        _event(run_id).set()


def _answer(run_id: int, person: str, wait: float):
    ev = _event(run_id)
    run = run_row(run_id, person)
    if run and run["state"] == "running":
        ev.wait(wait)
        run = run_row(run_id, person)
    if run is None:
        return _err(404, "not_found", "No such search for this person.")
    if run["state"] != "running":
        with _events_lock:
            _events.pop(run_id, None)
    body = reply(run)
    if run["state"] in STATE_HTTP:
        status, error = STATE_HTTP[run["state"]]
        return JSONResponse(status_code=status, content={"error": error, **body})
    return body


@broker_router.post("/mls")
async def post_broker_mls(request: Request):
    broker._authorize(request)
    body = await broker._json_body(request)
    entry = broker._turn_person(body.get("ref"))
    if entry is None:
        return _err(403, "no_turn", "No running chat turn.")
    person = str(entry["person_key"])
    op = body.get("op")
    if op not in ("search", "result"):
        return _err(400, "bad_request", "op must be search or result.")
    why = available_for(person)
    if why:
        return _err(403, "unavailable", UNAVAILABLE[why], why=why)
    thread_id = str(entry.get("thread_id") or "")
    thread = threads_mod.get_thread(thread_id) if thread_id else None
    if not thread or thread.get("direct_for") != person:
        threads_mod.bump_counter("mls_refused_channel")
        return _err(403, "direct_only", "MLS results are shown only in the person's own direct line.")
    from starlette.concurrency import run_in_threadpool  # noqa: PLC0415
    if op == "result":
        rid = body.get("run_id")
        if isinstance(rid, bool) or not isinstance(rid, int):
            return _err(400, "bad_request", "run_id must be a whole number.")
        if run_row(rid, person):
            actor_refs.request_action(body["ref"], f"mls:{rid}")
        return await run_in_threadpool(_answer, rid, person, WAIT_S)
    s, _ = settings()
    params = {k: v for k, v in body.items() if k not in ("ref", "op")}
    try:
        q = parse_query(params, _today(), s["max_results"])
    except BadRequest as exc:
        threads_mod.bump_counter("mls_refused_bad_request")
        return _err(400, "bad_request", str(exc))
    if browser.taken_over(person):
        threads_mod.bump_counter("mls_refused_taken_over")
        return _err(409, "taken_over", "They're using the browser themselves right now.")
    try:
        run_id, limit = start_run(person, thread_id, q, s)
    except Refused as r:
        return _err(r.status, r.error, **r.extra)
    actor_refs.request_action(body["ref"], f"mls:{run_id}")
    ctx = broker._context(request)
    gctx = {"run": f"mls-{run_id}", "session": ctx.get("session", ""), "task": ctx.get("task", ""),
            "skill": "jarvis", "source": f"chat_thread:{thread_id}" if broker.CONTEXT_VALUE.match(thread_id) else ""}
    _event(run_id)
    threading.Thread(target=_background, args=(run_id, limit, person, q, s, gctx), daemon=True,
                     name=f"mls-run-{run_id}").start()
    return await run_in_threadpool(_answer, run_id, person, WAIT_S)


# ── the results card ─────────────────────────────────────────────────────────

@router.get("/runs/{run_id}")
def get_run(run_id: int, sess: dict = Depends(auth.current_session)):
    """One search's card, for the person who ran it. Anyone else, owner and
    operator included, gets the same 404 as a run that does not exist."""
    person = None if auth.is_operator(sess) else auth.person_of(sess)
    run = run_row(run_id, person) if person else None
    if run is None:
        threads_mod.bump_counter("mls_card_not_found")
        raise HTTPException(status_code=404, detail="No such search.")
    s, _ = settings()
    conn = _db()
    with conn:
        _clear_expired(conn, _now(), s["keep_days"])
    run = run_row(run_id, person)
    return {**reply(run), "started_at": run["started_at"], "finished_at": run["finished_at"]}
