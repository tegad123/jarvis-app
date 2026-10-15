#!/usr/bin/env python3
"""Prove `provision_tenant.py --upgrade` on a replica of an old-layout tenant.

Run as root by the lead agent, under the agent lock. It builds a scratch tenant
(default `upgcheck`) from the SOURCE tenant's tree (default rehearsal): the
same code commits, rendered skill config, preflight and unit drop-ins with slug,
ports and addresses substituted. It never copies data or secrets: databases,
skill data and secrets are fresh, and the source tenant is only read.

  A. The replica runs at the old commits and is healthy.
  B. Seeded data: tasks, leads, threads (shared, the old layout), accounts,
     skill data files.
  C. Canary: an upgrade to a commit whose app cannot start rolls back by
     itself. The old code, unit files and data are back, no platform database
     is left, and the old version is healthy.
  D. The real upgrade: new commits, every seeded row and file kept, threads
     moved behind the app, old drop-ins archived, the feed file shared 660,
     writes still unset, then prove_isolation.py passes.
  E. A second upgrade with the platform database in place: a schema the new
     code does not expect is refused before anything stops (canary); a broken
     commit rolls back without dropping the database (canary); the real one
     keeps the database and its rows, dumps it into the snapshot, and the
     proof passes.

A-D need an old-layout source (threads in shared state). Every tenant is on
the new layout now, so --existing-db provisions the replica from a platform
tenant's manifest at the commits it runs (default eddie) and runs E only.

    python3 docs/platform/verify_upgrade.py [--source rehearsal] [--slug upgcheck]
        [--ports 8780,8782,18798] [--skills-sha SHA] [--app-sha SHA] [--keep] [--existing-db]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import provision_tenant as pt  # noqa: E402

PY = "/opt/jarvis/venv/bin/python"
PASS = FAIL = 0


def check(label: str, ok: bool) -> bool:
    global PASS, FAIL
    PASS, FAIL = PASS + bool(ok), FAIL + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def sh(*cmd: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, **kw)


def head(repo: Path) -> str:
    return sh("git", "-C", str(repo), "rev-parse", "HEAD").stdout.strip()


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Subst:
    """Source tenant names, ports and addresses -> the replica's."""

    def __init__(self, src_m: dict, slug: str, ports: list[int]):
        s = src_m["slug"]
        self.src, self.slug = s, slug
        self.pairs = [(f"/opt/{s}", f"/opt/{slug}"), (str(src_m["public_origin"]).rstrip("/"),
                                                      f"https://{slug}.invalid")]
        for d in src_m.get("boundary_domains") or []:
            self.pairs.append((d, f"{slug}.invalid"))
        for k, new in zip(("app", "gateway", "hooks"), ports):
            self.pairs.append((str(src_m["ports"][k]), str(new)))
        self.pairs += [(s, slug), (s.capitalize(), slug.capitalize())]
        self.forbidden = [s, s.capitalize()] + [p[0] for p in self.pairs[1:-2]]

    def __call__(self, text: str) -> str:
        for a, b in self.pairs:
            text = re.sub(rf"(?<![0-9A-Za-z]){re.escape(a)}(?![0-9])", b, text) if a.isdigit() \
                else text.replace(a, b)
        return text

    def leftovers(self, text: str) -> list[str]:
        return [f for f in self.forbidden if f in text]


def owner_map(src: str, slug: str, name: str) -> str:
    return {f"jarvis-{src}": f"jarvis-{slug}", f"jarvis-{src}-agent": f"jarvis-{slug}-agent",
            src: slug}.get(name, name)


