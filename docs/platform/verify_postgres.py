#!/usr/bin/env python3
"""The host Postgres cluster keeps decision 2's conditions. Run as root.

  A  socket only, peer only, no rule for all users (setup_postgres check),
     and a canary proving that check sees a bad rule
  B  every agent OS user is refused by pg_hba, whatever database or role it
     names; TCP is not there to try
  C  each tenant app user reaches its own database as its own role, and
     nothing else
  D  the app role can append to the audit log but never rewrite it; the
     trigger stops even the superuser
  E  a dump restores with identical contents and the append-only trigger,
     and a changed source is noticed

    /opt/jarvis/venv/bin/python docs/platform/verify_postgres.py
"""
import json
import os
import pwd
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve()
APP = Path(os.environ.get("VERIFY_APP_ROOT") or HERE.parents[2])
sys.path.insert(0, str(APP / "scripts"))
sys.path.insert(0, str(APP))
import pg_tenant  # noqa: E402

PY = sys.executable
SOCKET = pg_tenant.SOCKET
HBA_D = pg_tenant.ETC / "hba.d"
VDB = "jarvis_verify_pgcheck"
REFUSED = ("no pg_hba.conf entry", "Peer authentication failed")
results = []


def ck(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f" — {detail}"))


def setup_check():
    p = subprocess.run([PY, str(APP / "scripts/setup_postgres.py"), "check"], capture_output=True, text=True)
    return p.returncode, p.stdout


def psql_as(user, db, role, host=SOCKET):
    p = subprocess.run(["runuser", "-u", user, "--", "psql", "-X", "-qAt", "-h", host, "-d", db, "-U", role,
                        "-c", "SELECT current_user"], capture_output=True, text=True, timeout=30, cwd="/")
    return p.returncode, (p.stdout + p.stderr).strip()


def tenants():
    """slug -> app OS user, from the installed hba rules."""
    out = {}
    for f in sorted(HBA_D.glob("*.conf")):
        if f.stem.startswith("00-"):
            continue
        ident = pg_tenant.ETC / "ident.d" / f.name
        out[f.stem] = ident.read_text().split()[1]
    return out


