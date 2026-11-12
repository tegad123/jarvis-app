#!/usr/bin/env python3
"""MLS search (docs/plans/browser-har-comps.md, steps 11 onward).

Runs against the verify sandbox's databases (docs/tasks/tasks_sandbox.sh,
started first; TASKS_SANDBOX names it) in a child process with a stand-in
tenant_config, so app.mls and app.browser are read exactly as the app reads
them. No browser, no network, never :8700 or :8710.

Step 11, availability and settings:
- each condition of plan section 7 off in turn gives its own `why` and moves
  its own counter by exactly one; all on gives available and moves none
  (the presence twin); count=False moves nothing;
- an invalid app.mls reads as config_invalid with mls_config_invalid +1, and
  the provisioner refuses the same block; out-of-range caps, an http or
  off-site start_url, unknown keys and a start_url on a headless or disabled
  browser are refused; a valid block renders with its defaults;
- there is no MLS person feature: accounts.FEATURES is unchanged.
--canary makes the child ignore the person's browser feature: the
browser_feature_off check must FAIL.
"""
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SB = pathlib.Path(os.environ.get("TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox"))
CANARY = "--canary" in sys.argv

ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({extra})" if extra and not cond else ""))


CHILD = r'''
import json, os, sys, types
sys.path.insert(0, os.environ["APP_ROOT"])
CFG = {}
tc = types.ModuleType("tenant_config")
def _get(key, default=None):
    cur = CFG
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
tc.get = _get
sys.modules["tenant_config"] = tc
from app import accounts, mls, threads
if os.environ.get("CANARY") == "1":
    accounts.feature_on = lambda person, feature: True
GOOD = {"browser": {"mode": "headed"}, "mls": {"site": "matrix.example", "start_url": "https://matrix.example/search"}}
def count(n):
    return threads.bump_counter(n, 0)
def why_with(cfg, person="omar", env_on=True):
    CFG.clear(); CFG["app"] = json.loads(json.dumps(cfg))
    if env_on:
        os.environ["JARVIS_BROWSER"] = "on"
    else:
        os.environ["JARVIS_BROWSER"] = "off"
    return mls.available_for(person)
WHYS = list(mls.UNAVAILABLE)
out = {"features": list(accounts.FEATURES), "cases": {}}
was_on = accounts.feature_on("omar", "browser")
accounts.set_feature("omar", "browser", True, by="dana")
cases = [
    ("browser_off", GOOD, "omar", False),
    ("not_headed", {**GOOD, "browser": {"mode": "headless"}}, "omar", True),
    ("not_headed_missing", {"mls": GOOD["mls"]}, "omar", True),
    ("config_invalid", {**GOOD, "mls": {**GOOD["mls"], "max_results": 500}}, "omar", True),
    ("not_configured", {**GOOD, "mls": {"site": "matrix.example"}}, "omar", True),
    ("not_a_person", GOOD, None, True),
    ("available", GOOD, "omar", True),
]
for name, cfg, person, env_on in cases:
    before = {w: count(f"mls_refused_{w}") for w in WHYS}
    inv0 = count("mls_config_invalid")
    why = why_with(cfg, person, env_on)
    after = {w: count(f"mls_refused_{w}") for w in WHYS}
    out["cases"][name] = {"why": why, "delta": {w: after[w] - before[w] for w in WHYS if after[w] != before[w]},
                          "invalid_delta": count("mls_config_invalid") - inv0}
accounts.set_feature("omar", "browser", False, by="dana")
before = count("mls_refused_browser_feature_off")
out["cases"]["browser_feature_off"] = {"why": why_with(GOOD), "delta": {
    "browser_feature_off": count("mls_refused_browser_feature_off") - before}}
accounts.set_feature("omar", "browser", True, by="dana")
before = {w: count(f"mls_refused_{w}") for w in WHYS}
CFG.clear(); CFG["app"] = json.loads(json.dumps(GOOD))
out["quiet_why"] = mls.available_for("omar", count=False)
out["quiet_delta"] = sum(count(f"mls_refused_{w}") - before[w] for w in WHYS)
accounts.set_feature("omar", "browser", was_on, by="dana")
out["restored"] = accounts.feature_on("omar", "browser") == was_on
print("RESULT " + json.dumps(out))
'''


