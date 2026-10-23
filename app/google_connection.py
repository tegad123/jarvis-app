"""Google connections: the tenant's assistant mailbox (one row, background
jobs) and each person's own Google (person_google, one row per person, chat).

Decrypt happens ONLY inside build_credentials. Status GET never touches the
cipher. A missing APP_GOOGLE_TOKEN_KEY is a connect-route failure, not a PIN
login failure — this module is not imported by the PIN path.

Never log cipher bytes, refresh tokens, or the Fernet key.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from . import accounts, config

log = logging.getLogger(__name__)

STATUS_CONNECTED = "connected"
STATUS_NEEDS_REAUTH = "needs_reauth"
STATUS_DISCONNECTED = "disconnected"

COUNTER_DECRYPT_FAILED = "google_connection_decrypt_failed"
COUNTER_OAUTH_CLIENT_MISSING = "google_connection_oauth_client_missing"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"


class OAuthClientMissing(RuntimeError):
    """No usable OAuth client file, so a stored grant can never be refreshed."""


class ConnectionCipherError(Exception):
    """Cipher missing or undecryptable. Never a silent None."""


class PersonNotConnected(Exception):
    """This person has no usable Google connection (none, or it needs reconnecting)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fernet() -> MultiFernet:
    """Encrypt with the first key; decrypt tries all (rotation window)."""
    keys = config.google_token_key_list()
    if not keys:
        raise RuntimeError(
            "APP_GOOGLE_TOKEN_KEY is not set. Add it to the environment "
            f"(or {config.ENV_PATH}) and restart. Connect routes cannot run."
        )
    return MultiFernet([Fernet(k.encode("ascii")) for k in keys])


def encrypt_tokens(payload: Dict[str, Any]) -> bytes:
    """Fernet blob. Callers must not log payload or the result."""
    blob = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _fernet().encrypt(blob)


def _decrypt_tokens(cipher: bytes) -> Dict[str, Any]:
    """Private. The public decrypt door is build_credentials."""
    raw = _fernet().decrypt(bytes(cipher))
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ConnectionCipherError("google connection cipher payload is not an object")
    return data


def _bump_counter(name: str) -> None:
    try:
        from . import threads as threads_mod
        threads_mod.bump_counter(name)
    except Exception:
        log.error("google_connection: counter bump failed (%s)", name)


def _bump_decrypt_failed() -> None:
    _bump_counter(COUNTER_DECRYPT_FAILED)


def _mark_last_error(kind: str) -> None:
    try:
        conn = accounts._ensure()
        conn.execute(
            "UPDATE google_connection SET last_error=?, status=? WHERE id=1",
            (kind, STATUS_NEEDS_REAUTH),
        )
        conn.commit()
    except Exception:
        log.error("google_connection: could not record last_error class")


def get_row() -> Optional[Dict[str, Any]]:
    r = accounts._ensure().execute(
        "SELECT * FROM google_connection WHERE id = 1"
    ).fetchone()
    return dict(r) if r else None


def row_count() -> int:
    n = accounts._ensure().execute(
        "SELECT COUNT(*) AS n FROM google_connection"
    ).fetchone()
    return int(n["n"] if n["n"] is not None else 0)


def _announce_mailbox_match(google_email: Optional[str]) -> None:
    """google_connection.google_email vs identity.assistant_address. Mismatch announces."""
    try:
        p = str(config.SHARED)
        if p not in sys.path:
            sys.path.insert(0, p)
        import identity
        import tenant_config as tc
        addr = (identity.load().get("assistant_address") or "").strip()
        tc.announce_assistant_mailbox_mismatch(google_email or "", addr)
    except Exception:
        log.warning("google_connection: mailbox-match check failed", exc_info=True)


def status_public() -> Dict[str, Any]:
    """The GET /api/google/connection body. No token-ish keys, no cipher."""
    row = get_row()
    if not row or not row.get("token_cipher"):
        return {
            "status": STATUS_DISCONNECTED,
            "google_email": None,
            "connected_at": None,
            "refreshed_at": None,
        }
    email = row.get("google_email")
    _announce_mailbox_match(email)
    return {
        "status": row.get("status") or STATUS_DISCONNECTED,
        "google_email": email,
        "connected_at": row.get("connected_at"),
        "refreshed_at": row.get("refreshed_at"),
    }


def mark_needs_reauth(kind: str) -> None:
    """Public door for google_creds refresh failure. Kind is a class, not a body."""
    _mark_last_error(kind)


