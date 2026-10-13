#!/usr/bin/env python3
"""Slice 3 — staff Google login canaries against the verify sandbox (:8711) only.

Presence: start redirects + state cookie; forged state 401/400 no session;
replay rejected; mock login as dana@testco.example.com is dana/owner;
require_bodies parity vs PIN; disabled-dana mock login 401 generic then re-enable.

Absence: dana PIN 200; wrong PIN 401 same message; operator person_key=null;
GET /api/people has no emails.

Never :8700. Never prints pins, passphrases, or env values.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

BASE = os.environ.get("AUTH_BASE", "http://127.0.0.1:8711").rstrip("/")
if BASE.endswith(":8700"):
    sys.exit("REFUSING: :8700 is the live tenant.")

HOME = pathlib.Path("/opt/jarvis-verify-fixture/hermes")
ACCOUNTS = HOME / "state" / "jarvis_accounts.db"
PINS_PATH = pathlib.Path("/opt/jarvis-verify-fixture/SEEDED_PINS.json")
SCR = pathlib.Path("/tmp/verify-auth-google")
SCR.mkdir(parents=True, exist_ok=True)
GENERIC = "That PIN is not right."
DANA_EMAIL = "dana@testco.example.com"

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
    for line in (HOME / ".env").read_text().splitlines():
        if line.strip().startswith("APP_PASSPHRASE="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("REFUSING: APP_PASSPHRASE missing (presence only; value not printed)")


def curl(path, jar=None, method="GET", body=None, follow=False, headers_path=None):
    out = SCR / "body"
    hdr = headers_path or (SCR / "hdr")
    cmd = [
        "curl", "-s", "-D", str(hdr), "-o", str(out), "-w", "%{http_code}",
        "-X", method,
    ]
    if follow:
        cmd += ["-L", "--max-redirs", "5"]
    else:
        cmd += ["--max-redirs", "0"]
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


def cookie_set(heads, name):
    """True if a non-empty, non-deleted cookie named `name` was issued."""
    for line in heads.splitlines():
        if not line.lower().startswith("set-cookie:"):
            continue
        rest = line.split(":", 1)[1].strip()
        if not rest.lower().startswith(name.lower() + "="):
            continue
        pair = rest.split(";", 1)[0]
        val = pair.split("=", 1)[1] if "=" in pair else ""
        attrs = rest.lower()
        if "max-age=0" in attrs:
            continue
        if val and val.lower() not in ("deleted", '""', "''"):
            return True
    return False


def location(heads):
    for line in heads.splitlines():
        if line.lower().startswith("location:"):
            loc = line.split(":", 1)[1].strip()
            if loc.startswith("/"):
                return BASE + loc
            return loc
    return ""


def pragma_email():
    src, dst = ACCOUNTS, SCR / "accounts-snap.db"
    con_src = sqlite3.connect(str(src))
    con_dst = sqlite3.connect(str(dst))
    con_src.backup(con_dst)
    con_dst.close()
    con_src.close()
    con = sqlite3.connect(str(dst))
    cols = [r[1] for r in con.execute("PRAGMA table_info(accounts)")]
    n_email = con.execute(
        "SELECT COUNT(*) FROM accounts WHERE email IS NOT NULL"
    ).fetchone()[0] if "email" in cols else 0
    con.close()
    return cols, n_email


print("── ledger: accounts.email (slice 2 lazy migration) ──────────")
cols, n_email = pragma_email()
print(f"  columns={cols}")
ck("email column is present on testco accounts", "email" in cols, str(cols))
ck("at least one account has an email after backfill", n_email >= 1, str(n_email))

print("\n── absence (PIN and operator still exist) ──────────────────")
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
ck("GET /api/people has no role or disabled fields",
   code == 200 and people is not None
   and all("role" not in p and "disabled" not in p and "email" not in p for p in people),
   str(people[:1] if people else people))

print("\n── presence: start redirects + state cookie ───────────────")
jar_start = str(SCR / "start.jar")
pathlib.Path(jar_start).unlink(missing_ok=True)
code, _, heads = curl("/api/auth/google/start", jar_start)
loc = location(heads)
ck("GET /api/auth/google/start redirects (302)", code == 302, f"HTTP {code}")
ck("Location is the local mock, not Google mailbox scopes",
   "/api/auth/google/mock/authorize" in loc
   and "gmail" not in loc.lower()
   and "calendar" not in loc.lower(),
   loc)
ck("start sets a signed state cookie",
   cookie_set(heads, "jarvis_oauth_state"),
   heads.split("set-cookie:", 1)[-1][:40] if "set-cookie:" in heads.lower() else "none")

os.environ.setdefault("HERMES_HOME", str(HOME))
os.environ.setdefault("JARVIS_ACCOUNTS_DB", str(ACCOUNTS))
os.environ.setdefault("JARVIS_SKILLS_ROOT",
                      "/opt/jarvis-verify-fixture/skills/openclaw-imports")
os.environ.setdefault("JARVIS_IDENTITY_CONFIG",
                      "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json")
sys.path.insert(0, "/root/jarvis-app")
from app import google_oauth, config as app_config  # noqa: E402


def _state_cookie_value(jar_path):
    """Read jarvis_oauth_state from a curl jar. HttpOnly rows included."""
    for line in pathlib.Path(jar_path).read_text().splitlines():
        stripped = line.lstrip()
        http_only = stripped.startswith("#HttpOnly_")
        if not line.strip() or (stripped.startswith("#") and not http_only):
            continue
        raw = stripped[len("#HttpOnly_"):] if http_only else stripped
        parts = raw.split("\t")
        if len(parts) >= 7 and parts[5] == "jarvis_oauth_state":
            return parts[6]
    return None


_state_raw = _state_cookie_value(jar_start)
_state_keys = []
_has_verifier = False
if _state_raw:
    try:
        _state_payload = google_oauth._state_serializer().loads(
            _state_raw, max_age=app_config.OAUTH_STATE_MAX_AGE,
        )
        _state_keys = sorted(_state_payload)
        _has_verifier = bool(
            isinstance(_state_payload.get("code_verifier"), str)
            and _state_payload.get("code_verifier")
        )
    except Exception as exc:
        _state_keys = [type(exc).__name__]
if "/api/auth/google/mock/authorize" in loc:
    ck("mock start state cookie has nonce/purpose/exp and no code_verifier",
       set(_state_keys) >= {"exp", "nonce", "purpose"}
       and "code_verifier" not in _state_keys,
       f"keys={_state_keys}")
else:
    ck("real-mode start state cookie carries code_verifier",
       set(_state_keys) >= {"code_verifier", "exp", "nonce", "purpose"}
       and _has_verifier,
       f"keys={_state_keys}")

print("\n── presence: forged state ─────────────────────────────────")
jar_forge = str(SCR / "forge.jar")
pathlib.Path(jar_forge).unlink(missing_ok=True)
code, d, heads = curl("/oauth/callback?state=forged&code=x", jar_forge)
ck("forged state is 401/400", code in (400, 401), f"HTTP {code} {d}")
forged_session = cookie_set(heads, "jarvis_session")
ck("forged state issues no session cookie",
   not forged_session,
   "no jarvis_session Set-Cookie" if not forged_session else "session cookie was set")

print("\n── presence: mock Google login as dana ────────────────────")
jar_g = str(SCR / "dana-google.jar")
pathlib.Path(jar_g).unlink(missing_ok=True)
code, _, heads = curl("/api/auth/google/start", jar_g)
loc = location(heads)
parsed = urlparse(loc)
q = parse_qs(parsed.query)
q["email"] = [DANA_EMAIL]
authz = urlunparse(parsed._replace(query=urlencode(q, doseq=True)))
code, _, heads = curl(authz, jar_g)
ck("mock authorize redirects to /oauth/callback",
   code == 302 and "/oauth/callback" in location(heads), f"HTTP {code} {location(heads)}")
cb = location(heads)
code, _, heads = curl(cb, jar_g)
ck("callback redirects home after mock login", code == 302, f"HTTP {code}")
ck("callback issued a session cookie", cookie_set(heads, "jarvis_session"))
code, sess, _ = curl("/api/session", jar_g)
ck("mock login as dana@testco.example.com is person_key=dana role=owner",
   code == 200 and sess.get("person_key") == "dana" and sess.get("role") == "owner",
   f"HTTP {code} {sess}")

print("\n── presence: require_bodies parity vs PIN ─────────────────")
code_g, hist_g, _ = curl("/api/chat/history?thread_id=direct", jar_g)
code_p, hist_p, _ = curl("/api/chat/history?thread_id=direct", jar_pin)
ck("Google session and PIN session both reach require_bodies (200)",
   code_g == 200 and code_p == 200, f"g={code_g} pin={code_p}")
ck("require_bodies response is identical to the dana PIN session",
   hist_g == hist_p,
   "identical" if hist_g == hist_p else "bodies differed")

print("\n── presence: replay rejected ──────────────────────────────")
jar_replay = str(SCR / "replay.jar")
pathlib.Path(jar_replay).unlink(missing_ok=True)
code, d, heads = curl(cb, jar_replay)
ck("replaying the callback URL is 401/400", code in (400, 401), f"HTTP {code} {d}")
ck("replay issues no session cookie",
   not cookie_set(heads, "jarvis_session"))

print("\n── presence: disabled-dana mock login 401, then re-enable ─")
os.environ.setdefault("HERMES_HOME", str(HOME))
os.environ.setdefault("JARVIS_ACCOUNTS_DB", str(ACCOUNTS))
os.environ.setdefault("JARVIS_SKILLS_ROOT",
                      "/opt/jarvis-verify-fixture/skills/openclaw-imports")
os.environ.setdefault("JARVIS_IDENTITY_CONFIG",
                      "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json")
sys.path.insert(0, "/root/jarvis-app")
from app import accounts  # noqa: E402

priya_was = (accounts.get("priya") or {}).get("role")
disabled_ok = False
try:
    if (accounts.get("dana") or {}).get("role") == "owner":
        accounts.set_role("priya", "owner")
    accounts.set_disabled("dana", True)
    jar_dis = str(SCR / "dana-disabled.jar")
    pathlib.Path(jar_dis).unlink(missing_ok=True)
    code, _, heads = curl("/api/auth/google/start", jar_dis)
    loc = location(heads)
    parsed = urlparse(loc)
    q = parse_qs(parsed.query)
    q["email"] = [DANA_EMAIL]
    authz = urlunparse(parsed._replace(query=urlencode(q, doseq=True)))
    code, _, heads = curl(authz, jar_dis)
    cb_dis = location(heads)
    code, d, heads = curl(cb_dis, jar_dis)
    ck("disabled-dana mock Google login is 401 with the generic message",
       code == 401 and isinstance(d, dict) and d.get("detail") == GENERIC,
       f"HTTP {code} {d}")
    ck("disabled-dana issues no session cookie",
       not cookie_set(heads, "jarvis_session"))
    disabled_ok = True
finally:
    try:
        accounts.set_disabled("dana", False)
    except Exception:
        pass
    try:
        if priya_was:
            accounts.set_role("priya", priya_was)
    except Exception as exc:
        print(f"  WARN  could not restore priya role: {type(exc).__name__}")

dana_now = accounts.get("dana")
ck("dana is re-enabled after the canary",
   dana_now is not None and not dana_now.get("disabled"))

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