def build_replica(src: str, slug: str, sub: Subst) -> None:
    S, R = Path(f"/opt/{src}"), Path(f"/opt/{slug}")
    print(f"── build {R} from {S} (code and config only; no data, no secrets)")
    R.mkdir(mode=0o700)
    ign_app = shutil.ignore_patterns("__pycache__")
    shutil.copytree(S / "jarvis-app", R / "jarvis-app", symlinks=True, ignore=ign_app)
    for d in (R / "jarvis-app" / "data").iterdir():
        shutil.rmtree(d) if d.is_dir() else d.unlink()
        d.mkdir()
    (R / "skills").mkdir()
    shutil.copytree(S / "skills" / "secrets", R / "skills" / "secrets")
    shutil.copytree(S / "skills" / "openclaw-imports", R / "skills" / "openclaw-imports", symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    clone = R / "skills" / "openclaw-imports"
    for data in clone.glob("*/data"):
        shutil.rmtree(data)
        data.mkdir()
    shared = clone / "_shared"
    (shared / f"{src}-provision.json").rename(shared / f"{slug}-provision.json")
    H = R / "hermes"
    H.mkdir()
    for d in ("state", "outbox", "credentials", "workspace"):
        (H / d).mkdir()
    (H / "skills").symlink_to(R / "skills")
    for name in ("config.yaml", "SOUL.md", ".no-bundled-skills"):
        shutil.copy2(S / "hermes" / name, H / name)
    shutil.copy2(S / "ONBOARDING-MAILBOX.md", R / "ONBOARDING-MAILBOX.md")
    shutil.copy2(S / f"{src}_preflight.sh", R / f"{slug}_preflight.sh")
    texts = [shared / n for n in ("identity.json", "runtime.json", "channels.json", f"{slug}-provision.json")]
    texts += [H / "config.yaml", H / "SOUL.md", R / f"{slug}_preflight.sh"]
    left = {}
    for p in texts:
        p.write_text(sub(p.read_text()))
        if sub.leftovers(p.read_text()):
            left[str(p)] = sub.leftovers(p.read_text())
    check(f"replica config carries none of the source's slug, domains or ports (left: {left or 'none'})",
          not left)

    n = pt.create_identities(slug)
    api = secrets.token_urlsafe(32)
    pw = "-".join(secrets.token_hex(2) for _ in range(3))
    env = pt.ETC_JARVIS / f"{slug}-app.env"
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"APP_GOOGLE_TOKEN_KEY={pt.Fernet.generate_key().decode()}\nAPP_PASSPHRASE={pw}\n"
                 f"APP_SESSION_SECRET={secrets.token_hex(32)}\nAPI_SERVER_KEY={api}\n")
    (H / ".env").write_text(f"API_SERVER_KEY={api}\n")
    priv = R / "app-private"
    priv.mkdir()
    sh("install", "-m", "600", str(pt.PLATFORM_OAUTH_CLIENT), str(priv / "oauth_client.json"))
    for name in ("jarvis_tasks.db", "jarvis_leads.db", "jarvis_threads.db"):
        (H / "state" / name).touch()

    # Ownership and modes copied from the source, path by path.
    unmatched = []
    for p in [R, *sorted(R.rglob("*"))]:
        if p.is_symlink():
            continue
        rel = p.relative_to(R)
        q = S / Path(str(rel).replace(f"{slug}_preflight", f"{src}_preflight")
                     .replace(f"{slug}-provision", f"{src}-provision"))
        if not q.exists() or q.is_symlink():
            unmatched.append(str(rel))
            continue
        st = q.stat()
        u = owner_map(src, slug, sh("id", "-nu", str(st.st_uid)).stdout.strip())
        g = owner_map(src, slug, sh("getent", "group", str(st.st_gid)).stdout.split(":")[0])
        shutil.chown(p, u, g)
        os.chmod(p, st.st_mode & 0o7777)
    print(f"  ownership mirrored; {len(unmatched)} path(s) with no source twin: {unmatched[:6]}")

    for p in pt.tenant_unit_paths(src):
        dest = Path(str(p).replace(f"{src}-", f"{slug}-", 1)) if p.parent == pt.UNIT_DIR \
            else p.parent / f"jarvis-{slug}"
        if p.is_dir():
            dest.mkdir()
            for f in p.iterdir():
                (dest / f.name).write_text(sub(f.read_text()))
        else:
            dest.write_text(sub(p.read_text()))
    units_left = {str(u): sub.leftovers(u.read_text()) for u in pt.UNIT_DIR.rglob(f"{slug}-*")
                  if u.is_file() and sub.leftovers(u.read_text())}
    check(f"replica units carry none of the source's names or ports (left: {units_left or 'none'})",
          not units_left)
    sh("systemctl", "daemon-reload")
    r = sh("systemctl", "enable", "--now", f"{slug}-app.service", f"{slug}-gateway.service", f"{slug}-backup.timer")
    print(f"  enable --now rc={r.returncode}")


