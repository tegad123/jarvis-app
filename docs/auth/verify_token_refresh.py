#!/usr/bin/env python3
"""Verify: a stored Google grant refreshes with the OAuth client from YAMIN_OAUTH_CLIENT.

Before the fix, build_credentials passed client_id=None and client_secret=None
outside mock mode, so google-auth refused every refresh ("The credentials do
not contain the necessary fields need to refresh the access token") and
rehearsal's connection went to needs_reauth on first use.

This runs the REAL refresh path, google_creds.credentials() -> build_credentials
-> google.oauth2.credentials.Credentials.refresh, against a local fake token
endpoint named by the client file's token_uri. Throwaway Fernet key, databases
and client file; no real Google, no secrets read.

Cases:
  1. refresh posts grant_type/client_id/client_secret/refresh_token once, the
     new access token comes back, the row is re-encrypted and needs_reauth clears
  2. no YAMIN_OAUTH_CLIENT: OAuthClientMissing, zero network calls, counter +1,
     row says oauth_client_missing
  3. client file without client_secret: the same refusal
  4. CANARY: case 1 against the pre-fix google_connection.py (git ref, default
     2b20c5d) must fail the way production did

Env: VERIFY_APP_ROOT (default: this checkout), VERIFY_SKILLS_ROOT (default
/root/jarvis-skills-platform), VERIFY_CANARY_REF (default 2b20c5d).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve()
APP = Path(os.environ.get("VERIFY_APP_ROOT") or HERE.parents[2])
SKILLS = Path(os.environ.get("VERIFY_SKILLS_ROOT") or "/root/jarvis-skills-platform")
CANARY_REF = os.environ.get("VERIFY_CANARY_REF", "2b20c5d")
PY = sys.executable

if not (SKILLS / "_shared" / "google_creds.py").is_file():
    sys.exit(f"REFUSING: no skills checkout at {SKILLS} (set VERIFY_SKILLS_ROOT)")

results = []


def ck(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))


class TokenEndpoint(BaseHTTPRequestHandler):
    posts = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        TokenEndpoint.posts.append(dict(urllib.parse.parse_qsl(body)))
        out = json.dumps({"access_token": "verify-new-access", "expires_in": 3600,
                          "token_type": "Bearer"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


SEED = r"""
import json, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, sys.argv[1])
from app import google_connection as gc
payload = {"refresh_token": "verify-refresh", "access_token": "verify-expired",
           "expiry": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
           "scopes": "https://www.googleapis.com/auth/gmail.modify"}
gc.put_connected(google_email="owner@verify.example", scopes=payload["scopes"],
                 token_cipher=gc.encrypt_tokens(payload), connected_by="owner")
gc.mark_needs_reauth("RefreshError")
print("seeded")
"""

USE = r"""
import json, sqlite3, sys, os
sys.path.insert(0, sys.argv[2] + "/_shared")
import google_creds as g
out = {}
try:
    creds = g.credentials()
    out["token"] = creds.token
except Exception as exc:
    out["raised"] = type(exc).__name__
    out["msg"] = str(exc)[:200]
c = sqlite3.connect(os.environ["JARVIS_ACCOUNTS_DB"])
row = c.execute("select status, last_error, refreshed_at from google_connection").fetchone()
out["row"] = list(row) if row else None
try:
    out["counters"] = dict(sqlite3.connect(os.environ["JARVIS_THREADS_DB"]).execute(
        "select name, value from counters").fetchall())
except Exception:
    out["counters"] = {}
if "token" in out:
    sys.path.insert(0, sys.argv[1])
    from app import google_connection as gc
    out["stored_access"] = gc.build_credentials().token
