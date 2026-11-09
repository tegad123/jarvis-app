"""Phone app sign-in: tokens for the Jarvis mobile app.

The web signs in with a cookie. The phone app holds two tokens instead:

- an access token, signed with the same secret as the cookie and good for
  ACCESS_SECONDS, sent as `Authorization: Bearer ja1.<token>`.
  auth.current_session reads it and runs the same _live() check as a cookie,
  so every route serves the web and the phone the same way;
- a refresh token, random, kept here only as a hash, traded for a new pair.
  A phone unused for IDLE_DAYS has to sign in again.

Each phone is a row in app_devices. Signing out, or revoking it from
Settings, ends that phone's access on its next request.

A phone first signs in one of two ways:

- GET /app/start in the phone's system sign-in sheet. That page uses the web
  sign-in the person already has (their cookie, or Google, which comes back
  here), then sends a one-time code to the app's redirect address. The code
  trades only with the PKCE verifier the app kept, and only once.
- POST /api/app/login with a PIN, where PIN sign-in is allowed. Same throttle,
  same lock and same Google-only rule as the web login.

The break-glass operator passphrase never signs in a phone.
"""
import base64
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from . import auth, config

log = logging.getLogger("jarvis.devices")

PREFIX = "ja1."
ACCESS_SECONDS = int(config.get("APP_MOBILE_ACCESS_SECONDS", "3600"))
IDLE_DAYS = int(config.get("APP_MOBILE_IDLE_DAYS", "60"))
CODE_SECONDS = 120
PENDING_SECONDS = 600
PENDING_COOKIE = "jarvis_app_pending"
SEEN_EVERY = timedelta(minutes=5)
PLATFORMS = ("ios", "android")
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
_ACCESS_SALT = "jarvis-app-access-v1"
_PENDING_SALT = "jarvis-app-pending-v1"

router = APIRouter()

_ready = False
_ready_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _bump(name: str) -> None:
    try:
        from . import threads  # noqa: PLC0415
        threads.bump_counter(name)
    except Exception:
        log.error("devices: counter bump failed (%s)", name)


def _db():
    global _ready
    from . import accounts  # noqa: PLC0415
    conn = accounts._ensure()
    if not _ready:
        with _ready_lock:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS app_devices (
                    device_id TEXT PRIMARY KEY, person_key TEXT NOT NULL, name TEXT NOT NULL,
                    platform TEXT NOT NULL, refresh_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL, revoked_at TEXT, revoked_by TEXT);
                CREATE INDEX IF NOT EXISTS app_devices_person ON app_devices (person_key);
                CREATE TABLE IF NOT EXISTS app_handoffs (
                    code_hash TEXT PRIMARY KEY, person_key TEXT NOT NULL, challenge TEXT NOT NULL,
                    expires_at INTEGER NOT NULL, used_at TEXT);
            """)
            _ready = True
    return conn


def redirects() -> List[str]:
    """Where /app/start may send a code: the app's own address, nowhere else."""
    raw = config.get("APP_MOBILE_REDIRECTS", "jarvis://auth") or ""
    return [r.strip() for r in raw.split(",") if r.strip()]


def _access_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.require("APP_SESSION_SECRET"), salt=_ACCESS_SALT)


def _pending_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.require("APP_SESSION_SECRET"), salt=_PENDING_SALT)


def _emit(headline: str, detail: str, kind: str, person: str) -> None:
    """Feed event, visible to that person and to owner and admin. Never raises."""
    try:
        import sys  # noqa: PLC0415
        if str(config.SHARED) not in sys.path:
            sys.path.insert(0, str(config.SHARED))
        import notify  # noqa: PLC0415
        notify.notify("devices", headline, headline=headline, detail=detail, kind=kind, skill="devices",
                      subject=None, attachments=[{"type": "device", "owner_person": person}])
    except Exception:
        _bump("devices_feed_write_failed")
        log.error("devices: feed write failed for %s", kind)


# ── tokens ────────────────────────────────────────────────────────────────────

def bearer_token(authorization: Optional[str]) -> Optional[str]:
    """The app token in an Authorization header, or None for anything else."""
    if not isinstance(authorization, str) or not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value.startswith(PREFIX):
        return None
    return value[len(PREFIX):]


