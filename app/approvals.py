"""Approvals, in the platform database (decision 2; docs/SPEC.md 9.3).

A request is a row in `approvals`, filed by Sentinel when a write needs a
person (origin broker, holding the exact action) or by a skill through
POST /api/broker/approvals (origin producer). The agent can propose; it can
never decide: it has no session, cannot reach the database, and cannot mint a
callback.

A decision arrives one of two ways, both structured:
- a signed callback (sentinel.sign_callback) issued with the queue to the
  owner's session; it names the approval, the answer and the grant scope;
- the PWA card's button from an owner session, which is a one-time approval.
Free text is never parsed. A decision is recorded once (pending -> decided is
a conditional UPDATE), then executed: a broker action re-runs the Sentinel
gate with the approval answering `ask`; a config change is applied first and
refused changes leave the request pending; an approved send_email goes
through send_gate, and in shadow mode is held instead.
"""
import hashlib
import hmac
import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import config
from . import platform_store as store
from . import sentinel

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
KIND_ACTION = "action"
KIND_CONFIG = "config-change"
VALID_KINDS = (KIND_ACTION, KIND_CONFIG)
VALID_DECISIONS = (APPROVED, REJECTED)
GRANT_SCOPES = (sentinel.SCOPE_ONCE, sentinel.SCOPE_SESSION, sentinel.SCOPE_TASK, sentinel.SCOPE_ALWAYS)


class ConfigChangeRefused(RuntimeError):
    """An approved config-change could not be applied; the request stays pending."""


def _secret() -> bytes:
    return hmac.new(config.require("APP_SESSION_SECRET").encode(), b"jarvis-approval-callback-v1",
                    hashlib.sha256).digest()


def _iso(v) -> Optional[str]:
    return v.isoformat() if isinstance(v, datetime) else v


def _scopes_for(row: Dict[str, Any]) -> List[str]:
    """A grant binds one connector, one destination and one skill, so a wider
    scope than once is offered only for a broker action with one destination."""
    if row["origin"] != "broker" or row.get("action_class") in (None, sentinel.LOCKED):
        return [sentinel.SCOPE_ONCE]
    a = row["action_json"] or {}
    try:
        from . import broker  # noqa: PLC0415
        params = a.get("params") or {}
        addrs = broker._recipients(params)[0] if (a.get("api"), a.get("version"), a.get("method")) \
            in broker.SEND_METHODS else []
        _cls, dests = sentinel.classify_google(a.get("api"), a.get("method"), params, broker._org_domains(), addrs)
    except Exception:
        return [sentinel.SCOPE_ONCE]
    return list(GRANT_SCOPES) if len(dests) == 1 else [sentinel.SCOPE_ONCE]


def _view(row: Dict[str, Any], person: Optional[str]) -> Dict[str, Any]:
    decision = row.get("decision_json") or {}
    item = {
        "id": row["id"], "ts": _iso(row["created_at"]), "tenant": config.get("JARVIS_TENANT") or "",
        "origin": row["origin"], "kind": row["kind"], "skill": row["skill"], "summary": row["summary"],
        "payload": row["action_json"], "action_class": row.get("action_class"), "reason": row.get("reason"),
        "source": ({"kind": row["source_kind"], "id": row["source_id"]} if row.get("source_kind") else None),
        "requires_role": row.get("requires_role"), "status": row["status"],
        "expires_at": _iso(row["expires_at"]),
    }
    if row["status"] != PENDING:
        item.update({"decided_by": row.get("decided_by"), "decided_at": _iso(row.get("decided_at"))})
        for k in ("effect", "send", "execution", "scope", "via"):
            if decision.get(k) is not None:
                item[k] = decision[k]
    elif person is not None:
        exp = row["expires_at"]
        sign = lambda ans, scope: sentinel.sign_callback(  # noqa: E731
            _secret(), approval_id=row["id"], user=person, answer=ans, scope=scope, expires_at=exp)
        item["callbacks"] = {"reject": sign("reject", sentinel.SCOPE_ONCE),
                             "approve": {s: sign("approve", s) for s in _scopes_for(row)}}
    return item


