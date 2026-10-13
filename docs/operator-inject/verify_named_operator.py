#!/usr/bin/env python3
import sys; sys.exit("RETIRED 2026-10-13: testco is retired (docs/platform/decisions.md); this script needs its running services or tree.")  # noqa: E702
"""Named operator credential (bao): logs in as role operator with attribution, works on the three
operator surfaces, journals as operator:bao, and is REJECTED on the live tenant (:8700)."""
import json, pathlib, subprocess, sys, re
SCR=pathlib.Path("/tmp/claude-0/-root/5856f8be-0b4c-4374-9aeb-3d70bf132d08/scratchpad/named-op"); SCR.mkdir(parents=True, exist_ok=True)
env=pathlib.Path("/opt/testco/hermes/.env").read_text()
BAO=dict(p.split("=",1) for p in re.search(r"^APP_OPERATOR_PASSPHRASES=(.*)$", env, re.M).group(1).split(";"))["bao"].strip()
TEGA=re.search(r"^APP_PASSPHRASE=(.*)$", env, re.M).group(1).strip().strip('"\'')
ok=[]; ck=lambda n,c,x="": (ok.append(bool(c)), print(f"  {'PASS' if c else 'FAIL'}  {n}"+(f" — {x}" if x and not c else "")))
def call(base, path, jar, method="GET", body=None):
    o=SCR/"b.json"; cmd=["curl","-s","-o",str(o),"-w","%{http_code}","-X",method,base+path,"-b",jar,"-c",jar]
    if body is not None: cmd+=["-H","Content-Type: application/json","-d",json.dumps(body)]
    code=subprocess.run(cmd,capture_output=True,text=True).stdout.strip()
    try: d=json.loads(o.read_text())
    except Exception: d={}
    return int(code or 0), d
T="http://127.0.0.1:8710"; L="http://127.0.0.1:8700"
J=pathlib.Path("/opt/testco/hermes/state/operator_inject.jsonl"); j0=len(J.read_text().splitlines())
print("=== testco ===")
jar=str(SCR/"bao.jar"); pathlib.Path(jar).unlink(missing_ok=True)
code,d=call(T,"/api/login",jar,"POST",{"passphrase":BAO}); ck("bao passphrase logs in on testco → role operator, operator=bao", code==200 and d.get("role")=="operator" and d.get("operator")=="bao", f"{code} {d}")
code,d=call(T,"/api/session",jar); ck("/api/session carries operator=bao, no person", code==200 and d.get("operator")=="bao" and d.get("person_key") is None, str(d))
code,d=call(T,"/api/health/summary",jar); ck("/api/health/summary → 200", code==200 and "counters" in json.dumps(d), str(code))
code,d=call(T,"/api/operator/outbox?limit=3",jar); ck("/api/operator/outbox → 200 with rows", code==200 and isinstance(d.get("rows"),list), str(code))
code,d=call(T,"/api/operator/inject",jar,"POST",{"kind":"reply","fixture":"digest_reply_nonsense.eml"}); ck("/api/operator/inject → 200, by=operator:bao", code==200 and d.get("by")=="operator:bao", f"{code} {json.dumps(d)[:160]}")
rows=[json.loads(l) for l in J.read_text().splitlines()[j0:]]; ck("journal row attributes the run to operator:bao", any(r.get("by")=="operator:bao" for r in rows), str([r.get("by") for r in rows]))
feed=[json.loads(l) for l in open("/opt/testco/hermes/state/activity_feed.jsonl") if l.strip()][-5:]
ck("feed line detail says 'by operator:bao'", any("by operator:bao" in (e.get("detail") or "") for e in feed), str([e.get("detail","")[:60] for e in feed]))
jar2=str(SCR/"tega.jar"); pathlib.Path(jar2).unlink(missing_ok=True)
code,d=call(T,"/api/login",jar2,"POST",{"passphrase":TEGA}); ck("primary passphrase still works, now labelled tega", code==200 and d.get("operator")=="tega", str(d))
code,d=call(T,"/api/operator/inject",jar2,"POST",{"kind":"reply","fixture":"digest_reply_nonsense.eml"}); ck("primary journals as operator:tega — distinguishable", code==200 and d.get("by")=="operator:tega", str(d.get("by")))
jar3=str(SCR/"bad.jar"); code,d=call(T,"/api/login",jar3,"POST",{"passphrase":BAO[:-1]+"x"}); ck("near-miss passphrase → 401", code==401)
print("=== live (:8700) ===")
jar4=str(SCR/"live.jar"); pathlib.Path(jar4).unlink(missing_ok=True)
code,d=call(L,"/api/login",jar4,"POST",{"passphrase":BAO}); ck("bao passphrase REJECTED on live → 401", code==401, f"{code} {d}")
code,d=call(L,"/api/session",jar4); ck("no live session was issued", code==401, str(code))
print(f"\n{sum(ok)}/{len(ok)} PASS"); sys.exit(0 if all(ok) else 1)
