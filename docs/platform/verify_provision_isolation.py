#!/usr/bin/env python3
"""Verify the provisioner's two-user layout without touching the server.

1. --print-units for an isolation: two_user manifest renders the users, the
   sandbox, the EnvironmentFile and the platform venv, with no secret in any
   drop-in; the same manifest with isolation: none renders none of it (twin).
2. Manifests the provisioner must refuse: no isolation field, isolation none
   on a blocked tenant, two_user on the shared venv, a slug too long for the
   user names. Each refusal manifest also uses app port 8700, which is always
   refused, so a missing isolation check still stops before any write and the
   message check fails.

The on-host half (a real fresh tenant, then scripts/prove_isolation.py) is the
acceptance run recorded in docs/platform/decisions.md.
"""
import copy
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "scripts" / "provision_tenant.py"
BASE = yaml.safe_load((REPO / "docs/tenants/EXAMPLE.yaml").read_text())
results = []


def ck(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))


def run(manifest: dict, *args):
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(manifest, f)
    p = subprocess.run([sys.executable, str(TOOL), "--manifest", f.name, *args],
                       capture_output=True, text=True, timeout=120)
    Path(f.name).unlink()
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    m = copy.deepcopy(BASE)
    m.update(slug="isoprint", isolation="two_user", venv="platform")
    print("── 1. rendered units, isolation: two_user")
    rc, out = run(m, "--print-units")
    ck("print-units exits 0", rc == 0, out[-300:])
    for needle in ("User=jarvis-isoprint\n", "User=jarvis-isoprint-agent\n", "SupplementaryGroups=isoprint",
                   "EnvironmentFile=/etc/jarvis/isoprint-app.env", "Environment=HERMES_HOME_MODE=0710",
                   "ProtectSystem=strict", "ProtectHome=yes", "NoNewPrivileges=yes", "UMask=0007",
                   "ExecStart=/opt/jarvis/venv/bin/python -m app.main",
                   "Environment=JARVIS_ACCOUNTS_DB=/opt/isoprint/app-private/jarvis_accounts.db",
                   "Environment=YAMIN_OAUTH_CLIENT=/opt/isoprint/app-private/oauth_client.json",
                   "ExecStart=/opt/jarvis/venv/bin/python /opt/isoprint/jarvis-app/scripts/backup_tenant.py"):
        ck(f"renders {needle.strip()}", needle in out)
    ck("no APP_GOOGLE_TOKEN_KEY assignment rendered", "APP_GOOGLE_TOKEN_KEY=" not in out)
    ck("canary: the scan sees a key assignment", "APP_GOOGLE_TOKEN_KEY=" in 'Environment="APP_GOOGLE_TOKEN_KEY=x"')
    directives = [ln for ln in out.splitlines()
                  if ln.split("=", 1)[0] in ("ExecStart", "ExecStartPre", "WorkingDirectory", "Environment",
                                              "EnvironmentFile", "ReadWritePaths")]
    live = [ln for ln in directives if "/root/jarvis-app" in ln or "/root/.hermes" in ln]
    ck(f"no unit directive points at live's tree or home ({len(directives)} directives)", not live, str(live))
    ck("canary: the directive filter sees the backup ExecStart",
       any(ln.startswith("ExecStart=") and "backup_tenant.py" in ln for ln in directives))
    ck("backup renders Environment=JARVIS_ACCOUNTS_DB in its drop-in",
       out.count("Environment=JARVIS_ACCOUNTS_DB=/opt/isoprint/app-private/jarvis_accounts.db") >= 3)
    units = dict(re.findall(r"(?ms)^##### (\S+) #####\n(.*?)(?=^##### |\Z)", out))
    broker = "Environment=JARVIS_APP_BROKER_URL=http://127.0.0.1:8720\n"
    threads = "Environment=JARVIS_THREADS_DB=/opt/isoprint/app-private/jarvis_threads.db\n"
    ck("canary: the unit splitter found the four isolation drop-ins",
       all(u in units for u in ("app.isolation.conf", "gateway.isolation.conf",
                                "job.isolation.conf", "backup.isolation.conf")), str(sorted(units)))
    ck("gateway drop-in points the agent at the app broker", broker in units.get("gateway.isolation.conf", ""))
    ck("only the gateway gets a broker URL (the app refuses to boot with one)",
       [u for u, body in units.items() if "JARVIS_APP_BROKER_URL" in body] == ["gateway.isolation.conf"])
    for u in ("app.isolation.conf", "gateway.isolation.conf", "job.isolation.conf", "backup.isolation.conf"):
        ck(f"{u} names the app-private threads DB", threads in units.get(u, ""))

    print("── 1b. twin: isolation: none renders no users and keeps the drop-in key")
    n = copy.deepcopy(m)
    n.update(isolation="none", venv="shared", writes_posture="captured", public_origin="http://127.0.0.1:8799")
    n.setdefault("safety", {})["outbound_recipient_suffix"] = ["acme.example"]
    rc, out = run(n, "--print-units")
    ck("print-units exits 0", rc == 0, out[-300:])
    ck("no User= line", "\nUser=" not in out)
    ck("legacy oauth drop-in still carries the key placeholder value", "APP_GOOGLE_TOKEN_KEY=" in out)

    print("── 2. refusals (all also carry app port 8700, the last-resort stop)")
    cases = [
        ("no isolation field", {"isolation": None}, "isolation must be one of"),
        ("isolation none on a blocked tenant", {"isolation": "none", "venv": "shared"},
         "isolation: none is for captured loopback scratch only"),
        ("two_user on the shared venv", {"venv": "shared"}, "requires venv: platform"),
        ("slug too long for user names", {"slug": "a" * 20}, "too long for the two-user layout"),
    ]
    for label, patch, msg in cases:
        bad = copy.deepcopy(m)
        bad["ports"] = dict(bad["ports"], app=8700)
        for k, v in patch.items():
            if v is None:
                bad.pop(k, None)
            else:
                bad[k] = v
        rc, out = run(bad)
        ck(f"refuses {label}", rc != 0 and msg in out, out.strip().splitlines()[-1] if out.strip() else "")
        ck(f"  …and created nothing for {label}", not Path(f"/opt/{bad['slug']}").exists())

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
