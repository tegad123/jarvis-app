#!/usr/bin/env python3
"""Stamp /opt/{slug}/ from a tenant ops manifest (slice 2).

Does not edit live units. Does not pip install. Does not apply cloudflared
unless --apply-ingress (not implemented: that flag prints and stops).
Skills clone only from the slice-0 bare origin — never cp from a working tree.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml
from cryptography.fernet import Fernet

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TEMPLATES = HERE / "templates"
# Retired: packing list lives in the skills clone as LOADOUTS.yaml.
LEGACY_LOADOUTS_FILE = HERE / "loadouts.yaml"

SKILLS_ORIGIN = Path("/root/git/openclaw-imports.git")
# A copy of the live venv: a pip install here does not reach live, and tenant
# users can run it (/root is not reachable under ProtectHome).
PLATFORM_VENV = Path("/opt/jarvis/venv/bin/python")
HERMES_PYTHON = Path("/usr/local/lib/hermes-agent/venv/bin/python")
OAUTH_CLIENT = Path("/root/.hermes/yamin_oauth_client.json")
ETC_JARVIS = Path("/etc/jarvis")
# Root-only platform copy of the OAuth client. Two-user tenants get their own
# app-only copy of it; nothing reads /root/.hermes.
PLATFORM_OAUTH_CLIENT = ETC_JARVIS / "oauth_client.json"
ISOLATIONS = ("two_user", "none")
SENTINEL_MODES = ("shadow", "enforce")
SWITCHES = ("broker", "sentinel", "model_proxy")
DEFAULT_AGENT_MODEL = "claude-sonnet-5-5"
DEFAULT_BACKGROUND_MODEL = "claude-haiku-5-5"
MODEL_ID = re.compile(r"claude-[a-z0-9.-]{1,60}")
# Approvals are not here: they live in the tenant's Postgres database, which
# the agent user cannot connect to (a tricked agent must not approve itself).
SHARED_STATE = ("jarvis_tasks.db", "jarvis_leads.db", "activity_feed.jsonl")

RESERVED_SLUGS = frozenset({"testco", "root", "live", "34dev"})
RFC2606 = (".example", ".test", ".invalid", ".localhost",
           "example.com", "example.net", "example.org")
SWEEPS = ("stale_email", "task_followup", "quiet_lead")
SWEEP_BOOT_DEFAULTS = (180, 240, 300)

UNIT_DIR = Path("/etc/systemd/system")


class Refuse(SystemExit):
    """Hard stop. Never improvise past a fence."""


def die(msg: str, code: int = 2) -> None:
    print(f"REFUSED: {msg}", file=sys.stderr)
    raise Refuse(code)


def log(msg: str) -> None:
    print(f"provision: {msg}", flush=True)


def fill(text: str, mapping: Dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in mapping:
            die(f"template placeholder {{{{{key}}}}} has no value")
        return mapping[key]
    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", repl, text)


def read_template(name: str) -> str:
    path = TEMPLATES / name
    if not path.is_file():
        die(f"missing template {path}")
    return path.read_text()


def run(cmd: List[str], **kw: Any) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)


def load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text())
    except Exception as exc:
        die(f"cannot parse {path}: {exc}")
    if not isinstance(data, dict):
        die(f"{path} is not a mapping")
    return data


def listed_skill_dirs(tree: Path) -> List[str]:
    return sorted(
        p.name for p in tree.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def load_loadout(skills_clone: Path, name: str) -> dict:
    """Read LOADOUTS.yaml from the cloned skills tree. Never from jarvis-app."""
    if skills_clone is None:
        die(
            "LOADOUTS.yaml is read from the cloned skills tree at "
            "skills_origin_sha, not from jarvis-app. "
            f"{LEGACY_LOADOUTS_FILE} is retired."
        )
    path = skills_clone / "LOADOUTS.yaml"
    if not path.is_file():
        die(
            f"LOADOUTS.yaml missing from skills clone {skills_clone}. "
            "The packing list versions with the skills repo. "
            f"{LEGACY_LOADOUTS_FILE} is retired and is not a fallback."
        )
    data = load_yaml(path)
    if data.get("retired"):
        die(f"{path} is a retirement stub, not a loadout")
    loadout_name = name or "base"
    if loadout_name != "base":
        die(f"unknown skill_loadout {loadout_name!r}; only 'base' plus skill_loadout_optional")
    base = list(data.get("base") or [])
    optional = list(data.get("optional") or [])
    excluded = list(data.get("excluded") or [])
    if not base:
        die(f"loadout file {path} has empty base")
    return {"base": base, "optional": optional, "excluded": excluded, "source": str(path)}


def assert_completeness(skills_clone: Path, loadout: dict) -> List[str]:
    """FAIL if a skill dir exists in no bucket, or a name is in two buckets.

    Excluded names may be absent (deny-list for live-only custom skills).
    Base and optional names must exist as directories.
    """
    dirs = listed_skill_dirs(skills_clone)
    buckets = {
        "base": list(loadout["base"]),
        "optional": list(loadout["optional"]),
        "excluded": list(loadout["excluded"]),
    }
    owner: Dict[str, str] = {}
    for bucket, names in buckets.items():
        for n in names:
            if n in owner:
                die(f"loadout name {n!r} is in both {owner[n]} and {bucket}")
            owner[n] = bucket
    unaccounted = [d for d in dirs if d not in owner]
    if unaccounted:
        die(
            "skill dirs not in any LOADOUTS.yaml bucket "
            f"(new skills must be classified): {unaccounted}"
        )
    missing_base = [s for s in buckets["base"] if s not in dirs]
    if missing_base:
        die(f"base loadout names missing from skills tree: {missing_base}")
    missing_opt = [s for s in buckets["optional"] if s not in dirs]
    if missing_opt:
        die(f"optional loadout names missing from skills tree: {missing_opt}")
    absent_excl = [s for s in buckets["excluded"] if s not in dirs]
    log(
        "LOADOUT COMPLETENESS PASS: "
        f"{len(dirs)} dirs classified "
        f"(base={len(buckets['base'])}, optional={len(buckets['optional'])}, "
        f"excluded={len(buckets['excluded'])} absent_ok={absent_excl})"
    )
    return dirs


def check_slug(slug: str) -> None:
    if not slug or not isinstance(slug, str):
        die("slug is required")
    if slug != slug.strip() or slug != slug.lower():
        die(f"slug must be lowercase stripped, got {slug!r}")
    if slug in RESERVED_SLUGS:
        die(f"slug {slug!r} is reserved")
    if "/" in slug or "\\" in slug or ".." in slug or slug.startswith("."):
        die(f"slug {slug!r} is a path escape")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", slug):
        die(f"slug {slug!r} is not a hostname label")


def _unit_env_files() -> List[Path]:
    files: List[Path] = []
    if not UNIT_DIR.is_dir():
        return files
    for p in UNIT_DIR.rglob("*"):
        if p.is_file() and (p.suffix in {".service", ".timer", ".conf"} or ".service.d" in p.parts):
            files.append(p)
    return files


def isolation_names(slug: str) -> Dict[str, str]:
    names = {"app_user": f"jarvis-{slug}", "agent_user": f"jarvis-{slug}-agent", "group": slug}
    if len(names["agent_user"]) > 32:
        die(f"slug {slug!r} is too long for the two-user layout (user {names['agent_user']} > 32 chars)")
    return names


def _getent(db: str, name: str) -> Optional[str]:
    r = subprocess.run(["getent", db, name], capture_output=True, text=True, check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def identity_collisions(slug: str) -> List[str]:
    n = isolation_names(slug)
    hits = [f"user exists: {n[k]}" for k in ("app_user", "agent_user") if _getent("passwd", n[k])]
    if _getent("group", n["group"]):
        hits.append(f"group exists: {n['group']}")
    env_file = ETC_JARVIS / f"{slug}-app.env"
    if env_file.exists():
        hits.append(f"secrets file exists: {env_file}")
    return hits


def create_identities(slug: str) -> Dict[str, str]:
    n = isolation_names(slug)
    nologin = "/usr/sbin/nologin"
    run(["groupadd", "--system", n["group"]])
    run(["useradd", "--system", "--user-group", "--no-create-home", "--shell", nologin,
         "--home-dir", f"/opt/{slug}/app-private", "--groups", n["group"], n["app_user"]])
    run(["useradd", "--system", "--user-group", "--no-create-home", "--shell", nologin,
         "--home-dir", f"/opt/{slug}/hermes", "--groups", n["group"], n["agent_user"]])
    return n


def mint_isolated_secrets(slug: str, hermes_home: Path) -> str:
    """App secrets into /etc/jarvis/{slug}-app.env (root 600, read by systemd
    before the user switch); the agent's hermes/.env gets API_SERVER_KEY only."""
    env_file = ETC_JARVIS / f"{slug}-app.env"
    if env_file.exists() or (hermes_home / ".env").exists():
        die(f"{env_file} or {hermes_home / '.env'} already exists — refusing to rotate secrets")
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    passphrase = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
    api_key = secrets.token_urlsafe(32)
    ETC_JARVIS.mkdir(mode=0o750, exist_ok=True)
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"# {slug}-app secrets. root 600; read by systemd before the user switch.\n"
                 f"APP_GOOGLE_TOKEN_KEY={Fernet.generate_key().decode('ascii')}\n"
                 f"APP_PASSPHRASE={passphrase}\n"
                 f"APP_SESSION_SECRET={secrets.token_hex(32)}\n"
                 f"API_SERVER_KEY={api_key}\n")
    # provision_client mints (and prints) any APP_* key missing from hermes/.env,
    # so the app keys sit there until trim_agent_env() runs after it.
    session = next(ln.split("=", 1)[1] for ln in env_file.read_text().splitlines()
                   if ln.startswith("APP_SESSION_SECRET="))
    fd = os.open(hermes_home / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"API_SERVER_KEY={api_key}\nAPP_PASSPHRASE={passphrase}\nAPP_SESSION_SECRET={session}\n")
    log(f"minted APP_GOOGLE_TOKEN_KEY, APP_PASSPHRASE, APP_SESSION_SECRET, API_SERVER_KEY into {env_file} "
        f"(root 600)")
    return passphrase


def trim_agent_env(hermes_home: Path) -> None:
    """Leave the agent's hermes/.env holding API_SERVER_KEY and, as
    ANTHROPIC_API_KEY, the same value: the stand-in Hermes sends to the app's
    model proxy. No real model key is ever written here."""
    env = hermes_home / ".env"
    keep = [ln for ln in env.read_text().splitlines() if ln.startswith("API_SERVER_KEY=")]
    if len(keep) != 1:
        die(f"expected exactly one API_SERVER_KEY line in {env}, found {len(keep)}")
    env.write_text(keep[0] + "\n" + "ANTHROPIC_API_KEY=" + keep[0].split("=", 1)[1] + "\n")
    os.chmod(env, 0o600)
    keys = [ln.split("=", 1)[0] for ln in env.read_text().splitlines() if "=" in ln]
    if keys != ["API_SERVER_KEY", "ANTHROPIC_API_KEY"]:
        die(f"{env} still holds {keys} after trimming")
    log(f"{env}: API_SERVER_KEY and its model-proxy stand-in only")


