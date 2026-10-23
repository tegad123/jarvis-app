"""Per-person accounts: PINs, roles, and the join to identity.people.

WHY THE PERSON KEY IS THE JOIN
    identity.people already IS the roster — it carries names, addresses, titles
    and roles, and every record this product writes now carries a person key
    (leads.owner_person, tasks.assignee_person, deal intended_members). An
    account here is therefore NOT a new identity; it is a CREDENTIAL attached to
    an identity that already exists. Nothing can have an account without being
    in the registry first, which is what keeps "who is this" answerable from one
    place.

WHAT THIS REPLACES
    A single shared APP_PASSPHRASE that opened a session with no idea who was
    holding it. Phase A recorded the IOU plainly: "any authenticated session
    sees every thread". This is the payoff for the standing design rules — the
    person key was written onto every record precisely so scoping could become a
    WHERE clause rather than a migration.

APP_PASSPHRASE SURVIVES AS BREAK-GLASS
    It no longer identifies a person, so it no longer opens a person session. It
    opens an OPERATOR session: full read access for support and recovery,
    marked as such in the session so nothing mistakes it for the owner. Keeping
    it means a locked-out tenant is recoverable without a database edit.

PINS
    Same shape as the minted passphrase — hyphenated groups from an unambiguous
    alphabet (no 0/O/1/l) — because a person types this on a phone. Stored as a
    salted PBKDF2-SHA256 hash; the plaintext is shown once, at mint time, by the
    CLI and never persisted.
"""

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

if str(config.SHARED) not in sys.path:
    sys.path.insert(0, str(config.SHARED))

import identity  # noqa: E402

DB_PATH = Path(
    os.environ.get("JARVIS_ACCOUNTS_DB", str(config.HERMES_HOME / "state" / "jarvis_accounts.db"))
).expanduser()

ROLES = ("owner", "admin", "member")

# Unambiguous alphabet — no 0/O/1/l/I. Matches the provisioner's passphrase form.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_PBKDF2_ROUNDS = 240_000
_EMAIL_INDEX = "idx_accounts_email_lower"

log = logging.getLogger(__name__)

_local = threading.local()
_INIT_LOCK = threading.Lock()
_initialised = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_pin() -> str:
    """Three hyphenated 4-char groups. ~10^18 keyspace, typeable on a phone."""
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(3))


def _hash(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, _PBKDF2_ROUNDS).hex()


def _connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        _local.conn = conn
    return conn


def init() -> None:
    global _initialised
    with _INIT_LOCK:
        conn = _connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                person_key  TEXT PRIMARY KEY,
                role        TEXT NOT NULL CHECK (role IN ('owner','admin','member')),
                pin_hash    TEXT,
                pin_salt    TEXT,
                disabled    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                last_login  TEXT
            );
            """
        )
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
        if "email" not in cols:
            conn.execute("ALTER TABLE accounts ADD COLUMN email TEXT")
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_EMAIL_INDEX} "
            "ON accounts(lower(email)) WHERE email IS NOT NULL"
        )
        # Per-attempt OAuth state. Consume-on-use; many in-flight nonces;
        # survives a worker restart. Not oauth_demo's single _oauth_state slot.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS oauth_states (
                nonce       TEXT PRIMARY KEY,
                purpose     TEXT NOT NULL,
                expires_at  INTEGER NOT NULL,
                consumed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS google_connection (
                id            INTEGER PRIMARY KEY CHECK (id = 1),
                google_email  TEXT,
                scopes        TEXT,
                token_cipher  BLOB,
                status        TEXT NOT NULL,
                connected_by  TEXT,
                connected_at  TEXT,
                refreshed_at  TEXT,
                last_error    TEXT
            );

            -- Each person's own Google (chat: their calendar and email). One
            -- row per person_key; the tenant row above stays the assistant
            -- mailbox for background jobs. Same cipher and key as above.
            CREATE TABLE IF NOT EXISTS person_google (
                person_key    TEXT PRIMARY KEY,
                google_email  TEXT,
                scopes        TEXT,
                token_cipher  BLOB,
                status        TEXT NOT NULL,
                connected_at  TEXT,
                refreshed_at  TEXT,
                last_error    TEXT
            );
            """
        )
        conn.commit()
        _initialised = True
    backfill_emails_from_identity()


def _ensure() -> sqlite3.Connection:
    if not _initialised:
        init()
    return _connect()


class AccountError(Exception):
    pass


