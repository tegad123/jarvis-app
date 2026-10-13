#!/usr/bin/env python3
"""Slice 4 — tenant Google connection canaries.

Default AUTH_BASE is the verify sandbox (:8711, docs/tasks/tasks_sandbox.sh:
MOCK=1, throwaway token key, APP_PUBLIC_ORIGIN loopback). Override
JARVIS_ACCOUNTS_DB to that sandbox's accounts.db so ledger snaps read the
sandbox, not the pristine fixture.

Presence: connect start dana 302 / omar 403 / operator 403 / logged-out 401;
owner mock-connect row 0→1, status=connected, google_email set; status JSON
has no token-ish keys and no base64 blobs; 4-of-5 grant leaves row count 0
with a visible rejection; disconnect 1→0; connect callback replay rejected;
callback without staff cookie stores nothing; /api/health has no refresh token
after connect.

Class: connect never reads email from an id_token. The mock token JSON has
no id_token/email; the address comes from Gmail users.getProfile emailAddress.
The owner connect plants the tenant identity's assistant_address; a profile
with any other address is refused reason=mismatch, writes 0 rows, and the
refusal log line (VERIFY_APP_LOG, the app's log file) carries no token.

No fallback (subprocess, no HTTP): decoy service-account / google_token.json /
ADC files are planted in a scratch HERMES_HOME and named by every legacy env
var. With no stored connection google_creds.credentials(), gmail() and
leads.load() fail with "Reconnect Google", bump google_creds_not_connected
0→1 and log the refusal, and an audit hook on open/listdir records zero
touches of the decoys (canary: the hook records a deliberate decoy open).
Companion: with a planted connection credentials() returns, source is
tenant_connection, and the decoys are still untouched.

Reconnect (HTTP): with 0 rows /api/health sheets is fail, source=not_connected,
reconnect_required, detail carries the reconnect message, and the counter
moves; after connect source=tenant_connection and the counter does not move.

Absence: dana PIN 200; wrong PIN 401 same message; operator person_key=null;
GET /api/people has no emails.

The operator passphrase comes from VERIFY_OPERATOR_PASSPHRASE; this script
never reads an env file. Never :8700. Never prints pins, passphrases, env
values, keys, or cipher bytes.
"""
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

BASE = os.environ.get("AUTH_BASE", "http://127.0.0.1:8711").rstrip("/")
if BASE.endswith(":8700"):
    sys.exit("REFUSING: :8700 is the live tenant.")

HOME = pathlib.Path("/opt/jarvis-verify-fixture/hermes")
ACCOUNTS = pathlib.Path(
    os.environ.get("JARVIS_ACCOUNTS_DB", str(HOME / "state" / "jarvis_accounts.db"))
)
THREADS = pathlib.Path(
    os.environ.get("JARVIS_THREADS_DB", str(HOME / "state" / "jarvis_threads.db"))
)
APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_SRC = APP_ROOT / "app" / "google_oauth.py"
SKILLS_ROOT = (os.environ.get("JARVIS_SKILLS_ROOT") or os.environ.get("VERIFY_SKILLS_ROOT")
               or "/opt/jarvis-verify-fixture/skills/openclaw-imports")
RECONNECT = "Reconnect Google from Settings"
NOT_CONNECTED_LOG = "google_creds: refused, no tenant connection (reconnect Google)"
COUNTER_NOT_CONNECTED = "google_creds_not_connected"
PINS_PATH = pathlib.Path("/opt/jarvis-verify-fixture/SEEDED_PINS.json")
SCR = pathlib.Path("/tmp/verify-google-connection")
SCR.mkdir(parents=True, exist_ok=True)
GENERIC = "That PIN is not right."
IDENTITY = pathlib.Path(os.environ.get(
    "JARVIS_IDENTITY_CONFIG",
    "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json",
))
MAILBOX = (json.loads(IDENTITY.read_text()).get("assistant_address") or "").strip()
if not MAILBOX:
    sys.exit(f"REFUSING: {IDENTITY} has no assistant_address")
WRONG_MAILBOX = "mailbox@testco.example.com"
if WRONG_MAILBOX.lower() == MAILBOX.lower():
    sys.exit("REFUSING: mismatch fixture equals the tenant assistant_address")
