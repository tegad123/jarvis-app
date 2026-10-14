"""Sentinel's deterministic gate (docs/SPEC.md 9.1-9.5).

Pure decisions, no I/O: the broker supplies the action, the policy, the
grants and the recent-send counts, and records what this module decides.
The model check (9.4, second half) is not here; when it exists it may only
tighten a verdict this module returns.

    classify_google()  which action class a broker call is (9.1)
    effective_mode()   default, user override and org ceiling combined (9.2)
    grant_covers()     whether a user's grant covers this exact action (9.3)
    decide()           allow / ask / deny, with the reason (9.4, 9.5)
    sign_callback() / verify_callback()
                       the only way an approval reaches Sentinel: a signed,
                       structured answer. Free text is never parsed (9.3).

Taint (9.5): an action from a run that read untrusted inbound content loses
`auto` for external_send and destructive unless a grant covers that exact
destination and skill. An action with no run identity is treated as tainted.
"""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

READ = "read"
INTERNAL_WRITE = "internal_write"
INTERNAL_SEND = "internal_send"
EXTERNAL_SEND = "external_send"
DESTRUCTIVE = "destructive"
LOCKED = "locked"
CLASSES = (READ, INTERNAL_WRITE, INTERNAL_SEND, EXTERNAL_SEND, DESTRUCTIVE, LOCKED)
WRITE_CLASSES = (INTERNAL_WRITE, INTERNAL_SEND, EXTERNAL_SEND, DESTRUCTIVE, LOCKED)

AUTO = "auto"
APPROVE = "approve"
BLOCK = "block"
MODES = (AUTO, APPROVE, BLOCK)
_RANK = {AUTO: 0, APPROVE: 1, BLOCK: 2}

DEFAULTS = {
    READ: AUTO,
    INTERNAL_WRITE: AUTO,
    INTERNAL_SEND: AUTO,
    EXTERNAL_SEND: APPROVE,
    DESTRUCTIVE: APPROVE,
    LOCKED: APPROVE,
}
TAINT_SENSITIVE = (EXTERNAL_SEND, DESTRUCTIVE)

ALLOW = "allow"
ASK = "ask"
DENY = "deny"

SCOPE_ONCE = "once"
SCOPE_SESSION = "session"
SCOPE_TASK = "task"
SCOPE_UNTIL = "until"
SCOPE_ALWAYS = "always"
SCOPES = (SCOPE_ONCE, SCOPE_SESSION, SCOPE_TASK, SCOPE_UNTIL, SCOPE_ALWAYS)

# 9.4 check 4.
MAX_EXTERNAL_SENDS_PER_HOUR = 20
MAX_SENDS_PER_RECIPIENT_PER_DAY = 3

_GOOGLE_CLASSES = {
    ("gmail", "users.getProfile"): READ,
    ("gmail", "users.messages.list"): READ,
    ("gmail", "users.messages.get"): READ,
    ("gmail", "users.messages.attachments.get"): READ,
    ("gmail", "users.threads.list"): READ,
    ("gmail", "users.threads.get"): READ,
    ("gmail", "users.history.list"): READ,
    ("gmail", "users.labels.list"): READ,
    ("gmail", "users.messages.modify"): INTERNAL_WRITE,
    ("gmail", "users.watch"): INTERNAL_WRITE,
    ("gmail", "users.stop"): INTERNAL_WRITE,
    ("calendar", "calendarList.list"): READ,
    ("calendar", "events.list"): READ,
    ("calendar", "events.get"): READ,
    ("calendar", "events.delete"): DESTRUCTIVE,
}


@dataclass(frozen=True)
class Action:
    connector: str
    method: str
    cls: str
    destinations: Tuple[str, ...] = ()
    skill: str = ""
    user: str = ""
    run_id: str = ""
    session_id: str = ""
    task_id: str = ""
    tainted: bool = True
    source_kind: str = ""
    source_id: str = ""


@dataclass
class Grant:
    id: str
    user: str
    connector: str
    skill: str
    destination: str
    scope: str
    session_id: str = ""
    task_id: str = ""
    expires_at: Optional[datetime] = None
    used: bool = False


@dataclass(frozen=True)
class Decision:
    verdict: str
    reason: str
    cls: str
    mode: str
    grant_id: Optional[str] = None


@dataclass
class Rates:
    external_sends_last_hour: int = 0
    per_recipient_today: Dict[str, int] = field(default_factory=dict)


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def is_internal(address: str, org_domains: Iterable[str]) -> bool:
    d = _domain(address)
    return bool(d) and d in {x.lower().lstrip("@") for x in org_domains}


class UnparseableRecipients(ValueError):
    pass


def parse_recipients(values: Iterable[str]) -> Tuple[str, ...]:
    """Lowercased addresses from header values (To, Cc, Bcc), each parsed on
    its own: Python's strict getaddresses() turns a whole list into one empty
    address if any element is empty or malformed. A non-empty value that
    yields no clean address raises; the caller refuses the send."""
    out = set()
    for v in values:
        v = str(v or "").strip()
        if not v:
            continue
        got = getaddresses([v])
        if not got or any("@" not in a for _, a in got):
            raise UnparseableRecipients(f"cannot parse recipient header ({len(v)} chars)")
        out.update(a.strip().lower() for _, a in got)
    return tuple(sorted(out))


