"""Reads and writes of the platform tables (app/platform_db.py).

Every Sentinel decision, taint event, approval and grant change is a row in
`actions` (append-only). Approvals, grants and policies are their own tables;
`usage` carries model tokens and cost per day. No function here falls back to
a file: if the database is unavailable the caller gets platform_db.Unavailable
and refuses the action.
"""
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import platform_db
from . import sentinel

APPROVAL_TTL = timedelta(hours=24)
ACTION_COLUMNS = ("connector", "method", "action_class", "destinations", "skill", "user_id", "run_id",
                  "session_id", "task_id", "tainted", "verdict", "policy_mode", "sentinel_mode", "reason",
                  "grant_id", "approval_id", "source_kind", "source_id", "status_code")


def record_action(event: str, outcome: str, fields: Dict[str, Any], detail: Optional[dict] = None) -> int:
    cols = {k: v for k, v in fields.items() if k in ACTION_COLUMNS and v is not None}
    if "destinations" in cols:
        cols["destinations"] = list(cols["destinations"])
    names = ["event", "outcome", "detail", *cols]
    values = [event, outcome, json.dumps(detail or {}), *cols.values()]
    sql = (f"INSERT INTO actions ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))}) "
           f"RETURNING id")
    with platform_db.connect() as c:
        return int(c.execute(sql, values).fetchone()["id"])


def fields_from_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """sentinel.summarize() keys -> actions columns."""
    return {"connector": summary.get("connector"), "method": summary.get("method"),
            "action_class": summary.get("class"), "destinations": summary.get("destinations") or [],
            "skill": summary.get("skill") or "", "user_id": summary.get("user") or "",
            "run_id": summary.get("run_id") or "", "session_id": summary.get("session_id") or "",
            "task_id": summary.get("task_id") or "", "tainted": summary.get("tainted"),
            "verdict": summary.get("verdict"), "policy_mode": summary.get("mode"),
            "reason": summary.get("reason"), "grant_id": summary.get("grant_id"),
            "source_kind": summary.get("source_kind"), "source_id": summary.get("source_id")}


def record_held(event: str, outcome: str, fields: Dict[str, Any], action: Dict[str, Any],
                detail: Optional[dict] = None) -> int:
    """A held action: the body-free audit row plus the full proposal, readable
    only through the owner's held-action view."""
    aid = record_action(event, outcome, fields, detail)
    with platform_db.connect() as c:
        c.execute("INSERT INTO held_actions (action_id, action_json) VALUES (%s, %s)", (aid, json.dumps(action)))
    return aid


def get_held(action_id: int) -> Optional[Dict[str, Any]]:
    with platform_db.connect() as c:
        return c.execute("SELECT a.*, h.action_json FROM actions a JOIN held_actions h ON h.action_id = a.id "
                         "WHERE a.id = %s", (action_id,)).fetchone()


def recent_actions(limit: int = 50, outcome: Optional[str] = None) -> List[Dict[str, Any]]:
    with platform_db.connect() as c:
        if outcome:
            return c.execute("SELECT * FROM actions WHERE outcome=%s ORDER BY id DESC LIMIT %s",
                             (outcome, limit)).fetchall()
        return c.execute("SELECT * FROM actions ORDER BY id DESC LIMIT %s", (limit,)).fetchall()


def rates(now: datetime) -> sentinel.Rates:
    """Executed sends in the last day (9.4 check 4)."""
    out = sentinel.Rates()
    with platform_db.connect() as c:
        rows = c.execute(
            "SELECT ts, action_class, destinations FROM actions WHERE outcome='executed' AND ts >= %s "
            "AND action_class IN (%s, %s)",
            (now - timedelta(days=1), sentinel.INTERNAL_SEND, sentinel.EXTERNAL_SEND)).fetchall()
    hour = now - timedelta(hours=1)
    for r in rows:
        if r["action_class"] == sentinel.EXTERNAL_SEND and r["ts"] >= hour:
            out.external_sends_last_hour += 1
        for d in r["destinations"] or []:
            out.per_recipient_today[d] = out.per_recipient_today.get(d, 0) + 1
    return out


def is_tainted(run_id: str) -> bool:
    if not run_id:
        return True
    with platform_db.connect() as c:
        return c.execute("SELECT 1 FROM actions WHERE event='taint' AND run_id=%s LIMIT 1",
                         (run_id,)).fetchone() is not None


