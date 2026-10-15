"""W1 step 2: the leads store's contract, against a scratch DB."""
import sys
sys.exit("REFUSING: this script works on live (/root/jarvis-app or /root/.hermes); port it to the platform tree (VERIFY_APP_ROOT, VERIFY_SKILLS_ROOT) before running it")
import os, sys, tempfile, shutil
from pathlib import Path
tmp = Path(tempfile.mkdtemp())
os.environ['JARVIS_LEADS_DB'] = str(tmp / 'leads.db')
# Isolate the FEED too: the store emits on create, so a harness that only
# isolates the database still writes lead events into the live feed.
os.environ['JARVIS_ACTIVITY_FEED'] = str(tmp / 'feed.jsonl')   # never the live store
sys.path.insert(0, '/root/jarvis-app')
from app import leads_store as ls

ok=[]
def ck(n,c,x=''):
    print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" — {x}" if x else "")); ok.append(c)

ls.init()
print("lifecycle\n")
a = ls.create("Marcus Webb", email="marcus@example.com", source="Zillow",
              temperature="Hot", owner_person="spencer", created_by="jarvis")
ck("create returns a row with an opaque id", a['lead_id'].startswith('ld_'), a['lead_id'])
ck("created_by recorded", a['created_by'] == 'jarvis')
ck("status defaults to New", a['status'] == 'New')
ck("standing rules present", 'owner_person' in a and 'thread_id' in a,
   f"owner={a['owner_person']} thread={a['thread_id']}")

b = ls.update(a['lead_id'], status="Responded", follow_up="Called on 8/20")
ck("partial update touches only what was passed",
   b['status'] == 'Responded' and b['temperature'] == 'Hot' and b['email'] == 'marcus@example.com')
ck("updated_at moves", b['updated_at'] >= a['updated_at'])

print("\narchive is not delete")
c = ls.set_archived(a['lead_id'])
ck("archived flag set", c['archived'] is True)
ck("excluded from the default list", all(l['lead_id'] != a['lead_id'] for l in ls.query()))
ck("still retrievable by id (never hard-deleted)", ls.get(a['lead_id']) is not None)
ck("listed when archived is requested", any(l['lead_id'] == a['lead_id'] for l in ls.query(archived=True)))
ls.set_archived(a['lead_id'], False)

print("\nimport idempotency via external_ref")
x1 = ls.upsert_external("MRT-0010", name="Joaquin Maldonado", temperature="Warm", owner_person="martin")
x2 = ls.upsert_external("MRT-0010", name="Joaquin Maldonado", temperature="Hot", owner_person="martin")
ck("second upsert updates, does not duplicate", x1['lead_id'] == x2['lead_id'])
ck("the update landed", x2['temperature'] == 'Hot')
ck("only one row for that ref", len([l for l in ls.query() if l['external_ref'] == 'MRT-0010']) == 1)
try:
    import sqlite3
    ls._ensure().execute("INSERT INTO leads (lead_id,name,created_at,updated_at,external_ref) VALUES ('x','y',?,?,'MRT-0010')", (ls._now(), ls._now()))
    ck("duplicate external_ref rejected by the DB", False, "insert succeeded")
except Exception as e:
    ck("duplicate external_ref rejected by the DB", 'UNIQUE' in str(e).upper(), type(e).__name__)

print("\nfilters + counts")
ls.create("Cold Caller", temperature="Cold", status="No Response")
ck("needs_touch filter", all(l['status']=='No Response' for l in ls.query(needs_touch=True)))
ck("temperature filter", all(l['temperature']=='Hot' for l in ls.query(temperature='Hot')))
cn = ls.counts()
ck("counts shape matches the screen's contract",
   set(cn) >= {'all','needs_touch','hot','new'}, str(cn))
try:
    ls.create("   ")
    ck("a lead with no name is refused", False, "it was accepted")
except ls.LeadError:
    ck("a lead with no name is refused", True)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