def recipients_from_headers(headers: Dict[str, str]) -> Tuple[str, ...]:
    return parse_recipients(headers.get(k, "") for k in ("To", "Cc", "Bcc"))


def classify_google(api: str, method: str, params: dict, org_domains: Sequence[str],
                    recipients: Sequence[str] = ()) -> Tuple[str, Tuple[str, ...]]:
    """(action class, destinations) for one broker call.

    `recipients` is what the broker parsed from a send's raw message. A method
    this table does not know is `locked`: it can never run without a human.
    """
    if (api, method) == ("gmail", "users.messages.send"):
        dests = tuple(sorted({r.lower() for r in recipients}))
        if not dests:
            return LOCKED, ()
        external = [r for r in dests if not is_internal(r, org_domains)]
        return (EXTERNAL_SEND if external else INTERNAL_SEND), dests
    if (api, method) in (("calendar", "events.insert"), ("calendar", "events.patch")):
        body = params.get("body") or {}
        attendees = tuple(sorted({str(a.get("email", "")).lower() for a in body.get("attendees") or []
                                  if a.get("email")}))
        notifies = str(params.get("sendUpdates") or "none") in ("all", "externalOnly")
        external = [a for a in attendees if not is_internal(a, org_domains)]
        if external and notifies:
            return EXTERNAL_SEND, tuple(external)
        if attendees and notifies:
            return INTERNAL_SEND, attendees
        return INTERNAL_WRITE, ()
    return _GOOGLE_CLASSES.get((api, method), LOCKED), ()


def effective_mode(cls: str, user_override: Optional[str] = None,
                   org_ceiling: Optional[str] = None) -> str:
    """The user's choice, never looser than the org ceiling. `locked` ignores
    the user and can only be made stricter (blocked) by the org."""
    if cls not in DEFAULTS:
        return BLOCK
    mode = DEFAULTS[cls]
    if cls != LOCKED and user_override in MODES:
        mode = user_override
    if org_ceiling in MODES and _RANK[org_ceiling] > _RANK[mode]:
        mode = org_ceiling
    return mode


def destination_matches(pattern: str, address: str) -> bool:
    """A grant names one contact (an address) or one recipient domain ('@x.com')."""
    pattern, address = pattern.strip().lower(), address.strip().lower()
    if not pattern or not address:
        return False
    if pattern.startswith("@"):
        return _domain(address) == pattern[1:]
    return pattern == address


def grant_covers(grant: Grant, action: Action, now: datetime) -> bool:
    if action.cls == LOCKED or grant.scope not in SCOPES:
        return False
    if grant.user != action.user or grant.connector != action.connector or grant.skill != action.skill:
        return False
    if not action.destinations or not all(destination_matches(grant.destination, d)
                                          for d in action.destinations):
        return False
    if grant.expires_at is not None and now >= grant.expires_at:
        return False
    if grant.scope == SCOPE_ONCE:
        return not grant.used
    if grant.scope == SCOPE_SESSION:
        return bool(grant.session_id) and grant.session_id == action.session_id
    if grant.scope == SCOPE_TASK:
        return bool(grant.task_id) and grant.task_id == action.task_id
    if grant.scope == SCOPE_UNTIL:
        return grant.expires_at is not None
    return True


def _rate_refusal(action: Action, rates: Rates) -> Optional[str]:
    if action.cls == EXTERNAL_SEND and rates.external_sends_last_hour >= MAX_EXTERNAL_SENDS_PER_HOUR:
        return f"rate limit: {MAX_EXTERNAL_SENDS_PER_HOUR} external sends per hour"
    if action.cls in (INTERNAL_SEND, EXTERNAL_SEND):
        for d in action.destinations:
            if rates.per_recipient_today.get(d, 0) >= MAX_SENDS_PER_RECIPIENT_PER_DAY:
                return f"rate limit: {MAX_SENDS_PER_RECIPIENT_PER_DAY} sends per recipient per day ({d})"
    return None


def decide(action: Action, *, mode: str, grants: Sequence[Grant] = (), now: datetime,
           rates: Optional[Rates] = None, boundary_refusal: Optional[str] = None) -> Decision:
    """The deterministic verdict. Order: unknown class, source record,
    boundary, rate limits, blocked mode, then grants, auto and taint."""
    def out(verdict, reason, grant=None):
        return Decision(verdict, reason, action.cls, mode, grant.id if grant else None)

    if action.cls not in CLASSES or mode not in MODES:
        return out(DENY, f"unknown class or mode ({action.cls}, {mode})")
    if action.cls in WRITE_CLASSES and not (action.source_kind and action.source_id):
        return out(DENY, "no source record: every write names the record it acts on (spec 6)")
    if boundary_refusal:
        return out(DENY, f"recipient boundary: {boundary_refusal}")
    refusal = _rate_refusal(action, rates or Rates())
    if refusal:
        return out(DENY, refusal)
    if mode == BLOCK:
        return out(DENY, f"{action.cls} is blocked by policy")
    if action.cls == READ:
        return out(ALLOW, "read")
    grant = next((g for g in grants if grant_covers(g, action, now)), None)
    if mode == AUTO:
        if action.tainted and action.cls in TAINT_SENSITIVE:
            if grant:
                return out(ALLOW, "tainted run, covered by a grant", grant)
            return out(ASK, "tainted run: untrusted content was read; auto is off for this action")
        return out(ALLOW, f"{action.cls} is auto")
    if grant:
        return out(ALLOW, "covered by a grant", grant)
    if action.cls == LOCKED:
        return out(ASK, "locked: always needs a fresh approval")
    return out(ASK, f"{action.cls} needs approval")


