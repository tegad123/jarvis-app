"""Jarvis app backend — v1 skeleton.

Binds 127.0.0.1:8700. Public reachability is provided entirely by a cloudflared
tunnel unit, so the socket is never itself exposed; if the tunnel stops, the app
stops being reachable rather than falling back to an open port.

Endpoints
    POST /api/login    passphrase -> signed session cookie
    POST /api/logout   clear cookie
    GET  /api/session  who am I / is my cookie still good
    GET  /api/feed     activity events via _shared/notify.read_feed()   [auth]
    GET  /api/feed/summary  today's counts by kind over the whole feed   [auth]
    GET  /api/health   real gateway/feed/sheets checks                  [auth]
    GET  /api/ping     unauthenticated liveness, no internal detail

/api/health is authenticated on purpose: it reports subsystem detail (gateway
version, credential scopes, tenant slugs) and is reachable from the internet.
/api/ping is the endpoint for uptime monitoring.
"""

import copy
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import (  # noqa: E402
    Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, status,
)
from fastapi.responses import JSONResponse  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402

from app import approvals as approvals_mod  # noqa: E402
from app import sentinel as sentinel_mod  # noqa: E402
from app import attachments as attachments_mod  # noqa: E402
from app import accounts as accounts_mod  # noqa: E402
from app import google_connection as google_conn_mod  # noqa: E402
from app import google_oauth  # noqa: E402
from app import auth, chat as chat_mod, config, health, whoami  # noqa: E402
from app import feed as feed_mod  # noqa: E402
from app import leads as leads_mod  # noqa: E402
from app import leads_source as leads_src  # noqa: E402
from app import leads_store  # noqa: E402
from app import tasks_source as tasks_src  # noqa: E402
from app import tasks_store  # noqa: E402
from app import threads as threads_mod  # noqa: E402
from app import transcribe as transcribe_mod  # noqa: E402

APP_VERSION = "1.0.0-skeleton"

# Built SPA (Vite writes here from frontend/, see frontend/vite.config.ts).
# Resolved but not required at import time: the API must still boot on a box
# where the frontend has never been built.
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

app = FastAPI(
    title="Jarvis App API",
    version=APP_VERSION,
    docs_url=None,      # no interactive docs on a publicly-reachable surface
    redoc_url=None,
    openapi_url=None,
)

from app import broker as broker_mod  # noqa: E402

if os.environ.get("JARVIS_APP_BROKER_URL"):
    # Broker mode makes app.threads send counters to the broker, which is this
    # process: every bump would call itself.
    raise SystemExit("REFUSED: JARVIS_APP_BROKER_URL is set on the app itself; it belongs on the gateway only")
if broker_mod.sentinel_mode() not in ("enforce", broker_mod.SHADOW):
    # A misspelt "shadow" must not quietly run as enforce.
    raise SystemExit("REFUSED: JARVIS_SENTINEL_MODE must be enforce or shadow")
from app import switches as switches_mod  # noqa: E402
from app import platform_db  # noqa: E402

try:
    SWITCHES = switches_mod.state()
except switches_mod.BadSwitch as _exc:
    raise SystemExit(f"REFUSED: {_exc}")
if SWITCHES["sentinel"]:
    # Sentinel's policies, grants, approvals and audit log live here; without
    # them every write would be undecidable, so the app does not start.
    try:
        PLATFORM_DB = platform_db.check()
    except platform_db.Unavailable as _exc:
        raise SystemExit(f"REFUSED: Sentinel is on but {_exc}")
else:
    PLATFORM_DB = None
app.include_router(broker_mod.router)
from app import model_proxy as model_proxy_mod  # noqa: E402
app.include_router(model_proxy_mod.router)


def _uvicorn_log_config() -> dict:
    """uvicorn's default dictConfig never attaches a handler to root.

    app.* loggers (google_oauth, google_connection, accounts, ...) propagate
    to root. Root has no handlers and WARNING level, so INFO announcements
    vanish — journald only shows uvicorn access lines. Reuse uvicorn's stderr
    handler on root at INFO so systemd sees application logs.
    """
    from uvicorn.config import LOGGING_CONFIG
    cfg = copy.deepcopy(LOGGING_CONFIG)
    cfg["root"] = {"handlers": ["default"], "level": "INFO"}
    return cfg


@app.on_event("startup")
def _announce_app_logging() -> None:
    lg = logging.getLogger("app")
    lg.info("app logging wired to stderr at INFO")
    lg.warning("switches: %s; sentinel mode %s; platform db %s",
               " ".join(f"{k}={'on' if v else 'off'}" for k, v in SWITCHES.items()),
               broker_mod.sentinel_mode(), PLATFORM_DB or "not used (sentinel off)")


class LoginRequest(BaseModel):
    """Either a per-person sign-in, or the break-glass operator passphrase.

    person_key + pin  -> a PERSON session (this is the normal path)
    passphrase only   -> an OPERATOR session (support/recovery, no person)
    """
    person_key: Optional[str] = Field(None, max_length=64)
    pin: Optional[str] = Field(None, max_length=64)
    passphrase: Optional[str] = Field(None, max_length=512)


