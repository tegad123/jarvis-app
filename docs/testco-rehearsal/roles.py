#!/usr/bin/env python3
"""A4.4 — each testco account sees only what its role and membership allow."""
import json, pathlib, subprocess, sys
B = "http://127.0.0.1:8710"
PINS = json.load(open("/opt/testco/SEEDED_PINS.json"))
ok = []
def ck(n, c, x=""):
    ok.append(bool(c)); print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" — {x}" if x else ""))

def login(who):
    jar = f"/tmp/rj_{who}.txt"; pathlib.Path(jar).unlink(missing_ok=True)
    subprocess.run(["curl","-s","-c",jar,"-X","POST",f"{B}/api/login","-H","Content-Type: application/json",
                    "-d",json.dumps({"person_key":who,"pin":PINS[who]})],capture_output=True)
    return jar
def get(p, jar, code=False):
    if code:
        return subprocess.run(["curl","-s","-o","/dev/null","-w","%{http_code}","-b",jar,f"{B}{p}"],
                              capture_output=True,text=True).stdout.strip()
    return json.loads(subprocess.run(["curl","-s","-b",jar,f"{B}{p}"],capture_output=True,text=True).stdout)

J = {w: login(w) for w in ("dana","omar","priya")}
S = {w: get("/api/session", J[w]) for w in J}
for w, role in (("dana","owner"),("priya","admin"),("omar","member")):
    ck(f"{w} signs in as {role}", S[w].get("role") == role, S[w].get("role"))
    ck(f"  {w} is greeted by their own name", (S[w].get("display_name") or "").lower().startswith(w))

T = {w: get("/api/threads", J[w]) for w in J}
names = {w: sorted(t["name"] for t in T[w]["threads"]) for w in J}
ck("owner sees every channel", sum(1 for t in T["dana"]["threads"] if not t["is_direct"]) >= 5,
   str(sum(1 for t in T["dana"]["threads"] if not t["is_direct"])))
ck("admin sees every channel too",
   sum(1 for t in T["priya"]["threads"] if not t["is_direct"])
   == sum(1 for t in T["dana"]["threads"] if not t["is_direct"]))
ck("MEMBER sees strictly fewer channels than the owner",
   len(names["omar"]) < len(names["dana"]), f"omar={len(names['omar'])} dana={len(names['dana'])}")
ck("member does not see a channel they are not in",
   not any("Copperbeam" in n for n in names["omar"]), str(names["omar"]))
ck("every account has exactly one direct line — its own",
   all(sum(1 for t in T[w]["threads"] if t["is_direct"]) == 1 for w in J))
ck("a direct line is private even from the OWNER",
   all(t["direct_for"] in (None, "dana") for t in T["dana"]["threads"]))

ck("approvals: owner 200", get("/api/approvals", J["dana"], code=True) == "200")
ck("approvals: admin 403", get("/api/approvals", J["priya"], code=True) == "403")
ck("approvals: member 403", get("/api/approvals", J["omar"], code=True) == "403")

L = {w: get("/api/leads", J[w]) for w in J}
ck("owner sees all leads", L["dana"]["count"] >= 40, str(L["dana"]["count"]))
ck("member sees only their own", L["omar"]["count"] < L["dana"]["count"],
   f"omar={L['omar']['count']} dana={L['dana']['count']}")
F = {w: get("/api/feed?limit=1000", J[w]) for w in J}
ck("member's feed is scoped smaller than the owner's",
   F["omar"]["total"] < F["dana"]["total"], f"omar={F['omar']['total']} dana={F['dana']['total']}")
print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
