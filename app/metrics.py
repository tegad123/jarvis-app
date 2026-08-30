"""The spine under the numbers — per-tenant counters with a day, and samples.

WHY THIS EXISTS. Slide 18 quotes numbers ("<2 min response", "zero missed
deadlines", "fewer leads go cold") that nothing measured. Prompts 3–5 bumped
counters as they built, but a bare monotonic total cannot say "this month" or
"7 days vs 30", and nothing recorded a LATENCY at all. This gives the existing
bumps a period and adds measurements, so the results slide is reported, not
asserted, and the every-two-months health check has an instrument.

TWO TABLES, ONE DATABASE. Both live in the tenant's own threads.db beside the
`counters` table they extend — HERMES_HOME decides which file, which is what
makes them per-tenant. `counter_days` is the same name bumped into a daily
bucket; `measurements` is a sample with a timestamp and an internal ref.

FIRE-AND-FORGET, LOUD ONCE. A metrics failure must never fail the operation it
was measuring, so nothing here raises. But a metrics store that is silently
unreachable produces a results slide built on zeros, so the FIRST failure in a
process announces itself on the feed; the rest of that run stays quiet.

THE MASKING CONTRACT HOLDS HERE TOO. `ref` is an internal id — a message id, a
task id — never an address. A sample cannot carry a name or a mailbox, and the
summary this feeds aggregates; the monthly feed line shows counts and medians.
"""

import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from . import config, threads

DEFAULT_RETENTION_DAYS = 180

_DDL = """
CREATE TABLE IF NOT EXISTS counter_days (
    name  TEXT NOT NULL,
    day   TEXT NOT NULL,          -- YYYY-MM-DD, tenant-local
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (name, day)
);
CREATE TABLE IF NOT EXISTS measurements (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL,
    value REAL NOT NULL,
    ts    TEXT NOT NULL,          -- UTC iso
    day   TEXT NOT NULL,          -- YYYY-MM-DD, tenant-local
    ref   TEXT                    -- an INTERNAL id, never an address
);
CREATE INDEX IF NOT EXISTS idx_measurements_name_day ON measurements(name, day);
CREATE INDEX IF NOT EXISTS idx_counter_days_day ON counter_days(day);
"""

_ready_for = None        # id() of the connection the DDL last ran on
_announced_failure = False
_pruned = False


def _tz():
    try:
        p = str(config.SHARED)
        if p not in sys.path:
            sys.path.insert(0, p)
        import tenant_config as tc
        from zoneinfo import ZoneInfo
        name = (tc.get("client.timezone", "") or "").strip()
        return ZoneInfo(name) if name else timezone.utc
    except Exception:
        return timezone.utc


def today() -> str:
    return datetime.now(_tz()).date().isoformat()


def retention_days() -> int:
    try:
        p = str(config.SHARED)
        if p not in sys.path:
            sys.path.insert(0, p)
        import tenant_config as tc
        v = int(tc.get("skills.metrics.retention_days", DEFAULT_RETENTION_DAYS) or DEFAULT_RETENTION_DAYS)
        return max(1, v)
    except Exception:
        return DEFAULT_RETENTION_DAYS


def _conn(force: bool = False):
    """The threads connection, with this module's tables guaranteed on it.
    Keyed on the connection, not a module flag: a harness that empties the
    box between sections hands back a fresh database under the same process."""
    global _ready_for
    conn = threads._ensure()
    if force or _ready_for != id(conn):
        conn.executescript(_DDL)
        conn.commit()
        _ready_for = id(conn)
    return conn


def _write(sql: str, params: tuple) -> None:
    """One statement; a missing table (the file was replaced under us) gets
    the DDL re-run and exactly one retry."""
    try:
        conn = _conn()
        _prune(conn)
        with conn:
            conn.execute(sql, params)
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        conn = _conn(force=True)
        with conn:
            conn.execute(sql, params)


def _announce_once(exc: BaseException) -> None:
    """The first metrics failure in this process reaches the feed; the rest
    go to the journal. A results slide built on zeros must not be silent."""
    global _announced_failure
    print(f"[metrics] store unreachable: {type(exc).__name__}: {exc}", file=sys.stderr)
    if _announced_failure:
        return
    _announced_failure = True
    try:
        p = str(config.SHARED)
        if p not in sys.path:
            sys.path.insert(0, p)
        import notify
        notify.notify("ops", "Usage numbers are not being recorded right now",
                      headline="Usage numbers are not being recorded right now",
                      detail=f"metrics store unreachable · {type(exc).__name__} · the work itself "
                             f"still went through · full details are in the service journal",
                      kind="alert", skill="metrics")
    except Exception:
        pass