def _access(person_key: str, role: str, device_id: str) -> str:
    payload = {"sid": secrets.token_hex(16), "iat": int(time.time()), "person_key": person_key,
               "role": role, "device": device_id}
    return PREFIX + _access_serializer().dumps(payload)


def session_from_access(token: str) -> dict:
    """The session an app token stands for. 401 when it is expired, forged,
    or its phone was signed out."""
    try:
        sess = _access_serializer().loads(token, max_age=ACCESS_SECONDS)
    except SignatureExpired:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired.")
    except BadSignature:
        _bump("app_token_refused")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session.")
    device = str(sess.get("device") or "")
    row = _db().execute("SELECT person_key, last_seen_at, revoked_at FROM app_devices WHERE device_id = ?",
                        (device,)).fetchone()
    if not row or row["person_key"] != sess.get("person_key") or row["revoked_at"]:
        _bump("app_token_device_revoked")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This phone was signed out.")
    try:
        if _now() - datetime.fromisoformat(row["last_seen_at"]) > SEEN_EVERY:
            conn = _db()
            conn.execute("UPDATE app_devices SET last_seen_at = ? WHERE device_id = ?", (_now().isoformat(), device))
            conn.commit()
    except Exception:
        log.warning("devices: could not record when a phone was last seen")
    return sess


def _tokens(person_key: str, role: str, device_id: str, refresh: str) -> Dict[str, Any]:
    return {"access_token": _access(person_key, role, device_id), "refresh_token": refresh,
            "device_id": device_id, "expires_in": ACCESS_SECONDS, "token_type": "Bearer",
            "person_key": person_key, "role": role, "tenant": config.tenant_slug() or None}


