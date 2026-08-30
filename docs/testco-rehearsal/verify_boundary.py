#!/usr/bin/env python3
"""A4.1 + A4.2 — the outbound boundary, re-asserted post-reboot.

Rebuilt after the reboot wiped /tmp. Triggers real sends through gated_send,
the production path, against the RUNNING testco configuration.
"""
import io, contextlib, json, os, sys
from pathlib import Path

T, SK = "/opt/testco", "/opt/testco/skills/openclaw-imports"
os.environ.update({
    "HERMES_HOME": f"{T}/hermes", "JARVIS_SKILLS_ROOT": SK,
    "JARVIS_RUNTIME_CONFIG": f"{SK}/_shared/runtime.json",
    "JARVIS_IDENTITY_CONFIG": f"{SK}/_shared/identity.json",
    "JARVIS_ACTIVITY_FEED": f"{T}/hermes/state/activity_feed.jsonl",
    "JARVIS_ALLOW_WRITES": "1",
})
sys.path.insert(0, f"{SK}/_shared"); sys.path.insert(0, f"{SK}/owner-email/scripts")
import outbound_boundary as ob   # noqa: E402
import notify                    # noqa: E402
from send_gate import gated_send # noqa: E402

ok, fails = [], []
def ck(n, c, x=""):
    ok.append(bool(c)); print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" — {x}" if x else ""))
    if not c: fails.append(n)

FEED = Path(notify.feed_path())
flen = lambda: len([l for l in FEED.read_text().splitlines() if l.strip()]) if FEED.exists() else 0
since = lambda n: [json.loads(l) for l in FEED.read_text().splitlines() if l.strip()][n:]

print(f"\nboundary: active={ob.active()} suffixes={ob.suffixes()} violations={ob.reserved_violations()}")
ck("boundary active for testco", ob.active())
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
r = gated_send(to="dana@testco.example.com", subject="Post-reboot capture probe",
               body="Confirming Sunday 2pm.", transport="smtp")
ck("not sent to a transport", r.get("sent") is False, str(r)[:70])
ck("reported as captured", r.get("captured") is True)
ck("landed in the capture outbox", len(ob.read_outbox()) == ob_before + 1,
   f"{ob_before} -> {len(ob.read_outbox())}")
box = ob.read_outbox()[-1]
ck("outbox record carries recipient, subject, body",
   box["to"] == ["dana@testco.example.com"] and box["subject"] and box["body"])
info = [e for e in since(before) if e.get("kind") == "info"]
ck("visible on the feed", len(info) >= 1, info[0]["headline"][:60] if info else "NO EVENT")
ck("feed event names recipient and subject",
   bool(info) and "dana@testco.example.com" in (info[0].get("detail") or ""))

print("\n── Cc is checked too ──────────────────────────────────────────")
try:
    with contextlib.redirect_stderr(io.StringIO()):
        gated_send(to="dana@testco.example.com", subject="Cc probe", body="x",
                   cc="someone@gmail.com", transport="smtp")
    ck("blocked Cc refused", False, "IT WENT THROUGH")
except ob.RecipientRefused:
    ck("blocked Cc refused", True)

print("\n── live tenant untouched ──────────────────────────────────────")
live = sum(1 for l in open("/root/.hermes/state/activity_feed.jsonl") if l.strip())
ck("live feed still 82", live == 82, str(live))
ck("outbox lives inside testco", str(ob.outbox_path()).startswith("/opt/testco/"))
ck("no real domain anywhere in testco's feed",
   not any(d in FEED.read_text() for d in ("34dev", "cqhouston", "@gmail.com")))

print(f"\n{sum(ok)}/{len(ok)} passed")
if fails: print("FAILED: " + "; ".join(fails))
sys.exit(0 if all(ok) else 1)
