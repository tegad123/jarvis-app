import os, sys, tempfile
from pathlib import Path
sys.path.insert(0,'/root/jarvis-app'); sys.path.insert(0,'/root/.hermes/skills/openclaw-imports/_shared')
import tenant_config as tc
COPY = open('/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad/copy_sheet_id.txt').read().strip()
if COPY == tc.get('skills.lead-reply.sheet_id',''): sys.exit(3)
tmp = Path(tempfile.mkdtemp())
os.environ.update({'JARVIS_LEADS_DB':str(tmp/'l.db'),'JARVIS_ACTIVITY_FEED':str(tmp/'f.jsonl'),'JARVIS_ALLOW_WRITES':'1'})
from app import leads_store as ls, leads_export as ex, leads_import as imp
ls._initialised=False; ls.DB_PATH=tmp/'l.db'; ls.init()
_o=tc.get
tc.get = lambda k,d=None: COPY if k=='skills.lead-reply.sheet_id' else (True if k=='skills.leads.export_enabled' else _o(k,d))
imp.run(dry_run=False)
a = ex.export(COPY, dry_run=False)['cells']
b = ex.export(COPY, dry_run=False)['cells']
print(f"\n  export run 1: {a} cells   run 2: {b} cells")
print("  IDEMPOTENT" if b == 0 else f"  NOT IDEMPOTENT — {b} cells rewritten with no change in between")
c = ex.export(COPY, dry_run=True)['cells']
print(f"  dry run after: {c} cells would change")