def _account(person_key: str) -> Dict[str, Any]:
    """The account as it is now. 401 when it is gone or switched off."""
    from . import accounts  # noqa: PLC0415
    acct = accounts.get(person_key)
    if not acct or acct.get("disabled"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Your access to this account was turned off.")
    return acct


def create_device(person_key: str, name: str, platform: str, how: str) -> Dict[str, Any]:
    acct = _account(person_key)
    device_id = "dev_" + secrets.token_hex(8)
    refresh = secrets.token_urlsafe(32)
    now = _now().isoformat()
    conn = _db()
    conn.execute("INSERT INTO app_devices (device_id, person_key, name, platform, refresh_hash, created_at, "
                 "last_seen_at) VALUES (?,?,?,?,?,?,?)",
                 (device_id, person_key, name, platform, _hash(refresh), now, now))
    conn.commit()
    try:
        from . import threads  # noqa: PLC0415
        threads.ensure_direct(person_key)
    except Exception:
        log.warning("devices: direct line not ensured for a new phone")
    _bump("app_device_signed_in")
    log.info("devices: a phone signed in (%s, %s)", platform, how)
    _emit(f"Signed in on {name}", f"{device_id} · {platform} · {how}", "device.signed_in", person_key)
    return _tokens(person_key, acct["role"], device_id, refresh)


def refresh_tokens(device_id: str, refresh: str) -> Dict[str, Any]:
    conn = _db()
    row = conn.execute("SELECT * FROM app_devices WHERE device_id = ?", (device_id,)).fetchone()
    if not row or row["revoked_at"] or not hmac.compare_digest(row["refresh_hash"], _hash(refresh or "")):
        _bump("app_refresh_refused")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in again on this phone.")
    if _now() - datetime.fromisoformat(row["last_seen_at"]) > timedelta(days=IDLE_DAYS):
        revoke(device_id, by="idle")
        _bump("app_refresh_idle")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in again on this phone.")
    try:
        acct = _account(row["person_key"])
    except HTTPException:
        _bump("app_refresh_account_off")
        raise
    new = secrets.token_urlsafe(32)
    cur = conn.execute("UPDATE app_devices SET refresh_hash = ?, last_seen_at = ? "
                       "WHERE device_id = ? AND refresh_hash = ? AND revoked_at IS NULL",
                       (_hash(new), _now().isoformat(), device_id, row["refresh_hash"]))
    conn.commit()
    if cur.rowcount != 1:
        _bump("app_refresh_refused")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in again on this phone.")
    _bump("app_refreshed")
    return _tokens(row["person_key"], acct["role"], device_id, new)


def revoke(device_id: str, by: str) -> bool:
    conn = _db()
    row = conn.execute("SELECT person_key, name FROM app_devices WHERE device_id = ?", (device_id,)).fetchone()
    cur = conn.execute("UPDATE app_devices SET revoked_at = ?, revoked_by = ? "
                       "WHERE device_id = ? AND revoked_at IS NULL", (_now().isoformat(), by, device_id))
    conn.commit()
    if cur.rowcount != 1:
        return False
    _bump("app_device_signed_out")
    log.info("devices: a phone was signed out (by %s)", "its owner" if by == row["person_key"] else by)
    _emit(f"Signed out on {row['name']}", f"{device_id} · by {by}", "device.signed_out", row["person_key"])
    return True


def listing(person_key: str) -> List[Dict[str, Any]]:
    rows = _db().execute("SELECT device_id, name, platform, created_at, last_seen_at FROM app_devices "
                         "WHERE person_key = ? AND revoked_at IS NULL ORDER BY last_seen_at DESC",
                         (person_key,)).fetchall()
    return [dict(r) for r in rows]


# ── the one-time code for the system sign-in sheet ───────────────────────────

def _mint_code(person_key: str, challenge: str) -> str:
    code = secrets.token_urlsafe(32)
    conn = _db()
    conn.execute("DELETE FROM app_handoffs WHERE expires_at < ?", (int(time.time()) - 3600,))
    conn.execute("INSERT INTO app_handoffs (code_hash, person_key, challenge, expires_at) VALUES (?,?,?,?)",
                 (_hash(code), person_key, challenge, int(time.time()) + CODE_SECONDS))
    conn.commit()
    return code


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def exchange_code(code: str, verifier: str) -> str:
    """The person a code was minted for. The code is spent on the first try,
    right verifier or not."""
    conn = _db()
    h = _hash(code or "")
    row = conn.execute("SELECT person_key, challenge, expires_at, used_at FROM app_handoffs WHERE code_hash = ?",
                       (h,)).fetchone()
    cur = conn.execute("UPDATE app_handoffs SET used_at = ? WHERE code_hash = ? AND used_at IS NULL",
                       (_now().isoformat(), h))
    conn.commit()
    if not row or cur.rowcount != 1 or row["expires_at"] < int(time.time()):
        _bump("app_code_refused")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="That sign-in has expired. Try again.")
    if not hmac.compare_digest(row["challenge"], _s256(verifier or "")):
        _bump("app_code_verifier_refused")
        log.warning("devices: a sign-in code came back with the wrong verifier; refused")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="That sign-in has expired. Try again.")
    return row["person_key"]


