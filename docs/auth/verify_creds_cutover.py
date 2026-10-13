#!/usr/bin/env python3
"""Slice 6 — google_creds tenant connection, fail closed. Boxed first, then AUTH_BASE.

Boxed (scratch DB, fake cipher, no HTTP, no Google):
  - connected row → source=tenant_connection (assert SOURCE, not just success)
  - no row → GoogleNotConnected ("Reconnect Google") AND counter
    google_creds_not_connected +1 AND grepable log line; no fallback exists
  - corrupt cipher → ConnectionCipherError through google_creds (not silent None)
  - fake refresh invalid_grant → needs_reauth, counter 0→1, feed line, caller exception
  - missing APP_GOOGLE_TOKEN_KEY with a row → loud RuntimeError

HTTP AUTH_BASE (default :8711, the verify sandbox; mock tokens, no real Google):
  - mock-connect → /api/health sheets source=tenant_connection
  - disconnect → source=not_connected, reconnect_required, counter moved
  - rerun verify_google_connection.py from this checkout; exit 0 and N/N

JARVIS_SKILLS_ROOT / JARVIS_IDENTITY_CONFIG / JARVIS_THREADS_DB select the tree
and ledger under test; the app is this checkout. The operator passphrase comes
from VERIFY_OPERATOR_PASSPHRASE; no env file is read. Never :8700. Never prints
pins, keys, cipher, or token bytes.
"""
import io
import json
import logging
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# ---------------------------------------------------------------------------
# BOXED — env isolation BEFORE any app / google_creds import
# ---------------------------------------------------------------------------

BOX = pathlib.Path(tempfile.mkdtemp(prefix="creds-cutover-"))
APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILLS_ROOT = (os.environ.get("JARVIS_SKILLS_ROOT") or os.environ.get("VERIFY_SKILLS_ROOT")
               or "/opt/jarvis-verify-fixture/skills/openclaw-imports")
IDN = pathlib.Path(os.environ.get(
    "JARVIS_IDENTITY_CONFIG", "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json"))
SKILLS_SHARED = pathlib.Path(SKILLS_ROOT) / "_shared"
FORBIDDEN = (pathlib.Path("/root/.hermes"), pathlib.Path("/root/jarvis-app/data"))
LIVE_SKILLS_CREDS = pathlib.Path(
    "/root/.hermes/skills/openclaw-imports/_shared/google_creds.py"
)

ACCOUNTS = BOX / "hermes/state/jarvis_accounts.db"
THREADS = BOX / "hermes/state/jarvis_threads.db"
FEED = BOX / "hermes/state/activity_feed.jsonl"
CREDS_DIR = BOX / "hermes/credentials"
(BOX / "hermes/state").mkdir(parents=True)
CREDS_DIR.mkdir(parents=True)

from cryptography.fernet import Fernet  # noqa: E402
os.environ["APP_GOOGLE_TOKEN_KEY"] = Fernet.generate_key().decode()  # never printed
os.environ["HERMES_HOME"] = str(BOX / "hermes")
os.environ["JARVIS_ACCOUNTS_DB"] = str(ACCOUNTS)
os.environ["JARVIS_ACTIVITY_FEED"] = str(FEED)
HTTP_THREADS = os.environ.get("JARVIS_THREADS_DB")
os.environ["JARVIS_THREADS_DB"] = str(THREADS)
os.environ["JARVIS_SKILLS_ROOT"] = SKILLS_ROOT
os.environ["JARVIS_IDENTITY_CONFIG"] = str(IDN)
os.environ["JARVIS_APP_ROOT"] = str(APP_ROOT)
os.environ["JARVIS_CREDENTIALS_DIR"] = str(CREDS_DIR)
os.environ["APP_GOOGLE_OAUTH_MOCK"] = "1"

sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(SKILLS_SHARED))

ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))


def boxed_counter(name):
    if not THREADS.exists():
        return 0
    con = sqlite3.connect(str(THREADS))
    try:
        row = con.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    con.close()
    return int(row[0]) if row else 0


def plant_connected(gc_mod):
    """Encrypt a fake payload and write one row. Never print payload/cipher."""
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    payload = {
        "refresh_token": "boxed-refresh",
        "access_token": "boxed-access",
        "expiry": expiry,
        "scopes": "https://www.googleapis.com/auth/spreadsheets",
    }
    cipher = gc_mod.encrypt_tokens(payload)
    gc_mod.put_connected(
        google_email="mailbox@testco.example.com",
        scopes=payload["scopes"],
        token_cipher=cipher,
        connected_by="dana",
    )


