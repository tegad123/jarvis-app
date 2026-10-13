#!/usr/bin/env python3
"""A4.1 + A4.2 — the outbound boundary, against a provisioned tenant.

Default invocation is unchanged (testco). Pass --manifest or --opt to point
at any other tenant. Does not talk to :8700. Does not restart units.

    python3 docs/testco-rehearsal/verify_boundary.py
    python3 docs/testco-rehearsal/verify_boundary.py --manifest docs/tenants/clientzero.yaml
"""
from __future__ import annotations
import sys; sys.exit("RETIRED 2026-10-13: testco is retired (docs/platform/decisions.md); this script needs its running services or tree.")  # noqa: E702

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def _load_manifest(path: Path) -> dict:
    if yaml is None:
        sys.exit("PyYAML is required to read --manifest")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        sys.exit(f"{path} is not a mapping")
    return data


def _owner_email(ident: dict, fallback: str) -> str:
    people = ident.get("people") or {}
    for p in people.values():
        if "owner" in (p.get("roles") or []):
            emails = p.get("email") or []
            if emails:
                return str(emails[0])
    return fallback


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Outbound-send boundary suite.")
    ap.add_argument("--manifest", type=Path, help="tenant ops YAML; sets --opt from slug")
    ap.add_argument("--opt", default=None, help="tenant root (default /opt/testco)")
    ap.add_argument("--skills", default=None, help="openclaw-imports path")
    ap.add_argument("--capture-to", default=None,
                    help="reserved-suffix recipient for the capture probe")
    ap.add_argument("--live-feed", type=Path,
                    default=Path("/root/.hermes/state/activity_feed.jsonl"),
                    help="live tenant feed; asserted unchanged (read-only)")
    ap.add_argument("--skip-live-untouched", action="store_true",
                    help="skip the live-feed line-count assertion")
    return ap.parse_args(argv)


