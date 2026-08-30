"""Approvals — the v1 file contract.

Two append-only JSONL files under ~/.hermes/state/, mirroring how the activity
feed already works so skills can produce approvals with no new infrastructure:

    approvals.jsonl            {id, ts, tenant, skill, summary, payload, status}
    approvals_decisions.jsonl  {id, decision, decided_by, ts}

A request's *current* status is the request's own status overlaid with the LAST
decision recorded for its id. Decisions are appended, never rewritten, so the
history of who decided what and when survives — and a replayed or duplicated
decision line is harmless rather than corrupting.

THIS CONTRACT IS DISPOSABLE. The spec says so explicitly: Phase 4/5 may move
approvals to the gateway. What carries forward is the UI shape (queue,
summary, approve/reject, decision history), not this file format. Keep the
read/write surface in this one module so replacing it is a single-file job.

No skill writes requests yet — email-router's Category E (new-deal-channel
creation) is earmarked as the first real producer. Until then the queue is
seeded with fixtures via `python3 -m app.approvals seed`.
"""

import fcntl
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

STATE_DIR = config.HERMES_HOME / "state"
REQUESTS_PATH = Path(
    os.environ.get("JARVIS_APPROVALS", str(STATE_DIR / "approvals.jsonl"))
).expanduser()
DECISIONS_PATH = Path(
    os.environ.get("JARVIS_APPROVAL_DECISIONS", str(STATE_DIR / "approvals_decisions.jsonl"))
).expanduser()

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
KIND_ACTION = "action"
KIND_CONFIG = "config-change"
VALID_KINDS = (KIND_ACTION, KIND_CONFIG)

