#!/usr/bin/env python3
"""Turns invite sign-ups into accounts. Runs as root (jarvis-signup.service).

Every few seconds it takes the oldest queued sign-up from the front door's
ledger and, under the agent lock:

  1. checks there is room (MemAvailable and an account cap); if not, the
     sign-up waits as `full` and is retried when room appears;
  2. writes a manifest from scripts/templates/selfserve-manifest.yaml, with the
     app and skills pins of the practice template (docs/tenants/eddie.yaml);
  3. runs provision_tenant.py --manifest on it;
  4. makes the owner's login: their Google address, role owner (and a PIN in a
     root-only file under /root/onboarding for loopback proofs);
  5. opens the route, so <slug>.yamin.io reaches the new app.

Anything that fails leaves the sign-up `failed`, counted and logged, and the
half-made account in place for a human: this never deletes.

The provisioner prints the operator passphrase once; it is written to
/root/onboarding/<slug>-operator.pass (root 600) and replaced in the log.

    signup_worker.py            # loop (the service)
    signup_worker.py --once     # one pass, for a human
"""
import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from front import store  # noqa: E402

log = logging.getLogger("jarvis.signup")
TEMPLATE = REPO / "scripts" / "templates" / "selfserve-manifest.yaml"
PINS_FROM = Path(os.environ.get("SIGNUP_PINS_FROM", str(REPO / "docs" / "tenants" / "eddie.yaml")))
STATE = Path(os.environ.get("SIGNUP_STATE", "/var/lib/jarvis-signup"))
ONBOARDING = Path("/root/onboarding")
PYTHON = "/opt/jarvis/venv/bin/python"
MIN_FREE_MB = int(os.environ.get("SIGNUP_MIN_FREE_MB", "700"))
MAX_ACCOUNTS = int(os.environ.get("SIGNUP_MAX_ACCOUNTS", "3"))
BASE = os.environ.get("FRONT_BASE_DOMAIN", "yamin.io")
FRONT = os.environ.get("SIGNUP_OAUTH_FRONT", f"https://join.{BASE}")
APP_PORTS = range(8800, 9000, 10)


def free_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def made_accounts(conn) -> int:
    return conn.execute("SELECT count(*) FROM signups WHERE status IN ('ready','provisioning')").fetchone()[0]


def room(conn, pending: int = 0) -> str:
    """'' when there is room, else why not. `pending` counts a sign-up not yet
    claimed (the one being handled is already `provisioning`)."""
    if made_accounts(conn) + pending > MAX_ACCOUNTS:
        return f"account cap {MAX_ACCOUNTS} reached"
    mb = free_mb()
    if mb < MIN_FREE_MB:
        return f"only {mb} MB free (needs {MIN_FREE_MB})"
    return ""


def pins() -> dict:
    text = PINS_FROM.read_text()
    out = {}
    for key in ("app_origin_sha", "skills_origin_sha"):
        m = re.search(rf"^{key}:\s*'?([0-9a-f]{{40}})'?\s*$", text, re.M)
        if not m:
            raise RuntimeError(f"{PINS_FROM} has no {key}")
        out[key] = m.group(1)
    return out


def ports_in_use() -> set:
    used = set()
    for f in list((REPO / "docs" / "tenants").glob("*.yaml")) + list((STATE / "manifests").glob("*.yaml")):
        used.update(int(p) for p in re.findall(r"^\s+(?:app|gateway|hooks):\s*(\d+)\s*$", f.read_text(), re.M))
    out = subprocess.run(["ss", "-Hltn"], capture_output=True, text=True).stdout
    used.update(int(m) for m in re.findall(r":(\d+)\s", out))
    return used


def pick_ports() -> dict:
    used = ports_in_use()
    for i, app in enumerate(APP_PORTS):
        cand = {"app": app, "gateway": app + 2, "hooks": 18800 + i}
        if not used & set(cand.values()):
            return cand
    raise RuntimeError("no free port block")


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def write_manifest(su: dict) -> Path:
    p, ports = pins(), pick_ports()
    owner = su["owner_name"].strip() or "Owner"
    agent = f"{owner.split()[0]}'s Assistant"
    values = {
        "SLUG": su["slug"], "DISPLAY_NAME": _q(su["business_name"]),
        "DESCRIPTION": _q(f"{su['business_name']}: self-serve Jarvis, practice mode"),
        "AGENT_NAME": _q(agent), "OWNER_NAME": _q(owner), "OWNER_EMAIL": _q(su["email"]),
        "APP_PORT": str(ports["app"]), "GATEWAY_PORT": str(ports["gateway"]), "HOOKS_PORT": str(ports["hooks"]),
        "PUBLIC_ORIGIN": f"https://{su['slug']}.{BASE}", "OAUTH_FRONT": FRONT,
        "SKILLS_SHA": p["skills_origin_sha"], "APP_SHA": p["app_origin_sha"],
    }
    text = TEMPLATE.read_text()
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    if "{{" in text:
        raise RuntimeError("manifest template has an unfilled placeholder")
    out = STATE / "manifests" / f"{su['slug']}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return out


