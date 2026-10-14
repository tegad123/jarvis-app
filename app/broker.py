"""App-side broker: the agent asks, the app acts. The agent never holds a token.

The gateway's skills cannot read the tenant's Google connection or write the
threads store (two-user isolation). They call these endpoints instead:

    POST /api/broker/google           {api, version, method, params} -> {result}
    GET  /api/broker/google/status    -> {source}
    POST /api/broker/counters         {op: bump|record, ...}
    POST /api/broker/channels         {op: ensure_deal_channel, name}

Auth is the tenant's API_SERVER_KEY as a bearer token, from loopback only, and
never through the tunnel (a request carrying Cloudflare or forwarding headers
is refused even with the key). Google methods are an allowlist; a write-class
method runs only when writes are enabled, and a send must clear the outbound
recipient boundary here too, whatever the caller checked.

SENTINEL (docs/SPEC.md 9.1-9.5, app/sentinel.py). Every write is classified
and decided before it can run, and every decision is appended to the
app-only ledger (JARVIS_SENTINEL_LEDGER). JARVIS_SENTINEL_MODE=shadow logs
each write with its verdict and executes none. Reads that bring in
untrusted content taint the caller's run (X-Jarvis-Run, falling back to the
Hermes session); a write with no run identity is treated as tainted. An
`ask` verdict is refused until the approvals and grants store exists.
"""
import base64
import fcntl
import hmac
import json
import logging
import os
import re
import sys
import threading
from datetime import timedelta
from pathlib import Path
from email import message_from_bytes
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import config
from . import sentinel
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
FORWARD_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "forwarded", "x-real-ip")
COUNTER_NAME = re.compile(r"^[a-z0-9_.:-]{1,120}$")
CONTEXT_VALUE = re.compile(r"^[A-Za-z0-9_.:@-]{1,120}$")
SHADOW = "shadow"
LEDGER = Path(os.environ.get("JARVIS_SENTINEL_LEDGER")
              or str(config.HERMES_HOME / "state" / "sentinel_ledger.jsonl")).expanduser()
_TAINTED: Dict[str, str] = {}
_TAINT_LOCK = threading.Lock()


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


def _writes_allowed() -> bool:
    _shared()
    import write_guard  # noqa: PLC0415
    return write_guard.writes_allowed()


