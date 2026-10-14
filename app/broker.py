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
"""
import base64
import hmac
import logging
import os
import re
import sys
from email import message_from_bytes
from email.utils import getaddresses
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import config
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


def _check_recipients(params: Dict[str, Any]) -> None:
    raw = ((params.get("body") or {}).get("raw") or "")
    try:
        msg = message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        raise HTTPException(status_code=400, detail="users.messages.send needs body.raw (base64url MIME).")
    addrs = [a for _, a in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", [])) if a]
    if not addrs:
        raise HTTPException(status_code=400, detail="The message has no recipients.")
    _shared()
    import outbound_boundary  # noqa: PLC0415
    try:
        outbound_boundary.check(addrs)
    except outbound_boundary.RecipientRefused:
        _bump("broker_google_recipient_refused")
        log.error("broker: send refused by the outbound boundary")
        raise HTTPException(status_code=403, detail="A recipient is outside the outbound boundary.")


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
    if klass == WRITE:
        if not _writes_allowed():
            _bump("broker_google_write_refused")
            log.error("broker: write %s.%s refused, writes are disabled", api, method)
            return JSONResponse(status_code=403, content={"error": "writes_disabled",
                                                          "detail": "Writes are disabled for this tenant."})
        if (api, version, method) in SEND_METHODS:
            _check_recipients(params)
    from starlette.concurrency import run_in_threadpool  # noqa: PLC0415
    return await run_in_threadpool(_execute, api, version, method, params, klass)


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