def apply_isolation(opt: Path, n: Dict[str, str], data_dirs: List[Path], keep_private: bool = False) -> None:
    """Ownership and modes for the two-user layout. Code is root:{group} and
    read-only to both users; the app owns app-private and jarvis-app/data; the
    agent owns the hermes home; state/, outbox/ and skill data/ are shared
    setgid group dirs. keep_private (upgrade): the app's databases and OAuth
    client are created only if missing, never replaced; a refresh token is
    only valid with the client that issued it."""
    app, agent, g = n["app_user"], n["agent_user"], n["group"]
    jarvis, skills, hermes, private = opt / "jarvis-app", opt / "skills", opt / "hermes", opt / "app-private"
    run(["chown", f"root:{g}", str(opt)])
    os.chmod(opt, 0o750)
    for f in opt.iterdir():
        if f.is_file():
            run(["chown", f"root:{g}", str(f)])
            os.chmod(f, 0o750 if f.suffix == ".sh" else 0o640)
    for tree in (jarvis, skills):
        run(["chown", "-R", f"root:{g}", str(tree)])
        run(["chmod", "-R", "g+rX,g-w,o-rwx", str(tree)])
    for p in (skills / "openclaw-imports" / "_shared").glob("*-provision.json"):
        run(["chown", "root:root", str(p)])
        os.chmod(p, 0o600)
    (jarvis / "data" / "tmp-transcribe").mkdir(parents=True, exist_ok=True)
    run(["chown", "-R", f"{app}:{app}", str(jarvis / "data")])
    run(["chmod", "-R", "u+rwX,go-rwx", str(jarvis / "data")])
    for d in data_dirs:
        d.mkdir(parents=True, exist_ok=True)
        run(["chown", "-R", f"root:{g}", str(d)])
        run(["find", str(d), "-type", "d", "-exec", "chmod", "2770", "{}", "+"])
        run(["find", str(d), "-type", "f", "-exec", "chmod", "660", "{}", "+"])
    run(["chown", "-R", "-h", f"{agent}:{agent}", str(hermes)])
    run(["chown", f"{agent}:{g}", str(hermes), str(hermes / "config.yaml")])
    os.chmod(hermes, 0o710)
    os.chmod(hermes / "config.yaml", 0o640)
    # SQLite creates databases 0644 whatever the umask, so a store first opened
    # by one user would be read-only to the other. Pre-create them 660.
    for name in SHARED_STATE:
        (hermes / "state" / name).touch(exist_ok=True)
    for d in ("state", "outbox"):
        run(["chown", "-R", f"{agent}:{g}", str(hermes / d)])
        run(["find", str(hermes / d), "-type", "d", "-exec", "chmod", "2770", "{}", "+"])
        run(["find", str(hermes / d), "-type", "f", "-exec", "chmod", "660", "{}", "+"])
    run(["chmod", "-R", "o-rwx", str(hermes)])
    os.chmod(hermes / ".env", 0o600)
    private.mkdir(mode=0o700, exist_ok=True)
    if not (keep_private and (private / "oauth_client.json").exists()):
        run(["install", "-m", "600", "-o", app, "-g", app, str(PLATFORM_OAUTH_CLIENT),
             str(private / "oauth_client.json")])
    # Created empty (a valid SQLite database) so it is app-only before first use.
    for name in ("jarvis_accounts.db", "jarvis_threads.db"):
        if not (keep_private and (private / name).exists()):
            run(["install", "-m", "600", "-o", app, "-g", app, "/dev/null", str(private / name)])
    if keep_private:
        run(["chown", "-R", f"{app}:{app}", str(private)])
        run(["chmod", "-R", "u+rwX,go-rwx", str(private)])
    run(["chown", f"{app}:{app}", str(private)])
    os.chmod(private, 0o700)
    log(f"two-user ownership applied: app={app} agent={agent} group={g}")


def structural_collisions(slug: str, ports: dict) -> List[str]:
    """Refuse leftover tree, unit files, or Environment= port claims. Not ss."""
    hits: List[str] = []
    opt = Path(f"/opt/{slug}")
    if opt.exists():
        hits.append(f"tree exists: {opt}")
    for p in sorted(UNIT_DIR.glob(f"{slug}-*")):
        hits.append(f"unit path exists: {p}")
    app = int(ports["app"])
    gw = int(ports["gateway"])
    hooks = int(ports["hooks"])
    patterns = [
        (re.compile(rf"(?m)^Environment=APP_PORT={app}\s*$"),
         f"APP_PORT={app}"),
        (re.compile(rf"(?m)^Environment=HERMES_API_URL=.*:{gw}\s*$"),
         f"HERMES_API_URL port {gw}"),
        (re.compile(rf"(?m)^Environment=.*(?:webhook|WEBHOOK|HOOKS).*{hooks}"),
         f"webhook/hooks port {hooks}"),
        (re.compile(rf"(?m)^Environment=.*:{hooks}\s*$"),
         f"Environment ends with :{hooks}"),
    ]
    for path in _unit_env_files():
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for rx, label in patterns:
            if rx.search(text):
                hits.append(f"{label} in {path}")
    return hits


def ss_listen(port: int) -> bool:
    r = subprocess.run(
        ["ss", "-ltn"], capture_output=True, text=True, check=False,
    )
    return bool(re.search(rf":{port}\s", r.stdout or ""))


def another_gateway_running() -> bool:
    r = subprocess.run(
        ["pgrep", "-af", "hermes_cli.main gateway"],
        capture_output=True, text=True, check=False,
    )
    return bool((r.stdout or "").strip())


def suffixes_reserved(suffixes: List[str]) -> List[str]:
    bad = []
    for s in suffixes:
        s = s.strip().lower()
        if not s:
            continue
        if not any(s == r or s.endswith(r) for r in RFC2606):
            bad.append(s)
    return bad


def posture_rules(manifest: dict) -> Dict[str, Any]:
    posture = manifest.get("writes_posture")
    if posture not in ("blocked", "captured", "live"):
        die("writes_posture must be blocked | captured | live")
    safety = manifest.get("safety") or {}
    if safety.get("allow_writes") is True:
        die("safety.allow_writes:true is refused at stamp time; arm via JARVIS_ALLOW_WRITES=1 after cutover")
    suffixes = list((safety.get("outbound_recipient_suffix") or []))
    origin = str(manifest.get("public_origin") or "")
    loopback = origin.startswith("http://127.0.0.1")
    https = origin.startswith("https://")

    writes_armed = posture == "captured"
    if posture == "captured":
        if not suffixes:
            die("writes_posture=captured requires RFC 2606 outbound_recipient_suffix")
        bad = suffixes_reserved(suffixes)
        if bad:
            die(f"writes_posture=captured but suffixes are not reserved: {bad}")
        if not loopback and https:
            die("writes_posture=captured with an https public_origin is a writes-guard-off / capture mismatch")
    if posture == "live":
        bad = [s for s in suffixes if any(
            s.strip().lower() == r or s.strip().lower().endswith(r) for r in RFC2606
        )]
        if bad:
            die(f"writes_posture=live must not carry RFC 2606 capture suffixes {bad}")
        if not https:
            die("writes_posture=live requires https public_origin")

    # Scratch (captured + loopback HTTP): cookie 0 so PIN login works.
    # Production templates (blocked | live): cookie 1 even on a loopback drill.
    if posture == "captured":
        cookie = "0"
        if not loopback:
            die("writes_posture=captured requires a loopback public_origin (APP_COOKIE_SECURE=0)")
    else:
        cookie = "1"
        if posture == "live" and not https:
            die("writes_posture=live requires https public_origin")

    return {
        "posture": posture,
        "writes_armed": writes_armed,
        "cookie": cookie,
        "loopback": loopback,
        "oauth_template": "oauth.scratch.conf" if posture == "captured" else "oauth.production.conf",
        "suffixes": suffixes,
    }


def mint_env(hermes_home: Path) -> str:
    """Mint APP_PASSPHRASE, APP_SESSION_SECRET, API_SERVER_KEY. Print passphrase to TTY only."""
    env_path = hermes_home / ".env"
    if env_path.exists():
        die(f"{env_path} already exists — refusing to rotate secrets")
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    passphrase = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
    session = secrets.token_hex(32)
    api_key = secrets.token_urlsafe(32)
    lines = [
        "# Per-tenant secrets, generated at provisioning. Never imaged.",
        f"APP_PASSPHRASE={passphrase}",
        f"APP_SESSION_SECRET={session}",
        f"API_SERVER_KEY={api_key}",
        "",
    ]
    env_path.write_text("\n".join(lines))
    os.chmod(env_path, 0o600)
    log(f"minted APP_PASSPHRASE, APP_SESSION_SECRET, API_SERVER_KEY into {env_path} (mode 600)")
    return passphrase


def print_passphrase_once(passphrase: str) -> None:
    """Operator tty only. The verify report redacts this line."""
    banner = (
        "\n"
        "  " + "!" * 62 + "\n"
        "  OWNER PASSPHRASE — SHOWN ONCE, NOT WRITTEN TO THE TREE.\n"
        "\n"
        f"      {passphrase}\n"
        "\n"
        "  " + "!" * 62 + "\n"
    )
    sys.stdout.write(banner)
    sys.stdout.flush()


def clone_app(dest: Path, sha: str) -> None:
    src = REPO
    log(f"cloning jarvis-app from {src} at {sha}")
    run(["git", "clone", str(src), str(dest)])
    run(["git", "-C", str(dest), "checkout", "--detach", sha])
    got = subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
    full = subprocess.check_output(["git", "-C", str(src), "rev-parse", sha], text=True).strip()
    if got != full:
        die(f"jarvis-app clone HEAD {got} != requested {full}")
    log(f"jarvis-app at {got}")


def clone_skills(dest: Path, sha: str) -> None:
    if not SKILLS_ORIGIN.exists():
        die(f"slice 0 origin missing at {SKILLS_ORIGIN} — will not clone from a working tree")
    log(f"cloning skills from {SKILLS_ORIGIN} at {sha} (never cp from a working tree)")
    run(["git", "clone", str(SKILLS_ORIGIN), str(dest)])
    run(["git", "-C", str(dest), "checkout", "--detach", sha])
    got = subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
    full = subprocess.check_output(
        ["git", "--git-dir", str(SKILLS_ORIGIN), "rev-parse", sha], text=True
    ).strip()
    if got != full:
        die(f"skills clone HEAD {got} != requested {full}")
    log(f"skills at {got}")


