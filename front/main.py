"""The front door: every <slug>.yamin.io address and join.yamin.io.

Cloudflare sends the whole wildcard here (one route, set once). For an
account's address this forwards the request, streaming, to that account's
app on loopback. join.yamin.io is the front door's own: invite links, Google
sign-up, "find my Jarvis", and Google's one redirect URI for every account.

Google answers at join.yamin.io/oauth/callback. A state that starts with
"<slug>." belongs to that account: the browser is sent on to the account's
own /oauth/callback with the same query, and the account does the exchange
with the PKCE verifier only it holds. The front door never sees an account's
tokens. A state that starts with "front." is the front door's own sign-in.

Requests carry X-Forwarded-For (replaced, never passed through), which is how
an account knows a request came from the public side. Agent-only routes
(/api/broker, /api/model) are refused here.
"""
import base64
import hashlib
import html
import http.cookiejar
import json
import logging
import os
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.background import BackgroundTask

from . import store

log = logging.getLogger("jarvis.front")

BASE = os.environ.get("FRONT_BASE_DOMAIN", "yamin.io").strip().lower()
JOIN = os.environ.get("FRONT_JOIN_HOST", f"join.{BASE}").strip().lower()
SCHEME = os.environ.get("FRONT_SCHEME", "https").strip()
# The sandbox serves http://join.front.test:8790; production has no port.
PORT_SUFFIX = os.environ.get("FRONT_URL_PORT", "").strip()
MOCK = os.environ.get("FRONT_OAUTH_MOCK", "").strip().lower() in ("1", "true", "yes")
CLIENT = os.environ.get("FRONT_OAUTH_CLIENT", "").strip()
INTERNAL = ("/api/broker/", "/api/broker", "/api/model/")
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers",
       "transfer-encoding", "upgrade", "host", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"}
STATE_COOKIE, SIGNUP_COOKIE, WAIT_COOKIE = "front_state", "front_signup", "front_wait"
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


def _secret() -> str:
    s = os.environ.get("FRONT_SECRET", "")
    if len(s) < 32:
        raise SystemExit("REFUSED: FRONT_SECRET (32+ chars) is required")
    return s


if MOCK and SCHEME == "https":
    raise SystemExit("REFUSED: FRONT_OAUTH_MOCK is for the loopback sandbox only")
_signer = URLSafeTimedSerializer(_secret(), salt="jarvis-front")
_mock_codes: Dict[str, Dict[str, Any]] = {}


class _NoJar(http.cookiejar.CookieJar):
    """One client serves everyone: it must never keep a Set-Cookie from one
    person's response and send it with the next person's request."""

    def set_cookie(self, cookie):
        pass

    def extract_cookies(self, response, request):
        pass


_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=None, write=60.0, pool=5.0),
                            follow_redirects=False, cookies=_NoJar())
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def url(host: str, path: str) -> str:
    return f"{SCHEME}://{host}{PORT_SUFFIX}{path}"


def account_host(slug: str) -> str:
    return f"{slug}.{BASE}"


def _bump(name: str) -> None:
    try:
        store.bump(store.connect(), name)
    except Exception:
        log.error("front: counter bump failed (%s)", name)


def _host(request: Request) -> str:
    return (request.headers.get("host") or "").split(":")[0].strip().lower()