def _viewer(sess: dict):
    """THE SCOPING ARGUMENT for the feed and the leads list, in one place.

    None means "no WHERE clause" and is returned only for sessions entitled to
    the whole tenant — owner, admin, break-glass operator. Anyone else gets
    their person key, and every scoped read takes it verbatim.

    Deliberately one function: the failure mode this guards against is a new
    handler re-deriving the rule slightly differently and quietly widening it.

    THREADS DO NOT USE THIS. They need both facts at once — who you are AND
    whether you are unscoped — because a direct line is private to its person
    even from a session that sees everything else. See _thread_scope().
    """
    return None if auth.sees_everything(sess) else auth.person_of(sess)


def _tasks_writable() -> None:
    """App-native tenants only, same shape as the leads write gate."""
    if tasks_src.source() != "store":
        raise HTTPException(status_code=409, detail=tasks_src.LEGACY_SENTENCE)


def _task_or_404(task_id: str) -> dict:
    t = tasks_store.get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="No such task.")
    return t


def _may_change_status(sess: dict, task: dict) -> dict:
    """WHO CAN SAY A PIECE OF WORK MOVED: the person doing it, or someone
    senior enough to be accountable for the list.

    The assignee is in here because a shared task list whose members cannot
    tick their own work off is a list they will stop using. Admin and owner are
    in here because work gets finished by people who then forget to say so, and
    somebody has to be able to close it.

    require_person first, deliberately: a break-glass operator has no person
    key, so it can never match the assignee branch, and it must be refused for
    not being anybody rather than for lacking a rank.
    """
    auth.require_person(sess)
    if auth.role_of(sess) in ("owner", "admin"):
        return sess
    if task.get("assignee") and task["assignee"] == auth.person_of(sess):
        return sess
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only the person a task is assigned to, an admin, or the owner "
               "can change its status.",
    )


def _thread_scope(sess: dict):
    """(viewer, unscoped) for the thread store. See threads.list_threads."""
    return auth.person_of(sess), auth.sees_everything(sess)


def _reach_thread(thread_id: str, sess: dict) -> dict:
    """Fetch a thread this session may REACH, else 404.

    404, not 403, when a member asks for a channel they are not in: a 403 would
    confirm the channel exists. Reach is not read — call auth.require_bodies()
    as well before returning any message content.
    """
    t = threads_mod.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail="No such conversation.")
    viewer, unscoped = _thread_scope(sess)
    if not threads_mod.can_see(thread_id, viewer, unscoped):
        raise HTTPException(status_code=404, detail="No such conversation.")
    return t


@app.get("/api/ping")
def ping():
    """Unauthenticated liveness. Deliberately reveals nothing but 'alive'."""
    return {"ok": True, "service": "jarvis-app", "version": APP_VERSION}


@app.get("/api/people")
def get_people():
    """Sign-in roster. UNAUTHENTICATED on purpose — the login screen needs it.

    Deliberately minimal: person key, display name, title. NO email addresses,
    no roles, no account state. Enough to render a picker for a small team on a
    private deployment, and nothing that turns the login page into a directory.
    """
    try:
        return {"people": [
            {"person_key": r["person_key"], "name": r["name"], "title": r["title"]}
            for r in accounts_mod.roster() if r["has_pin"] and not r["disabled"]
        ]}
    except Exception:
        return {"people": []}


@app.post("/api/login")
def login(body: LoginRequest, request: Request, response: Response):
    if auth.throttled(request):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again shortly.",
        )
    # ── per-person sign-in ────────────────────────────────────────────────
    if body.person_key:
        if auth.person_locked(body.person_key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many wrong PINs. Sign-in is locked for {auth.lock_duration_text()}.",
            )
        acct = auth.verify_pin(body.person_key, body.pin or "")
        if not acct:
            auth.record_failure(request)
            if auth.record_pin_failure(body.person_key):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many wrong PINs. Sign-in is locked for {auth.lock_duration_text()}.",
                )
            # One message for "no such person", "wrong PIN" and "disabled": a
            # login form that distinguishes them tells an attacker which person
            # keys are real.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="That PIN is not right.")
        auth.clear_failures(request)
        auth.clear_pin_failures(body.person_key)
        # Their private line, created on first sight rather than at account
        # creation — a person added to the registry outside the app still gets
        # one the moment they sign in.
        threads_mod.ensure_direct(acct["person_key"])
        session = auth.issue_cookie(response, person_key=acct["person_key"], role=acct["role"])
        return {"ok": True, "session": {"iat": session["iat"]},
                "person_key": acct["person_key"], "role": acct["role"],
                "expires_in": config.SESSION_MAX_AGE}

    # ── break-glass operator sign-in ──────────────────────────────────────
    if not body.passphrase:
        raise HTTPException(status_code=422, detail="Choose who you are, then enter your PIN.")
    try:
        valid = auth.verify_passphrase(body.passphrase)
    except RuntimeError as exc:      # APP_PASSPHRASE not configured
        raise HTTPException(status_code=500, detail=str(exc))

    if not valid:
        auth.record_failure(request)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid passphrase.")

    auth.clear_failures(request)
    session = auth.issue_cookie(response, person_key=None, role="operator", operator=valid)
    return {"ok": True, "session": {"iat": session["iat"]},
            "person_key": None, "role": "operator", "operator": valid,
            "expires_in": config.SESSION_MAX_AGE}