def login(port: int, slug: str) -> httpx.Client:
    pw = next(ln.split("=", 1)[1] for ln in (pt.ETC_JARVIS / f"{slug}-app.env").read_text().splitlines()
              if ln.startswith("APP_PASSPHRASE="))
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30)
    r = c.post("/api/login", json={"passphrase": pw})
    r.raise_for_status()
    # Production posture marks the cookie Secure; send it back over loopback http by hand.
    c.headers["cookie"] = "; ".join(f"{k}={v}" for k, v in r.cookies.items())
    c.cookies.clear()
    return c


def seed_rows(src_db: Path, db: Path, table: str, n: int, fixed: dict | None = None) -> None:
    """The source's schema (DDL only, never rows) if the table is not there yet,
    then n rows with every required column filled."""
    con = sqlite3.connect(db)
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        s = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
        for (ddl,) in s.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL "
                                "ORDER BY type DESC", (table,)):
            con.execute(ddl)
        s.close()
    cols = [r for r in con.execute(f'PRAGMA table_info("{table}")') if r[3] and r[4] is None and not r[5]]
    for i in range(n):
        names = ", ".join(f'"{c[1]}"' for c in cols)
        vals = [(fixed or {}).get(c[1], i if "INT" in (c[2] or "").upper() else f"upgrade canary {i}")
                for c in cols]
        con.execute(f'INSERT INTO "{table}" ({names}) VALUES ({", ".join("?" * len(cols))})'
                    if cols else f'INSERT INTO "{table}" DEFAULT VALUES', vals)
    con.commit()
    con.close()


def seed(slug: str, src: str, port: int) -> None:
    R, S = Path(f"/opt/{slug}"), Path(f"/opt/{src}")
    login(port, slug).get("/api/ping").raise_for_status()
    seed_rows(S / "hermes/state/jarvis_tasks.db", R / "hermes/state/jarvis_tasks.db", "tasks", 3)
    seed_rows(S / "hermes/state/jarvis_leads.db", R / "hermes/state/jarvis_leads.db", "leads", 2)
    acc = R / "app-private" / "jarvis_accounts.db"
    seed_rows(S / "app-private/jarvis_accounts.db", acc, "accounts", 1, {"role": "member"})
    shutil.chown(acc, f"jarvis-{slug}", f"jarvis-{slug}")
    os.chmod(acc, 0o600)
    con = sqlite3.connect(R / "hermes" / "state" / "jarvis_threads.db")
    con.execute("INSERT INTO counters (name, value) VALUES ('upgrade_canary', 7)")
    con.execute("INSERT INTO threads (thread_id, name, created_by, created_at) "
                "VALUES ('upgrade-canary', 'upgrade canary', 'jarvis', datetime('now'))")
    con.commit()
    con.close()
    clone = R / "skills" / "openclaw-imports"
    (clone / "owner-email" / "data" / "external_sends.log").write_text("upgrade canary line\n")
    t = sqlite3.connect(clone / "task-manager" / "data" / "tasks.db")
    t.execute("CREATE TABLE IF NOT EXISTS canary (v TEXT)")
    t.execute("INSERT INTO canary VALUES ('kept')")
    t.commit()
    t.close()


def ledger(slug: str) -> dict:
    R = Path(f"/opt/{slug}")
    out = {}
    dbs = {"tasks": R / "hermes/state/jarvis_tasks.db", "leads": R / "hermes/state/jarvis_leads.db",
           "accounts": R / "app-private/jarvis_accounts.db"}
    th = [R / "hermes/state/jarvis_threads.db", R / "app-private/jarvis_threads.db"]
    dbs["threads"] = next((p for p in th if p.exists() and p.stat().st_size), th[0])
    for k, p in dbs.items():
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        out[k] = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                  for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
    clone = R / "skills" / "openclaw-imports"
    out["files"] = {str(p.relative_to(clone)): sha256(p) for p in
                    (clone / "owner-email/data/external_sends.log", clone / "task-manager/data/tasks.db")}
    return out