APP_LOG = os.environ.get("VERIFY_APP_LOG")
PARTIAL_MSG = "all five permissions are required"
SIGNIN_AGAIN = "sign in again then connect"
TOKENISH = ("token", "refresh", "client_secret", "cipher")
BLOB_RE = re.compile(r"^gAAAAA[A-Za-z0-9_\-]{20,}$")

ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))


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


def counter(name):
    """WAL-inclusive snapshot of one counter from the app's threads DB."""
    if not THREADS.exists():
        return 0
    dst = SCR / "threads-snap.db"
    con_src = sqlite3.connect(str(THREADS))
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


NO_FALLBACK_CHILD = r'''
import io, json, logging, os, sqlite3, sys
decoy = os.environ["DECOY_DIR"]
touched = []

def hook(event, args):
    if event in ("open", "os.listdir", "os.scandir") and args:
        p = args[0]
        if isinstance(p, bytes):
            p = p.decode(errors="replace")
        if isinstance(p, os.PathLike):
            p = os.fspath(p)
        if isinstance(p, str) and os.path.abspath(p).startswith(decoy):
            touched.append(os.path.basename(p) or p)

sys.addaudithook(hook)
out = {}
with open(os.path.join(decoy, "canary.txt")) as fh:
    fh.read()
out["canary_seen"] = "canary.txt" in touched
touched.clear()

buf = io.StringIO()
logging.getLogger().addHandler(logging.StreamHandler(buf))
logging.getLogger().setLevel(logging.INFO)
sys.path.insert(0, os.environ["JARVIS_APP_ROOT"])
sys.path.insert(0, os.path.join(os.environ["JARVIS_SKILLS_ROOT"], "_shared"))
from app import accounts, threads, google_connection
accounts.init()
threads._ensure()
import google_creds
out["module"] = google_creds.__file__

def ctr():
    con = sqlite3.connect(os.environ["JARVIS_THREADS_DB"])
    try:
        row = con.execute("SELECT value FROM counters WHERE name=?",
                          ("google_creds_not_connected",)).fetchone()
    except sqlite3.OperationalError:
        row = None
    con.close()
    return int(row[0]) if row else 0

if os.environ["CHILD_MODE"] == "connected":
    payload = {"refresh_token": "child-refresh", "access_token": "child-access",
               "expiry": "2999-01-01T00:00:00+00:00",
               "scopes": "https://www.googleapis.com/auth/spreadsheets"}
    google_connection.put_connected(
        google_email="child@tenant.example", scopes=payload["scopes"],
        token_cipher=google_connection.encrypt_tokens(payload), connected_by="dana")

def attempt(fn):
    try:
        got = fn()
        return {"raised": None, "returned": got is not None}
    except Exception as exc:
        return {"raised": type(exc).__name__, "msg": str(exc)[:300]}

out["counter_before"] = ctr()
out["credentials"] = attempt(google_creds.credentials)
out["counter_after_credentials"] = ctr()
out["source"] = google_creds.source()
if os.environ["CHILD_MODE"] == "empty":
    out["gmail"] = attempt(google_creds.gmail)
    from app import leads
    try:
        leads.load()
        out["leads"] = {"raised": None}
    except leads.LeadsUnavailable as exc:
        out["leads"] = {"raised": "LeadsUnavailable", "msg": exc.message}
    except Exception as exc:
        out["leads"] = {"raised": type(exc).__name__, "msg": str(exc)[:300]}
out["log"] = buf.getvalue()[-4000:]
out["touched"] = sorted(set(touched))
print("CHILD_RESULT " + json.dumps(out))
'''

DECOY_FILES = {
    "service_account.json": {"type": "service_account", "client_email": "decoy@decoy.example",
                             "private_key": "DECOY-NOT-A-KEY", "token_uri": "https://decoy.example/token"},
    "sa_jarvis_prod.json": {"type": "service_account", "client_email": "decoy@decoy.example",
                            "private_key": "DECOY-NOT-A-KEY", "token_uri": "https://decoy.example/token"},
    "google_token.json": {"refresh_token": "decoy-refresh", "client_id": "decoy",
                          "client_secret": "decoy", "token_uri": "https://decoy.example/token"},
    "oauth_token.json": {"refresh_token": "decoy-refresh", "client_id": "decoy",
                         "client_secret": "decoy", "token_uri": "https://decoy.example/token"},
    "adc.json": {"type": "authorized_user", "refresh_token": "decoy-refresh",
                 "client_id": "decoy", "client_secret": "decoy"},
}


