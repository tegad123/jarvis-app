#!/usr/bin/env python3
"""Companion mutate for slice 3. If this cannot fail the Google path, verify is a false green.

Remap dana's accounts.email; mock Google login as dana@testco.example.com MUST
fail; PIN login MUST still pass; restore the address after.

Against http://127.0.0.1:8711 only. Never :8700.
"""
import json
import os
import pathlib
import subprocess
import sys
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

BASE = os.environ.get("AUTH_BASE", "http://127.0.0.1:8711").rstrip("/")
if BASE.endswith(":8700"):
    sys.exit("REFUSING: :8700 is the live tenant.")

HOME = pathlib.Path("/opt/jarvis-verify-fixture/hermes")
ACCOUNTS = HOME / "state" / "jarvis_accounts.db"
PINS_PATH = pathlib.Path("/opt/jarvis-verify-fixture/SEEDED_PINS.json")
SCR = pathlib.Path("/tmp/mutate-auth-google")
SCR.mkdir(parents=True, exist_ok=True)
GENERIC = "That PIN is not right."
REAL = "dana@testco.example.com"
OTHER = "remap-dana@testco.example.com"

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


def cookie_set(heads, name):
    for line in heads.splitlines():
        if not line.lower().startswith("set-cookie:"):
            continue
        rest = line.split(":", 1)[1].strip()
        if not rest.lower().startswith(name.lower() + "="):
            continue
        pair = rest.split(";", 1)[0]
        val = pair.split("=", 1)[1] if "=" in pair else ""
        if "max-age=0" in rest.lower():
            continue
        if val and val.lower() not in ("deleted", '""'):
            return True
    return False


def mock_google(email, jar):
    pathlib.Path(jar).unlink(missing_ok=True)
    code, _, heads = curl("/api/auth/google/start", jar)
    loc = location(heads)
    parsed = urlparse(loc)
    q = parse_qs(parsed.query)
    q["email"] = [email]
    authz = urlunparse(parsed._replace(query=urlencode(q, doseq=True)))
    code, _, heads = curl(authz, jar)
    cb = location(heads)
    return curl(cb, jar)


os.environ.setdefault("HERMES_HOME", str(HOME))
os.environ.setdefault("JARVIS_ACCOUNTS_DB", str(ACCOUNTS))
os.environ.setdefault("JARVIS_SKILLS_ROOT",
                      "/opt/jarvis-verify-fixture/skills/openclaw-imports")
os.environ.setdefault("JARVIS_IDENTITY_CONFIG",
                      "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json")
sys.path.insert(0, "/root/jarvis-app")
from app import accounts  # noqa: E402

original = (accounts.get("dana") or {}).get("email")
ck("dana currently has a login email", bool(original), "no email on dana")

restored = False
try:
    accounts.set_email("dana", OTHER)
    now = (accounts.get("dana") or {}).get("email")
    ck("dana email remapped away from the real address",
       now == OTHER, now)

    jar_g = str(SCR / "google.jar")
    code, d, heads = mock_google(REAL, jar_g)
    google_failed = code in (400, 401) and not cookie_set(heads, "jarvis_session")
    ck("mock Google login as the REAL address now fails",
       google_failed, f"HTTP {code} {d}")
    if not google_failed:
        print("  FALSE GREEN: Google path still succeeded after remap — verify is untested")

    PINS = pins()
    jar_p = str(SCR / "pin.jar")
    pathlib.Path(jar_p).unlink(missing_ok=True)
    code, d, _ = curl("/api/login", jar_p, "POST",
                      {"person_key": "dana", "pin": PINS["dana"]})
    ck("PIN login still passes after the remap",
       code == 200 and d.get("person_key") == "dana", f"HTTP {code}")
finally:
    accounts.set_email("dana", original or REAL)
    restored = (accounts.get("dana") or {}).get("email") == (original or REAL)

ck("dana email restored", restored, (accounts.get("dana") or {}).get("email"))

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
