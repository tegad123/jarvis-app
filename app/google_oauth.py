"""Staff Google sign-in (OpenID) and tenant mailbox connect (five scopes).

LOGIN SCOPES ARE openid / email / profile. CONNECT SCOPES ARE the five
Restricted/Sensitive mailbox scopes. Mixing them onto login would put mailbox
consent on every staff member.

State is a signed cookie (nonce + purpose + expiry) plus a row in
accounts.oauth_states that is consumed on first use. Replay is rejected.
Do not import app.oauth_demo — that module's single _oauth_state slot is
one in-memory value for the whole process.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import accounts, auth, config, google_connection, threads as threads_mod

log = logging.getLogger(__name__)

# Google expands OpenID scopes (email/profile → userinfo.email/userinfo.profile).
# oauthlib then raises Warning("Scope has changed ...") unless this is set.
# Required on the live cutover drop-in as well as testco.
_RELAX_SCOPE_ENV = "OAUTHLIB_RELAX_TOKEN_SCOPE"

# Strip token-ish query values if an exception message includes a URL or body.
_REDACT = re.compile(
    r"(?i)\b(code|access_token|refresh_token|id_token|client_secret)=[^\s&]+"
)

LOGIN_SCOPES = ("openid", "email", "profile")
CONNECT_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
)
_MAILBOX = ("gmail", "calendar", "spreadsheets", "drive.file", "drive")
assert not any(any(m in s for m in _MAILBOX) for s in LOGIN_SCOPES)
assert all("googleapis.com/auth/" in s for s in CONNECT_SCOPES)

PURPOSE_LOGIN = "login"
PURPOSE_CONNECT = "connect"
_GENERIC = "That PIN is not right."
_STATE_SALT = "jarvis-oauth-state-v1"
_PARTIAL_MSG = "all five permissions are required"
_SIGNIN_AGAIN = "sign in again then connect"

# Mock authorization codes: code -> record. Testco only. Never logged.
_mock_codes: Dict[str, Dict[str, Any]] = {}


def _state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.require("APP_SESSION_SECRET"), salt=_STATE_SALT)


def _origin_or_page() -> Optional[str]:
    return config.public_origin()


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


def _missing_token_key_page() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Google connection unavailable</title>"
        "<body style='font-family:sans-serif;max-width:36em;margin:4em auto'>"
        "<h1>Google connection cannot start</h1>"
        "<p>APP_GOOGLE_TOKEN_KEY is not set. Connect routes cannot encrypt a "
        "mailbox token without it. Sign-in with PIN is unaffected.</p>"
        "<p><a href='/'>Back</a></p>",
        status_code=503,
    )


def _partial_grant_page() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Google connection incomplete</title>"
        "<body style='font-family:sans-serif;max-width:36em;margin:4em auto'>"
        f"<h1>{_PARTIAL_MSG}</h1>"
        "<p>Google did not grant every mailbox permission this connection needs. "
        "Nothing was stored.</p>"
        "<p><a href='/'>Back</a></p>",
        status_code=403,
    )


def _signin_again_page(status_code: int = 401) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Sign in again</title>"
        "<body style='font-family:sans-serif;max-width:36em;margin:4em auto'>"
        f"<h1>{_SIGNIN_AGAIN}</h1>"
        "<p>Connecting Gmail needs an owner or admin session. Sign in, then "
        "start Connect Google again. Nothing was stored.</p>"
        "<p><a href='/'>Back</a></p>",
        status_code=status_code,
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


def _mint_state(purpose: str) -> Tuple[str, str]:
    nonce = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + config.OAUTH_STATE_MAX_AGE
    accounts.put_oauth_state(nonce, purpose, expires_at)
    token = _state_serializer().dumps(
        {"nonce": nonce, "purpose": purpose, "exp": expires_at}
    )
    return nonce, token


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

    nonce, token = _mint_state(PURPOSE_LOGIN)
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


def start_connect(request: Request, response: Response, sess: dict):
    """Five-scope tenant connect. Caller already required owner/admin person."""
    origin = _origin_or_page()
    if not origin:
        return _missing_origin_page()
    if config.google_token_key_list() is None:
        return _missing_token_key_page()
    mock = config.oauth_mock()
    if not mock and config.oauth_client_path() is None:
        return _missing_client_page()

    nonce, token = _mint_state(PURPOSE_CONNECT)
    redirect_uri = f"{origin}/oauth/callback"
    if mock:
        loc = (
            f"{origin}/api/auth/google/mock/authorize?"
            + urlencode({"state": nonce, "purpose": PURPOSE_CONNECT})
        )
    else:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            str(config.oauth_client_path()),
            scopes=list(CONNECT_SCOPES),
            redirect_uri=redirect_uri,
        )
        loc, _ = flow.authorization_url(
            state=nonce,
            access_type="offline",
            prompt="consent",
            include_granted_scopes="false",
        )
    redir = RedirectResponse(loc, status_code=302)
    _set_state_cookie(redir, token)
    return redir


def mock_authorize(
    request: Request,
    email: Optional[str],
    state: Optional[str],
    purpose: Optional[str] = None,
    grant: Optional[str] = None,
):
    """Local fake consent. Exists only when APP_GOOGLE_OAUTH_MOCK is on.

    grant=partial mints 4 of 5 connect scopes. grant=full (default for connect)
    mints all five. Login ignores grant.
    """
    if not config.oauth_mock():
        raise HTTPException(status_code=404, detail="Not found.")
    origin = config.public_origin()
    if not origin:
        return _missing_origin_page()
    addr = (email or "").strip()
    if not addr or not state:
        raise HTTPException(status_code=400, detail=_GENERIC)
    purpose = (purpose or PURPOSE_LOGIN).strip() or PURPOSE_LOGIN
    if purpose not in (PURPOSE_LOGIN, PURPOSE_CONNECT):
        raise HTTPException(status_code=400, detail=_GENERIC)
    if purpose == PURPOSE_CONNECT:
        if (grant or "full").strip().lower() == "partial":
            scopes = CONNECT_SCOPES[:-1]  # 4 of 5; drop drive.file
        else:
            scopes = CONNECT_SCOPES
    else:
        scopes = LOGIN_SCOPES
    code = secrets.token_urlsafe(24)
    _mock_codes[code] = {
        "email": addr,
        "exp": time.time() + 120,
        "purpose": purpose,
        "scopes": list(scopes),
        "refresh_token": "mock-refresh-" + secrets.token_urlsafe(16),
        "access_token": "mock-access-" + secrets.token_urlsafe(8),
    }
    loc = f"{origin}/oauth/callback?{urlencode({'code': code, 'state': state})}"
    return RedirectResponse(loc, status_code=302)


def mock_token_response(code: str) -> dict:
    """Google-shaped token JSON. Mock mode only; never logs the code."""
    rec = _exchange_mock(code)
    return {
        "token_type": "Bearer",
        "access_token": rec.get("access_token") or "mock",
        "expires_in": 3600,
        "scope": " ".join(rec.get("scopes") or LOGIN_SCOPES),
        "email": rec["email"],
    }


def _exchange_mock(code: str) -> Dict[str, Any]:
    rec = _mock_codes.pop(code, None)
    if not rec:
        raise KeyError("unknown code")
    if time.time() > rec["exp"]:
        raise KeyError("expired code")
    return rec


def _relax_token_scope() -> None:
    os.environ.setdefault(_RELAX_SCOPE_ENV, "1")


def _safe_exc_text(exc: BaseException) -> str:
    """Exception text with token-ish query values stripped. Never log .token."""
    return _REDACT.sub(r"\1=<redacted>", str(exc))


def _log_exchange_failure(op: str, exc: BaseException) -> None:
    log.exception(
        "%s token exchange failed (%s: %s)",
        op,
        type(exc).__name__,
        _safe_exc_text(exc),
    )


def _exchange_google_login(code: str, origin: str) -> str:
    from google.auth.transport import requests as greq
    from google.oauth2 import id_token as gid
    from google_auth_oauthlib.flow import Flow

    _relax_token_scope()
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


def _exchange_google_connect(code: str, origin: str) -> Dict[str, Any]:
    from google_auth_oauthlib.flow import Flow

    redirect_uri = f"{origin}/oauth/callback"
    _relax_token_scope()
    flow = Flow.from_client_secrets_file(
        str(config.oauth_client_path()),
        scopes=list(CONNECT_SCOPES),
        redirect_uri=redirect_uri,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    granted = " ".join(creds.scopes or [])
    email = ""
    raw = getattr(creds, "id_token", None)
    if raw:
        try:
            from google.auth.transport import requests as greq
            from google.oauth2 import id_token as gid
            client_id = flow.client_config.get("client_id") if flow.client_config else None
            claims = gid.verify_oauth2_token(raw, greq.Request(), audience=client_id)
            email = (claims.get("email") or "").strip()
        except Exception:
            log.info("google connect: id_token email unavailable")
    expiry = None
    if creds.expiry:
        expiry = creds.expiry.isoformat()
    return {
        "email": email,
        "scopes": granted,
        "refresh_token": creds.refresh_token,
        "access_token": creds.token,
        "expiry": expiry,
    }


def _read_state_cookie(request: Request) -> Optional[dict]:
    raw = request.cookies.get(config.OAUTH_STATE_COOKIE)
    if not raw:
        return None
    try:
        return _state_serializer().loads(raw, max_age=config.OAUTH_STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def _scope_set(raw: str) -> set:
    out = set()
    for part in (raw or "").split():
        part = part.strip()
        if not part:
            continue
        out.add(part)
        if not part.startswith("https://"):
            out.add("https://www.googleapis.com/auth/" + part)
    return out


def granted_all_five(granted: str) -> bool:
    have = _scope_set(granted)
    return all(s in have for s in CONNECT_SCOPES)


def callback(request: Request, response: Response):
    """Dispatch on signed-state purpose. Login is unauthenticated; connect is not."""
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
    purpose = cookie.get("purpose")
    if purpose == PURPOSE_LOGIN:
        return _callback_login(request, code, q_state, origin)
    if purpose == PURPOSE_CONNECT:
        return _callback_connect(request, code, q_state, origin)
    return _fail_response(request, status_code=400)


def _callback_login(request: Request, code: str, q_state: str, origin: str):
    if not accounts.consume_oauth_state(q_state, PURPOSE_LOGIN):
        return _fail_response(request, status_code=401)
    if not code:
        return _fail_response(request, status_code=400)

    try:
        if config.oauth_mock():
            email = _exchange_mock(code)["email"]
        else:
            email = _exchange_google_login(code, origin)
    except Exception as exc:
        _log_exchange_failure("google login", exc)
        return _fail_response(request, status_code=401)

    acct = accounts.get_by_email(email)
    if not acct or acct.get("disabled"):
        return _fail_response(request, status_code=401)

    auth.clear_failures(request)
    accounts.mark_login(acct["person_key"])
    threads_mod.ensure_direct(acct["person_key"])
    redir = RedirectResponse("/", status_code=302)
    _clear_state_cookie(redir)
    auth.issue_cookie(redir, person_key=acct["person_key"], role=acct["role"])
    return redir


def _callback_connect(request: Request, code: str, q_state: str, origin: str):
    if not accounts.consume_oauth_state(q_state, PURPOSE_CONNECT):
        return _fail_response(request, status_code=401)
    # FIRST after consume: same gates as GET /api/google/connect/start.
    # Missing/invalid → 401; member/operator → 403. Do not exchange the code.
    raw = request.cookies.get(config.COOKIE_NAME)
    if not raw:
        resp = _signin_again_page(401)
        _clear_state_cookie(resp)
        return resp
    try:
        sess = auth.current_session(jarvis_session=raw)
        auth.require_person(sess)
        auth.require_admin(sess)
    except HTTPException as exc:
        code_out = exc.status_code if exc.status_code in (401, 403) else 401
        resp = _signin_again_page(code_out)
        _clear_state_cookie(resp)
        return resp

    if config.google_token_key_list() is None:
        resp = _missing_token_key_page()
        _clear_state_cookie(resp)
        return resp
    if not code:
        return _fail_response(request, status_code=400)

    try:
        if config.oauth_mock():
            rec = _exchange_mock(code)
            granted = " ".join(rec.get("scopes") or [])
            payload = {
                "email": rec["email"],
                "scopes": granted,
                "refresh_token": rec.get("refresh_token"),
                "access_token": rec.get("access_token"),
                "expiry": None,
            }
        else:
            payload = _exchange_google_connect(code, origin)
            granted = payload.get("scopes") or ""
    except Exception as exc:
        _log_exchange_failure("google connect", exc)
        return _fail_response(request, status_code=401)

    if not granted_all_five(granted):
        resp = _partial_grant_page()
        _clear_state_cookie(resp)
        return resp

    email = (payload.get("email") or "").strip()
    if not email:
        log.info("google connect: no mailbox email on grant")
        return _fail_response(request, status_code=401)

    try:
        cipher = google_connection.encrypt_tokens({
            "refresh_token": payload.get("refresh_token"),
            "access_token": payload.get("access_token"),
            "expiry": payload.get("expiry"),
            "scopes": granted,
        })
        google_connection.put_connected(
            google_email=email,
            scopes=granted,
            token_cipher=cipher,
            connected_by=auth.person_of(sess),
        )
    except Exception as exc:
        log.exception(
            "google connect: encrypt/store failed (%s: %s)",
            type(exc).__name__,
            _safe_exc_text(exc),
        )
        return _fail_response(request, status_code=401)

    redir = RedirectResponse("/", status_code=302)
    _clear_state_cookie(redir)
    return redir


def _revoke_at_google(token: str) -> None:
    """Revoke. Mock skips the network. HTTP errors are logged as status only."""
    if config.oauth_mock():
        log.info("google revoke: mock status=200")
        return
    data = urllib.parse.urlencode({"token": token}).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/revoke",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("google revoke: status=%s", getattr(resp, "status", "?"))
    except urllib.error.HTTPError as exc:
        log.info("google revoke: status=%s", exc.code)
    except Exception as exc:
        log.info("google revoke: status=%s", type(exc).__name__)


def disconnect(sess: dict) -> dict:
    """Revoke (best-effort), then delete the row. Cipher is gone."""
    row = google_connection.get_row()
    token = None
    if row and row.get("token_cipher"):
        try:
            creds = google_connection.build_credentials()
            token = creds.refresh_token or creds.token
        except google_connection.ConnectionCipherError:
            log.info("google disconnect: cipher unreadable, skipping revoke")
        except Exception:
            log.info("google disconnect: no usable token, skipping revoke")
    if token:
        _revoke_at_google(token)
    google_connection.clear()
    return {"ok": True, "status": google_connection.STATUS_DISCONNECTED}


# Kept so older imports of callback_login still hit the dispatcher.
callback_login = callback
