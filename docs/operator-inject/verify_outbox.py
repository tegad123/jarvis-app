#!/usr/bin/env python3
"""
GET /api/operator/outbox — regression suite against testco (:8710).
  roles      owner / admin / member → 403, no session → 401, operator → 200
  limit      default 20; 1 and 200 accepted; 0, 201, -1, 'x', 1.5 → 400
  fidelity   rows equal the last N lines of outbox/captured_mail.jsonl projected to
             (ts, to, cc, subject, body), in file order; count == len(rows)
"""
import argparse, json, pathlib, subprocess, sys
ap = argparse.ArgumentParser(); ap.add_argument("--base", default="http://127.0.0.1:8710"); ap.add_argument("-v", "--verbose", action="store_true")
args = ap.parse_args(); BASE = args.base.rstrip("/")
if BASE.endswith(":8700"): sys.exit("REFUSING: :8700 is the live tenant.")
HOME = pathlib.Path("/opt/testco/hermes"); OUTBOX = HOME / "outbox" / "captured_mail.jsonl"
SCR = pathlib.Path("/tmp/claude-0/-root/5856f8be-0b4c-4374-9aeb-3d70bf132d08/scratchpad/outbox"); SCR.mkdir(parents=True, exist_ok=True)
PINS = json.loads(pathlib.Path("/opt/testco/SEEDED_PINS.json").read_text())
ok = []
def ck(name, cond, extra=""):
    ok.append(bool(cond)); print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if (extra and (not cond or args.verbose)) else ""))
def passphrase():
    for l in (HOME / ".env").read_text().splitlines():
        if l.strip().startswith("APP_PASSPHRASE="): return l.split("=", 1)[1].strip().strip('"').strip("'")
def call(path, jar=None, method="GET", body=None):
    out = SCR / "b.json"; cmd = ["curl", "-s", "-o", str(out), "-w", "%{http_code}", "-X", method, BASE + path]
    if jar: cmd += ["-b", jar, "-c", jar]
    if body is not None: cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    code = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    try: data = json.loads(out.read_text())
    except Exception: data = {}
    return int(code or 0), data
def login(who):
    jar = str(SCR / f"{who}.jar"); pathlib.Path(jar).unlink(missing_ok=True)
    body = {"passphrase": passphrase()} if who == "operator" else {"person_key": who, "pin": PINS[who]}
    code, d = call("/api/login", jar, "POST", body)
    if not d.get("ok"): sys.exit(f"login {who} failed {code} {d}")
    return jar

print("\n=== roles ===")
for who, role in (("dana", "owner"), ("priya", "admin"), ("tester", "member")):
    code, d = call("/api/operator/outbox", login(who)); ck(f"{role} ({who}) → 403", code == 403, f"{code} {d.get('detail','')[:60]}")
code, d = call("/api/operator/outbox"); ck("no session → 401", code == 401, str(code))
op = login("operator")
code, d = call("/api/operator/outbox", op); ck("operator → 200", code == 200, str(code))

print("\n=== limit bounds ===")
ck("default limit is 20", d.get("limit") == 20 and len(d.get("rows", [])) <= 20, str(d.get("limit")))
for lim, want in ((1, 200), (200, 200), (0, 400), (201, 400), (-1, 400), ("x", 400), ("1.5", 400), ("", 400)):
    code, dd = call(f"/api/operator/outbox?limit={lim}", op); ck(f"limit={lim!r} → {want}", code == want, f"{code} {dd.get('detail','')[:60]}")
code, dd = call("/api/operator/outbox?limit=1", op); ck("limit=1 returns exactly one row", len(dd.get("rows", [])) == 1 and dd.get("count") == 1)

print("\n=== fidelity ===")
file_rows = [json.loads(l) for l in OUTBOX.read_text(encoding="utf-8").splitlines() if l.strip()]
keep = ("ts", "to", "cc", "subject", "body")
for lim in (1, 5, 200):
    code, dd = call(f"/api/operator/outbox?limit={lim}", op)
    expect = [{k: r.get(k) for k in keep} for r in file_rows[-lim:]]
    ck(f"limit={lim}: rows == last {min(lim, len(file_rows))} file lines, projected, in file order", dd.get("rows") == expect and dd.get("count") == len(expect), f"got {len(dd.get('rows', []))} vs {len(expect)}" if dd.get("rows") != expect else "")
ck("rows carry only ts/to/cc/subject/body", all(set(r) == set(keep) for r in dd.get("rows", [])))
ck("newest file line is the last row", dd.get("rows") and dd["rows"][-1]["ts"] == file_rows[-1]["ts"])
ck("boundary reported active", dd.get("active") is True)

n = sum(ok); print(f"\n{n}/{len(ok)} PASS"); sys.exit(0 if n == len(ok) else 1)