def prune_to_loadout(skills_clone: Path, loadout: dict, optional_enables: List[str]) -> List[str]:
    assert_completeness(skills_clone, loadout)
    extra = list(optional_enables or [])
    unknown = [s for s in extra if s not in loadout["optional"]]
    if unknown:
        die(f"skill_loadout_optional names not in optional list: {unknown}")
    excluded_hit = [s for s in extra if s in loadout["excluded"]]
    if excluded_hit:
        die(f"excluded skills cannot be enabled: {excluded_hit}")
    keep = set(loadout["base"]) | set(extra)
    removable = set(loadout["optional"]) | set(loadout["excluded"])
    shipped: List[str] = []
    for child in sorted(skills_clone.iterdir()):
        if child.name.startswith(".") or not child.is_dir():
            continue
        if child.name not in keep:
            if child.name not in removable:
                die(
                    f"unclassified skill dir {child.name} survived completeness "
                    "and is not optional/excluded — refusing silent drop"
                )
            log(f"pruning skill dir {child.name} (not in this tenant's loadout)")
            shutil.rmtree(child)
            continue
        if child.name != "_shared":
            shipped.append(child.name)
    missing_core = [s for s in loadout["base"] if not (skills_clone / s).is_dir()]
    if missing_core:
        die(f"core loadout dirs missing after clone: {missing_core}")
    for bad in loadout["excluded"]:
        if (skills_clone / bad).exists():
            die(f"excluded skill {bad} still present after prune")
    expected = [s for s in loadout["base"] if s != "_shared"] + list(extra)
    if sorted(shipped) != sorted(expected):
        die(f"shipped {shipped} != base+optional {expected}")
    return shipped


def build_client_config(manifest: dict, paths: dict, shipped: List[str], loadout: dict) -> dict:
    slug = manifest["slug"]
    ports = manifest["ports"]
    identity_people = (manifest.get("people") or {})
    if not identity_people:
        die("manifest.people is required (provision_client needs an owner)")
    agent_name = manifest["agent_display_name"]
    assistant_addr = manifest["assistant_address"]
    people = {}
    for key, person in identity_people.items():
        p = dict(person)
        if "assistant" in (p.get("roles") or []):
            p["name"] = agent_name
            p["display_name"] = agent_name
            emails = list(p.get("email") or [])
            if assistant_addr not in emails:
                emails.append(assistant_addr)
            p["email"] = emails
        people[key] = p

    platform = manifest.get("platform") or {}
    destinations = manifest.get("destinations") or {}
    if not platform:
        die("manifest.platform is required")
    if not destinations:
        die("manifest.destinations is required")

    digest = manifest.get("digest") or {}
    sweeps = manifest.get("sweeps") or {}
    email_router = manifest.get("email_router") or {}
    email_monitor = manifest.get("email_monitor") or {}
    gpt = manifest.get("gmail_push_trigger") or {}
    cost_guard = manifest.get("cost_guard") or {}
    leads = manifest.get("leads") or {}
    tasks = manifest.get("tasks") or {}
    lead_reply = manifest.get("lead_reply") or {}
    gcal = manifest.get("google_calendar") or {}
    e2c = manifest.get("email_to_calendar") or {}
    meeting_intake = manifest.get("meeting_intake") or {}
    meeting_briefing = manifest.get("meeting_briefing") or {}
    voice = manifest.get("owner_email_voice") or {}
    feed = manifest.get("feed") or {}
    safety_in = manifest.get("safety") or {}
    schedule = manifest.get("schedule") or {}
    showingsmart = manifest.get("showingsmart_autoreply") or {}
    crm_weekly = manifest.get("crm_weekly_brief") or {}
    metrics = manifest.get("metrics") or {}

    optional_on = set(manifest.get("skill_loadout_optional") or [])
    skills: Dict[str, Any] = {}
    for name in shipped:
        enabled = name in loadout["base"] or name in optional_on
        skills[name] = {"enabled": bool(enabled)}
    # Historical runtime alias (directory is discord-recording-handler).
    if "discord-recording-handler" in skills:
        skills["recording-handler"] = dict(skills["discord-recording-handler"])

    tm: Dict[str, Any] = dict(skills.get("task-manager") or {"enabled": True})
    if "times_local" in digest:
        tm["digest_times_local"] = digest["times_local"]
    if "lookback_days" in digest:
        tm["digest_lookback_days"] = digest["lookback_days"]
    if "cutoff_hours" in digest:
        tm["digest_cutoff_hours"] = digest["cutoff_hours"]
    if "transport" in digest:
        tm["digest_transport"] = digest["transport"]
    if "default_assignee" in digest:
        tm["default_assignee"] = digest["default_assignee"]
    skills["task-manager"] = tm

    sw: Dict[str, Any] = dict(skills.get("sweeps") or {"enabled": True})
    for k in ("stale_email_hours", "email_grace_days", "task_quiet_hours",
              "task_escalate_after", "lead_quiet_days", "hot_lead_quiet_days",
              "lead_nudge_backoff_days", "min_interval_minutes", "lead_revival_hours"):
        if k in sweeps:
            sw[k] = sweeps[k]
    skills["sweeps"] = sw

    er: Dict[str, Any] = dict(skills.get("email-router") or {"enabled": True})
    for k in ("lookback_days", "recording_subject_prefix", "deal_channel_prefix"):
        if k in email_router:
            er[k] = email_router[k]
    skills["email-router"] = er

    em: Dict[str, Any] = dict(skills.get("email-monitor") or {"enabled": True})
    if "lookback_days" in email_monitor:
        em["lookback_days"] = email_monitor["lookback_days"]
    skills["email-monitor"] = em

    gpt_out: Dict[str, Any] = dict(skills.get("gmail-push-trigger") or {"enabled": True})
    gpt_out["webhook_port"] = int(ports["hooks"])
    for k in ("mailbox", "gcp_project", "pubsub_topic", "pubsub_subscription",
              "tunnel_hostname", "watchdog_lookback_hours", "poll_seconds"):
        if k in gpt:
            gpt_out[k] = gpt[k]
    skills["gmail-push-trigger"] = gpt_out

    cg: Dict[str, Any] = dict(skills.get("cost-guard") or {"enabled": True})
    for k in ("spend_warn_usd", "spend_pause_usd", "session_check_minutes",
              "context_check_minutes", "daily_spend_check_minutes", "prune_cron"):
        if k in cost_guard:
            cg[k] = cost_guard[k]
    skills["cost-guard"] = cg

    skills["leads"] = {
        "source": leads.get("source", "store"),
        "export_enabled": bool(leads.get("export_enabled", False)),
        "export_sheet_id": leads.get("export_sheet_id", ""),
    }
    skills["tasks"] = {"source": tasks.get("source", "store")}
    lr: Dict[str, Any] = dict(skills.get("lead-reply") or {"enabled": True})
    if "sheet_id" in lead_reply:
        lr["sheet_id"] = lead_reply["sheet_id"]
    if "exclude_tabs" in lead_reply:
        lr["exclude_tabs"] = lead_reply["exclude_tabs"]
    skills["lead-reply"] = lr

    gc: Dict[str, Any] = dict(skills.get("google-calendar") or {"enabled": True})
    if "default_calendar_id" in gcal:
        gc["default_calendar_id"] = gcal["default_calendar_id"]
    skills["google-calendar"] = gc

    e2: Dict[str, Any] = dict(skills.get("email-to-calendar") or {"enabled": True})
    for k in ("calendar_id", "always_attendee"):
        if k in e2c:
            e2[k] = e2c[k]
    skills["email-to-calendar"] = e2

    if "meeting-intake" in skills:
        mi = dict(skills["meeting-intake"])
        for k in ("subject_prefix", "max_transcript_chars"):
            if k in meeting_intake:
                mi[k] = meeting_intake[k]
        skills["meeting-intake"] = mi

    mb: Dict[str, Any] = dict(skills.get("meeting-briefing") or {"enabled": True})
    if "owner_person" in meeting_briefing:
        mb["owner_person"] = meeting_briefing["owner_person"]
    skills["meeting-briefing"] = mb

    vo: Dict[str, Any] = dict(skills.get("owner-email-voice") or {"enabled": True})
    if "voice_corpus_path" in voice:
        vo["voice_corpus_path"] = voice["voice_corpus_path"]
    skills["owner-email-voice"] = vo

    if "showingsmart-autoreply" in skills:
        ss = dict(skills["showingsmart-autoreply"])
        for k in ("past_window_hours", "default_duration_hours", "sheet_id", "sender"):
            if k in showingsmart:
                ss[k] = showingsmart[k]
        skills["showingsmart-autoreply"] = ss

    if "crm-weekly-brief" in skills and "local_time" in crm_weekly:
        skills["crm-weekly-brief"]["local_time"] = crm_weekly["local_time"]

    if metrics.get("retention_days") is not None:
        skills["metrics"] = {"retention_days": metrics["retention_days"]}

    client = {
        "slug": slug,
        "display_name": manifest["display_name"],
        "timezone": manifest["timezone"],
        "business_description": manifest.get("business_description") or "",
    }
    if manifest.get("sheet_id") not in (None,):
        client["sheet_id"] = manifest.get("sheet_id") or ""

    return {
        "client": client,
        "safety": {
            "allow_writes": False,
            "env_var": "JARVIS_ALLOW_WRITES",
            "outbound_recipient_suffix": list(safety_in.get("outbound_recipient_suffix") or []),
        },
        "gateway": {
            "base_url": f"http://127.0.0.1:{int(ports['gateway'])}",
            "api_key_env": "API_SERVER_KEY",
        },
        "paths": paths,
        "platform": platform,
        "identity": {
            "assistant_address": assistant_addr,
            "internal_domains": list(manifest.get("boundary_domains") or []),
            "enforcement": "strict",
            "people": people,
            "aliases": manifest.get("aliases") or {},
            "permissions": manifest.get("permissions") or {},
        },
        "destinations": destinations,
        "skills": skills,
        "app": {
            "feed": {
                "team_destinations": list(feed.get("team_destinations") or ["ops"]),
            }
        },
        "schedule": schedule,
        "credentials": {},
    }


def run_provision_client(skills_clone: Path, cfg: dict, hermes_home: Path, python: Path) -> bool:
    cfg_path = skills_clone / "_shared" / f"{cfg['client']['slug']}-provision.json"
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    os.chmod(cfg_path, 0o600)
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["HOME"] = str(hermes_home.parent)
    # Origin clones may still carry another tenant's identity/runtime (testco at
    # the slice-1 pin). This is a fresh /opt/{slug} stamp — drop those files so
    # provision_client writes this tenant without --force on a live tree.
    for name in ("identity.json", "channels.json", "runtime.json"):
        p = skills_clone / "_shared" / name
        if p.exists():
            log(f"removing clone-baked {name} before provision_client")
            p.unlink()
    log(f"running provision_client.py --config {cfg_path}")
    p = subprocess.run(
        [str(python), str(skills_clone / "_shared" / "provision_client.py"),
         "--config", str(cfg_path)],
        env=env, capture_output=True, text=True,
    )
    out = re.sub(r"(APP_PASSPHRASE:\s*)\S+", r"\1[REDACTED]", p.stdout + p.stderr)
    sys.stdout.write(out)
    if p.returncode != 0:
        die(f"provision_client.py exited {p.returncode}")
    minted = "[REDACTED]" in out
    if minted:
        log("provision_client minted and printed its own passphrase (redacted above)")
    return minted