def query(status: Optional[str] = None, person: Optional[str] = None) -> Dict[str, Any]:
    store.expire_due(sentinel.utcnow())
    items = [_view(r, person) for r in store.list_approvals()]
    counts = {s: sum(1 for i in items if i["status"] == s) for s in (PENDING, APPROVED, REJECTED, EXPIRED)}
    if status:
        items = [i for i in items if i["status"] == status]
    return {"approvals": items, "count": len(items), "counts": counts}


def get(request_id: str) -> Optional[Dict[str, Any]]:
    row = store.get_approval(request_id)
    return _view(row, None) if row else None


def answer_from_callback(request_id: str, token: str, person: str) -> Dict[str, str]:
    """Verify a signed callback for this approval and this session's person.
    Raises sentinel.CallbackRefused."""
    data = sentinel.verify_callback(_secret(), token, now=sentinel.utcnow(), user=person)
    if data["approval_id"] != request_id:
        raise sentinel.CallbackRefused("callback is for a different approval")
    return {"decision": APPROVED if data["answer"] == "approve" else REJECTED, "scope": data["scope"]}


def decide(request_id: str, decision: str, decided_by: str, scope: str = sentinel.SCOPE_ONCE,
           via: str = "pwa_session") -> Dict[str, Any]:
    """Record a decision, once, then carry it out. LookupError for an unknown
    id; ValueError for a bad decision or one that is no longer pending."""
    decision = (decision or "").strip().lower()
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {VALID_DECISIONS}")
    store.expire_due(sentinel.utcnow())
    row = store.get_approval(request_id)
    if row is None:
        raise LookupError(request_id)
    if row["status"] != PENDING:
        raise ValueError(f"already {row['status']}")
    if decision == APPROVED and scope not in _scopes_for(row):
        raise ValueError(f"scope {scope!r} is not offered for this request")

    record: Dict[str, Any] = {"scope": scope, "via": via}
    payload = row["action_json"] or {}
    if decision == APPROVED and row["kind"] == KIND_CONFIG:
        from . import config_apply  # noqa: PLC0415
        try:
            record["effect"] = config_apply.apply_changes(payload.get("changes", []))
        except config_apply.ConfigApplyError as exc:
            raise ConfigChangeRefused(str(exc)) from exc

    if not store.resolve_approval(request_id, decision, decided_by, record):
        raise ValueError("already decided")

    if decision == APPROVED and row["kind"] == KIND_ACTION:
        if row["origin"] == "broker":
            from . import broker  # noqa: PLC0415
            record["execution"] = broker.execute_approved(row, scope, decided_by)
            store.annotate_approval(request_id, {"execution": record["execution"]})
        elif payload.get("action") == "send_email":
            record["send"] = _execute_send(row)
            store.annotate_approval(request_id, {"send": record["send"]})
            _announce_send(row, record["send"], decided_by)
    return get(request_id) or {}


# ── approved send_email (producer filings) ───────────────────────────────────
SEND_FIELDS = ("to", "subject")


