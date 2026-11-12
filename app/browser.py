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

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import auth
from . import broker
from . import browser_policy
from . import config
from . import platform_db
from . import platform_store as store
from . import sentinel

log = logging.getLogger("app.browser")
broker_router = APIRouter(prefix="/api/broker")
router = APIRouter(prefix="/api/browser")

CONNECTOR = "browser"
READ_CONNECTOR = "browser.read"
SKILL = "jarvis"
TASK_MINUTES = 30
TAKEOVER_IDLE_S = 900
READ_OPS = ("open", "read", "scroll", "back")
ACT_OPS = ("click", "type", "press", "login")
OPS = READ_OPS + ACT_OPS + ("request_access", "ask_person", "status")
# "tables" is for app code (app/mls.py) through gate(); it is not in OPS, so
# the agent's browser tool can't ask for it.
TABLE_KEYS = ("tables", "cards", "data", "lists", "signals")
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


# ── "needs you": the card asks the person to take over ─────────────────────────
# Set by the server (no saved login) or by the agent (a code, a check it can't
# pass); cleared when they take over or close the browser.
_needs_you: Dict[str, str] = {}


def needs_you(person: str) -> Optional[str]:
    with _takeover_lock:
        return _needs_you.get(person)


def _ask_person(person: str, reason: str, ctx: Dict[str, str], site: str, why: str) -> None:
    with _takeover_lock:
        _needs_you[person] = reason[:300]
    broker._bump(f"browser_needs_you_{why}")
    _record("needs_you", "asked", sentinel.READ, site, ctx, person, reason=reason[:200])


def _clear_needs_you(person: str) -> None:
    with _takeover_lock:
        _needs_you.pop(person, None)


# ── saved logins ──────────────────────────────────────────────────────────────

