#!/usr/bin/env python3
import sys; sys.exit("RETIRED 2026-10-13: testco is retired (docs/platform/decisions.md); this script needs its running services or tree.")  # noqa: E702
"""Companion mutate for slice 4. Corrupt the stored cipher, then prove the
read path fails LOUDLY (raised error + counter), not as silent None.

If this cannot fail build_credentials after a flipped cipher, verify is a
false green.

Against http://127.0.0.1:8710 only. Never :8700. Never prints cipher bytes,
keys, pins, or tokens.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

BASE = os.environ.get("AUTH_BASE", "http://127.0.0.1:8710").rstrip("/")
if BASE.endswith(":8700"):
    sys.exit("REFUSING: :8700 is the live tenant.")

HOME = pathlib.Path("/opt/testco/hermes")
ACCOUNTS = HOME / "state" / "jarvis_accounts.db"
PINS_PATH = pathlib.Path("/opt/testco/SEEDED_PINS.json")
SCR = pathlib.Path("/tmp/mutate-google-connection")
SCR.mkdir(parents=True, exist_ok=True)
MAILBOX = "mailbox@testco.example.com"
COUNTER = "google_connection_decrypt_failed"

ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))


def pins():
    raw = json.loads(PINS_PATH.read_text())
    if raw and isinstance(next(iter(raw.values())), dict):
        return {k: v.get("pin") for k, v in raw.items()}
    return raw


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


def snap_count():
    src, dst = ACCOUNTS, SCR / "accounts-snap.db"
    con_src = sqlite3.connect(str(src))
    con_dst = sqlite3.connect(str(dst))
    con_src.backup(con_dst)
    con_dst.close()
    con_src.close()
    con = sqlite3.connect(str(dst))
    n = con.execute("SELECT COUNT(*) FROM google_connection").fetchone()[0]
    con.close()
    return n


def counter_value():
    db = HOME / "state" / "jarvis_threads.db"
    src, dst = db, SCR / "threads-snap.db"
    con_src = sqlite3.connect(str(src))
    con_dst = sqlite3.connect(str(dst))
    con_src.backup(con_dst)
    con_dst.close()
    con_src.close()
    con = sqlite3.connect(str(dst))
    try:
        row = con.execute(
            "SELECT value FROM counters WHERE name=?", (COUNTER,)
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    con.close()
    return int(row[0]) if row else 0


def mock_connect(jar):
    code, _, heads = curl("/api/google/connect/start", jar)
    loc = location(heads)
    parsed = urlparse(loc)
    q = parse_qs(parsed.query)
    q["email"] = [MAILBOX]
    q["grant"] = ["full"]
    if "purpose" not in q:
        q["purpose"] = ["connect"]
    authz = urlunparse(parsed._replace(query=urlencode(q, doseq=True)))
    code, _, heads = curl(authz, jar)
    cb = location(heads)
    code, _, _ = curl(cb, jar)
    return code


PINS = pins()
jar = str(SCR / "dana.jar")
pathlib.Path(jar).unlink(missing_ok=True)
code, d, _ = curl("/api/login", jar, "POST",
                  {"person_key": "dana", "pin": PINS["dana"]})
ck("dana PIN login for mutate",
   code == 200 and d.get("person_key") == "dana", f"HTTP {code}")

curl("/api/google/connection/disconnect", jar, "POST")
n0 = snap_count()
code = mock_connect(jar)
n1 = snap_count()
ck("mutate planted a connected row",
   n0 == 0 and n1 == 1 and code == 302,
   f"rows {n0}→{n1} callback HTTP {code}")

before = counter_value()
# Flip one byte of the cipher in the live accounts DB. Never print it.
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

os.environ.setdefault("HERMES_HOME", str(HOME))
os.environ.setdefault("JARVIS_ACCOUNTS_DB", str(ACCOUNTS))
os.environ.setdefault("JARVIS_SKILLS_ROOT",
                      "/opt/testco/skills/openclaw-imports")
os.environ.setdefault("JARVIS_IDENTITY_CONFIG",
                      "/opt/testco/skills/openclaw-imports/_shared/identity.json")
os.environ.setdefault("JARVIS_THREADS_DB", str(HOME / "state" / "jarvis_threads.db"))
# The Fernet key lives in the testco drop-in, not in this process. Load it
# into the environment without printing the value so decrypt can fail loudly.
_dropin = pathlib.Path("/etc/systemd/system/testco-app.service.d/oauth.conf")
_loaded = False
for _line in _dropin.read_text().splitlines():
    if _line.startswith("Environment=APP_GOOGLE_TOKEN_KEY="):
        os.environ["APP_GOOGLE_TOKEN_KEY"] = _line.split("=", 2)[2]
        _loaded = True
        break
if not _loaded:
    sys.exit("REFUSING: APP_GOOGLE_TOKEN_KEY missing from drop-in (presence only)")
sys.path.insert(0, "/root/jarvis-app")
from app import google_connection  # noqa: E402

raised = None
result = "unset"
try:
    result = google_connection.build_credentials()
except google_connection.ConnectionCipherError as exc:
    raised = type(exc).__name__
except Exception as exc:
    raised = type(exc).__name__

ck("build_credentials raises on a flipped cipher (not silent None)",
   raised == "ConnectionCipherError" and result == "unset",
   f"raised={raised} result_is_none={result is None}")

after = counter_value()
ck("decrypt-failed counter incremented (announced, not silent)",
   after >= before + 1,
   f"{before}→{after}")

# Restore: drop the corrupt row so testco is not left broken.
google_connection.clear()
n2 = snap_count()
ck("corrupt row removed after the mutate",
   n2 == 0, f"rows={n2}")

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
