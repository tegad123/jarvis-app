"""App-side broker: the agent asks, the app acts. The agent never holds a token.

The gateway's skills cannot read the tenant's Google connection, write the
threads store, or write approvals (two-user isolation). They call these
endpoints instead:

    POST /api/broker/google           {api, version, method, params} -> {result}
    GET  /api/broker/google/status    -> {source}
    POST /api/broker/outbound         {kind, platform, destinations, summary, content}
    POST /api/broker/approvals        {skill, summary, payload, kind, requires_role}
    GET  /api/broker/usage?days=N     -> {days: [model tokens and cost per UTC day], currency, day_boundary}
    POST /api/broker/counters         {op: bump|record, ...}
    POST /api/broker/channels         {op: ensure_deal_channel, name}

Auth is the tenant's API_SERVER_KEY as a bearer token, from loopback only, and
never through the tunnel (a request carrying Cloudflare or forwarding headers
is refused even with the key). Google methods are an allowlist; a send must
clear the outbound recipient boundary here too, whatever the caller checked.

SENTINEL (docs/SPEC.md 9.1-9.5, app/sentinel.py). Every write is classified
and decided before it can run; every decision, taint event, approval and
grant is a row in the append-only `actions` table (app/platform_store.py).
Policies and grants come from the platform database. An `ask` verdict files
an approval holding the exact action; only a signed approval callback from
the owner's session runs it (app/approvals.py -> execute_approved).
JARVIS_SENTINEL_MODE=shadow holds every write and every outbound message,
records each as "would have done X" on the feed with its source record, and
executes none. Reads that bring in untrusted content taint the caller's run
(X-Jarvis-Run, falling back to the Hermes session) for good; a write with no
run identity is treated as tainted.

Outbound chat posts, channel creation and SMTP mail have no app-side
transport: in shadow they are held like any write, and in enforce they are
refused (501 not_brokered) until a connector moves app-side.
"""
import base64
import hmac
import logging
import os
import re
import sys
from email import message_from_bytes
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import actor_refs
from . import config
from . import platform_db
from . import platform_store as store
from . import sentinel
from . import switches
from . import threads as threads_mod

log = logging.getLogger("app.broker")
router = APIRouter(prefix="/api/broker")

READ, WRITE = "read", "write"
METHODS: Dict[tuple, Dict[str, str]] = {
    ("gmail", "v1"): {
        "users.getProfile": READ,
        "users.messages.list": READ,
        "users.messages.get": READ,
        "users.messages.attachments.get": READ,
        "users.threads.list": READ,
        "users.threads.get": READ,
        "users.history.list": READ,
        "users.labels.list": READ,
        "users.messages.send": WRITE,
        "users.messages.modify": WRITE,
        "users.watch": WRITE,
        "users.stop": WRITE,
    },
    ("calendar", "v3"): {
        "calendarList.list": READ,
        "events.list": READ,
        "events.get": READ,
        "events.insert": WRITE,
        "events.patch": WRITE,
        "events.delete": WRITE,
    },
}
SEND_METHODS = {("gmail", "v1", "users.messages.send")}
OUTBOUND_KINDS = ("chat_post", "chat_channel_create", "chat_upload", "email_send")
FORWARD_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "forwarded", "x-real-ip")
COUNTER_NAME = re.compile(r"^[a-z0-9_.:-]{1,120}$")
CONTEXT_VALUE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,200}$")
SHADOW = "shadow"
OWNER = "owner"


def sentinel_mode() -> str:
    return os.environ.get("JARVIS_SENTINEL_MODE", "enforce").strip().lower() or "enforce"


def _bump(name: str) -> None:
    try:
        threads_mod.bump_counter(name)
    except Exception:
        log.error("broker: counter bump failed (%s)", name)


def _shared():
    p = str(config.SHARED)
    if p not in sys.path:
        sys.path.insert(0, p)