@app.get("/api/auth/google/start")
def google_login_start(request: Request, response: Response):
    """OpenID staff sign-in. Sets a signed state cookie, redirects to Google or the mock."""
    return google_oauth.start_login(request, response)


@app.get("/api/auth/google/mock/authorize")
def google_mock_authorize(
    request: Request,
    state: Optional[str] = Query(None),
    email: Optional[str] = Query(None),
    purpose: Optional[str] = Query(None),
    grant: Optional[str] = Query(None),
):
    return google_oauth.mock_authorize(request, email, state, purpose, grant)


@app.post("/api/auth/google/mock/token")
async def google_mock_token(request: Request):
    if not config.oauth_mock():
        raise HTTPException(status_code=404, detail="Not found.")
    form = await request.form()
    code = str(form.get("code") or "")
    try:
        return google_oauth.mock_token_response(code)
    except KeyError:
        raise HTTPException(status_code=400, detail="That PIN is not right.")


@app.get("/oauth/callback")
def oauth_callback(request: Request, response: Response):
    """Google redirect. Dispatches on signed-state purpose (login vs connect)."""
    return google_oauth.callback(request, response)


@app.get("/api/google/connect/start")
def google_connect_start(
    request: Request,
    response: Response,
    sess: dict = Depends(auth.current_session),
):
    """Five-scope tenant connect. Owner or admin person session required."""
    auth.require_person(sess)
    auth.require_admin(sess)
    return google_oauth.start_connect(request, response, sess)


@app.get("/api/google/connection")
def google_connection_get(sess: dict = Depends(auth.current_session)):
    """Status only. No token, refresh, client_secret, or cipher fields."""
    auth.require_person(sess)
    auth.require_admin(sess)
    return google_conn_mod.status_public()


@app.post("/api/google/connection/disconnect")
def google_connection_disconnect(sess: dict = Depends(auth.current_session)):
    auth.require_person(sess)
    auth.require_admin(sess)
    return google_oauth.disconnect(sess)


@app.post("/api/logout")
def logout(response: Response):
    auth.clear_cookie(response)
    return {"ok": True}


@app.get("/api/session")
def session(sess: dict = Depends(auth.current_session)):
    body = {"ok": True, "authenticated": True, "issued_at": sess.get("iat"),
            "person_key": auth.person_of(sess), "role": auth.role_of(sess)}
    if auth.operator_of(sess):
        body["operator"] = auth.operator_of(sess)
    # THE NAME OF WHOEVER IS SIGNED IN, resolved through the identity registry
    # and never hardcoded. Omitted when unresolvable so the client falls back
    # to "Hi there" — which is also what a break-glass operator gets, because
    # that session is not a person and must not borrow one's name.
    person = auth.person_of(sess)
    name = whoami.display_name_for(person) if person else None
    if name:
        body["display_name"] = name
    return body


@app.get("/api/feed")
def get_feed(
    sess: dict = Depends(auth.current_session),
    limit: int = Query(50, ge=1, le=1000),
    skill: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    destination: Optional[str] = Query(None),
    since: Optional[str] = Query(None, description="ISO-8601 ts; returns events strictly newer"),
):
    return feed_mod.query(limit=limit, skill=skill, kind=kind,
                          destination=destination, since=since, viewer=_viewer(sess))


@app.get("/api/feed/summary")
def get_feed_summary(
    sess: dict = Depends(auth.current_session),
    tz: Optional[str] = Query(None, max_length=64,
                              description="Caller's IANA timezone, e.g. America/Chicago. "
                                          "Invalid or absent -> UTC."),
):
    """Today's counts by kind over the WHOLE feed — the hero's source.

    Registered after /api/feed but the paths are distinct, so ordering is not
    load-bearing here.
    """
    return feed_mod.summary(tz_name=tz, viewer=_viewer(sess))


class ChatRequest(BaseModel):
    thread_id: str = Field(chat_mod.DIRECT_THREAD, min_length=1, max_length=64)
    # May be empty when attachments carry the turn — sending a photo with no
    # caption is a normal thing to do. The handler rejects wholly empty turns.
    message: str = Field("", max_length=8000)
    attachments: List[str] = Field(default_factory=list, max_length=10)


class ThreadCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class ThreadRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


