#!/usr/bin/env python3
"""W3 half 2 — membership scoping, verified by TRIGGERING.

Everything here goes over HTTP against the running service. No store calls,
because the thing under test is the boundary, not the query.
"""
import json, os, pathlib, subprocess, sys

# THE SANDBOX IS THE DEFAULT, not an option. This suite probes negative paths
# ("a member must NOT be able to rename this channel"), and under the mutation
# test — where the guard is deliberately gone — every one of those probes
# becomes a real write. Run it at the live service and it renames live
# channels; that is not hypothetical, it happened. See w3_sandbox.sh.
BASE = os.environ.get("W3_BASE", "http://127.0.0.1:8701")
if BASE.endswith(":8700"):
    sys.exit("REFUSING to run against the live app on :8700 — start w3_sandbox.sh")
PINS = json.loads(pathlib.Path(
    "/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad/pins.json").read_text())
PASSPHRASE = subprocess.run(
    ["bash","-lc","grep -m1 '^APP_PASSPHRASE=' /root/.hermes/.env | cut -d= -f2-"],
    capture_output=True, text=True).stdout.strip()

ok, fails = [], []
def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))
    if not cond: fails.append(name)

def curl(method, path, jar, data=None, code_only=False):
    cmd = ["curl","-s","-b",jar,"-c",jar,"-X",method,f"{BASE}{path}"]
    if data is not None:
        cmd += ["-H","Content-Type: application/json","-d",json.dumps(data)]
    if code_only:
        cmd = ["curl","-s","-o","/dev/null","-w","%{http_code}","-b",jar,"-c",jar,
               "-X",method,f"{BASE}{path}"]
        if data is not None:
            cmd += ["-H","Content-Type: application/json","-d",json.dumps(data)]
        return subprocess.run(cmd,capture_output=True,text=True).stdout.strip()
    out = subprocess.run(cmd,capture_output=True,text=True).stdout
    try: return json.loads(out)
    except Exception: return {"_raw": out[:200]}

def login(who):
    jar = f"/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad/w3-sandbox/jar_{who}.txt"
    pathlib.Path(jar).unlink(missing_ok=True)
    body = ({"passphrase": PASSPHRASE} if who == "operator"
            else {"person_key": who, "pin": PINS[who]})
    r = curl("POST","/api/login",jar,body)
    assert r.get("ok"), f"login {who} failed: {r}"
    return jar

print("\n── sessions ───────────────────────────────────────────────")
J = {w: login(w) for w in ("spencer","tega","martin","operator")}
for w in J:
    s = curl("GET","/api/session",J[w])
    print(f"  {w:9} person_key={s.get('person_key')!s:9} role={s.get('role')}")

print("\n── 1. threads: the WHERE clause ───────────────────────────")
T = {w: curl("GET","/api/threads",J[w]) for w in J}
names = {w: sorted(t["name"] for t in T[w]["threads"]) for w in J}
ck("owner sees the 4 channels + ONE direct line (their own)",
   T["spencer"]["count"] == 5 and
   sum(1 for t in T["spencer"]["threads"] if t["is_direct"]) == 1, str(names["spencer"]))
ck("admin likewise: every channel, only their own direct line",
   T["tega"]["count"] == 5 and
   sum(1 for t in T["tega"]["threads"] if t["is_direct"]) == 1, str(names["tega"]))
ck("A DIRECT LINE IS PRIVATE FROM THE OWNER TOO",
   not any(t["direct_for"] not in (None, "spencer") for t in T["spencer"]["threads"]),
   str([t["direct_for"] for t in T["spencer"]["threads"]]))
ck("operator sees 4 channels and NO direct line (it has no person)",
   T["operator"]["count"] == 4 and
   not any(t["is_direct"] for t in T["operator"]["threads"]), str(names["operator"]))
ck("member sees ONLY their own direct line",
   names["martin"] == ["Ask Jarvis"], str(names["martin"]))
ck("member's direct line is not the owner's",
   [t for t in T["martin"]["threads"]][0]["thread_id"] != "direct",
   [t["thread_id"] for t in T["martin"]["threads"]][0])
ck("every session's pinned line is its OWN",
   all(any(t["is_direct"] and t["direct_for"] in (w, None)
           for t in T[w]["threads"]) for w in ("spencer","martin")))