NOT_CONNECTED_LOG = "google_creds: refused, no tenant connection (reconnect Google)"
COUNTER_NC = "google_creds_not_connected"
RECONNECT = "Reconnect Google from Settings"
COUNTER_REAUTH = "google_connection_needs_reauth"
COUNTER_DECRYPT = "google_connection_decrypt_failed"

print("── boxed isolation ──────────────────────────────────────────")
print(f"  box={BOX}")
ck("identity source is not live hermes",
   "/root/.hermes" not in str(IDN.resolve()))
ck("this process patches a non-live google_creds",
   (SKILLS_SHARED / "google_creds.py").is_file()
   and (not LIVE_SKILLS_CREDS.exists()
        or SKILLS_SHARED.resolve() != LIVE_SKILLS_CREDS.parent.resolve()))
ck("APP_GOOGLE_TOKEN_KEY is set (value not printed)",
   bool(os.environ.get("APP_GOOGLE_TOKEN_KEY")))

from app import accounts, google_connection, threads  # noqa: E402
import google_creds  # noqa: E402

ck("google_creds loaded from JARVIS_SKILLS_ROOT",
   str(pathlib.Path(google_creds.__file__).resolve()).startswith(
       str(SKILLS_SHARED.resolve())
   ),
   google_creds.__file__)
ck("accounts.DB_PATH is inside the box",
   str(accounts.DB_PATH.resolve()).startswith(str(BOX.resolve())),
   str(accounts.DB_PATH))
ck("accounts.DB_PATH is not live hermes",
   not str(accounts.DB_PATH.resolve()).startswith(str(pathlib.Path("/root/.hermes").resolve())))
ck("threads.DB_PATH is inside the box",
   str(threads.DB_PATH.resolve()).startswith(str(BOX.resolve())),
   str(threads.DB_PATH))
ck("google_creds JARVIS_APP_ROOT is this checkout",
   os.environ.get("JARVIS_APP_ROOT") == str(APP_ROOT))

accounts.init()
threads._ensure()

logbuf = io.StringIO()
h = logging.StreamHandler(logbuf)
h.setLevel(logging.WARNING)
lg = logging.getLogger("google_creds")
lg.setLevel(logging.WARNING)
lg.addHandler(h)

print("\n── boxed: prefer tenant connection ────────────────────────")
plant_connected(google_connection)
before_nc = boxed_counter(COUNTER_NC)
peek = google_creds.source()
creds = google_creds.credentials()
ck("source() peeks tenant_connection when a cipher row exists",
   peek == "tenant_connection", peek)
ck("credentials() source is tenant_connection (not just success)",
   google_creds.source() == "tenant_connection",
   google_creds.source())
ck("tenant path returned credentials (not None)",
   creds is not None, type(creds).__name__)
ck("not-connected counter did not move on tenant path",
   boxed_counter(COUNTER_NC) == before_nc,
   f"{before_nc}→{boxed_counter(COUNTER_NC)}")

print("\n── boxed: no row → fail closed, reconnect Google ──────────")
google_connection.clear()
logbuf.truncate(0)
logbuf.seek(0)
before_nc = boxed_counter(COUNTER_NC)
peek = google_creds.source()
raised = None
got = "unset"
try:
    got = google_creds.credentials()
except Exception as exc:
    raised = exc
logged = logbuf.getvalue()
after_nc = boxed_counter(COUNTER_NC)
ck("source() is not_connected when no cipher row",
   peek == "not_connected", peek)
ck("credentials() raises GoogleNotConnected, returns nothing",
   type(raised).__name__ == "GoogleNotConnected" and got == "unset",
   f"raised={type(raised).__name__ if raised else None}")
ck("refusal carries the reconnect message",
   raised is not None and RECONNECT in str(raised), str(raised)[:120])
ck("google_creds_not_connected counter +1",
   after_nc == before_nc + 1,
   f"{before_nc}→{after_nc}")
ck("refusal log line exists (announced, not silent)",
   NOT_CONNECTED_LOG in logged,
   "grep hit" if NOT_CONNECTED_LOG in logged else "line missing from logger")