@app.post("/api/chat")
def post_chat(body: ChatRequest, sess: dict = Depends(auth.current_session)):
    # A thread must exist and be live before it can take a message. Archived
    # threads are readable but closed to new turns — otherwise "archive" would
    # be a suggestion rather than a state.
    auth.require_bodies(sess)
    thread = _reach_thread(body.thread_id, sess)
    if thread["archived"]:
        raise HTTPException(status_code=409, detail="That channel is archived.")

    if not body.message.strip() and not body.attachments:
        raise HTTPException(status_code=422, detail="Nothing to send.")

    # Every referenced attachment must exist AND belong to this thread —
    # otherwise an id from another conversation could be replayed into this one.
    for att_id in body.attachments:
        rec = attachments_mod.get(att_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Attachment not found.")
        if rec["thread_id"] != body.thread_id:
            raise HTTPException(
                status_code=403, detail="That attachment belongs to another conversation."
            )

    return StreamingResponse(
        chat_mod.stream_chat(body.thread_id, body.message, body.attachments,
                             person_key=auth.person_of(sess), role=auth.role_of(sess)),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            # Stop any reverse proxy (cloudflared included) from buffering the
            # stream into one lump, which would defeat progressive rendering.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/chat/history")
def get_chat_history(
    sess: dict = Depends(auth.current_session),
    thread_id: str = Query(chat_mod.DIRECT_THREAD, min_length=1, max_length=64),
):
    """Durable per-thread transcript. Survives reload AND service restart."""
    auth.require_bodies(sess)
    _reach_thread(thread_id, sess)
    messages = chat_mod.get_history(thread_id)
    # Resolve attachment ids to full records so a reload can re-render chips
    # without one request per attachment.
    for m in messages:
        ids = m.get("attachments") or []
        m["attachments"] = attachments_mod.public_many(ids) if ids else []
    return {"thread_id": thread_id, "messages": messages, "count": len(messages)}


# ------------------------------------------------------------------- threads


@app.get("/api/threads")
def get_threads(
    sess: dict = Depends(auth.current_session),
    include_archived: bool = Query(False),
):
    viewer, unscoped = _thread_scope(sess)
    items = threads_mod.list_threads(include_archived=include_archived,
                                     viewer=viewer, unscoped=unscoped)
    # The list screen shows a preview line per row; fetching it here keeps the
    # client to one request instead of one per channel.
    #
    # THE PREVIEW IS A MESSAGE BODY. It is the one place structure and content
    # meet, so the operator gate applies to it too — an operator sees the row,
    # the name, the timestamp and the membership, and no text from inside.
    bodies = not auth.is_operator(sess)
    for t in items:
        last = threads_mod.last_message(t["thread_id"]) if bodies else None
        t["preview"] = last["content"][:160] if last else None
        t["preview_role"] = last["role"] if last else None
        t["members"] = [m["person_key"] for m in threads_mod.members(t["thread_id"])]
    counts = threads_mod.counts() if unscoped else {
        "active": len(items),
        "archived": 0,
        "channels": sum(1 for t in items if not t["is_direct"]),
        "messages": 0,
    }
    return {"threads": items, "count": len(items), "counts": counts,
            "bodies_visible": bodies}


@app.post("/api/threads", status_code=201)
def post_thread(body: ThreadCreate, sess: dict = Depends(auth.current_session)):
    try:
        # created_by='user' — the UI shows the spark mark only for channels
        # Jarvis opened, which is what that mark means.
        return {"ok": True, "thread": threads_mod.create_thread(
            body.name, created_by="user", creator=auth.person_of(sess))}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.patch("/api/threads/{thread_id}")
def patch_thread(
    thread_id: str, body: ThreadRename, sess: dict = Depends(auth.current_session)
):
    _reach_thread(thread_id, sess)
    try:
        return {"ok": True, "thread": threads_mod.rename_thread(thread_id, body.name)}
    except threads_mod.ThreadProtected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except threads_mod.ThreadNotFound:
        raise HTTPException(status_code=404, detail="No such conversation.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/threads/{thread_id}/archive")
def post_thread_archive(
    thread_id: str,
    sess: dict = Depends(auth.current_session),
    archived: bool = Query(True, description="false un-archives"),
):
    """Archive is a FLAG. Nothing is ever hard-deleted — see app/threads.py."""
    _reach_thread(thread_id, sess)
    try:
        return {"ok": True, "thread": threads_mod.set_archived(thread_id, archived)}
    except threads_mod.ThreadProtected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except threads_mod.ThreadNotFound:
        raise HTTPException(status_code=404, detail="No such conversation.")


class MemberAdd(BaseModel):
    person_key: str = Field(..., min_length=1, max_length=64)


@app.get("/api/threads/{thread_id}/members")
def get_thread_members(thread_id: str, sess: dict = Depends(auth.current_session)):
    """Membership is STRUCTURE — readable by anyone who can reach the channel,
    the break-glass operator included. That is the whole point of the split."""
    _reach_thread(thread_id, sess)
    return {"thread_id": thread_id, "members": threads_mod.members(thread_id)}


@app.post("/api/threads/{thread_id}/members", status_code=201)
def post_thread_member(
    thread_id: str, body: MemberAdd, sess: dict = Depends(auth.current_session)
):
    """Add someone to a channel. Admin or owner only.

    An operator cannot do this: adding a member is a change to who can read the
    customer's conversations, and break-glass exists to look, not to grant.
    """
    auth.require_admin(sess)
    _reach_thread(thread_id, sess)
    if body.person_key not in accounts_mod.known_people():
        raise HTTPException(status_code=422, detail="No such person.")
    try:
        return {"ok": True, **threads_mod.add_member(
            thread_id, body.person_key, added_by=auth.person_of(sess))}
    except threads_mod.ThreadProtected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except threads_mod.ThreadNotFound:
        raise HTTPException(status_code=404, detail="No such conversation.")


@app.delete("/api/threads/{thread_id}/members/{person_key}")
def delete_thread_member(
    thread_id: str, person_key: str, sess: dict = Depends(auth.current_session)
):
    auth.require_admin(sess)
    _reach_thread(thread_id, sess)
    return {"ok": True, **threads_mod.remove_member(thread_id, person_key)}


class DecisionRequest(BaseModel):
    decision: Optional[str] = Field(None, pattern="^(approve|reject|approved|rejected)$")
    callback: Optional[str] = Field(None, max_length=2000)


def _approvals_on() -> None:
    if not switches_mod.on("sentinel"):
        raise HTTPException(status_code=503, detail="sentinel_off: approvals are unavailable while Sentinel is off.")


@app.get("/api/approvals")
def get_approvals(
    sess: dict = Depends(auth.current_session),
    status: Optional[str] = Query(None, pattern="^(pending|approved|rejected|expired)$"),
):
    # OWNER-ONLY (spec W3). A member must not even see the queue: it contains
    # draft outbound mail and config changes. Break-glass operator is excluded
    # too — support may READ the tenant's world but must never approve on their
    # behalf.
    auth.require_owner(sess)
    _approvals_on()
    return approvals_mod.query(status=status, person=auth.person_of(sess) or "owner")


@app.post("/api/approvals/{request_id}")
def post_approval(
    request_id: str,
    body: DecisionRequest,
    sess: dict = Depends(auth.current_session),
):
    # OWNER-ONLY, enforced server-side.
    auth.require_owner(sess)
    _approvals_on()
    # Who decided — now a real person, not "whoever held the shared passphrase".
    decided_by = auth.person_of(sess) or "owner"
    decision = {"approve": "approved", "reject": "rejected"}.get((body.decision or "").lower(), body.decision)
    scope, via = "once", "pwa_session"
    if body.callback:
        try:
            answer = approvals_mod.answer_from_callback(request_id, body.callback, decided_by)
        except sentinel_mod.CallbackRefused as exc:
            threads_mod.bump_counter("approvals_callback_refused")
            raise HTTPException(status_code=403, detail=f"Approval callback refused: {exc}.")
        if decision and decision != answer["decision"]:
            raise HTTPException(status_code=409, detail="The decision does not match the signed callback.")
        decision, scope, via = answer["decision"], answer["scope"], "signed_callback"
    if decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="Send the card's signed callback or a decision.")

    try:
        updated = approvals_mod.decide(request_id, decision, decided_by, scope=scope, via=via)
    except LookupError:
        raise HTTPException(status_code=404, detail="No such approval request.")
    except approvals_mod.ConfigChangeRefused as exc:
        # The approval was genuine but the change could not be applied — a
        # forbidden key, or the live value moved since the proposal was written.
        # 409 (conflict), and the request is STILL PENDING: the owner can
        # re-propose against reality. Deliberately distinct from a 422, because
        # nothing about the request was malformed.
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        # Already resolved, or a decision value that got past the pattern.
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "approval": updated}


