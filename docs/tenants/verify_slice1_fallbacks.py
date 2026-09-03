#!/usr/bin/env python3
"""Boxed slice-1 canaries. Does not talk to :8710 or :8700. Does not restart anything.

Binding: missing timezone / digest_times_local announce; missing app_root refuses;
unset webhook port refuses to bind; omitted lead_revival_hours announces (boxed).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

SKILLS_SHARED = Path(os.environ.get(
    "SLICE1_SKILLS_SHARED",
    "/tmp/openclaw-imports-feat-tenant-manifest/_shared",
))
if not SKILLS_SHARED.is_dir():
    sys.stderr.write(f"FAIL: skills _shared not at {SKILLS_SHARED}\n")
    sys.exit(2)

sys.path.insert(0, str(SKILLS_SHARED))
import tenant_config as tc  # noqa: E402

PASS = 0
FAIL = 0


def ck(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"FAIL  {name}" + (f"  {detail}" if detail else ""))


def _write_runtime(data: dict) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


def _box(data: dict):
    path = _write_runtime(data)
    os.environ["JARVIS_RUNTIME_CONFIG"] = path
    os.environ.pop("HERMES_API_URL", None)
    tc.reset_cache()
    return path


def main() -> int:
    print(f"skills _shared: {SKILLS_SHARED}")
    print(f"tenant_config: {tc.__file__}")

    # --- missing timezone announces UTC
    p = _box({"client": {"slug": "box"}, "skills": {}, "paths": {}})
    try:
        name = tc.timezone_name()
        ck("missing client.timezone → UTC", name == "UTC", repr(name))
        ck("missing client.timezone announces",
           tc.fallback_count("client.timezone") >= 1,
           f"count={tc.fallback_count('client.timezone')}")
    finally:
        Path(p).unlink(missing_ok=True)

    # --- missing digest_times_local announces ["05:00"]
    p = _box({"client": {"timezone": "UTC"}, "skills": {"task-manager": {}}, "paths": {}})
    try:
        slots = tc.get("skills.task-manager.digest_times_local", ["05:00"])
        ck("missing digest_times_local → ['05:00']", slots == ["05:00"], repr(slots))
        ck("missing digest_times_local announces",
           tc.fallback_count("skills.task-manager.digest_times_local") >= 1,
           f"count={tc.fallback_count('skills.task-manager.digest_times_local')}")
    finally:
        Path(p).unlink(missing_ok=True)

    # --- missing paths.app_root refuses (no /root/jarvis-app)
    p = _box({"client": {}, "paths": {}, "skills": {}})
    try:
        refused = False
        msg = ""
        try:
            tc.app_root()
        except tc.KeyMissing as exc:
            refused = True
            msg = str(exc)
        ck("missing paths.app_root refuses", refused, msg[:120])
        ck("missing paths.app_root does not announce a live-path fallback",
           tc.fallback_count("paths.app_root") == 0
           and "/root/jarvis-app" not in msg)
    finally:
        Path(p).unlink(missing_ok=True)

    # --- unset webhook port refuses to bind
    p = _box({"skills": {"gmail-push-trigger": {}}, "paths": {}})
    try:
        refused = False
        msg = ""
        try:
            tc.webhook_port()
        except tc.BindRefused as exc:
            refused = True
            msg = str(exc)
        ck("unset webhook_port refuses BindRefused", refused, msg[:160])
        ck("unset webhook_port never defaults 18790", "18790" in msg and "never" in msg.lower())
    finally:
        Path(p).unlink(missing_ok=True)

    # --- lead_revival_hours omitted announces 48 (boxed; not testco)
    p = _box({"skills": {"sweeps": {"stale_email_hours": 24}}, "paths": {}})
    try:
        v = tc.get("skills.sweeps.lead_revival_hours", 48)
        ck("omitted lead_revival_hours → 48", v == 48, repr(v))
        ck("omitted lead_revival_hours announces (boxed canary)",
           tc.fallback_count("skills.sweeps.lead_revival_hours") >= 1,
           f"count={tc.fallback_count('skills.sweeps.lead_revival_hours')}")
    finally:
        Path(p).unlink(missing_ok=True)

    # --- gateway.base_url require unless env
    p = _box({"gateway": {}, "paths": {}})
    try:
        refused = False
        try:
            tc.gateway_url()
        except tc.KeyMissing:
            refused = True
        ck("gateway.base_url required when HERMES_API_URL unset", refused)
        os.environ["HERMES_API_URL"] = "http://127.0.0.1:8662"
        tc.reset_cache()
        os.environ["JARVIS_RUNTIME_CONFIG"] = p
        ck("HERMES_API_URL wins when set", tc.gateway_url() == "http://127.0.0.1:8662")
    finally:
        os.environ.pop("HERMES_API_URL", None)
        Path(p).unlink(missing_ok=True)

    # --- dual-source mailbox mismatch announces
    p = _box({"client": {"slug": "box"}, "skills": {}, "paths": {}})
    try:
        ok = tc.announce_assistant_mailbox_mismatch(
            "owner@acmerealty.com", "sofia@acmerealty.com"
        )
        ck("mailbox mismatch returns False", ok is False)
        ck(
            "mailbox mismatch announces",
            tc.mismatch_count(
                "identity.assistant_address vs google_connection.google_email"
            ) >= 1,
        )
    finally:
        Path(p).unlink(missing_ok=True)

    # --- digest_tick.slots() uses announcing get (invoke what we changed)
    p = _box({"client": {"timezone": "UTC"}, "skills": {"task-manager": {}}, "paths": {}})
    try:
        sys.path.insert(0, str(SKILLS_SHARED.parent / "task-manager" / "scripts"))
        import digest_tick  # noqa: E402
        slots = digest_tick.slots()
        ck("digest_tick.slots() falls back to 05:00", slots == [(5, 0)], repr(slots))
        ck("digest_tick.slots() announced",
           tc.fallback_count("skills.task-manager.digest_times_local") >= 1)
    except Exception:
        ck("digest_tick.slots() invoked", False, traceback.format_exc()[:400])
    finally:
        Path(p).unlink(missing_ok=True)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    os.environ.pop("JARVIS_RUNTIME_CONFIG", None)
    sys.exit(main())