def stamp_identity_mailbox(skills_clone: Path, manifest: dict) -> None:
    ident_path = skills_clone / "_shared" / "identity.json"
    ident = json.loads(ident_path.read_text())
    ident["mailbox_strategy"] = manifest["mailbox_strategy"]
    ident["agent_display_name"] = manifest["agent_display_name"]
    if ident.get("assistant_address") != manifest["assistant_address"]:
        die("identity.assistant_address != manifest after provision_client")
    ident_path.write_text(json.dumps(ident, indent=2) + "\n")
    log("stamped mailbox_strategy + agent_display_name onto identity.json")


def common_map(manifest: dict, posture: dict, fernet: str, force: str,
               shipped: Sequence[str] = ()) -> Dict[str, str]:
    slug = manifest["slug"]
    opt = f"/opt/{slug}"
    writes = "Environment=JARVIS_ALLOW_WRITES=1" if posture["writes_armed"] else ""
    iso: Dict[str, str] = {}
    if manifest.get("isolation") == "two_user":
        n = isolation_names(slug)
        iso = {"APP_USER": n["app_user"], "AGENT_USER": n["agent_user"], "GROUP": n["group"],
               "APP_PRIVATE": f"{opt}/app-private", "ENV_FILE": str(ETC_JARVIS / f"{slug}-app.env"),
               "SENTINEL_MODE": str(manifest.get("sentinel_mode") or ""),
               "PG_DSN": pg_dsn(slug),
               "DATA_DIRS": " ".join(str(d) for d in skill_data_dirs(Path(opt), shipped))}
    sw = switch_states(manifest)
    # A tenant without two-user isolation runs as root and has no platform
    # database, so Sentinel is off: writes and approvals refuse (503).
    no_db_line = "" if iso else "Environment=JARVIS_SWITCH_SENTINEL=off"
    return {
        **iso,
        "SLUG": slug,
        "DISPLAY_NAME": manifest["display_name"],
        "OPT": opt,
        "JARVIS_APP": f"{opt}/jarvis-app",
        "HERMES_HOME": f"{opt}/hermes",
        "SKILLS": f"{opt}/skills/openclaw-imports",
        "PREFLIGHT": f"{opt}/{slug}_preflight.sh",
        "VENV_PYTHON": str(venv_python(manifest)),
        "HERMES_PYTHON": str(HERMES_PYTHON),
        "APP_PORT": str(int(manifest["ports"]["app"])),
        "AGENT_MODEL": str(manifest.get("agent_model") or ""),
        "BACKGROUND_MODEL": str(manifest.get("background_model") or DEFAULT_BACKGROUND_MODEL),
        "SWITCH_BROKER": sw["broker"],
        "SWITCH_SENTINEL": sw["sentinel"],
        "SWITCH_MODEL_PROXY": sw["model_proxy"],
        "NO_PLATFORM_DB_LINE": no_db_line,
        "GATEWAY_PORT": str(int(manifest["ports"]["gateway"])),
        "HOOKS_PORT": str(int(manifest["ports"]["hooks"])),
        "WRITES_LINE": writes,
        "GATEWAY_FORCE": force,
        "PUBLIC_ORIGIN": str(manifest["public_origin"]).rstrip("/"),
        "COOKIE_SECURE": posture["cookie"],
        "FERNET_KEY": fernet,
        "OAUTH_CLIENT": str(OAUTH_CLIENT),
        "POSTURE": posture["posture"],
        "OAUTH_TEMPLATE": posture["oauth_template"],
        "BACKUP_ROOT": str((manifest.get("backup") or {}).get("destination")
                           or Path("/root/backups") / slug),
        "BACKUP_KEEP": str((manifest.get("backup") or {}).get("keep_daily") or 14),
    }


def venv_shebang_leaks(venv_bin: Path = PLATFORM_VENV.parent) -> List[str]:
    """Scripts in the platform venv whose interpreter is under /root. One of
    these once sent a pip install into live's venv (decisions.md)."""
    leaks = []
    for f in sorted(venv_bin.iterdir()):
        if f.is_file() and not f.is_symlink():
            with open(f, "rb") as fh:
                first = fh.readline(300)
            if first.startswith(b"#!") and b"/root/" in first:
                leaks.append(f.name)
    return leaks


def pg_dsn(slug: str) -> str:
    return f"postgresql:///jarvis_{slug}?host=/var/run/postgresql&user=jarvis_{slug}_app"


def switch_states(manifest: dict) -> Dict[str, str]:
    """manifest.switches -> on/off per piece. YAML reads a bare on/off as a
    bool, so both spellings are accepted; anything else refuses."""
    raw = manifest.get("switches") or {}
    if not isinstance(raw, dict) or set(raw) - set(SWITCHES):
        die(f"switches must be a mapping of {SWITCHES}")
    out = {}
    for name in SWITCHES:
        v = raw.get(name, "on")
        v = {True: "on", False: "off"}.get(v, v) if isinstance(v, bool) else str(v).strip().lower()
        if v not in ("on", "off"):
            die(f"switches.{name} must be on or off")
        out[name] = v
    return out


def model_settings(manifest: dict) -> Dict[str, str]:
    """runtime.json models: the agent's chat model and the cheaper model for
    background parsing and extraction. Per tenant, from the manifest."""
    return {"agent": str(manifest["agent_model"]),
            "background": str(manifest.get("background_model") or DEFAULT_BACKGROUND_MODEL)}


def check_models(manifest: dict) -> None:
    for key in ("agent_model", "background_model"):
        v = manifest.get(key)
        if key == "background_model" and v is None:
            continue
        if not MODEL_ID.fullmatch(str(v)):
            die(f"{key} must be a Claude model id (claude-...)")


def venv_python(manifest: dict) -> Path:
    return PLATFORM_VENV


def skill_data_dirs(opt: Path, shipped: List[str]) -> List[Path]:
    """Every shipped skill gets a shared data/ dir; skill code itself is read-only."""
    return [opt / "skills" / "openclaw-imports" / s / "data" for s in sorted(shipped)]


