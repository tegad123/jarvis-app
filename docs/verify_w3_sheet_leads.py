#!/usr/bin/env python3
"""The SHEET lead branch, scope-verified against the live service.

Separate file because it is the one check that cannot run in the sandbox: the
sandbox has no outbound network, so the Google Sheets reader 503s there and the
branch is dead. This runs against :8700 — and is READ-ONLY BY CONSTRUCTION, one
GET per role, no mutation test, no writes. That is what makes pointing it at the
live service acceptable where verify_w3_scope.py refuses to.
"""
import json, os, pathlib, subprocess, sys

BASE = "http://127.0.0.1:8700"
SP = "/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad"
PINS = json.loads(pathlib.Path(f"{SP}/pins.json").read_text())

ok = []
def ck(n, c, x=""):
    ok.append(bool(c)); print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" — {x}" if x else ""))

def get(path, jar, tries=6):
    """Retry through the Sheets per-minute read quota.

    Three roles x one GET each is well inside the quota, but re-running this
    suite while iterating is not — and a 503 from a quota bounce is not a
    scoping failure. Back off and retry rather than report a red that means
    nothing. Anything still failing after this is real.
    """
    import time
    for i in range(tries):
        out = subprocess.run(["curl","-s","-b",jar,"-c",jar,f"{BASE}{path}"],
                             capture_output=True,text=True).stdout
        body = json.loads(out)
        if "429" not in (body.get("detail") or ""):
            return body
        time.sleep(12)
    raise SystemExit("Sheets quota did not clear after retries — rerun in a minute.")

def login(who):
    jar = f"{SP}/sheetjar_{who}.txt"
    pathlib.Path(jar).unlink(missing_ok=True)
    r = subprocess.run(["curl","-s","-c",jar,"-X","POST",f"{BASE}/api/login",
                        "-H","Content-Type: application/json",
                        "-d",json.dumps({"person_key":who,"pin":PINS[who]})],
                       capture_output=True,text=True).stdout
    assert json.loads(r).get("ok"), r
    return jar

J = {w: login(w) for w in ("spencer","tega","martin")}
L = {w: get("/api/leads", J[w]) for w in J}

ck("this really is the sheet branch", L["spencer"].get("source") == "sheet",
   str(L["spencer"].get("source")))
ck("owner sees the whole tracker", L["spencer"]["count"] > 30, str(L["spencer"]["count"]))
ck("admin sees the whole tracker", L["tega"]["count"] == L["spencer"]["count"])
ck("member sees strictly fewer",
   0 < L["martin"]["count"] < L["spencer"]["count"],
   f"{L['martin']['count']} of {L['spencer']['count']}")
ck("every row a member sees is on their own tab",
   {(l.get("agent") or "").lower() for l in L["martin"]["leads"]} == {"martin"},
   str({l.get("agent") for l in L["martin"]["leads"]}))
ck("the member's tab list narrows too",
   L["martin"]["tabs"] == ["Martin"], str(L["martin"]["tabs"]))
ck("the count chips agree with the rows",
   L["martin"]["counts"]["all"] == L["martin"]["count"])
# CO-LISTING IS REAL, and this assertion has to know it. The tracker has at
# least one lead (MRT-0003, "Cori Redfield + husband") sitting on two agents'
# tabs because two agents work it. Comparing bare names calls that a leak; it
# is not one. What must never appear in a member's list is a lead worked ONLY
# by someone else — so compare against names EXCLUSIVE to other agents.
mine = {l["name"] for l in L["martin"]["leads"]}
by_name = {}
for l in L["spencer"]["leads"]:
    by_name.setdefault(l["name"], set()).add((l.get("agent") or "").lower())
exclusive_to_others = {n for n, agents in by_name.items() if "martin" not in agents}
co_listed = {n for n, agents in by_name.items() if len(agents) > 1}
ck("no lead worked only by someone else appears",
   not (mine & exclusive_to_others), str(mine & exclusive_to_others))
# ...and co-listing is not always WITH martin: BLK-0006 is shared by Spencer
# and Blake. Only the ones martin is actually on should reach him.
martin_co = {n for n in co_listed if "martin" in by_name[n]}
others_co = co_listed - martin_co
ck("a co-listed lead martin is ON reaches him",
   bool(martin_co) and martin_co <= mine, f"{martin_co}")
ck("a co-listed lead martin is NOT on still does not",
   bool(others_co) and not (others_co & mine), f"{others_co}")

print(f"\n{sum(ok)}/{len(ok)} passed  (read-only against the live service)")
sys.exit(0 if all(ok) else 1)
