"""Model proxy: the tenant's agent talks to Claude through the app.

The gateway's Hermes is configured with provider anthropic and base_url
http://127.0.0.1:{app port}/api/model/anthropic. Hermes honours a custom
base_url for provider anthropic only when its path ends in /anthropic; any
other path silently becomes https://api.anthropic.com. It authenticates with its own
API_SERVER_KEY (a stand-in, spec 9.6); this module checks that key, swaps in
the platform's ANTHROPIC_API_KEY (from /etc/jarvis/anthropic.env, app-only)
and forwards the request upstream, streaming the answer back unchanged.
The agent never holds the real key.

    POST /api/model/anthropic/v1/messages
    POST /api/model/anthropic/v1/messages/count_tokens

Same caller rules as the broker: loopback only, never through the tunnel.
No key configured is a loud 503, never a silent fallback to another model.
JARVIS_MODEL_UPSTREAM is honoured in mock mode only (verify sandboxes).
"""
import hmac
import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from . import config
from . import threads as threads_mod

log = logging.getLogger("app.model_proxy")
router = APIRouter(prefix="/api/model/anthropic")

UPSTREAM = "https://api.anthropic.com"
PATHS = ("v1/messages", "v1/messages/count_tokens")
FORWARD_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "forwarded", "x-real-ip")
PASS_REQUEST_HEADERS = ("anthropic-version", "anthropic-beta", "content-type", "accept")
PASS_RESPONSE_HEADERS = ("content-type", "request-id", "anthropic-ratelimit-requests-remaining",
                         "anthropic-ratelimit-tokens-remaining", "retry-after")
MAX_BODY = 32 * 1024 * 1024
TIMEOUT = httpx.Timeout(connect=10.0, read=900.0, write=60.0, pool=10.0)


def _bump(name: str) -> None:
    try:
        threads_mod.bump_counter(name)
    except Exception:
        log.error("model proxy: counter bump failed (%s)", name)


def _authorize(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1") or any(h in request.headers for h in FORWARD_HEADERS):
        _bump("model_proxy_refused_auth")
        raise HTTPException(status_code=403, detail="The model proxy answers direct loopback callers only.")
    expected = f"{config.require('API_SERVER_KEY')}".encode()
    got = request.headers.get("x-api-key", "")
    if not got and request.headers.get("authorization", "").startswith("Bearer "):
        got = request.headers["authorization"][len("Bearer "):]
    if not hmac.compare_digest(got.encode(), expected):
        _bump("model_proxy_refused_auth")
        raise HTTPException(status_code=401, detail="Model proxy key missing or wrong.")


def _upstream() -> str:
    override = os.environ.get("JARVIS_MODEL_UPSTREAM", "").strip()
    if override:
        if not config.oauth_mock():
            raise HTTPException(status_code=500, detail="JARVIS_MODEL_UPSTREAM is honoured in mock mode only.")
        return override.rstrip("/")
    return UPSTREAM


@router.post("/{path:path}")
async def proxy(path: str, request: Request):
    _authorize(request)
    if path not in PATHS:
        _bump("model_proxy_refused_path")
        raise HTTPException(status_code=404, detail=f"/{path} is not proxied.")
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        _bump("model_proxy_not_configured")
        log.error("model proxy: ANTHROPIC_API_KEY is not configured for this app; the agent cannot chat")
        return JSONResponse(status_code=503, content={
            "type": "error", "error": {"type": "model_not_configured",
                                       "message": "No model key is configured for this tenant's app."}})
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(status_code=413, detail="Request too large.")
    headers = {h: request.headers[h] for h in PASS_REQUEST_HEADERS if h in request.headers}
    headers["x-api-key"] = key
    client = httpx.AsyncClient(timeout=TIMEOUT)
    try:
        upstream = await client.send(
            client.build_request("POST", f"{_upstream()}/{path}", content=body, headers=headers,
                                 params=dict(request.query_params)),
            stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        _bump("model_proxy_upstream_error")
        log.error("model proxy: upstream unreachable (%s)", type(exc).__name__)
        return JSONResponse(status_code=502, content={
            "type": "error", "error": {"type": "upstream_unreachable", "message": type(exc).__name__}})
    _bump("model_proxy_requests")
    if upstream.status_code >= 400:
        _bump("model_proxy_upstream_error")
        log.error("model proxy: upstream returned HTTP %s", upstream.status_code)

    async def close():
        await upstream.aclose()
        await client.aclose()

    out_headers = {h: upstream.headers[h] for h in PASS_RESPONSE_HEADERS if h in upstream.headers}
    return StreamingResponse(upstream.aiter_bytes(), status_code=upstream.status_code, headers=out_headers,
                             background=BackgroundTask(close))
