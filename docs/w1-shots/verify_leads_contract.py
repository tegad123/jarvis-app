"""Both readers must answer with the SAME shape, or swapping source breaks the screen."""
import sys
sys.exit("REFUSING: this script works on live (/root/jarvis-app or /root/.hermes); port it to the platform tree (VERIFY_APP_ROOT, VERIFY_SKILLS_ROOT) before running it")
import os, sys, tempfile, shutil, json
from pathlib import Path
sys.path.insert(0, '/root/jarvis-app')

ok=[]
def ck(n,c,x=''):
    print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" — {x}" if x else "")); ok.append(c)

from app import leads_source as lsrc
sheet = lsrc.load()                      # real source on this box
ck("sheet source answers", sheet['source'] == 'sheet' and sheet['count'] > 0, f"{sheet['count']} leads")

# Now the store path, isolated.
tmp = Path(tempfile.mkdtemp())
os.environ['JARVIS_LEADS_DB'] = str(tmp / 'leads.db')
# Isolate the FEED too: the store emits on create, so a harness that only
# isolates the database still writes lead events into the live feed.
os.environ['JARVIS_ACTIVITY_FEED'] = str(tmp / 'feed.jsonl')
os.environ['JARVIS_LEADS_SOURCE'] = 'store'
from app import leads_store as ls
ls._initialised = False; ls.DB_PATH = tmp / 'leads.db'; ls.init()
ls.create("Marcus Webb", property="4211 Oak Forest Dr", price_point="$525k",
          temperature="Hot", status="No Response", owner_person="spencer",
          follow_up="Emailed 8/20", created_by="jarvis")
ls.create("Dana Reyes", temperature="Warm", owner_person="alex")
store = lsrc.load()
ck("store source answers", store['source'] == 'store' and store['count'] == 2)

print("\ncontract equality")
ck("top-level keys identical", set(sheet) - {'source'} == set(store) - {'source'},
   f"sheet-only={set(sheet)-set(store)} store-only={set(store)-set(sheet)}")
sk, tk = set(sheet['leads'][0]), set(store['leads'][0])
ck("every sheet lead field is present on a store lead", sk <= tk, f"missing={sk-tk}")
ck("counts keys identical", set(sheet['counts']) == set(store['counts']), str(set(store['counts'])))
types_match = all(type(store['leads'][0][k]) is type(sheet['leads'][0][k]) for k in sk)
ck("field TYPES match (a string where the screen expects a string)", types_match,
   str({k:(type(sheet['leads'][0][k]).__name__, type(store['leads'][0][k]).__name__)
        for k in sk if type(store['leads'][0][k]) is not type(sheet['leads'][0][k])}))

print("\nstore rows carry what the sheet cannot")
row = [l for l in store['leads'] if l['name'] == 'Marcus Webb'][0]
ck("created_by surfaces for Jarvis-created leads", row['created_by'] == 'jarvis')
ck("temperature_rank derived, not stored", row['temperature_rank'] == 3)
ck("needs_touch derived from status", row['needs_touch'] is True)
ck("follow_up carried as free text", row['follow_up'] == 'Emailed 8/20')

os.environ.pop('JARVIS_LEADS_SOURCE'); shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