def write_file(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.chmod(path, mode)
    log(f"wrote {path}")


def install_units(manifest: dict, mapping: Dict[str, str]) -> None:
    slug = manifest["slug"]
    sched = manifest.get("schedule") or {}
    mapping = dict(mapping)
    mapping.update({
        "DIGEST_ON_BOOT": str(sched.get("digest_on_boot_sec", 120)),
        "DIGEST_TICK": str(sched.get("digest_tick_seconds", 300)),
        "DIGEST_ACCURACY": str(sched.get("digest_accuracy_sec", 30)),
        "SWEEP_TICK": str(sched.get("sweep_tick_seconds", 1800)),
        "SWEEP_ACCURACY": str(sched.get("sweep_accuracy_sec", 60)),
        "METRICS_CALENDAR": str(sched.get("metrics_calendar", "*-*-* 08:30:00")),
        "METRICS_ACCURACY": str(sched.get("metrics_accuracy", "5m")),
    })
    write_file(UNIT_DIR / f"{slug}-app.service", fill(read_template("app.service"), mapping))
    write_file(UNIT_DIR / f"{slug}-gateway.service", fill(read_template("gateway.service"), mapping))
    oauth_tmpl = mapping.get("OAUTH_TEMPLATE") or "oauth.scratch.conf"
    dropin = UNIT_DIR / f"{slug}-app.service.d" / "oauth.conf"
    if manifest.get("isolation") == "two_user":
        write_file(dropin, fill(read_template("oauth.isolated.conf"), mapping), 0o644)
    else:
        write_file(dropin, fill(read_template(oauth_tmpl), mapping), 0o600)
    write_file(UNIT_DIR / f"{slug}-backup.service", fill(read_template("backup.service"), mapping))
    write_file(UNIT_DIR / f"{slug}-backup.timer", fill(read_template("backup.timer"), mapping))
    logrotate = Path("/etc/logrotate.d") / f"jarvis-{slug}"
    write_file(logrotate, fill(read_template("logrotate.conf"), mapping))
    write_file(UNIT_DIR / f"{slug}-task-digest.service", fill(read_template("digest.service"), mapping))
    write_file(UNIT_DIR / f"{slug}-task-digest.timer", fill(read_template("digest.timer"), mapping))
    boots = list(sched.get("sweep_on_boot_sec") or list(SWEEP_BOOT_DEFAULTS))
    if len(boots) != 3:
        die("schedule.sweep_on_boot_sec must be three values (one per sweep) when set")
    for sweep, boot in zip(SWEEPS, boots):
        m = dict(mapping)
        m["SWEEP"] = sweep
        m["SWEEP_ON_BOOT"] = str(boot)
        write_file(UNIT_DIR / f"{slug}-sweep-{sweep.replace('_', '-')}.service",
                   fill(read_template("sweep.service"), m))
        write_file(UNIT_DIR / f"{slug}-sweep-{sweep.replace('_', '-')}.timer",
                   fill(read_template("sweep.timer"), m))
    write_file(UNIT_DIR / f"{slug}-metrics-monthly.service", fill(read_template("metrics.service"), mapping))
    write_file(UNIT_DIR / f"{slug}-metrics-monthly.timer", fill(read_template("metrics.timer"), mapping))
    log("timer units written; NOT enabled (optional until knobs land)")
    if manifest.get("isolation") == "two_user":
        dropins = {f"{slug}-app.service": "app.isolation.conf",
                   f"{slug}-gateway.service": "gateway.isolation.conf",
                   f"{slug}-backup.service": "backup.isolation.conf",
                   f"{slug}-task-digest.service": "job.isolation.conf",
                   f"{slug}-metrics-monthly.service": "job.isolation.conf"}
        for sweep in SWEEPS:
            dropins[f"{slug}-sweep-{sweep.replace('_', '-')}.service"] = "job.isolation.conf"
        for unit, tmpl in dropins.items():
            write_file(UNIT_DIR / f"{unit}.d" / "10-isolation.conf", fill(read_template(tmpl), mapping))
        log(f"two-user isolation drop-ins written for {len(dropins)} units")


def print_ingress(slug: str, origin: str) -> None:
    print("\n# cloudflared — PRINTED, not run (no --apply-ingress)\n")
    for name in ("app", "gateway", "hooks"):
        print(f"cloudflared tunnel route dns yamin {slug}.{name}.yamin.io")
    print("\n# Ingress snippets (do not apply for loopback scratch):")
    print(f"#   hostname: {slug}.app.yamin.io      → http://127.0.0.1:<app>")
    print(f"#   hostname: {slug}.gateway.yamin.io  → http://127.0.0.1:<gateway>")
    print(f"#   hostname: {slug}.hooks.yamin.io    → http://127.0.0.1:<hooks>")
    print(f"\n# Google Console: add redirect URI {origin.rstrip('/')}/oauth/callback\n")


def print_unroute(slug: str) -> None:
    print("\n# DNS unroute — reverse of provision. Only if those routes were applied:")
    print(f"# Delete Cloudflare DNS records {slug}.{{app,gateway,hooks}}.yamin.io")
    print("# Strip matching ingress hostname lines from /etc/cloudflared/config.yml")
    print("# then: systemctl reload cloudflared")
    print("# Commands that created them (for the operator to reverse):")
    for name in ("app", "gateway", "hooks"):
        print(f"# cloudflared tunnel route dns yamin {slug}.{name}.yamin.io")
    print()


def provision(manifest_path: Path) -> None:
    manifest = load_yaml(manifest_path)
    slug, isolation, posture, ports, packs = validate_manifest(manifest)
    hits = structural_collisions(slug, ports)
    if isolation == "two_user":
        hits += identity_collisions(slug)
    if hits:
        die("structural collision:\n  " + "\n  ".join(hits))
    for label, port in (("app", ports["app"]), ("gateway", ports["gateway"]), ("hooks", ports["hooks"])):
        if ss_listen(int(port)):
            die(f"ss supplementary: port {port} ({label}) already listening")
    log("structural collision checks passed; ss supplementary clean")

    sha_skills, sha_app = origin_shas(manifest)

    client_src = PLATFORM_OAUTH_CLIENT if isolation == "two_user" else OAUTH_CLIENT
    if not client_src.is_file():
        die(f"OAuth client missing (not reading contents): {client_src}")

    opt = Path(f"/opt/{slug}")
    opt.mkdir(mode=0o700, parents=False)
    log(f"mkdir {opt}")
    jarvis = opt / "jarvis-app"
    skills_root = opt / "skills"
    skills_clone = skills_root / "openclaw-imports"
    hermes = opt / "hermes"
    skills_root.mkdir()
    clone_app(jarvis, sha_app)
    clone_skills(skills_clone, sha_skills)
    loadout = load_loadout(skills_clone, manifest.get("skill_loadout") or "base")
    log(f"loadout from {loadout['source']}")
    shipped = prune_to_loadout(
        skills_clone, loadout, manifest.get("skill_loadout_optional") or []
    )
    log(f"shipped skills: {shipped}")

    hermes.mkdir()
    for d in ("state", "credentials", "workspace", "outbox"):
        (hermes / d).mkdir()
    (jarvis / "data" / "attachments").mkdir(parents=True, exist_ok=True)
    (hermes / "skills").symlink_to(skills_root)
    log(f"symlink {hermes / 'skills'} → {skills_root}")
    if packs == "omit":
        (hermes / ".no-bundled-skills").write_text(
            "omit: hermes must not seed bundled curator packs\n"
        )
        log("curator_packs=omit: wrote hermes/.no-bundled-skills")
    else:
        log("curator_packs=defaults: leaving bundled sync enabled (no cp from testco/live)")

    mapping_pre = common_map(manifest, posture, "PENDING", "")
    write_file(opt / f"{slug}_preflight.sh", fill(read_template("preflight.sh"), mapping_pre), 0o755)
    write_file(hermes / "config.yaml", fill(read_template("hermes-config.yaml"), mapping_pre))
    write_file(
        hermes / "SOUL.md",
        f"You are {manifest['agent_display_name']}, the assistant for "
        f"{manifest['display_name']}.\n",
    )

    passphrase = mint_isolated_secrets(slug, hermes) if isolation == "two_user" else mint_env(hermes)
    print_passphrase_once(passphrase)

    minted = render_skill_config(manifest, posture, opt, skills_clone, shipped, loadout, hermes)
    if isolation == "two_user":
        if minted:
            die("provision_client minted a second passphrase on a two-user tenant; the app env file is the only source")
        trim_agent_env(hermes)

    # Two-user tenants keep the token key in the EnvironmentFile, not a drop-in.
    fernet = "IN-ENV-FILE" if isolation == "two_user" else Fernet.generate_key().decode("ascii")
    force = " --force" if another_gateway_running() else ""
    if force:
        log("another gateway run is on this host; gateway unit will use --force")
    mapping = common_map(manifest, posture, fernet, force, shipped)
    install_units(manifest, mapping)
    write_file(opt / "ONBOARDING-MAILBOX.md", (TEMPLATES / "ONBOARDING-MAILBOX.md").read_text())
    if isolation == "two_user":
        names = create_identities(slug)
        apply_isolation(opt, names, skill_data_dirs(opt, shipped))
        create_platform_db(jarvis, slug, names["app_user"])
    print_ingress(slug, manifest["public_origin"])

    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", f"{slug}-app.service", f"{slug}-gateway.service"])
    for unit in (f"{slug}-app.service", f"{slug}-gateway.service"):
        r = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True)
        st = (r.stdout or "").strip() or "unknown"
        if st != "active":
            die(f"{unit} is {st} after enable --now — preflight/start failed (see journalctl -u {unit})")
    run(["systemctl", "enable", "--now", f"{slug}-backup.timer"])
    log(f"enabled {slug}-app, {slug}-gateway, and {slug}-backup.timer")


def origin_shas(manifest: dict) -> tuple:
    sha_skills = str(manifest["skills_origin_sha"]).strip()
    sha_app = str(manifest.get("app_origin_sha") or "").strip()
    if not sha_app:
        sha_app = subprocess.check_output(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
        ).strip()
        log(f"app_origin_sha omitted; using invoking repo HEAD {sha_app}")
    return sha_skills, sha_app


def render_skill_config(manifest: dict, posture: dict, opt: Path, skills_clone: Path,
                        shipped: List[str], loadout: dict, hermes_home: Path) -> bool:
    """identity/channels/runtime.json into skills_clone/_shared, for a tenant
    whose tree is (or will be) at opt. True if provision_client minted secrets."""
    ports = manifest["ports"]
    jarvis, hermes = opt / "jarvis-app", opt / "hermes"
    paths = {
        "app_root": str(jarvis),
        "workspace": str(hermes / "workspace"),
        "skills_root": str(opt / "skills" / "openclaw-imports"),
        "tasks_db": str(hermes / "state" / "jarvis_tasks.db"),
        "media_inbound": str(hermes / "media" / "inbound"),
        "render_output": str(hermes / "workspace" / "renders"),
        "cloudflared_dir": "/etc/cloudflared",
    }
    cfg = build_client_config(manifest, paths, shipped, loadout)
    minted = run_provision_client(skills_clone, cfg, hermes_home, venv_python(manifest))
    stamp_identity_mailbox(skills_clone, manifest)
    runtime = json.loads((skills_clone / "_shared" / "runtime.json").read_text())
    # Dual source: runtime.safety.allow_writes matches JARVIS_ALLOW_WRITES.
    # provision_client refused true in its *input*; the provisioner patches the
    # stamped output so captured/blocked/live stay in sync with the unit env.
    runtime.setdefault("safety", {})["allow_writes"] = bool(posture["writes_armed"])
    runtime["models"] = model_settings(manifest)
    (skills_clone / "_shared" / "runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
    log(f"runtime.safety.allow_writes={runtime['safety']['allow_writes']} (synced to env)")
    if (runtime.get("gateway") or {}).get("base_url") != f"http://127.0.0.1:{int(ports['gateway'])}":
        die("runtime gateway.base_url does not equal HERMES_API_URL target")
    if (runtime.get("paths") or {}).get("app_root") != str(jarvis):
        die("runtime paths.app_root missing after provision_client")
    gpt_port = ((runtime.get("skills") or {}).get("gmail-push-trigger") or {}).get("webhook_port")
    if int(gpt_port) != int(ports["hooks"]):
        die("runtime webhook_port != ports.hooks")
    for name in shipped:
        block = (runtime.get("skills") or {}).get(name)
        if not isinstance(block, dict) or "enabled" not in block:
            die(f"runtime.json missing enabled key for shipped skill {name}")
    return minted


def validate_manifest(manifest: dict) -> tuple:
    slug = manifest.get("slug")
    check_slug(slug)
    for key in ("display_name", "timezone", "assistant_address", "agent_display_name",
                "mailbox_strategy", "ports", "public_origin", "skills_origin_sha",
                "curator_packs", "writes_posture", "venv", "agent_model"):
        if key not in manifest:
            die(f"manifest missing required field {key}")
    check_models(manifest)
    switch_states(manifest)
    for key, person in (manifest.get("people") or {}).items():
        roles = (person or {}).get("roles") or []
        if "owner" in roles and "internal" not in roles:
            die(f"people.{key} is an owner without the internal role: roles are literal, and "
                "task.add needs internal or assistant, so the owner could not file their own tasks")
    if manifest["mailbox_strategy"] != "dedicated_account":
        die("mailbox_strategy must be dedicated_account (send_as_alias is rejected)")
    if str(manifest["agent_display_name"]).strip().lower() == "jarvis":
        die("agent_display_name must be tenant-chosen, not Jarvis")
    if "@" not in str(manifest["assistant_address"]):
        die("assistant_address is not an email")
    packs = manifest["curator_packs"]
    if packs not in ("omit", "defaults"):
        die("curator_packs must be omit | defaults")
    if manifest["venv"] == "per-tenant":
        die("venv: per-tenant is human-yes; this slice uses platform")
    if manifest["venv"] == "shared":
        die("venv: shared is retired: it ran tenants on live's venv; use venv: platform")
    if manifest["venv"] != "platform":
        die("venv must be platform | per-tenant")
    isolation = manifest.get("isolation")
    if isolation not in ISOLATIONS:
        die(f"isolation must be one of {ISOLATIONS}")
    if isolation == "none" and manifest.get("writes_posture") != "captured":
        die("isolation: none is for captured loopback scratch only; blocked and live tenants are two_user")
    if isolation == "two_user":
        if manifest["venv"] != "platform":
            die("isolation: two_user requires venv: platform (tenant users cannot reach /root)")
        if not PLATFORM_VENV.is_file():
            die(f"platform venv missing: {PLATFORM_VENV}")
        leaks = venv_shebang_leaks()
        if leaks:
            die(f"platform venv scripts run an interpreter under /root: {leaks}")
        if manifest.get("sentinel_mode") not in SENTINEL_MODES:
            die(f"isolation: two_user requires sentinel_mode: {' | '.join(SENTINEL_MODES)}")
        isolation_names(slug)
    log(f"venv=platform: {PLATFORM_VENV} (a copy; pip installs there do not reach live)")

    ports = manifest["ports"]
    for k in ("app", "gateway", "hooks"):
        if k not in ports:
            die(f"ports.{k} required")
        int(ports[k])
    if int(ports["app"]) in (8700, 8710) or int(ports["gateway"]) in (8642, 8652):
        die("manifest ports collide with live/testco well-known ports")

    gpt = manifest.get("gmail_push_trigger") or {}
    if "webhook_port" in gpt and int(gpt["webhook_port"]) != int(ports["hooks"]):
        die("gmail_push_trigger.webhook_port must equal ports.hooks")

    posture = posture_rules(manifest)
    return slug, isolation, posture, ports, packs


def create_platform_db(jarvis: Path, slug: str, app_user: str) -> None:
    """Database, roles and peer rule from the tenant's own code, so the schema
    matches what it runs. Only the app user gets a rule."""
    out = run([str(PLATFORM_VENV), str(jarvis / "scripts" / "pg_tenant.py"), "create", slug,
               "--app-user", app_user], capture_output=True, text=True).stdout.strip().splitlines()[-1]
    if json.loads(out).get("dsn") != pg_dsn(slug):
        die(f"pg_tenant created a database whose DSN is not {pg_dsn(slug)}")
    log(f"platform database jarvis_{slug} at schema {json.loads(out)['schema']}, app role only")


def archive_platform_db(slug: str) -> None:
    """Teardown keeps the tenant's approvals, grants and audit log: a dump in
    the backup root, written before the database is dropped."""
    tool = REPO / "scripts" / "pg_tenant.py"
    exists = subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-XqAt", "-c",
                             f"SELECT 1 FROM pg_database WHERE datname = 'jarvis_{slug}'"],
                            capture_output=True, text=True, cwd="/").stdout.strip() == "1"
    if not exists:
        log(f"no platform database jarvis_{slug}; nothing to archive")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump = Path("/root/backups") / slug / f"teardown-{stamp}-platform.dump"
    run([str(PLATFORM_VENV), str(tool), "dump", slug, str(dump)])
    run([str(PLATFORM_VENV), str(tool), "restore-check", str(dump)])
    log(f"archived jarvis_{slug} to {dump} (restore proven)")
    run([str(PLATFORM_VENV), str(tool), "drop", slug, "--yes"])
    log(f"dropped jarvis_{slug} and its roles")