def _send_gate():
    """send_gate lives in owner-email/scripts and puts _shared on sys.path itself."""
    scripts = config.SKILLS_ROOT / "owner-email" / "scripts"
    for p in (str(scripts), str(config.SHARED)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import send_gate  # noqa: PLC0415
    return send_gate


def _execute_send(row: Dict[str, Any]) -> Dict[str, Any]:
    """Carry out an approved send_email payload. In shadow mode it is held
    through the broker's outbound gate; otherwise it goes through
    send_gate.gated_send, the one send path. Never raises.

    Result shape (always): {ok, state, reason, to, subject, ...gate fields}
      state: 'sent' | 'captured' | 'blocked' | 'held' | 'failed'
    """
    from . import broker  # noqa: PLC0415
    payload = dict(row.get("action_json") or {})
    to, subject = payload.get("to"), payload.get("subject") or ""
    body, html = payload.get("body"), payload.get("html_body")
    base = {"ok": False, "to": to, "subject": subject, "ts": sentinel.utcnow().isoformat()}
    missing = [f for f in SEND_FIELDS if not payload.get(f)]
    if missing:
        return {**base, "state": "failed", "reason": f"payload is missing {missing}"}
    if not body and not html:
        return {**base, "state": "failed",
                "reason": "payload carries no full body (body/html_body) — only a preview; "
                          "the producing skill must file the whole message"}
    if broker.sentinel_mode() == broker.SHADOW:
        dests = [to] if isinstance(to, str) else list(to or [])
        ctx = {"run": row.get("run_id") or "", "session": row.get("session_id") or "",
               "task": row.get("task_id") or "", "skill": row.get("skill") or "",
               "source": f"approval:{row['id']}"}
        status, out = broker.gate_outbound("email_send", "email", dests, f"send the approved email: {row['summary']}",
                                           {"to": to, "subject": subject, "body": body, "html_body": html,
                                            "cc": payload.get("cc")}, ctx)
        return {**base, "state": "held", "reason": out.get("detail"), "action_id": out.get("action_id")}
    try:
        sg = _send_gate()
        plain = body or html
        token = sg.create_approval_token(to, subject, plain)
        result = sg.gated_send(
            to=to, subject=subject, body=plain,
            transport=payload.get("transport") or "smtp",
            approval_token=token, cc=payload.get("cc"), html_body=html,
            reply_to=payload.get("reply_to"), attachments=payload.get("attachments") or [],
            run_id=row.get("id"))
    except Exception as exc:
        return {**base, "state": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    result = dict(result or {})
    state = "captured" if result.get("captured") else "sent" if result.get("sent") else \
        "blocked" if result.get("blocked") else "failed"
    return {**base, **{k: v for k, v in result.items() if k != "token"},
            "state": state, "ok": state in ("sent", "captured"),
            "reason": result.get("reason") or result.get("error") or state}


def _announce_send(row: Dict[str, Any], send: Dict[str, Any], decided_by: str) -> None:
    """One feed line per approved send. A failure is an ops alert with the
    reason; it must be as visible as the approval was. A held send already
    has its would-have line from the gate."""
    if send.get("state") == "held":
        return
    p = str(config.SHARED)
    if p not in sys.path:
        sys.path.insert(0, p)
    try:
        import notify  # noqa: PLC0415
        summary = str(row.get("summary") or "the approved email")
        if send.get("ok"):
            notify.notify("ops", kind="approval.sent", headline=f"Sent the approved email — {summary}",
                          detail=f"approval {row['id']} · approved by {decided_by} · "
                                 f"{send.get('state')} · {send.get('reason')}",
                          skill=str(row.get("skill") or "approvals"))
        else:
            notify.notify("ops", kind="alert", headline=f"Couldn't send the approved email — {summary}",
                          detail=f"approval {row['id']} · approved by {decided_by} · "
                                 f"{send.get('state')}: {send.get('reason')} · nothing was sent · "
                                 f"the approval stands; fix the cause and ask the skill to re-file",
                          skill=str(row.get("skill") or "approvals"))
    except Exception as exc:
        print(f"[approvals] feed announce failed: {type(exc).__name__}: {exc}", file=sys.stderr)


# --------------------------------------------------------------------- seed and import

FIXTURES = [
    {
        "skill": "email-router",
        "summary": "Open a deal channel for 2047 Columbia",
        "payload": {
            "action": "create_channel",
            "channel_name": "jarvis-deals-2047-columbia",
            "reason": "3 emails in 5 days from two parties reference this address",
            "from": "vipin.das@sobharealty.com",
            "subject": "2047 Columbia — permit #26032635 update",
        },
    },
    {
        "skill": "owner-email",
        "summary": "Send a follow-up to William Mchenry about 609 25th St",
        "payload": {
            "action": "send_email",
            "to": "wmchenry@example.com",
            "subject": "Following up on 609 25th St",
            # THE WHOLE MESSAGE, not a preview: an approved send_email is
            # executed from its payload.
            "body": (
                "Hi William — great meeting you and your wife at the open house "
                "on Saturday. You mentioned the second bedroom mattered most, so "
                "I pulled two more listings nearby with a real second bedroom "
                "rather than a converted study; I'll send those over separately.\n\n"
                "If 609 25th is still in the running, I can set up a second walk-"
                "through this week — Thursday afternoon or Saturday morning both "
                "work on my end. Just let me know which suits.\n\n"
                "Best,\nSpencer"
            ),
            "lead_temperature": "Warm",
        },
    },
    {
        "skill": "email-to-calendar",
        "summary": "Add a showing at 11 Tiel Way to the calendar",
        "payload": {
            "action": "create_event",
            "title": "Showing — 11 Tiel Way",
            "start": "2026-08-19T15:00:00-05:00",
            "end": "2026-08-19T15:45:00-05:00",
            "attendees": ["eddie@34dev.com"],
            "source": "ShowingTime confirmation email",
        },
    },
    {
        "skill": "jarvis",
        "kind": KIND_CONFIG,
        "requires_role": "owner",
        "summary": "Change the daily digest times",
        "payload": {
            "requested_via": "chat",
            "request_text": "can you send my digest at 8am instead of 5am and 3pm?",
            "changes": [
                {
                    "key": "skills.task-manager.digest_times_local",
                    "current": ["05:00", "15:00"],
                    "proposed": ["08:00"],
                    "label": "Digest times: 5:00 AM & 3:00 PM → 8:00 AM",
                }
            ],
        },
    },
]


def seed() -> int:
    """File the fixture requests. Idempotent by summary — safe to re-run."""
    existing = {r["summary"] for r in store.list_approvals()}
    written = 0
    for f in FIXTURES:
        if f["summary"] in existing:
            continue
        kind = f.get("kind", KIND_ACTION)
        store.create_approval(origin="seed", user_id="owner", kind=kind, skill=f["skill"], summary=f["summary"],
                              action=f["payload"], reason="fixture",
                              requires_role=f.get("requires_role") or ("owner" if kind == KIND_CONFIG else None))
        written += 1
    return written


def import_jsonl(requests_path, decisions_path) -> Dict[str, int]:
    """One-time import of the v1 file contract (approvals.jsonl plus
    approvals_decisions.jsonl). Ids already in the table are skipped; the
    files are left where they are."""
    def rows(path):
        out = []
        try:
            for line in open(path, encoding="utf-8", errors="replace"):
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
        except FileNotFoundError:
            pass
        return out

    latest = {str(d.get("id")): d for d in rows(decisions_path) if d.get("id")}
    have = {r["id"] for r in store.list_approvals()}
    counts = {"imported": 0, "skipped": 0}
    for req in rows(requests_path):
        rid = str(req.get("id") or "")
        if not rid.startswith("apr_") or rid in have:
            counts["skipped"] += 1
            continue
        d = latest.get(rid) or {}
        status = str(d.get("decision") or "").lower()
        status = status if status in VALID_DECISIONS else PENDING
        try:
            created = datetime.fromisoformat(str(req.get("ts")))
        except ValueError:
            created = sentinel.utcnow()
        decided = None
        if status != PENDING:
            try:
                decided = datetime.fromisoformat(str(d.get("ts")))
            except ValueError:
                decided = sentinel.utcnow()
        kind = req.get("kind") if req.get("kind") in VALID_KINDS else KIND_ACTION
        store.create_approval(origin="import", user_id="owner", kind=kind, skill=str(req.get("skill") or "unknown"),
                              summary=str(req.get("summary") or "(no summary)"), action=req.get("payload") or {},
                              reason="imported from approvals.jsonl", requires_role=req.get("requires_role"),
                              approval_id=rid, created_at=created, status=status, decided_at=decided,
                              decided_by=d.get("decided_by") if status != PENDING else None,
                              decision={k: d[k] for k in ("effect", "send") if k in d} if status != PENDING else None)
        counts["imported"] += 1
    return counts


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "seed":
        print(f"seeded {seed()} approval request(s)")
    elif len(sys.argv) > 3 and sys.argv[1] == "import":
        print(json.dumps(import_jsonl(sys.argv[2], sys.argv[3])))
    else:
        print(json.dumps(query(), indent=2, default=str)[:4000])