@app.get("/api/sentinel/held/{action_id}")
def get_held_action(action_id: int, sess: dict = Depends(auth.current_session)):
    """What a held action would have done, in full, so the owner can judge it.
    The feed line and the audit row carry structure only; this carries the
    message. Owner-only: the operator sees structure, never bodies."""
    auth.require_owner(sess)
    _approvals_on()
    from app import platform_store  # noqa: PLC0415
    row = platform_store.get_held(action_id)
    if not row:
        raise HTTPException(status_code=404, detail="No held action with that id.")
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


class LeadCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: Optional[str] = Field(None, max_length=320)
    phone: Optional[str] = Field(None, max_length=64)
    property: Optional[str] = Field(None, max_length=300)
    price_point: Optional[str] = Field(None, max_length=64)
    source: Optional[str] = Field(None, max_length=120)
    notes: Optional[str] = Field(None, max_length=4000)
    status: Optional[str] = Field(None, max_length=40)
    temperature: Optional[str] = Field(None, max_length=20)
    follow_up: Optional[str] = Field(None, max_length=500)
    owner_person: Optional[str] = Field(None, max_length=64)
    thread_id: Optional[str] = Field(None, max_length=64)
    next_touch_at: Optional[str] = Field(None, max_length=40)


class LeadPatch(BaseModel):
    """Every field optional — this is a PARTIAL update by design.

    A whole-row write from the UI would silently revert a change a skill made
    between the read and the save.
    """
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    email: Optional[str] = Field(None, max_length=320)
    phone: Optional[str] = Field(None, max_length=64)
    property: Optional[str] = Field(None, max_length=300)
    price_point: Optional[str] = Field(None, max_length=64)
    source: Optional[str] = Field(None, max_length=120)
    notes: Optional[str] = Field(None, max_length=4000)
    status: Optional[str] = Field(None, max_length=40)
    temperature: Optional[str] = Field(None, max_length=20)
    follow_up: Optional[str] = Field(None, max_length=500)
    owner_person: Optional[str] = Field(None, max_length=64)
    thread_id: Optional[str] = Field(None, max_length=64)
    next_touch_at: Optional[str] = Field(None, max_length=40)


@app.post("/api/leads", status_code=201)
def create_lead(body: LeadCreate, sess: dict = Depends(auth.current_session)):
    """App-native only. Writing our own store is NOT a shared-surface write, so
    it needs no write-guard arming; exporting to a tenant's Sheet does, and that
    lives in the connector."""
    auth.require_person(sess)
    if leads_src.source() != "store":
        raise HTTPException(
            status_code=409,
            detail="This tenant reads leads from a Google Sheet; creating in-app is "
                   "disabled until it is migrated to app-native storage.",
        )
    try:
        return {"ok": True, "lead": leads_store.create(**body.model_dump(exclude_none=True))}
    except leads_store.LeadError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.patch("/api/leads/{lead_id}")
