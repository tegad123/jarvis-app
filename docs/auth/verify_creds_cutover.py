#!/usr/bin/env python3
"""Slice 6 — google_creds tenant-connection cutover. Boxed first, then :8710.

Boxed (scratch DB, fake cipher, no HTTP, no Google):
  - connected row → source=tenant_connection (assert SOURCE, not just success)
  - no row → SA fallback AND counter increment AND grepable log line
  - corrupt cipher → ConnectionCipherError through google_creds (not silent None, not SA)
  - fake refresh invalid_grant → needs_reauth, counter 0→1, feed line, caller exception
  - missing APP_GOOGLE_TOKEN_KEY with a row → loud fail, not SA

Live :8710 (mock tokens; no real Google):
  - mock-connect → /api/health sheets source=tenant_connection
  - disconnect → source=service_account and google_creds_sa_fallback moved
  - rerun verify_google_connection.py (36/36) + PIN/operator canaries inside it

Never :8700. Never prints pins, keys, cipher, or token bytes.
"""
import io
import json
import logging
import os
import pathlib
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
IDN = pathlib.Path("/opt/testco/skills/openclaw-imports/_shared/identity.json")
SKILLS_SHARED = pathlib.Path("/opt/testco/skills/openclaw-imports/_shared")
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
os.environ["JARVIS_THREADS_DB"] = str(THREADS)
os.environ["JARVIS_ACTIVITY_FEED"] = str(FEED)
os.environ["JARVIS_SKILLS_ROOT"] = "/opt/testco/skills/openclaw-imports"
os.environ["JARVIS_IDENTITY_CONFIG"] = str(IDN)
os.environ["JARVIS_APP_ROOT"] = "/root/jarvis-app"
os.environ["JARVIS_CREDENTIALS_DIR"] = str(CREDS_DIR)
os.environ["APP_GOOGLE_OAUTH_MOCK"] = "1"
os.environ.pop("JARVIS_GOOGLE_SA_KEY", None)
os.environ.pop("JARVIS_GOOGLE_DISABLE_SA", None)

sys.path.insert(0, "/root/jarvis-app")
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


def reset_creds_module(gcreds):
    gcreds._last_source = None
    gcreds._creds_cache.clear()


SA_FALLBACK_LOG = "google_creds: service-account fallback (no tenant connection row)"
COUNTER_SA = "google_creds_sa_fallback"
COUNTER_REAUTH = "google_connection_needs_reauth"
COUNTER_DECRYPT = "google_connection_decrypt_failed"

print("── boxed isolation ──────────────────────────────────────────")
print(f"  box={BOX}")
ck("identity source is testco skills, not live hermes",
   "/opt/testco/skills" in str(IDN) and "/root/.hermes" not in str(IDN))
ck("this process patches TESTCO google_creds, not live",
   (SKILLS_SHARED / "google_creds.py").is_file()
   and (not LIVE_SKILLS_CREDS.exists()
        or SKILLS_SHARED.resolve() != LIVE_SKILLS_CREDS.parent.resolve()))
ck("APP_GOOGLE_TOKEN_KEY is set (value not printed)",
   bool(os.environ.get("APP_GOOGLE_TOKEN_KEY")))

from app import accounts, google_connection, threads  # noqa: E402
import google_creds  # noqa: E402

