"""Approvals v1.1 — the applier's refusals, and the never-appliable allowlist.

The allowlist is load-bearing: this is the path a PERSUASIVE CHAT REQUEST
reaches. `safety.allow_writes` is the switch between this box and a customer's
live mailbox, so "the owner approved it" must not be the bar for arming it.
"""
import copy, json, os, shutil, sys, tempfile
from pathlib import Path

APP = '/root/jarvis-app'
sys.path.insert(0, APP)

results = []
def check(name, ok, extra=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {extra}" if extra else ""))
    results.append(ok)

# Isolated runtime.json so nothing here touches the live tenant config.
SH = Path('/root/.hermes/skills/openclaw-imports/_shared')
tmp = Path(tempfile.mkdtemp())
shutil.copy2(SH / 'runtime.json', tmp / 'runtime.json')
os.environ['JARVIS_RUNTIME_CONFIG'] = str(tmp / 'runtime.json')

sys.path.insert(0, str(SH))
import tenant_config as tc; tc.reset_cache()
from app import config_apply as ca

print("never-appliable keys — refused in CODE, with no override\n")
for key in ("safety.allow_writes", "safety.env_var",
            "credentials.google_token.path", "identity.assistant_address"):
    try:
        ca.check_key(key)
        check(f"{key} refused", False, "IT WAS ACCEPTED")
    except ca.ConfigApplyError as e:
        check(f"{key} refused", "never be changed" in str(e))

print("\nappliable keys are accepted")
for key in ("skills.task-manager.digest_times_local", "client.timezone"):
    try:
        ca.check_key(key); check(f"{key} accepted", True)
    except ca.ConfigApplyError as e:
        check(f"{key} accepted", False, str(e))

print("\nkeys outside the positive allowlist are refused")
for key in ("gateway.base_url", "paths.workspace"):
    try:
        ca.check_key(key); check(f"{key} refused", False, "IT WAS ACCEPTED")
    except ca.ConfigApplyError as e:
        check(f"{key} refused", "not in the set" in str(e))

print("\nstale diff — proposal written against a value that has since moved")
live = json.loads((tmp / 'runtime.json').read_text())
real = live['skills']['task-manager']['digest_times_local']
try:
    ca.apply_changes([{ "key": "skills.task-manager.digest_times_local",
                        "current": ["99:99"], "proposed": ["08:00"] }])
    check("stale diff refused", False, "IT WAS APPLIED")
except ca.ConfigApplyError as e:
    unchanged = json.loads((tmp/'runtime.json').read_text())['skills']['task-manager']['digest_times_local']
    check("stale diff refused, config untouched", "has changed since this was proposed" in str(e) and unchanged == real)

print("\nno partial application when one change in a batch is bad")
before = (tmp/'runtime.json').read_text()
try:
    ca.apply_changes([
        {"key": "client.timezone", "current": live['client']['timezone'], "proposed": "America/New_York"},
        {"key": "safety.allow_writes", "current": False, "proposed": True},
    ])
    check("batch refused", False, "IT WAS APPLIED")
except ca.ConfigApplyError:
    check("batch refused whole; nothing written", (tmp/'runtime.json').read_text() == before)

print("\na valid change really applies, atomically, with a backup")
r = ca.apply_changes([{ "key": "skills.task-manager.digest_times_local",
                       "current": real, "proposed": ["08:00"] }])
after = json.loads((tmp/'runtime.json').read_text())['skills']['task-manager']['digest_times_local']
check("value changed on disk", after == ["08:00"], f"{real} -> {after}")
check("backup written", Path(r['backup']).exists())
tc.reset_cache()
check("readers see the new value (cache reset)", tc.require('skills.task-manager.digest_times_local') == ["08:00"])

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