def lost(before: dict, after: dict) -> list[str]:
    """Tables that lost rows and seeded files that changed. The running app may
    add rows of its own; it must never lose one."""
    out = [f"{db}.{t}: {n} -> {after.get(db, {}).get(t)}" for db, tables in before.items() if db != "files"
           for t, n in tables.items() if (after.get(db, {}).get(t) or 0) < n]
    out += [f"file {f} changed" for f, h in before["files"].items() if after["files"].get(f) != h]
    return out


def unit_bytes(slug: str) -> dict:
    return {str(p): p.read_bytes() for p in sorted(pt.UNIT_DIR.rglob(f"{slug}-*")) if p.is_file()}


def db_exists(slug: str) -> bool:
    return sh("runuser", "-u", "postgres", "--", "psql", "-XqAt", "-c",
              f"SELECT 1 FROM pg_database WHERE datname = 'jarvis_{slug}'", cwd="/").stdout.strip() == "1"


def broken_commit() -> tuple[str, Path]:
    """A commit whose app refuses to import, on a throwaway branch."""
    wt = Path(tempfile.mkdtemp(prefix="upgrade-canary-"))
    shutil.rmtree(wt)
    sh("git", "-C", str(ROOT), "worktree", "add", "-q", "-b", "canary/upgrade-broken", str(wt), "HEAD")
    main = wt / "app" / "main.py"
    main.write_text('raise SystemExit("canary: this commit cannot start")\n' + main.read_text())
    sh("git", "-C", str(wt), "commit", "-qam", "canary: app cannot start (verify_upgrade)")
    return head(wt), wt


def run_upgrade(manifest: dict, path: Path) -> subprocess.CompletedProcess:
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    r = sh(PY, str(ROOT / "scripts" / "provision_tenant.py"), "--manifest", str(path), "--upgrade")
    log = re.sub(r"\b[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}\b", "<redacted>", r.stdout + r.stderr)
    (path.with_suffix(".log")).write_text(log)
    return r


