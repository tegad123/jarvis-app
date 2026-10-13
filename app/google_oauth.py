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

import html
import json
import logging
import os
import re
import secrets
import sys
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
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

# Mock authorization codes: code -> record. Testco only. Never logged.
_mock_codes: Dict[str, Dict[str, Any]] = {}
# Mock Gmail users.getProfile('me') bodies, keyed by access_token. Same JSON
# shape as Google. Connect must not learn the mailbox from an id_token.
_mock_gmail_profiles: Dict[str, Dict[str, Any]] = {}


class GmailProfileError(RuntimeError):
    """users.getProfile('me') failed or had no emailAddress."""


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


def _no_email_page() -> HTMLResponse:
    return _mailbox_unidentified_page()


def _mailbox_unidentified_page() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Google connection incomplete</title>"
        "<body style='font-family:sans-serif;max-width:36em;margin:4em auto'>"
        "<h1>The mailbox could not be identified</h1>"
        "<p>The grant completed but Gmail did not return the mailbox address. "
        "Nothing was stored. Retry Connect Google signed in as the assistant "
        "account.</p>"
        "<p><a href='/'>Back</a></p>",
        status_code=401,
    )


def _mismatch_page(got: str, expected: str) -> HTMLResponse:
    """Owner/admin already has a session — naming both addresses is not enumeration."""
    g = html.escape(got)
    e = html.escape(expected)
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Wrong Google account</title>"
        "<body style='font-family:sans-serif;max-width:36em;margin:4em auto'>"
        "<h1>Connected Google account does not match the assistant mailbox</h1>"
        f"<p>Google returned <code>{g}</code>. This tenant's assistant is "
        f"<code>{e}</code>.</p>"
        "<p>Nothing was stored. Sign out of other Google accounts or use a "
        "dedicated browser profile, then Connect Google signed in as the "
        "assistant account.</p>"
        "<p><a href='/'>Back</a></p>",
        status_code=403,
    )


def _assistant_address() -> str:
    try:
        shared = str(config.SHARED)
        if shared not in sys.path:
            sys.path.insert(0, shared)
        import identity  # noqa: WPS433
        return (identity.load().get("assistant_address") or "").strip()
    except Exception:
        log.warning(
            "google connect: could not read identity.assistant_address",
            exc_info=True,
        )
        return ""


def _emails_match(got: str, expected: str) -> bool:
    return got.strip().lower() == expected.strip().lower()


