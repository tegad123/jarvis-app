"""Passphrase login and signed session cookies.

One shared passphrase (APP_PASSPHRASE) is exchanged for a signed, timestamped
cookie (APP_SESSION_SECRET). The passphrase itself is never stored in the
cookie — only an opaque session payload — so a captured cookie expires on its
own and cannot be replayed into a new session after APP_SESSION_SECRET rotates.

This is v1 auth for a single-operator app. Per-tenant user accounts land with
the customer app; the interface here (`current_session` as a FastAPI
dependency) does not change when they do.
"""

import hmac
import time
import uuid
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import Cookie, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import config

_SALT = "jarvis-app-session-v1"


def _serializer() -> URLSafeTimedSerializer:
    # Resolved per call so a rotated APP_SESSION_SECRET takes effect on restart
    # without the value being captured at import time.
    return URLSafeTimedSerializer(config.require("APP_SESSION_SECRET"), salt=_SALT)


# ---------------------------------------------------------------- throttling

_failures: Dict[str, Deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    """Best-effort client identity for throttling.

    Behind cloudflared every request arrives from 127.0.0.1, so the forwarded
    header is the only thing with any discriminating power. It is spoofable —
    this is a rate limit, not an authorization decision, and the throttle is
    deliberately cheap to keep that distinction honest.
    """
    fwd = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune(bucket: Deque[float], now: float) -> None:
    while bucket and now - bucket[0] > config.LOGIN_WINDOW_SECONDS:
        bucket.popleft()


def throttled(request: Request) -> bool:
    now = time.time()
    bucket = _failures[_client_ip(request)]
    _prune(bucket, now)
    return len(bucket) >= config.LOGIN_MAX_FAILURES


def record_failure(request: Request) -> None:
    now = time.time()
    bucket = _failures[_client_ip(request)]
    _prune(bucket, now)
    bucket.append(now)


def clear_failures(request: Request) -> None:
    _failures.pop(_client_ip(request), None)


# --------------------------------------------------------------------- login

def verify_pin(person_key: str, pin: str):
    """Per-person sign-in. Returns the account or None."""
    from . import accounts
    return accounts.verify(person_key, pin)


def operator_passphrases() -> Dict[str, str]:
    """label -> passphrase. APP_PASSPHRASE is the primary (label from
    APP_OPERATOR_LABEL, default "operator"); APP_OPERATOR_PASSPHRASES adds
    named ones as `label=passphrase;label=passphrase` — one per person doing
    support, so the journal and the feed can say WHICH operator acted. Every
    one is the same break-glass role; the label is attribution, not rank."""
    out = {config.get("APP_OPERATOR_LABEL", "operator") or "operator": config.require("APP_PASSPHRASE")}
    extra = config.get("APP_OPERATOR_PASSPHRASES", "") or ""
    for pair in extra.split(";"):
        pair = pair.strip()
        if "=" in pair:
            label, value = pair.split("=", 1)
            if label.strip() and value.strip():
                out[label.strip()] = value.strip()
    return out


def verify_passphrase(candidate: str):
    """The operator LABEL the candidate matches, or None. Constant-time per
    entry; every entry is compared so timing does not reveal which exists."""
    matched = None
    for label, expected in operator_passphrases().items():
        if hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8")):
            matched = label
    return matched


def issue_cookie(response: Response, person_key: str = None, role: str = None,
                 operator: str = None) -> dict:
    """Mint a session cookie.

    A session now carries WHO it is. `person_key` is the join to
    identity.people and to every record this product writes; `role` is what they
    may see. A session with person_key=None and role="operator" is the
    BREAK-GLASS path (the shared APP_PASSPHRASE) — deliberately distinguishable,
    so nothing downstream can mistake support access for the owner.
    """
    payload = {
        "sid": uuid.uuid4().hex,
        "iat": int(time.time()),
        "person_key": person_key,
        "role": role or ("operator" if person_key is None else "member"),
    }
    if operator:
        payload["operator"] = operator
    token = _serializer().dumps(payload)
    response.set_cookie(
        config.COOKIE_NAME,
        token,
        max_age=config.SESSION_MAX_AGE,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return payload


def clear_cookie(response: Response) -> None:
    response.delete_cookie(config.COOKIE_NAME, path="/")


# ---------------------------------------------------------------- dependency

def current_session(
    jarvis_session: Optional[str] = Cookie(default=None, alias=config.COOKIE_NAME),
) -> dict:
    """FastAPI dependency. Raises 401 unless a valid, unexpired cookie is present."""
    if not jarvis_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Cookie"},
        )
    try:
        return _serializer().loads(jarvis_session, max_age=config.SESSION_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Session expired.")
    except BadSignature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid session.")


# ── role gates ────────────────────────────────────────────────────────────────
#
# THE API ENFORCES; THE UI ONLY REFLECTS. A hidden button is a courtesy, not a
# control — every one of these is checked server-side so a crafted request is
# refused with 403 rather than quietly served.

def role_of(sess: dict) -> str:
    return (sess or {}).get("role") or "member"


def person_of(sess: dict):
    return (sess or {}).get("person_key")


def operator_of(sess: dict):
    """Which operator credential signed in ("operator", "bao", …), or None
    for a person session."""
    return sess.get("operator") if is_operator(sess) else None


def is_operator(sess: dict) -> bool:
    """Break-glass session: full READ access for support, not an owner."""
    return role_of(sess) == "operator"


def sees_everything(sess: dict) -> bool:
    """Owner, admin, and break-glass operator reach every channel.

    REACH IS NOT READ. For the operator this grants STRUCTURE only — see
    require_bodies() immediately below, which is what stops the same session
    from opening the messages.
    """
    return role_of(sess) in ("owner", "admin", "operator")


def require_bodies(sess: dict) -> dict:
    """Message CONTENT. Everything except the break-glass operator.

    STRUCTURE, NOT BODIES. An operator session lists channels, sees who is in
    them, reads metadata and the activity feed — and gets 403 on the messages
    themselves.

    Three reasons this line is drawn here:

      * break-glass is the WEAKEST credential in the system. It is one shared
        passphrase, it belongs to no person, and it is the one an operator is
        most likely to have typed into a support ticket. The thing it unlocks
        should be correspondingly small.
      * support needs are STRUCTURAL. "Is the channel there, is she in it, did
        the event fire" is answerable from structure. Almost nothing an
        operator legitimately debugs requires reading what the customer said.
      * when body access is genuinely needed, the route is an owner PIN reset
        WITH THE OWNER'S CONSENT — accounts_cli set-pin, then the owner signs
        in. That is auditable and the customer knows it happened. Quietly
        reading their conversations from a support session is not.
    """
    if is_operator(sess):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator access sees channels and membership, not message "
                   "content. Reading messages needs the owner to sign in.",
        )
    return sess


def require_owner(sess: dict) -> dict:
    """Owner-only surfaces: approvals, settings, ground rules, billing.

    The break-glass operator is NOT an owner. It can read the tenant's world for
    support; it must not approve an outbound email on their behalf.
    """
    if role_of(sess) != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the account owner can do that.",
        )
    return sess