def _authorize(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1"):
        _bump("broker_refused_auth")
        raise HTTPException(status_code=403, detail="The broker answers loopback callers only.")
    if any(h in request.headers for h in FORWARD_HEADERS):
        _bump("broker_refused_auth")
        log.error("broker: refused a forwarded request (tunnel traffic never reaches the broker)")
        raise HTTPException(status_code=403, detail="The broker does not answer forwarded requests.")
    expected = config.require("API_SERVER_KEY")
    got = request.headers.get("authorization", "")
    if not hmac.compare_digest(got.encode(), f"Bearer {expected}".encode()):
        _bump("broker_refused_auth")
        raise HTTPException(status_code=401, detail="Broker key missing or wrong.")
    if not switches.on("broker"):
        _bump("broker_switch_off")
        raise HTTPException(status_code=503, detail="broker_disabled: the broker is switched off for this tenant.")


def _require_sentinel() -> None:
    if not switches.on("sentinel"):
        _bump("broker_sentinel_switch_off")
        raise HTTPException(status_code=503, detail="sentinel_off: writes are refused while Sentinel is switched off.")


def _writes_allowed() -> bool:
    _shared()
    import write_guard  # noqa: PLC0415
    return write_guard.writes_allowed()


def _parse_raw(params: Dict[str, Any]):
    raw = ((params.get("body") or {}).get("raw") or "")
    try:
        return message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        raise HTTPException(status_code=400, detail="users.messages.send needs body.raw (base64url MIME).")


def _boundary(addrs) -> Optional[str]:
    _shared()
    import outbound_boundary  # noqa: PLC0415
    try:
        outbound_boundary.check(list(addrs))
    except outbound_boundary.RecipientRefused as exc:
        return str(exc) or "outside the outbound boundary"
    return None


def _recipients(params: Dict[str, Any]) -> Tuple[list, Optional[str]]:
    """The send's recipients and the outbound boundary's refusal, if any."""
    msg = _parse_raw(params)
    try:
        addrs = list(sentinel.parse_recipients(
            msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", [])))
    except sentinel.UnparseableRecipients:
        _bump("broker_google_recipient_refused")
        raise HTTPException(status_code=403, detail="A recipient header could not be parsed.")
    if not addrs:
        raise HTTPException(status_code=400, detail="The message has no recipients.")
    return addrs, _boundary(addrs)


def _context(request: Request) -> Dict[str, str]:
    out = {}
    for key in ("run", "session", "task", "skill", "source"):
        v = request.headers.get(f"x-jarvis-{key}", "").strip()
        out[key] = v if v and CONTEXT_VALUE.match(v) else ""
    return out


def _source(api: str, method: str, params: Dict[str, Any], ctx: Dict[str, str]) -> Tuple[str, str]:
    """The record a write acts on (spec 6). The caller names it in
    X-Jarvis-Source as kind:id; a few writes carry their own: labelling a
    message acts on that message, a reply acts on its thread, and starting or
    stopping the Gmail watch is a system action."""
    kind, _, sid = (ctx.get("source") or "").partition(":")
    if kind and sid:
        return kind, sid
    if (api, method) == ("gmail", "users.messages.modify") and params.get("id"):
        return "gmail", str(params["id"])
    if (api, method) in (("gmail", "users.watch"), ("gmail", "users.stop")):
        return "system", f"gmail.{method}"
    if (api, method) == ("gmail", "users.messages.send"):
        tid = (params.get("body") or {}).get("threadId")
        if tid:
            return "gmail_thread", str(tid)
    return "", ""


def _org_domains() -> list:
    _shared()
    import identity  # noqa: PLC0415
    return list(identity.internal_domains())


def _run(ctx: Dict[str, str]) -> str:
    return ctx.get("run") or ctx.get("session") or ""


def _taint(ctx: Dict[str, str], reason: str) -> None:
    run = _run(ctx)
    if not run or not switches.on("sentinel"):
        return
    try:
        if store.taint(run, reason, ctx.get("skill", "")):
            _bump("broker_sentinel_run_tainted")
    except platform_db.Unavailable:
        _bump("broker_sentinel_db_unavailable")
        log.error("broker: could not record taint for a run (%s); later writes from it will be refused", reason)


def _note_untrusted(ctx: Dict[str, str], api: str, method: str, result: Any) -> None:
    """9.5: reading untrusted inbound content taints the run."""
    org = _org_domains()
    if not isinstance(result, dict):
        return
    if (api, method) == ("gmail", "users.messages.get"):
        if sentinel.untrusted_sender((result.get("payload") or {}).get("headers") or [], org):
            _taint(ctx, "read an email from outside the org")
    elif (api, method) == ("gmail", "users.threads.get"):
        if any(sentinel.untrusted_sender((m.get("payload") or {}).get("headers") or [], org)
               for m in result.get("messages") or []):
            _taint(ctx, "read a thread with mail from outside the org")
    elif (api, method) == ("gmail", "users.threads.list"):
        if any(t.get("snippet") for t in result.get("threads") or []):
            _taint(ctx, "read thread snippets of unknown senders")
    elif (api, method) == ("gmail", "users.messages.attachments.get"):
        _taint(ctx, "downloaded an attachment")
    elif api == "calendar" and method in ("events.get", "events.list"):
        events = result.get("items") if method == "events.list" else [result]
        for ev in events or []:
            who = str(((ev or {}).get("organizer") or {}).get("email") or "")
            if who and not sentinel.is_internal(who, org) and not who.endswith("calendar.google.com"):
                _taint(ctx, "read an event organized outside the org")
                break


def _service(api: str, version: str):
    _shared()
    import google_creds  # noqa: PLC0415
    from googleapiclient.discovery import build  # noqa: PLC0415
    opts = None
    endpoint = os.environ.get("JARVIS_GOOGLE_API_ENDPOINT")
    if endpoint:
        if not config.oauth_mock():
            raise HTTPException(status_code=500, detail="JARVIS_GOOGLE_API_ENDPOINT is honoured in mock mode only.")
        opts = {"api_endpoint": endpoint}
    return build(api, version, credentials=google_creds.credentials(), cache_discovery=False,
                 client_options=opts)


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be JSON.")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object.")
    return body


@router.post("/google")
async def post_google(request: Request):
    _authorize(request)
    body = await _json_body(request)
    api, version, method = body.get("api"), body.get("version"), body.get("method")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be an object.")
    klass = METHODS.get((api, version), {}).get(method)
    if klass is None:
        _bump("broker_google_refused_method")
        log.error("broker: refused method %s %s %s (not allowlisted)", api, version, method)
        raise HTTPException(status_code=403, detail=f"{api} {version} {method} is not allowed through the broker.")
    ctx = _context(request)
    from starlette.concurrency import run_in_threadpool  # noqa: PLC0415
    if klass == READ:
        out = await run_in_threadpool(_execute, api, version, method, params, klass)
        if isinstance(out, dict):
            _note_untrusted(ctx, api, method, out.get("result"))
        return out
    _require_sentinel()
    status, content = await run_in_threadpool(gate_google, api, version, method, params, ctx)
    return content if status == 200 else JSONResponse(status_code=status, content=content)


# ── the gate ──────────────────────────────────────────────────────────────────

def _describe_google(api, method, params, cls, dests, content: bool = False) -> str:
    """One plain clause for what the action does. `content` adds the subject
    or title, for the owner-only approval queue; the feed and audit log carry
    structure only."""
    if (api, method) == ("gmail", "users.messages.send"):
        subject = str(_parse_raw(params).get("Subject") or "").strip() if content else ""
        return f"send an email to {', '.join(dests) or 'nobody'}" + (f" ({subject[:80]})" if subject else "")
    if (api, method) == ("calendar", "events.insert"):
        title = str((params.get("body") or {}).get("summary") or "")[:80] if content else ""
        return (f"add '{title}' to the calendar" if title else "add an event to the calendar") + \
            (f" and notify {', '.join(dests)}" if dests else "")
    if (api, method) == ("calendar", "events.patch"):
        return "change a calendar event" + (f" and notify {', '.join(dests)}" if dests else "")
    if (api, method) == ("calendar", "events.delete"):
        return "delete a calendar event"
    if (api, method) == ("gmail", "users.messages.modify"):
        return "relabel an email"
    return f"{api} {method}"


def _feed_held(what: str, cls: str, outcome: str, decision: sentinel.Decision, source: Tuple[str, str],
               skill: str, action_id: Optional[int]) -> None:
    """The owner's view of a held action: what Jarvis would have done, why
    Sentinel would have decided as it did, and the record it acted on."""
    _shared()
    try:
        import notify  # noqa: PLC0415
        src = f"{source[0]}:{source[1]}" if source[0] else "no source record"
        notify.notify("ops", kind="sentinel.would_have", skill=skill or "jarvis",
                      headline=f"Practice mode: Jarvis would have tried to {what}",
                      detail=f"{cls} · Sentinel would {decision.verdict}: {decision.reason} · source {src} · "
                             f"held action #{action_id} · nothing was sent")
    except Exception as exc:
        _bump("broker_feed_write_failed")
        log.error("broker: would-have feed write failed (%s)", type(exc).__name__)


def gate_google(api, version, method, params, ctx, approval: Optional[Dict[str, Any]] = None
                ) -> Tuple[int, Dict[str, Any]]:
    """Decide one Google write and run it if allowed. `approval` is a decided
    approval row whose callback the owner signed; it answers an `ask`, nothing else."""
    addrs, refusal = (_recipients(params) if (api, version, method) in SEND_METHODS else ([], None))
    cls, dests = sentinel.classify_google(api, method, params, _org_domains(), addrs)
    source = _source(api, method, params, ctx)
    now = sentinel.utcnow()
    run = _run(ctx)
    try:
        user_mode, ceiling = store.policy_modes(OWNER, ctx["skill"], cls)
        grants = store.active_grants(OWNER, api, ctx["skill"])
        rates = store.rates(now)
        tainted = store.is_tainted(run)
    except platform_db.Unavailable as exc:
        _bump("broker_sentinel_db_unavailable")
        log.error("broker: platform database unavailable, write refused (%s)", exc)
        return 503, {"error": "sentinel_unavailable", "detail": "The platform database is unavailable; nothing ran."}
    action = sentinel.Action(connector=api, method=method, cls=cls, destinations=dests, skill=ctx["skill"],
                             user=OWNER, run_id=run, session_id=ctx["session"], task_id=ctx["task"],
                             tainted=tainted, source_kind=source[0], source_id=source[1])
    decision = sentinel.decide(action, mode=sentinel.effective_mode(cls, user_mode, ceiling), grants=grants,
                               now=now, rates=rates, boundary_refusal=refusal)
    fields = store.fields_from_summary(sentinel.summarize(action, decision))
    fields["sentinel_mode"] = sentinel_mode()
    if approval:
        fields["approval_id"] = approval["id"]
    what = _describe_google(api, method, params, cls, dests)
    proposal = {"type": "google", "api": api, "version": version, "method": method, "params": params}

    def held(status, error, outcome, counter, detail, keep=False):
        if keep:
            aid = store.record_held("decision", outcome, fields, proposal)
            _feed_held(what, cls, outcome, decision, source, ctx["skill"], aid)
        else:
            aid = store.record_action("decision", outcome, fields)
        _bump(counter)
        log.error("broker: %s.%s %s (%s, %s)", api, method, outcome, cls, decision.reason)
        return status, {"error": error, "verdict": decision.verdict, "class": cls, "detail": detail,
                        "action_id": aid}

    if sentinel_mode() == SHADOW:
        return held(409, "shadowed", "shadowed", "broker_sentinel_shadowed",
                    f"Practice mode: logged, not executed. Sentinel would {decision.verdict}: {decision.reason}.",
                    keep=True)
    if not _writes_allowed():
        return held(403, "writes_disabled", "writes_disabled", "broker_google_write_refused",
                    "Writes are disabled for this tenant.")
    if refusal:
        return held(403, "recipient_refused", "recipient_refused", "broker_google_recipient_refused",
                    "A recipient is outside the outbound boundary.")
    if decision.verdict == sentinel.DENY:
        return held(403, "sentinel_denied", "denied", "broker_sentinel_denied", decision.reason)
    if decision.verdict == sentinel.ASK and not approval:
        full = _describe_google(api, method, params, cls, dests, content=True)
        aid = store.create_approval(origin="broker", user_id=OWNER, kind="action", skill=ctx["skill"] or "jarvis",
                                    summary=full[:1].upper() + full[1:], action=proposal, action_class=cls,
                                    reason=decision.reason, source_kind=source[0], source_id=source[1],
                                    run_id=run, session_id=ctx["session"], task_id=ctx["task"])
        fields["approval_id"] = aid
        status, content = held(403, "approval_required", "approval_required", "broker_sentinel_ask",
                               f"{decision.reason}. Waiting for the owner to approve {aid}; nothing was sent.")
        return status, {**content, "approval_id": aid}
    if decision.grant_id and any(g.id == decision.grant_id and g.scope == sentinel.SCOPE_ONCE for g in grants):
        if not store.consume_once_grant(decision.grant_id):
            return held(409, "grant_used", "grant_used", "broker_sentinel_grant_race",
                        "That one-time grant was already used.")
    store.record_action("decision", "allowed", fields)
    out = _execute(api, version, method, params, WRITE)
    ok = isinstance(out, dict)
    store.record_action("decision", "executed" if ok else "failed",
                        {**fields, **({} if ok else {"status_code": getattr(out, "status_code", None)})})
    if ok:
        return 200, out
    import json as _json  # noqa: PLC0415
    return out.status_code, _json.loads(out.body)


def execute_approved(item: Dict[str, Any], scope: str, decided_by: str) -> Dict[str, Any]:
    """Run an approved broker action through the gate again, with the approval
    answering `ask`. A wider scope than once becomes a grant first."""
    if not switches.on("sentinel"):
        _bump("broker_sentinel_switch_off")
        return {"state": "refused", "reason": "sentinel_off"}
    a = item.get("action_json") or {}
    if a.get("type") != "google":
        return {"state": "failed", "reason": "not a broker action"}
    ctx = {"run": item.get("run_id") or "", "session": item.get("session_id") or "", "task": item.get("task_id") or "",
           "skill": item.get("skill") or "", "source": f"{item.get('source_kind')}:{item.get('source_id')}"
           if item.get("source_kind") and item.get("source_id") else ""}
    grant_id = None
    if scope != sentinel.SCOPE_ONCE:
        api, method, params = a["api"], a["method"], a.get("params") or {}
        addrs = _recipients(params)[0] if (api, a["version"], method) in SEND_METHODS else []
        cls, dests = sentinel.classify_google(api, method, params, _org_domains(), addrs)
        action = sentinel.Action(connector=api, method=method, cls=cls, destinations=dests, skill=ctx["skill"],
                                 user=OWNER, session_id=ctx["session"], task_id=ctx["task"])
        try:
            grant = sentinel.grant_from_approval(item["id"], action, scope, sentinel.utcnow())
        except ValueError as exc:
            return {"state": "refused", "reason": f"grant refused: {exc}"}
        store.create_grant(grant, approval_id=item["id"], action_class=cls, created_by=decided_by)
        grant_id = grant.id
    status, content = gate_google(a["api"], a["version"], a["method"], a.get("params") or {}, ctx, approval=item)
    state = "executed" if status == 200 else (content.get("error") or "failed")
    out = {"state": state, "status": status, "grant_id": grant_id, "action_id": content.get("action_id")}
    if status != 200:
        out["reason"] = content.get("detail")
    return out


def _execute(api, version, method, params, klass):
    _shared()
    import google_creds  # noqa: PLC0415
    from googleapiclient.errors import HttpError  # noqa: PLC0415
    try:
        node = _service(api, version)
        parts = method.split(".")
        for p in parts[:-1]:
            node = getattr(node, p)()
        req = getattr(node, parts[-1])(**params)
        result = req.execute()
    except google_creds.GoogleNotConnected as exc:
        return JSONResponse(status_code=409, content={"error": "not_connected", "detail": str(exc)})
    except HttpError as exc:
        _bump("broker_google_http_error")
        status = int(getattr(exc.resp, "status", 0) or 0)
        content = exc.content.decode("utf-8", "replace") if isinstance(exc.content, bytes) else str(exc.content)
        log.error("broker: google %s.%s returned HTTP %s", api, method, status)
        return JSONResponse(status_code=502, content={"error": "google_http_error", "status": status,
                                                      "content": content[:20000]})
    except TypeError as exc:
        return JSONResponse(status_code=400, content={"error": "bad_params", "detail": str(exc)[:500]})
    _bump(f"broker_google_{klass}")
    log.info("broker: google %s.%s ok (%s)", api, method, klass)
    return {"result": result}


# ── outbound messages with no app-side transport ─────────────────────────────

def gate_outbound(kind: str, platform: str, destinations, summary: str, content: Dict[str, Any],
                  ctx: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """Chat posts, channel creation and SMTP mail reach other people, so they
    are external sends to Sentinel. Shadow holds them with the full proposal;
    enforce refuses them, because the app holds no transport for them yet."""
    dests = tuple(sorted({str(d).strip().lower() for d in destinations or [] if str(d).strip()}))
    cls = sentinel.EXTERNAL_SEND
    if kind == "email_send":
        org = _org_domains()
        cls = sentinel.INTERNAL_SEND if dests and all(sentinel.is_internal(d, org) for d in dests) \
            else sentinel.EXTERNAL_SEND
    kind_src, _, sid = (ctx.get("source") or "").partition(":")
    run = _run(ctx)
    try:
        tainted = store.is_tainted(run)
    except platform_db.Unavailable:
        _bump("broker_sentinel_db_unavailable")
        return 503, {"error": "sentinel_unavailable", "detail": "The platform database is unavailable; nothing ran."}
    action = sentinel.Action(connector=platform, method=kind, cls=cls, destinations=dests, skill=ctx["skill"],
                             user=OWNER, run_id=run, session_id=ctx["session"], task_id=ctx["task"],
                             tainted=tainted, source_kind=kind_src, source_id=sid)
    decision = sentinel.decide(action, mode=sentinel.effective_mode(cls), now=sentinel.utcnow(),
                               boundary_refusal=_boundary(dests) if kind == "email_send" and dests else None)
    fields = {**store.fields_from_summary(sentinel.summarize(action, decision)), "sentinel_mode": sentinel_mode()}
    proposal = {"type": "outbound", "kind": kind, "platform": platform, "destinations": list(dests),
                "summary": summary, "content": content}
    if sentinel_mode() == SHADOW:
        aid = store.record_held("outbound", "shadowed", fields, proposal)
        _feed_held(summary, cls, "shadowed", decision, (kind_src, sid), ctx["skill"], aid)
        _bump("broker_sentinel_shadowed")
        return 409, {"error": "shadowed", "verdict": decision.verdict, "class": cls, "action_id": aid,
                     "detail": f"Practice mode: logged, not executed. Sentinel would {decision.verdict}: "
                               f"{decision.reason}."}
    aid = store.record_action("outbound", "not_brokered", fields)
    _bump("broker_outbound_not_brokered")
    log.error("broker: outbound %s on %s refused: no app-side transport", kind, platform)
    return 501, {"error": "not_brokered", "verdict": decision.verdict, "class": cls, "action_id": aid,
                 "detail": f"{platform} {kind} has no app-side transport yet, so nothing was sent."}


@router.post("/outbound")
async def post_outbound(request: Request):
    _authorize(request)
    _require_sentinel()
    body = await _json_body(request)
    kind, platform = body.get("kind"), body.get("platform")
    summary, dests, content = body.get("summary"), body.get("destinations") or [], body.get("content") or {}
    if kind not in OUTBOUND_KINDS or not isinstance(platform, str) or not 0 < len(platform) <= 40:
        raise HTTPException(status_code=400, detail=f"kind must be one of {OUTBOUND_KINDS}; platform is required.")
    if not isinstance(summary, str) or not 0 < len(summary.strip()) <= 300:
        raise HTTPException(status_code=400, detail="summary is one plain sentence (1-300 chars).")
    if not isinstance(dests, list) or not isinstance(content, dict):
        raise HTTPException(status_code=400, detail="destinations is a list and content an object.")
    from starlette.concurrency import run_in_threadpool  # noqa: PLC0415
    status, out = await run_in_threadpool(gate_outbound, kind, platform, dests, summary.strip(), content,
                                          _context(request))
    return JSONResponse(status_code=status, content=out)


# ── approval requests filed by skills ────────────────────────────────────────

@router.post("/approvals")
async def post_approval_request(request: Request):
    """A skill files a request for a person to see. The agent can propose;
    only the owner's signed callback decides (app/approvals.py)."""
    _authorize(request)
    _require_sentinel()
    body = await _json_body(request)
    skill, summary, payload = body.get("skill"), body.get("summary"), body.get("payload")
    kind = body.get("kind") or "action"
    requires_role = body.get("requires_role")
    if not isinstance(skill, str) or not COUNTER_NAME.match(skill):
        raise HTTPException(status_code=400, detail="skill is required ([a-z0-9_.:-], 1-120).")
    if not isinstance(summary, str) or not 0 < len(summary.strip()) <= 300:
        raise HTTPException(status_code=400, detail="summary is one plain sentence (1-300 chars).")
    if not isinstance(payload, dict) or kind not in ("action", "config-change"):
        raise HTTPException(status_code=400, detail="payload is an object; kind is action or config-change.")
    if requires_role not in (None, "owner", "admin"):
        raise HTTPException(status_code=400, detail="requires_role is owner, admin or absent.")
    ctx = _context(request)
    kind_src, _, sid = (ctx.get("source") or "").partition(":")
    try:
        aid = store.create_approval(origin="producer", user_id=OWNER, kind=kind, skill=skill,
                                    summary=summary.strip(), action=payload, reason="filed by a skill",
                                    source_kind=kind_src or None, source_id=sid or None, run_id=_run(ctx),
                                    session_id=ctx["session"], task_id=ctx["task"],
                                    requires_role=requires_role or ("owner" if kind == "config-change" else None))
    except platform_db.Unavailable:
        _bump("broker_sentinel_db_unavailable")
        raise HTTPException(status_code=503, detail="The platform database is unavailable; nothing was filed.")
    _bump("broker_approval_filed")
    return {"ok": True, "id": aid}


@router.get("/usage")
def get_usage(request: Request, days: int = 1):
    """Model tokens and cost per day for the cost guard. Totals only."""
    _authorize(request)
    _require_sentinel()
    if not 1 <= days <= 62:
        raise HTTPException(status_code=400, detail="days is 1..62.")
    try:
        rows = store.usage_by_day(days)
    except platform_db.Unavailable:
        _bump("broker_sentinel_db_unavailable")
        raise HTTPException(status_code=503, detail="The platform database is unavailable.")
    return {"days": [{**r, "day": r["day"].isoformat()} for r in rows], "currency": "USD",
            "day_boundary": "UTC"}


@router.get("/google/status")
def get_google_status(request: Request):
    _authorize(request)
    _shared()
    import google_creds  # noqa: PLC0415
    return {"source": google_creds.source()}


@router.post("/counters")
async def post_counters(request: Request):
    _authorize(request)
    body = await request.json()
    op, name = body.get("op"), body.get("name")
    if not isinstance(name, str) or not COUNTER_NAME.match(name):
        raise HTTPException(status_code=400, detail="Counter names are 1-120 of [a-z0-9_.:-].")
    if op == "bump":
        by = body.get("by", 1)
        if not isinstance(by, int) or not 1 <= by <= 1_000_000:
            raise HTTPException(status_code=400, detail="by must be an integer 1..1000000.")
        return {"ok": True, "value": threads_mod.bump_counter(name, by)}
    if op == "record":
        value, ref = body.get("value"), body.get("ref")
        if not isinstance(value, (int, float)) or (ref is not None and not isinstance(ref, str)):
            raise HTTPException(status_code=400, detail="record needs a numeric value and an optional string ref.")
        from . import metrics  # noqa: PLC0415
        metrics.record(name, value, ref)
        return {"ok": True}
    raise HTTPException(status_code=400, detail="op must be bump or record.")


@router.get("/actor/active")
async def get_actor_active(request: Request):
    _authorize(request)
    return {"active": actor_refs.active_count()}


@router.post("/actor")
async def post_actor(request: Request):
    """Who sent the chat turn a tool is running in: the agent passes the
    reference the gateway put in its environment, the app answers from the
    session it minted it for."""
    _authorize(request)
    body = await request.json()
    ref = body.get("ref") if isinstance(body, dict) else None
    if not isinstance(ref, str) or not ref.startswith(actor_refs.PREFIX) or len(ref) > 100:
        _bump("broker_actor_refused")
        raise HTTPException(status_code=400, detail="ref must be an app: actor reference.")
    entry, why = actor_refs.resolve(ref)
    if entry is None:
        _bump(f"broker_actor_{why}")
        log.warning("broker: actor reference refused (%s)", why)
        raise HTTPException(status_code=404, detail=f"actor reference {why}")
    _bump("broker_actor_resolved")
    return {"person_key": entry["person_key"], "thread_id": entry["thread_id"],
            "age_s": entry["age_s"], "turn_ended": entry["ended"] is not None}


@router.post("/chat/action")
async def post_chat_action(request: Request):
    """A tool asks the app to show one of its own controls under the running
    turn's reply. The app decides from its own state whether it appears; the
    model never draws a link or button itself."""
    _authorize(request)
    from . import agent_status  # noqa: PLC0415
    body = await _json_body(request)
    ref, action = body.get("ref"), body.get("action")
    if action not in agent_status.CHAT_ACTIONS:
        _bump("broker_chat_action_refused")
        raise HTTPException(status_code=400, detail=f"action must be one of {agent_status.CHAT_ACTIONS}.")
    entry, why = actor_refs.resolve(ref) if isinstance(ref, str) and ref.startswith(actor_refs.PREFIX) \
        else (None, "unknown")
    if entry is None or entry["ended"] is not None:
        _bump("broker_chat_action_no_turn")
        log.warning("broker: chat action %s without a running turn (%s)", action, why or "ended")
        return {"shown": False, "why": "no_turn"}
    if agent_status.google_state() == "connected":
        return {"shown": False, "why": "already_connected"}
    if not agent_status.can_connect(entry["role"]):
        _bump("broker_chat_action_not_allowed")
        return {"shown": False, "why": "not_allowed"}
    actor_refs.request_action(ref, action)
    _bump(f"broker_chat_action_{action}")
    return {"shown": True}


@router.post("/channels")
async def post_channels(request: Request):
    _authorize(request)
    body = await request.json()
    name = body.get("name")
    if body.get("op") != "ensure_deal_channel" or not isinstance(name, str) or not 0 < len(name.strip()) <= 200:
        raise HTTPException(status_code=400, detail="Body must be {op: ensure_deal_channel, name: 1-200 chars}.")
    name = name.strip()
    for existing in threads_mod.list_threads(include_archived=True, unscoped=True):
        if existing["name"].strip().lower() == name.lower():
            return {"thread": existing, "created": False}
    return {"thread": threads_mod.create_thread(name=name, created_by="jarvis"), "created": True}