def default_role(person_key: str) -> str:
    """Role implied by the registry, used when an account is first created.

    The registry's `roles` list is about what someone IS to the business
    (owner/internal/admin/assistant); this maps that onto what they may SEE.
    'admin' in the registry means an app admin; the owner is the owner; everyone
    else starts as a member and is promoted deliberately.
    """
    rec = (identity.load().get("people") or {}).get(person_key) or {}
    roles = rec.get("roles") or []
    if "owner" in roles:
        return "owner"
    if "admin" in roles:
        return "admin"
    return "member"


def known_people() -> set:
    """Person keys the registry knows. The guard on "add this person to a
    channel" — a typo must not create a membership row for a key that names
    nobody, because nothing downstream would ever notice."""
    return {r["person_key"] for r in roster()}


def roster() -> List[Dict[str, Any]]:
    """Registry people who may hold an account, with any account they have.

    The assistant is excluded: it is not a person who logs in.
    """
    people = identity.load().get("people") or {}
    conn = _ensure()
    rows = {r["person_key"]: dict(r) for r in conn.execute("SELECT * FROM accounts")}
    out = []
    for key, rec in people.items():
        if key.startswith("_") or "assistant" in (rec.get("roles") or []):
            continue
        acct = rows.get(key)
        out.append({
            "person_key": key,
            "name": rec.get("display_name") or rec.get("name") or key,
            "full_name": rec.get("name") or key,
            "title": rec.get("title") or "",
            "role": (acct or {}).get("role") or default_role(key),
            "has_pin": bool((acct or {}).get("pin_hash")),
            "has_email": bool((acct or {}).get("email")),
            "disabled": bool((acct or {}).get("disabled")),
            "last_login": (acct or {}).get("last_login"),
        })
    return out


def get(person_key: str) -> Optional[Dict[str, Any]]:
    r = _ensure().execute("SELECT * FROM accounts WHERE person_key = ?", (person_key,)).fetchone()
    return dict(r) if r else None


def get_by_email(email: str) -> Optional[Dict[str, Any]]:
    """The account that Google sign-in will map, or None. Does not create one."""
    addr = normalize_email(email)
    if not addr:
        return None
    r = _ensure().execute(
        "SELECT * FROM accounts WHERE lower(email) = ?", (addr,)
    ).fetchone()
    return dict(r) if r else None


def mark_login(person_key: str) -> None:
    _ensure().execute(
        "UPDATE accounts SET last_login=? WHERE person_key=?", (_now(), person_key)
    )
    _ensure().commit()


def put_oauth_state(nonce: str, purpose: str, expires_at: int) -> None:
    conn = _ensure()
    conn.execute(
        "INSERT OR REPLACE INTO oauth_states (nonce, purpose, expires_at, consumed_at) "
        "VALUES (?,?,?,NULL)",
        (nonce, purpose, int(expires_at)),
    )
    conn.commit()


def consume_oauth_state(nonce: str, purpose: str) -> bool:
    """True once, if this nonce exists, matches purpose, is unexpired, unused."""
    import time as _time
    if not nonce:
        return False
    conn = _ensure()
    cur = conn.execute(
        "UPDATE oauth_states SET consumed_at=? "
        "WHERE nonce=? AND purpose=? AND consumed_at IS NULL AND expires_at >= ?",
        (_now(), nonce, purpose, int(_time.time())),
    )
    conn.commit()
    return cur.rowcount == 1


def set_pin(person_key: str, role: Optional[str] = None) -> str:
    """Create or reset a person's PIN. Returns the plaintext ONCE."""
    people = identity.load().get("people") or {}
    if person_key not in people:
        raise AccountError(
            f"'{person_key}' is not in identity.people. Add them to the registry first — "
            f"an account is a credential attached to an identity, never a new identity."
        )
    if "assistant" in (people[person_key].get("roles") or []):
        raise AccountError("The assistant does not log in.")

    role = role or (get(person_key) or {}).get("role") or default_role(person_key)
    if role not in ROLES:
        raise AccountError(f"role must be one of {ROLES}")

    pin = make_pin()
    salt = secrets.token_bytes(16)
    now = _now()
    conn = _ensure()
    conn.execute(
        """INSERT INTO accounts (person_key, role, pin_hash, pin_salt, disabled, created_at, updated_at)
           VALUES (?,?,?,?,0,?,?)
           ON CONFLICT(person_key) DO UPDATE SET
             role = excluded.role, pin_hash = excluded.pin_hash,
             pin_salt = excluded.pin_salt, disabled = 0, updated_at = excluded.updated_at""",
        (person_key, role, _hash(pin, salt), salt.hex(), now, now),
    )
    conn.commit()
    return pin