def teardown(slug: str, yes: bool) -> None:
    check_slug(slug)
    opt = Path(f"/opt/{slug}")
    if str(opt).startswith("/root") or opt == Path("/root"):
        die("refusing teardown under /root")
    if not yes:
        print(
            f"Refusing teardown of {slug} without --yes.\n"
            f"This would stop units, remove {UNIT_DIR}/{slug}-*, remove {opt}.\n"
            f"Re-run: {Path(__file__).name} --teardown {slug} --yes"
        )
        raise Refuse(1)
    units = sorted(UNIT_DIR.glob(f"{slug}-*"))
    names = [p.name for p in units if p.suffix in {".service", ".timer"}]
    if names:
        log(f"stopping {names}")
        subprocess.run(["systemctl", "stop", *names], check=False)
        subprocess.run(["systemctl", "disable", *names], check=False)
    for p in units:
        if p.is_dir():
            shutil.rmtree(p)
            log(f"removed {p}")
        elif p.exists():
            p.unlink()
            log(f"removed {p}")
    lr = Path("/etc/logrotate.d") / f"jarvis-{slug}"
    if lr.exists():
        lr.unlink()
        log(f"removed {lr}")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "reset-failed"], check=False)
    if opt.exists():
        shutil.rmtree(opt)
        log(f"removed {opt}")
    archive_platform_db(slug)
    env_file = ETC_JARVIS / f"{slug}-app.env"
    if env_file.exists():
        env_file.unlink()
        log(f"removed {env_file}")
    n = isolation_names(slug)
    ours = {n["app_user"], n["agent_user"]}
    grp = _getent("group", n["group"])
    members = {m for m in grp.split(":")[3].split(",") if m} if grp else set()
    for user in sorted(ours):
        if _getent("passwd", user):
            run(["userdel", user])
    if grp:
        if members and members <= ours:
            run(["groupdel", n["group"]])
        else:
            log(f"NOT removing group {n['group']}: members {sorted(members)} are not exactly this tenant's users")
    print_unroute(slug)
    tree_hits = []
    if opt.exists():
        tree_hits.append(f"tree still exists: {opt}")
    for p in sorted(UNIT_DIR.glob(f"{slug}-*")):
        tree_hits.append(f"unit path still exists: {p}")
    if env_file.exists():
        tree_hits.append(f"secrets file still exists: {env_file}")
    for user in (n["app_user"], n["agent_user"]):
        if _getent("passwd", user):
            tree_hits.append(f"user still exists: {user}")
    if tree_hits:
        die("teardown incomplete:\n  " + "\n  ".join(tree_hits))
    log("teardown complete")


ISOLATION_TEMPLATE = {"app": "app.isolation.conf", "gateway": "gateway.isolation.conf",
                      "backup": "backup.isolation.conf"}


def mailbox_connected(opt: Path) -> bool:
    db = opt / "app-private" / "jarvis_accounts.db"
    if not db.is_file():
        return False
    import sqlite3  # noqa: PLC0415
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return bool(con.execute("SELECT count(*) FROM google_connection WHERE coalesce(token_cipher, '') != ''")
                    .fetchone()[0])
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


def rewrite_keeping_owner(path: Path, text: str) -> bool:
    """Replace a file's contents, keeping its owner and mode. True if it changed."""
    st = path.stat()
    if path.read_text() == text:
        return False
    path.write_text(text)
    os.chown(path, st.st_uid, st.st_gid)
    os.chmod(path, st.st_mode & 0o7777)
    log(f"rewrote {path}")
    return True


def reconfigure(manifest_path: Path, restart_approved: bool) -> None:
    """Display names, timezone, models and switches from the manifest onto an
    existing two-user tenant, then restart its app and gateway. No re-clone,
    no new secrets, no data touched. Ports, posture and isolation are not
    reconfigurable; those re-provision."""
    manifest = load_yaml(manifest_path)
    slug = manifest.get("slug")
    check_slug(slug)
    for key in ("display_name", "agent_display_name", "timezone", "agent_model", "ports"):
        if key not in manifest:
            die(f"manifest missing required field {key}")
    check_models(manifest)
    switch_states(manifest)
    name = str(manifest["agent_display_name"]).strip()
    if not name or name.lower() == "jarvis":
        die("agent_display_name must be tenant-chosen, not Jarvis")
    opt = Path(f"/opt/{slug}")
    if manifest.get("isolation") != "two_user" or not (opt / "app-private").is_dir():
        die(f"--reconfigure is for provisioned two-user tenants; {opt} is not one")
    if mailbox_connected(opt) and not restart_approved:
        die(f"STOP gate: {slug} has a Google mailbox connected. Restarting it needs a human yes; "
            "re-run with --restart-approved once you have it.")
    hermes, shared = opt / "hermes", opt / "skills" / "openclaw-imports" / "_shared"
    cfg_text = (hermes / "config.yaml").read_text()
    if f"port: {int(manifest['ports']['gateway'])}" not in cfg_text \
            or f"127.0.0.1:{int(manifest['ports']['app'])}/" not in cfg_text:
        die("manifest ports differ from the running tenant; ports change only by re-provisioning")

    changed = []
    ident_p = shared / "identity.json"
    ident = json.loads(ident_p.read_text())
    ident["agent_display_name"] = name
    ident_people = ident.get("people") or {}
    for key, mp in (manifest.get("people") or {}).items():
        if key not in ident_people:
            die(f"manifest person {key} is not on the running tenant; people are added by re-provisioning")
        ident_people[key]["roles"] = list((mp or {}).get("roles") or [])
    for person in ident_people.values():
        if "assistant" in (person.get("roles") or []):
            person["name"] = person["display_name"] = name
    changed += [ident_p] if rewrite_keeping_owner(ident_p, json.dumps(ident, indent=2) + "\n") else []
    rt_p = shared / "runtime.json"
    rt = json.loads(rt_p.read_text())
    rt.setdefault("client", {}).update(display_name=manifest["display_name"], timezone=manifest["timezone"])
    rt["models"] = model_settings(manifest)
    changed += [rt_p] if rewrite_keeping_owner(rt_p, json.dumps(rt, indent=2) + "\n") else []
    soul = hermes / "SOUL.md"
    changed += [soul] if rewrite_keeping_owner(
        soul, f"You are {name}, the assistant for {manifest['display_name']}.\n") else []

    shipped = sorted(d.name for d in (opt / "skills" / "openclaw-imports").iterdir() if (d / "data").is_dir())
    mapping = common_map(manifest, posture_rules(manifest), "IN-ENV-FILE", "", shipped)
    cfg = hermes / "config.yaml"
    changed += [cfg] if rewrite_keeping_owner(cfg, fill(read_template("hermes-config.yaml"), mapping)) else []
    for dropin in sorted(UNIT_DIR.glob(f"{slug}-*.service.d/10-isolation.conf")):
        role = dropin.parent.name[len(slug) + 1:-len(".service.d")]
        tmpl = ISOLATION_TEMPLATE.get(role, "job.isolation.conf")
        changed += [dropin] if rewrite_keeping_owner(dropin, fill(read_template(tmpl), mapping)) else []
    log(f"reconfigure {slug}: {len(changed)} file(s) changed")

    run(["systemctl", "daemon-reload"])
    units = [f"{slug}-app.service", f"{slug}-gateway.service"]
    run(["systemctl", "restart", *units])
    bad = wait_healthy(slug, manifest["ports"])
    if bad:
        die(f"{slug} not healthy within {UPGRADE_TIMEOUT}s after reconfigure: {bad} (journalctl -u {units[0]})")
    log(f"reconfigured {slug}: app and gateway active, app answers /api/ping, gateway /health ok")


LEGACY_FILES = ("hermes/state/approvals.jsonl", "app-private/approvals_decisions.jsonl",
                "app-private/sentinel_ledger.jsonl")
UPGRADE_TIMEOUT = 120


def tenant_unit_paths(slug: str) -> List[Path]:
    """Every unit file, drop-in dir and logrotate file the provisioner owns for slug."""
    stems = ["app", "gateway", "backup", "task-digest", "metrics-monthly"] + \
            [f"sweep-{s.replace('_', '-')}" for s in SWEEPS]
    out = []
    for stem in stems:
        for suffix in (".service", ".timer", ".service.d"):
            p = UNIT_DIR / f"{slug}-{stem}{suffix}"
            if p.exists():
                out.append(p)
    lr = Path("/etc/logrotate.d") / f"jarvis-{slug}"
    return out + ([lr] if lr.exists() else [])