def patch_lead(lead_id: str, body: LeadPatch, sess: dict = Depends(auth.current_session)):
    auth.require_person(sess)
    if leads_src.source() != "store":
        raise HTTPException(status_code=409, detail="Leads are sheet-backed for this tenant.")
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    try:
        return {"ok": True, "lead": leads_store.update(lead_id, **fields)}
    except leads_store.LeadNotFound:
        raise HTTPException(status_code=404, detail="No such lead.")
    except leads_store.LeadError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/leads/{lead_id}/archive")
def archive_lead(lead_id: str, sess: dict = Depends(auth.current_session)):
    auth.require_person(sess)
    if leads_src.source() != "store":
        raise HTTPException(status_code=409, detail="Leads are sheet-backed for this tenant.")
    try:
        return {"ok": True, "lead": leads_store.set_archived(lead_id, True)}
    except leads_store.LeadNotFound:
        raise HTTPException(status_code=404, detail="No such lead.")


@app.get("/api/leads")
def get_leads(sess: dict = Depends(auth.current_session)):
    try:
        return leads_src.load(viewer=_viewer(sess))
    except leads_mod.LeadsUnavailable as exc:
        # 503 with a real body, matching /api/health's contract: the client
        # renders this as an honest error, never as an empty lead list.
        return JSONResponse(
            status_code=503,
            content={
                "error": exc.message,
                "detail": exc.detail,
                "leads": [],
                "count": 0,
            },
        )


# --------------------------------------------------------------------- tasks
#
# THE TASK LIST IS TEAM-VISIBLE. Every member reads every task, which is the
# product's promise ("one shared task list that you and your team can see").
# That is deliberately NOT the leads rule, where a member sees only their own —
# a shared list you can only half see is not shared. The scoping that remains
# is on the FEED, where an event still belongs to whoever the task is for.


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    detail: Optional[str] = Field(None, max_length=4000)
    assignee: Optional[str] = Field(None, max_length=64)
    priority: Optional[str] = Field(None, pattern=r"^(high|medium|low)$")
    deadline: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class TaskPatch(BaseModel):
    """Every field optional — a PARTIAL update, for the same reason as leads."""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    detail: Optional[str] = Field(None, max_length=4000)
    priority: Optional[str] = Field(None, pattern=r"^(high|medium|low)$")
    deadline: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class TaskAssign(BaseModel):
    """assignee=null unassigns. Explicit, because "no assignee" is a real
    decision and must not require deleting and recreating the task."""
    assignee: Optional[str] = Field(None, max_length=64)


class TaskStatus(BaseModel):
    status: str = Field(..., pattern=r"^(open|in_progress|done)$")


@app.get("/api/tasks")
def get_tasks(sess: dict = Depends(auth.current_session),
              status_: Optional[str] = Query(None, alias="status"),
              priority: Optional[str] = Query(None),
              assignee: Optional[str] = Query(None),
              stale: Optional[bool] = Query(None),
              archived: bool = Query(False)):
    # STRUCTURE, NOT BODIES — the same line drawn for thread previews. An
    # operator sees the list, the titles, who each is for and when it is due,
    # and none of the detail written inside.
    bodies = not auth.is_operator(sess)
    return tasks_src.load(bodies=bodies, status=status_, priority=priority,
                          assignee=assignee, stale=stale, archived=archived)


@app.post("/api/tasks", status_code=201)
def create_task(body: TaskCreate, sess: dict = Depends(auth.current_session)):
    """Any person on the team may add to the shared list."""
    auth.require_person(sess)
    _tasks_writable()
    try:
        return {"ok": True, "task": tasks_store.create(
            title=body.title, created_by=auth.person_of(sess),
            detail=body.detail, assignee=body.assignee,
            priority=body.priority, deadline=body.deadline,
            source="manual")}
    except tasks_store.TaskError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.patch("/api/tasks/{task_id}")
def patch_task(task_id: str, body: TaskPatch, sess: dict = Depends(auth.current_session)):
    auth.require_person(sess)
    auth.require_task_admin(sess)
    _tasks_writable()
    _task_or_404(task_id)
    try:
        fields = body.model_dump(exclude_unset=True)
        return {"ok": True, "task": tasks_store.update(task_id, **fields)}
    except tasks_store.TaskNotFound:
        raise HTTPException(status_code=404, detail="No such task.")
    except tasks_store.TaskError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/tasks/{task_id}/assign")
def assign_task(task_id: str, body: TaskAssign, sess: dict = Depends(auth.current_session)):
    auth.require_person(sess)
    auth.require_task_admin(sess)
    _tasks_writable()
    _task_or_404(task_id)
    try:
        return {"ok": True, "task": tasks_store.assign(
            task_id, body.assignee, by=auth.person_of(sess))}
    except tasks_store.TaskNotFound:
        raise HTTPException(status_code=404, detail="No such task.")
    except tasks_store.TaskError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/tasks/{task_id}/status")
def set_task_status(task_id: str, body: TaskStatus,
                    sess: dict = Depends(auth.current_session)):
    _tasks_writable()
    task = _task_or_404(task_id)
    _may_change_status(sess, task)
    try:
        return {"ok": True, "task": tasks_store.set_status(
            task_id, body.status, by=auth.person_of(sess))}
    except tasks_store.TaskNotFound:
        raise HTTPException(status_code=404, detail="No such task.")
    except tasks_store.TaskError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/tasks/{task_id}/archive")