def pending(request: Request) -> Optional[Dict[str, str]]:
    raw = request.cookies.get(PENDING_COOKIE)
    if not raw:
        return None
    try:
        data = _pending_serializer().loads(raw, max_age=PENDING_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    if not _CHALLENGE.fullmatch(str(data.get("challenge") or "")) or data.get("redirect") not in redirects():
        return None
    return data


def after_login_path(request: Request) -> str:
    """Where Google sign-in lands: back in the phone's sheet when it started
    there, the web app otherwise."""
    return "/app/start" if pending(request) else "/"


def _page(text: str, code: int) -> HTMLResponse:
    from html import escape  # noqa: PLC0415
    return HTMLResponse(
        "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Jarvis</title><body style='font-family:system-ui;background:#0a0a0a;color:#eee;padding:32px'>"
        f"<h1>Jarvis</h1><p>{escape(text)}</p></body>",
        status_code=code, headers={"Cache-Control": "no-store"})


@router.get("/app/start", include_in_schema=False)
def app_start(request: Request, challenge: Optional[str] = Query(None), redirect: Optional[str] = Query(None)):
    if challenge is not None or redirect is not None:
        target = redirect or (redirects()[0] if redirects() else "")
        if not challenge or not _CHALLENGE.fullmatch(challenge) or target not in redirects():
            _bump("app_start_refused")
            return _page("This sign-in link is not valid. Start again from the Jarvis app.", 400)
        want = {"challenge": challenge, "redirect": target}
    else:
        want = pending(request)
        if not want:
            return _page("Start signing in from the Jarvis app.", 400)
    try:
        sess = auth.current_session(jarvis_session=request.cookies.get(config.COOKIE_NAME))
    except HTTPException:
        sess = None
    if sess is None:
        resp = RedirectResponse("/api/auth/google/start", status_code=302)
        resp.set_cookie(PENDING_COOKIE, _pending_serializer().dumps(want), max_age=PENDING_SECONDS,
                        httponly=True, secure=config.COOKIE_SECURE, samesite="lax", path="/")
        return resp
    person = auth.person_of(sess)
    if not person:
        _bump("app_start_operator_refused")
        return _page("Operator access cannot sign in a phone. Sign in as yourself.", 403)
    code = _mint_code(person, want["challenge"])
    query = {"code": code, "tenant": config.tenant_slug() or "", "origin": config.public_origin() or ""}
    resp = RedirectResponse(f"{want['redirect']}?{urlencode(query)}", status_code=302)
    resp.delete_cookie(PENDING_COOKIE, path="/")
    _bump("app_start_code_sent")
    return resp


# ── the app's API ─────────────────────────────────────────────────────────────

class DeviceInfo(BaseModel):
    device_name: str = Field("Phone", min_length=1, max_length=80)
    platform: str = Field("ios", max_length=16)


class ExchangeBody(DeviceInfo):
    code: str = Field(..., min_length=10, max_length=200)
    verifier: str = Field(..., min_length=43, max_length=128)


class PinBody(DeviceInfo):
    person_key: str = Field(..., min_length=1, max_length=64)
    pin: str = Field(..., min_length=1, max_length=64)


class RefreshBody(BaseModel):
    device_id: str = Field(..., min_length=4, max_length=40)
    refresh_token: str = Field(..., min_length=10, max_length=200)


def _info(body: DeviceInfo) -> Dict[str, str]:
    platform = body.platform.strip().lower()
    if platform not in PLATFORMS:
        raise HTTPException(status_code=422, detail="platform must be ios or android.")
    return {"name": " ".join(body.device_name.split())[:80] or "Phone", "platform": platform}


def _throttle(request: Request) -> None:
    if auth.throttled(request):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Too many failed attempts. Try again shortly.")


@router.post("/api/app/exchange")
def post_exchange(body: ExchangeBody, request: Request):
    _throttle(request)
    info = _info(body)
    try:
        person = exchange_code(body.code, body.verifier)
    except HTTPException:
        auth.record_failure(request)
        raise
    from . import accounts  # noqa: PLC0415
    accounts.mark_login(person)
    return create_device(person, info["name"], info["platform"], "web sign-in")


@router.post("/api/app/login")
def post_app_login(body: PinBody, request: Request):
    _throttle(request)
    if not auth.secret_signin_allowed(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sign in with Google on this address.")
    info = _info(body)
    acct = auth.pin_signin(request, body.person_key, body.pin)
    return create_device(acct["person_key"], info["name"], info["platform"], "PIN")


@router.post("/api/app/refresh")
def post_refresh(body: RefreshBody, request: Request):
    _throttle(request)
    try:
        return refresh_tokens(body.device_id, body.refresh_token)
    except HTTPException:
        auth.record_failure(request)
        raise


@router.post("/api/app/signout")
def post_signout(sess: dict = Depends(auth.current_session)):
    device = sess.get("device")
    if not device:
        raise HTTPException(status_code=400, detail="This is not a phone session.")
    revoke(device, by=auth.person_of(sess) or "unknown")
    return {"ok": True}


@router.get("/api/devices")
def get_devices(sess: dict = Depends(auth.current_session)):
    auth.require_person(sess)
    return {"devices": listing(auth.person_of(sess)), "current": sess.get("device")}


@router.delete("/api/devices/{device_id}")
def delete_device(device_id: str, sess: dict = Depends(auth.current_session)):
    auth.require_person(sess)
    row = _db().execute("SELECT person_key FROM app_devices WHERE device_id = ? AND revoked_at IS NULL",
                        (device_id,)).fetchone()
    me = auth.person_of(sess)
    if not row or (row["person_key"] != me and auth.role_of(sess) != "owner"):
        raise HTTPException(status_code=404, detail="No such phone.")
    revoke(device_id, by=me)
    return {"ok": True}