def _local(request: Request) -> bool:
    return not (request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for"))


# ── pages ────────────────────────────────────────────────────────────────────

_STYLE = """
:root{color-scheme:dark;--bg:#0A0A0A;--fg:#FAFAFA;--muted:#A1A1A1;--line:#262626;--accent:#3B82F6}
*{box-sizing:border-box}body{margin:0;min-height:100dvh;display:grid;place-items:center;background:var(--bg);
color:var(--fg);font:16px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;padding:32px 16px}
main{width:100%;max-width:400px;display:grid;gap:14px}h1{margin:0;font-size:32px;font-weight:600;text-align:center}
p{margin:0;color:var(--muted);text-align:center}a.btn,button{display:block;width:100%;min-height:48px;border:0;
border-radius:12px;background:var(--accent);color:#fff;font:inherit;font-weight:600;text-align:center;
text-decoration:none;line-height:48px;cursor:pointer}label{display:grid;gap:6px;font-size:14px;color:var(--muted)}
input{min-height:48px;border-radius:12px;border:1px solid var(--line);background:#141414;color:var(--fg);
font:inherit;padding:0 14px}.err{color:#F87171;text-align:center}.addr{color:var(--fg);font-weight:600}
"""


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title>"
        f"<meta name=theme-color content='#0A0A0A'><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>", status_code=status,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


def _e(s: Any) -> str:
    return html.escape(str(s or ""))


# ── signed cookies ───────────────────────────────────────────────────────────

def _set(resp: Response, name: str, value: Dict[str, Any], max_age: int) -> None:
    resp.set_cookie(name, _signer.dumps(value), max_age=max_age, httponly=True, secure=SCHEME == "https",
                    samesite="lax", path="/")


def _get(request: Request, name: str, max_age: int) -> Optional[Dict[str, Any]]:
    raw = request.cookies.get(name)
    if not raw:
        return None
    try:
        return _signer.loads(raw, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


# ── Google, for the front door's own sign-in ─────────────────────────────────

def _client_config() -> Dict[str, str]:
    """Path only in logs; the contents never are."""
    with open(CLIENT) as f:
        cfg = json.load(f)
    return cfg.get("web") or cfg.get("installed") or {}


def _google_start(purpose: str, invite: str = "") -> Response:
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    state = f"front.{nonce}"
    redirect_uri = url(JOIN, "/oauth/callback")
    if MOCK:
        loc = url(JOIN, "/mock/authorize?" + urlencode({"state": state, "redirect_uri": redirect_uri}))
    else:
        if not CLIENT or not os.path.isfile(CLIENT):
            _bump("front_oauth_client_missing")
            log.error("front: FRONT_OAUTH_CLIENT is missing (path %s)", CLIENT or "unset")
            return page("Jarvis", "<h1>Jarvis</h1><p class=err>Google sign-in isn't set up yet.</p>", 503)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        loc = GOOGLE_AUTH + "?" + urlencode({
            "client_id": _client_config()["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
            "scope": "openid email profile", "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256", "prompt": "select_account"})
    resp = RedirectResponse(loc, status_code=302)
    _set(resp, STATE_COOKIE, {"nonce": nonce, "purpose": purpose, "verifier": verifier, "invite": invite}, 600)
    return resp


async def _google_identity(code: str, verifier: str) -> Dict[str, Any]:
    """{email, name} of a verified Google address, or raises."""
    if MOCK:
        rec = _mock_codes.pop(code, None)
        if not rec or rec["exp"] < time.time():
            raise ValueError("unknown or expired mock code")
        claims = rec["claims"]
    else:
        from google.auth.transport import requests as greq  # noqa: PLC0415
        from google.oauth2 import id_token as gid  # noqa: PLC0415
        cfg = _client_config()
        r = await _client.post(GOOGLE_TOKEN, data={
            "code": code, "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
            "redirect_uri": url(JOIN, "/oauth/callback"), "grant_type": "authorization_code",
            "code_verifier": verifier})
        if r.status_code != 200:
            raise ValueError(f"token exchange HTTP {r.status_code}")
        claims = gid.verify_oauth2_token(r.json()["id_token"], greq.Request(), audience=cfg["client_id"])
    email = str(claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") is not True:
        raise ValueError("no verified email")
    return {"email": email, "name": str(claims.get("name") or "").strip()}


# ── join.yamin.io ────────────────────────────────────────────────────────────

def _home(request: Request) -> Response:
    msg = {"none": "No Jarvis is set up for that Google account. Use your Jarvis address, or the invite link "
                   "you were sent.",
           "failed": "Google sign-in didn't finish. Try again."}.get(request.query_params.get("error", ""))
    return page("Jarvis", "<h1>Jarvis</h1><p>Your assistant, signed in with Google.</p>"
                f"<a class=btn href='/signin/google'>Sign in with Google</a>"
                + (f"<p class=err role=alert>{_e(msg)}</p>" if msg else ""))


def _invite(request: Request, token: str) -> Response:
    if not store.invite_ok(store.connect(), token):
        _bump("front_invite_page_refused")
        return page("Jarvis", "<h1>Jarvis</h1><p>This invite link has been used or has expired. "
                    "Ask for a new one.</p>", 410)
    return page("You're invited", "<h1>Jarvis</h1><p>You're invited to your own Jarvis. Continue with the Google "
                "account you'll use for work.</p>"
                f"<a class=btn href='/signup/google?{urlencode({'i': token})}'>Continue with Google</a>")


async def _callback(request: Request) -> Response:
    q = request.query_params
    state = q.get("state", "")
    if not state.startswith("front."):
        slug = state.split(".", 1)[0]
        if not store.route(store.connect(), slug):
            _bump("front_callback_unknown_account")
            return page("Jarvis", "<h1>Jarvis</h1><p class=err>That sign-in isn't for any Jarvis here.</p>", 400)
        _bump("front_callback_forwarded")
        return RedirectResponse(url(account_host(slug), "/oauth/callback?" + request.url.query), status_code=302)

    cookie = _get(request, STATE_COOKIE, 600)
    if not cookie or f"front.{cookie.get('nonce')}" != state or not q.get("code"):
        _bump("front_signin_refused")
        return RedirectResponse("/?error=failed", status_code=302)
    try:
        who = await _google_identity(q["code"], cookie["verifier"])
    except Exception as exc:
        _bump("front_signin_refused")
        log.warning("front: Google sign-in refused (%s)", type(exc).__name__)
        return RedirectResponse("/?error=failed", status_code=302)

    if cookie["purpose"] == "signup":
        if not store.invite_ok(store.connect(), cookie.get("invite", "")):
            return _invite(request, cookie.get("invite", ""))
        resp = RedirectResponse("/signup/details", status_code=302)
        _set(resp, SIGNUP_COOKIE, {"email": who["email"], "name": who["name"], "invite": cookie["invite"]}, 1800)
        resp.delete_cookie(STATE_COOKIE, path="/")
        return resp

    slugs = store.routes_for_email(store.connect(), who["email"])
    if not slugs:
        _bump("front_signin_no_account")
        return RedirectResponse("/?error=none", status_code=302)
    _bump("front_signin_found")
    resp = RedirectResponse(url(account_host(slugs[0]), "/api/auth/google/start"), status_code=302)
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp


def _details(request: Request, error: str = "") -> Response:
    su = _get(request, SIGNUP_COOKIE, 1800)
    if not su:
        return RedirectResponse("/", status_code=302)
    return page("Set up your Jarvis", "<h1>Your Jarvis</h1>"
                f"<p>Signed in as <span class=addr>{_e(su['email'])}</span></p>"
                "<form method=post action='/signup' style='display:grid;gap:14px'>"
                f"<label>Your name<input name=owner_name required maxlength=80 value='{_e(su.get('name'))}'></label>"
                "<label>Business name<input name=business_name required maxlength=80 "
                "placeholder='e.g. Ravenscourt Realty'></label>"
                "<button type=submit>Create my Jarvis</button></form>"
                + (f"<p class=err role=alert>{_e(error)}</p>" if error else ""))


async def _signup_post(request: Request) -> Response:
    su = _get(request, SIGNUP_COOKIE, 1800)
    if not su:
        return RedirectResponse("/", status_code=302)
    body = await request.body()
    if len(body) > 4096:
        return _details(request, "That was too long.")
    form = {k: v[0].strip() for k, v in parse_qs(body.decode("utf-8", "replace")).items() if v}
    owner, business = form.get("owner_name", ""), form.get("business_name", "")
    if not owner or not business:
        return _details(request, "Fill in both names.")
    got = store.create_signup(store.connect(), su["invite"], su["email"], owner, business)
    if not got:
        return _invite(request, su["invite"])
    log.info("front: sign-up %s queued as %s", got["id"], got["slug"])
    resp = RedirectResponse(f"/signup/{got['id']}", status_code=303)
    resp.delete_cookie(SIGNUP_COOKIE, path="/")
    _set(resp, WAIT_COOKIE, {"id": got["id"]}, 86400)
    return resp


def _own_signup(request: Request, sid: str) -> Optional[Dict[str, Any]]:
    wait = _get(request, WAIT_COOKIE, 86400)
    if not wait or wait.get("id") != sid:
        return None
    return store.signup(store.connect(), sid)


def _signup_view(su: Dict[str, Any]) -> Dict[str, Any]:
    out = {"status": su["status"], "address": url(account_host(su["slug"]), "/")}
    if su["status"] == "ready":
        out["next"] = url(account_host(su["slug"]), "/api/auth/google/start")
    if su["status"] in ("failed", "full"):
        out["detail"] = ("We're full right now. You're on the list; we'll set it up as soon as there's room."
                         if su["status"] == "full" else "Setting up didn't finish. We've been told and will fix it.")
    return out


def _wait_page(request: Request, sid: str) -> Response:
    su = _own_signup(request, sid)
    if not su:
        return RedirectResponse("/", status_code=302)
    v = _signup_view(su)
    return page("Setting up your Jarvis", "<h1>Setting up</h1>"
                f"<p>Your Jarvis will live at <span class=addr>{_e(v['address'])}</span></p>"
                "<p id=s role=status>This takes about a minute. Keep this page open.</p>"
                "<script>(function(){var s=document.getElementById('s');function t(){fetch('/api/signup/"
                + _e(sid) + "',{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(v){"
                "if(v.status==='ready'){s.textContent='Ready. Opening your Jarvis…';location.href=v.next;return}"
                "if(v.detail){s.textContent=v.detail;return}setTimeout(t,3000)}).catch(function(){setTimeout(t,5000)})}"
                "t()})()</script>")


JOIN_GET = {"/": _home, "/signup/details": _details}


async def join(request: Request, path: str) -> Response:
    method = request.method
    if method == "GET" and path in JOIN_GET:
        return JOIN_GET[path](request)
    if method == "GET" and path.startswith("/i/"):
        return _invite(request, path[3:])
    if method == "GET" and path in ("/signin/google", "/signup/google"):
        if path == "/signup/google":
            token = request.query_params.get("i", "")
            if not store.invite_ok(store.connect(), token):
                return _invite(request, token)
            return _google_start("signup", token)
        return _google_start("signin")
    if method == "GET" and path == "/oauth/callback":
        return await _callback(request)
    if method == "POST" and path == "/signup":
        return await _signup_post(request)
    if method == "GET" and path.startswith("/signup/su_"):
        return _wait_page(request, path[len("/signup/"):])
    if method == "GET" and path.startswith("/api/signup/su_"):
        su = _own_signup(request, path[len("/api/signup/"):])
        return JSONResponse(_signup_view(su) if su else {"status": "unknown"}, status_code=200 if su else 404,
                            headers={"Cache-Control": "no-store"})
    if method == "GET" and path == "/mock/authorize" and MOCK:
        return _mock_authorize(request)
    if method == "GET" and path == "/__front/health" and _local(request):
        conn = store.connect()
        return JSONResponse({"ok": True, "routes": conn.execute("SELECT count(*) FROM routes WHERE "
                                                                 "status='ready'").fetchone()[0],
                             "counters": store.counters(conn)})
    return page("Jarvis", "<h1>Jarvis</h1><p>Nothing here.</p>", 404)


def _mock_authorize(request: Request) -> Response:
    """Sandbox only. Mints what Google's id_token carries for a verified
    address, and nothing more; the test chooses the address."""
    q = request.query_params
    if q.get("redirect_uri") != url(JOIN, "/oauth/callback"):
        return page("mock", "<p>bad redirect_uri</p>", 400)
    if not q.get("email"):
        return page("mock", "<p>mock Google: add &amp;email= to choose the account</p>", 400)
    code = secrets.token_urlsafe(16)
    _mock_codes[code] = {"exp": time.time() + 120, "claims": {
        "email": q.get("email", ""), "email_verified": q.get("verified", "true") == "true",
        "name": q.get("name", "")}}
    return RedirectResponse(q["redirect_uri"] + "?" + urlencode({"code": code, "state": q.get("state", "")}),
                            status_code=302)


# ── an account's address ─────────────────────────────────────────────────────

async def forward(request: Request, slug: str, port: int) -> Response:
    path = request.url.path
    if any(path == p.rstrip("/") or path.startswith(p) for p in INTERNAL):
        _bump("front_internal_refused")
        return JSONResponse({"detail": "Not found."}, status_code=404)
    target = f"http://127.0.0.1:{port}{path}" + (f"?{request.url.query}" if request.url.query else "")
    headers = [(k, v) for k, v in request.headers.raw if k.decode("latin-1").lower() not in HOP]
    peer = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
    headers += [(b"host", (request.headers.get("host") or "").encode("latin-1")),
                (b"x-forwarded-for", peer.encode("latin-1")), (b"x-forwarded-proto", SCHEME.encode()),
                (b"x-forwarded-host", _host(request).encode("latin-1"))]
    upstream = _client.build_request(request.method, target, headers=headers, content=request.stream())
    try:
        resp = await _client.send(upstream, stream=True)
    except httpx.HTTPError as exc:
        _bump("front_upstream_unreachable")
        log.warning("front: %s (port %d) unreachable: %s", slug, port, type(exc).__name__)
        return page("Jarvis", "<h1>Jarvis</h1><p>This Jarvis is restarting. Try again in a minute.</p>", 502)
    out = StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, background=BackgroundTask(resp.aclose))
    out.raw_headers = [(k, v) for k, v in resp.headers.raw
                       if k.decode("latin-1").lower() not in HOP | {"date", "server"}]
    return out


@app.api_route("/{rest:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def entry(request: Request, rest: str) -> Response:
    host = _host(request)
    if host == JOIN:
        return await join(request, "/" + rest)
    if host.endswith("." + BASE):
        slug = host[: -len(BASE) - 1]
        row = store.route(store.connect(), slug) if store.SLUG_RE.fullmatch(slug) else None
        if row:
            return await forward(request, slug, int(row["port"]))
    _bump("front_unknown_host")
    return page("Jarvis", "<h1>Jarvis</h1><p>No Jarvis at this address.</p>", 404)


def main() -> None:
    import uvicorn  # noqa: PLC0415
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    store.connect().close()
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FRONT_PORT", "8790")),
                proxy_headers=False, server_header=False, log_level="warning")


if __name__ == "__main__":
    main()