ch = next(t for t in T["spencer"]["threads"] if not t["is_direct"])
print(f"     test channel: {ch['name']} ({ch['thread_id']})")

print("\n── 2. a member cannot reach a channel they are not in ──────")
ck("GET history -> 404 (not 403: 403 would confirm it exists)",
   curl("GET",f"/api/chat/history?thread_id={ch['thread_id']}",J["martin"],code_only=True) == "404")
ck("POST chat -> 404", curl("POST","/api/chat",J["martin"],
   {"thread_id": ch["thread_id"], "message": "hello"}, code_only=True) == "404")
ck("GET members -> 404",
   curl("GET",f"/api/threads/{ch['thread_id']}/members",J["martin"],code_only=True) == "404")
ck("rename -> 404", curl("PATCH",f"/api/threads/{ch['thread_id']}",J["martin"],
   {"name":"hijacked"}, code_only=True) == "404")
ck("archive -> 404", curl("POST",f"/api/threads/{ch['thread_id']}/archive",J["martin"],
   code_only=True) == "404")
ck("attachments list -> 404",
   curl("GET",f"/api/threads/{ch['thread_id']}/attachments",J["martin"],code_only=True) == "404")

print("\n── 3. operator: STRUCTURE, NOT BODIES ─────────────────────")
ck("lists the channel", any(t["thread_id"] == ch["thread_id"]
                            for t in T["operator"]["threads"]))
mem = curl("GET",f"/api/threads/{ch['thread_id']}/members",J["operator"])
ck("reads its membership", "members" in mem and mem["members"],
   str([m["person_key"] for m in mem.get("members",[])]))
ck("403 on its messages",
   curl("GET",f"/api/chat/history?thread_id={ch['thread_id']}",J["operator"],code_only=True) == "403")
ck("403 on POSTing into it", curl("POST","/api/chat",J["operator"],
   {"thread_id": ch["thread_id"], "message": "hi"}, code_only=True) == "403")
ck("403 on its attachments",
   curl("GET",f"/api/threads/{ch['thread_id']}/attachments",J["operator"],code_only=True) == "403")
ck("no preview text leaks in the list",
   all(t["preview"] is None for t in T["operator"]["threads"]),
   str([t["preview"] for t in T["operator"]["threads"] if t["preview"]])[:80])
ck("owner's list DOES carry previews",
   any(t["preview"] for t in T["spencer"]["threads"]))
ck("the owner cannot OPEN another person's direct line either",
   curl("GET","/api/chat/history?thread_id=" +
        next(t["thread_id"] for t in curl("GET","/api/threads",J["martin"])["threads"]
             if t["is_direct"]), J["spencer"], code_only=True) == "404")
ck("bodies_visible flag is honest",
   T["operator"]["bodies_visible"] is False and T["spencer"]["bodies_visible"] is True)
ck("operator cannot grant membership", curl("POST",
   f"/api/threads/{ch['thread_id']}/members",J["operator"],
   {"person_key":"martin"}, code_only=True) == "403")

print("\n── 4. adding a member (admin/owner only) ──────────────────")
ck("member cannot add anyone", curl("POST",f"/api/threads/{ch['thread_id']}/members",
   J["martin"], {"person_key":"martin"}, code_only=True) in ("403","404"))
add = curl("POST",f"/api/threads/{ch['thread_id']}/members",J["tega"],{"person_key":"martin"})
ck("admin can add", add.get("ok") is True, str(add)[:120])
ck("unknown person refused", curl("POST",f"/api/threads/{ch['thread_id']}/members",
   J["tega"], {"person_key":"nobody-here"}, code_only=True) == "422")
after = curl("GET","/api/threads",J["martin"])
ck("the channel now appears for the member",
   any(t["thread_id"] == ch["thread_id"] for t in after["threads"]),
   str(sorted(t["name"] for t in after["threads"])))
hist = curl("GET",f"/api/chat/history?thread_id={ch['thread_id']}",J["martin"])
ck("FULL history on join, not just messages since",
   hist.get("count",0) == len(curl("GET",f"/api/chat/history?thread_id={ch['thread_id']}",
                                    J["spencer"]).get("messages",[])),
   f"member={hist.get('count')}")
ck("direct lines refuse members", curl("POST","/api/threads/direct/members",
   J["spencer"], {"person_key":"martin"}, code_only=True) == "409")