def archive_task(task_id: str, sess: dict = Depends(auth.current_session)):
    auth.require_person(sess)
    auth.require_task_admin(sess)
    _tasks_writable()
    _task_or_404(task_id)
    try:
        return {"ok": True, "task": tasks_store.set_archived(
            task_id, True, by=auth.person_of(sess))}
    except tasks_store.TaskNotFound:
        raise HTTPException(status_code=404, detail="No such task.")


# --------------------------------------------------------------- attachments


@app.post("/api/threads/{thread_id}/attachments", status_code=201)
async def post_attachment(
    thread_id: str,
    sess: dict = Depends(auth.current_session),
    file: UploadFile = File(...),
):
    auth.require_bodies(sess)
    thread = _reach_thread(thread_id, sess)
    if thread["archived"]:
        raise HTTPException(status_code=409, detail="That channel is archived.")

    # Read with a hard ceiling rather than trusting Content-Length, so a lying
    # header cannot make us buffer an unbounded body.
    data = await file.read(attachments_mod.MAX_UPLOAD_BYTES + 1)
    if len(data) > attachments_mod.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                "That file is larger than the "
                f"{attachments_mod.human_size(attachments_mod.MAX_UPLOAD_BYTES)} limit."
            ),
        )

    try:
        record = attachments_mod.store(
            thread_id, file.filename or "file", file.content_type or "", data
        )
    except attachments_mod.AttachmentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    return {"ok": True, "attachment": record}


@app.get("/api/attachments/{att_id}")
def get_attachment(att_id: str, sess: dict = Depends(auth.current_session)):
    """Authenticated only — these are the tenant's documents and photos."""
    rec = attachments_mod.get(att_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    path = Path(rec["path"])
    if not path.is_file():
        raise HTTPException(status_code=410, detail="That file is no longer on disk.")
    return FileResponse(
        path,
        media_type=rec["mime"],
        filename=rec["filename"],
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/api/threads/{thread_id}/attachments")
def list_thread_attachments(thread_id: str, sess: dict = Depends(auth.current_session)):
    auth.require_bodies(sess)
    _reach_thread(thread_id, sess)
    items = attachments_mod.thread_attachments(thread_id)
    return {"thread_id": thread_id, "attachments": items, "count": len(items)}


@app.post("/api/transcribe")
async def post_transcribe(
    sess: dict = Depends(auth.current_session),
    audio: UploadFile = File(...),
):
    """Push-to-talk: audio in, text out. The audio is NOT retained (app/transcribe.py)."""
    data = await audio.read(transcribe_mod.MAX_UPLOAD_BYTES + 1)
    if len(data) > transcribe_mod.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That recording is too large.")
    try:
        # Blocking CPU work: hand it to the threadpool so the event loop keeps
        # serving the feed and any in-flight chat stream while whisper decodes.
        result = await run_in_threadpool(
            transcribe_mod.transcribe_bytes, data, audio.content_type or ""
        )
    except transcribe_mod.TranscribeError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Transcription failed: {type(exc).__name__}",
        )
    return {"ok": True, **result}


# ------------------------------------------------------------------ counters

# Allowlisted so a client cannot invent unbounded counter names and grow the
# table. Voice playback is client-side (browser SpeechSynthesis), so these are
# the only signal available for the "fund local Kokoro?" decision.
COUNTERS = {"tts_toggle_on", "tts_utterance", "ptt_utterance"}


@app.post("/api/counters/{name}")
def post_counter(name: str, sess: dict = Depends(auth.current_session)):
    """Bump a usage counter. Deliberately NOT an activity-feed event — the feed
    is the customer's record of what Jarvis did, not a telemetry channel."""
    if name not in COUNTERS:
        raise HTTPException(status_code=422, detail="Unknown counter.")
    return {"ok": True, "name": name, "value": threads_mod.bump_counter(name)}


@app.get("/api/counters")
def get_counters(sess: dict = Depends(auth.current_session)):
    return {"counters": threads_mod.all_counters()}


# ── operator fixture injection ────────────────────────────────────────────────
# The tester's way to make an inbound email happen on a tenant with no poller.
# Operator session only (a person — even the owner — is refused: this is a
# support tool, not a feature), whitelist only (enumerated at start, never a
# path), and every call is journalled. See app/inject.py.
def _require_operator(sess: dict) -> None:
    if not auth.is_operator(sess):
        raise HTTPException(status_code=403,
                            detail="Only an operator session can inject test input.")


@app.get("/api/operator/inject")
def get_inject_whitelist(sess: dict = Depends(auth.current_session)):
    _require_operator(sess)
    from . import inject as inject_mod
    return {"kinds": sorted(inject_mod.KINDS), "fixtures": inject_mod.WHITELIST,
            "boundary_active": inject_mod.boundary_active()}


@app.post("/api/operator/inject")
async def post_inject(request: Request, sess: dict = Depends(auth.current_session)):
    _require_operator(sess)
    from . import inject as inject_mod
    # Parsed by hand, not a model: the contract is exactly two string fields
    # and the spec wants 400 (not 422) for anything else.
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be JSON: {\"kind\", \"fixture\"}.")
    if not isinstance(body, dict) or set(body) != {"kind", "fixture"} \
            or not isinstance(body.get("kind"), str) or not isinstance(body.get("fixture"), str):
        raise HTTPException(status_code=400,
                            detail="Body must be exactly {\"kind\": \"showing|meeting|reply\", "
                                   "\"fixture\": \"<whitelisted name>\"}.")
    try:
        inject_mod.resolve(body["kind"], body["fixture"])  # 400 before any work
        result = await run_in_threadpool(
            inject_mod.run, body["kind"], body["fixture"],
            by=f"operator:{auth.operator_of(sess) or 'operator'}", origin="api")
    except inject_mod.InjectError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return result


@app.get("/api/operator/outbox")
def get_operator_outbox(request: Request, sess: dict = Depends(auth.current_session)):
    """The capture outbox, read-only: what the boundary caught instead of
    sending. Operator only — it holds full message bodies addressed to
    fixture people, and it is a support surface, not a feature. `limit`
    1..200 (default 20), newest last, exactly as the file has them."""
    _require_operator(sess)
    raw = request.query_params.get("limit", "20")
    try:
        limit = int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="limit must be an integer between 1 and 200.")
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=400, detail="limit must be an integer between 1 and 200.")
    import sys as _sys
    p = str(config.SHARED)
    if p not in _sys.path:
        _sys.path.insert(0, p)
    import outbound_boundary  # noqa: WPS433
    rows = outbound_boundary.read_outbox(limit)
    keep = ("ts", "to", "cc", "subject", "body")
    return {"rows": [{k: r.get(k) for k in keep} for r in rows],
            "count": len(rows), "limit": limit, "active": outbound_boundary.active()}


