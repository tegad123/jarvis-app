#!/usr/bin/env python3
"""Prove a provisioned tenant's two-user isolation. Run as root on the host.

    prove_isolation.py --slug SLUG [--expect-app-source not_connected|tenant_connection]

Prints results only, never a secret value. Every denial has a root canary
(the same read succeeds as root, so the file exists and the probe works) and
every user has an own-file presence twin. Side effects: probe files created
and removed in shared dirs, one transient canary unit, one operator login.
Exit 0 only if every check passes.
"""
import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

PY = "/opt/jarvis/venv/bin/python"
fails = []
passed = []


def check(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if ok:
        passed.append(name)
    else:
        fails.append(name)


def pid(unit):
    return subprocess.check_output(["systemctl", "show", "-p", "MainPID", "--value", unit], text=True).strip()


def env_file(path: Path) -> dict:
    out = {}
    for ln in path.read_text().splitlines():
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def proc_env(p) -> dict:
    raw = Path(f"/proc/{p}/environ").read_bytes().split(b"\0")
    return dict(kv.decode().split("=", 1) for kv in raw if b"=" in kv)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--expect-app-source", default="not_connected",
                    choices=("not_connected", "tenant_connection"))
    args = ap.parse_args()
    slug = args.slug
    OPT = Path(f"/opt/{slug}")
    H, S, PRIV = OPT / "hermes", OPT / "skills/openclaw-imports", OPT / "app-private"
    APP, AGENT, GROUP = f"jarvis-{slug}", f"jarvis-{slug}-agent", slug
    ENVF = Path(f"/etc/jarvis/{slug}-app.env")
    app_unit, gw_unit = f"{slug}-app", f"{slug}-gateway"
    port = re.search(r"APP_PORT=(\d+)", Path(f"/etc/systemd/system/{app_unit}.service").read_text()).group(1)

    def sh(user, cmd, sandbox=False):
        if sandbox:
            argv = ["systemd-run", "--quiet", "--wait", "--pipe", "--collect",
                    "-p", f"User={user}", "-p", f"SupplementaryGroups={GROUP}",
                    "-p", "ProtectHome=yes", "-p", "ProtectSystem=strict", "-p", "PrivateTmp=yes",
                    "-p", "NoNewPrivileges=yes", "/bin/sh", "-c", cmd]
        elif user == "root":
            argv = ["/bin/sh", "-c", cmd]
        else:
            argv = ["runuser", "-u", user, "--", "/bin/sh", "-c", cmd]
        return subprocess.run(argv, capture_output=True, text=True, timeout=120).returncode

    def read(path):
        return f"head -c1 '{path}' >/dev/null"

    def counter(name):
        c = sqlite3.connect(f"file:{PRIV}/jarvis_threads.db?mode=ro", uri=True)
        try:
            row = c.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0]) if row else 0

    app_pid, gw_pid = pid(app_unit), pid(gw_unit)
    print(f"── {slug}: processes")
    for unit, user, p in ((app_unit, APP, app_pid), (gw_unit, AGENT, gw_pid)):
        who = subprocess.run(["ps", "-o", "user=", "-p", p], capture_output=True, text=True).stdout.strip()
        check(f"{unit} pid {p} runs as {user}", who == user)

    want = env_file(ENVF)
    print("── login through the running app (operator passphrase from the env file)")
    import httpx
    r = httpx.post(f"http://127.0.0.1:{port}/api/login", json={"passphrase": want.get("APP_PASSPHRASE", "")},
                   timeout=20)
    check(f"operator login 200 (got {r.status_code})", r.status_code == 200)
    cookie = "; ".join(c.split(";", 1)[0] for c in r.headers.get_list("set-cookie"))
    s = httpx.get(f"http://127.0.0.1:{port}/api/session", headers={"cookie": cookie}, timeout=20)
    role = s.json().get("role") if s.status_code == 200 else None
    check(f"session is operator (got {s.status_code} {role})", role == "operator")
    bad = httpx.post(f"http://127.0.0.1:{port}/api/login", json={"passphrase": "wrong-wrong-wrong"}, timeout=20)
    check(f"canary: a wrong passphrase is refused (got {bad.status_code})", bad.status_code in (401, 403))
    check("accounts db lives in app-private", (PRIV / "jarvis_accounts.db").is_file())
    check("no accounts db in hermes/state", not (H / "state/jarvis_accounts.db").exists())
    check("threads db lives in app-private", (PRIV / "jarvis_threads.db").is_file())
    check("no threads db in hermes/state", not (H / "state/jarvis_threads.db").exists())

    print("── posture: blocked, writes off everywhere")
    app_env, gw_env = proc_env(app_pid), proc_env(gw_pid)
    check("app process has no JARVIS_ALLOW_WRITES", "JARVIS_ALLOW_WRITES" not in app_env)
    check("gateway process has no JARVIS_ALLOW_WRITES", "JARVIS_ALLOW_WRITES" not in gw_env)
    runtime = json.loads((S / "_shared/runtime.json").read_text())
    check("runtime.safety.allow_writes is false", (runtime.get("safety") or {}).get("allow_writes") is False)
    check("preflight says POSTURE=\"blocked\"", 'POSTURE="blocked"' in (OPT / f"{slug}_preflight.sh").read_text())

    print("── the agent cannot read app secrets (runuser, then the unit's sandbox)")
    for f in (PRIV / "oauth_client.json", PRIV / "jarvis_accounts.db", PRIV / "jarvis_threads.db", ENVF,
              Path(f"/proc/{app_pid}/environ")):
        check(f"root canary can read {f}", sh("root", read(f)) == 0)
        check(f"agent denied {f}", sh(AGENT, read(f)) != 0)
        check(f"agent denied {f} (sandboxed)", sh(AGENT, read(f), sandbox=True) != 0)
    check("root canary can list /root", sh("root", "ls /root >/dev/null") == 0)
    check("agent cannot list /root", sh(AGENT, "ls /root >/dev/null") != 0)
    check("agent cannot list /root (sandboxed)", sh(AGENT, "ls /root >/dev/null", sandbox=True) != 0)

    print("── the app cannot read agent-only state")
    for f in (H / ".env", H / "state.db", Path(f"/proc/{gw_pid}/environ")):
        check(f"root canary can read {f}", sh("root", read(f)) == 0)
        check(f"app denied {f}", sh(APP, read(f)) != 0)
    check("root canary can list the agent's home", sh("root", f"ls '{H}' >/dev/null") == 0)
    check("app cannot list the agent's home (0710)", sh(APP, f"ls '{H}' >/dev/null") != 0)
    check("app cannot list hermes/sessions", sh(APP, f"ls '{H}/sessions' >/dev/null") != 0)

    print("── own files and shared paths work (presence twins)")
    check("app reads its oauth client", sh(APP, read(PRIV / "oauth_client.json")) == 0)
    check("app reads hermes/config.yaml (preflight input)", sh(APP, read(H / "config.yaml")) == 0)
    check("agent reads its hermes/.env", sh(AGENT, read(H / ".env")) == 0)
    check("agent reads state.db", sh(AGENT, read(H / "state.db")) == 0)
    data_dirs = sorted(p for p in S.glob("*/data") if p.is_dir())
    check("skill data dirs exist", len(data_dirs) > 0)
    for user in (APP, AGENT):
        for d in [H / "state"] + data_dirs[:2]:
            probe = d / f".probe-{user}"
            check(f"{user} can write {d}", sh(user, f"echo x > '{probe}' && rm '{probe}'") == 0)

    shared = [f for f in (H / "state").iterdir() if f.is_file() and not f.name.startswith("gateway.")]
    not_gw = [f.name for f in shared if (f.stat().st_mode & 0o060) != 0o060]
    check(f"every shared state file is group read-write ({len(shared)} files)", shared and not not_gw)
    if not_gw:
        print(f"        not group-writable: {sorted(not_gw)}")
    for user in (APP, AGENT):
        check(f"{user} can append to activity_feed.jsonl",
              sh(user, f"test -w '{H}/state/activity_feed.jsonl'") == 0)

    print("── code is read-only to both users; the root-run backup script is not tenant-writable")
    for user in (APP, AGENT):
        for d in (S / "_shared", OPT / "jarvis-app/app", OPT / "jarvis-app/scripts"):
            check(f"{user} cannot create files in {d}", sh(user, f"touch '{d}/.probe' 2>/dev/null") != 0)
        check(f"{user} cannot modify backup_tenant.py",
              sh(user, f"echo >> '{OPT}/jarvis-app/scripts/backup_tenant.py' 2>/dev/null") != 0)
    check("root canary can create files in _shared",
          sh("root", f"touch '{S}/_shared/.probe' && rm '{S}/_shared/.probe'") == 0)

    print("── secrets: in the app process, absent from units, systemctl show and the gateway")
    want = env_file(ENVF)
    for k in ("APP_GOOGLE_TOKEN_KEY", "APP_PASSPHRASE", "APP_SESSION_SECRET", "API_SERVER_KEY"):
        check(f"app process {k} matches the env file (sha256)",
              bool(want.get(k)) and hashlib.sha256(app_env.get(k, "").encode()).digest()
              == hashlib.sha256(want[k].encode()).digest())
    check("gateway process has no APP_GOOGLE_TOKEN_KEY", "APP_GOOGLE_TOKEN_KEY" not in gw_env)
    check("gateway process has no APP_PASSPHRASE", "APP_PASSPHRASE" not in gw_env)
    broker_url = f"http://127.0.0.1:{port}"
    check("gateway process is in broker mode (JARVIS_APP_BROKER_URL = the app)",
          gw_env.get("JARVIS_APP_BROKER_URL") == broker_url)
    check("app process has no JARVIS_APP_BROKER_URL", "JARVIS_APP_BROKER_URL" not in app_env)
    check("hermes/.env holds API_SERVER_KEY only", sorted(env_file(H / ".env")) == ["API_SERVER_KEY"])
    units = [p for p in Path("/etc/systemd/system").glob(f"{slug}-*") if p.is_file()] + \
            list(Path("/etc/systemd/system").glob(f"{slug}-*.d/*.conf"))
    secret_rx = re.compile(r"(APP_GOOGLE_TOKEN_KEY|APP_PASSPHRASE|APP_SESSION_SECRET|API_SERVER_KEY)=")
    check(f"no secret assignment in {len(units)} unit files", not any(secret_rx.search(p.read_text()) for p in units))
    check("canary: the unit-file pattern matches a key assignment",
          bool(secret_rx.search('Environment="APP_GOOGLE_TOKEN_KEY=x"')))
    show = subprocess.run(["runuser", "-u", AGENT, "--", "systemctl", "show", app_unit, "-p", "Environment"],
                          capture_output=True, text=True).stdout
    check("systemctl show (as the agent) has no APP_GOOGLE_TOKEN_KEY", "APP_GOOGLE_TOKEN_KEY" not in show)
    subprocess.run(["systemd-run", "--quiet", "--unit", f"isolation-canary-{slug}", "-p",
                    "Environment=CANARY_SECRET=visible", "--remain-after-exit", "/bin/true"],
                   capture_output=True, text=True)
    seen = subprocess.run(["runuser", "-u", AGENT, "--", "systemctl", "show", f"isolation-canary-{slug}",
                           "-p", "Environment"], capture_output=True, text=True).stdout
    subprocess.run(["systemctl", "stop", f"isolation-canary-{slug}"], capture_output=True)
    check("canary: systemctl show exposes Environment= to the agent", "CANARY_SECRET=visible" in seen)

    print("── google_creds: the agent goes through the app broker and never holds tokens")
    base_env = {"PATH": "/usr/bin:/bin", "HERMES_HOME": str(H), "JARVIS_APP_ROOT": str(OPT / "jarvis-app"),
                "JARVIS_SKILLS_ROOT": str(S), "JARVIS_RUNTIME_CONFIG": str(S / "_shared/runtime.json"),
                "JARVIS_IDENTITY_CONFIG": str(S / "_shared/identity.json"),
                "JARVIS_ACCOUNTS_DB": str(PRIV / "jarvis_accounts.db"),
                "JARVIS_THREADS_DB": str(PRIV / "jarvis_threads.db"), "PYTHONDONTWRITEBYTECODE": "1"}
    probe = (f"import sys,json,base64; sys.path.insert(0,'{S}/_shared'); import google_creds as g\n"
             "o={'source': g.source()}\n"
             "try:\n    g.credentials(); o['raised']=None\n"
             "except Exception as e:\n    o['raised']=type(e).__name__; o['msg']=str(e)\n"
             "if '--send' in sys.argv:\n"
             "    raw=base64.urlsafe_b64encode(b'To: probe@isolation.invalid\\r\\nSubject: p\\r\\n\\r\\nx').decode()\n"
             "    try:\n        g.gmail().users().messages().send(userId='me', body={'raw': raw}).execute(); o['send']='SENT'\n"
             "    except Exception as e:\n        o['send']=type(e).__name__\n"
             "    import app_counters; app_counters.bump('prove_isolation_broker_bump')\n"
             "print(json.dumps(o))")

    def as_user(user, home, broker=False, send=False):
        e = dict(base_env, HOME=home)
        if broker:
            e["JARVIS_APP_BROKER_URL"] = broker_url
        argv = (["runuser", "-u", user, "--", "env", "-i"] + [f"{k}={v}" for k, v in e.items()]
                + [PY, "-c", probe] + (["--send"] if send else []))
        p = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        try:
            return json.loads(p.stdout.strip().splitlines()[-1]), p.stderr
        except Exception:
            return {"error": p.stderr[-400:]}, p.stderr

    a0, w0, b0 = (counter("google_creds_tokens_app_side"), counter("broker_google_write_refused"),
                  counter("prove_isolation_broker_bump"))
    agent_out, _ = as_user(AGENT, str(H), broker=True, send=True)
    check(f"agent source() via broker = {args.expect_app_source} (got {agent_out.get('source')})",
          agent_out.get("source") == args.expect_app_source)
    check(f"agent credentials() raises TokensAreAppSide (got {agent_out.get('raised')})",
          agent_out.get("raised") == "TokensAreAppSide")
    check("…counted through the broker (google_creds_tokens_app_side +1)",
          counter("google_creds_tokens_app_side") - a0 == 1)
    check(f"agent send through the broker is refused, writes blocked (got {agent_out.get('send')})",
          agent_out.get("send") == "WritesDisabled")
    check("…counted app-side (broker_google_write_refused +1)", counter("broker_google_write_refused") - w0 == 1)
    check("agent counter bump lands in the app-private threads db (+1)",
          counter("prove_isolation_broker_bump") - b0 == 1)
    check("no stray threads db in hermes/state", not (H / "state/jarvis_threads.db").exists())

    print("── twin: without the broker the agent is refused as unreadable, not told to reconnect")
    u0, n0 = counter("google_creds_connection_unreadable"), counter("google_creds_not_connected")
    direct, direct_err = as_user(AGENT, str(H))
    check(f"agent source() direct = connection_unreadable (got {direct.get('source')})",
          direct.get("source") == "connection_unreadable")
    check(f"agent credentials() direct raises GoogleConnectionUnreadable (got {direct.get('raised')})",
          direct.get("raised") == "GoogleConnectionUnreadable")
    check("agent message does not say reconnect", "Reconnect" not in direct.get("msg", "Reconnect"))
    check("agent refusal is logged", "connection not readable from this process" in direct_err)
    check("unreadable counter did not move (the agent cannot write the threads db either)",
          counter("google_creds_connection_unreadable") - u0 == 0)
    check("not-connected counter did not move", counter("google_creds_not_connected") - n0 == 0)
    check("no stray accounts db in hermes/state", not (H / "state/jarvis_accounts.db").exists())
    app_out, _ = as_user(APP, str(PRIV))
    check(f"app source() = {args.expect_app_source} (got {app_out.get('source')})",
          app_out.get("source") == args.expect_app_source)

    print(f"\n{len(passed)}/{len(passed) + len(fails)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