def _log_connect_refusal(
    reason: str,
    *,
    google_email: str = "",
    expected: str = "",
    granted: str = "",
    outcome: str = "",
) -> None:
    """INFO only. Never tokens. google_email/granted/outcome on every refuse."""
    log.info(
        "google connect refused: reason=%s google_email=%s expected=%s "
        "granted=%s outcome=%s",
        reason,
        google_email or "(none)",
        expected or "(none)",
        granted or "(none)",
        outcome or reason,
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


def _begin_state(purpose: str) -> Tuple[str, int]:
    nonce = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + config.OAUTH_STATE_MAX_AGE
    accounts.put_oauth_state(nonce, purpose, expires_at)
    return nonce, expires_at


def _sign_state_cookie(
    nonce: str,
    purpose: str,
    expires_at: int,
    code_verifier: Optional[str] = None,
) -> str:
    """Signed state cookie. code_verifier is PKCE, never logged."""
    payload: Dict[str, Any] = {
        "nonce": nonce,
        "purpose": purpose,
        "exp": expires_at,
    }
    if code_verifier:
        payload["code_verifier"] = code_verifier
    return _state_serializer().dumps(payload)


def _mint_state(purpose: str, code_verifier: Optional[str] = None) -> Tuple[str, str]:
    nonce, expires_at = _begin_state(purpose)
    return nonce, _sign_state_cookie(nonce, purpose, expires_at, code_verifier)


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

    nonce, expires_at = _begin_state(PURPOSE_LOGIN)
    redirect_uri = f"{origin}/oauth/callback"
    verifier = None
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
        verifier = flow.code_verifier
        if not verifier:
            log.error("google login: Flow produced no code_verifier")
    token = _sign_state_cookie(nonce, PURPOSE_LOGIN, expires_at, verifier)
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

    nonce, expires_at = _begin_state(PURPOSE_CONNECT)
    redirect_uri = f"{origin}/oauth/callback"
    verifier = None
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
        verifier = flow.code_verifier
        if not verifier:
            log.error("google connect: Flow produced no code_verifier")
    token = _sign_state_cookie(nonce, PURPOSE_CONNECT, expires_at, verifier)
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
    access_token = "mock-access-" + secrets.token_urlsafe(8)
    rec: Dict[str, Any] = {
        "exp": time.time() + 120,
        "purpose": purpose,
        "scopes": list(scopes),
        "refresh_token": "mock-refresh-" + secrets.token_urlsafe(16),
        "access_token": access_token,
    }
    if purpose == PURPOSE_CONNECT:
        # Same JSON shape as GET .../gmail/v1/users/me/profile. No id_token.
        _mock_gmail_profiles[access_token] = {"emailAddress": addr}
    else:
        rec["email"] = addr
    _mock_codes[code] = rec
    loc = f"{origin}/oauth/callback?{urlencode({'code': code, 'state': state})}"
    return RedirectResponse(loc, status_code=302)


def mock_token_response(code: str) -> dict:
    """Google-shaped token JSON. Mock mode only; never logs the code.

    Connect tokens match real Google: no id_token (openid was not requested).
    Login may include email for the OpenID mock; connect must not.
    """
    rec = _exchange_mock(code)
    out: Dict[str, Any] = {
        "token_type": "Bearer",
        "access_token": rec.get("access_token") or "mock",
        "expires_in": 3600,
        "scope": " ".join(rec.get("scopes") or LOGIN_SCOPES),
        "refresh_token": rec.get("refresh_token"),
    }
    if rec.get("purpose") == PURPOSE_LOGIN and rec.get("email"):
        out["email"] = rec["email"]
    return out


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


def _exchange_google_login(
    code: str, origin: str, code_verifier: Optional[str] = None,
) -> str:
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
    if code_verifier:
        flow.code_verifier = code_verifier
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


def _mock_gmail_get_profile(access_token: str) -> dict:
    """In-process GET .../users/me/profile. Same JSON shape as Google."""
    body = _mock_gmail_profiles.get(access_token)
    if body is None:
        raise KeyError("unknown mock access_token")
    return dict(body)


def _gmail_profile_email(access_token: str) -> str:
    """Mailbox address from Gmail users.getProfile('me').emailAddress.

    No extra scopes. Mock and real parse the same JSON key. Never reads id_token.
    """
    if not access_token:
        raise GmailProfileError("no access_token")
    try:
        if config.oauth_mock():
            data = _mock_gmail_get_profile(access_token)
        else:
            req = urllib.request.Request(
                GMAIL_PROFILE_URL,
                headers={"Authorization": "Bearer " + access_token},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise GmailProfileError("gmail profile is not an object")
        email = (data.get("emailAddress") or "").strip()
        if not email:
            raise GmailProfileError("gmail profile has no emailAddress")
        return email
    except GmailProfileError:
        raise
    except Exception as exc:
        log.info("google connect: gmail profile failed (%s)", type(exc).__name__)
        raise GmailProfileError(type(exc).__name__) from exc


def _exchange_google_connect(
    code: str, origin: str, code_verifier: Optional[str] = None,
) -> Dict[str, Any]:
    """Token exchange then Gmail profile. Never takes email from an id_token."""
    if config.oauth_mock():
        rec = _exchange_mock(code)
        access = rec.get("access_token") or ""
        granted = " ".join(rec.get("scopes") or [])
        refresh = rec.get("refresh_token")
        expiry = None
    else:
        from google_auth_oauthlib.flow import Flow

        redirect_uri = f"{origin}/oauth/callback"
        _relax_token_scope()
        flow = Flow.from_client_secrets_file(
            str(config.oauth_client_path()),
            scopes=list(CONNECT_SCOPES),
            redirect_uri=redirect_uri,
        )
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(code=code)
        creds = flow.credentials
        access = creds.token or ""
        granted = " ".join(creds.scopes or [])
        refresh = creds.refresh_token
        expiry = creds.expiry.isoformat() if creds.expiry else None

    email = _gmail_profile_email(access)
    return {
        "email": email,
        "scopes": granted,
        "refresh_token": refresh,
        "access_token": access,
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
        return _callback_login(request, code, q_state, origin, cookie)
    if purpose == PURPOSE_CONNECT:
        return _callback_connect(request, code, q_state, origin, cookie)
    return _fail_response(request, status_code=400)


def _callback_login(
    request: Request, code: str, q_state: str, origin: str, cookie: dict,
):
    if not accounts.consume_oauth_state(q_state, PURPOSE_LOGIN):
        return _fail_response(request, status_code=401)
    if not code:
        return _fail_response(request, status_code=400)

    verifier = (cookie.get("code_verifier") or "").strip() or None
    try:
        if config.oauth_mock():
            email = _exchange_mock(code)["email"]
        else:
            email = _exchange_google_login(code, origin, code_verifier=verifier)
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


def _callback_connect(
    request: Request, code: str, q_state: str, origin: str, cookie: dict,
):
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

    verifier = (cookie.get("code_verifier") or "").strip() or None
    try:
        payload = _exchange_google_connect(
            code, origin, code_verifier=verifier,
        )
        granted = payload.get("scopes") or ""
    except GmailProfileError as exc:
        cause = type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__
        log.info("google connect: mailbox unidentified (%s)", cause)
        _log_connect_refusal(
            "no_email",
            google_email="",
            expected=_assistant_address(),
            outcome=cause,
        )
        resp = _mailbox_unidentified_page()
        _clear_state_cookie(resp)
        return resp
    except Exception as exc:
        _log_exchange_failure("google connect", exc)
        _log_connect_refusal(
            "exchange",
            outcome=type(exc).__name__,
        )
        return _fail_response(request, status_code=401)

    email = (payload.get("email") or "").strip()
    expected = _assistant_address()
    granted = (payload.get("scopes") or granted or "").strip()

    if not granted_all_five(granted):
        _log_connect_refusal(
            "partial_grant",
            google_email=email,
            expected=expected,
            granted=granted,
            outcome="partial_grant",
        )
        resp = _partial_grant_page()
        _clear_state_cookie(resp)
        return resp

    if not email:
        _log_connect_refusal(
            "no_email",
            google_email="",
            expected=expected,
            granted=granted,
            outcome="no_email",
        )
        resp = _no_email_page()
        _clear_state_cookie(resp)
        return resp

    if expected and not _emails_match(email, expected):
        _log_connect_refusal(
            "mismatch",
            google_email=email,
            expected=expected,
            granted=granted,
            outcome="mismatch",
        )
        resp = _mismatch_page(email, expected)
        _clear_state_cookie(resp)
        return resp

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
        _log_connect_refusal(
            "store",
            google_email=email,
            expected=expected,
            granted=granted,
            outcome=type(exc).__name__,
        )
        return _fail_response(request, status_code=401)

    log.info(
        "google connect: google_email=%s expected=%s granted=%s outcome=connected",
        email,
        expected or "(none)",
        granted,
    )
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
