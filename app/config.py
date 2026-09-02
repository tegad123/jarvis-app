"""Configuration for the Jarvis app backend.

Secrets live in ~/.hermes/.env and are read HERE rather than injected via
systemd's EnvironmentFile=. Two reasons:

  * that file is ~24KB of mixed-provenance values, some quoted, some with
    characters systemd's parser treats specially — one bad line would take the
    unit down at start;
  * `systemctl show` prints a unit's Environment= block, so EnvironmentFile
    secrets are one command away from any operator's scrollback.

Real environment variables still win over the file, so a deploy can override
anything without editing it.
"""

import os
from pathlib import Path
from typing import Dict, Optional

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
ENV_PATH = HERMES_HOME / ".env"

# The skills tree, for _shared/notify.py.
SKILLS_ROOT = Path(
    os.environ.get("JARVIS_SKILLS_ROOT", HERMES_HOME / "skills" / "openclaw-imports")
).expanduser()
SHARED = SKILLS_ROOT / "_shared"

GOOGLE_TOKEN_PATH = HERMES_HOME / "credentials" / "google_token.json"


def _parse_env_file(path: Path) -> Dict[str, str]:
    """Minimal KEY=VALUE reader. Ignores blanks, comments and `export` prefixes."""
    out: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                key, sep, value = line.partition("=")
                if not sep:
                    continue
                key = key.strip()
                if not key:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                out[key] = value
    except FileNotFoundError:
        pass
    except Exception:
        # A malformed .env must not stop the app from booting; the missing-secret
        # check below produces a far clearer error than a parse traceback.
        pass
    return out


_FILE_ENV = _parse_env_file(ENV_PATH)


def get(name: str, default: Optional[str] = None) -> Optional[str]:
    """Process environment first, then ~/.hermes/.env, then the default."""
    value = os.environ.get(name)
    if value is not None and value.strip():
        return value.strip()
    value = _FILE_ENV.get(name)
    if value is not None and value.strip():
        return value.strip()
    return default


def require(name: str) -> str:
    value = get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Add it to {ENV_PATH} (or the environment) "
            f"and restart jarvis-app.service."
        )
    return value


HOST = get("APP_HOST", "127.0.0.1")
PORT = int(get("APP_PORT", "8700"))

# Session cookie lifetime. Short enough that a leaked cookie ages out, long
# enough that the operator is not retyping a 32-char passphrase all day.
SESSION_MAX_AGE = int(get("APP_SESSION_MAX_AGE", str(12 * 60 * 60)))
COOKIE_NAME = get("APP_COOKIE_NAME", "jarvis_session")

# The app is reached over an HTTPS cloudflared hostname, so Secure is correct by
# default. Set APP_COOKIE_SECURE=0 only for a plain-HTTP loopback test.
COOKIE_SECURE = get("APP_COOKIE_SECURE", "1") not in ("0", "false", "no")

GATEWAY_URL = get("HERMES_API_URL", "http://127.0.0.1:8642").rstrip("/")

# Login throttle. This endpoint is publicly reachable through the tunnel and
# guards a single shared passphrase, so unbounded guessing is not acceptable.
LOGIN_MAX_FAILURES = int(get("APP_LOGIN_MAX_FAILURES", "10"))
LOGIN_WINDOW_SECONDS = int(get("APP_LOGIN_WINDOW_SECONDS", "300"))

# Public origin for OAuth redirect_uri. Scheme + host, no trailing slash.
# Required to START Google login; never guessed from request.base_url.
OAUTH_STATE_COOKIE = get("APP_OAUTH_STATE_COOKIE", "jarvis_oauth_state")
OAUTH_STATE_MAX_AGE = int(get("APP_OAUTH_STATE_MAX_AGE", "600"))


def public_origin() -> Optional[str]:
    """APP_PUBLIC_ORIGIN or None. Missing is a loud refuse, not a guess."""
    raw = get("APP_PUBLIC_ORIGIN")
    if not raw:
        return None
    return raw.rstrip("/")


def oauth_mock() -> bool:
    """Testco (and boxed tests) use a local fake instead of accounts.google.com."""
    return (get("APP_GOOGLE_OAUTH_MOCK") or "").strip().lower() in ("1", "true", "yes")


def oauth_client_path() -> Optional[Path]:
    """Path to the OAuth client JSON. Path only — never log contents."""
    raw = get("YAMIN_OAUTH_CLIENT") or get("GOOGLE_OAUTH_CLIENT")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() else None