def provisioner_checks():
    sys.path.insert(0, str(ROOT / "scripts"))
    import provision_tenant as pt  # noqa: PLC0415

    def refused(manifest):
        try:
            pt.mls(manifest)
        except pt.Refuse:
            return True
        return False

    headed = {"browser": "enabled", "browser_mode": "headed"}
    good = {"site": "harmls.com", "start_url": "https://matrix.harmls.com/Matrix/Search"}
    r = pt.mls({**headed, "mls": good})
    ck("provisioner: a valid block renders with its defaults filled in",
       r["start_url"] == good["start_url"] and r["max_results"] == 25 and r["pause_seconds"] == [4, 9]
       and r["max_page_loads_per_request"] == 10 and "available" not in r, r)
    ck("provisioner: no mls block renders unconfigured (no start_url), not refused",
       pt.mls({})["start_url"] == "")
    for label, block, man in [
        ("max_results above 100", {**good, "max_results": 101}, headed),
        ("page loads per request above 10", {**good, "max_page_loads_per_request": 11}, headed),
        ("daily requests above 20", {**good, "max_requests_per_person_per_day": 21}, headed),
        ("daily page loads above 100", {**good, "max_page_loads_per_person_per_day": 101}, headed),
        ("keep_days above 90", {**good, "keep_days": 91}, headed),
        ("pause under 3 seconds", {**good, "pause_seconds": [1, 5]}, headed),
        ("pause high before low", {**good, "pause_seconds": [9, 4]}, headed),
        ("cooldown 0 hours", {**good, "cooldown_hours_after_block": 0}, headed),
        ("a boolean as a number", {**good, "max_results": True}, headed),
        ("an http start_url", {**good, "start_url": "http://matrix.harmls.com/x"}, headed),
        ("an off-site start_url", {**good, "start_url": "https://evil.example/x"}, headed),
        ("credentials in start_url", {**good, "start_url": "https://u:p@matrix.harmls.com/x"}, headed),
        ("an unknown key", {**good, "available": True}, headed),
        ("per-request above per-day", {**good, "max_page_loads_per_person_per_day": 5}, headed),
        ("start_url with a headless browser", good, {"browser": "enabled"}),
        ("start_url with the browser off", good, {"browser_mode": "headed"}),
    ]:
        ck(f"provisioner refuses: {label}", refused({**man, "mls": block}))
    ck("provisioner: a lower cap than the limit is accepted (presence twin)",
       pt.mls({**headed, "mls": {**good, "max_results": 10, "keep_days": 7}})["max_results"] == 10)


def main() -> int:
    for k in ("AUTH_BASE", "APP_PORT"):
        if os.environ.get(k, "").endswith(("8700", "8710")):
            sys.exit("REFUSING: live or testco port")
    print("── step 11: availability and settings")
    provisioner_checks()
    base = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC", "DISPLAY"))}
    env = {**base,
           "APP_ROOT": str(ROOT), "APP_GOOGLE_TOKEN_KEY": (SB / "token_key").read_text().strip(),
           "HERMES_HOME": str(SB / "hermes"), "JARVIS_APP_ROOT": str(ROOT),
           "JARVIS_SKILLS_ROOT": "/opt/jarvis-verify-fixture/skills/openclaw-imports",
           "JARVIS_RUNTIME_CONFIG": str(SB / "runtime.json"),
           "JARVIS_IDENTITY_CONFIG": "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json",
           "JARVIS_ACCOUNTS_DB": str(SB / "accounts.db"), "JARVIS_THREADS_DB": str(SB / "threads.db"),
           "JARVIS_TASKS_DB": str(SB / "tasks.db"), "JARVIS_LEADS_DB": str(SB / "leads.db"),
           "JARVIS_ACTIVITY_FEED": str(SB / "feed.jsonl"), "JARVIS_PG_DSN": (SB / "pg_dsn").read_text().strip(),
           "JARVIS_BROWSER": "on", "JARVIS_BROWSER_SOCKET": "/nonexistent/w.sock",
           "CANARY": "1" if CANARY else "0"}
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(ROOT), capture_output=True, text=True,
                       timeout=120)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(p.stderr[-3000:])
        ck("child ran", False)
        return 1
    o = json.loads(line[-1][7:])
    c = o["cases"]
    for why in ("browser_off", "not_headed", "config_invalid", "not_configured", "not_a_person"):
        ck(f"{why}: refused with its own reason, its counter +1 and no other",
           c[why]["why"] == why and c[why]["delta"] == {why: 1}, c[why])
    ck("not_headed: an absent app.browser reads as headless", c["not_headed_missing"]["why"] == "not_headed",
       c["not_headed_missing"])
    ck("config_invalid: mls_config_invalid +1", c["config_invalid"]["invalid_delta"] == 1, c["config_invalid"])
    ck("browser_feature_off: refused for a person whose browser is off, its counter +1",
       c["browser_feature_off"]["why"] == "browser_feature_off"
       and c["browser_feature_off"]["delta"] == {"browser_feature_off": 1}, c["browser_feature_off"])
    ck("available: everything on gives None and moves no refusal counter (presence twin)",
       c["available"]["why"] is None and c["available"]["delta"] == {} and c["available"]["invalid_delta"] == 0,
       c["available"])
    ck("count=False reports the reason without moving a counter", o["quiet_why"] is None and o["quiet_delta"] == 0, o)
    ck("no MLS person feature: FEATURES unchanged",
       o["features"] == ["ask", "leads", "tasks", "send", "browser", "imessage"], o["features"])
    ck("omar's browser feature restored", o["restored"])
    n, t = sum(ok), len(ok)
    print(f"\n{n}/{t} passed" + ("  (canary run: a FAIL above is the point)" if CANARY else ""))
    return 0 if n == t else 1


if __name__ == "__main__":
    sys.exit(main())
