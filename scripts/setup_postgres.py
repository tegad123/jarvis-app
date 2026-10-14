#!/usr/bin/env python3
"""Configure the host Postgres cluster for platform tenants (decision 2).

    python3 scripts/setup_postgres.py apply     # write config, restart the cluster
    python3 scripts/setup_postgres.py check     # assert the running cluster matches

Shape:
- Unix socket only: listen_addresses = ''. No TCP listener at all.
- pg_hba has exactly one built-in rule (root and postgres -> role postgres, peer)
  and includes hba.d/. Each tenant gets hba.d/<slug>.conf allowing only its app
  OS user, by peer auth, into its own database as its own role. Agent users
  have no rule, so Postgres refuses them before any password exchange.
- Verify sandboxes run as root against databases named jarvis_verify_*; root
  maps to the jarvis_verify role for those databases only.
"""
import argparse
import subprocess
import sys
from pathlib import Path

CLUSTER = ("16", "main")
ETC = Path("/etc/postgresql") / CLUSTER[0] / CLUSTER[1]
SOCKET_DIR = "/var/run/postgresql"
VERIFY_ROLE = "jarvis_verify"

CONF = """\
# Managed by jarvis-platform scripts/setup_postgres.py.
listen_addresses = ''
unix_socket_permissions = 0777
max_connections = 40
shared_buffers = 64MB
work_mem = 4MB
maintenance_work_mem = 32MB
effective_cache_size = 256MB
password_encryption = scram-sha-256
log_connections = on
log_disconnections = on
"""

HBA = """\
# Managed by jarvis-platform scripts/setup_postgres.py. Unix socket only.
# Tenant rules live in hba.d/<slug>.conf. No rule = refused.
local   all   postgres   peer   map=jarvis_admin
include_dir hba.d
"""

IDENT = """\
# Managed by jarvis-platform scripts/setup_postgres.py.
jarvis_admin   root       postgres
jarvis_admin   postgres   postgres
include_dir ident.d
"""

VERIFY_HBA = f"local   /^jarvis_verify_[a-z0-9_]+$   {VERIFY_ROLE}   peer   map=jarvis_verify\n"
VERIFY_IDENT = f"jarvis_verify   root   {VERIFY_ROLE}\n"


def run(*cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


def psql_admin(sql: str, db: str = "postgres") -> str:
    return run("psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-h", SOCKET_DIR,
               "-U", "postgres", "-d", db, "-c", sql).stdout.strip()


def install(path: Path, text: str, mode: str = "0640") -> None:
    path.write_text(text)
    run("chown", "postgres:postgres", str(path))
    run("chmod", mode, str(path))


def apply() -> None:
    for name in ("pg_hba.conf", "pg_ident.conf"):
        dist = ETC / f"{name}.dist"
        if not dist.exists():
            run("cp", "-p", str(ETC / name), str(dist))
    for d in ("hba.d", "ident.d"):
        (ETC / d).mkdir(mode=0o750, exist_ok=True)
        run("chown", "postgres:postgres", str(ETC / d))
    install(ETC / "conf.d" / "jarvis.conf", CONF, "0644")
    install(ETC / "pg_hba.conf", HBA)
    install(ETC / "pg_ident.conf", IDENT)
    install(ETC / "hba.d" / "00-verify.conf", VERIFY_HBA)
    install(ETC / "ident.d" / "00-verify.conf", VERIFY_IDENT)
    run("systemctl", "restart", f"postgresql@{CLUSTER[0]}-{CLUSTER[1]}.service")
    if psql_admin(f"SELECT 1 FROM pg_roles WHERE rolname = '{VERIFY_ROLE}'") != "1":
        psql_admin(f"CREATE ROLE {VERIFY_ROLE} LOGIN")
    print("applied")


def check() -> int:
    fails = 0

    def ok(cond: bool, label: str) -> None:
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + label)
        fails += 0 if cond else 1

    ok(psql_admin("SHOW listen_addresses") == "", "listen_addresses is empty (socket only)")
    tcp = run("ss", "-Hltnp").stdout
    ok("postgres" not in tcp and ":5432 " not in tcp, "no TCP listener for postgres")
    rules = psql_admin("SELECT type, database, user_name, auth_method, coalesce(error,'') "
                       "FROM pg_hba_file_rules ORDER BY rule_number")
    lines = [r.split("|") for r in rules.splitlines()]
    ok(all(t == "local" for t, *_ in lines), "every pg_hba rule is local (no host lines)")
    ok(all(m == "peer" for *_, m, _e in lines), "every pg_hba rule is peer auth")
    ok(all(not e for *_, e in lines), "pg_hba parses with no errors")
    ok(not any("{all}" == u for _t, _d, u, *_ in lines), "no rule admits all users")
    ok(int(psql_admin("SHOW max_connections")) <= 40, "max_connections capped")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("apply", "check"))
    a = ap.parse_args()
    if a.action == "apply":
        apply()
        return check()
    return check()


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
