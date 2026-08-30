"""Mutation test for the never-appliable allowlist.

Two questions this answers that the unit tests cannot:
  1. Does a safety.* change actually refuse through the FULL approval path —
     seeded request, real decide() call — not just via check_key() directly?
  2. Is the refusal STRUCTURAL? If the allowlist can be neutered by flipping a
     value, it is a config setting wearing a constant's clothes.
"""
import copy, json, os, shutil, subprocess, sys, tempfile, pathlib

APP = '/root/jarvis-app'
SH = pathlib.Path('/root/.hermes/skills/openclaw-imports/_shared')
SRC = pathlib.Path(APP) / 'app' / 'config_apply.py'
backup_src = SRC.read_text()

def full_path_attempt(tmp):
    """Seed a safety.* config-change and drive the REAL decide() on it."""
    env = dict(os.environ,
               JARVIS_RUNTIME_CONFIG=str(tmp / 'runtime.json'),
               JARVIS_APPROVALS_DIR=str(tmp))
    code = f'''
import sys, json, pathlib
sys.path.insert(0, {APP!r}); sys.path.insert(0, {str(SH)!r})
from app import approvals as a
a.REQUESTS_PATH  = pathlib.Path({str(tmp)!r}) / "approvals.jsonl"
a.DECISIONS_PATH = pathlib.Path({str(tmp)!r}) / "decisions.jsonl"
req = {{"id": "atk-1", "ts": "2026-08-18T00:00:00+00:00", "tenant": "t", "skill": "jarvis",
        "kind": "config-change", "summary": "arm writes",
        "payload": {{"changes": [{{"key": "safety.allow_writes", "current": False,
                                   "proposed": True}}]}}}}
a._append_jsonl(a.REQUESTS_PATH, req)
try:
    a.decide("atk-1", "approved", "owner")
    print("APPLIED")
except a.ConfigChangeRefused as e:
    print("REFUSED")
except Exception as e:
    print("OTHER:" + type(e).__name__)
live = json.loads(pathlib.Path({str(tmp)!r} + "/runtime.json").read_text())
print("allow_writes=" + str(live["safety"]["allow_writes"]))
'''
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env)
    return r.stdout.strip()

def fresh_tmp():
    tmp = pathlib.Path(tempfile.mkdtemp())
    shutil.copy2(SH / 'runtime.json', tmp / 'runtime.json')
    return tmp

ok = []
print("1. safety.allow_writes through the FULL approval path\n")
tmp = fresh_tmp()
out = full_path_attempt(tmp)
refused = "REFUSED" in out and "allow_writes=False" in out
print(f"  {'PASS' if refused else 'FAIL'}  refused, and the switch is still False")
print(f"        {out.replace(chr(10), ' | ')}")
ok.append(refused)
shutil.rmtree(tmp, ignore_errors=True)

print("\n2. mutation — try to defeat the allowlist and prove the test notices\n")
mutations = {
    "NEVER_APPLIABLE emptied":
        lambda s: s.replace('NEVER_APPLIABLE = ("safety", "credentials", "identity")',
                            'NEVER_APPLIABLE = ()'),
    "safety.* added to the positive allowlist":
        lambda s: s.replace('    "skills.",', '    "skills.",\n    "safety.",'),
}
for label, mutate in mutations.items():
    SRC.write_text(mutate(backup_src))
    tmp = fresh_tmp()
    out = full_path_attempt(tmp)
    # With NEVER_APPLIABLE emptied the prefix check is gone; with safety.* in the
    # positive list the second gate is gone. EITHER alone should still be caught
    # by the other — that is what "by construction" has to mean.
    still_safe = "REFUSED" in out and "allow_writes=False" in out
    print(f"  {'DEFENCE HELD' if still_safe else 'GOT THROUGH  '}  {label}")
    print(f"        {out.replace(chr(10), ' | ')}")
    ok.append(still_safe)
    shutil.rmtree(tmp, ignore_errors=True)
    SRC.write_text(backup_src)

SRC.write_text(backup_src)
print(f"\n{sum(ok)}/{len(ok)} — allowlist holds")
sys.exit(0 if all(ok) else 1)
