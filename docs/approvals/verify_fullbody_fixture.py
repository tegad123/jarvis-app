#!/usr/bin/env python3
"""The seed fixture carries the full body: boxed seed → approve → captured mail has the whole message;
then the same regenerated request on testco's real queue renders and executes through the API."""
import json, os, pathlib, shutil, subprocess, sys, uuid
BOX = pathlib.Path("/tmp/claude-0/-root/5856f8be-0b4c-4374-9aeb-3d70bf132d08/scratchpad/fullbody-box"); shutil.rmtree(BOX, ignore_errors=True); (BOX/"state").mkdir(parents=True)
SK="/opt/testco/skills/openclaw-imports"
os.environ.update({"HERMES_HOME":str(BOX),"JARVIS_SKILLS_ROOT":SK,"JARVIS_RUNTIME_CONFIG":f"{SK}/_shared/runtime.json","JARVIS_IDENTITY_CONFIG":f"{SK}/_shared/identity.json",
  "JARVIS_APPROVALS":str(BOX/"state/approvals.jsonl"),"JARVIS_APPROVAL_DECISIONS":str(BOX/"state/approvals_decisions.jsonl"),"JARVIS_ACTIVITY_FEED":str(BOX/"state/feed.jsonl"),"JARVIS_ALLOW_WRITES":"1"})
sys.path.insert(0,"/root/jarvis-app"); from app import approvals
ok=[]; ck=lambda n,c,x="": (ok.append(bool(c)), print(f"  {'PASS' if c else 'FAIL'}  {n}"+(f" — {x}" if x and not c else "")))
lines=lambda p: [json.loads(l) for l in pathlib.Path(p).read_text().splitlines() if l.strip()] if pathlib.Path(p).exists() else []
print("=== boxed: seed → approve ===")
approvals.seed("testco")
w=[a for a in approvals.load_all() if a["summary"].startswith("Send a follow-up to William")]
ck("seeded fixture present, pending", len(w)==1 and w[0]["status"]=="pending")
pl=w[0]["payload"]; ck("payload carries full body, no body_preview", "body" in pl and "body_preview" not in pl and pl["body"].endswith("Best,\nSpencer"), str(sorted(pl)))
out=approvals.decide(w[0]["id"],"approved","dana")
ck("approve → captured (boundary; wmchenry@example.com is inside .example.com)", out.get("send",{}).get("state")=="captured", json.dumps(out.get("send"))[:200])
cap=lines(BOX/"outbox/captured_mail.jsonl")
ck("captured mail body is the WHOLE message", cap and cap[-1]["body"]==pl["body"] and "second walk-" in cap[-1]["body"], (cap[-1]["body"][-60:] if cap else ""))
print("=== testco: regenerated request on the real queue, via the API ===")
HOME=pathlib.Path("/opt/testco/hermes"); rid=f"apr_regen_{uuid.uuid4().hex[:8]}"
req={"id":rid,"ts":approvals._iso_now(),"tenant":"testco","status":"pending","skill":"owner-email","summary":"Send a follow-up to William Mchenry about 609 25th St","payload":pl,"kind":"action"}
with open(HOME/"state/approvals.jsonl","a") as fh: fh.write(json.dumps(req, ensure_ascii=False)+"\n")
PINS=json.loads(pathlib.Path("/opt/testco/SEEDED_PINS.json").read_text()); jar=str(BOX/"dana.jar"); BASE="http://127.0.0.1:8710"
def call(path,method="GET",body=None):
    o=BOX/"b.json"; cmd=["curl","-s","-o",str(o),"-w","%{http_code}","-X",method,BASE+path,"-b",jar,"-c",jar]
    if body is not None: cmd+=["-H","Content-Type: application/json","-d",json.dumps(body)]
    code=subprocess.run(cmd,capture_output=True,text=True).stdout.strip()
    try: d=json.loads(o.read_text())
    except Exception: d={}
    return int(code or 0),d
call("/api/login","POST",{"person_key":"dana","pin":PINS["dana"]})
code,d=call("/api/approvals"); items=d.get("approvals",d.get("items",[])) if isinstance(d,dict) else []
it=next((a for a in items if a.get("id")==rid),None)
ck("GET /api/approvals returns it pending with the full body in payload", it and it["status"]=="pending" and it["payload"].get("body")==pl["body"], str(it)[:120] if it else str(d)[:120])
o0=len(lines(HOME/"outbox/captured_mail.jsonl"))
code,d=call(f"/api/approvals/{rid}","POST",{"decision":"approve"})
ck("owner approves → 200, send captured", code==200 and d.get("approval",{}).get("send",{}).get("state")=="captured", f"{code} {json.dumps(d)[:160]}")
cap=lines(HOME/"outbox/captured_mail.jsonl")[o0:]
ck("testco outbox row: full body, run_id = approval id", len(cap)==1 and cap[0]["body"]==pl["body"] and cap[0].get("run_id")==rid)
print(f"\n{sum(ok)}/{len(ok)} PASS"); sys.exit(0 if all(ok) else 1)