def configure(args: argparse.Namespace) -> dict:
    opt = args.opt
    skills = args.skills
    capture = args.capture_to
    slug = "testco"
    if args.manifest:
        man = _load_manifest(args.manifest)
        slug = man["slug"]
        opt = opt or f"/opt/{slug}"
        if capture is None:
            safety = man.get("safety") or {}
            suffixes = safety.get("outbound_recipient_suffix") or [".example"]
            people = man.get("people") or {}
            for p in people.values():
                if "owner" in (p.get("roles") or []):
                    emails = p.get("email") or []
                    if emails:
                        capture = str(emails[0])
                        break
            if capture is None:
                capture = f"probe@{slug}.example.com"
            # Prefer a suffix-matching address when the manifest named one.
            del suffixes
    opt = opt or "/opt/testco"
    skills = skills or f"{opt}/skills/openclaw-imports"
    if capture is None:
        capture = "dana@testco.example.com"
    return {"opt": opt, "skills": skills, "capture": capture, "slug": slug}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = configure(args)
    T, SK = cfg["opt"], cfg["skills"]
    capture = cfg["capture"]
    os.environ.update({
        "HERMES_HOME": f"{T}/hermes",
        "JARVIS_SKILLS_ROOT": SK,
        "JARVIS_RUNTIME_CONFIG": f"{SK}/_shared/runtime.json",
        "JARVIS_IDENTITY_CONFIG": f"{SK}/_shared/identity.json",
        "JARVIS_ACTIVITY_FEED": f"{T}/hermes/state/activity_feed.jsonl",
        "JARVIS_ALLOW_WRITES": os.environ.get("JARVIS_ALLOW_WRITES") or "1",
    })
    sys.path.insert(0, f"{SK}/_shared")
    sys.path.insert(0, f"{SK}/owner-email/scripts")
    import outbound_boundary as ob  # noqa: WPS433
    import notify  # noqa: WPS433
    from send_gate import gated_send  # noqa: WPS433

    ident = {}
    ident_path = Path(SK) / "_shared" / "identity.json"
    if ident_path.is_file():
        ident = json.loads(ident_path.read_text())
        capture = _owner_email(ident, capture)

    ok, fails = [], []

    def ck(n, c, x=""):
        ok.append(bool(c))
        print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" — {x}" if x else ""))
        if not c:
            fails.append(n)

    FEED = Path(notify.feed_path())
    flen = lambda: len([l for l in FEED.read_text().splitlines() if l.strip()]) if FEED.exists() else 0
    since = lambda n: [json.loads(l) for l in FEED.read_text().splitlines() if l.strip()][n:]

    live_before = None
    if not args.skip_live_untouched and args.live_feed.is_file():
        live_before = sum(1 for l in args.live_feed.open() if l.strip())

    print(f"\nboundary tenant={T} capture_to={capture}")
    print(f"boundary: active={ob.active()} suffixes={ob.suffixes()} violations={ob.reserved_violations()}")
    ck(f"boundary active for {Path(T).name}", ob.active())
    ck("every configured suffix is RFC-reserved", ob.reserved_violations() == [])

    print("\n── A4.1 refusals: real domains ────────────────────────────────")
    for addr, label in (("spencerhuck@34dev.com", "live tenant domain"),
                        ("blake.dennis@cqhouston.com", "affiliated real domain"),
                        ("someone@gmail.com", "consumer mailbox")):
        before = flen(); err = io.StringIO(); refused = False; detail = ""
        try:
            with contextlib.redirect_stderr(err):
                r = gated_send(to=addr, subject=f"Post-reboot boundary probe — {label}",
                               body="must not leave the box", transport="smtp")
            refused = bool(r.get("blocked")) and not r.get("sent"); detail = str(r)[:60]
        except ob.RecipientRefused as e:
            refused = True; detail = str(e)[:60]
        except Exception as e:
            detail = f"WRONG FAILURE MODE: {type(e).__name__}: {e}"
        ck(f"{addr} refused ({label})", refused, detail)
        ck("  full address on the OPERATOR channel (stderr/journal)", addr in err.getvalue())
        new = [e for e in since(before) if e.get("kind") == "alert"]
        ck("  announced on the feed (not a silent drop)", len(new) >= 1,
           new[0]["headline"][:60] if new else "NO EVENT")
        if new:
            ck("  masked on the tenant feed (no real address)",
               addr not in json.dumps(new[0]) and "***" in json.dumps(new[0]))

    print("\n── A4.2 capture: a reserved recipient ─────────────────────────")
    before, ob_before = flen(), len(ob.read_outbox())
    r = gated_send(to=capture, subject="Post-reboot capture probe",
                   body="Confirming Sunday 2pm.", transport="smtp")
    ck("not sent to a transport", r.get("sent") is False, str(r)[:70])
    ck("reported as captured", r.get("captured") is True)
    ck("landed in the capture outbox", len(ob.read_outbox()) == ob_before + 1,
       f"{ob_before} -> {len(ob.read_outbox())}")
    box = ob.read_outbox()[-1]
    ck("outbox record carries recipient, subject, body",
       box["to"] == [capture] and box["subject"] and box["body"])
    info = [e for e in since(before) if e.get("kind") == "info"]
    ck("visible on the feed", len(info) >= 1, info[0]["headline"][:60] if info else "NO EVENT")
    ck("feed event names recipient and subject",
       bool(info) and capture in (info[0].get("detail") or ""))

    print("\n── Cc is checked too ──────────────────────────────────────────")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            gated_send(to=capture, subject="Cc probe", body="x",
                       cc="someone@gmail.com", transport="smtp")
        ck("blocked Cc refused", False, "IT WENT THROUGH")
    except ob.RecipientRefused:
        ck("blocked Cc refused", True)

    print("\n── live tenant untouched ──────────────────────────────────────")
    if args.skip_live_untouched:
        ck("live feed check skipped (not testco default)", True)
        live_n = live_before
    else:
        live_n = sum(1 for l in args.live_feed.open() if l.strip()) if args.live_feed.is_file() else None
        ck("live feed line count unchanged",
           live_before is not None and live_n == live_before,
           f"{live_before} -> {live_n}")
    ck("outbox lives inside this tenant", str(ob.outbox_path()).startswith(str(Path(T).resolve())))
    feed_text = FEED.read_text() if FEED.exists() else ""
    ck("no real domain anywhere in this tenant's feed",
       not any(d in feed_text for d in ("34dev", "cqhouston", "@gmail.com")))

    print(f"\n{sum(ok)}/{len(ok)} passed")
    if fails:
        print("FAILED: " + "; ".join(fails))
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