def unit_env_has(unit: str, name: str) -> bool:
    env = subprocess.run(["systemctl", "show", unit, "-p", "Environment", "--value"],
                         capture_output=True, text=True).stdout
    return any(tok.startswith(f"{name}=") for tok in env.split())


def tenant_healthy(slug: str, ports: dict) -> List[str]:
    """Empty when app and gateway are active and answering; else what is wrong."""
    import urllib.request  # noqa: PLC0415
    bad = []
    for u in (f"{slug}-app.service", f"{slug}-gateway.service"):
        st = subprocess.run(["systemctl", "is-active", u], capture_output=True, text=True).stdout.strip()
        if st != "active":
            bad.append(f"{u} {st}")
    if not _ping(int(ports["app"])):
        bad.append("app /api/ping not answering")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(ports['gateway'])}/health", timeout=3) as r:
            if json.loads(r.read()).get("status") != "ok":
                bad.append("gateway /health not ok")
    except Exception as exc:  # noqa: BLE001
        bad.append(f"gateway /health: {type(exc).__name__}")
    return bad


def wait_healthy(slug: str, ports: dict, timeout: int = UPGRADE_TIMEOUT) -> List[str]:
    deadline = time.time() + timeout
    bad = ["not checked"]
    while time.time() < deadline:
        bad = tenant_healthy(slug, ports)
        if not bad:
            return []
        time.sleep(2)
    return bad


def copy_sqlite(src: Path, dest: Path, owner: str) -> Dict[str, int]:
    """Consistent copy (WAL included) with the SQLite backup API; row counts
    must match table for table."""
    import sqlite3  # noqa: PLC0415
    tmp = dest.with_name(dest.name + ".upgrade-tmp")
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(tmp)
    s.backup(d)
    tables = [r[0] for r in s.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    counts = {}
    for t in tables:
        a = s.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        b = d.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        if a != b:
            die(f"copy of {src} lost rows in {t}: {a} -> {b}")
        counts[t] = a
    s.close()
    d.close()
    run(["install", "-m", "600", "-o", owner, "-g", owner, str(tmp), str(dest)])
    tmp.unlink()
    return counts


def upgrade(manifest_path: Path, restart_approved: bool) -> None:
    """Bring an existing two-user tenant to the manifest's commits and the
    current layout without touching its data or secrets: fresh code and skill
    config, units re-rendered (old drop-ins archived), threads moved behind the
    app, its Postgres database created. Everything replaced is snapshotted
    first; if app or gateway is not healthy within UPGRADE_TIMEOUT seconds the
    snapshot is restored and the old version restarted."""
    manifest = load_yaml(manifest_path)
    slug, isolation, posture, ports, _packs = validate_manifest(manifest)
    if isolation != "two_user":
        die("--upgrade is for two-user tenants")
    if posture["writes_armed"]:
        die("--upgrade refuses a manifest that arms writes")
    opt = Path(f"/opt/{slug}")
    names = isolation_names(slug)
    env_file = ETC_JARVIS / f"{slug}-app.env"
    missing = [str(p) for p in (opt / "app-private", opt / "hermes" / ".env", env_file) if not p.exists()]
    missing += [n for n in (names["app_user"], names["agent_user"]) if not _getent("passwd", n)]
    if missing:
        die(f"--upgrade needs an existing two-user tenant; missing: {missing}")
    cfg_text = (opt / "hermes" / "config.yaml").read_text()
    if f"port: {int(ports['gateway'])}" not in cfg_text:
        die("manifest gateway port differs from the running tenant; ports change only by re-provisioning")
    if any(ln.startswith("JARVIS_ALLOW_WRITES") for ln in env_file.read_text().splitlines()):
        die(f"{env_file} sets JARVIS_ALLOW_WRITES")
    for u in (f"{slug}-app.service", f"{slug}-gateway.service"):
        if unit_env_has(u, "JARVIS_ALLOW_WRITES"):
            die(f"{u} has JARVIS_ALLOW_WRITES in its environment")
    log(f"posture before upgrade: {posture['posture']}; JARVIS_ALLOW_WRITES unset on app, gateway and env file")
    if mailbox_connected(opt) and not restart_approved:
        die(f"STOP gate: {slug} has a Google mailbox connected. Restarting it needs a human yes; "
            "re-run with --restart-approved once you have it.")
    for rel in LEGACY_FILES:
        p = opt / rel
        if p.exists() and p.stat().st_size:
            die(f"{p} holds records; importing them is not part of --upgrade")
    if (opt / "app-private" / "jarvis_threads.db").exists() and (opt / "hermes" / "state" / "jarvis_threads.db").exists():
        die("threads database exists both shared and app-private; refusing to pick one")
    db_existed = subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-XqAt", "-c",
                                 f"SELECT 1 FROM pg_database WHERE datname = 'jarvis_{slug}'"],
                                capture_output=True, text=True, cwd="/").stdout.strip() == "1"
    db_version = ""
    if db_existed:
        db_version = subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-XqAt", "-d", f"jarvis_{slug}",
                                     "-c", "SELECT version_num FROM alembic_version"],
                                    capture_output=True, text=True, cwd="/").stdout.strip()
        if not db_version:
            die(f"jarvis_{slug} exists but has no alembic version; refusing to guess its schema")
    if not PLATFORM_OAUTH_CLIENT.is_file() and not (opt / "app-private" / "oauth_client.json").exists():
        die("no OAuth client for the tenant")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap = Path("/root/backups") / slug / f"upgrade-{stamp}"
    snap.mkdir(parents=True, mode=0o700)
    os.chmod(snap, 0o700)
    log(f"upgrade workspace {snap}")

    # Staged while the old version still runs: nothing under /opt changes yet.
    sha_skills, sha_app = origin_shas(manifest)
    new = snap / "new"
    new.mkdir()
    clone_app(new / "jarvis-app", sha_app)
    clone_skills(new / "openclaw-imports", sha_skills)
    loadout = load_loadout(new / "openclaw-imports", manifest.get("skill_loadout") or "base")
    shipped = prune_to_loadout(new / "openclaw-imports", loadout, manifest.get("skill_loadout_optional") or [])
    render_home = snap / "render-home"
    render_home.mkdir(mode=0o700)
    (render_home / ".env").write_text("APP_PASSPHRASE=unused-render-only\nAPP_SESSION_SECRET=unused-render-only\n")
    if render_skill_config(manifest, posture, opt, new / "openclaw-imports", shipped, loadout, render_home):
        die("provision_client minted secrets during an upgrade render")
    shutil.rmtree(render_home)
    log(f"staged app {sha_app} and skills {sha_skills} in {new}")
    if db_existed:
        head = re.search(r'^SCHEMA_HEAD\s*=\s*"([^"]+)"', (new / "jarvis-app" / "app" / "platform_db.py")
                         .read_text(), re.M)
        if not head:
            die("staged app/platform_db.py has no SCHEMA_HEAD; cannot tell whether the schema changes")
        if head.group(1) != db_version:
            die(f"jarvis_{slug} is at schema {db_version}, the new code wants {head.group(1)}: "
                "a schema migration needs a human yes; nothing was stopped")
        log(f"jarvis_{slug} kept: schema {db_version} is already what the new code expects")

    units = [f"{slug}-app.service", f"{slug}-gateway.service"]
    timers = [p.name for p in tenant_unit_paths(slug) if p.suffix == ".timer"]
    enabled_timers = [t for t in timers if subprocess.run(["systemctl", "is-enabled", t], capture_output=True,
                                                          text=True).stdout.strip() == "enabled"]
    run(["systemctl", "stop", *timers, *units])
    run(["tar", "--xattrs", "--acls", "-cpf", str(snap / "opt.tar"), "-C", "/opt", slug])
    run(["tar", "--xattrs", "--acls", "-cpf", str(snap / "units.tar"), "-C", "/",
         *[str(p.relative_to("/")) for p in tenant_unit_paths(slug)]])
    for f in ("opt.tar", "units.tar"):
        os.chmod(snap / f, 0o600)
    log(f"snapshot: {snap}/opt.tar and units.tar; enabled timers {enabled_timers}")
    if db_existed:
        run([str(PLATFORM_VENV), str(REPO / "scripts" / "pg_tenant.py"), "dump", slug, str(snap / "platform.dump")])
        os.chmod(snap / "platform.dump", 0o600)
        log(f"snapshot: jarvis_{slug} dumped to {snap / 'platform.dump'}")

    created_db = False
    try:
        old = snap / "old"
        old.mkdir()
        jarvis = opt / "jarvis-app"
        clone = opt / "skills" / "openclaw-imports"
        os.rename(jarvis, old / "jarvis-app")
        os.rename(new / "jarvis-app", jarvis)
        if (jarvis / "data").exists():
            shutil.rmtree(jarvis / "data")
        os.rename(old / "jarvis-app" / "data", jarvis / "data")
        os.rename(clone, old / "openclaw-imports")
        os.rename(new / "openclaw-imports", clone)
        for data in sorted((old / "openclaw-imports").glob("*/data")):
            target = clone / data.parent.name / "data"
            if not target.parent.is_dir():
                log(f"skill {data.parent.name} is no longer shipped; its data stays in {data}")
                continue
            if target.exists():
                shutil.rmtree(target)
            os.rename(data, target)
            log(f"kept skill data {target}")

        legacy = snap / "legacy"
        legacy.mkdir()
        shared_threads = opt / "hermes" / "state" / "jarvis_threads.db"
        if shared_threads.exists():
            counts = copy_sqlite(shared_threads, opt / "app-private" / "jarvis_threads.db", names["app_user"])
            for suffix in ("", "-wal", "-shm"):
                p = Path(f"{shared_threads}{suffix}")
                if p.exists():
                    os.rename(p, legacy / p.name)
            log(f"threads database moved behind the app (rows per table {counts}); shared copy in {legacy}")
        for rel in LEGACY_FILES:
            p = opt / rel
            if p.exists():
                os.rename(p, legacy / p.name)
                log(f"moved empty legacy file {rel} to {legacy}")

        old_units = snap / "old-dropins"
        old_units.mkdir()
        for d in UNIT_DIR.glob(f"{slug}-*.service.d"):
            for conf in d.glob("*.conf"):
                os.rename(conf, old_units / f"{d.name}__{conf.name}")
        log(f"archived old drop-ins to {old_units}")

        mapping = common_map(manifest, posture, "IN-ENV-FILE",
                             " --force" if another_gateway_running() else "", shipped)
        install_units(manifest, mapping)
        hermes = opt / "hermes"
        write_file(opt / f"{slug}_preflight.sh", fill(read_template("preflight.sh"), mapping), 0o755)
        write_file(hermes / "config.yaml", fill(read_template("hermes-config.yaml"), mapping))
        write_file(hermes / "SOUL.md", f"You are {manifest['agent_display_name']}, the assistant for "
                                       f"{manifest['display_name']}.\n")
        trim_agent_env(hermes)
        apply_isolation(opt, names, skill_data_dirs(opt, shipped), keep_private=True)
        if db_existed:
            run([str(PLATFORM_VENV), str(jarvis / "scripts" / "pg_tenant.py"), "migrate", slug])
            log(f"jarvis_{slug}: grants re-applied at schema {db_version}")
        else:
            create_platform_db(jarvis, slug, names["app_user"])
            created_db = True

        run(["systemctl", "daemon-reload"])
        run(["systemctl", "start", *units])
        bad = wait_healthy(slug, ports)
        for u in units:
            if unit_env_has(u, "JARVIS_ALLOW_WRITES"):
                bad.append(f"{u} has JARVIS_ALLOW_WRITES after upgrade")
        if bad:
            die(f"{slug} not healthy within {UPGRADE_TIMEOUT}s after upgrade: {bad}")
        if enabled_timers:
            run(["systemctl", "start", *enabled_timers])
    except BaseException as exc:
        log(f"UPGRADE FAILED ({exc!r}); rolling back from {snap}")
        rollback_upgrade(slug, ports, snap, created_db, units, timers, enabled_timers)
        raise
    log(f"upgraded {slug} to app {sha_app}, skills {sha_skills}; app and gateway healthy; "
        f"snapshot kept in {snap}")