def taint(run_id: str, reason: str, skill: str) -> bool:
    """Record that a run read untrusted content. True if this is the run's first taint."""
    if not run_id or is_tainted(run_id):
        return False
    record_action("taint", "tainted", {"run_id": run_id, "skill": skill, "reason": reason, "tainted": True})
    return True


def policy_modes(user: str, skill: str, cls: str) -> Tuple[Optional[str], Optional[str]]:
    """(user override, org ceiling) for one action class; the specific skill wins over '*'
    for the user, and the strictest ceiling applies."""
    with platform_db.connect() as c:
        u = c.execute("SELECT mode FROM policies WHERE scope='user' AND user_id=%s AND action_class=%s "
                      "AND skill IN (%s, '*') ORDER BY (skill = '*') LIMIT 1", (user, cls, skill)).fetchone()
        ceilings = [r["mode"] for r in c.execute(
            "SELECT mode FROM policies WHERE scope='org_ceiling' AND action_class=%s AND skill IN (%s, '*')",
            (cls, skill)).fetchall()]
    rank = {sentinel.AUTO: 0, sentinel.APPROVE: 1, sentinel.BLOCK: 2}
    ceiling = max(ceilings, key=lambda m: rank.get(m, 3)) if ceilings else None
    return (u["mode"] if u else None), ceiling


def active_grants(user: str, connector: str, skill: str) -> List[sentinel.Grant]:
    with platform_db.connect() as c:
        rows = c.execute("SELECT * FROM grants WHERE user_id=%s AND connector=%s AND skill=%s "
                         "AND revoked_at IS NULL", (user, connector, skill)).fetchall()
    return [sentinel.Grant(id=r["id"], user=r["user_id"], connector=r["connector"], skill=r["skill"],
                           destination=r["destination"], scope=r["scope"], session_id=r["session_id"],
                           task_id=r["task_id"], expires_at=r["expires_at"], used=r["used_at"] is not None)
            for r in rows]


def consume_once_grant(grant_id: str) -> bool:
    """Atomically use a one-time grant. False if someone else used it first."""
    with platform_db.connect() as c:
        return c.execute("UPDATE grants SET used_at=now() WHERE id=%s AND scope='once' AND used_at IS NULL "
                         "RETURNING id", (grant_id,)).fetchone() is not None


def create_grant(grant: sentinel.Grant, *, approval_id: str, action_class: str, created_by: str) -> None:
    with platform_db.connect() as c:
        c.execute("INSERT INTO grants (id, approval_id, user_id, connector, skill, action_class, destination, "
                  "scope, session_id, task_id, expires_at, created_by) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (grant.id, approval_id, grant.user, grant.connector, grant.skill, action_class,
                   grant.destination, grant.scope, grant.session_id, grant.task_id, grant.expires_at,
                   created_by))
    record_action("grant", "granted", {"grant_id": grant.id, "approval_id": approval_id,
                                       "connector": grant.connector, "skill": grant.skill,
                                       "action_class": action_class, "destinations": [grant.destination],
                                       "user_id": grant.user}, {"scope": grant.scope})


def new_approval_id() -> str:
    return "apr_" + uuid.uuid4().hex[:12]


def create_approval(*, origin: str, user_id: str, kind: str, skill: str, summary: str,
                    action: Dict[str, Any], action_class: Optional[str] = None, reason: str = "",
                    source_kind: Optional[str] = None, source_id: Optional[str] = None,
                    run_id: str = "", session_id: str = "", task_id: str = "",
                    requires_role: Optional[str] = None, approval_id: Optional[str] = None,
                    created_at: Optional[datetime] = None, status: str = "pending",
                    decided_at: Optional[datetime] = None, decided_by: Optional[str] = None,
                    decision: Optional[dict] = None) -> str:
    aid = approval_id or new_approval_id()
    now = created_at or sentinel.utcnow()
    with platform_db.connect() as c:
        c.execute("INSERT INTO approvals (id, origin, user_id, kind, skill, summary, action_class, action_json, "
                  "reason, source_kind, source_id, run_id, session_id, task_id, requires_role, status, "
                  "created_at, expires_at, decided_at, decided_by, decision_json) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (aid, origin, user_id, kind, skill, summary, action_class, json.dumps(action), reason,
                   source_kind, source_id, run_id, session_id, task_id, requires_role, status, now,
                   now + APPROVAL_TTL, decided_at, decided_by,
                   json.dumps(decision) if decision is not None else None))
    record_action("approval", "requested" if status == "pending" else f"imported_{status}",
                  {"approval_id": aid, "skill": skill, "action_class": action_class, "user_id": user_id,
                   "run_id": run_id, "session_id": session_id, "task_id": task_id,
                   "source_kind": source_kind, "source_id": source_id, "reason": reason},
                  {"origin": origin, "kind": kind})
    return aid