print(json.dumps(out))
"""


def run_case(app_root: Path, box: Path, client: dict | None, port: int):
    from cryptography.fernet import Fernet
    box.mkdir(parents=True)
    (box / "hermes" / "state").mkdir(parents=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(box),
           "HERMES_HOME": str(box / "hermes"), "JARVIS_APP_ROOT": str(app_root),
           "JARVIS_SKILLS_ROOT": str(SKILLS),
           "JARVIS_ACCOUNTS_DB": str(box / "accounts.db"), "JARVIS_THREADS_DB": str(box / "threads.db"),
           "APP_GOOGLE_TOKEN_KEY": Fernet.generate_key().decode(), "PYTHONDONTWRITEBYTECODE": "1"}
    if client is not None:
        (box / "client.json").write_text(json.dumps(client))
        env["YAMIN_OAUTH_CLIENT"] = str(box / "client.json")
    s = subprocess.run([PY, "-c", SEED, str(app_root)], env=env, capture_output=True, text=True, timeout=120)
    if s.returncode != 0:
        return {"seed_error": s.stderr[-600:]}, ""
    TokenEndpoint.posts.clear()
    u = subprocess.run([PY, "-c", USE, str(app_root), str(SKILLS)], env=env, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(u.stdout.strip().splitlines()[-1]), u.stderr
    except Exception:
        return {"use_error": u.stderr[-600:]}, u.stderr


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), TokenEndpoint)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    client = {"web": {"client_id": "verify-client-id.apps.googleusercontent.com",
                      "client_secret": "verify-client-secret",
                      "token_uri": f"http://127.0.0.1:{port}/token"}}
    tmp = Path(tempfile.mkdtemp(prefix="verify-refresh-"))
    try:
        print(f"── 1. refresh with the client from YAMIN_OAUTH_CLIENT ({APP})")
        out, err = run_case(APP, tmp / "ok", client, port)
        posts = list(TokenEndpoint.posts)
        ck("no seed/use error", "seed_error" not in out and "use_error" not in out, str(out)[:300])
        ck("exactly one POST to the token endpoint", len(posts) == 1, f"{len(posts)}")
        p = posts[0] if posts else {}
        ck("grant_type=refresh_token", p.get("grant_type") == "refresh_token")
        ck("client_id sent from the client file", p.get("client_id") == client["web"]["client_id"])
        ck("client_secret sent from the client file", p.get("client_secret") == client["web"]["client_secret"])
        ck("stored refresh_token sent", p.get("refresh_token") == "verify-refresh")
        ck("credentials() returns the new access token", out.get("token") == "verify-new-access", str(out.get("raised")))
        ck("row re-encrypted with the new access token", out.get("stored_access") == "verify-new-access")
        row = out.get("row") or ["<no row>", "<no row>", None]
        ck("needs_reauth cleared to connected", row[0] == "connected", str(row))
        ck("last_error cleared", out.get("row") is not None and row[1] is None, str(row))
        ck("refreshed_at set", bool(row[2]))

        for label, cl in (("2. no YAMIN_OAUTH_CLIENT", None),
                          ("3. client file without client_secret",
                           {"web": {"client_id": "x", "token_uri": f"http://127.0.0.1:{port}/token"}})):
            print(f"── {label}")
            out, err = run_case(APP, tmp / label[:1], cl, port)
            ck("raises OAuthClientMissing", out.get("raised") == "OAuthClientMissing", str(out)[:200])
            ck("zero network calls (after a successful seed)",
               "seed_error" not in out and len(TokenEndpoint.posts) == 0, str(len(TokenEndpoint.posts)))
            ck("counter google_connection_oauth_client_missing = 1",
               out.get("counters", {}).get("google_connection_oauth_client_missing") == 1, str(out.get("counters")))
            ck("row says needs_reauth / oauth_client_missing",
               (out.get("row") or [None, None])[:2] == ["needs_reauth", "oauth_client_missing"], str(out.get("row")))
            ck("refusal is logged", "no usable OAuth client" in err)

        print(f"── 4. CANARY: case 1 against google_connection.py at {CANARY_REF}")
        old = tmp / "old-app"
        shutil.copytree(APP / "app", old / "app", ignore=shutil.ignore_patterns("__pycache__"))
        src = subprocess.run(["git", "-C", str(APP), "show", f"{CANARY_REF}:app/google_connection.py"],
                             capture_output=True, text=True)
        ck(f"pre-fix source available at {CANARY_REF}", src.returncode == 0, src.stderr[-200:])
        (old / "app" / "google_connection.py").write_text(src.stdout)
        out, err = run_case(old, tmp / "canary", client, port)
        ck("pre-fix code never reaches the token endpoint (after a successful seed)",
           "seed_error" not in out and len(TokenEndpoint.posts) == 0, str(out)[:200])
        ck("pre-fix code fails with RefreshError (the production error)",
           out.get("raised") == "RefreshError" and "necessary fields" in out.get("msg", ""), str(out)[:200])
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