def _write_secret(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def provision(slug: str, manifest: Path) -> int:
    """Runs the provisioner; its output goes to a root-only log with the
    one-time passphrase moved to its own root-only file."""
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "agent_lock.py"), "run", "--who", "signup-worker",
                        "--why", f"self-serve account {slug}", "--", PYTHON,
                        str(REPO / "scripts" / "provision_tenant.py"), "--manifest", str(manifest)],
                       cwd=str(REPO), capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    lines, passphrase = out.splitlines(), None
    for i, line in enumerate(lines):
        if "OWNER PASSPHRASE" in line:
            for j in range(i + 1, min(i + 4, len(lines))):
                if lines[j].strip():
                    passphrase = lines[j].strip()
                    lines[j] = "      <written to /root/onboarding, not logged>"
                    break
    if passphrase:
        _write_secret(ONBOARDING / f"{slug}-operator.pass", passphrase + "\n")
    logs = STATE / "logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(logs / f"{slug}.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write("\n".join(lines) + f"\n# rc={r.returncode}\n")
    return r.returncode


def as_app_user(slug: str, *args: str, out_file: Path = None) -> int:
    opt = f"/opt/{slug}"
    cmd = ["/usr/sbin/runuser", "-u", f"jarvis-{slug}", "--", "env", "-i", "PATH=/usr/bin:/bin",
           "PYTHONDONTWRITEBYTECODE=1", f"HOME={opt}/app-private",
           f"JARVIS_ACCOUNTS_DB={opt}/app-private/jarvis_accounts.db", f"HERMES_HOME={opt}/hermes",
           f"JARVIS_SKILLS_ROOT={opt}/skills/openclaw-imports",
           f"JARVIS_IDENTITY_CONFIG={opt}/skills/openclaw-imports/_shared/identity.json",
           f"JARVIS_RUNTIME_CONFIG={opt}/skills/openclaw-imports/_shared/runtime.json",
           PYTHON, "-m", "app.accounts_cli", *args]
    r = subprocess.run(cmd, cwd=f"{opt}/jarvis-app", capture_output=True, text=True)
    if out_file is not None and r.returncode == 0:
        _write_secret(out_file, r.stdout)
    return r.returncode


def make_owner(su: dict) -> None:
    slug = su["slug"]
    if as_app_user(slug, "set-email", "owner", su["email"]):
        raise RuntimeError("set-email failed")
    if as_app_user(slug, "promote", "owner", "--role", "owner"):
        raise RuntimeError("promote failed")
    pin_file = ONBOARDING / f"{slug}-owner-pin.txt"
    if not pin_file.exists() and as_app_user(slug, "set-pin", "owner", "--role", "owner", out_file=pin_file):
        raise RuntimeError("set-pin failed")


def healthy(port: int) -> bool:
    r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://127.0.0.1:{port}/api/ping"],
                       capture_output=True, text=True)
    return r.stdout.strip() == "200"


def handle(conn, su: dict) -> None:
    sid, slug = su["id"], su["slug"]
    why = room(conn)
    if why:
        store.set_status(conn, sid, "full", why)
        store.bump(conn, "signup_full")
        log.warning("sign-up %s (%s) waits: %s", sid, slug, why)
        return
    try:
        manifest = write_manifest(su)
        rc = provision(slug, manifest)
        if rc == 2:
            store.set_status(conn, sid, "queued", "agent lock busy; retrying")
            store.bump(conn, "signup_lock_busy")
            log.info("sign-up %s: agent lock busy, back in the queue", sid)
            return
        if rc != 0:
            raise RuntimeError(f"provisioner rc={rc} (see {STATE}/logs/{slug}.log)")
        make_owner(su)
        port = int(re.search(r"^  app:\s*(\d+)", manifest.read_text(), re.M).group(1))
        if not healthy(port):
            raise RuntimeError(f"app on {port} is not answering /api/ping")
        store.put_route(conn, slug, port, su["email"])
        store.set_status(conn, sid, "ready")
        store.bump(conn, "signup_ready")
        log.info("sign-up %s: %s is ready on port %d", sid, slug, port)
    except Exception as exc:
        store.set_status(conn, sid, "failed", f"{type(exc).__name__}: {exc}")
        store.bump(conn, "signup_failed")
        log.error("sign-up %s (%s) failed: %s", sid, slug, exc)


def requeue_full(conn) -> None:
    if room(conn, pending=1):
        return
    row = conn.execute("SELECT id FROM signups WHERE status='full' ORDER BY created_at LIMIT 1").fetchone()
    if row:
        store.set_status(conn, row["id"], "queued", "room again")
        store.bump(conn, "signup_requeued")


def once() -> bool:
    conn = store.connect()
    requeue_full(conn)
    su = store.claim_next(conn)
    if su:
        handle(conn, su)
    return bool(su)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=5)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if os.geteuid() != 0:
        print("REFUSED: the account worker runs as root", file=sys.stderr)
        return 2
    if a.once:
        once()
        return 0
    while True:
        try:
            if once():
                continue
        except Exception:
            log.exception("signup worker pass failed")
        time.sleep(a.every)


if __name__ == "__main__":
    raise SystemExit(main())
