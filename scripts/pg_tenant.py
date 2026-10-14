#!/usr/bin/env python3
"""Per-tenant platform databases (decision 2). Run as root on the host.

    pg_tenant.py create   <slug> --app-user jarvis-<slug>   database, roles, pg_hba, migrate, grants
    pg_tenant.py migrate  <slug>                            alembic upgrade head, then re-apply grants
    pg_tenant.py dsn      <slug>                            the JARVIS_PG_DSN the app uses
    pg_tenant.py dump     <slug> <file>                     pg_dump -Fc (backup)
    pg_tenant.py restore-check <file>                       restore into a scratch db and compare
    pg_tenant.py drop     <slug> --yes                      teardown (callers archive a dump first)
    pg_tenant.py verify-db create|drop <name>               jarvis_verify_* databases for sandboxes

Each tenant gets database jarvis_<slug>, a NOLOGIN owner role that owns every
object, and a LOGIN app role that only the tenant's app OS user can become
(peer auth through ident map jarvis_<slug>). The agent user has no rule.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from app.platform_db import APP_PRIVILEGES, SCHEMA_HEAD  # noqa: E402

SOCKET = "/var/run/postgresql"
ETC = Path("/etc/postgresql/16/main")
SLUG = re.compile(r"^[a-z][a-z0-9]{0,18}$")
VERIFY_DB = re.compile(r"^jarvis_verify_[a-z0-9_]{1,40}$")
VERIFY_ROLE = "jarvis_verify"


def names(slug: str) -> dict:
    if not SLUG.match(slug):
        raise SystemExit(f"refusing slug {slug!r}: [a-z][a-z0-9]{{0,18}}")
    return {"db": f"jarvis_{slug}", "owner": f"jarvis_{slug}_owner", "app": f"jarvis_{slug}_app",
            "map": f"jarvis_{slug}"}


def admin(db: str = "postgres"):
    import psycopg  # noqa: PLC0415
    return psycopg.connect(host=SOCKET, user="postgres", dbname=db, autocommit=True)


def q(ident: str) -> str:
    if not re.match(r"^[a-z_][a-z0-9_]*$", ident):
        raise SystemExit(f"refusing identifier {ident!r}")
    return f'"{ident}"'


def dsn_for(db: str, role: str) -> str:
    return f"postgresql:///{db}?host={SOCKET}&user={role}"


def _exists(c, sql: str, arg: str) -> bool:
    return c.execute(sql, (arg,)).fetchone() is not None


def ensure_roles_db(n: dict) -> None:
    with admin() as c:
        if not _exists(c, "SELECT 1 FROM pg_roles WHERE rolname=%s", n["owner"]):
            c.execute(f"CREATE ROLE {q(n['owner'])} NOLOGIN")
        if not _exists(c, "SELECT 1 FROM pg_roles WHERE rolname=%s", n["app"]):
            c.execute(f"CREATE ROLE {q(n['app'])} LOGIN CONNECTION LIMIT 20")
        if not _exists(c, "SELECT 1 FROM pg_database WHERE datname=%s", n["db"]):
            c.execute(f"CREATE DATABASE {q(n['db'])} OWNER {q(n['owner'])} ENCODING 'UTF8' TEMPLATE template0")
        c.execute(f"REVOKE ALL ON DATABASE {q(n['db'])} FROM PUBLIC")
        c.execute(f"GRANT CONNECT ON DATABASE {q(n['db'])} TO {q(n['app'])}")
    with admin(n["db"]) as c:
        c.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        c.execute(f"ALTER SCHEMA public OWNER TO {q(n['owner'])}")
        c.execute(f"GRANT USAGE ON SCHEMA public TO {q(n['app'])}")


def install_hba(slug: str, n: dict, app_user: str) -> None:
    if not re.match(r"^jarvis-[a-z0-9-]{1,30}$", app_user) or app_user.endswith("-agent"):
        raise SystemExit(f"refusing app user {app_user!r}: an agent user never gets a database rule")
    files = {
        ETC / "hba.d" / f"{slug}.conf": f"local   {n['db']}   {n['app']}   peer   map={n['map']}\n",
        ETC / "ident.d" / f"{slug}.conf": f"{n['map']}   {app_user}   {n['app']}\n",
    }
    for path, text in files.items():
        path.write_text(text)
        subprocess.run(["chown", "postgres:postgres", str(path)], check=True)
        os.chmod(path, 0o640)
    reload()


def reload() -> None:
    with admin() as c:
        c.execute("SELECT pg_reload_conf()")
        errs = c.execute("SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL").fetchone()[0]
        errs += c.execute("SELECT count(*) FROM pg_ident_file_mappings WHERE error IS NOT NULL").fetchone()[0]
    if errs:
        raise SystemExit("pg_hba/pg_ident has errors after the change; fix before continuing")


def migrate(db: str, owner: str, app_role: str) -> None:
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415
    cfg = Config()
    cfg.set_main_option("script_location", str(REPO / "app" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"postgresql+psycopg://postgres@/{db}?host={SOCKET}")
    cfg.attributes["owner_role"] = owner
    command.upgrade(cfg, "head")
    apply_grants(db, owner, app_role)


def apply_grants(db: str, owner: str, app_role: str) -> None:
    with admin(db) as c:
        tables = {r[0] for r in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()}
        missing = set(APP_PRIVILEGES) - tables
        if missing:
            raise SystemExit(f"tables missing after migrate: {sorted(missing)}")
        for t in sorted(tables):
            c.execute(f"REVOKE ALL ON {q(t)} FROM PUBLIC, {q(app_role)}")
            if t in APP_PRIVILEGES:
                c.execute(f"GRANT {APP_PRIVILEGES[t]} ON {q(t)} TO {q(app_role)}")
        c.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, {q(app_role)}")
        c.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")
        version = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    if version != SCHEMA_HEAD:
        raise SystemExit(f"schema is {version}, expected {SCHEMA_HEAD}")


def cmd_create(a) -> None:
    n = names(a.slug)
    ensure_roles_db(n)
    install_hba(a.slug, n, a.app_user)
    migrate(n["db"], n["owner"], n["app"])
    print(json.dumps({"ok": True, "db": n["db"], "app_role": n["app"], "schema": SCHEMA_HEAD,
                      "dsn": dsn_for(n["db"], n["app"])}))


def cmd_migrate(a) -> None:
    n = names(a.slug)
    migrate(n["db"], n["owner"], n["app"])
    print(json.dumps({"ok": True, "db": n["db"], "schema": SCHEMA_HEAD}))


def cmd_dsn(a) -> None:
    n = names(a.slug)
    print(dsn_for(n["db"], n["app"]))


def pg_dump(db: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as fh:
        subprocess.run(["pg_dump", "-Fc", "--no-owner", "--no-privileges", "-h", SOCKET, "-U", "postgres",
                        "-d", db], check=True, stdout=fh)
    os.chmod(out, 0o600)


def cmd_dump(a) -> None:
    n = names(a.slug)
    pg_dump(n["db"], Path(a.file))
    print(json.dumps({"ok": True, "db": n["db"], "file": a.file, "bytes": Path(a.file).stat().st_size}))


def table_digest(db: str) -> dict:
    """Row count and an order-independent content hash per platform table."""
    out = {}
    with admin(db) as c:
        for t in sorted(APP_PRIVILEGES):
            row = c.execute(f"SELECT count(*), coalesce(md5(string_agg(md5(x::text), '' ORDER BY md5(x::text))), '') "
                            f"FROM {q(t)} x").fetchone()
            out[t] = [row[0], row[1]]
    return out


class _Rollback(Exception):
    pass


def append_only_enforced(db: str) -> bool:
    """Both triggers present and enabled, and TRUNCATE refused even as the
    superuser. The row trigger cannot be exercised on an empty table; the
    statement trigger can, and the TRUNCATE is rolled back if it ever succeeds."""
    import psycopg  # noqa: PLC0415
    with admin(db) as c:
        enabled = {r[0] for r in c.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'actions'::regclass AND tgenabled = 'O' "
            "AND NOT tgisinternal").fetchall()}
        if enabled != {"actions_no_update_delete", "actions_no_truncate"}:
            return False
        try:
            with c.transaction():
                c.execute("TRUNCATE actions CASCADE")
                raise _Rollback
        except _Rollback:
            return False
        except psycopg.Error:
            return True


def cmd_restore_check(a) -> None:
    """Restore a dump into a scratch database, compare it with its source when
    --against names one, and drop the scratch database."""
    scratch = "jarvis_restorecheck"
    with admin() as c:
        c.execute(f"DROP DATABASE IF EXISTS {q(scratch)}")
        c.execute(f"CREATE DATABASE {q(scratch)} TEMPLATE template0")
    try:
        subprocess.run(["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", "-h", SOCKET,
                        "-U", "postgres", "-d", scratch, a.file], check=True)
        restored = table_digest(scratch)
        result = {"ok": True, "restored": restored}
        if a.against:
            source = table_digest(a.against)
            result["matches_source"] = source == restored
            result["ok"] = source == restored
        result["append_only_after_restore"] = append_only_enforced(scratch)
        result["ok"] = result["ok"] and result["append_only_after_restore"]
    finally:
        with admin() as c:
            c.execute(f"DROP DATABASE IF EXISTS {q(scratch)}")
    print(json.dumps(result))
    if not result["ok"]:
        raise SystemExit(1)


def cmd_drop(a) -> None:
    if not a.yes:
        raise SystemExit("drop deletes the tenant's platform database; pass --yes after archiving a dump")
    n = names(a.slug)
    for p in (ETC / "hba.d" / f"{a.slug}.conf", ETC / "ident.d" / f"{a.slug}.conf"):
        p.unlink(missing_ok=True)
    reload()
    with admin() as c:
        c.execute(f"DROP DATABASE IF EXISTS {q(n['db'])} WITH (FORCE)")
        for role in (n["app"], n["owner"]):
            c.execute(f"DROP ROLE IF EXISTS {q(role)}")
    print(json.dumps({"ok": True, "dropped": n["db"]}))


def cmd_verify_db(a) -> None:
    if not VERIFY_DB.match(a.name):
        raise SystemExit("verify databases are named jarvis_verify_*")
    with admin() as c:
        c.execute(f"DROP DATABASE IF EXISTS {q(a.name)} WITH (FORCE)")
        if a.op == "drop":
            print(json.dumps({"ok": True, "dropped": a.name}))
            return
        owner = f"{a.name}_owner"
        if not _exists(c, "SELECT 1 FROM pg_roles WHERE rolname=%s", owner):
            c.execute(f"CREATE ROLE {q(owner)} NOLOGIN")
        c.execute(f"CREATE DATABASE {q(a.name)} OWNER {q(owner)} TEMPLATE template0")
        c.execute(f"REVOKE ALL ON DATABASE {q(a.name)} FROM PUBLIC")
        c.execute(f"GRANT CONNECT ON DATABASE {q(a.name)} TO {q(VERIFY_ROLE)}")
    with admin(a.name) as c:
        c.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        c.execute(f"ALTER SCHEMA public OWNER TO {q(owner)}")
        c.execute(f"GRANT USAGE ON SCHEMA public TO {q(VERIFY_ROLE)}")
    migrate(a.name, owner, VERIFY_ROLE)
    print(dsn_for(a.name, VERIFY_ROLE))


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("run as root")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create"); p.add_argument("slug"); p.add_argument("--app-user", required=True)
    p = sub.add_parser("migrate"); p.add_argument("slug")
    p = sub.add_parser("dsn"); p.add_argument("slug")
    p = sub.add_parser("dump"); p.add_argument("slug"); p.add_argument("file")
    p = sub.add_parser("restore-check"); p.add_argument("file"); p.add_argument("--against")
    p = sub.add_parser("drop"); p.add_argument("slug"); p.add_argument("--yes", action="store_true")
    p = sub.add_parser("verify-db"); p.add_argument("op", choices=("create", "drop")); p.add_argument("name")
    a = ap.parse_args()
    {"create": cmd_create, "migrate": cmd_migrate, "dsn": cmd_dsn, "dump": cmd_dump,
     "restore-check": cmd_restore_check, "drop": cmd_drop, "verify-db": cmd_verify_db}[a.cmd](a)


if __name__ == "__main__":
    main()