def normalize_email(raw: Optional[str]) -> Optional[str]:
    """One login address: trimmed, lowercased. Empty becomes None (SQL NULL)."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    return s or None


def set_email(person_key: str, email: str) -> Dict[str, Any]:
    """Set or fix the login address. Does not mint an account from Google.

    A person already in the registry may get an email-only row (no PIN).
    Unique index on lower(email) refuses a second person the same address.
    """
    people = identity.load().get("people") or {}
    if person_key not in people:
        raise AccountError(
            f"'{person_key}' is not in identity.people. Add them to the registry first — "
            f"an account is a credential attached to an identity, never a new identity."
        )
    if "assistant" in (people[person_key].get("roles") or []):
        raise AccountError("The assistant does not log in.")
    addr = normalize_email(email)
    if not addr:
        raise AccountError("email is empty.")

    now = _now()
    role = (get(person_key) or {}).get("role") or default_role(person_key)
    conn = _ensure()
    try:
        conn.execute(
            """INSERT INTO accounts
               (person_key, role, pin_hash, pin_salt, disabled, created_at, updated_at, email)
               VALUES (?,?,NULL,NULL,0,?,?,?)
               ON CONFLICT(person_key) DO UPDATE SET
                 email = excluded.email, updated_at = excluded.updated_at""",
            (person_key, role, now, now, addr),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise AccountError("that email is already assigned")
    return get(person_key)


def backfill_emails_from_identity() -> Dict[str, List[str]]:
    """Copy identity.people[key].email[0] onto account rows whose email is NULL.

    Duplicate login addresses skip-and-log and the run continues. Never aborts
    mid-run. Does not insert new account rows (no auto-create from a profile).
    """
    filled: List[str] = []
    skipped: List[str] = []
    people = identity.load().get("people") or {}
    conn = _connect()
    rows = conn.execute(
        "SELECT person_key FROM accounts WHERE email IS NULL"
    ).fetchall()
    for row in rows:
        key = row["person_key"]
        rec = people.get(key) or {}
        emails = rec.get("email") or []
        if not emails:
            continue
        addr = normalize_email(emails[0] if isinstance(emails, list) else emails)
        if not addr:
            continue
        try:
            conn.execute(
                "UPDATE accounts SET email=?, updated_at=? WHERE person_key=? AND email IS NULL",
                (addr, _now(), key),
            )
            conn.commit()
            filled.append(key)
        except sqlite3.IntegrityError:
            conn.rollback()
            log.warning(
                "accounts email backfill: skip %s — duplicate login address",
                key,
            )
            skipped.append(key)
            continue
    return {"filled": filled, "skipped": skipped}


def set_role(person_key: str, role: str) -> Dict[str, Any]:
    if role not in ROLES:
        raise AccountError(f"role must be one of {ROLES}")
    if get(person_key) is None:
        raise AccountError(f"'{person_key}' has no account yet — set a PIN first.")
    if role != "owner":
        # Never leave the tenant without an owner: demoting the last one would
        # lock everybody out of settings, approvals and role management.
        owners = [r["person_key"] for r in _ensure().execute(
            "SELECT person_key FROM accounts WHERE role='owner' AND disabled=0")]
        if owners == [person_key]:
            raise AccountError(
                f"'{person_key}' is the only owner — promote someone else first, or the "
                f"tenant is left with nobody who can approve or change settings."
            )
    conn = _ensure()
    conn.execute("UPDATE accounts SET role=?, updated_at=? WHERE person_key=?",
                 (role, _now(), person_key))
    conn.commit()
    return get(person_key)


def set_disabled(person_key: str, disabled: bool = True) -> Dict[str, Any]:
    if get(person_key) is None:
        raise AccountError(f"'{person_key}' has no account.")
    if disabled and (get(person_key) or {}).get("role") == "owner":
        owners = [r["person_key"] for r in _ensure().execute(
            "SELECT person_key FROM accounts WHERE role='owner' AND disabled=0")]
        if owners == [person_key]:
            raise AccountError("Refusing to disable the only owner.")
    conn = _ensure()
    conn.execute("UPDATE accounts SET disabled=?, updated_at=? WHERE person_key=?",
                 (1 if disabled else 0, _now(), person_key))
    conn.commit()
    return get(person_key)


def verify(person_key: str, pin: str) -> Optional[Dict[str, Any]]:
    """Check a PIN. Returns the account on success, None otherwise.

    Constant-time compare, and a disabled or PIN-less account always fails.
    """
    acct = get(person_key)
    if not acct or acct.get("disabled") or not acct.get("pin_hash"):
        return None
    candidate = _hash(pin or "", bytes.fromhex(acct["pin_salt"]))
    if not hmac.compare_digest(candidate, acct["pin_hash"]):
        return None
    conn = _ensure()
    conn.execute("UPDATE accounts SET last_login=? WHERE person_key=?", (_now(), person_key))
    conn.commit()
    return get(person_key)