ck("google_creds exposes no fallback builders",
   not any(hasattr(google_creds, n) for n in
           ("_service_account_credentials", "sa_key_path", "_creds_cache", "mode")),
   [n for n in ("_service_account_credentials", "sa_key_path", "_creds_cache", "mode")
    if hasattr(google_creds, n)])

print("\n── boxed: corrupt cipher through google_creds ─────────────")
plant_connected(google_connection)
con = sqlite3.connect(str(ACCOUNTS))
blob = con.execute("SELECT token_cipher FROM google_connection WHERE id=1").fetchone()
ck("a cipher blob is stored to corrupt",
   blob is not None and blob[0] is not None and len(blob[0]) > 8,
   f"cipher_bytes={len(blob[0]) if blob and blob[0] is not None else 0}")
ba = bytearray(blob[0])
ba[len(ba) // 2] ^= 0xFF
con.execute("UPDATE google_connection SET token_cipher=? WHERE id=1", (bytes(ba),))
con.commit()
con.close()
before_dec = boxed_counter(COUNTER_DECRYPT)
raised = None
result = "unset"
try:
    result = google_creds.credentials()
except google_connection.ConnectionCipherError as exc:
    raised = type(exc).__name__
except Exception as exc:
    raised = type(exc).__name__
ck("google_creds raises ConnectionCipherError on flipped cipher (not None)",
   raised == "ConnectionCipherError" and result == "unset",
   f"raised={raised} result_is_none={result is None}")
ck("decrypt-failed counter incremented through google_creds",
   boxed_counter(COUNTER_DECRYPT) >= before_dec + 1,
   f"{before_dec}→{boxed_counter(COUNTER_DECRYPT)}")
google_connection.clear()

print("\n── boxed: fake refresh invalid_grant ──────────────────────")
plant_connected(google_connection)
from google.auth.exceptions import RefreshError  # noqa: E402

_orig_maybe = google_creds._maybe_refresh


def boom(creds, gc):
    raise RefreshError("invalid_grant: Token has been expired or revoked.")


google_creds._maybe_refresh = boom
before_reauth = boxed_counter(COUNTER_REAUTH)
FEED.parent.mkdir(parents=True, exist_ok=True)
if FEED.exists():
    FEED.write_text("")
raised = None
result = "unset"
try:
    result = google_creds.credentials()
except Exception as exc:
    raised = type(exc).__name__
google_creds._maybe_refresh = _orig_maybe
st = google_connection.status_public()
after_reauth = boxed_counter(COUNTER_REAUTH)
feed_txt = FEED.read_text() if FEED.exists() else ""
ck("invalid_grant caller gets an exception, not None",
   raised is not None and result == "unset",
   f"raised={raised} result_is_none={result is None}")
ck("status flipped to needs_reauth",
   st.get("status") == "needs_reauth",
   f"status={st.get('status')}")
ck("google_connection_needs_reauth counter 0→1 (or +1)",
   after_reauth >= before_reauth + 1,
   f"{before_reauth}→{after_reauth}")
ck("needs_reauth feed event written",
   "google.needs_reauth" in feed_txt
   and "Google mailbox needs to be reconnected" in feed_txt,
   "kind+headline present" if "google.needs_reauth" in feed_txt else "feed miss")
ck("needs_reauth headline has no email address",
   "@" not in feed_txt.split("headline")[-1][:200] if "headline" in feed_txt else True)

print("\n── boxed: missing token key with a connected row ──────────")
plant_connected(google_connection)
saved_key = os.environ.pop("APP_GOOGLE_TOKEN_KEY")
raised = None
result = "unset"
try:
    result = google_creds.credentials()
except Exception as exc:
    raised = type(exc).__name__
os.environ["APP_GOOGLE_TOKEN_KEY"] = saved_key
ck("missing APP_GOOGLE_TOKEN_KEY with a row is a loud failure",
   raised == "RuntimeError" and result == "unset",
   f"raised={raised} result_is_none={result is None}")
google_connection.clear()

if not all(ok):
    print(f"\nboxed {sum(ok)}/{len(ok)} passed — stopping before :8711")
    sys.exit(1)

print(f"\nboxed {sum(ok)}/{len(ok)} passed")

# ---------------------------------------------------------------------------
# SANDBOX :8711 — mock connection only, no real Google
# ---------------------------------------------------------------------------

BASE = os.environ.get("AUTH_BASE", "http://127.0.0.1:8711").rstrip("/")
if BASE.endswith(":8700"):
    sys.exit("REFUSING: :8700 is the live tenant.")

HOME = pathlib.Path("/opt/jarvis-verify-fixture/hermes")
LIVE_THREADS = pathlib.Path(HTTP_THREADS or HOME / "state" / "jarvis_threads.db")
PINS_PATH = pathlib.Path("/opt/jarvis-verify-fixture/SEEDED_PINS.json")
SCR = pathlib.Path("/tmp/verify-creds-cutover")
SCR.mkdir(parents=True, exist_ok=True)
MAILBOX = (json.loads(IDN.read_text()).get("assistant_address") or "").strip()
GENERIC = "That PIN is not right."


def pins():
    raw = json.loads(PINS_PATH.read_text())
    if raw and isinstance(next(iter(raw.values())), dict):
        return {k: v.get("pin") for k, v in raw.items()}
    return raw


def passphrase():
    value = os.environ.get("VERIFY_OPERATOR_PASSPHRASE")
    if not value:
        sys.exit("REFUSING: VERIFY_OPERATOR_PASSPHRASE is not set (value never printed)")
    return value


def curl(path, jar=None, method="GET", body=None):
    out = SCR / "body"
    hdr = SCR / "hdr"
    out.unlink(missing_ok=True)
    hdr.unlink(missing_ok=True)
    cmd = [
        "curl", "-s", "-D", str(hdr), "-o", str(out), "-w", "%{http_code}",
        "-X", method, "--max-redirs", "0",
    ]
    url = path if path.startswith("http") else BASE + path
    cmd.append(url)
    if jar:
        cmd += ["-b", jar, "-c", jar]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    code = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    text = out.read_text() if out.exists() else ""
    heads = hdr.read_text() if hdr.exists() else ""
    try:
        data = json.loads(text) if text.strip() else {}
    except Exception:
        data = text
    return int(code or 0), data, heads


def location(heads):
    for line in heads.splitlines():
        if line.lower().startswith("location:"):
            loc = line.split(":", 1)[1].strip()
            if loc.startswith("/"):
                return BASE + loc
            return loc
    return ""


def live_counter(name):
    src, dst = LIVE_THREADS, SCR / "threads-snap.db"
    con_src = sqlite3.connect(str(src))
    con_dst = sqlite3.connect(str(dst))
    con_src.backup(con_dst)
    con_dst.close()
    con_src.close()
    con = sqlite3.connect(str(dst))
    try:
        row = con.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    con.close()
    return int(row[0]) if row else 0


def sheets_check(health):
    if not isinstance(health, dict):
        return {}
    return (health.get("checks") or {}).get("sheets") or {}


def mock_connect_flow(jar, email, grant="full"):
    code, _, heads = curl("/api/google/connect/start", jar)
    loc = location(heads)
    if code != 302 or not loc:
        return code, None, heads, loc
    parsed = urlparse(loc)
    q = parse_qs(parsed.query)
    q["email"] = [email]
    q["grant"] = [grant]
    if "purpose" not in q:
        q["purpose"] = ["connect"]
    authz = urlunparse(parsed._replace(query=urlencode(q, doseq=True)))
    code, _, heads = curl(authz, jar)
    cb = location(heads)
    return code, cb, heads, loc


print(f"\n── HTTP {BASE} mock resolution ─────────────────────────────")
PINS = pins()
jar = str(SCR / "dana.jar")
pathlib.Path(jar).unlink(missing_ok=True)
code, d, _ = curl("/api/login", jar, "POST",
                  {"person_key": "dana", "pin": PINS["dana"]})
ck("dana PIN login for live cutover",
   code == 200 and d.get("person_key") == "dana" and d.get("role") == "owner",
   f"HTTP {code}")

curl("/api/google/connection/disconnect", jar, "POST")
code, cb, _, _ = mock_connect_flow(jar, MAILBOX, grant="full")
ck("full mock authorize redirects to callback",
   code == 302 and cb and "/oauth/callback" in (cb or ""), f"HTTP {code}")
code, _, _ = curl(cb, jar)
ck("full connect callback redirects home", code == 302, f"HTTP {code}")

code, status_body, _ = curl("/api/google/connection", jar)
ck("status GET includes refreshed_at (field only, no secrets)",
   code == 200 and isinstance(status_body, dict)
   and "refreshed_at" in status_body
   and status_body.get("status") == "connected",
   f"HTTP {code} keys={sorted(status_body) if isinstance(status_body, dict) else type(status_body).__name__}")

c_tenant = live_counter(COUNTER_NC)
code, health, _ = curl("/api/health", jar)
src = sheets_check(health).get("source")
ck("GET /api/health sheets source=tenant_connection while connected",
   src == "tenant_connection",
   f"HTTP {code} source={src}")
ck("tenant health did not increment the not-connected counter",
   live_counter(COUNTER_NC) == c_tenant,
   f"{c_tenant}→{live_counter(COUNTER_NC)}")

c_before = live_counter(COUNTER_NC)
code, d, _ = curl("/api/google/connection/disconnect", jar, "POST")
ck("disconnect before not-connected health",
   code == 200 and isinstance(d, dict) and d.get("ok") is True,
   f"HTTP {code}")
code, health, _ = curl("/api/health", jar)
sh = sheets_check(health)
c_after = live_counter(COUNTER_NC)
ck("GET /api/health sheets source=not_connected after disconnect",
   sh.get("source") == "not_connected",
   f"HTTP {code} source={sh.get('source')}")
ck("disconnected health is fail with reconnect_required and the message",
   sh.get("status") == "fail" and sh.get("reconnect_required") is True
   and RECONNECT in (sh.get("detail") or ""),
   f"status={sh.get('status')} detail={(sh.get('detail') or '')[:80]}")
ck("not-connected counter moved after disconnect health",
   c_after >= c_before + 1,
   f"{c_before}→{c_after}")

print("\n── absence: PIN and operator (live) ───────────────────────")
code, d, _ = curl("/api/login", str(SCR / "dana-pin.jar"), "POST",
                  {"person_key": "dana", "pin": PINS["dana"]})
ck("dana PIN login still 200, role=owner",
   code == 200 and d.get("role") == "owner" and d.get("person_key") == "dana",
   f"HTTP {code}")
code, d, _ = curl("/api/login", str(SCR / "bad-pin.jar"), "POST",
                  {"person_key": "dana", "pin": "zzzz-zzzz-zzzz"})
ck("wrong PIN still 401 with the generic message",
   code == 401 and isinstance(d, dict) and d.get("detail") == GENERIC,
   f"HTTP {code}")
jar_op = str(SCR / "op.jar")
pathlib.Path(jar_op).unlink(missing_ok=True)
code, d, _ = curl("/api/login", jar_op, "POST", {"passphrase": passphrase()})
ck("operator passphrase still person_key=null, role=operator",
   code == 200 and d.get("person_key") is None and d.get("role") == "operator",
   f"HTTP {code}")

print("\n── rerun verify_google_connection.py ──────────────────────")
rerun_env = dict(os.environ)
if HTTP_THREADS:
    rerun_env["JARVIS_THREADS_DB"] = HTTP_THREADS
else:
    rerun_env.pop("JARVIS_THREADS_DB", None)
for k in ("HERMES_HOME", "JARVIS_ACCOUNTS_DB", "JARVIS_ACTIVITY_FEED",
          "JARVIS_APP_ROOT", "JARVIS_CREDENTIALS_DIR", "APP_GOOGLE_TOKEN_KEY"):
    rerun_env.pop(k, None)
if os.environ.get("VERIFY_HTTP_ACCOUNTS_DB"):
    rerun_env["JARVIS_ACCOUNTS_DB"] = os.environ["VERIFY_HTTP_ACCOUNTS_DB"]
rerun = subprocess.run(
    [sys.executable, str(APP_ROOT / "docs" / "auth" / "verify_google_connection.py")],
    capture_output=True, text=True, env=rerun_env,
)
sys.stdout.write(rerun.stdout)
if rerun.stderr:
    sys.stderr.write(rerun.stderr)
tail = (rerun.stdout or "").strip().splitlines()[-1] if rerun.stdout else ""
ck("verify_google_connection.py exited 0",
   rerun.returncode == 0, f"exit={rerun.returncode}")
m = re.fullmatch(r"(\d+)/(\d+) passed", tail.strip())
ck("verify_google_connection.py reports N/N passed",
   bool(m) and m.group(1) == m.group(2) and int(m.group(2)) > 0,
   tail.strip())

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
