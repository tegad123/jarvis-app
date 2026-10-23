"""The tenant's browser, decided app-side (docs/SPEC.md 8.5 B, 9.6).

The agent asks through POST /api/broker/browser; the app decides and drives
the browser worker (app/browser_worker.py) over `<slug>-browser.socket`, which
only the app user can open. The agent never reaches the worker, the egress
proxy, or a saved login.

    read   open, read, scroll, back       no approval; every site is a ledger
                                          row and the run counts as tainted
    act    click, type, press, login      needs a task approval for that site:
                                          one tap lets Jarvis act there for
                                          TASK_MINUTES (a Sentinel grant)
    risky  payments, purchases, deleting, cancelling, card fields, checkout
           pages                          always a fresh one-time approval of
                                          that exact action; a grant never covers it

A tainted run that opens a new site with data in the address needs a one-time
approval (an address can carry data out). Saved logins live encrypted in the
accounts database, owner-managed, and are typed by the worker; their values
never reach the agent, a log line or a reply. Live view and takeover are the
owner's: while the owner holds the browser, every agent action is refused.

Every person has their own browser: their own profile and signed-in sessions,
their own saved logins, their own task grants, live view and takeover. Each
person approves their own browser approvals; the owner sees them all and can
reject any, never approve one for someone else (app/approvals.py). The
break-glass operator has no browser. JARVIS_BROWSER=on and
JARVIS_BROWSER_SOCKET switch it on; anything else answers 503 browser_off.
"""
import json
import logging
import os
import re
import socket
import threading
import time
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import auth
from . import broker
from . import platform_db
from . import platform_store as store
from . import sentinel

log = logging.getLogger("app.browser")
broker_router = APIRouter(prefix="/api/broker")
router = APIRouter(prefix="/api/browser")

CONNECTOR = "browser"
SKILL = "jarvis"
TASK_MINUTES = 30
TAKEOVER_IDLE_S = 900
READ_OPS = ("open", "read", "scroll", "back")
ACT_OPS = ("click", "type", "press", "login")
OPS = READ_OPS + ACT_OPS + ("request_access", "status")
KEYS = ("Enter", "Tab", "Escape", "Backspace", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "PageDown",
        "PageUp", "Home", "End", "Space")
REF = re.compile(r"^e\d{1,5}$")
TWO_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu", "ne", "or", "go"}

RISKY_LABEL = re.compile(
    r"\b(pay|payment|purchase|buy|checkout|check out|place (your )?order|order now|complete order|"
    r"confirm (order|purchase|payment|booking)|book now|subscribe|upgrade|donate|transfer|send money|wire|"
    r"withdraw|delete|remove|erase|destroy|discard|cancel (my |your |the )?(account|subscription|membership|order|"
    r"plan|booking|reservation)|close (my |your )?account|deactivate|unsubscribe|e-?sign|sign and submit|"
    r"accept (the )?offer|submit (an |the )?offer|make (an )?offer)\b|\$\s?\d", re.I)
RISKY_FIELD = re.compile(r"card|cvv|cvc|security code|expir|iban|routing|account.?number|ssn|social security|"
                         r"tax.?id|bank", re.I)
RISKY_PAGE = re.compile(r"checkout|payment|billing|purchase|/cart\b|place-?order", re.I)

_takeover: Dict[str, float] = {}
_takeover_lock = threading.Lock()


class WorkerDown(RuntimeError):
    pass


def enabled() -> bool:
    return os.environ.get("JARVIS_BROWSER", "off").strip().lower() == "on" and \
        bool(os.environ.get("JARVIS_BROWSER_SOCKET", "").strip())