def rollback_upgrade(slug: str, ports: dict, snap: Path, created_db: bool, units: List[str],
                     timers: List[str], enabled_timers: List[str]) -> None:
    failed = snap / "failed"
    failed.mkdir(exist_ok=True)
    subprocess.run(["systemctl", "stop", *timers, *units], check=False)
    if created_db:
        tool = REPO / "scripts" / "pg_tenant.py"
        r = subprocess.run([str(PLATFORM_VENV), str(tool), "dump", slug, str(failed / "platform.dump")], check=False)
        log(f"rollback: dumped jarvis_{slug} to {failed / 'platform.dump'} (rc={r.returncode})")
        run([str(PLATFORM_VENV), str(tool), "drop", slug, "--yes"])
    opt = Path(f"/opt/{slug}")
    if opt.exists():
        os.rename(opt, failed / "opt")
    run(["tar", "--xattrs", "--acls", "-xpf", str(snap / "opt.tar"), "-C", "/opt"])
    (failed / "units").mkdir(exist_ok=True)
    for p in tenant_unit_paths(slug):
        os.rename(p, failed / "units" / p.name)
    run(["tar", "--xattrs", "--acls", "-xpf", str(snap / "units.tar"), "-C", "/"])
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "start", *units])
    bad = wait_healthy(slug, ports)
    if enabled_timers:
        subprocess.run(["systemctl", "start", *enabled_timers], check=False)
    if bad:
        log(f"ROLLBACK DONE BUT OLD VERSION NOT HEALTHY: {bad}. Failed tree in {failed}.")
    else:
        log(f"rolled back: old version healthy again; failed tree in {failed}")


def _ping(port: int) -> bool:
    import urllib.request  # noqa: PLC0415
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def print_units(manifest_path: Path, posture_override: str | None) -> None:
    """Render unit templates to stdout. No secrets. No writes."""
    manifest = load_yaml(manifest_path)
    check_slug(manifest.get("slug"))
    if posture_override:
        if posture_override not in ("production", "scratch"):
            die("--posture must be production | scratch")
        if posture_override == "production":
            manifest = dict(manifest)
            manifest["writes_posture"] = "blocked"
            safety = dict(manifest.get("safety") or {})
            safety["allow_writes"] = False
            # Production print: no capture suffix in the rendered picture.
            if posture_override == "production":
                safety["outbound_recipient_suffix"] = []
            manifest["safety"] = safety
            if str(manifest.get("public_origin") or "").startswith("http://"):
                # Show the production cookie template; origin stays what the file says.
                pass
        else:
            manifest = dict(manifest)
            manifest["writes_posture"] = "captured"
    posture = posture_rules(manifest)
    mapping = common_map(manifest, posture, "[minted-at-stamp]", " --force")
    mapping.update({
        "DIGEST_ON_BOOT": "120",
        "DIGEST_TICK": "300",
        "DIGEST_ACCURACY": "30",
        "SWEEP_TICK": "1800",
        "SWEEP_ACCURACY": "60",
        "METRICS_CALENDAR": "*-*-* 08:30:00",
        "METRICS_ACCURACY": "5m",
        "SWEEP": "stale_email",
        "SWEEP_ON_BOOT": "180",
    })
    print(f"# print-units posture={posture['posture']} cookie={posture['cookie']} "
          f"writes_armed={posture['writes_armed']} oauth={posture['oauth_template']}")
    names = ["app.service", "gateway.service", posture["oauth_template"],
             "preflight.sh", "backup.service", "backup.timer", "hermes-config.yaml"]
    if manifest.get("isolation") == "two_user":
        names[2] = "oauth.isolated.conf"
        names += ["app.isolation.conf", "gateway.isolation.conf", "job.isolation.conf", "backup.isolation.conf"]
    for name in names:
        print(f"\n##### {name} #####")
        print(fill(read_template(name), mapping))


def check_only(manifest_path: Path) -> None:
    manifest = load_yaml(manifest_path)
    check_slug(manifest.get("slug"))
    hits = structural_collisions(manifest["slug"], manifest["ports"])
    if hits:
        print("STRUCTURAL CHECK FAIL:")
        for h in hits:
            print(f"  {h}")
        raise Refuse(2)
    for label, port in (("app", manifest["ports"]["app"]),
                        ("gateway", manifest["ports"]["gateway"]),
                        ("hooks", manifest["ports"]["hooks"])):
        listening = ss_listen(int(port))
        print(f"ss {label} :{port} listening={listening}")
        if listening:
            print("STRUCTURAL CHECK FAIL: ss supplementary listener")
            raise Refuse(2)
    slug = manifest["slug"]
    print(
        f"STRUCTURAL CHECK PASS: no /opt/{slug}, no {slug}-* units, "
        "no Environment= port claims, ss clean"
    )


def check_loadout(manifest_path: Optional[Path], skills_tree: Optional[Path],
                  optional_enables: Optional[List[str]]) -> None:
    """Completeness + optional prune dry-run. Does not stamp /opt or units."""
    extras = list(optional_enables or [])
    tmp: Optional[Path] = None
    tree = skills_tree
    name = "base"
    if tree is None:
        if manifest_path is None:
            die("--check-loadout requires --skills-tree or --manifest")
        manifest = load_yaml(manifest_path)
        sha = str(manifest["skills_origin_sha"]).strip()
        tmp = Path(tempfile.mkdtemp(prefix="loadout-check-"))
        tree = tmp / "openclaw-imports"
        clone_skills(tree, sha)
        name = manifest.get("skill_loadout") or "base"
        if not extras:
            extras = list(manifest.get("skill_loadout_optional") or [])
    elif not tree.is_dir():
        die(f"--skills-tree is not a directory: {tree}")
    try:
        loadout = load_loadout(tree, name)
        log(f"loadout from {loadout['source']}")
        assert_completeness(tree, loadout)
        if tmp is None:
            print("LOADOUT COMPLETENESS PASS (inspect-only; did not prune --skills-tree)")
            return
        shipped = prune_to_loadout(tree, loadout, extras)
        remaining = listed_skill_dirs(tree)
        log(f"shipped skills: {shipped}")
        log(f"tree after prune: {remaining}")
        for bad in loadout["excluded"]:
            if bad in remaining:
                die(f"excluded {bad} present after dry-run prune")
        print("LOADOUT DRY-RUN PASS")
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def hold_agent_lock() -> None:
    """Provision, upgrade, reconfigure and teardown change the server: hold the
    agent lock (scripts/agent_lock.py) for the whole run, or change nothing."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import agent_lock
    try:
        agent_lock.ensure("provision_tenant", " ".join(sys.argv[1:])[:200])
    except agent_lock.Held as h:
        die(f"the agent lock is held by another process tree: {h}. Nothing was changed.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Stamp or teardown a Jarvis tenant.")
    ap.add_argument("--manifest", type=Path, help="ops YAML (docs/tenants/{slug}.yaml)")
    ap.add_argument("--teardown", metavar="SLUG")
    ap.add_argument("--yes", action="store_true", help="confirm teardown")
    ap.add_argument("--reconfigure", action="store_true",
                    help="with --manifest: re-apply names, timezone, models and switches, then restart")
    ap.add_argument("--upgrade", action="store_true",
                    help="with --manifest: bring an existing two-user tenant to the manifest's commits and "
                         "the current layout, keeping data and secrets; rolls back if unhealthy")
    ap.add_argument("--restart-approved", action="store_true",
                    help="with --reconfigure or --upgrade: a human said yes to restarting a tenant with a "
                         "mailbox connected")
    ap.add_argument("--check-collisions", action="store_true")
    ap.add_argument("--check-loadout", action="store_true",
                    help="completeness + prune dry-run; no /opt stamp")
    ap.add_argument("--skills-tree", type=Path,
                    help="with --check-loadout: inspect this clone (no prune)")
    ap.add_argument("--optional", action="append", default=None,
                    help="with --check-loadout: extra optional skill to keep (repeatable)")
    ap.add_argument("--print-units", action="store_true")
    ap.add_argument("--posture", choices=("production", "scratch"),
                    help="with --print-units: render production or scratch templates")
    ap.add_argument("--import-from", metavar="HERMES_HOME",
                    help="reserved (slice 5); exits without copying")
    ap.add_argument("--apply-ingress", action="store_true",
                    help="not implemented; prints commands and stops")
    args = ap.parse_args(argv)

    if args.import_from:
        print("reserved: --import-from is slice 5 and is not built")
        return 2
    if args.apply_ingress:
        print("REFUSED: --apply-ingress is not implemented in slice 2; commands are printed during provision")
        return 2
    if args.teardown or not (args.check_loadout or args.print_units or args.check_collisions):
        hold_agent_lock()
    if args.teardown:
        teardown(args.teardown, args.yes)
        return 0
    if args.check_loadout:
        check_loadout(args.manifest, args.skills_tree, args.optional)
        return 0
    if args.print_units:
        if not args.manifest:
            die("--print-units requires --manifest")
        print_units(args.manifest, args.posture)
        return 0
    if not args.manifest:
        die("--manifest is required unless --teardown")
    if args.check_collisions:
        check_only(args.manifest)
        return 0
    if args.reconfigure:
        reconfigure(args.manifest, args.restart_approved)
        return 0
    if args.upgrade:
        upgrade(args.manifest, args.restart_approved)
        return 0
    provision(args.manifest)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        print(f"REFUSED: command failed: {exc}", file=sys.stderr)
        sys.exit(exc.returncode or 2)
    except Refuse as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        sys.exit(code)