def section_e(m: dict, slug: str, R: Path, work: Path, bad_sha: str) -> None:
    print("── E. upgrading a tenant that already has its platform database")
    db = f"jarvis_{slug}"
    psql = lambda q: sh("runuser", "-u", "postgres", "--", "psql", "-XqAt", "-d", db, "-c", q,  # noqa: E731
                        cwd="/").stdout.strip()
    psql("INSERT INTO usage (day, model, purpose, requests, input_tokens, output_tokens, cache_read_tokens, "
         "cache_write_tokens, cost_usd, unpriced_requests) VALUES (current_date, 'verify-upgrade-seed', "
         "'agent', 1, 1, 1, 0, 0, 0, 0)")
    seeded = lambda: psql("SELECT count(*) FROM usage WHERE model = 'verify-upgrade-seed'")  # noqa: E731
    check(f"seeded one usage row in {db} (got {seeded()})", seeded() == "1")
    version = psql("SELECT version_num FROM alembic_version")
    pid = lambda: sh("systemctl", "show", f"{slug}-app", "-p", "MainPID", "--value").stdout.strip()  # noqa: E731
    pid0, ran = pid(), head(R / "jarvis-app")

    psql("UPDATE alembic_version SET version_num = 'canary_mismatch'")
    r = run_upgrade(m, work / "mismatch.yaml")
    log = (work / "mismatch.log").read_text()
    check(f"canary: a schema the new code does not expect is refused (rc={r.returncode})",
          r.returncode != 0 and "needs a human yes" in log)
    check("   and nothing was stopped: same app process", pid() == pid0)
    psql(f"UPDATE alembic_version SET version_num = '{version}'")

    r = run_upgrade({**m, "app_origin_sha": bad_sha}, work / "broken2.yaml")
    log = (work / "broken2.log").read_text()
    check(f"canary: a broken commit on a tenant with a database rolls back (rc={r.returncode})",
          r.returncode != 0 and "old version healthy again" in log)
    check("   the existing database survives the rollback", db_exists(slug) and seeded() == "1")
    check(f"   app back at the commit it ran before ({ran[:7]})", head(R / "jarvis-app") == ran)

    earlier = set(Path(f"/root/backups/{slug}").glob("upgrade-*"))
    r = run_upgrade(m, work / "reupgrade.yaml")
    log = (work / "reupgrade.log").read_text()
    check(f"re-upgrade with the database in place exits 0 (rc={r.returncode})", r.returncode == 0)
    if r.returncode:
        print("\n".join(log.splitlines()[-15:]))
    check("it kept the database instead of creating one", f"{db} kept: schema {version}" in log)
    check(f"the seeded usage row is still there (got {seeded()})", seeded() == "1")
    snaps = [d for d in sorted(set(Path(f"/root/backups/{slug}").glob("upgrade-*")) - earlier)
             if (d / "opt.tar").exists()]
    dump = snaps[-1] / "platform.dump" if snaps else Path("/nonexistent")
    check("the database was dumped into the snapshot, root 600",
          dump.exists() and dump.stat().st_mode & 0o777 == 0o600)
    p = sh(PY, str(ROOT / "scripts" / "prove_isolation.py"), "--slug", slug)
    tail = (p.stdout.strip().splitlines() or [""])[-1]
    check(f"prove_isolation.py after the re-upgrade: {tail}", p.returncode == 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="rehearsal")
    ap.add_argument("--slug", default="upgcheck")
    ap.add_argument("--ports", default="8780,8782,18798")
    ap.add_argument("--skills-sha", default=None, help="default: docs/tenants/isocheck pin (platform)")
    ap.add_argument("--app-sha", default=None, help="default: this repo's HEAD")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--existing-db", action="store_true",
                    help="section E only: provision the replica from --source's manifest at the commits that "
                         "tenant runs (default source eddie), then upgrade it with its database in place")
    a = ap.parse_args()
    if a.existing_db and a.source == "rehearsal":
        a.source = "eddie"
    if os.geteuid() != 0:
        print("run as root (the lead agent, under the agent lock)")
        return 2
    src, slug = a.source, a.slug
    ports = [int(x) for x in a.ports.split(",")]
    if Path(f"/opt/{slug}").exists():
        print(f"/opt/{slug} exists; tear it down first")
        return 2
    src_m = yaml.safe_load((ROOT / "docs" / "tenants" / f"{src}.yaml").read_text())
    sub = Subst(src_m, slug, ports)
    old_app, old_skills = head(Path(f"/opt/{src}/jarvis-app")), head(Path(f"/opt/{src}/skills/openclaw-imports"))
    new_app = a.app_sha or head(ROOT)
    new_skills = a.skills_sha or yaml.safe_load((ROOT / "docs/tenants/eddie.yaml").read_text())["skills_origin_sha"]
    print(f"source {src}: app {old_app[:7]}, skills {old_skills[:7]}; upgrade to app {new_app[:7]}, "
          f"skills {new_skills[:7]}")
    m = yaml.safe_load(sub(yaml.safe_dump(src_m, sort_keys=False)))
    m.update(skills_origin_sha=new_skills, app_origin_sha=new_app, venv="platform",
             isolation="two_user", sentinel_mode="shadow")
    m.setdefault("backup", {})["destination"] = f"/root/backups/{slug}"
    work = Path(tempfile.mkdtemp(prefix="verify-upgrade-"))
    wt = None
    if a.existing_db:
        try:
            old_m = {**m, "app_origin_sha": old_app, "skills_origin_sha": old_skills}
            (work / "old.yaml").write_text(yaml.safe_dump(old_m, sort_keys=False))
            r = sh(PY, str(ROOT / "scripts" / "provision_tenant.py"), "--manifest", str(work / "old.yaml"))
            (work / "provision.log").write_text(re.sub(r"[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}", "<redacted>",
                                                       r.stdout + r.stderr))
            print(f"── provisioned {slug} from {src}'s manifest at app {old_app[:7]} (rc={r.returncode})")
            R = Path(f"/opt/{slug}")
            bad = pt.wait_healthy(slug, {"app": ports[0], "gateway": ports[1]})
            check(f"replica healthy at the old commit (problems: {bad or 'none'})", not bad)
            check("replica app at the source's commit", head(R / "jarvis-app") == old_app)
            check("replica has its platform database before the upgrade", db_exists(slug))
            if not bad:
                bad_sha, wt = broken_commit()
                section_e(m, slug, R, work, bad_sha)
        finally:
            if wt:
                sh("git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt))
                sh("git", "-C", str(ROOT), "branch", "-D", "canary/upgrade-broken")
            if not a.keep and Path(f"/opt/{slug}").exists():
                r = sh(PY, str(ROOT / "scripts" / "provision_tenant.py"), "--teardown", slug, "--yes")
                print(f"── teardown {slug}: rc={r.returncode}")
        print(f"\n{PASS}/{PASS + FAIL} passed")
        return 1 if FAIL else 0
    try:
        build_replica(src, slug, sub)
        print("── A. the replica runs the old version")
        bad = pt.wait_healthy(slug, {"app": ports[0], "gateway": ports[1]})
        check(f"old-layout replica healthy (problems: {bad or 'none'})", not bad)
        R = Path(f"/opt/{slug}")
        check("replica app at the source's commit", head(R / "jarvis-app") == old_app)
        check("replica skills at the source's commit", head(R / "skills/openclaw-imports") == old_skills)
        check("replica keeps threads in shared hermes/state (the old layout)",
              (R / "hermes/state/jarvis_threads.db").exists() and not (R / "app-private/jarvis_threads.db").exists())

        print("── B. seed")
        seed(slug, src, ports[0])
        before = ledger(slug)
        check(f"seeded: 3 tasks (got {before['tasks'].get('tasks')})", before["tasks"].get("tasks") == 3)
        check(f"seeded: 2 leads (got {before['leads'].get('leads')})", before["leads"].get("leads") == 2)
        check(f"seeded: a thread and a counter in the shared threads copy (got {before['threads']})",
              before["threads"].get("threads", 0) >= 1 and before["threads"].get("counters", 0) >= 1)
        check(f"seeded: 1 account in the app-private accounts database (got {before['accounts'].get('accounts')})",
              before["accounts"].get("accounts") == 1)
        units_before = unit_bytes(slug)

        print("── C. canary: a commit that cannot start rolls back by itself")
        bad_sha, wt = broken_commit()
        r = run_upgrade({**m, "app_origin_sha": bad_sha}, work / "broken.yaml")
        log = (work / "broken.log").read_text()
        check(f"upgrade to the broken commit exits non-zero (rc={r.returncode})", r.returncode != 0)
        check("it said it was rolling back", "rolling back" in log)
        check("rollback reports the old version healthy", "old version healthy again" in log)
        check("app back at the old commit", head(R / "jarvis-app") == old_app)
        check("skills back at the old commit", head(R / "skills/openclaw-imports") == old_skills)
        check("unit files byte-identical to before", unit_bytes(slug) == units_before)
        check("no platform database left behind", not db_exists(slug))
        check("no pg_hba rule left behind", not Path(f"/etc/postgresql/16/main/hba.d/{slug}.conf").exists())
        gone = lost(before, ledger(slug))
        check(f"no table lost rows and every seeded file is back (lost: {gone or 'none'})", not gone)
        bad = pt.tenant_healthy(slug, {"app": ports[0], "gateway": ports[1]})
        check(f"old version healthy now (problems: {bad or 'none'})", not bad)

        print("── D. the real upgrade")
        earlier = set(Path(f"/root/backups/{slug}").glob("upgrade-*"))
        r = run_upgrade(m, work / "upgrade.yaml")
        log = (work / "upgrade.log").read_text()
        check(f"upgrade exits 0 (rc={r.returncode})", r.returncode == 0)
        if r.returncode:
            print("\n".join(log.splitlines()[-15:]))
        check("it reported posture blocked and JARVIS_ALLOW_WRITES unset before restarting",
              "JARVIS_ALLOW_WRITES unset on app, gateway and env file" in log)
        check("app at the new commit", head(R / "jarvis-app") == new_app)
        check("skills at the new commit", head(R / "skills/openclaw-imports") == new_skills)
        after = ledger(slug)
        gone = lost(before, after)
        check(f"no table lost rows, skill data files byte-identical (lost: {gone or 'none'})", not gone)
        check(f"seeded rows still there: 3 tasks, 2 leads, 1 account (got {after['tasks'].get('tasks')}, "
              f"{after['leads'].get('leads')}, {after['accounts'].get('accounts')})",
              (after["tasks"].get("tasks"), after["leads"].get("leads"), after["accounts"].get("accounts"))
              == (3, 2, 1))
        th = R / "app-private/jarvis_threads.db"
        st = th.stat() if th.exists() else None
        check("threads database now app-private, app-owned, 600",
              bool(st) and sh("id", "-nu", str(st.st_uid)).stdout.strip() == f"jarvis-{slug}"
              and (st.st_mode & 0o777) == 0o600)
        check("no threads database left in shared state", not (R / "hermes/state/jarvis_threads.db").exists())
        r2 = sh("runuser", "-u", f"jarvis-{slug}-agent", "--", "test", "-r", str(th))
        check("the agent cannot read the threads database", r2.returncode != 0)
        r3 = sh("runuser", "-u", f"jarvis-{slug}-agent", "--", "test", "-r", str(R / "hermes/state/jarvis_tasks.db"))
        check("presence twin: the agent can read the shared tasks database", r3.returncode == 0)
        dropins = {p.name for p in pt.UNIT_DIR.glob(f"{slug}-*.service.d/*.conf")}
        check(f"old hand-made drop-ins gone (now {sorted(dropins)})",
              "writes.conf" not in dropins and "10-tree.conf" not in dropins)
        snaps = [d for d in sorted(set(Path(f"/root/backups/{slug}").glob("upgrade-*")) - earlier)
                 if (d / "opt.tar").exists()]
        snap = snaps[-1] if snaps else Path("/nonexistent")
        check("old drop-ins archived in the snapshot", any((snap / "old-dropins").glob("*writes.conf")))
        feed = R / "hermes/state/activity_feed.jsonl"
        fst = feed.stat() if feed.exists() else None
        check("activity feed exists agent:group 660",
              bool(fst) and (fst.st_mode & 0o777) == 0o660
              and sh("id", "-nu", str(fst.st_uid)).stdout.strip() == f"jarvis-{slug}-agent"
              and sh("getent", "group", str(fst.st_gid)).stdout.startswith(f"{slug}:"))
        check("platform database created", db_exists(slug))
        for u in ("app", "gateway"):
            check(f"{slug}-{u} has no JARVIS_ALLOW_WRITES", not pt.unit_env_has(f"{slug}-{u}.service",
                                                                                "JARVIS_ALLOW_WRITES"))
        check("snapshot tarballs are root 600", all((snap / f).exists() and (snap / f).stat().st_mode & 0o777 == 0o600
                                                    for f in ("opt.tar", "units.tar")))
        p = sh(PY, str(ROOT / "scripts" / "prove_isolation.py"), "--slug", slug)
        tail = (p.stdout.strip().splitlines() or [""])[-1]
        check(f"prove_isolation.py on the upgraded replica: {tail}", p.returncode == 0)
        if p.returncode:
            print("\n".join(ln for ln in p.stdout.splitlines() if "FAIL" in ln))
        section_e(m, slug, R, work, bad_sha)
    finally:
        if wt:
            sh("git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt))
            sh("git", "-C", str(ROOT), "branch", "-D", "canary/upgrade-broken")
        if not a.keep and Path(f"/opt/{slug}").exists():
            r = sh(PY, str(ROOT / "scripts" / "provision_tenant.py"), "--teardown", slug, "--yes")
            print(f"── teardown {slug}: rc={r.returncode}")
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
