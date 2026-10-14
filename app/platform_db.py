"""Platform tables in Postgres (decision 2): approvals, grants, policies, the
audit log (`actions`) and model usage.

One database per tenant (jarvis_<slug>). The app connects as its own role
(jarvis_<slug>_app) by peer auth over the local socket; JARVIS_PG_DSN names
it. The tenant's agent user has no pg_hba rule, so it cannot connect at all.
Schema changes come only from the Alembic migrations in app/migrations, run
by scripts/pg_tenant.py as the database owner role; the app role gets the
grants in APP_PRIVILEGES and nothing else. `actions` is append-only: the app
may INSERT and SELECT, and a trigger refuses UPDATE, DELETE and TRUNCATE for
every role.
"""
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator

SCHEMA_HEAD = "0001"

APP_PRIVILEGES: Dict[str, str] = {
    "actions": "SELECT, INSERT",
    "held_actions": "SELECT, INSERT",
    "approvals": "SELECT, INSERT, UPDATE",
    "grants": "SELECT, INSERT, UPDATE",
    "policies": "SELECT, INSERT, UPDATE",
    "usage": "SELECT, INSERT, UPDATE",
    "alembic_version": "SELECT",
}


class Unavailable(RuntimeError):
    """The platform database is not configured, unreachable, or on the wrong schema."""


def dsn() -> str:
    return os.environ.get("JARVIS_PG_DSN", "").strip()


@contextmanager
def connect() -> Iterator[Any]:
    d = dsn()
    if not d:
        raise Unavailable("JARVIS_PG_DSN is not set")
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415
    try:
        conn = psycopg.connect(d, autocommit=True, row_factory=dict_row, connect_timeout=5)
    except psycopg.OperationalError as exc:
        raise Unavailable(f"cannot connect to the platform database: {exc}") from exc
    try:
        yield conn
    finally:
        conn.close()


def check() -> Dict[str, Any]:
    """Raises Unavailable unless the database answers on the expected schema."""
    with connect() as c:
        row = c.execute("SELECT version_num FROM alembic_version").fetchone()
        who = c.execute("SELECT current_user AS u, current_database() AS d").fetchone()
    version = (row or {}).get("version_num")
    if version != SCHEMA_HEAD:
        raise Unavailable(f"platform database schema is {version!r}, this build needs {SCHEMA_HEAD!r}")
    return {"schema": version, "role": who["u"], "database": who["d"]}
