#!/usr/bin/env python3
"""Slice 2 — accounts.email column, unique index, backfill. Scratch DB only.

Copies the seeded TESTCO accounts file into a disposable box and points
JARVIS_ACCOUNTS_DB there BEFORE importing app.accounts. Never opens
/root/.hermes/state or the live :8700 database. Does not talk HTTP.
"""
import io
import json
import logging
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

BOX = Path(tempfile.mkdtemp(prefix="auth-email-col-"))
SRC = Path("/opt/jarvis-verify-fixture/hermes/state/jarvis_accounts.db")
IDN = Path("/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json")
FORBIDDEN = (Path("/root/.hermes"), Path("/root/jarvis-app/data"))


def snap_accounts(dst: Path) -> None:
    """WAL-inclusive copy. sqlite3 CLI is not installed; backup() is."""
    if not SRC.exists():
        sys.exit(f"REFUSING: no seeded accounts at {SRC}")
    src = sqlite3.connect(str(SRC))
    out = sqlite3.connect(str(dst))
    src.backup(out)
    out.close()
    src.close()


DB = BOX / "jarvis_accounts.db"
snap_accounts(DB)
# The fixture was migrated after this suite was written. Put the scratch copy
# back in the pre-migration shape so the migration and backfill really run.
_pre = sqlite3.connect(str(DB))
if "email" in [r[1] for r in _pre.execute("PRAGMA table_info(accounts)")]:
    _pre.execute("DROP INDEX IF EXISTS idx_accounts_email_lower")
    _pre.execute("ALTER TABLE accounts DROP COLUMN email")
    _pre.commit()
    print("  scratch copy: email column dropped to the pre-migration shape")
_pre.close()
os.environ["JARVIS_ACCOUNTS_DB"] = str(DB)
os.environ["HERMES_HOME"] = str(BOX / "hermes")
(BOX / "hermes" / "state").mkdir(parents=True)
os.environ["JARVIS_SKILLS_ROOT"] = "/opt/jarvis-verify-fixture/skills/openclaw-imports"
os.environ["JARVIS_IDENTITY_CONFIG"] = str(IDN)
sys.path.insert(0, os.environ.get("VERIFY_APP_ROOT") or str(Path(__file__).resolve().parents[2]))

ok = []
def ck(n, c, x=""):
    ok.append(bool(c))
    print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" — {x}" if x else ""))


def pragma_cols(path):
    con = sqlite3.connect(str(path))
    names = [r[1] for r in con.execute("PRAGMA table_info(accounts)")]
    con.close()
    return names


def indexes(path):
    con = sqlite3.connect(str(path))
    rows = list(con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='accounts'"
    ))
    con.close()
    return rows


print("── isolation ────────────────────────────────────────────────")
print(f"  box={BOX}")
ck("seeded source is testco, not live",
   "/opt/jarvis-verify-fixture/hermes/state" in str(SRC) and "/root/.hermes" not in str(SRC))
before_cols = pragma_cols(DB)
ck("copied schema has no email column yet", "email" not in before_cols,
   str(before_cols))

from app import accounts  # noqa: E402
import identity  # noqa: E402

ck("accounts.DB_PATH is inside the box",
   str(accounts.DB_PATH.resolve()).startswith(str(BOX.resolve())),
   str(accounts.DB_PATH))
ck("accounts.DB_PATH is not the live tenant",
   not str(accounts.DB_PATH.resolve()).startswith(str(Path("/root/.hermes").resolve())))
ck("accounts.DB_PATH is not the running testco file",
   Path(accounts.DB_PATH).resolve() != SRC.resolve())

print("\n── migration + backfill (first boot) ───────────────────────")
accounts.init()
after_cols = {r["name"] for r in accounts._connect().execute("PRAGMA table_info(accounts)")}
ck("email column present after init", "email" in after_cols)
idx = indexes(DB)
ck("unique index on lower(email) exists",
   any(n == "idx_accounts_email_lower" and sql and "lower(email)" in sql
       and "WHERE" in sql.upper() for n, sql in idx),
   str(idx))

dana = accounts.get("dana")
ck("dana row exists in the scratch copy", dana is not None)
ck("dana has email after backfill", bool(dana and dana.get("email")),
   "no email" if not (dana or {}).get("email") else "set")
ck("dana email is identity.people[dana].email[0], lowercased",
   (dana or {}).get("email") == "dana@testco.example.com")
roster = {r["person_key"]: r for r in accounts.roster()}
ck("roster shows email is set for dana, not a Google flow",
   roster.get("dana", {}).get("has_email") is True)
ck("roster has_email is a boolean, not the address",
   roster.get("dana", {}).get("has_email") is True
   and "email" not in {k for k in roster.get("dana", {}) if k == "email"})

print("\n── second boot is a no-op ──────────────────────────────────")
cols_once = after_cols
accounts.init()
cols_twice = {r["name"] for r in accounts._connect().execute("PRAGMA table_info(accounts)")}
ck("second init does not drop or duplicate columns", cols_once == cols_twice)
ck("second init still has the email column", "email" in cols_twice)
idx2 = indexes(DB)
ck("second init leaves the unique index in place",
   any(n == "idx_accounts_email_lower" for n, _ in idx2))