def expire_due(now: datetime) -> int:
    with platform_db.connect() as c:
        rows = c.execute("UPDATE approvals SET status='expired', decided_at=%s, decided_by='sentinel' "
                         "WHERE status='pending' AND expires_at <= %s RETURNING id, skill", (now, now)).fetchall()
    for r in rows:
        record_action("approval", "expired", {"approval_id": r["id"], "skill": r["skill"]})
    return len(rows)


def list_approvals() -> List[Dict[str, Any]]:
    with platform_db.connect() as c:
        return c.execute("SELECT * FROM approvals ORDER BY created_at DESC").fetchall()


def get_approval(approval_id: str) -> Optional[Dict[str, Any]]:
    with platform_db.connect() as c:
        return c.execute("SELECT * FROM approvals WHERE id=%s", (approval_id,)).fetchone()


def resolve_approval(approval_id: str, status: str, decided_by: str, decision: Dict[str, Any]) -> bool:
    """Pending -> approved/rejected, once. False if it was no longer pending."""
    with platform_db.connect() as c:
        row = c.execute("UPDATE approvals SET status=%s, decided_at=now(), decided_by=%s, decision_json=%s "
                        "WHERE id=%s AND status='pending' RETURNING skill, action_class, user_id",
                        (status, decided_by, json.dumps(decision), approval_id)).fetchone()
    if row:
        record_action("approval", status, {"approval_id": approval_id, "skill": row["skill"],
                                           "action_class": row["action_class"], "user_id": row["user_id"]},
                      {"decided_by": decided_by, "scope": decision.get("scope")})
    return row is not None


def annotate_approval(approval_id: str, extra: Dict[str, Any]) -> None:
    """Merge the execution outcome into a decided approval's decision record."""
    with platform_db.connect() as c:
        c.execute("UPDATE approvals SET decision_json = coalesce(decision_json, '{}'::jsonb) || %s::jsonb "
                  "WHERE id=%s AND status <> 'pending'", (json.dumps(extra), approval_id))


def add_usage(day, model: str, purpose: str, *, input_tokens: int, output_tokens: int,
              cache_read: int, cache_write: int, cost_usd: Optional[float]) -> None:
    with platform_db.connect() as c:
        c.execute("INSERT INTO usage (day, model, purpose, requests, input_tokens, output_tokens, "
                  "cache_read_tokens, cache_write_tokens, cost_usd, unpriced_requests) "
                  "VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s) "
                  "ON CONFLICT (day, model, purpose) DO UPDATE SET requests = usage.requests + 1, "
                  "input_tokens = usage.input_tokens + EXCLUDED.input_tokens, "
                  "output_tokens = usage.output_tokens + EXCLUDED.output_tokens, "
                  "cache_read_tokens = usage.cache_read_tokens + EXCLUDED.cache_read_tokens, "
                  "cache_write_tokens = usage.cache_write_tokens + EXCLUDED.cache_write_tokens, "
                  "cost_usd = usage.cost_usd + EXCLUDED.cost_usd, "
                  "unpriced_requests = usage.unpriced_requests + EXCLUDED.unpriced_requests",
                  (day, model, purpose, input_tokens, output_tokens, cache_read, cache_write,
                   cost_usd or 0, 0 if cost_usd is not None else 1))


def usage_by_day(days: int = 31) -> List[Dict[str, Any]]:
    with platform_db.connect() as c:
        return c.execute("SELECT day, model, purpose, requests, input_tokens, output_tokens, cache_read_tokens, "
                         "cache_write_tokens, cost_usd::float AS cost_usd, unpriced_requests FROM usage "
                         "WHERE day > current_date - %s ORDER BY day DESC, model", (days,)).fetchall()
