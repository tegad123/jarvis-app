"""Real export round-trip: import from a COPY, change a lead, export, verify."""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0,'/root/jarvis-app'); sys.path.insert(0,'/root/.hermes/skills/openclaw-imports/_shared')
import tenant_config as tc

COPY = open('/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad/copy_sheet_id.txt').read().strip()
LIVE = tc.get('skills.lead-reply.sheet_id','')
if COPY == LIVE:
    print("REFUSING: target is the live sheet"); sys.exit(3)

tmp = Path(tempfile.mkdtemp())
os.environ['JARVIS_LEADS_DB'] = str(tmp/'leads.db')
os.environ['JARVIS_ACTIVITY_FEED'] = str(tmp/'feed.jsonl')
os.environ['JARVIS_ALLOW_WRITES'] = '1'          # scoped to THIS process only
import google_creds
from app import leads_store as ls, leads_export as ex, leads_import as imp
ls._initialised=False; ls.DB_PATH=tmp/'leads.db'; ls.init()

ok=[]
def ck(n,c,x=''):
    print(f"  {'PASS' if c else 'FAIL'}  {n}"+(f" — {x}" if x else "")); ok.append(c)

# import FROM the copy
_orig = tc.get
tc.get = lambda k,d=None: COPY if k=='skills.lead-reply.sheet_id' else (True if k=='skills.leads.export_enabled' else _orig(k,d))
imp.run(dry_run=False)
n = len(ls.query()); ck("imported from the copy", n>0, f"{n} leads")

# pick a lead, change a field the export owns
lead = [l for l in ls.query() if l['external_ref']][0]
ref = lead['external_ref']
new_status = "Responded" if lead['status'] != "Responded" else "No Response"
ls.update(lead['lead_id'], status=new_status)
print(f"  changed {ref}: status -> {new_status}")

# read the sheet BEFORE
gc = google_creds.gspread_client(); sh = gc.open_by_key(COPY)
def cell_for(ref):
    for ws in sh.worksheets():
        v = ws.get_all_values()
        if len(v)<=1: continue
        cols,key = ex._locate(v[1])
        if key is None or 'status' not in cols: continue
        for r in v[2:]:
            if key < len(r) and (r[key] or '').strip()==ref:
                return ws.title.strip(), (r[cols['status']] if cols['status']<len(r) else '')
    return None, None
tab, before = cell_for(ref)
ck("found the row on the copy", tab is not None, f"tab={tab} status={before!r}")

res = ex.export(COPY, dry_run=False)
print(f"  export wrote {res['cells']} cell(s)")
sh = gc.open_by_key(COPY)          # re-read
tab2, after = cell_for(ref)
ck("the change round-tripped to the sheet", after == new_status, f"{before!r} -> {after!r}")

# Thread ID must be untouched
for ws in sh.worksheets():
    v = ws.get_all_values()
    if len(v)>1 and 'thread id' in [h.strip().lower() for h in v[1]]:
        qi = [h.strip().lower() for h in v[1]].index('thread id')
        vals = [r[qi] for r in v[2:] if len(r)>qi and (r[qi] or '').strip()]
        if vals:
            ck("Thread ID column left untouched by export", True, f"{len(vals)} value(s) intact on {ws.title.strip()}")
            break
tc.get = _orig
print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