print("\n── unique index rejects a duplicate ────────────────────────")
dana_addr = dana["email"]
dup_rejected = False
dup_err = ""
try:
    accounts.set_email("omar", dana_addr)
except accounts.AccountError as exc:
    dup_rejected = True
    dup_err = str(exc)
ck("set-email duplicate raises AccountError", dup_rejected, dup_err)
omar = accounts.get("omar")
ck("omar did not take dana's address",
   (omar or {}).get("email") != dana_addr, (omar or {}).get("email") or "unset")

raw_rejected = False
con = sqlite3.connect(str(DB))
try:
    con.execute("UPDATE accounts SET email=? WHERE person_key='priya'", (dana_addr,))
    con.commit()
except sqlite3.IntegrityError:
    raw_rejected = True
    con.rollback()
con.close()
ck("raw UPDATE of a second row to the same address is IntegrityError", raw_rejected)

print("\n── deliberate-duplicate backfill skip-and-log ──────────────")
scratch_idn = BOX / "identity.json"
data = json.loads(IDN.read_text())
data["people"]["omar"]["email"] = [data["people"]["dana"]["email"][0]]
scratch_idn.write_text(json.dumps(data))
identity.CONFIG_PATH = scratch_idn
identity.load(force=True)

con = accounts._connect()
con.execute("UPDATE accounts SET email=NULL")
con.commit()

skip_logs = []
class _H(logging.Handler):
    def emit(self, record):
        skip_logs.append(record.getMessage())
h = _H()
accounts.log.addHandler(h)
accounts.log.setLevel(logging.WARNING)
aborted = False
try:
    result = accounts.backfill_emails_from_identity()
except Exception as exc:
    aborted = True
    result = {"filled": [], "skipped": [], "exc": type(exc).__name__}
finally:
    accounts.log.removeHandler(h)

ck("backfill completed (did not abort mid-run)", not aborted, str(result))
ck("backfill logged the skip",
   any("skip" in m and "duplicate" in m for m in skip_logs),
   skip_logs[-1] if skip_logs else "no warning")
ck("skipped list names the loser",
   bool(result.get("skipped")), str(result.get("skipped")))
holders = [r["person_key"] for r in accounts._connect().execute(
    "SELECT person_key FROM accounts WHERE lower(email)=lower(?)",
    (data["people"]["dana"]["email"][0],),
)]
ck("exactly one account holds the colliding address",
   len(holders) == 1, str(holders))
still_null = [r["person_key"] for r in accounts._connect().execute(
    "SELECT person_key FROM accounts WHERE person_key IN ('dana','omar') AND email IS NULL"
)]
ck("the other of dana/omar remains unset after the skip",
   len(still_null) == 1, str(still_null))

print("\n── PIN login still works (boxed, no HTTP) ─────────────────")
# Restore identity so dana's pin check is against the real registry.
identity.CONFIG_PATH = IDN
identity.load(force=True)
pins = json.loads(Path("/opt/jarvis-verify-fixture/SEEDED_PINS.json").read_text())
if isinstance(next(iter(pins.values())), dict):
    pins = {k: v.get("pin") for k, v in pins.items()}
acct = accounts.verify("dana", pins["dana"])
ck("dana PIN still verifies after the email column",
   acct is not None and acct.get("person_key") == "dana")
ck("wrong PIN still fails", accounts.verify("dana", "zzzz-zzzz-zzzz") is None)
ck("PIN hash columns still present",
   bool((acct or {}).get("pin_hash")) and bool((acct or {}).get("pin_salt")))

print("\n── CLI set-email ──────────────────────────────────────────")
# Give tester a distinct address (identity already has tester@…).
try:
    accounts.set_email("tester", "  tester@testco.example.com  ")
    cli_ok = True
    cli_err = ""
except accounts.AccountError as exc:
    cli_ok = False
    cli_err = str(exc)
ck("set-email stores trimmed lowercase",
   cli_ok and (accounts.get("tester") or {}).get("email") == "tester@testco.example.com",
   cli_err)
buf = io.StringIO()
sys_stdout = sys.stdout
sys.stdout = buf
try:
    from app import accounts_cli
    class _A: pass
    rc = accounts_cli.cmd_list(_A())
finally:
    sys.stdout = sys_stdout
listing = buf.getvalue()
ck("CLI list shows EMAIL column as set/unset, no @ addresses",
   rc == 0 and "EMAIL" in listing and "@" not in listing,
   listing.splitlines()[0] if listing else "empty")

print("\n── live stores untouched ──────────────────────────────────")
ck("scratch path still under the box",
   str(accounts.DB_PATH.resolve()).startswith(str(BOX.resolve())))

failed = sum(1 for x in ok if not x)
print(f"\n{sum(1 for x in ok if x)}/{len(ok)} passed")
sys.exit(0 if failed == 0 else 1)