def _prune(conn) -> None:
    """Once per process, drop what is older than the retention window."""
    global _pruned
    if _pruned:
        return
    _pruned = True
    cutoff = (datetime.now(_tz()).date() - timedelta(days=retention_days())).isoformat()
    with conn:
        conn.execute("DELETE FROM counter_days WHERE day < ?", (cutoff,))
        conn.execute("DELETE FROM measurements WHERE day < ?", (cutoff,))


# ── writes: never raise ───────────────────────────────────────────────────────

def bump_day(name: str, by: int = 1) -> None:
    """The daily bucket for a counter. threads.bump_counter calls this, so every
    existing bump in the tree lands in a period without touching call sites."""
    try:
        _write("INSERT INTO counter_days (name, day, value) VALUES (?, ?, ?) "
               "ON CONFLICT(name, day) DO UPDATE SET value = value + excluded.value",
               (name, today(), by))
    except Exception as exc:
        _announce_once(exc)


def record(name: str, value: float, ref: Optional[str] = None) -> None:
    """One sample. `ref` must be an internal id; an address is refused, not stored."""
    try:
        if ref and "@" in str(ref):
            ref = None   # the masking contract: a sample never carries an address
        _write("INSERT INTO measurements (name, value, ts, day, ref) VALUES (?, ?, ?, ?, ?)",
               (name, float(value), datetime.now(timezone.utc).isoformat(), today(), ref))
    except Exception as exc:
        _announce_once(exc)


# ── reads ─────────────────────────────────────────────────────────────────────

def _days_back(n: int) -> str:
    return (datetime.now(_tz()).date() - timedelta(days=n - 1)).isoformat()


def counter_window(days: int) -> Dict[str, int]:
    """name -> total over the last `days` days (today inclusive)."""
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT name, SUM(value) AS v FROM counter_days WHERE day >= ? GROUP BY name",
            (_days_back(days),)).fetchall()
        return {r["name"]: int(r["v"]) for r in rows}
    except Exception as exc:
        _announce_once(exc)
        return {}


def counter_month(year: int, month: int) -> Dict[str, int]:
    prefix = f"{year:04d}-{month:02d}-"
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT name, SUM(value) AS v FROM counter_days WHERE day LIKE ? GROUP BY name",
            (prefix + "%",)).fetchall()
        return {r["name"]: int(r["v"]) for r in rows}
    except Exception as exc:
        _announce_once(exc)
        return {}


def samples(name: str, days: int) -> List[float]:
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT value FROM measurements WHERE name = ? AND day >= ? ORDER BY ts",
            (name, _days_back(days))).fetchall()
        return [float(r["value"]) for r in rows]
    except Exception as exc:
        _announce_once(exc)
        return []


def samples_month(name: str, year: int, month: int) -> List[float]:
    prefix = f"{year:04d}-{month:02d}-"
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT value FROM measurements WHERE name = ? AND day LIKE ? ORDER BY ts",
            (name, prefix + "%")).fetchall()
        return [float(r["value"]) for r in rows]
    except Exception as exc:
        _announce_once(exc)
        return []


def stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "p90": None, "max": None}
    vs = sorted(values)
    return {"count": len(vs), "median": median(vs),
            "p90": vs[min(len(vs) - 1, int(round(0.9 * (len(vs) - 1))))], "max": vs[-1]}


def summary() -> Dict[str, Any]:
    """The health-check instrument: current period, 7/30-day trends, samples."""
    now_local = datetime.now(_tz())
    month = counter_month(now_local.year, now_local.month)
    week, month30 = counter_window(7), counter_window(30)
    try:
        all_time = threads.all_counters()
    except Exception as exc:
        _announce_once(exc)
        all_time = {}
    names = sorted(set(month) | set(week) | set(month30) | set(all_time))
    out = {
        "period": {"month": now_local.strftime("%Y-%m"), "today": now_local.date().isoformat(),
                   "timezone": str(_tz())},
        "retention_days": retention_days(),
        "counters": {n: {"month": month.get(n, 0), "last_7d": week.get(n, 0),
                         "last_30d": month30.get(n, 0),
                         "all_time": all_time.get(n, {}).get("value", 0)}
                     for n in names},
        "measurements": {n: {"last_7d": stats(samples(n, 7)), "last_30d": stats(samples(n, 30))}
                         for n in ("response_latency_seconds", "lead_revival_hours",
                                   "tasks_completion_seconds")},
        "sweeps": {"runs_30d": month30.get("sweep_runs", 0),
                   "failures_30d": month30.get("sweep_failures", 0)},
    }
    return out