def call(op: str, person: str, timeout: float = 100, **kw) -> Dict[str, Any]:
    """One request to this person's browser. WorkerDown when it cannot be reached."""
    path = os.environ.get("JARVIS_BROWSER_SOCKET", "").strip()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        s.sendall(json.dumps({"op": op, "person": person, **kw}).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(1 << 20)
            if not chunk:
                break
            data += chunk
    except OSError as exc:
        broker._bump("browser_worker_unreachable")
        log.error("browser: worker unreachable (%s)", type(exc).__name__)
        raise WorkerDown(str(exc)) from exc
    finally:
        s.close()
    try:
        return json.loads(data)
    except ValueError as exc:
        broker._bump("browser_worker_bad_reply")
        raise WorkerDown("the browser gave no answer") from exc


def site_key(url_or_host: str) -> str:
    """The site a grant names: the registrable part of the host (accounts.x.com
    and www.x.com are both x.com), so one task approval covers a site's own
    sign-in pages."""
    host = (urlsplit(url_or_host).hostname if "//" in url_or_host else url_or_host) or ""
    host = host.lower().rstrip(".")
    if re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host):
        return host
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in TWO_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def risky(op: str, desc: Dict[str, Any], page: Dict[str, Any], text: str = "") -> Optional[str]:
    where = f"{page.get('url', '')} {page.get('title', '')}"
    if RISKY_PAGE.search(where):
        return "this looks like a checkout or payment page"
    name = str(desc.get("name") or "")
    if op == "click" and RISKY_LABEL.search(name):
        return f"'{name[:80]}' can spend money, delete something or can't be undone"
    if op == "type" and (RISKY_FIELD.search(f"{name} {desc.get('field', '')}")
                         or re.fullmatch(r"[\d -]{13,23}", text.strip() or "x")):
        return "this field takes payment or bank details"
    return None


# ── takeover ──────────────────────────────────────────────────────────────────

def taken_over(person: str) -> bool:
    with _takeover_lock:
        at = _takeover.get(person)
        if at is not None and time.monotonic() - at > TAKEOVER_IDLE_S:
            del _takeover[person]
            broker._bump("browser_takeover_expired")
            return False
        return at is not None


def _touch_takeover(person: str) -> None:
    with _takeover_lock:
        if person in _takeover:
            _takeover[person] = time.monotonic()


# ── saved logins ──────────────────────────────────────────────────────────────

def _db():
    from . import accounts  # noqa: PLC0415
    conn = accounts._ensure()
    conn.execute("""CREATE TABLE IF NOT EXISTS browser_logins (
        person_key TEXT NOT NULL, site TEXT NOT NULL, username TEXT NOT NULL, secret_cipher BLOB NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_used_at TEXT,
        PRIMARY KEY (person_key, site))""")
    return conn


def _now() -> str:
    return sentinel.utcnow().isoformat()


def list_logins(person: str):
    rows = _db().execute("SELECT site, username, updated_at, last_used_at FROM browser_logins WHERE person_key=? "
                         "ORDER BY site", (person,))
    return [dict(r) for r in rows.fetchall()]


def save_login(person: str, site: str, username: str, password: str) -> None:
    from . import google_connection  # noqa: PLC0415
    cipher = google_connection._fernet().encrypt(json.dumps({"password": password}).encode())
    conn = _db()
    now = _now()
    conn.execute("INSERT INTO browser_logins (person_key, site, username, secret_cipher, created_at, updated_at) "
                 "VALUES (?,?,?,?,?,?) ON CONFLICT(person_key, site) DO UPDATE SET username=excluded.username, "
                 "secret_cipher=excluded.secret_cipher, updated_at=excluded.updated_at",
                 (person, site, username, cipher, now, now))
    conn.commit()
    broker._bump("browser_login_saved")


def delete_login(person: str, site: str) -> bool:
    conn = _db()
    n = conn.execute("DELETE FROM browser_logins WHERE person_key=? AND site=?", (person, site)).rowcount
    conn.commit()
    return n > 0


def _login_for(person: str, site: str) -> Optional[Tuple[str, str]]:
    from . import google_connection  # noqa: PLC0415
    conn = _db()
    row = conn.execute("SELECT username, secret_cipher FROM browser_logins WHERE person_key=? AND site=?",
                       (person, site)).fetchone()
    if not row:
        return None
    try:
        secret = json.loads(google_connection._fernet().decrypt(bytes(row["secret_cipher"])))["password"]
    except Exception:
        broker._bump("browser_login_unreadable")
        log.error("browser: saved login for a site could not be decrypted")
        return None
    conn.execute("UPDATE browser_logins SET last_used_at=? WHERE person_key=? AND site=?", (_now(), person, site))
    conn.commit()
    return row["username"], secret


