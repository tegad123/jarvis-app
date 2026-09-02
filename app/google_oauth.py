"""Staff Google sign-in (OpenID only). Not the tenant mailbox connection.

LOGIN SCOPES ARE openid / email / profile. Never gmail, calendar, sheets, or
drive. Mixing Restricted scopes into login would put mailbox consent on every
staff member. Slice 4 owns those five.

State is a signed cookie (nonce + purpose + expiry) plus a row in
accounts.oauth_states that is consumed on first use. Replay is rejected.
Do not import app.oauth_demo — that module's single _oauth_state slot is
one in-memory value for the whole process.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import accounts, auth, config, threads as threads_mod

log = logging.getLogger(__name__)

LOGIN_SCOPES = ("openid", "email", "profile")
_MAILBOX = ("gmail", "calendar", "spreadsheets", "drive.file", "drive")
assert not any(any(m in s for m in _MAILBOX) for s in LOGIN_SCOPES)

PURPOSE_LOGIN = "login"
_GENERIC = "That PIN is not right."
_STATE_SALT = "jarvis-oauth-state-v1"

# Mock authorization codes: code -> (email, expires_at). Testco only.
_mock_codes: Dict[str, Tuple[str, float]] = {}


def _state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.require("APP_SESSION_SECRET"), salt=_STATE_SALT)


def _origin_or_page() -> Optional[str]:
    origin = config.public_origin()
    return origin


def _missing_origin_page() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Google sign-in unavailable</title>"
        "<body style='font-family:sans-serif;max-width:36em;margin:4em auto'>"
        "<h1>Google sign-in cannot start</h1>"
        "<p>APP_PUBLIC_ORIGIN is not set. It is the public hostname this app is "
        "reached at, and the OAuth redirect URI is built from it. The server "
        "will not guess the host from this request.</p>"
        "<p><a href='/'>Back</a></p>",
        status_code=503,
    )


def _missing_client_page() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Google sign-in unavailable</title>"
        "<body style='font-family:sans-serif;max-width:36em;margin:4em auto'>"
        "<h1>Google sign-in cannot start</h1>"
        "<p>No OAuth client file is configured (YAMIN_OAUTH_CLIENT / "
        "GOOGLE_OAUTH_CLIENT). Google sign-in is unavailable until that path "
        "is set.</p>"
        "<p><a href='/'>Back</a></p>",
        status_code=503,
    )


def _set_state_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        config.OAUTH_STATE_COOKIE,
        token,
        max_age=config.OAUTH_STATE_MAX_AGE,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _clear_state_cookie(response: Response) -> None:
    response.delete_cookie(config.OAUTH_STATE_COOKIE, path="/")


def _fail_response(request: Request, status_code: int = 401) -> JSONResponse:
    auth.record_failure(request)
    resp = JSONResponse({"detail": _GENERIC}, status_code=status_code)
    _clear_state_cookie(resp)
    return resp


def start_login(request: Request, response: Response):
    """Redirect to Google (or the local mock) with OpenID scopes only."""
    if auth.throttled(request):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again shortly.",
        )
    origin = _origin_or_page()
    if not origin:
        return _missing_origin_page()
    mock = config.oauth_mock()
    if not mock and config.oauth_client_path() is None:
        return _missing_client_page()

    nonce = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + config.OAUTH_STATE_MAX_AGE
    accounts.put_oauth_state(nonce, PURPOSE_LOGIN, expires_at)
    token = _state_serializer().dumps(
        {"nonce": nonce, "purpose": PURPOSE_LOGIN, "exp": expires_at}
    )
    redirect_uri = f"{origin}/oauth/callback"
    if mock:
        loc = f"{origin}/api/auth/google/mock/authorize?{urlencode({'state': nonce})}"
    else:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            str(config.oauth_client_path()),
            scopes=list(LOGIN_SCOPES),
            redirect_uri=redirect_uri,
        )
        loc, _ = flow.authorization_url(
            state=nonce,
            prompt="select_account",
            include_granted_scopes="false",
        )
    redir = RedirectResponse(loc, status_code=302)
    _set_state_cookie(redir, token)
    return redir


def mock_authorize(request: Request, email: Optional[str], state: Optional[str]):
    """Local fake consent screen. Exists only when APP_GOOGLE_OAUTH_MOCK is on."""
    if not config.oauth_mock():
        raise HTTPException(status_code=404, detail="Not found.")
    origin = config.public_origin()
    if not origin:
        return _missing_origin_page()
    addr = (email or "").strip()
    if not addr or not state:
        raise HTTPException(status_code=400, detail=_GENERIC)
    code = secrets.token_urlsafe(24)
    _mock_codes[code] = (addr, time.time() + 120)
    loc = f"{origin}/oauth/callback?{urlencode({'code': code, 'state': state})}"
    return RedirectResponse(loc, status_code=302)


def mock_token_response(code: str) -> dict:
    """Google-shaped token JSON. Mock mode only; never logs the code."""
    email = _exchange_mock(code)
    return {
        "token_type": "Bearer",
        "access_token": "mock",
        "expires_in": 3600,
        "scope": " ".join(LOGIN_SCOPES),
        "email": email,
    }


def _exchange_mock(code: str) -> str:
    rec = _mock_codes.pop(code, None)
    if not rec:
        raise KeyError("unknown code")
    email, exp = rec
    if time.time() > exp:
        raise KeyError("expired code")
    return email


def _exchange_google(code: str, origin: str) -> str:
    from google.auth.transport import requests as greq
    from google.oauth2 import id_token as gid
    from google_auth_oauthlib.flow import Flow

    redirect_uri = f"{origin}/oauth/callback"
    flow = Flow.from_client_secrets_file(
        str(config.oauth_client_path()),
        scopes=list(LOGIN_SCOPES),
        redirect_uri=redirect_uri,
    )
    flow.fetch_token(code=code)
    raw = flow.credentials.id_token
    if not raw:
        raise ValueError("no id_token")
    client_id = flow.client_config.get("client_id") if flow.client_config else None
    claims = gid.verify_oauth2_token(raw, greq.Request(), audience=client_id)
    email = (claims.get("email") or "").strip()
    if not email:
        raise ValueError("no email")
    return email


def _read_state_cookie(request: Request) -> Optional[dict]:
    raw = request.cookies.get(config.OAUTH_STATE_COOKIE)
    if not raw:
        return None
    try:
        return _state_serializer().loads(raw, max_age=config.OAUTH_STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def callback_login(request: Request, response: Response):
    """Consume state, map email → account, refuse if disabled, issue_cookie."""
    if auth.throttled(request):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again shortly.",
        )
    origin = config.public_origin()
    if not origin:
        resp = JSONResponse({"detail": "APP_PUBLIC_ORIGIN is not set."}, status_code=503)
        _clear_state_cookie(resp)
        return resp

    q_state = (request.query_params.get("state") or "").strip()
    code = (request.query_params.get("code") or "").strip()
    cookie = _read_state_cookie(request)

    if not cookie or not q_state or cookie.get("nonce") != q_state:
        return _fail_response(request, status_code=400)
    if cookie.get("purpose") != PURPOSE_LOGIN:
        return _fail_response(request, status_code=400)
    if not accounts.consume_oauth_state(q_state, PURPOSE_LOGIN):
        return _fail_response(request, status_code=401)
    if not code:
        return _fail_response(request, status_code=400)

    try:
        if config.oauth_mock():
            email = _exchange_mock(code)
        else:
            email = _exchange_google(code, origin)
    except Exception:
        log.info("google login: token exchange failed")
        return _fail_response(request, status_code=401)

    acct = accounts.get_by_email(email)
    # Unknown email, missing row, and disabled all take the same door.
    if not acct or acct.get("disabled"):
        return _fail_response(request, status_code=401)

    auth.clear_failures(request)
    accounts.mark_login(acct["person_key"])
    threads_mod.ensure_direct(acct["person_key"])
    redir = RedirectResponse("/", status_code=302)
    _clear_state_cookie(redir)
    auth.issue_cookie(redir, person_key=acct["person_key"], role=acct["role"])
    return redir
