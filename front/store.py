"""The front door's own ledger: which slug is on which port, invite links,
sign-ups waiting for the account worker, and counters.

One SQLite file (FRONT_DB). The front door (user jarvis-front) reads and
writes it; the account worker (root) claims queued sign-ups and marks them
ready. Invite links are stored as hashes only.
"""
import hashlib
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

DB = os.environ.get("FRONT_DB", "/var/lib/jarvis-front/front.db")

# Hostname labels that are never an account: the front door's own names,
# the old tunnel hostnames, and words that read as official.
RESERVED = frozenset({
    "join", "www", "app", "api", "admin", "gateway", "hooks", "mail", "smtp", "auth", "login", "signin",
    "signup", "account", "accounts", "support", "help", "status", "static", "cdn", "assets", "jarvis",
    "rehearsal", "rehearsal-gw", "rehearsal-hooks", "testco", "root", "live", "34dev", "operator",
})
SLUG_RE = re.compile(r"[a-z][a-z0-9-]{2,15}")
STATUSES = ("queued", "provisioning", "ready", "failed", "full")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS routes (
            slug TEXT PRIMARY KEY, port INTEGER NOT NULL UNIQUE, owner_email TEXT,
            status TEXT NOT NULL DEFAULT 'ready', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS invites (
            token_hash TEXT PRIMARY KEY, note TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            used_at TEXT, signup_id TEXT);
        CREATE TABLE IF NOT EXISTS signups (
            id TEXT PRIMARY KEY, invite_hash TEXT NOT NULL UNIQUE, email TEXT NOT NULL, owner_name TEXT NOT NULL,
            business_name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, status TEXT NOT NULL, detail TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0);
    """)
    return conn


def bump(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("INSERT INTO counters (name, n) VALUES (?, 1) ON CONFLICT(name) DO UPDATE SET n = n + 1", (name,))


def counters(conn: sqlite3.Connection) -> Dict[str, int]:
    return {r["name"]: r["n"] for r in conn.execute("SELECT name, n FROM counters")}


# ── routes ───────────────────────────────────────────────────────────────────

def route(conn: sqlite3.Connection, slug: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM routes WHERE slug=? AND status='ready'", (slug,)).fetchone()


def put_route(conn: sqlite3.Connection, slug: str, port: int, owner_email: Optional[str] = None) -> None:
    if not SLUG_RE.fullmatch(slug) or slug in RESERVED:
        raise ValueError(f"not an account name: {slug!r}")
    if not 1024 < int(port) < 65536 or int(port) in (8700, 8710):
        raise ValueError(f"port {port} is not an account app port")
    conn.execute("INSERT INTO routes (slug, port, owner_email, status, created_at) VALUES (?,?,?,'ready',?) "
                 "ON CONFLICT(slug) DO UPDATE SET port=excluded.port, owner_email=excluded.owner_email, "
                 "status='ready'", (slug, int(port), (owner_email or "").strip().lower() or None, now().isoformat()))


def routes_for_email(conn: sqlite3.Connection, email: str) -> List[str]:
    return [r["slug"] for r in conn.execute(
        "SELECT slug FROM routes WHERE owner_email=? AND status='ready' ORDER BY slug", (email.strip().lower(),))]


# ── invites ──────────────────────────────────────────────────────────────────

def new_invite(conn: sqlite3.Connection, note: str = "", days: int = 7) -> str:
    token = secrets.token_urlsafe(18)
    conn.execute("INSERT INTO invites (token_hash, note, created_at, expires_at) VALUES (?,?,?,?)",
                 (_hash(token), note[:120], now().isoformat(), (now() + timedelta(days=days)).isoformat()))
    bump(conn, "front_invites_made")
    return token


def invite_ok(conn: sqlite3.Connection, token: str) -> bool:
    row = conn.execute("SELECT expires_at, used_at FROM invites WHERE token_hash=?", (_hash(token or ""),)).fetchone()
    return bool(row) and not row["used_at"] and datetime.fromisoformat(row["expires_at"]) > now()


# ── sign-ups ─────────────────────────────────────────────────────────────────

def slug_taken(conn: sqlite3.Connection, slug: str) -> bool:
    return slug in RESERVED or bool(
        conn.execute("SELECT 1 FROM routes WHERE slug=? UNION SELECT 1 FROM signups WHERE slug=?",
                     (slug, slug)).fetchone())


def suggest_slug(conn: sqlite3.Connection, *names: str) -> str:
    for name in names:
        base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
        base = re.sub(r"^[^a-z]+", "", base)[:14].strip("-")
        if len(base) < 3:
            continue
        for n in range(0, 50):
            cand = base if n == 0 else f"{base[:13 - len(str(n))].rstrip('-')}-{n}"
            if SLUG_RE.fullmatch(cand) and not slug_taken(conn, cand):
                return cand
    for _ in range(20):
        cand = "jv-" + secrets.token_hex(3)
        if not slug_taken(conn, cand):
            return cand
    raise RuntimeError("no free account name")


def create_signup(conn: sqlite3.Connection, invite: str, email: str, owner_name: str,
                  business_name: str) -> Optional[Dict[str, Any]]:
    """Consumes the invite and queues the account, in one transaction. None if
    the invite is no longer good (used, expired, or never existed)."""
    sid = "su_" + secrets.token_hex(8)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not invite_ok(conn, invite):
            conn.execute("ROLLBACK")
            bump(conn, "front_signup_invite_refused")
            return None
        slug = suggest_slug(conn, business_name, owner_name, email.split("@")[0])
        stamp = now().isoformat()
        conn.execute("UPDATE invites SET used_at=?, signup_id=? WHERE token_hash=? AND used_at IS NULL",
                     (stamp, sid, _hash(invite)))
        conn.execute("INSERT INTO signups (id, invite_hash, email, owner_name, business_name, slug, status, "
                     "created_at, updated_at) VALUES (?,?,?,?,?,?,'queued',?,?)",
                     (sid, _hash(invite), email.strip().lower(), owner_name.strip()[:80],
                      business_name.strip()[:80], slug, stamp, stamp))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    bump(conn, "front_signups_queued")
    return signup(conn, sid)


def signup(conn: sqlite3.Connection, sid: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM signups WHERE id=?", (sid,)).fetchone()
    return dict(row) if row else None


def claim_next(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """The worker takes the oldest queued sign-up, once."""
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT id FROM signups WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
    if row:
        conn.execute("UPDATE signups SET status='provisioning', updated_at=? WHERE id=? AND status='queued'",
                     (now().isoformat(), row["id"]))
    conn.execute("COMMIT")
    return signup(conn, row["id"]) if row else None


def set_status(conn: sqlite3.Connection, sid: str, status: str, detail: str = "") -> None:
    if status not in STATUSES:
        raise ValueError(status)
    conn.execute("UPDATE signups SET status=?, detail=?, updated_at=? WHERE id=?",
                 (status, detail[:300], now().isoformat(), sid))