print("\n── 5. removal revokes reach ───────────────────────────────")
curl("DELETE",f"/api/threads/{ch['thread_id']}/members/martin",J["tega"])
ck("channel disappears again",
   not any(t["thread_id"] == ch["thread_id"]
           for t in curl("GET","/api/threads",J["martin"])["threads"]))
ck("and its history 404s again",
   curl("GET",f"/api/chat/history?thread_id={ch['thread_id']}",J["martin"],code_only=True) == "404")
ck("but the messages still exist for the owner",
   curl("GET",f"/api/chat/history?thread_id={ch['thread_id']}",J["spencer"]).get("count",0) > 0)

print("\n── 6. feed scoping ────────────────────────────────────────")
F = {w: curl("GET","/api/feed?limit=1000",J[w]) for w in J}
# Derived from the sandbox's own feed file, not hardcoded: the seeder adds
# events, and a literal here would go stale the moment the seed list changes.
ALL_EVENTS = sum(1 for l in open(
    "/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad/w3-sandbox/feed.jsonl")
    if l.strip())
ck("owner sees the whole feed", F["spencer"]["total"] == ALL_EVENTS,
   f'{F["spencer"]["total"]} of {ALL_EVENTS}')
ck("admin sees the whole feed", F["tega"]["total"] == ALL_EVENTS)
ck("operator sees the whole feed (events are not message bodies)",
   F["operator"]["total"] == ALL_EVENTS)
ck("a member sees their own leads' events",
   any(e.get("skill") == "leads" for e in F["martin"]["events"]),
   f'member total={F["martin"]["total"]}')
ck("member does NOT see the owner's mail",
   not any(e["destination"] == "emails" for e in F["martin"]["events"]),
   f"member total={F['martin']['total']}")
ck("member sees strictly less than the owner",
   F["martin"]["total"] < F["spencer"]["total"],
   f'{F["martin"]["total"]} < {F["spencer"]["total"]}')
S = {w: curl("GET","/api/feed/summary?tz=America/Chicago",J[w]) for w in J}
ck("the hero count agrees with the member's own list",
   S["martin"]["total"] == sum(1 for e in F["martin"]["events"]
                               if e["ts"][:10] == S["martin"]["date"]),
   f"summary={S['martin']['total']}")
ck("owner's hero is unchanged by scoping",
   S["spencer"]["total"] == sum(1 for e in F["spencer"]["events"]
                                if e["ts"][:10] == S["spencer"]["date"]))

print("\n── 7. leads scoping (app-native store; see sandbox notes) ─")
L = {w: curl("GET","/api/leads",J[w]) for w in J}
ck("the sandbox is exercising the STORE branch, not the sheet",
   L["spencer"].get("source") == "store", str(L["spencer"].get("source")))
ck("owner sees every lead", L["spencer"]["count"] == 6, str(L["spencer"]["count"]))
ck("admin sees every lead", L["tega"]["count"] == 6)
ck("member sees only their own two",
   L["martin"]["count"] == 2, str([l["name"] for l in L["martin"]["leads"]]))
ck("and every one of them is theirs",
   {l.get("agent") for l in L["martin"]["leads"]} == {"martin"},
   str({l.get("agent") for l in L["martin"]["leads"]}))
ck("an UNASSIGNED lead belongs to no member",
   not any(l["name"] == "Unassigned Walk-in" for l in L["martin"]["leads"]) and
   any(l["name"] == "Unassigned Walk-in" for l in L["spencer"]["leads"]))
ck("the count chips agree with the rows",
   L["martin"]["counts"]["all"] == L["martin"]["count"] == 2)

print("\n── 8. approvals stay owner-only ───────────────────────────")
for w, want in (("spencer","200"),("tega","403"),("martin","403"),("operator","403")):
    ck(f"{w} -> {want}", curl("GET","/api/approvals",J[w],code_only=True) == want)

print("\n── 9. the live box is untouched ───────────────────────────")
live = sum(1 for l in open("/root/.hermes/state/activity_feed.jsonl") if l.strip())
ck("live feed still 82 events", live == 82, str(live))
ck("this suite never spoke to :8700", not BASE.endswith(":8700"))

print(f"\n{sum(ok)}/{len(ok)} passed")
if fails:
    print("FAILED: " + "; ".join(fails))
sys.exit(0 if all(ok) else 1)
