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
Only the Jarvis tools (app/agent_status.ALLOWED) reach the model: any other
tool in a request is stripped, counted and logged, and the names offered on the
latest request are kept in agent_tools_seen.json beside the threads database.
JARVIS_MODEL_UPSTREAM is honoured in mock mode only (verify sandboxes).
"""
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from . import agent_status
from . import config
from . import switches
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


TECH_WORDS = ("hermes", "terminal", "shell", "python", "script", "skill", "gateway", "sub-agent", "subagent",
              "api", "config", "token")


def _system_text(req: dict) -> str:
    sysp = req.get("system")
    if isinstance(sysp, list):
        return " ".join(str(b.get("text") or "") for b in sysp if isinstance(b, dict))
    return str(sysp or "")


def _allowlist_tools(body: bytes) -> bytes:
    try:
        req = json.loads(body)
    except ValueError:
        return body
    if not isinstance(req, dict) or not isinstance(req.get("tools"), list):
        return body
    offered = [str(t.get("name")) for t in req["tools"] if isinstance(t, dict)]
    kept, stripped = agent_status.strip_disallowed(req["tools"])
    if stripped:
        _bump("model_proxy_tool_stripped")
        log.error("model proxy: stripped %d tool(s) outside the allowlist: %s", len(stripped),
                  ", ".join(sorted(stripped))[:400])
        choice = req.get("tool_choice")
        if isinstance(choice, dict) and choice.get("name") in stripped:
            req.pop("tool_choice")
        if kept:
            req["tools"] = kept
        else:
            req.pop("tools")
            req.pop("tool_choice", None)
    system = _system_text(req).lower()
    try:
        seen = {"at": datetime.now(timezone.utc).isoformat(), "offered": sorted(offered),
                "sent": sorted(t["name"] for t in kept), "stripped": sorted(stripped),
                "system_chars": len(system),
                "system_word_counts": {w: system.count(w) for w in TECH_WORDS if w in system}}
        out = threads_mod.DB_PATH.parent / "agent_tools_seen.json"
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(seen, indent=1) + "\n")
        tmp.replace(out)
    except Exception as exc:
        _bump("model_proxy_tools_seen_failed")
        log.error("model proxy: could not record the tools seen (%s)", type(exc).__name__)
    return json.dumps(req).encode() if stripped else body


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
    if not switches.on("model_proxy"):
        _bump("model_proxy_switch_off")
        return JSONResponse(status_code=503, content={
            "type": "error", "error": {"type": "model_disabled",
                                       "message": "The model proxy is switched off for this tenant."}})
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
    body = _allowlist_tools(body)
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
    meter = UsageMeter(_requested_model(body), _purpose(request), path == "v1/messages" and upstream.status_code < 400)

    async def stream():
        async for chunk in upstream.aiter_bytes():
            meter.feed(chunk)
            yield chunk
        await meter.finish()

    return StreamingResponse(stream(), status_code=upstream.status_code, headers=out_headers,
                             background=BackgroundTask(close))


PURPOSES = ("agent", "background")
PRICES_PATH = Path(__file__).with_name("model_prices.json")


def _requested_model(body: bytes) -> str:
    try:
        return str(json.loads(body).get("model") or "")[:80]
    except Exception:
        return ""


def _purpose(request: Request) -> str:
    p = request.headers.get("x-jarvis-purpose", "agent").strip().lower()
    return p if p in PURPOSES else "agent"


def price(model: str, usage: dict):
    """USD for one response's usage, or None when the model has no price."""
    table = json.loads(PRICES_PATH.read_text())
    p = table.get(model)
    if not isinstance(p, dict):
        return None
    return (usage.get("input_tokens", 0) * p["input"] + usage.get("output_tokens", 0) * p["output"]
            + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]
            + usage.get("cache_read_input_tokens", 0) * p["cache_read"]) / 1_000_000


class UsageMeter:
    """Reads the token counts out of an Anthropic response as it streams
    past (SSE message_start / message_delta, or a plain JSON body) and adds
    them to the tenant's usage table for today. A response it cannot read is
    counted, never guessed."""

    def __init__(self, model: str, purpose: str, active: bool):
        self.model, self.purpose, self.active = model, purpose, active
        self.buf = b""
        self.usage = {}
        self.seen = False

    def feed(self, chunk: bytes) -> None:
        if self.active and len(self.buf) < MAX_BODY:
            self.buf += chunk

    def _merge(self, u: dict) -> None:
        for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            if isinstance(u.get(k), int):
                self.usage[k] = u[k]
        self.seen = True

    def _parse(self) -> None:
        text = self.buf.decode("utf-8", "replace")
        if text.lstrip().startswith("{"):
            obj = json.loads(text)
            self.model = obj.get("model") or self.model
            self._merge(obj.get("usage") or {})
            return
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            if ev.get("type") == "message_start":
                msg = ev.get("message") or {}
                self.model = msg.get("model") or self.model
                self._merge(msg.get("usage") or {})
            elif ev.get("type") == "message_delta":
                self._merge(ev.get("usage") or {})

    async def finish(self) -> None:
        if not self.active or not switches.on("sentinel"):
            return
        try:
            self._parse()
            if not self.seen:
                raise ValueError("no usage in the response")
            from . import platform_store  # noqa: PLC0415
            from starlette.concurrency import run_in_threadpool  # noqa: PLC0415
            cost = price(self.model, self.usage)
            if cost is None:
                _bump("model_usage_unpriced")
                log.error("model proxy: no price for %s; tokens recorded, cost unknown", self.model)
            await run_in_threadpool(
                platform_store.add_usage, datetime.now(timezone.utc).date(), self.model or "unknown",
                self.purpose, input_tokens=self.usage.get("input_tokens", 0),
                output_tokens=self.usage.get("output_tokens", 0),
                cache_read=self.usage.get("cache_read_input_tokens", 0),
                cache_write=self.usage.get("cache_creation_input_tokens", 0), cost_usd=cost)
        except Exception as exc:
            _bump("model_usage_record_failed")
            log.error("model proxy: usage not recorded (%s: %s)", type(exc).__name__, str(exc)[:200])