def run_no_fallback_child(mode):
    """Run google_creds in a scratch tenant with decoy legacy credentials."""
    from cryptography.fernet import Fernet

    box = pathlib.Path(tempfile.mkdtemp(prefix="verify-no-fallback-"))
    try:
        home = box / "hermes"
        decoy = home / "credentials"
        (home / "state").mkdir(parents=True)
        decoy.mkdir(parents=True)
        for name, body in DECOY_FILES.items():
            (decoy / name).write_text(json.dumps(body))
        (decoy / "canary.txt").write_text("canary")
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CHILD_MODE": mode,
            "DECOY_DIR": str(decoy.resolve()),
            "HERMES_HOME": str(home),
            "JARVIS_APP_ROOT": str(APP_ROOT),
            "JARVIS_SKILLS_ROOT": SKILLS_ROOT,
            "JARVIS_IDENTITY_CONFIG": str(IDENTITY),
            "JARVIS_ACCOUNTS_DB": str(home / "state" / "jarvis_accounts.db"),
            "JARVIS_THREADS_DB": str(home / "state" / "jarvis_threads.db"),
            "JARVIS_ACTIVITY_FEED": str(home / "state" / "activity_feed.jsonl"),
            "JARVIS_LEADS_SHEET_ID": "decoy-sheet-id",
            "APP_GOOGLE_TOKEN_KEY": Fernet.generate_key().decode(),
            "APP_GOOGLE_OAUTH_MOCK": "1",
            "JARVIS_CREDENTIALS_DIR": str(decoy),
            "HERMES_CREDENTIALS_DIR": str(decoy),
            "JARVIS_GOOGLE_SA_KEY": str(decoy / "service_account.json"),
            "JARVIS_GOOGLE_TOKEN": str(decoy / "google_token.json"),
            "GOOGLE_APPLICATION_CREDENTIALS": str(decoy / "adc.json"),
        }
        if os.environ.get("JARVIS_RUNTIME_CONFIG"):
            env["JARVIS_RUNTIME_CONFIG"] = os.environ["JARVIS_RUNTIME_CONFIG"]
        proc = subprocess.run([sys.executable, "-c", NO_FALLBACK_CHILD],
                              capture_output=True, text=True, env=env, timeout=120)
        line = next((ln for ln in proc.stdout.splitlines()
                     if ln.startswith("CHILD_RESULT ")), "")
        if not line:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
            return {"error": f"exit={proc.returncode} {' | '.join(tail)}"}
        return json.loads(line[len("CHILD_RESULT "):])
    finally:
        shutil.rmtree(box, ignore_errors=True)


def curl(path, jar=None, method="GET", body=None, headers_path=None, form=None):
    out = SCR / "body"
    hdr = headers_path or (SCR / "hdr")
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
    if form is not None:
        cmd += ["-H", "Content-Type: application/x-www-form-urlencoded"]
        for k, v in form.items():
            cmd += ["--data-urlencode", f"{k}={v}"]
    elif body is not None:
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


def snap_connection():
    """WAL-inclusive snapshot. Returns (tables, rowcount, public_row). Never cipher."""
    src, dst = ACCOUNTS, SCR / "accounts-snap.db"
    con_src = sqlite3.connect(str(src))
    con_dst = sqlite3.connect(str(dst))
    con_src.backup(con_dst)
    con_dst.close()
    con_src.close()
    con = sqlite3.connect(str(dst))
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]
    n = 0
    public = None
    if "google_connection" in tables:
        n = con.execute("SELECT COUNT(*) FROM google_connection").fetchone()[0]
        row = con.execute(
            "SELECT status, google_email, length(token_cipher) FROM google_connection WHERE id=1"
        ).fetchone()
        if row:
            public = {
                "status": row[0],
                "google_email": row[1],
                "cipher_bytes": row[2],
            }
    con.close()
    return tables, n, public