# ── the ledger ────────────────────────────────────────────────────────────────

def _record(op: str, outcome: str, cls: str, site: str, ctx: Dict[str, str], person: str,
            **extra) -> Optional[int]:
    kind, _, sid = (ctx.get("source") or "").partition(":")
    fields = {"connector": CONNECTOR, "method": op, "action_class": cls, "destinations": [f"@{site}"] if site else [],
              "skill": SKILL, "user_id": person, "run_id": broker._run(ctx), "session_id": ctx.get("session", ""),
              "task_id": ctx.get("task", ""), "source_kind": kind or None, "source_id": sid or None,
              "sentinel_mode": broker.sentinel_mode(), **extra}
    try:
        return store.record_action("decision", outcome, fields)
    except platform_db.Unavailable:
        broker._bump("broker_sentinel_db_unavailable")
        log.error("browser: could not record %s %s", op, outcome)
        return None


def _active_grant(site: str, person: str) -> Optional[sentinel.Grant]:
    now = sentinel.utcnow()
    action = sentinel.Action(connector=CONNECTOR, method="act", cls=sentinel.EXTERNAL_SEND,
                             destinations=(f"@{site}",), skill=SKILL, user=person)
    return next((g for g in store.active_grants(person, CONNECTOR, SKILL) if sentinel.grant_covers(g, action, now)),
                None)