ck("google_creds loaded from testco skills tree",
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
ck("google_creds JARVIS_APP_ROOT is this checkout, not live default",
   os.environ.get("JARVIS_APP_ROOT") == "/root/jarvis-app")

accounts.init()
threads._ensure()

logbuf = io.StringIO()
h = logging.StreamHandler(logbuf)
h.setLevel(logging.WARNING)
lg = logging.getLogger("google_creds")
lg.setLevel(logging.WARNING)
lg.addHandler(h)

sa_hits = {"n": 0}


def fake_sa(scopes, subj):
    sa_hits["n"] += 1

    class C:
        valid = True

    return C()


google_creds._service_account_credentials = fake_sa

print("\n── boxed: prefer tenant connection ────────────────────────")
plant_connected(google_connection)
reset_creds_module(google_creds)
before_sa = boxed_counter(COUNTER_SA)
peek = google_creds.source()
creds = google_creds.credentials()
ck("source() peeks tenant_connection when a cipher row exists",
   peek == "tenant_connection", peek)
ck("credentials() source is tenant_connection (not just success)",
   google_creds.source() == "tenant_connection",
   google_creds.source())
ck("tenant path returned credentials (not None)",
   creds is not None, type(creds).__name__)
ck("SA builder was not called when a tenant row exists",
   sa_hits["n"] == 0, f"sa_hits={sa_hits['n']}")
ck("SA fallback counter did not move on tenant path",
   boxed_counter(COUNTER_SA) == before_sa,
   f"{before_sa}→{boxed_counter(COUNTER_SA)}")

print("\n── boxed: no row → announced SA fallback ──────────────────")
google_connection.clear()
reset_creds_module(google_creds)
# sa_key_path must look present so the SA branch is taken, not oauth_token.json.
google_creds.sa_key_path = lambda: pathlib.Path("/etc/hosts")
logbuf.truncate(0)
logbuf.seek(0)
before_sa = boxed_counter(COUNTER_SA)
peek = google_creds.source()
got = google_creds.credentials()
logged = logbuf.getvalue()
after_sa = boxed_counter(COUNTER_SA)
ck("source() is service_account when no cipher row",
   peek == "service_account", peek)
ck("credentials() source is service_account",
   google_creds.source() == "service_account",
   google_creds.source())
ck("SA builder was invoked (fallback chosen)",
   sa_hits["n"] >= 1 and got is not None,
   f"sa_hits={sa_hits['n']}")
ck("SA fallback counter incremented",
   after_sa >= before_sa + 1,
   f"{before_sa}→{after_sa}")
ck("SA fallback log line exists (announced, not silent)",
   SA_FALLBACK_LOG in logged,
   "grep hit" if SA_FALLBACK_LOG in logged else "line missing from logger")

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
reset_creds_module(google_creds)
sa_before_hits = sa_hits["n"]
before_dec = boxed_counter(COUNTER_DECRYPT)
before_sa = boxed_counter(COUNTER_SA)
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
ck("corrupt cipher did not fall back to SA",
   sa_hits["n"] == sa_before_hits and boxed_counter(COUNTER_SA) == before_sa,
   f"sa_hits {sa_before_hits}→{sa_hits['n']} fallback {before_sa}→{boxed_counter(COUNTER_SA)}")
google_connection.clear()

print("\n── boxed: fake refresh invalid_grant ──────────────────────")
plant_connected(google_connection)
reset_creds_module(google_creds)
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
reset_creds_module(google_creds)
saved_key = os.environ.pop("APP_GOOGLE_TOKEN_KEY")
sa_before_hits = sa_hits["n"]
before_sa = boxed_counter(COUNTER_SA)
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
ck("missing key did not fall back to SA",
   sa_hits["n"] == sa_before_hits and boxed_counter(COUNTER_SA) == before_sa,
   f"sa_hits {sa_before_hits}→{sa_hits['n']}")
google_connection.clear()

if not all(ok):
    print(f"\nboxed {sum(ok)}/{len(ok)} passed — stopping before :8710")
    sys.exit(1)

print(f"\nboxed {sum(ok)}/{len(ok)} passed")

# ---------------------------------------------------------------------------
# LIVE :8710 — mock connection only, no real Google
# ---------------------------------------------------------------------------

BASE = os.environ.get("AUTH_BASE", "http://127.0.0.1:8710").rstrip("/")
if BASE.endswith(":8700"):
    sys.exit("REFUSING: :8700 is the live tenant.")

HOME = pathlib.Path("/opt/testco/hermes")
LIVE_ACCOUNTS = HOME / "state" / "jarvis_accounts.db"
LIVE_THREADS = HOME / "state" / "jarvis_threads.db"
PINS_PATH = pathlib.Path("/opt/testco/SEEDED_PINS.json")
SCR = pathlib.Path("/tmp/verify-creds-cutover")
SCR.mkdir(parents=True, exist_ok=True)
MAILBOX = "mailbox@testco.example.com"
GENERIC = "That PIN is not right."


def pins():
    raw = json.loads(PINS_PATH.read_text())
    if raw and isinstance(next(iter(raw.values())), dict):
        return {k: v.get("pin") for k, v in raw.items()}
    return raw


def passphrase():
    for line in (HOME / ".env").read_text().splitlines():
        if line.strip().startswith("APP_PASSPHRASE="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("REFUSING: APP_PASSPHRASE missing (presence only; value not printed)")


def curl(path, jar=None, method="GET", body=None):
    out = SCR / "body"
    hdr = SCR / "hdr"
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


def sheets_source(health):
    if not isinstance(health, dict):
        return None
    checks = health.get("checks") or {}
    sheets = checks.get("sheets") or {}
    return sheets.get("source")


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


print("\n── live :8710 mock resolution ─────────────────────────────")
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

c_tenant = live_counter(COUNTER_SA)
code, health, _ = curl("/api/health", jar)
src = sheets_source(health)
ck("GET /api/health sheets source=tenant_connection while connected",
   src == "tenant_connection",
   f"HTTP {code} source={src}")
ck("tenant health did not increment SA fallback counter",
   live_counter(COUNTER_SA) == c_tenant,
   f"{c_tenant}→{live_counter(COUNTER_SA)}")

c_before = live_counter(COUNTER_SA)
code, d, _ = curl("/api/google/connection/disconnect", jar, "POST")
ck("disconnect before SA-source health",
   code == 200 and isinstance(d, dict) and d.get("ok") is True,
   f"HTTP {code}")
code, health, _ = curl("/api/health", jar)
src = sheets_source(health)
c_after = live_counter(COUNTER_SA)
ck("GET /api/health sheets source=service_account after disconnect",
   src == "service_account",
   f"HTTP {code} source={src}")
ck("SA fallback counter moved after disconnect health",
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
rerun = subprocess.run(
    ["/root/jarvis-app/.venv/bin/python",
     "/root/jarvis-app/docs/auth/verify_google_connection.py"],
    capture_output=True, text=True,
)
sys.stdout.write(rerun.stdout)
if rerun.stderr:
    sys.stderr.write(rerun.stderr)
tail = (rerun.stdout or "").strip().splitlines()[-1] if rerun.stdout else ""
ck("verify_google_connection.py exited 0",
   rerun.returncode == 0, f"exit={rerun.returncode}")
ck("verify_google_connection.py reports 36/36",
   tail.strip() == "36/36 passed",
   tail.strip())

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