def _db():
    from . import accounts  # noqa: PLC0415
    conn = accounts._ensure()
    conn.execute("""CREATE TABLE IF NOT EXISTS browser_logins (
        person_key TEXT NOT NULL, site TEXT NOT NULL, username TEXT NOT NULL, secret_cipher BLOB NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_used_at TEXT,
        PRIMARY KEY (person_key, site))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS browser_sites (
        person_key TEXT NOT NULL, site TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('connected','needs_signin')),
        connected_at TEXT NOT NULL, last_used_at TEXT,
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


# ── connected sites ───────────────────────────────────────────────────────────
# That a person signed in to a site in their own browser, and when. Never a
# username, password or cookie: those stay in the profile (or browser_logins).

SITE_NAME = re.compile(r"^[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63}){1,8}$")


def mark_site(person: str, site: str, state: str) -> None:
    now = _now()
    conn = _db()
    conn.execute("INSERT INTO browser_sites (person_key, site, state, connected_at) VALUES (?,?,?,?) "
                 "ON CONFLICT(person_key, site) DO UPDATE SET state=excluded.state, "
                 "connected_at=CASE WHEN excluded.state='connected' THEN excluded.connected_at ELSE connected_at END",
                 (person, site, state, now))
    conn.commit()


def touch_site(person: str, site: str) -> None:
    conn = _db()
    conn.execute("UPDATE browser_sites SET last_used_at=? WHERE person_key=? AND site=?", (_now(), person, site))
    conn.commit()


def site_row(person: str, site: str) -> Optional[Dict[str, Any]]:
    r = _db().execute("SELECT site, state, connected_at, last_used_at FROM browser_sites WHERE person_key=? AND site=?",
                      (person, site)).fetchone()
    return dict(r) if r else None


def list_sites(person: str):
    rows = [dict(r) for r in _db().execute("SELECT site, state, connected_at, last_used_at FROM browser_sites "
                                           "WHERE person_key=? ORDER BY site", (person,)).fetchall()]
    logins = {r["site"] for r in list_logins(person)}
    allow = read_allowances(person)
    sites = {r["site"] for r in rows}
    rows += [{"site": s, "state": None, "connected_at": None, "last_used_at": None} for s in sorted(allow)
             if s not in sites]
    return [{**r, "read_allowance": r["site"] in allow, "saved_login": r["site"] in logins} for r in rows]


def read_allowances(person: str) -> Dict[str, sentinel.Grant]:
    """Sites where this person lets Jarvis keep reading (links, tabs, search
    boxes) without asking. A separate connector from acting, so an allowance
    can never satisfy _active_grant()."""
    now = sentinel.utcnow()
    try:
        grants = store.active_grants(person, READ_CONNECTOR, SKILL)
    except platform_db.Unavailable:
        broker._bump("broker_sentinel_db_unavailable")
        return {}
    out: Dict[str, sentinel.Grant] = {}
    for g in grants:
        if g.scope == sentinel.SCOPE_ALWAYS or (g.expires_at and g.expires_at > now):
            out.setdefault(g.destination.lstrip("@"), g)
    return out


def revoke_reading(person: str, site: str, by: str, reason: str) -> int:
    n = 0
    for g in store.active_grants(person, READ_CONNECTOR, SKILL):
        if g.destination == f"@{site}" and store.revoke_grant(g.id, by=by, reason=reason):
            n += 1
    if n:
        broker._bump("browser_read_revoked")
    return n


def forget_site(person: str, site: str, by: str) -> Dict[str, Any]:
    """Sign out: the site's cookies and storage leave the person's profile, its
    row goes, and any standing read allowance for it is revoked."""
    r = call("forget_site", person, timeout=60, site=site)
    if not r.get("ok"):
        broker._bump("browser_site_forget_failed")
        raise WorkerDown(r.get("error") or "the browser could not clear that site")
    conn = _db()
    conn.execute("DELETE FROM browser_sites WHERE person_key=? AND site=?", (person, site))
    conn.commit()
    revoked = revoke_reading(person, site, by, "signed out of the site")
    broker._bump("browser_site_signed_out")
    _record("forget_site", "executed", sentinel.READ, site, {"source": f"person:{person}"}, person)
    return {"site": site, "cookies_removed": r.get("cookies_removed", 0), "allowances_revoked": revoked}


def erase_person(person: str, by: str) -> Dict[str, Any]:
    """A person was removed: their browser profile (and every sign-in saved in
    it), saved logins, connected sites, MLS runs and browser allowances go.
    Each part is attempted; what failed is named and counted."""
    from . import mls  # noqa: PLC0415
    out: Dict[str, Any] = {"profile": "browser_off", "failed": []}
    if enabled():
        try:
            r = call("erase_profile", person, timeout=60)
            out["profile"] = "erased" if r.get("erased") else "none" if r.get("ok") else "failed"
        except WorkerDown:
            out["profile"] = "failed"
        if out["profile"] == "failed":
            out["failed"].append("profile")
    conn = _db()
    with conn:
        out["logins"] = conn.execute("DELETE FROM browser_logins WHERE person_key=?", (person,)).rowcount
        out["sites"] = conn.execute("DELETE FROM browser_sites WHERE person_key=?", (person,)).rowcount
    out["mls_runs"] = mls.erase_person(person)
    revoked = 0
    try:
        # No platform database configured: no allowance can exist to revoke.
        for connector in (CONNECTOR, READ_CONNECTOR) if platform_db.dsn() else ():
            for g in store.active_grants(person, connector, SKILL):
                revoked += bool(store.revoke_grant(g.id, by=by, reason="person removed"))
    except platform_db.Unavailable:
        broker._bump("broker_sentinel_db_unavailable")
        out["failed"].append("allowances")
    out["allowances_revoked"] = revoked
    with _takeover_lock:
        _needs_you.pop(person, None)
        _takeover.pop(person, None)
    broker._bump("browser_person_erase_failed" if out["failed"] else "browser_person_erased")
    if out["failed"]:
        log.error("browser: removing %s left %s", person, ", ".join(out["failed"]))
    return out


def erase_all() -> str:
    """The tenant was deleted: every profile goes. "erased", "browser_off" or
    "failed" (counted and logged; the caller reports it)."""
    if not enabled():
        return "browser_off"
    try:
        r = call("erase_all", "", timeout=120)
    except WorkerDown:
        r = {"ok": False}
    if not r.get("ok"):
        broker._bump("browser_profiles_erase_failed")
        log.error("browser: the profiles could not all be removed (%s left)", r.get("left", "?"))
        return "failed"
    broker._bump("browser_profiles_erased")
    return "erased"


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


def _read_grant(site: str, person: str) -> Optional[sentinel.Grant]:
    """A standing (or 30-minute) read allowance for this site. Its own
    connector: it never satisfies _active_grant(), so it never lets Jarvis
    act."""
    now = sentinel.utcnow()
    action = sentinel.Action(connector=READ_CONNECTOR, method="read", cls=sentinel.EXTERNAL_SEND,
                             destinations=(f"@{site}",), skill=SKILL, user=person)
    return next((g for g in store.active_grants(person, READ_CONNECTOR, SKILL)
                 if sentinel.grant_covers(g, action, now)), None)


def _pending_read(site: str, person: str) -> Optional[str]:
    """One open ask per person and site: the agent clicking three links does
    not file three cards."""
    for r in store.list_approvals():
        a = r.get("action_json") or {}
        if r["status"] == "pending" and r.get("user_id") == person and a.get("type") == "browser_read" \
                and a.get("site") == site:
            return r["id"]
    return None


def _say_page(r: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("ok", "url", "title", "site", "snapshot", "dialogs", "blocked", "load_error", "login", "redacted",
            "http_status", "site_refused")
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
        elif op == "tables":
            if not current.get("running"):
                return 409, {"error": "nothing_open", "detail": "No page is open yet."}
            r = call("tables", person, snapshot=bool(args.get("snapshot")))
        elif op == "request_access":
            return _request_access(args, current, here, ctx, person)
        elif op == "ask_person":
            reason = re.sub(r"\s+", " ", str(args.get("text") or "")).strip()
            if not current.get("running"):
                return 409, {"error": "nothing_open", "detail": "Open the page first, then ask them to take over."}
            if len(reason) < 8:
                return 400, {"error": "bad_task", "detail": "Say in one sentence what they need to do in the browser."}
            _ask_person(person, reason, ctx, here, "agent")
            return 200, {"ok": True, "asked": True, "site": here}
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
        if r.get("site_refused"):
            broker._bump("browser_site_refused")
    out = _say_page(r)
    if op == "tables":
        out.update({k: r[k] for k in TABLE_KEYS if k in r})
    return (200 if r.get("ok") else 502), out


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
        reading = None
        if grant is None and op in ("click", "type"):
            facts = {**desc, "link_site": site_key(desc["href"]) if desc.get("href") else "",
                     "form_site": site_key(desc["form_action"]) if desc.get("form_action") else ""}
            reading = browser_policy.read_only_gesture(op, facts, text, bool(args.get("submit")), here)
        if grant is None and reading:
            grant = _read_grant(here, person)
            if grant is None:
                aid = _pending_read(here, person) or _file(
                    f"Let Jarvis keep reading {here}", {"type": "browser_read", "site": here,
                                                        "url": str(current.get("url") or "")[:500],
                                                        "example": f"{op} '{exact['name'][:80]}'"},
                    sentinel.EXTERNAL_SEND, f"following links and using search boxes on {here} needs their go-ahead",
                    here, ctx, person)
                broker._bump("browser_read_asked")
                return 403, {"error": "approval_required", "approval_id": aid, "site": here, "reading": reading}
            broker._bump("browser_read_gesture")
            touch_site(person, here)
        if grant is None:
            _record(op, "needs_task_approval", sentinel.EXTERNAL_SEND, here, ctx, person, verdict="ask")
            broker._bump("browser_needs_task_approval")
            return 403, {"error": "needs_task_approval", "site": here}
        grant_id = grant.id
    if op == "login":
        saved = _login_for(person, here)
        if not saved:
            _ask_person(person, f"Sign in to {here}: there's no saved login. Tap Take over, sign in yourself, "
                                f"then tap I'm done and tell Jarvis to carry on.", ctx, here, "no_saved_login")
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
    if a.get("type") == "browser_read":
        action = sentinel.Action(connector=READ_CONNECTOR, method="read", cls=sentinel.EXTERNAL_SEND,
                                 destinations=(f"@{a['site']}",), skill=SKILL, user=person)
        always = scope == sentinel.SCOPE_ALWAYS
        grant = sentinel.grant_from_approval(item["id"], action, sentinel.SCOPE_ALWAYS if always else
                                             sentinel.SCOPE_UNTIL, sentinel.utcnow(),
                                             duration=None if always else timedelta(minutes=TASK_MINUTES))
        store.create_grant(grant, approval_id=item["id"], action_class=sentinel.EXTERNAL_SEND, created_by=decided_by)
        broker._bump("browser_read_granted_always" if always else "browser_read_granted_until")
        return {"state": "granted", "grant_id": grant.id, "site": a["site"],
                "until": grant.expires_at.isoformat() if grant.expires_at else None}
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
    if t == "browser_read":
        return {"site": action.get("site", ""), "page": action.get("url", ""),
                "access": f"Lets Jarvis follow links, switch tabs, page through results and use search and filter "
                          f"boxes on {action.get('site')}. Nothing else: it still asks before typing anything "
                          f"else, signing in, contacting anyone or saving anything.",
                "example": action.get("example", "")}
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
    from . import accounts  # noqa: PLC0415
    if not accounts.feature_on(person, "browser"):
        broker._bump("feature_refused_browser")
        return JSONResponse(status_code=403, content={"error": "feature_off",
                                                      "detail": "The web browser is switched off for this person."})
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
    auth.require_feature(sess, "browser")
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
            "taken_over": taken_over(who), "access": grants, "live_view": st.get("mode") == "headed",
            "needs_you": needs_you(who) if st.get("running") else None}


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
            "width": r.get("width"), "height": r.get("height"), "taken_over": taken_over(who),
            "http_status": r.get("http_status"), "site_refused": r.get("site_refused")}


@router.post("/takeover")
def browser_takeover(body: TakeoverBody, sess: dict = Depends(auth.current_session)):
    who = _person(sess)
    with _takeover_lock:
        if body.on:
            _takeover[who] = time.monotonic()
            _needs_you.pop(who, None)
        else:
            _takeover.pop(who, None)
    if not body.on:
        # The live view ends with the takeover: its relay sees this within a
        # second, and the worker stops the person's x11vnc now.
        try:
            call("view_stop", who, timeout=10)
        except WorkerDown:
            pass
    broker._bump("browser_takeover_on" if body.on else "browser_takeover_off")
    _record("takeover_on" if body.on else "takeover_off", "executed", sentinel.READ, "",
            {"source": f"person:{who}"}, who)
    return {"taken_over": body.on}


# ── the live view: noVNC in the person's own tab ──────────────────────────────
# One WebSocket per person, open only while they have taken over a headed
# browser. Whose browser it shows comes from the session, never a parameter.
# What is logged: open, close, bytes, why it closed. Never frames or keys.

VIEW_RECHECK_S = 30
_viewers: Dict[str, float] = {}


def _view_session(ws: WebSocket) -> dict:
    return auth.current_session(ws.cookies.get(config.COOKIE_NAME), ws.headers.get("authorization"))


def view_refusal(ws: WebSocket) -> Tuple[str, str]:
    """('', person) when this socket may show its person's browser, else
    (why, person-or-''). The checks run in this order; each refusal has its
    own counter."""
    if not enabled():
        return "browser_off", ""
    try:
        sess = _view_session(ws)
    except HTTPException:
        return "no_session", ""
    who = auth.person_of(sess)
    if not who or auth.is_operator(sess):
        return "not_a_person", ""
    want = config.public_origin()
    # WebSockets are not bound by same-origin rules: without this, any site
    # the person visits could open their view with their cookie.
    if not want or ws.headers.get("origin", "").rstrip("/") != want:
        return "origin", str(who)
    from . import accounts  # noqa: PLC0415
    if not accounts.feature_on(str(who), "browser"):
        return "feature_off", str(who)
    try:
        st = call("status", str(who), timeout=10)
    except WorkerDown:
        return "browser_unavailable", str(who)
    if st.get("mode") != "headed":
        return "not_headed", str(who)
    if not taken_over(str(who)):
        return "not_taken_over", str(who)
    with _takeover_lock:
        if str(who) in _viewers:
            return "already_viewing", str(who)
    return "", str(who)


def _view_still_ok(ws: WebSocket, who: str) -> str:
    try:
        sess = _view_session(ws)
    except HTTPException:
        return "signed_out"
    from . import accounts  # noqa: PLC0415
    if auth.person_of(sess) != who or not accounts.feature_on(who, "browser"):
        return "feature_off"
    return ""


@router.websocket("/view")
async def browser_view(ws: WebSocket):
    import asyncio  # noqa: PLC0415
    from starlette.concurrency import run_in_threadpool  # noqa: PLC0415
    why, who = await run_in_threadpool(view_refusal, ws)
    if why:
        broker._bump(f"browser_view_refused_{why}")
        log.info("browser view refused: %s (person %s)", why, who or "-")
        await ws.close(code=4401 if why == "no_session" else 4403)
        return
    with _takeover_lock:
        if who in _viewers:
            broker._bump("browser_view_refused_already_viewing")
            await ws.close(code=4403)
            return
        _viewers[who] = time.monotonic()
    path = os.environ.get("JARVIS_BROWSER_SOCKET", "").strip()
    sent, reason = [0, 0], "closed"
    writer = None
    try:
        reader, writer = await asyncio.open_unix_connection(path)
        writer.write(json.dumps({"op": "view", "person": who}).encode() + b"\n")
        await writer.drain()
        reply = json.loads(await asyncio.wait_for(reader.readline(), 20) or b"{}")
        if not reply.get("ok"):
            broker._bump("browser_view_failed")
            log.warning("browser view for %s did not start: %s", who, str(reply.get("error"))[:120])
            await ws.close(code=1011)
            return
        offered = ws.scope.get("subprotocols") or []
        await ws.accept(subprotocol="binary" if "binary" in offered else None)
        broker._bump("browser_view_opened")
        log.info("browser view opened for %s", who)

        async def up():
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    return "viewer_left"
                data = msg.get("bytes") or (msg.get("text") or "").encode()
                if data:
                    sent[0] += len(data)
                    _touch_takeover(who)
                    writer.write(data)
                    await writer.drain()

        async def down():
            while True:
                data = await reader.read(65536)
                if not data:
                    return "browser_closed"
                sent[1] += len(data)
                await ws.send_bytes(data)

        async def watch():
            last = time.monotonic()
            while True:
                await asyncio.sleep(1)
                if not taken_over(who):
                    return "handed_back"
                if time.monotonic() - last >= VIEW_RECHECK_S:
                    last = time.monotonic()
                    bad = await run_in_threadpool(_view_still_ok, ws, who)
                    if bad:
                        return bad

        tasks = [asyncio.ensure_future(f()) for f in (up, down, watch)]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        reason = next((t.result() for t in done if not t.cancelled() and t.exception() is None), "error")
    except (OSError, asyncio.TimeoutError, ValueError) as exc:
        reason = f"relay_{type(exc).__name__}"
        broker._bump("browser_view_failed")
    finally:
        with _takeover_lock:
            _viewers.pop(who, None)
        if writer is not None:
            writer.close()
        try:
            await ws.close()
        except Exception:
            pass
        broker._bump("browser_view_closed")
        log.info("browser view closed for %s: %s (in %d bytes, out %d bytes)", who, reason, sent[0], sent[1])


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
    _clear_needs_you(who)
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


@router.get("/sites")
def sites_list(sess: dict = Depends(auth.current_session)):
    return {"sites": list_sites(_person(sess))}


@router.post("/sites/confirm")
def sites_confirm(sess: dict = Depends(auth.current_session)):
    """'I'm signed in', on hand back: the site of the page their browser is on.
    The person cannot name some other site here."""
    who = _person(sess)
    try:
        st = call("status", who, timeout=10)
    except WorkerDown:
        raise HTTPException(status_code=503, detail="The browser isn't running right now.")
    url = str(st.get("url") or "")
    site = site_key(url) if st.get("running") and urlsplit(url).scheme in ("http", "https") else ""
    if not site:
        raise HTTPException(status_code=409, detail="Open the site you signed in to first.")
    mark_site(who, site, "connected")
    broker._bump("browser_site_connected")
    _record("site_connected", "executed", sentinel.READ, site, {"source": f"person:{who}"}, who)
    return {"ok": True, "site": site_row(who, site)}


@router.delete("/sites/{site}")
def sites_sign_out(site: str, sess: dict = Depends(auth.current_session)):
    who = _person(sess)
    site = site.strip().lower()
    if not SITE_NAME.match(site) or (site_row(who, site) is None and site not in read_allowances(who)):
        raise HTTPException(status_code=404, detail="That site isn't in your connected sites.")
    try:
        return {"ok": True, **forget_site(who, site, who)}
    except WorkerDown:
        raise HTTPException(status_code=503, detail="The browser couldn't sign out of that site right now; "
                                                    "nothing was removed. Try again.")


@router.delete("/sites/{site}/reading")
def sites_stop_reading(site: str, sess: dict = Depends(auth.current_session)):
    """Stop always allowing: only the read allowance goes; the sign-in stays."""
    who = _person(sess)
    site = site.strip().lower()
    if not SITE_NAME.match(site):
        raise HTTPException(status_code=404, detail="That site isn't in your connected sites.")
    try:
        n = revoke_reading(who, site, who, "stopped from Settings")
    except platform_db.Unavailable:
        raise HTTPException(status_code=503, detail="The platform database is unavailable.")
    if not n:
        raise HTTPException(status_code=404, detail="Jarvis has no standing permission to read that site.")
    return {"ok": True, "revoked": n}


@router.delete("/logins/{site}")
def logins_delete(site: str, sess: dict = Depends(auth.current_session)):
    if not delete_login(_person(sess), site.strip().lower()):
        raise HTTPException(status_code=404, detail="No saved login for that site.")
    broker._bump("browser_login_deleted")
    return {"ok": True}
