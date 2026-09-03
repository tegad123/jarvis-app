#!/usr/bin/env python3
"""Assert identity.assistant_address == manifest, and google_email if connected.

A connected-but-wrong mailbox announces (via tenant_config) and fails.
Not connected is OK for a scratch tenant that has not done Connect Google.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import yaml


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_assistant_mailbox.py docs/tenants/{slug}.yaml", file=sys.stderr)
        return 2
    manifest = yaml.safe_load(Path(sys.argv[1]).read_text())
    slug = manifest["slug"]
    want = (manifest.get("assistant_address") or "").strip().lower()
    skills = Path(os.environ.get(
        "JARVIS_SKILLS_ROOT", f"/opt/{slug}/skills/openclaw-imports"
    ))
    ident = json.loads((skills / "_shared" / "identity.json").read_text())
    got = (ident.get("assistant_address") or "").strip().lower()
    strategy = ident.get("mailbox_strategy")
    agent = ident.get("agent_display_name")
    print(f"manifest.assistant_address={want}")
    print(f"identity.assistant_address={got}")
    print(f"identity.mailbox_strategy={strategy}")
    print(f"identity.agent_display_name={agent}")
    ok = True
    if got != want:
        print("FAIL: identity.assistant_address != manifest")
        ok = False
    else:
        print("PASS: identity.assistant_address == manifest")
    if strategy != "dedicated_account":
        print(f"FAIL: mailbox_strategy {strategy!r}")
        ok = False
    else:
        print("PASS: mailbox_strategy=dedicated_account")
    if not agent or str(agent).strip().lower() == "jarvis":
        print(f"FAIL: agent_display_name {agent!r}")
        ok = False
    else:
        print(f"PASS: agent_display_name={agent}")

    sys.path.insert(0, str(skills / "_shared"))
    import tenant_config as tc  # noqa: WPS433

    home = Path(os.environ.get("HERMES_HOME", f"/opt/{slug}/hermes"))
    db = Path(os.environ.get("JARVIS_ACCOUNTS_DB", str(home / "state" / "jarvis_accounts.db")))
    google_email = ""
    if db.is_file():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT google_email FROM google_connection LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        con.close()
        if row and row[0]:
            google_email = str(row[0]).strip()
    if not google_email:
        print("PASS: google_connection absent (scratch may be disconnected)")
        return 0 if ok else 1
    matched = tc.announce_assistant_mailbox_mismatch(google_email, ident.get("assistant_address") or "")
    print(f"google_connection.google_email={google_email}")
    if not matched:
        print("FAIL: google_email != assistant_address (announced)")
        ok = False
    else:
        print("PASS: google_email == assistant_address")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