@app.get("/api/health/summary")
def get_health_summary(sess: dict = Depends(auth.current_session)):
    """The every-two-months instrument: current-period counters, 7/30-day
    trends, response-latency and deadline samples, sweep failures. Aggregates
    only — no addresses, no names, no task titles. Owner, admin and the
    operator read it; a member does not: it describes the whole team's work."""
    if not (auth.sees_everything(sess) or auth.is_operator(sess)):
        raise HTTPException(status_code=403, detail="Only the owner or an admin can see usage numbers.")
    from . import metrics
    return metrics.summary()


@app.get("/api/health")
def get_health(sess: dict = Depends(auth.current_session)):
    report = health.run_all()
    # Usage counters ride along with health so the resize decision has a single
    # place to look. They are informational and never affect the status.
    try:
        report["counters"] = threads_mod.all_counters()
    except Exception:
        pass
    # Connection *status* only — never the cipher or refresh token.
    try:
        report["google_connection"] = google_conn_mod.status_public()
    except Exception:
        pass
    report["switches"] = {k: ("on" if v else "off") for k, v in switches_mod.state().items()}
    report["sentinel_mode"] = broker_mod.sentinel_mode()
    if switches_mod.on("sentinel"):
        from app import platform_store  # noqa: PLC0415
        try:
            report["model_usage"] = [{**r, "day": r["day"].isoformat()} for r in platform_store.usage_by_day(7)]
        except platform_db.Unavailable as exc:
            report["model_usage_error"] = str(exc)
    # A failing subsystem is reported with 503 so monitoring does not have to
    # parse the body to notice, while the body still carries the full detail.
    code = 503 if report["status"] == "fail" else 200
    return JSONResponse(status_code=code, content=report)


# --------------------------------------------------------------------------
# Static SPA
#
# Registered LAST so every /api route above wins the match. The frontend is
# served from this same origin on purpose: the app has no CORS middleware and
# the session cookie is httponly + samesite=lax, so a separately-hosted
# frontend could not authenticate at all.
# --------------------------------------------------------------------------

# Long-lived only for content-hashed build output; everything else must be
# revalidated or a stale index.html would pin clients to an old bundle.
_IMMUTABLE = "public, max-age=31536000, immutable"
_NO_CACHE = "no-cache"


def _index_response() -> Response:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        # The API is up but the bundle was never built. Say so plainly rather
        # than serving a 404 that looks like a routing bug.
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Frontend not built. Run `npm run build` in frontend/.",
            },
        )
    return FileResponse(index, headers={"Cache-Control": _NO_CACHE})


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str) -> Response:
    # Unknown /api/* stays a JSON 404 — it must never fall through to the SPA,
    # or a typo'd endpoint would return an HTML page with status 200.
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found.")

    if full_path:
        candidate = (STATIC_DIR / full_path).resolve()
        try:
            # Refuse anything that escapes the static root.
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found.")

        if candidate.is_file():
            cache = _IMMUTABLE if full_path.startswith("assets/") else _NO_CACHE
            return FileResponse(candidate, headers={"Cache-Control": cache})

    # Any other path is a client-side route: hand back the shell.
    return _index_response()


def main() -> None:
    import uvicorn
    # Fail loudly at boot rather than 500ing on the first login.
    config.require("APP_PASSPHRASE")
    config.require("APP_SESSION_SECRET")
    # Create the schema and seed the direct thread before serving, so the very
    # first request cannot race two workers into initialising it.
    threads_mod.init()
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info",
                log_config=_uvicorn_log_config(),
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