def put_connected(
    *,
    google_email: str,
    scopes: str,
    token_cipher: bytes,
    connected_by: str,
) -> None:
    """Replace the single row. Caller encrypts first."""
    now = _now()
    conn = accounts._ensure()
    conn.execute("DELETE FROM google_connection")
    conn.execute(
        """INSERT INTO google_connection
           (id, google_email, scopes, token_cipher, status, connected_by,
            connected_at, refreshed_at, last_error)
           VALUES (1, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
        (google_email, scopes, token_cipher, STATUS_CONNECTED, connected_by, now),
    )
    conn.commit()
    _announce_mailbox_match(google_email)


def clear() -> None:
    """Disconnect: drop the row (ledger 1→0). Cipher is gone, not zeroed in place."""
    conn = accounts._ensure()
    conn.execute("DELETE FROM google_connection")
    conn.commit()


def write_refreshed_cipher(token_cipher: bytes) -> None:
    """After credentials.refresh — new cipher, refreshed_at. Encrypt happened outside.

    A successful refresh proves the grant works, so it also clears needs_reauth."""
    conn = accounts._ensure()
    conn.execute(
        "UPDATE google_connection SET token_cipher=?, refreshed_at=?, last_error=NULL, "
        "status=? WHERE id=1",
        (token_cipher, _now(), STATUS_CONNECTED),
    )
    conn.commit()


def _oauth_client() -> Dict[str, str]:
    """client_id, client_secret and token_uri from YAMIN_OAUTH_CLIENT.

    Google refuses a refresh without the client pair, so a missing or unreadable
    file is a loud OAuthClientMissing, never a credential that fails later."""
    path = config.oauth_client_path()
    cfg: Dict[str, Any] = {}
    if path is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cfg = data.get("web") or data.get("installed") or {}
        except (OSError, ValueError) as exc:
            log.error("google_connection: OAuth client file unreadable (%s)", type(exc).__name__)
    if not cfg.get("client_id") or not cfg.get("client_secret"):
        _bump_counter(COUNTER_OAUTH_CLIENT_MISSING)
        _mark_last_error("oauth_client_missing")
        log.error("google_connection: no usable OAuth client (YAMIN_OAUTH_CLIENT); refresh is impossible")
        raise OAuthClientMissing("OAuth client not configured (YAMIN_OAUTH_CLIENT)")
    return {"client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
            "token_uri": cfg.get("token_uri") or DEFAULT_TOKEN_URI}


def build_credentials():
    """THE decrypt site. Raises ConnectionCipherError; never returns None.

    Slice 6 will hand this to google_creds. Slice 4 exists so a corrupt cipher
    cannot fail closed as a silent missing connection.
    """
    row = get_row()
    if not row or not row.get("token_cipher"):
        log.error("google_connection: build_credentials: no cipher (row_missing=%s)",
                  row is None)
        raise ConnectionCipherError("google connection cipher missing")
    try:
        payload = _decrypt_tokens(row["token_cipher"])
    except InvalidToken as exc:
        _bump_decrypt_failed()
        _mark_last_error("decrypt_failed")
        log.error("google_connection: decrypt failed (InvalidToken)")
        raise ConnectionCipherError("google connection token cannot be decrypted") from exc
    except ConnectionCipherError:
        _bump_decrypt_failed()
        _mark_last_error("decrypt_failed")
        log.error("google_connection: decrypt failed (payload)")
        raise
    except RuntimeError:
        # Missing APP_GOOGLE_TOKEN_KEY is a connect-path failure, not a corrupt cipher.
        log.error("google_connection: token key missing or unusable")
        raise
    except Exception as exc:
        _bump_decrypt_failed()
        _mark_last_error(type(exc).__name__)
        log.error("google_connection: decrypt failed (%s)", type(exc).__name__)
        raise ConnectionCipherError("google connection token cannot be decrypted") from exc

    refresh = payload.get("refresh_token") or ""
    access = payload.get("access_token") or ""
    if not refresh and not access:
        _bump_decrypt_failed()
        _mark_last_error("empty_payload")
        log.error("google_connection: decrypted payload had no tokens")
        raise ConnectionCipherError("google connection cipher payload empty")

    return _credentials(payload)


def _credentials(payload: Dict[str, Any]):
    from google.oauth2.credentials import Credentials

    refresh = payload.get("refresh_token") or ""
    access = payload.get("access_token") or ""
    expiry = None
    raw_exp = payload.get("expiry")
    if isinstance(raw_exp, str) and raw_exp:
        try:
            expiry = datetime.fromisoformat(raw_exp)
            # google-auth compares expiry to utcnow() as naive UTC.
            if expiry.tzinfo is not None:
                expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            expiry = None

    scopes = payload.get("scopes") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    if config.oauth_mock():
        client = {"client_id": "mock", "client_secret": "mock", "token_uri": DEFAULT_TOKEN_URI}
    else:
        client = _oauth_client()
    return Credentials(
        token=access or None,
        refresh_token=refresh or None,
        token_uri=client["token_uri"],
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        scopes=list(scopes) if scopes else None,
        expiry=expiry,
    )


# ── each person's own Google ─────────────────────────────────────────────────

COUNTER_PERSON_REFRESHED = "person_google_refreshed"
COUNTER_PERSON_NEEDS_REAUTH = "person_google_needs_reauth"


def person_row(person_key: str) -> Optional[Dict[str, Any]]:
    if not person_key:
        return None
    r = accounts._ensure().execute(
        "SELECT * FROM person_google WHERE person_key = ?", (person_key,)
    ).fetchone()
    return dict(r) if r else None


def person_count() -> int:
    n = accounts._ensure().execute("SELECT COUNT(*) AS n FROM person_google").fetchone()
    return int(n["n"] or 0)


def person_status(person_key: str) -> Dict[str, Any]:
    """This person's own connection. No token-ish keys, no cipher."""
    row = person_row(person_key)
    if not row or not row.get("token_cipher"):
        return {"status": STATUS_DISCONNECTED, "google_email": None, "connected_at": None, "refreshed_at": None}
    return {"status": row.get("status") or STATUS_DISCONNECTED, "google_email": row.get("google_email"),
            "connected_at": row.get("connected_at"), "refreshed_at": row.get("refreshed_at")}


def put_person(person_key: str, *, google_email: str, scopes: str, token_cipher: bytes) -> None:
    if not person_key:
        raise ValueError("person_key is required")
    conn = accounts._ensure()
    conn.execute(
        """INSERT INTO person_google
           (person_key, google_email, scopes, token_cipher, status, connected_at, refreshed_at, last_error)
           VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
           ON CONFLICT(person_key) DO UPDATE SET google_email=excluded.google_email,
             scopes=excluded.scopes, token_cipher=excluded.token_cipher, status=excluded.status,
             connected_at=excluded.connected_at, refreshed_at=NULL, last_error=NULL""",
        (person_key, google_email, scopes, token_cipher, STATUS_CONNECTED, _now()),
    )
    conn.commit()


def clear_person(person_key: str) -> int:
    conn = accounts._ensure()
    cur = conn.execute("DELETE FROM person_google WHERE person_key = ?", (person_key,))
    conn.commit()
    return cur.rowcount


def _mark_person_error(person_key: str, kind: str) -> None:
    try:
        conn = accounts._ensure()
        conn.execute("UPDATE person_google SET last_error=?, status=? WHERE person_key=?",
                     (kind, STATUS_NEEDS_REAUTH, person_key))
        conn.commit()
    except Exception:
        log.error("google_connection: could not record a person's last_error class")


def person_credentials(person_key: str):
    """THE decrypt site for a person's grant: valid credentials, refreshed and
    re-encrypted when expired. Raises PersonNotConnected; never another
    person's grant and never the tenant row."""
    row = person_row(person_key)
    if not row or not row.get("token_cipher"):
        raise PersonNotConnected("not connected")
    if row.get("status") != STATUS_CONNECTED:
        raise PersonNotConnected("needs reconnecting")
    try:
        payload = _decrypt_tokens(row["token_cipher"])
    except Exception as exc:
        _bump_decrypt_failed()
        _mark_person_error(person_key, "decrypt_failed")
        log.error("google_connection: a person's grant cannot be decrypted (%s)", type(exc).__name__)
        raise PersonNotConnected("needs reconnecting") from exc
    if not (payload.get("refresh_token") or payload.get("access_token")):
        _mark_person_error(person_key, "empty_payload")
        raise PersonNotConnected("needs reconnecting")
    creds = _credentials(payload)
    if config.oauth_mock() or creds.valid:
        return creds
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    try:
        creds.refresh(Request())
    except RefreshError as exc:
        _bump_counter(COUNTER_PERSON_NEEDS_REAUTH)
        _mark_person_error(person_key, "refresh_failed")
        log.error("google_connection: a person's grant could not be refreshed; marked needs_reauth")
        raise PersonNotConnected("needs reconnecting") from exc
    cipher = encrypt_tokens({"refresh_token": creds.refresh_token or payload.get("refresh_token"),
                             "access_token": creds.token,
                             "expiry": creds.expiry.isoformat() if creds.expiry else None,
                             "scopes": payload.get("scopes")})
    conn = accounts._ensure()
    conn.execute("UPDATE person_google SET token_cipher=?, refreshed_at=?, last_error=NULL WHERE person_key=?",
                 (cipher, _now(), person_key))
    conn.commit()
    _bump_counter(COUNTER_PERSON_REFRESHED)
    return creds