def grant_from_approval(approval_id: str, action: Action, scope: str, now: datetime,
                        duration: Optional[timedelta] = None) -> Grant:
    """The grant an approval creates, bound to one connector, one destination
    and one skill. Several destinations would be several grants; refused here
    so one tap cannot quietly widen to a group."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    if action.cls == LOCKED and scope != SCOPE_ONCE:
        raise ValueError("locked actions take a one-time approval only")
    if len(action.destinations) != 1:
        raise ValueError("a grant binds exactly one destination")
    if scope == SCOPE_UNTIL and not duration:
        raise ValueError("scope 'until' needs a duration")
    return Grant(id=f"grant-{approval_id}", user=action.user, connector=action.connector,
                 skill=action.skill, destination=action.destinations[0], scope=scope,
                 session_id=action.session_id if scope == SCOPE_SESSION else "",
                 task_id=action.task_id if scope == SCOPE_TASK else "",
                 expires_at=(now + duration) if scope == SCOPE_UNTIL else None)


# ── structured approval callbacks (9.3) ─────────────────────────────────────

CALLBACK_VERSION = "v1"
CALLBACK_ANSWERS = ("approve", "reject")


class CallbackRefused(ValueError):
    pass


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_callback(secret: bytes, *, approval_id: str, user: str, answer: str, scope: str,
                  expires_at: datetime) -> str:
    """The token a PWA card or Slack/WhatsApp button carries back. It names
    the approval, the user it was issued to, the answer and the grant scope."""
    if answer not in CALLBACK_ANSWERS:
        raise ValueError(f"answer must be one of {CALLBACK_ANSWERS}")
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    body = json.dumps({"a": approval_id, "u": user, "ans": answer, "s": scope,
                       "exp": int(expires_at.timestamp())}, separators=(",", ":"), sort_keys=True).encode()
    mac = hmac.new(secret, CALLBACK_VERSION.encode() + b"." + body, hashlib.sha256).digest()
    return f"{CALLBACK_VERSION}.{_b64(body)}.{_b64(mac)}"


def verify_callback(secret: bytes, token: str, *, now: datetime, user: str) -> dict:
    """The decoded answer, or CallbackRefused. `user` is the session that
    delivered the callback: a token issued to someone else does not count."""
    try:
        version, body_b64, mac_b64 = str(token).split(".")
        body, mac = _unb64(body_b64), _unb64(mac_b64)
    except Exception as exc:
        raise CallbackRefused("malformed callback") from exc
    if version != CALLBACK_VERSION:
        raise CallbackRefused("unknown callback version")
    want = hmac.new(secret, version.encode() + b"." + body, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, want):
        raise CallbackRefused("bad signature")
    data = json.loads(body)
    if int(data.get("exp", 0)) <= int(now.timestamp()):
        raise CallbackRefused("expired")
    if data.get("u") != user:
        raise CallbackRefused("issued to a different user")
    if data.get("ans") not in CALLBACK_ANSWERS or data.get("s") not in SCOPES:
        raise CallbackRefused("bad answer or scope")
    return {"approval_id": data["a"], "user": data["u"], "answer": data["ans"], "scope": data["s"]}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def summarize(action: Action, decision: Decision) -> dict:
    """Structure for the ledger: who, what class, where, verdict. No bodies."""
    return {"connector": action.connector, "method": action.method, "class": action.cls,
            "destinations": list(action.destinations), "skill": action.skill, "user": action.user,
            "run_id": action.run_id, "session_id": action.session_id, "task_id": action.task_id,
            "tainted": action.tainted, "source_kind": action.source_kind, "source_id": action.source_id,
            "verdict": decision.verdict, "mode": decision.mode, "reason": decision.reason,
            "grant_id": decision.grant_id}


def untrusted_sender(headers: List[dict], org_domains: Sequence[str]) -> bool:
    """True when a Gmail message's From is outside the org (9.5: an email body
    from outside the org taints the run). No From header counts as untrusted."""
    frm = next((h.get("value", "") for h in headers or [] if str(h.get("name", "")).lower() == "from"), "")
    addrs = [a for _, a in getaddresses([frm]) if "@" in a]
    return not addrs or not all(is_internal(a, org_domains) for a in addrs)
