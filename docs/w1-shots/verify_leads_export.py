"""Export connector verification — round-trip against a COPY, never the live sheet.

THE HARNESS REFUSES TO TOUCH THE LIVE SHEET, by construction.
    Same pattern as purge_tenant_data.sh refusing to run on this box: the thing
    that could do damage checks its own target before doing anything, rather
    than relying on whoever invokes it to pass the right argument. A test that
    writes a customer's live lead sheet is not a test failure — it is an
    incident — so the guard is here, in the harness, not in the instructions.
"""
import os, sys, tempfile, shutil
from pathlib import Path

sys.path.insert(0, '/root/jarvis-app')
sys.path.insert(0, '/root/.hermes/skills/openclaw-imports/_shared')

import tenant_config as tc

# The one sheet this harness must never write.
LIVE_SHEET_ID = tc.get('skills.lead-reply.sheet_id', '')

ok = []
def ck(n, c, x=''):
    print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" — {x}" if x else "")); ok.append(c)


def refuse_if_live(target: str) -> None:
    """Abort loudly if the target is the tenant's real sheet."""
    if LIVE_SHEET_ID and target == LIVE_SHEET_ID:
        print("\n  REFUSING: the target is the tenant's LIVE lead sheet.")
        print(f"  {target}")
        print("  Export tests run against a COPY. Duplicate the sheet, pass the copy's id,")
        print("  and never point a write test at the sheet a business runs on.")
        sys.exit(3)


print("0. the harness's own guard\n")
try:
    refuse_if_live(LIVE_SHEET_ID)
    ck("refuses when handed the live sheet id", False, "it did not refuse")
except SystemExit as e:
    ck("refuses when handed the live sheet id", e.code == 3, f"exit {e.code}")
refuse_if_live("some-other-copy-id")     # must NOT exit
ck("permits a different (copy) id", True)

# Isolate the store AND its side effects — pattern 3.
tmp = Path(tempfile.mkdtemp())
os.environ['JARVIS_LEADS_DB'] = str(tmp / 'leads.db')
os.environ['JARVIS_ACTIVITY_FEED'] = str(tmp / 'feed.jsonl')
from app import leads_store as ls, leads_export as ex
ls._initialised = False; ls.DB_PATH = tmp / 'leads.db'; ls.init()

print("\n1. off by default, and gated when on\n")
ck("export_enabled is False out of the box", ex.enabled() is False)
try:
    ex.export("copy-sheet-id", dry_run=False)
    ck("disabled export refuses", False, "it ran")
except ex.ExportError as e:
    ck("disabled export refuses", "disabled" in str(e).lower())

# Turn it on in-process only, then prove the WRITE GUARD still blocks.
_orig = tc.get
tc.get = lambda k, d=None: True if k == 'skills.leads.export_enabled' else _orig(k, d)
import write_guard
os.environ.pop(write_guard.ENV_FLAG, None)
try:
    ex.export("copy-sheet-id", dry_run=False)
    ck("write guard blocks a real export", False, "it was not blocked")
except write_guard.WritesDisabled:
    ck("write guard blocks a real export", True, "WritesDisabled raised before any sheet call")
except Exception as e:
    ck("write guard blocks a real export", False, f"{type(e).__name__}: {str(e)[:60]}")

print("\n2. dry run reaches the sheet layer without writing\n")
# NOT `ck(..., True)`. An assertion that cannot fail proves nothing; this one
# patches the two things a dry run must never reach and checks they stay unused.
from unittest.mock import patch
with patch('app.leads_export.block_if_readonly') as guard, \
     patch('app.leads_export.google_creds.gspread_client') as gs:
    gs.return_value.open_by_key.return_value.worksheets.return_value = []
    ex.export("copy-sheet-id", dry_run=True)
    ck("dry run never invokes the write guard", guard.call_count == 0)
    ck("dry run never calls batch_update", 
       gs.return_value.open_by_key.return_value.batch_update.call_count == 0)

print("\n3. it writes by HEADER, never by letter\n")
header = ["Date","Lead Name","Property Toured","Price Point","Notes","Lead Temperature",
          "Follow-up Status","Status Of Lead","Next Follow Up Date","Assigned Agent",
          "Email","Phone","Internal Agent Email","Lead ID","Nudge Count","Last Nudge Date","Thread ID"]
cols, key = ex._locate(header)
ck("Lead ID located at N (13) by header, not by the old 'P'", key == 13, f"key index {key}")
ck("status located by header", cols.get('status') == 7, f"index {cols.get('status')}")
ck("temperature located by header", cols.get('temperature') == 5, f"index {cols.get('temperature')}")
ck("Thread ID (Q/16) is NOT an owned column — export leaves it alone",
   16 not in cols.values(), f"owned indices {sorted(cols.values())}")
ck("Internal Agent Email (M/12) is NOT owned either", 12 not in cols.values())

print("\n4. a shifted sheet does not silently mis-write\n")
shifted = ["Date","Lead Name","NEW COLUMN","Lead ID","Status Of Lead"]
cols2, key2 = ex._locate(shifted)
ck("Lead ID follows its header when columns shift", key2 == 3, f"key index {key2}")
ck("a missing owned header is reported, not guessed",
   'temperature' not in cols2 and 'phone' not in cols2)

tc.get = _orig
shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