VALID_DECISIONS = (APPROVED, REJECTED)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """All parseable objects in a JSONL file. Malformed lines are skipped, not fatal."""
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except FileNotFoundError:
        return []
    return out


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    """Append one record under an exclusive lock, matching notify.py's discipline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _latest_decisions() -> Dict[str, Dict[str, Any]]:
    """id -> the last decision recorded for it (later lines win)."""
    latest: Dict[str, Dict[str, Any]] = {}
    for d in _read_jsonl(DECISIONS_PATH):
        rid = d.get("id")
        if rid:
            latest[str(rid)] = d
    return latest


def load_all() -> List[Dict[str, Any]]:
    """Every request with its effective status, newest first."""
    decisions = _latest_decisions()
    items: List[Dict[str, Any]] = []
    for req in _read_jsonl(REQUESTS_PATH):
        rid = str(req.get("id") or "")
        if not rid:
            continue
        item = dict(req)
        item["id"] = rid
        item.setdefault("status", PENDING)
        item.setdefault("skill", "unknown")
        item.setdefault("summary", "")
        item.setdefault("payload", {})
        # v1.1. Absent = "action", which is what every pre-2026-08-18 row is, so
        # no migration and no rewrite of the file: an old request is a valid new
        # request. Unknown kinds also render as "action" rather than erroring —
        # a producer from a newer build must degrade, not break the queue.
        item.setdefault("kind", KIND_ACTION)
        # Recorded now, ENFORCED by Workstream 3 when per-user auth exists. The
        # field existing today is what makes that a WHERE clause instead of a
        # migration (standing design rule).
        item.setdefault("requires_role", "owner" if item["kind"] == KIND_CONFIG else None)
        decision = decisions.get(rid)
        if decision:
            d = str(decision.get("decision") or "").lower()
            if d in VALID_DECISIONS:
                item["status"] = d
            item["decided_by"] = decision.get("decided_by")
            item["decided_at"] = decision.get("ts")
            if decision.get("send") is not None:
                item["send"] = decision["send"]
        items.append(item)
    items.sort(key=lambda i: str(i.get("ts") or ""), reverse=True)
    return items


def query(status: Optional[str] = None) -> Dict[str, Any]:
    items = load_all()
    counts = {
        PENDING: sum(1 for i in items if i["status"] == PENDING),
        APPROVED: sum(1 for i in items if i["status"] == APPROVED),
        REJECTED: sum(1 for i in items if i["status"] == REJECTED),
    }
    if status:
        items = [i for i in items if i["status"] == status]
    return {
        "approvals": items,
        "count": len(items),
        "counts": counts,
        "requests_path": str(REQUESTS_PATH),
    }


def get(request_id: str) -> Optional[Dict[str, Any]]:
    return next((i for i in load_all() if i["id"] == request_id), None)


class ConfigChangeRefused(RuntimeError):
    """An approved config-change could not be applied; the request stays pending."""


def decide(request_id: str, decision: str, decided_by: str) -> Dict[str, Any]:
    """
    Record a decision. Raises LookupError if the id is unknown and ValueError if
    the decision is not approve/reject or the request is already resolved.

    Already-resolved is refused rather than silently re-recorded: a double-tap
    on Approve should not read as two separate human decisions.
    """
    decision = (decision or "").strip().lower()
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {VALID_DECISIONS}")

    item = get(request_id)
    if item is None:
        raise LookupError(request_id)
    if item["status"] != PENDING:
        raise ValueError(f"already {item['status']}")

    # A config-change is applied BEFORE the decision is recorded, and a refusal
    # to apply leaves the request PENDING. The alternative — record first, apply
    # second — produces a request marked "approved" whose change never happened,
    # which is the most misleading state this queue could show.
    effect = None
    if decision == APPROVED and item.get("kind") == KIND_CONFIG:
        from . import config_apply
        try:
            effect = config_apply.apply_changes(item.get("payload", {}).get("changes", []))
        except config_apply.ConfigApplyError as exc:
            raise ConfigChangeRefused(str(exc)) from exc

    # AN APPROVED SEND IS EXECUTED HERE, and its outcome — sent, captured by
    # the boundary, blocked, or failed — is written on the decision record.
    # Before this, approving a send_email recorded "approved" and nothing
    # happened: the queue showed a mail as sent that was never sent. A failure
    # does not un-approve the decision (the person did approve it); it is
    # recorded beside it and announced on the feed, so it cannot vanish.
    send = None
    if decision == APPROVED and item.get("kind", KIND_ACTION) == KIND_ACTION \
            and (item.get("payload") or {}).get("action") == "send_email":
        send = _execute_send(item)
    record = {
        "id": request_id,
        "decision": decision,
        "decided_by": decided_by,
        "ts": _iso_now(),
    }
    if effect:
        record["effect"] = effect
    if send is not None:
        record["send"] = send
    _append_jsonl(DECISIONS_PATH, record)
    if send is not None:
        _announce_send(item, send, decided_by)
    out = {**item, "status": decision, "decided_by": decided_by, "decided_at": record["ts"]}
    if effect:
        out["effect"] = effect
    if send is not None:
        out["send"] = send
    return out


# ── send execution ────────────────────────────────────────────────────────────
SEND_FIELDS = ("to", "subject")


def _send_gate():
    """send_gate lives in owner-email/scripts and puts _shared on sys.path itself."""
    import sys
    scripts = config.SKILLS_ROOT / "owner-email" / "scripts"
    for p in (str(scripts), str(config.SHARED)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import send_gate  # noqa: WPS433
    return send_gate


def _execute_send(item: Dict[str, Any]) -> Dict[str, Any]:
    """Run an approved send_email payload through send_gate.gated_send — the
    one send path — and return a record of what happened. Never raises: an
    exception is a failed send, and a failed send is a fact for the record.

    Result shape (always): {ok, state, reason, to, subject, ...gate fields}
      state: 'sent' | 'captured' | 'blocked' | 'failed'
    """
    payload = dict(item.get("payload") or {})
    to = payload.get("to")
    subject = payload.get("subject") or ""
    body = payload.get("body")
    html = payload.get("html_body")
    base = {"ok": False, "to": to, "subject": subject, "ts": _iso_now()}
    missing = [f for f in SEND_FIELDS if not payload.get(f)]
    if missing:
        return {**base, "state": "failed", "reason": f"payload is missing {missing}"}
    if not body and not html:
        # A preview is not a message. Sending it would mail the customer the
        # first 200 characters of what they approved.
        return {**base, "state": "failed",
                "reason": "payload carries no full body (body/html_body) — only a preview; "
                          "the producing skill must file the whole message"}
    try:
        sg = _send_gate()
        plain = body or html
        token = sg.create_approval_token(to, subject, plain)
        result = sg.gated_send(
            to=to, subject=subject, body=plain,
            transport=payload.get("transport") or "smtp",
            approval_token=token, cc=payload.get("cc"), html_body=html,
            reply_to=payload.get("reply_to"), attachments=payload.get("attachments") or [],
            run_id=item.get("id"))
    except Exception as exc:
        return {**base, "state": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    result = dict(result or {})
    if result.get("captured"):
        state = "captured"
    elif result.get("sent"):
        state = "sent"
    elif result.get("blocked"):
        state = "blocked"
    else:
        state = "failed"
    out = {**base, **{k: v for k, v in result.items() if k != "token"},
           "state": state, "ok": state in ("sent", "captured"),
           "reason": result.get("reason") or result.get("error") or state}
    return out


def _announce_send(item: Dict[str, Any], send: Dict[str, Any], decided_by: str) -> None:
    """One feed line per executed send. A failure is an ops alert with the
    reason; it must be as visible as the approval was."""
    import sys
    try:
        p = str(config.SHARED)
        if p not in sys.path:
            sys.path.insert(0, p)
        import notify  # noqa: WPS433
        summary = str(item.get("summary") or "the approved email")
        if send.get("ok"):
            notify.notify("ops", kind="approval.sent",
                          headline=f"Sent the approved email — {summary}",
                          detail=f"approval {item.get('id')} · approved by {decided_by} · "
                                 f"{send.get('state')} · {send.get('reason')}",
                          skill=str(item.get("skill") or "approvals"))
        else:
            notify.notify("ops", kind="alert",
                          headline=f"Couldn't send the approved email — {summary}",
                          detail=f"approval {item.get('id')} · approved by {decided_by} · "
                                 f"{send.get('state')}: {send.get('reason')} · nothing was sent · "
                                 f"the approval stands; fix the cause and ask the skill to re-file",
                          skill=str(item.get("skill") or "approvals"))
    except Exception as exc:
        print(f"[approvals] feed announce failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------- seed


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
            # THE WHOLE MESSAGE, not a preview. An approved send_email is now
            # executed from its payload, so a request that carries only the
            # first 200 characters is a request to mail the first 200
            # characters. A producer that cannot file the full body must not
            # file a send_email at all.
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
    # v1.1 — exercises the config-change renderer and the applier end to end
    # WITHOUT a producer. Deliberately a real, appliable key with its real
    # current value, so approving this fixture genuinely changes the config and
    # the whole path is proven rather than mocked.
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


def seed(tenant: str = "34dev") -> int:
    """Append the fixture requests. Idempotent by summary — safe to re-run."""
    existing = {i.get("summary") for i in _read_jsonl(REQUESTS_PATH)}
    written = 0
    for f in FIXTURES:
        if f["summary"] in existing:
            continue
        _append_jsonl(
            REQUESTS_PATH,
            {
                "id": f"apr_{uuid.uuid4().hex[:12]}",
                "ts": _iso_now(),
                "tenant": tenant,
                "status": PENDING,
                **f,
            },
        )
        written += 1
        time.sleep(0.01)  # keep ts ordering stable and distinct
    return written


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "seed":
        n = seed()
        print(f"seeded {n} approval request(s) -> {REQUESTS_PATH}")
    else:
        report = query()
        print(json.dumps(report, indent=2)[:4000])