def tokenish_keys(obj, acc=None):
    if acc is None:
        acc = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kn = str(k).lower().replace("-", "_")
            if kn in ("token", "refresh", "refresh_token", "access_token",
                      "client_secret", "token_cipher", "cipher") or kn.endswith("_token"):
                acc.append(k)
            tokenish_keys(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            tokenish_keys(item, acc)
    return acc


def blob_values(obj, acc=None):
    if acc is None:
        acc = []
    if isinstance(obj, dict):
        for v in obj.values():
            blob_values(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            blob_values(item, acc)
    elif isinstance(obj, str) and BLOB_RE.match(obj):
        acc.append(True)
    return acc


def jar_without_session(src, dst):
    """Keep oauth state; drop jarvis_session, including #HttpOnly_ rows.

    curl writes HttpOnly cookies as '#HttpOnly_<host>\\t...\\tname\\tvalue'.
    Treating those lines as comments would leave the staff session in the jar
    and the no-cookie canary would be a false green (or a false leak).
    """
    lines = pathlib.Path(src).read_text().splitlines()
    out = []
    for line in lines:
        stripped = line.lstrip()
        http_only = stripped.startswith("#HttpOnly_")
        if not line.strip() or (stripped.startswith("#") and not http_only):
            out.append(line)
            continue
        raw = stripped[len("#HttpOnly_"):] if http_only else stripped
        parts = raw.split("\t")
        if len(parts) >= 6 and parts[5] == "jarvis_session":
            continue
        out.append(line)
    pathlib.Path(dst).write_text("\n".join(out) + ("\n" if out else ""))


def fn_body(name):
    """Source of one top-level def in app/google_oauth.py. No import of app."""
    text = APP_SRC.read_text()
    needle = f"def {name}("
    start = text.find(needle)
    if start < 0:
        return ""
    nxt = text.find("\ndef ", start + len(needle))
    return text[start:nxt] if nxt >= 0 else text[start:]


def mock_connect_flow(jar, email, grant="full"):
    code, _, heads = curl("/api/google/connect/start", jar)
    loc = location(heads)
    if code != 302 or not loc:
        return code, None, heads, loc, ""
    parsed = urlparse(loc)
    q = parse_qs(parsed.query)
    q["email"] = [email]
    q["grant"] = [grant]
    if "purpose" not in q:
        q["purpose"] = ["connect"]
    authz = urlunparse(parsed._replace(query=urlencode(q, doseq=True)))
    code, _, heads = curl(authz, jar)
    cb = location(heads)
    return code, cb, heads, loc, authz


def absence(label):
    print(f"\n── absence ({label}) ─────────────────────────────────")
    PINS = pins()
    jar_pin = str(SCR / "dana-pin.jar")
    pathlib.Path(jar_pin).unlink(missing_ok=True)
    code, d, _ = curl("/api/login", jar_pin, "POST",
                      {"person_key": "dana", "pin": PINS["dana"]})
    ck("dana PIN login still 200, role=owner",
       code == 200 and d.get("role") == "owner" and d.get("person_key") == "dana",
       f"HTTP {code}")

    code, d, _ = curl("/api/login", str(SCR / "bad-pin.jar"), "POST",
                      {"person_key": "dana", "pin": "zzzz-zzzz-zzzz"})
    ck("wrong PIN still 401 with the generic message",
       code == 401 and isinstance(d, dict) and d.get("detail") == GENERIC,
       f"HTTP {code} {d}")

    jar_op = str(SCR / "op.jar")
    pathlib.Path(jar_op).unlink(missing_ok=True)
    code, d, _ = curl("/api/login", jar_op, "POST", {"passphrase": passphrase()})
    ck("operator passphrase still person_key=null, role=operator",
       code == 200 and d.get("person_key") is None and d.get("role") == "operator",
       f"HTTP {code}")

    code, d, _ = curl("/api/people")
    people = (d or {}).get("people") if isinstance(d, dict) else None
    blob = json.dumps(d) if not isinstance(d, str) else d
    ck("GET /api/people has no @ (no emails)",
       code == 200 and "@" not in blob, blob[:120])
    return PINS, jar_pin, jar_op


print("\n── class: connect email is Gmail profile, not id_token ──")
ex = fn_body("_exchange_google_connect")
ck("connect exchange does not read email from an id_token",
   ".id_token" not in ex
   and "verify_oauth2_token" not in ex
   and "_gmail_profile_email" in ex)
ck("Gmail profile parser uses emailAddress (users.getProfile shape)",
   "emailAddress" in fn_body("_gmail_profile_email"))
cb = fn_body("_callback_connect")
ck("connect callback uses the same exchange for mock and real",
   "_exchange_google_connect" in cb and "_exchange_mock" not in cb)
authz_src = fn_body("mock_authorize")
ck("connect mock authorize plants Gmail profile emailAddress, not an id_token",
   "_mock_gmail_profiles" in authz_src
   and "emailAddress" in authz_src
   and ".id_token" not in authz_src)
tok_fn = fn_body("mock_token_response")
ck("mock_token_response never emits an id_token field",
   '["id_token"]' not in tok_fn and '"id_token":' not in tok_fn and "'id_token':" not in tok_fn)


print("\n── no fallback: no stored connection, decoys planted ────")
r = run_no_fallback_child("empty")
print(f"  google_creds under test: {r.get('module', r.get('error'))}")
ck("audit-hook canary: a deliberate decoy open is recorded",
   r.get("canary_seen") is True, r.get("error", ""))
cr = r.get("credentials") or {}
ck("credentials() raises GoogleNotConnected",
   cr.get("raised") == "GoogleNotConnected", f"raised={cr.get('raised')}")
ck("credentials() error says Reconnect Google",
   RECONNECT in (cr.get("msg") or ""), (cr.get("msg") or "")[:120])
gm = r.get("gmail") or {}
ck("gmail() raises GoogleNotConnected with the reconnect message",
   gm.get("raised") == "GoogleNotConnected" and RECONNECT in (gm.get("msg") or ""),
   f"raised={gm.get('raised')}")
ld = r.get("leads") or {}
ck("leads.load() surfaces the reconnect message",
   ld.get("raised") == "LeadsUnavailable" and RECONNECT in (ld.get("msg") or ""),
   f"raised={ld.get('raised')} msg={(ld.get('msg') or '')[:80]}")
ck("source() is not_connected",
   r.get("source") == "not_connected", f"source={r.get('source')}")
ck("no decoy credential file was opened or listed",
   "touched" in r and r["touched"] == [], f"touched={r.get('touched')}")
ck("google_creds_not_connected 0→1 for one credentials() call",
   r.get("counter_before") == 0 and r.get("counter_after_credentials") == 1,
   f"{r.get('counter_before')}→{r.get('counter_after_credentials')}")
ck("refusal is logged (announced, not silent)",
   NOT_CONNECTED_LOG in (r.get("log") or ""),
   "logged" if NOT_CONNECTED_LOG in (r.get("log") or "") else "log line missing")

print("\n── no fallback companion: stored connection, decoys planted ──")
r = run_no_fallback_child("connected")
ck("audit-hook canary fires in the connected child too",
   r.get("canary_seen") is True, r.get("error", ""))
cr = r.get("credentials") or {}
ck("credentials() returns credentials from the tenant connection",
   cr.get("raised") is None and cr.get("returned") is True,
   f"raised={cr.get('raised')} {(cr.get('msg') or '')[:120]}")
ck("source() is tenant_connection",
   r.get("source") == "tenant_connection", f"source={r.get('source')}")
ck("connected path opened no decoy credential file",
   "touched" in r and r["touched"] == [], f"touched={r.get('touched')}")
ck("connected path did not bump google_creds_not_connected",
   r.get("counter_before") == 0 and r.get("counter_after_credentials") == 0,
   f"{r.get('counter_before')}→{r.get('counter_after_credentials')}")


PINS, jar_pin, jar_op = absence("PIN and operator still exist")

print("\n── ledger: google_connection table ──────────────────────")
tables, n0, public0 = snap_connection()
ck("google_connection table exists",
   "google_connection" in tables, "present" if "google_connection" in tables else "missing")

# Leave any leftover row from a prior run at 0.
code, _, _ = curl("/api/google/connection/disconnect", jar_pin, "POST")
tables, n_clean, _ = snap_connection()
print(f"  ledger after cleanup disconnect: rows={n_clean} (was {n0})")
ck("cleanup leaves google_connection empty",
   n_clean == 0, f"rows={n_clean}")

print("\n── reconnect: tenant with no stored credentials ─────────")
c0 = counter(COUNTER_NOT_CONNECTED)
code, health, _ = curl("/api/health", jar_pin)
c1 = counter(COUNTER_NOT_CONNECTED)
sh = sheets_check(health)
ck("/api/health is 503 fail and sheets source=not_connected with 0 rows",
   code == 503 and isinstance(health, dict) and health.get("status") == "fail"
   and sh.get("source") == "not_connected",
   f"HTTP {code} status={health.get('status') if isinstance(health, dict) else None} "
   f"source={sh.get('source')}")
ck("/api/health sheets is fail with reconnect_required",
   sh.get("status") == "fail" and sh.get("reconnect_required") is True,
   f"status={sh.get('status')} reconnect_required={sh.get('reconnect_required')}")
ck("/api/health sheets detail carries the reconnect message",
   RECONNECT in (sh.get("detail") or ""), (sh.get("detail") or "")[:120])
ck("google_creds_not_connected moved on the refused health read",
   c1 >= c0 + 1, f"{c0}→{c1}")

print("\n── presence: connect start role gates ───────────────────")
code, _, heads = curl("/api/google/connect/start", jar_pin)
loc = location(heads)
ck("connect start as dana (owner) is 302",
   code == 302, f"HTTP {code}")
ck("connect start Location is the local mock with purpose=connect",
   code == 302
   and "/api/auth/google/mock/authorize" in loc
   and "purpose=connect" in loc,
   loc)

jar_omar = str(SCR / "omar.jar")
pathlib.Path(jar_omar).unlink(missing_ok=True)
code, _, _ = curl("/api/login", jar_omar, "POST",
                  {"person_key": "omar", "pin": PINS["omar"]})
code, d, _ = curl("/api/google/connect/start", jar_omar)
ck("connect start as omar (member) is 403",
   code == 403, f"HTTP {code}")

code, d, _ = curl("/api/google/connect/start", jar_op)
ck("connect start as operator is 403",
   code == 403, f"HTTP {code}")

code, d, _ = curl("/api/google/connect/start")
ck("connect start logged-out is 401",
   code == 401, f"HTTP {code}")

print("\n── class: mock token JSON has no id_token; profile still works ──")
jar_tok = str(SCR / "token-canary.jar")
pathlib.Path(jar_tok).unlink(missing_ok=True)
code, _, _ = curl("/api/login", jar_tok, "POST",
                  {"person_key": "dana", "pin": PINS["dana"]})
code, cb, _, _, _ = mock_connect_flow(jar_tok, MAILBOX, grant="full")
ck("token-canary mock authorize redirects to callback",
   code == 302 and cb and "/oauth/callback" in cb, f"HTTP {code}")
auth_code = (parse_qs(urlparse(cb).query).get("code") or [""])[0]
code, tok, _ = curl(
    "/api/auth/google/mock/token", jar_tok, "POST", form={"code": auth_code},
)
ck("POST mock/token on a connect grant is 200 JSON",
   code == 200 and isinstance(tok, dict), f"HTTP {code}")
ck("connect mock token response has no id_token",
   isinstance(tok, dict) and "id_token" not in tok,
   f"keys={sorted(tok) if isinstance(tok, dict) else type(tok).__name__}")
ck("connect mock token response has no email (real Google omits it)",
   isinstance(tok, dict) and "email" not in tok,
   f"keys={sorted(tok) if isinstance(tok, dict) else type(tok).__name__}")
ck("connect mock token still returns access_token for users.getProfile",
   isinstance(tok, dict) and bool(tok.get("access_token")))

print("\n── presence: partial 4-of-5 grant writes nothing ────────")
_, n_before_partial, _ = snap_connection()
jar_partial = str(SCR / "partial.jar")
pathlib.Path(jar_partial).unlink(missing_ok=True)
code, _, _ = curl("/api/login", jar_partial, "POST",
                  {"person_key": "dana", "pin": PINS["dana"]})
code, cb, heads, loc, _ = mock_connect_flow(jar_partial, MAILBOX, grant="partial")
ck("partial mock authorize redirects to callback",
   code == 302 and cb and "/oauth/callback" in cb, f"HTTP {code}")
code, body, _ = curl(cb, jar_partial)
body_s = body if isinstance(body, str) else json.dumps(body)
ck("partial grant is a visible rejection",
   PARTIAL_MSG in body_s.lower(),
   f"HTTP {code} contains rejection" if PARTIAL_MSG in body_s.lower() else f"HTTP {code}")
_, n_after_partial, _ = snap_connection()
ck("partial 4-of-5 grant leaves google_connection row count unchanged at 0",
   n_before_partial == 0 and n_after_partial == 0,
   f"{n_before_partial}→{n_after_partial}")

print("\n── presence: connect callback without staff cookie ──────")
jar_nostaff_src = str(SCR / "nostaff-src.jar")
pathlib.Path(jar_nostaff_src).unlink(missing_ok=True)
code, _, _ = curl("/api/login", jar_nostaff_src, "POST",
                  {"person_key": "dana", "pin": PINS["dana"]})
code, cb, _, _, _ = mock_connect_flow(jar_nostaff_src, MAILBOX, grant="full")
jar_nostaff = str(SCR / "nostaff.jar")
jar_without_session(jar_nostaff_src, jar_nostaff)
_, n_before_ns, _ = snap_connection()
code, body, _ = curl(cb, jar_nostaff)
body_s = body if isinstance(body, str) else json.dumps(body)
ck("connect callback without staff cookie stores nothing",
   code in (401, 403) and SIGNIN_AGAIN in body_s.lower(),
   f"HTTP {code}")
_, n_after_ns, _ = snap_connection()
ck("no-staff-cookie callback did not write a row",
   n_before_ns == n_after_ns == 0,
   f"{n_before_ns}→{n_after_ns}")

print("\n── presence: wrong mailbox is refused (mismatch) ────────")
log_path = pathlib.Path(APP_LOG) if APP_LOG else None
log_start = log_path.stat().st_size if log_path and log_path.exists() else 0
_, n_before_mm, _ = snap_connection()
jar_mm = str(SCR / "mismatch.jar")
pathlib.Path(jar_mm).unlink(missing_ok=True)
code, _, _ = curl("/api/login", jar_mm, "POST",
                  {"person_key": "dana", "pin": PINS["dana"]})
code, cb_mm, _, _, _ = mock_connect_flow(jar_mm, WRONG_MAILBOX, grant="full")
ck("mismatch mock authorize redirects to callback",
   code == 302 and cb_mm and "/oauth/callback" in cb_mm, f"HTTP {code}")
code, body, _ = curl(cb_mm, jar_mm) if cb_mm else (0, "", "")
body_s = body if isinstance(body, str) else json.dumps(body)
ck("mismatch callback is 403 naming both addresses",
   code == 403 and WRONG_MAILBOX in body_s and MAILBOX in body_s,
   f"HTTP {code}")
_, n_after_mm, _ = snap_connection()
ck("mismatch writes 0 google_connection rows",
   n_before_mm == 0 and n_after_mm == 0, f"{n_before_mm}→{n_after_mm}")
new_log = ""
if log_path and log_path.exists():
    with open(log_path, "r", errors="replace") as fh:
        fh.seek(log_start)
        new_log = fh.read()
mm_lines = [ln for ln in new_log.splitlines()
            if "google connect refused:" in ln and "reason=mismatch" in ln]
ck("app log has one reason=mismatch refusal for this run",
   len(mm_lines) == 1,
   f"lines={len(mm_lines)}" if log_path else "VERIFY_APP_LOG not set")
TOKEN_MARKS = ("mock-access-", "mock-refresh-", "access_token", "refresh_token",
               "ya29.", "1//0")
ck("mismatch log line contains no token",
   len(mm_lines) == 1 and not any(m in mm_lines[0] for m in TOKEN_MARKS),
   "clean" if mm_lines and not any(m in mm_lines[0] for m in TOKEN_MARKS)
   else "missing or token-like text present")
ck("mismatch log line names the wrong and expected address",
   len(mm_lines) == 1
   and f"google_email={WRONG_MAILBOX}" in mm_lines[0]
   and f"expected={MAILBOX}" in mm_lines[0])

print("\n── presence: owner mock-connect 0→1 ─────────────────────")
_, n_before, _ = snap_connection()
jar_g = str(SCR / "dana-connect.jar")
pathlib.Path(jar_g).unlink(missing_ok=True)
code, _, _ = curl("/api/login", jar_g, "POST",
                  {"person_key": "dana", "pin": PINS["dana"]})
code, cb, _, loc, _ = mock_connect_flow(jar_g, MAILBOX, grant="full")
ck("full mock authorize redirects to callback",
   code == 302 and cb and "/oauth/callback" in cb, f"HTTP {code}")
code, _, heads = curl(cb, jar_g)
ck("full connect callback redirects home", code == 302, f"HTTP {code}")
_, n_after, public = snap_connection()
ck("owner mock-connect writes one google_connection row",
   n_before == 0 and n_after == 1,
   f"{n_before}→{n_after}")
ck("row status=connected and google_email is set",
   public is not None
   and public.get("status") == "connected"
   and public.get("google_email") == MAILBOX
   and (public.get("cipher_bytes") or 0) > 0,
   f"status={public.get('status') if public else None} "
   f"email_set={bool(public and public.get('google_email'))} "
   f"cipher_bytes={public.get('cipher_bytes') if public else None}")

print("\n── presence: status JSON has no secrets ─────────────────")
code, status_body, _ = curl("/api/google/connection", jar_g)
ck("GET /api/google/connection as dana is 200 connected",
   code == 200 and isinstance(status_body, dict)
   and status_body.get("status") == "connected"
   and status_body.get("google_email") == MAILBOX,
   f"HTTP {code} keys={sorted(status_body) if isinstance(status_body, dict) else type(status_body).__name__}")
bad = tokenish_keys(status_body) if isinstance(status_body, dict) else ["non-object"]
blobs = blob_values(status_body) if isinstance(status_body, dict) else [True]
ck("status JSON contains no keys named token/refresh/client_secret/cipher",
   bad == [],
   "none" if not bad else f"bad keys={bad}")
ck("status JSON contains no Fernet/base64 blobs",
   blobs == [],
   "none" if not blobs else "a value looked like a cipher blob")
allowed = {"status", "google_email", "connected_at", "refreshed_at"}
extra_keys = sorted(set(status_body) - allowed) if isinstance(status_body, dict) else ["non-object"]
ck("status JSON only has status/google_email/connected_at/refreshed_at",
   extra_keys == [],
   f"keys={sorted(status_body) if isinstance(status_body, dict) else extra_keys}")

print("\n── presence: /api/health has no refresh token ───────────")
code, health, _ = curl("/api/health", jar_g)
health_keys = tokenish_keys(health) if isinstance(health, dict) else ["non-object"]
health_blobs = blob_values(health) if isinstance(health, dict) else [True]
# Do not dump the health body — it may be large and must never carry a token.
ck("GET /api/health contains no refresh/token/cipher keys",
   isinstance(health, dict) and health_keys == [],
   "none" if not health_keys else f"bad keys={health_keys}")
ck("GET /api/health contains no Fernet blobs",
   isinstance(health, dict) and health_blobs == [],
   "none" if not health_blobs else "a value looked like a cipher blob")
gc = (health or {}).get("google_connection") if isinstance(health, dict) else None
ck("GET /api/health reports google_connection status=connected",
   isinstance(gc, dict) and gc.get("status") == "connected",
   f"HTTP {code} status={gc.get('status') if isinstance(gc, dict) else None}")
c0 = counter(COUNTER_NOT_CONNECTED)
code, health, _ = curl("/api/health", jar_g)
c1 = counter(COUNTER_NOT_CONNECTED)
sh = sheets_check(health)
ck("/api/health sheets source=tenant_connection after connect",
   sh.get("source") == "tenant_connection" and not sh.get("reconnect_required"),
   f"source={sh.get('source')} status={sh.get('status')}")
ck("connected health read did not bump google_creds_not_connected",
   c1 == c0, f"{c0}→{c1}")

print("\n── presence: replay of the connect callback ─────────────")
jar_replay = str(SCR / "connect-replay.jar")
pathlib.Path(jar_replay).unlink(missing_ok=True)
code, d, _ = curl(cb, jar_replay)
_, n_replay, _ = snap_connection()
ck("replaying the connect callback is 401/400",
   code in (400, 401), f"HTTP {code}")
ck("replay did not add a google_connection row",
   n_replay == 1, f"rows={n_replay}")

print("\n── presence: disconnect 1→0 ─────────────────────────────")
_, n_pre_disc, _ = snap_connection()
code, d, _ = curl("/api/google/connection/disconnect", jar_g, "POST")
_, n_post_disc, public_disc = snap_connection()
ck("disconnect returns ok",
   code == 200 and isinstance(d, dict) and d.get("ok") is True,
   f"HTTP {code}")
ck("disconnect google_connection row count 1→0",
   n_pre_disc == 1 and n_post_disc == 0,
   f"{n_pre_disc}→{n_post_disc}")
code, st, _ = curl("/api/google/connection", jar_g)
ck("status after disconnect is disconnected",
   code == 200 and isinstance(st, dict) and st.get("status") == "disconnected"
   and st.get("google_email") is None,
   f"HTTP {code} status={st.get('status') if isinstance(st, dict) else None}")

absence("PIN and operator rerun")

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