def require_person(sess: dict) -> dict:
    """ACTING ON TENANT DATA. Any role, but it must be a PERSON.

    The gate is on identity, not seniority: owner, admin and member may all
    create and edit leads, and the break-glass operator may not — because it is
    not anybody. A lead edit is a claim about the tenant's business ("this one
    went cold"), and a claim needs someone to have made it. `decided_by`,
    `created_by` and `owner_person` all want a real key; an operator has none,
    so the record would say a change happened and be unable to say who by.

    THE SAME LINE AS require_bodies, ONE STEP FURTHER. That one says an
    operator may not READ the tenant's conversations. This one says it may not
    ACT on their data either: break-glass observes, it does not participate.
    Support questions are answered by looking; anything that changes what the
    customer sees goes through someone who works there.

    Note this is deliberately NOT require_admin. A member editing their own
    lead is the normal case and the whole point of W1's app-native store —
    seniority is the wrong axis here, personhood is the right one.
    """
    if person_of(sess) is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator access can look, not change. Sign in as yourself to edit leads.",
        )
    return sess


def require_task_admin(sess: dict) -> dict:
    """Admin or owner: EDITING, ASSIGNING and ARCHIVING a task.

    A separate gate from require_admin because the sentence has to name the
    thing being refused — "manage members" is not what happened when someone
    tried to reassign a task, and an error that describes the wrong action is
    how a member concludes the app is broken rather than that they lack a
    permission.

    THIS IS THE SECOND GATE, NEVER THE FIRST. Every task write calls
    require_person first, so a break-glass operator is refused for not being
    anybody before seniority is ever consulted. The order matters: an operator
    told "only an admin can do that" would reasonably go and get an admin's
    credentials, and the answer would still be no.

    Note what is NOT here: changing a task's STATUS. Marking your own work done
    is the normal case for a member, and gating it on seniority would make the
    shared list a list you can read and not use. See main._may_change_status.
    """
    if role_of(sess) not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin or the owner can edit, assign or archive tasks.",
        )
    return sess


def require_admin(sess: dict) -> dict:
    """Admin or owner: channel and member management."""
    if role_of(sess) not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin or the owner can manage members.",
        )
    return sess
