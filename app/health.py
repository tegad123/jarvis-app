"""Real subsystem checks for /api/health.

Every check here performs actual I/O — an HTTP request, a file read, a token
refresh against Google. None of them infer health from configuration alone.
A check that cannot be performed reports `unknown` with the reason; it never
reports `ok`.

Status vocabulary, worst-wins when rolled up:
    ok        working
    degraded  usable but not fully working
    fail      not working
    unknown   could not be determined
"""

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict

from . import config

ORDER = {"ok": 0, "unknown": 1, "degraded": 2, "fail": 3}

# The scope a Sheets-backed skill (crm, lead-reply) needs. Checked explicitly
# because a Google credential can be perfectly valid and still be useless for
# Sheets.
SHEETS_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
)


def _timed(fn) -> Dict[str, Any]:
    start = time.monotonic()
    try:
        result = fn()
    except Exception as exc:  # a check must never take down /api/health
        result = {"status": "unknown", "detail": f"check raised: {type(exc).__name__}: {exc}"}
    result["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
    return result


# -------------------------------------------------------------------- gateway

def check_gateway() -> Dict[str, Any]:
    """Hit the Hermes gateway's own /health. Unauthenticated by design."""
    url = f"{config.GATEWAY_URL}/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode("utf-8", "replace")[:2000]
            code = resp.status
    except urllib.error.HTTPError as exc:
        return {"status": "fail", "url": url, "http_status": exc.code,
                "detail": f"gateway returned HTTP {exc.code}"}
    except Exception as exc:
        return {"status": "fail", "url": url,
                "detail": f"gateway unreachable: {type(exc).__name__}: {exc}"}

    payload: Any
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {"raw": body[:200]}

    status = "ok" if code == 200 else "degraded"
    out = {"status": status, "url": url, "http_status": code}
    if isinstance(payload, dict):
        # Echo only identifying fields — never the whole body, which may grow to
        # include configuration detail.
        for key in ("status", "version", "platform", "model", "provider"):
            if key in payload:
                out[key] = payload[key]
    return out


# ----------------------------------------------------------------------- feed

def check_feed() -> Dict[str, Any]:
    """Read the activity feed the way /api/feed does, and report on it."""
    from .feed import feed_path, load_events  # local import: needs sys.path setup

    path = feed_path()
    out: Dict[str, Any] = {"path": str(path)}

    if not path.exists():
        # Not an error — a fresh tenant has emitted nothing yet. But it is not
        # "ok" either, since we have no evidence the write path works.
        out["status"] = "degraded"
        out["detail"] = "feed file does not exist yet (no events emitted)"
        out["events"] = 0
        parent = path.parent
        out["parent_writable"] = parent.exists() and __import__("os").access(parent, 2)
        return out

    events = load_events()
    out["events"] = len(events)
    out["size_bytes"] = path.stat().st_size
    out["writable"] = __import__("os").access(path, 2)

    if events:
        newest = events[-1]
        out["newest_ts"] = newest.get("ts")
        out["newest_kind"] = newest.get("kind")
        out["tenants"] = sorted({e.get("tenant") for e in events if e.get("tenant")})
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(newest["ts"])).total_seconds()
            out["newest_age_seconds"] = round(age, 1)
        except Exception:
            pass

    if not out["writable"]:
        out["status"] = "fail"
        out["detail"] = "feed file is not writable — skills cannot emit events"
    elif not events:
        out["status"] = "degraded"
        out["detail"] = "feed file exists but contains no readable events"
    else:
        out["status"] = "ok"
    return out


# --------------------------------------------------------------------- sheets

def check_sheets() -> Dict[str, Any]:
    """Validate the Google credential via google_creds, then check Sheets scope.

    Same resolution path as skills: the tenant connection, or a fail-closed
    "reconnect Google" refusal. Reports `source` as tenant_connection |
    not_connected. Never dumps tokens or cipher bytes.

    APP_GOOGLE_OAUTH_MOCK skips the Google token refresh and Sheets client
    build — mock tokens cannot authorise against Google, and this check must
    not call the real APIs on testco.
    """
    out: Dict[str, Any] = {}

    try:
        google_creds = _load_google_creds()
    except Exception as exc:
        return {"status": "unknown",
                "detail": f"google_creds unavailable: {type(exc).__name__}: {exc}"}

    try:
        out["source"] = google_creds.source()
    except Exception as exc:
        return {**out, "status": "fail",
                "detail": f"credential not resolvable: {type(exc).__name__}: {exc}"}

    scopes = list(getattr(google_creds, "DEFAULT_SCOPES", []))
    out["scopes"] = scopes
    has_sheets = any(s in SHEETS_SCOPES for s in scopes)
    out["sheets_scope_granted"] = has_sheets

    try:
        creds = google_creds.credentials()
    except google_creds.GoogleNotConnected as exc:
        return {**out, "status": "fail", "credential_valid": False,
                "reconnect_required": True, "detail": str(exc)}
    except ImportError as exc:
        return {**out, "status": "unknown",
                "detail": f"google auth libraries unavailable: {exc}"}
    except Exception as exc:
        return {**out, "status": "fail", "credential_valid": False,
                "detail": f"credential did not authorise: {type(exc).__name__}: {exc}"}

    if config.oauth_mock():
        out["credential_valid"] = True
        if not has_sheets:
            out["status"] = "fail"
            out["detail"] = (
                "Google credential is valid but carries no Sheets/Drive scope "
                f"(granted: {', '.join(s.rsplit('/', 1)[-1] for s in scopes) or 'none'})."
            )
            return out
        out["status"] = "ok"
        out["detail"] = "mock: skipped Google token refresh"
        return out

    # Step 1 — mint a real token. Only when not mock.
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        if not creds.valid:
            creds.refresh(GoogleRequest())
        out["credential_valid"] = bool(creds.valid)
    except ImportError as exc:
        return {**out, "status": "unknown",
                "detail": f"google auth libraries unavailable: {exc}"}
    except Exception as exc:
        return {**out, "status": "fail", "credential_valid": False,
                "detail": f"credential did not authorise: {type(exc).__name__}: {exc}"}

    if not has_sheets:
        out["status"] = "fail"
        out["detail"] = (
            "Google credential is valid but carries no Sheets/Drive scope "
            f"(granted: {', '.join(s.rsplit('/', 1)[-1] for s in scopes) or 'none'}). "
            "Sheets-backed skills (crm, lead-reply) cannot read. Reconnect "
            "Google from Settings and grant every requested permission."
        )
        return out

    # Step 2 — confirm the Sheets API actually answers for this credential.
    try:
        from googleapiclient.discovery import build
        build("sheets", "v4", credentials=creds, cache_discovery=False)
        out["status"] = "ok"
    except Exception as exc:
        out["status"] = "degraded"
        out["detail"] = f"scope granted but Sheets client failed to build: {exc}"
    return out


def _load_google_creds():
    """Import _shared/google_creds, the one credential path the app shares with the skills."""
    import sys
    if str(config.SHARED) not in sys.path:
        sys.path.insert(0, str(config.SHARED))
    import google_creds  # noqa: PLC0415
    return google_creds


# ------------------------------------------------------------------ roll-up

CHECKS = {"gateway": check_gateway, "feed": check_feed, "sheets": check_sheets}


def run_all() -> Dict[str, Any]:
    checks = {name: _timed(fn) for name, fn in CHECKS.items()}
    overall = max((c.get("status", "unknown") for c in checks.values()),
                  key=lambda s: ORDER.get(s, 1))
    return {
        "status": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