def main() -> int:
    if os.geteuid() != 0:
        sys.exit("run as root")

    print("── A: socket only, peer only")
    code, out = setup_check()
    ck("setup_postgres check: every line PASS", code == 0 and "FAIL" not in out and out.count("PASS") >= 7, out)
    canary = HBA_D / "zz-verify-canary.conf"
    try:
        # Not reloaded, so never live; and inert anyway with no TCP listener.
        canary.write_text("host   all   all   127.0.0.1/32   trust\n")
        code, out = setup_check()
        fails = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
        ck("canary: a host/trust/all rule on disk makes the check fail on all three counts",
           code != 0 and any("local" in f for f in fails) and any("peer" in f for f in fails)
           and any("all users" in f for f in fails), out)
    finally:
        canary.unlink(missing_ok=True)
    code, out = setup_check()
    ck("…and with the canary removed the check is clean again", code == 0, out)

    agents = sorted(u.pw_name for u in pwd.getpwall() if u.pw_name.startswith("jarvis-") and u.pw_name.endswith("-agent"))
    T = tenants()
    print(f"── B: agent users refused ({', '.join(agents) or 'none'})")
    ck("there are agent users to test, including jarvis-rehearsal-agent", "jarvis-rehearsal-agent" in agents, str(agents))
    subprocess.run([PY, str(APP / "scripts/pg_tenant.py"), "verify-db", "create", VDB], check=True, capture_output=True)
    for agent in agents:
        slug = agent[len("jarvis-"):-len("-agent")]
        targets = [(f"jarvis_{slug}", f"jarvis_{slug}_app"), (VDB, "jarvis_verify"), ("postgres", "postgres")]
        targets += [(f"jarvis_{s}", f"jarvis_{s}_app") for s in T if s != slug]
        for db, role in targets:
            code, out = psql_as(agent, db, role)
            ck(f"{agent} → {db} as {role}: refused by pg_hba", code != 0 and any(r in out for r in REFUSED), out[-200:])
        code, out = psql_as(agent, "postgres", "postgres", host="127.0.0.1")
        ck(f"{agent} over TCP: nothing listening", code != 0 and "Connection refused" in out, out[-200:])

    print(f"── C: each app user reaches its own database only ({', '.join(T) or 'none'})")
    ck("at least one tenant database to test", bool(T), str(T))
    for slug, user in T.items():
        code, out = psql_as(user, f"jarvis_{slug}", f"jarvis_{slug}_app")
        ck(f"{user} → jarvis_{slug} as jarvis_{slug}_app: connects (presence)",
           code == 0 and out == f"jarvis_{slug}_app", out[-200:])
        for db, role in [(f"jarvis_{slug}", "postgres"), (VDB, "jarvis_verify"), (f"jarvis_{slug}", f"jarvis_{slug}_owner")]:
            code, out = psql_as(user, db, role)
            ck(f"{user} → {db} as {role}: refused", code != 0 and any(r in out for r in REFUSED), out[-200:])

    print("── D: the app role appends, never rewrites")
    app = psycopg.connect(pg_tenant.dsn_for(VDB, "jarvis_verify"), autocommit=True)
    app.execute("INSERT INTO actions (event, outcome, action_class) VALUES ('decision', 'allowed', 'read')")
    ck("app role inserts an audit row", app.execute("SELECT count(*) FROM actions").fetchone()[0] == 1)
    for label, sql in [("UPDATE", "UPDATE actions SET reason = 'x'"), ("DELETE", "DELETE FROM actions"),
                       ("TRUNCATE", "TRUNCATE actions CASCADE"),
                       ("DISABLE TRIGGER", "ALTER TABLE actions DISABLE TRIGGER actions_no_update_delete"),
                       ("CREATE TABLE", "CREATE TABLE sneaky (x int)"),
                       ("DELETE approvals", "DELETE FROM approvals")]:
        try:
            app.execute(sql)
            ck(f"app role {label} refused", False, "succeeded")
        except psycopg.Error as exc:
            ck(f"app role {label} refused", True, str(exc))
    with pg_tenant.admin(VDB) as su:
        try:
            su.execute("UPDATE actions SET reason = 'x'")
            ck("superuser UPDATE stopped by the trigger", False, "succeeded")
        except psycopg.Error as exc:
            ck("superuser UPDATE stopped by the trigger", "append-only" in str(exc), str(exc))
    ck("append_only_enforced() says yes on an intact database", pg_tenant.append_only_enforced(VDB))

    print("── E: dump, restore, compare")
    tmp = Path(tempfile.mkdtemp(prefix="pgcheck-"))
    dump = tmp / "pgcheck.dump"
    pg_tenant.pg_dump(VDB, dump)
    ck("dump is private (0600)", oct(dump.stat().st_mode & 0o777) == "0o600")
    p = subprocess.run([PY, str(APP / "scripts/pg_tenant.py"), "restore-check", str(dump), "--against", VDB],
                       capture_output=True, text=True)
    r = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    ck("restore matches the source, table by table", p.returncode == 0 and r.get("matches_source") is True, p.stdout[-300:])
    ck("…the restored audit log is still append-only", r.get("append_only_after_restore") is True)
    ck("…and has the row we wrote", (r.get("restored") or {}).get("actions", [0])[0] == 1, str(r.get("restored")))
    app.execute("INSERT INTO actions (event, outcome, action_class) VALUES ('decision', 'allowed', 'read')")
    p = subprocess.run([PY, str(APP / "scripts/pg_tenant.py"), "restore-check", str(dump), "--against", VDB],
                       capture_output=True, text=True)
    r = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    ck("canary: a source that moved after the dump is reported as a mismatch",
       p.returncode != 0 and r.get("matches_source") is False, p.stdout[-300:])
    app.close()
    with pg_tenant.admin(VDB) as su:
        su.execute("ALTER TABLE actions DISABLE TRIGGER actions_no_truncate")
    ck("canary: append_only_enforced() says no once a trigger is disabled", not pg_tenant.append_only_enforced(VDB))
    subprocess.run([PY, str(APP / "scripts/pg_tenant.py"), "verify-db", "drop", VDB], check=True, capture_output=True)
    dump.unlink()
    tmp.rmdir()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