def _recipients(params: Dict[str, Any]) -> Tuple[list, Optional[str]]:
    """The send's recipients and the outbound boundary's refusal, if any."""
    raw = ((params.get("body") or {}).get("raw") or "")
    try:
        msg = message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        raise HTTPException(status_code=400, detail="users.messages.send needs body.raw (base64url MIME).")
    try:
        addrs = list(sentinel.parse_recipients(
            msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", [])))
    except sentinel.UnparseableRecipients:
        _bump("broker_google_recipient_refused")
        raise HTTPException(status_code=403, detail="A recipient header could not be parsed.")
    if not addrs:
        raise HTTPException(status_code=400, detail="The message has no recipients.")
    _shared()
    import outbound_boundary  # noqa: PLC0415
    try:
        outbound_boundary.check(addrs)
    except outbound_boundary.RecipientRefused as exc:
        return addrs, str(exc) or "outside the outbound boundary"
    return addrs, None


def _context(request: Request) -> Dict[str, str]:
    out = {}
    for key in ("run", "session", "task", "skill"):
        v = request.headers.get(f"x-jarvis-{key}", "").strip()
        out[key] = v if CONTEXT_VALUE.match(v or "-") and v else ""
    return out


def _org_domains() -> list:
    _shared()
    import identity  # noqa: PLC0415
    return list(identity.internal_domains())


def _ledger(entry: Dict[str, Any]) -> None:
    """Append one decision. Raises: a write whose decision cannot be recorded
    does not run, or shadow mode would be a silent no-op."""
    entry = {"ts": sentinel.utcnow().isoformat(), **entry}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _rates(now) -> sentinel.Rates:
    """Executed sends in the last day, from the ledger."""
    rates = sentinel.Rates()
    hour, day = (now - timedelta(hours=1)).isoformat(), (now - timedelta(days=1)).isoformat()
    try:
        with open(LEDGER, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("outcome") != "executed" or str(e.get("ts", "")) < day:
                    continue
                if e.get("class") == sentinel.EXTERNAL_SEND and str(e["ts"]) >= hour:
                    rates.external_sends_last_hour += 1
                if e.get("class") in (sentinel.INTERNAL_SEND, sentinel.EXTERNAL_SEND):
                    for d in e.get("destinations") or []:
                        rates.per_recipient_today[d] = rates.per_recipient_today.get(d, 0) + 1
    except FileNotFoundError:
        pass
    return rates


def _taint(ctx: Dict[str, str], reason: str) -> None:
    run = ctx.get("run") or ctx.get("session")
    if not run:
        return
    with _TAINT_LOCK:
        if run in _TAINTED:
            return
        _TAINTED[run] = reason
    _bump("broker_sentinel_run_tainted")
    try:
        _ledger({"event": "taint", "run_id": run, "reason": reason, "skill": ctx.get("skill", "")})
    except Exception:
        log.error("broker: could not record taint for a run (%s)", reason)


def _is_tainted(ctx: Dict[str, str]) -> bool:
    run = ctx.get("run") or ctx.get("session")
    if not run:
        return True
    with _TAINT_LOCK:
        return run in _TAINTED


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


@router.post("/google")
async def post_google(request: Request):
    _authorize(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be JSON.")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object.")
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
    return await run_in_threadpool(_write, api, version, method, params, ctx)


def _write(api, version, method, params, ctx):
    addrs, refusal = (_recipients(params) if (api, version, method) in SEND_METHODS else ([], None))
    cls, dests = sentinel.classify_google(api, method, params, _org_domains(), addrs)
    now = sentinel.utcnow()
    action = sentinel.Action(connector=api, method=method, cls=cls, destinations=dests,
                             skill=ctx["skill"], user="owner", run_id=ctx["run"] or ctx["session"],
                             session_id=ctx["session"], task_id=ctx["task"], tainted=_is_tainted(ctx))
    decision = sentinel.decide(action, mode=sentinel.effective_mode(cls), now=now,
                               rates=_rates(now), boundary_refusal=refusal)
    record = sentinel.summarize(action, decision)

    def held(status, error, outcome, counter, detail):
        _ledger({**record, "outcome": outcome})
        _bump(counter)
        log.error("broker: %s.%s %s (%s, %s)", api, method, outcome, cls, decision.reason)
        return JSONResponse(status_code=status, content={"error": error, "verdict": decision.verdict,
                                                         "class": cls, "detail": detail})

    if sentinel_mode() == SHADOW:
        return held(409, "shadowed", "shadowed", "broker_sentinel_shadowed",
                    f"Practice mode: logged, not executed. Sentinel would {decision.verdict}: {decision.reason}.")
    if not _writes_allowed():
        return held(403, "writes_disabled", "writes_disabled", "broker_google_write_refused",
                    "Writes are disabled for this tenant.")
    if refusal:
        return held(403, "recipient_refused", "recipient_refused", "broker_google_recipient_refused",
                    "A recipient is outside the outbound boundary.")
    if decision.verdict == sentinel.DENY:
        return held(403, "sentinel_denied", "denied", "broker_sentinel_denied", decision.reason)
    if decision.verdict == sentinel.ASK:
        return held(403, "approval_required", "approval_required", "broker_sentinel_ask",
                    f"{decision.reason}. Approvals are not wired yet, so this was not sent.")
    _ledger({**record, "outcome": "allowed"})
    out = _execute(api, version, method, params, WRITE)
    ok = isinstance(out, dict)
    _ledger({**record, "outcome": "executed" if ok else "failed",
             **({} if ok else {"status": getattr(out, "status_code", None)})})
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
