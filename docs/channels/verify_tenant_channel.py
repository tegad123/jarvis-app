#!/usr/bin/env python3
"""Team channels on a provisioned tenant, with the real gateway and model.

Run as root by the lead agent, on isocheck only (it writes a channel and its
messages into the tenant; real tenants keep only real conversations). Nothing
is stubbed. Asserted on the ledger, and on one fact the model can only know by
reading the channel:

  - a message without @jarvis is saved with its author, gets no reply, and the
    model usage ledger does not move (Jarvis was not called);
  - a message with @jarvis gets one reply, and the usage ledger grows;
  - the reply carries the code posted earlier in the channel, which only the
    channel's context could have given it;
  - nothing left Jarvis (no executed audit rows).

    python3 docs/channels/verify_tenant_channel.py --pin-file /root/onboarding/isocheck-owner-pin.txt
"""
import argparse
import json
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model"))
from verify_tenant_chat import executed, pin_from, runtime, usage  # noqa: E402

PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = PASS + bool(ok), FAIL + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


def post(c, tid, text):
    events = []
    with c.stream("POST", "/api/chat", json={"thread_id": tid, "message": text}) as resp:
        code = resp.status_code
        for line in resp.iter_lines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
    return code, events


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="isocheck")
    ap.add_argument("--pin-file", required=True, type=Path)
    ap.add_argument("--person", default="owner")
    ap.add_argument("--timeout", type=float, default=240)
    a = ap.parse_args()
    if a.slug != "isocheck":
        sys.exit("REFUSING: this writes a test channel into the tenant; isocheck only")
    rt = runtime(a.slug)
    # The app's port, as the tenant chat test finds it: where the agent's model proxy points.
    port = int(re.search(r"127\.0\.0\.1:(\d+)/api/model",
                         Path(f"/opt/{a.slug}/hermes/config.yaml").read_text()).group(1))
    if port in (8700, 8710):
        sys.exit("REFUSING: live or testco port")
    base = f"http://127.0.0.1:{port}"
    model, dbname = (rt.get("models") or {}).get("agent") or "", f"jarvis_{a.slug}"
    threads_db = Path(f"/opt/{a.slug}/app-private/jarvis_threads.db")
    print(f"tenant {a.slug}: app {base}, agent model {model}, ledger {dbname}")

    c = httpx.Client(base_url=base, timeout=a.timeout)
    r = c.post("/api/login", json={"person_key": a.person, "pin": pin_from(a.pin_file)})
    check(f"{a.person} signs in (HTTP {r.status_code})", r.status_code == 200)
    c.headers["cookie"] = "; ".join(f"{k}={v}" for k, v in r.cookies.items())
    c.cookies.clear()
    r = c.post("/api/threads", json={"name": f"verify-channel-{random.randint(1000, 9999)}"})
    tid = ((r.json() if r.status_code == 201 else {}).get("thread") or {}).get("thread_id")
    check(f"a fresh channel (HTTP {r.status_code})", bool(tid))
    if not tid:
        print(f"\n{PASS}/{PASS + FAIL} passed")
        return 1

    def rows():
        con = sqlite3.connect(f"file:{threads_db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            return [dict(x) for x in con.execute("SELECT id, role, author, content FROM messages WHERE thread_id = ? "
                                                 "ORDER BY id", (tid,))]
        finally:
            con.close()

    code_word = f"{random.randint(1000, 9999)}"
    u0, x0 = usage(dbname, model), executed(dbname)
    code, ev = post(c, tid, f"Lockbox code for the showing at 12 Oak is {code_word}.")
    time.sleep(2)
    u1 = usage(dbname, model)
    check(f"a message without @jarvis is posted, Jarvis not called (events {[e['type'] for e in ev]})",
          code == 200 and [e["type"] for e in ev] == ["posted"] and ev[0]["jarvis"] is False)
    check(f"   saved once, written by {a.person}, no reply", [(x["role"], x["author"]) for x in rows()] == [("user", a.person)])
    check(f"   and the model was not used: requests +{u1['requests'] - u0['requests']:.0f}", u1["requests"] == u0["requests"])

    code, ev = post(c, tid, "@jarvis what is the lockbox code someone posted above? Reply with just the number.")
    u2, x2 = usage(dbname, model), executed(dbname)
    reply = "".join(e.get("text", "") for e in ev if e["type"] == "delta")
    types = [e["type"] for e in ev]
    check(f"@jarvis: posted (jarvis true), a reply, then saved ({types})",
          code == 200 and types[0] == "posted" and ev[0]["jarvis"] is True and "delta" in types and types[-1] == "saved"
          and "error" not in types)
    check(f"   the reply knows the code from the channel ({reply.strip()[:80]!r})", code_word in reply)
    got = rows()
    check(f"   ledger: two people's messages and one reply ({[(x['role'], x['author']) for x in got]})",
          [(x["role"], x["author"]) for x in got][:3] == [("user", a.person), ("user", a.person), ("assistant", None)])
    check(f"   the model was used: requests +{u2['requests'] - u1['requests']:.0f}, cost +${u2['cost_usd'] - u1['cost_usd']:.6f}",
          u2["requests"] > u1["requests"] and u2["cost_usd"] > u1["cost_usd"])
    check(f"nothing left Jarvis: executed audit rows {x0} -> {x2}", x2 == x0)
    r = c.post(f"/api/threads/{tid}/archive")
    check(f"the test channel is archived afterwards (HTTP {r.status_code})", r.status_code == 200)
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