def _say_page(r: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("ok", "url", "title", "site", "snapshot", "dialogs", "blocked", "load_error", "login", "redacted")
    return {k: r[k] for k in keep if k in r}


# ── the gate ──────────────────────────────────────────────────────────────────

def _file(summary: str, action: Dict[str, Any], cls: str, reason: str, site: str, ctx: Dict[str, str],
          person: str) -> str:
    """Filed for the person whose browser it is; they decide it themselves."""
    kind, _, sid = (ctx.get("source") or "").partition(":")
    aid = store.create_approval(origin="broker", user_id=person, kind="action", skill=SKILL,
                                summary=summary[:300], action=action, action_class=cls, reason=reason,
                                source_kind=kind or None, source_id=sid or None, run_id=broker._run(ctx),
                                session_id=ctx.get("session", ""), task_id=ctx.get("task", ""))
    _record(action.get("op") or "task", "approval_required", cls, site, ctx, person, approval_id=aid, verdict="ask",
            reason=reason)
    broker._bump("browser_approval_filed")
    return aid


def _exfil_risk(url: str, ctx: Dict[str, str], current: str, person: str) -> bool:
    """An address is a way out: once a run has read untrusted content, a new
    site with data in the address waits for the owner."""
    u = urlsplit(url)
    target = site_key(url)
    if target == current or not (u.query or u.fragment or len(u.path) > 60):
        return False
    try:
        if not store.is_tainted(broker._run(ctx)):
            return False
        return _active_grant(target, person) is None
    except platform_db.Unavailable:
        return True


def gate(op: str, args: Dict[str, Any], ctx: Dict[str, str], person: str) -> Tuple[int, Dict[str, Any]]:
    if taken_over(person) and op != "status":
        broker._bump("browser_refused_takeover")
        return 409, {"error": "taken_over", "detail": "They are using the browser themselves right now."}
    try:
        if op == "status":
            return 200, call("status", person)
        current = call("status", person)
        here = site_key(current.get("url") or "") if current.get("running") else ""
        if op == "open":
            url = str(args.get("url") or "").strip()
            if urlsplit(url).scheme not in ("http", "https") or len(url) > 2000:
                return 400, {"error": "bad_url", "detail": "Only http and https addresses can be opened."}
            target = site_key(url)
            if _exfil_risk(url, ctx, here, person):
                aid = _file(f"Open {target}: {url[:200]}", {"type": "browser", "op": "open", "url": url,
                                                            "site": target},
                            sentinel.LOCKED, "a run that read outside content wants to open a new site with data in "
                                             "the address", target, ctx, person)
                return 403, {"error": "approval_required", "approval_id": aid}
            r = call("open", person, url=url)
        elif op in ("read", "scroll", "back"):
            if not current.get("running"):
                return 409, {"error": "nothing_open", "detail": "No page is open yet."}
            r = call(op, person, direction="up" if args.get("direction") == "up" else "down")
        elif op == "request_access":
            return _request_access(args, current, here, ctx, person)
        else:
            return _act(op, args, current, here, ctx, person)
    except WorkerDown:
        return 503, {"error": "browser_unavailable", "detail": "The browser isn't running right now."}
    site = site_key(r.get("url") or "")
    if r.get("blocked"):
        broker._bump("browser_egress_blocked")
        _record(op, "blocked", sentinel.READ, site, ctx, person, reason=str(r["blocked"])[:200])
    else:
        _record(op, "executed" if r.get("ok") else "failed", sentinel.READ, site, ctx, person, verdict="allow")
        broker._taint(ctx, "read a web page")
        broker._bump(f"browser_{op}")
    return (200 if r.get("ok") else 502), _say_page(r)


def _request_access(args, current, here, ctx, person):
    task = re.sub(r"\s+", " ", str(args.get("task") or "")).strip()
    site = site_key(str(args.get("site") or "")) if args.get("site") else here
    if not site:
        return 409, {"error": "nothing_open", "detail": "Open the site first, then ask for access to it."}
    if not 5 <= len(task) <= 300:
        return 400, {"error": "bad_task", "detail": "Describe the task in one plain sentence."}
    if broker.sentinel_mode() == broker.SHADOW:
        _record("request_access", "shadowed", sentinel.EXTERNAL_SEND, site, ctx, person)
        return 409, {"error": "shadowed"}
    g = _active_grant(site, person)
    if g:
        left = int((g.expires_at - sentinel.utcnow()).total_seconds() // 60) if g.expires_at else TASK_MINUTES
        return 200, {"ok": True, "already": True, "site": site, "minutes_left": max(left, 0)}
    aid = _file(f"On {site}: {task}", {"type": "browser_task", "site": site, "task": task,
                                       "url": str(current.get("url") or "")[:500]},
                sentinel.EXTERNAL_SEND, f"acting on {site} needs their go-ahead", site, ctx, person)
    return 403, {"error": "approval_required", "approval_id": aid, "site": site}


def _act(op, args, current, here, ctx, person, approval: Optional[Dict[str, Any]] = None):
    if not current.get("running") or not here:
        return 409, {"error": "nothing_open", "detail": "No page is open yet."}
    ref = str(args.get("element") or "")
    if op in ("click", "type") and not REF.match(ref):
        return 400, {"error": "bad_element", "detail": "Name an element from the page, like e12."}
    if op == "press" and args.get("key") not in KEYS:
        return 400, {"error": "bad_key", "detail": f"key is one of {KEYS}."}
    text = str(args.get("text") or "")[:2000]
    if broker.sentinel_mode() == broker.SHADOW:
        _record(op, "shadowed", sentinel.EXTERNAL_SEND, here, ctx, person)
        broker._bump("broker_sentinel_shadowed")
        return 409, {"error": "shadowed"}
    if not broker._writes_allowed():
        _record(op, "writes_disabled", sentinel.EXTERNAL_SEND, here, ctx, person)
        return 403, {"error": "writes_disabled"}
    desc = call("describe", person, ref=ref) if op in ("click", "type") else {"name": "", "field": ""}
    if not desc.get("ok", True):
        return 409, {"error": "stale", "detail": desc.get("error") or "Read the page again."}
    exact = {"type": "browser", "op": op, "site": here, "url": str(current.get("url") or "")[:500],
             "element": ref, "name": str(desc.get("name") or "")[:200], "key": args.get("key"),
             "text": text if op == "type" else None, "submit": bool(args.get("submit"))}
    why = risky(op, desc, current, text)
    grant_id = None
    if approval is None:
        if why:
            label = {"click": f"click '{exact['name'][:80]}'", "type": f"type into '{exact['name'][:60]}'",
                     "press": f"press {args.get('key')}", "login": "sign in"}[op]
            aid = _file(f"On {here}: {label}", exact, sentinel.LOCKED, why, here, ctx, person)
            broker._bump("browser_risky_held")
            return 403, {"error": "approval_required", "approval_id": aid, "risky": why}
        grant = _active_grant(here, person)
        if grant is None:
            _record(op, "needs_task_approval", sentinel.EXTERNAL_SEND, here, ctx, person, verdict="ask")
            broker._bump("browser_needs_task_approval")
            return 403, {"error": "needs_task_approval", "site": here}
        grant_id = grant.id
    if op == "login":
        saved = _login_for(person, here)
        if not saved:
            return 409, {"error": "no_saved_login", "site": here}
        r = call("fill_login", person, username=saved[0], password=saved[1], expect_site=current.get("site"))
        saved = None
    elif op == "click":
        r = call("click", person, ref=ref, expect_site=current.get("site"))
    elif op == "type":
        r = call("type", person, ref=ref, text=text, submit=bool(args.get("submit")), expect_site=current.get("site"))
    else:
        r = call("press", person, key=args.get("key"), expect_site=current.get("site"))
    _record(op, "executed" if r.get("ok") else "failed", sentinel.LOCKED if approval else sentinel.EXTERNAL_SEND,
            here, ctx, person, verdict="allow", grant_id=grant_id,
            approval_id=approval["id"] if approval else None)
    broker._bump(f"browser_{op}")
    broker._taint(ctx, "read a web page")
    if not r.get("ok"):
        return 409, {"error": "stale", "detail": r.get("error") or "That didn't work; read the page again."}
    return 200, _say_page(r)


def execute_approved(item: Dict[str, Any], scope: str, decided_by: str) -> Dict[str, Any]:
    """A browser approval: a task becomes a TASK_MINUTES grant for its site; a
    single risky action runs once, exactly as proposed, if the page still
    matches."""
    a = item.get("action_json") or {}
    person = str(item.get("user_id") or "")
    ctx = {"run": item.get("run_id") or "", "session": item.get("session_id") or "", "task": item.get("task_id") or "",
           "skill": SKILL, "source": f"approval:{item['id']}"}
    if a.get("type") == "browser_task":
        action = sentinel.Action(connector=CONNECTOR, method="task", cls=sentinel.EXTERNAL_SEND,
                                 destinations=(f"@{a['site']}",), skill=SKILL, user=person)
        grant = sentinel.grant_from_approval(item["id"], action, sentinel.SCOPE_UNTIL, sentinel.utcnow(),
                                             duration=timedelta(minutes=TASK_MINUTES))
        store.create_grant(grant, approval_id=item["id"], action_class=sentinel.EXTERNAL_SEND, created_by=decided_by)
        broker._bump("browser_task_granted")
        return {"state": "granted", "grant_id": grant.id, "site": a["site"], "until": grant.expires_at.isoformat()}
    if not enabled():
        return {"state": "refused", "reason": "the browser is switched off"}
    try:
        current = call("status", person)
        if a.get("op") == "open":
            r = call("open", person, url=a["url"])
            _record("open", "executed" if r.get("ok") else "failed", sentinel.LOCKED, a.get("site", ""), ctx, person,
                    approval_id=item["id"], verdict="allow")
            return {"state": "executed" if r.get("ok") else "failed", "url": r.get("url")}
        if site_key(current.get("url") or "") != a.get("site"):
            return {"state": "failed", "reason": "the browser is on a different page now; ask Jarvis to try again"}
        if a.get("op") in ("click", "type") and call("describe", person, ref=a.get("element")).get("name", "")[:200] != \
                (a.get("name") or ""):
            return {"state": "failed", "reason": "the page changed since this was proposed; ask Jarvis to try again"}
        status, out = _act(a["op"], a, current, a["site"], ctx, person, approval=item)
    except WorkerDown:
        return {"state": "failed", "reason": "the browser isn't running"}
    return {"state": "executed" if status == 200 else (out.get("error") or "failed"), "status": status}


def approval_display(action: Dict[str, Any]) -> Optional[Dict[str, str]]:
    t = action.get("type")
    if t == "browser_task":
        return {"site": action.get("site", ""), "task": action.get("task", ""),
                "access": f"Lets Jarvis click and type on {action.get('site')} for {TASK_MINUTES} minutes. It still "
                          "asks before payments, purchases, deleting anything or anything it can't undo.",
                "page": action.get("url", "")}
    if t == "browser":
        out = {"site": action.get("site", ""), "page": action.get("url", "")}
        op = action.get("op")
        if op == "open":
            out["action"] = f"open {action.get('url', '')}"
        elif op == "click":
            out["action"] = f"click '{action.get('name', '')}'"
        elif op == "type":
            out["action"] = f"type into '{action.get('name', '')}'"
            out["text"] = str(action.get("text") or "")
            if action.get("submit"):
                out["then"] = "press Enter"
        elif op == "press":
            out["action"] = f"press {action.get('key')}"
        elif op == "login":
            out["action"] = "sign in with your saved login"
        out["once"] = "Runs this one action, once."
        return out
    return None


# ── the agent's door ─────────────────────────────────────────────────────────

@broker_router.post("/browser")
async def post_browser(request: Request):
    broker._authorize(request)
    if not enabled():
        broker._bump("browser_off")
        return JSONResponse(status_code=503, content={"error": "browser_off",
                                                      "detail": "The browser isn't switched on for this tenant."})
    body = await broker._json_body(request)
    op = body.get("op")
    if op not in OPS:
        raise HTTPException(status_code=400, detail=f"op must be one of {OPS}.")
    entry = broker._turn_person(body.get("ref"))
    if entry is None:
        return JSONResponse(status_code=403, content={"error": "no_turn", "detail": "No running chat turn."})
    person = str(entry["person_key"])
    if op not in ("status", "open", "read", "scroll", "back"):
        broker._require_sentinel()
    ctx = broker._context(request)
    thread = str(entry.get("thread_id") or "")
    if not ctx.get("source") and broker.CONTEXT_VALUE.match(thread):
        ctx["source"] = f"chat_thread:{thread}"
    from starlette.concurrency import run_in_threadpool  # noqa: PLC0415
    try:
        status, out = await run_in_threadpool(gate, op, body, ctx, person)
    except platform_db.Unavailable:
        broker._bump("broker_sentinel_db_unavailable")
        status, out = 503, {"error": "sentinel_unavailable", "detail": "The platform database is unavailable."}
    return out if status == 200 else JSONResponse(status_code=status, content=out)


# ── each person's own door ───────────────────────────────────────────────────

def _person(sess: dict) -> str:
    """The signed-in person, whose browser every route below acts on. Never
    the break-glass operator: it is nobody, so it has no browser."""
    auth.require_person(sess)
    if not enabled():
        raise HTTPException(status_code=503, detail="browser_off: the browser isn't switched on for this tenant.")
    return str(auth.person_of(sess))


class LoginBody(BaseModel):
    site: str = Field(..., min_length=3, max_length=300)
    username: str = Field(..., min_length=1, max_length=320)
    password: str = Field(..., min_length=1, max_length=500)


class TakeoverBody(BaseModel):
    on: bool


class InputBody(BaseModel):
    kind: str = Field(..., pattern="^(click|type|key|open|back|scroll)$")
    x: Optional[float] = Field(None, ge=0, le=4000)
    y: Optional[float] = Field(None, ge=0, le=4000)
    text: Optional[str] = Field(None, max_length=2000)
    key: Optional[str] = Field(None, max_length=20)
    url: Optional[str] = Field(None, max_length=2000)


@router.get("/status")
def browser_status(sess: dict = Depends(auth.current_session)):
    auth.require_person(sess)
    if not enabled():
        return {"enabled": False}
    who = str(auth.person_of(sess))
    try:
        st = call("status", who, timeout=10)
    except WorkerDown:
        st = {"running": False, "unreachable": True}
    try:
        grants = [{"site": g.destination.lstrip("@"), "until": g.expires_at.isoformat() if g.expires_at else None}
                  for g in store.active_grants(who, CONNECTOR, SKILL)
                  if g.expires_at and g.expires_at > sentinel.utcnow()]
    except platform_db.Unavailable:
        grants = []
    return {"enabled": True, "running": bool(st.get("running")), "url": st.get("url"), "title": st.get("title"),
            "taken_over": taken_over(who), "access": grants}


@router.get("/screen")
def browser_screen(sess: dict = Depends(auth.current_session)):
    who = _person(sess)
    try:
        if not call("status", who, timeout=10).get("running"):
            return {"running": False, "taken_over": taken_over(who)}
        r = call("screen", who, timeout=20)
    except WorkerDown:
        raise HTTPException(status_code=503, detail="The browser isn't running right now.")
    return {"running": True, "url": r.get("url"), "title": r.get("title"), "jpeg": r.get("jpeg"),
            "width": r.get("width"), "height": r.get("height"), "taken_over": taken_over(who)}


@router.post("/takeover")
def browser_takeover(body: TakeoverBody, sess: dict = Depends(auth.current_session)):
    who = _person(sess)
    with _takeover_lock:
        if body.on:
            _takeover[who] = time.monotonic()
        else:
            _takeover.pop(who, None)
    broker._bump("browser_takeover_on" if body.on else "browser_takeover_off")
    _record("takeover_on" if body.on else "takeover_off", "executed", sentinel.READ, "",
            {"source": f"person:{who}"}, who)
    return {"taken_over": body.on}


@router.post("/input")
def browser_input(body: InputBody, sess: dict = Depends(auth.current_session)):
    who = _person(sess)
    if not taken_over(who):
        raise HTTPException(status_code=409, detail="Take over the browser first.")
    _touch_takeover(who)
    try:
        if body.kind == "click":
            if body.x is None or body.y is None:
                raise HTTPException(status_code=422, detail="click needs x and y.")
            r = call("human_click", who, x=body.x, y=body.y)
        elif body.kind == "type":
            r = call("human_type", who, text=body.text or "")
        elif body.kind == "key":
            if body.key not in KEYS:
                raise HTTPException(status_code=422, detail=f"key is one of {KEYS}.")
            r = call("human_key", who, key=body.key)
        elif body.kind == "open":
            if urlsplit(body.url or "").scheme not in ("http", "https"):
                raise HTTPException(status_code=422, detail="Only http and https addresses can be opened.")
            r = call("open", who, url=body.url)
        else:
            r = call(body.kind, who, direction="down")
    except WorkerDown:
        raise HTTPException(status_code=503, detail="The browser isn't running right now.")
    _record(f"human_{body.kind}", "executed" if r.get("ok") else "failed", sentinel.READ,
            site_key(r.get("url") or "") if r.get("url") else "", {"source": f"person:{who}"}, who)
    return {"ok": bool(r.get("ok")), "url": r.get("url"), "error": r.get("error")}


@router.post("/close")
def browser_close(sess: dict = Depends(auth.current_session)):
    who = _person(sess)
    try:
        call("close", who, timeout=20)
    except WorkerDown:
        pass
    return {"running": False}


@router.get("/logins")
def logins_list(sess: dict = Depends(auth.current_session)):
    return {"logins": list_logins(_person(sess))}


@router.post("/logins")
def logins_save(body: LoginBody, sess: dict = Depends(auth.current_session)):
    who = _person(sess)
    site = site_key(body.site.strip() if "//" in body.site else f"https://{body.site.strip()}")
    if not site or "." not in site:
        raise HTTPException(status_code=422, detail="Give the site's address, like zillow.com.")
    save_login(who, site, body.username.strip(), body.password)
    return {"ok": True, "site": site}


@router.delete("/logins/{site}")
def logins_delete(site: str, sess: dict = Depends(auth.current_session)):
    if not delete_login(_person(sess), site.strip().lower()):
        raise HTTPException(status_code=404, detail="No saved login for that site.")
    broker._bump("browser_login_deleted")
    return {"ok": True}
