#!/usr/bin/env python3
"""Sentinel inside the broker: ledger, taint, rate limits, shadow mode.

Two sandboxes behind the fake Google from docs/broker/verify_broker.py:
  E (8714): enforce, writes on, .example boundary
  S (8715): shadow, writes ALSO on, so only shadow mode can be what stops a send

    VERIFY_OPERATOR_PASSPHRASE=... python docs/sentinel/verify_sentinel_broker.py
"""
import json
import sys
import threading
from datetime import timedelta
from http.server import HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "broker"))
sys.path.insert(0, str(HERE.parents[2]))
import verify_broker as vb  # noqa: E402
from app import sentinel  # noqa: E402

results = []


def ck(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f" — {detail}"))


PROBE = r"""
import json, os, sys, base64
sys.path.insert(0, os.environ["SKILLS"] + "/_shared")
import google_creds as g
out = {}
def run(name, fn):
    try:
        out[name] = {"ok": fn()}
    except Exception as e:
        out[name] = {"raised": type(e).__name__, "outcome": getattr(e, "outcome", None),
                     "verdict": getattr(e, "verdict", None), "msg": str(e)[:300]}
def send(to):
    raw = base64.urlsafe_b64encode(("To: " + to + "\r\nCc: \r\nSubject: secret-subject\r\n\r\nsecret-body").encode()).decode()
    return g.gmail().users().messages().send(userId="me", body={"raw": raw}).execute()
for step in os.environ["STEPS"].split(";"):
    kind, _, arg = step.partition(":")
    if kind == "send":
        run(step, lambda: send(arg))
    elif kind == "read":
        run(step, lambda: g.gmail().users().messages().get(userId="me", id=arg).execute())
    elif kind == "profile":
        run(step, lambda: g.gmail().users().getProfile(userId="me").execute())
    elif kind == "insert":
        run(step, lambda: g.calendar().events().insert(calendarId="primary",
            body={"summary": "s", "start": {"date": "2026-10-14"}, "end": {"date": "2026-10-14"}}).execute())
print(json.dumps(out, default=str))
"""


def probe(port, key, steps, run_id=None, session=None):
    extra = {"STEPS": ";".join(steps), "JARVIS_SKILL": "verify-sentinel"}
    if run_id:
        extra["JARVIS_RUN_ID"] = run_id
    if session:
        extra["HERMES_SESSION_ID"] = session
    return vb.skill_probe(port, key, "", probe=PROBE, extra=extra)


def ledger(box):
    p = box / "sentinel_ledger.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def posts():
    return [x for x in vb.FakeGoogle.seen if x["method"] == "POST"]


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), vb.FakeGoogle)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{srv.server_address[1]}/"
    sandboxes = []
    try:
        box, env = vb.start_sandbox("e", 8714, "1", endpoint)
        sandboxes.append(env)
        key = vb.env_value(box / "hermes/.env", "API_SERVER_KEY")
        tdb = box / "threads.db"

        print("── E: a clean run's internal send executes and is ledgered")
        vb.FakeGoogle.seen.clear()
        out = probe(8714, key, ["send:dana@testco.example.com"], run_id="run-clean")
        ck("internal send executed (empty Cc header did not erase the To)",
           (out.get("send:dana@testco.example.com", {}).get("ok") or {}).get("id") == "sent1", str(out))
        ck("fake Google saw it", len(posts()) == 1)
        L = ledger(box)
        ex = [e for e in L if e.get("outcome") == "executed"]
        ck("ledger: one executed internal_send for run-clean, untainted",
           len(ex) == 1 and ex[0]["class"] == "internal_send" and ex[0]["run_id"] == "run-clean"
           and ex[0]["tainted"] is False and ex[0]["skill"] == "verify-sentinel", str(ex))
        ck("ledger: the decision was recorded before execution (allowed precedes executed)",
           [e.get("outcome") for e in L if e.get("run_id") == "run-clean"] == ["allowed", "executed"])
        text = (box / "sentinel_ledger.jsonl").read_text()
        ck("ledger carries no subject or body", "secret-subject" not in text and "secret-body" not in text)
        ck("canary: the probe really sent that subject (it reached the fake)",
           any("raw" in x["body"] for x in posts()))

        print("── E: an external send is held, ledgered, not sent")
        vb.FakeGoogle.seen.clear()
        a0 = vb.counter(tdb, "broker_sentinel_ask")
        out = probe(8714, key, ["send:buyer@client.example"], run_id="run-clean")
        r = out.get("send:buyer@client.example", {})
        ck("ActionHeld, outcome approval_required, verdict ask",
           (r.get("raised"), r.get("outcome"), r.get("verdict")) == ("ActionHeld", "approval_required", "ask"), str(r))
        ck("…counted (broker_sentinel_ask +1)", vb.counter(tdb, "broker_sentinel_ask") - a0 == 1)
        ck("…fake Google saw no send", not posts())
        ck("…ledgered as external_send / approval_required",
           any(e.get("outcome") == "approval_required" and e.get("class") == "external_send"
               and e.get("destinations") == ["buyer@client.example"] for e in ledger(box)))

        print("── E: taint follows the run")
        t0 = vb.counter(tdb, "broker_sentinel_run_tainted")
        out = probe(8714, key, ["read:m1", "insert:x"], run_id="run-internal-read")
        ck("reading mail from the org does not taint", vb.counter(tdb, "broker_sentinel_run_tainted") - t0 == 0,
           str(out))
        ck("…and its write is ledgered untainted", any(
            e.get("run_id") == "run-internal-read" and e.get("outcome") == "executed" and e["tainted"] is False
            for e in ledger(box)))
        out = probe(8714, key, ["read:mext", "insert:x"], run_id="run-taint")
        ck("reading mail from outside the org taints the run (+1)",
           vb.counter(tdb, "broker_sentinel_run_tainted") - t0 == 1, str(out))
        ck("…a taint event names the run", any(e.get("event") == "taint" and e.get("run_id") == "run-taint"
                                              for e in ledger(box)))
        ck("…the run's next write is decided as tainted", any(
            e.get("run_id") == "run-taint" and e.get("outcome") == "executed" and e["tainted"] is True
            for e in ledger(box)))
        probe(8714, key, ["insert:x"], run_id="run-fresh")
        ck("a new run starts clean", any(e.get("run_id") == "run-fresh" and e["tainted"] is False
                                         for e in ledger(box) if e.get("outcome") == "executed"))
        probe(8714, key, ["insert:x"], session="sess-77")
        ck("the Hermes session id is the run when JARVIS_RUN_ID is absent",
           any(e.get("run_id") == "sess-77" for e in ledger(box)))
        probe(8714, key, ["insert:x"])
        ck("no run identity at all → decided as tainted",
           any(e.get("run_id") == "" and e["tainted"] is True for e in ledger(box) if e.get("outcome") == "executed"))

        print("── E: rate limit from the ledger (3 per recipient per day)")
        now = sentinel.utcnow()
        with open(box / "sentinel_ledger.jsonl", "a") as fh:
            for i in range(2):
                fh.write(json.dumps({"ts": (now - timedelta(minutes=5 + i)).isoformat(), "outcome": "executed",
                                     "class": "internal_send", "destinations": ["omar@testco.example.com"]}) + "\n")
            fh.write(json.dumps({"ts": (now - timedelta(days=2)).isoformat(), "outcome": "executed",
                                 "class": "internal_send", "destinations": ["omar@testco.example.com"]}) + "\n")
        vb.FakeGoogle.seen.clear()
        out = probe(8714, key, ["send:omar@testco.example.com", "send:omar@testco.example.com"], run_id="run-rate")
        ck("third send today goes (two today, one two days ago does not count)", len(posts()) == 1, str(out))
        r = [v for k, v in out.items()]
        ck("a fourth is denied by the rate limit",
           any(x.get("outcome") == "sentinel_denied" and "rate limit" in x.get("msg", "") for x in r), str(r))

        print("── S: shadow mode (writes on, so only shadow can stop it)")
        box_s, env_s = vb.start_sandbox("s", 8715, "1", endpoint, sentinel_mode="shadow")
        sandboxes.append(env_s)
        key_s = vb.env_value(box_s / "hermes/.env", "API_SERVER_KEY")
        vb.FakeGoogle.seen.clear()
        s0 = vb.counter(box_s / "threads.db", "broker_sentinel_shadowed")
        out = probe(8715, key_s, ["profile:", "send:dana@testco.example.com", "send:buyer@client.example",
                                  "insert:x"], run_id="run-shadow")
        ck("reads still work in shadow", (out.get("profile:", {}).get("ok") or {}).get("emailAddress")
           == "assistant@fixture.example", str(out.get("profile:")))
        held = [out.get(k, {}) for k in ("send:dana@testco.example.com", "send:buyer@client.example", "insert:x")]
        ck("every write came back ActionHeld / shadowed",
           all((h.get("raised"), h.get("outcome")) == ("ActionHeld", "shadowed") for h in held), str(held))
        ck("…with what Sentinel would have done (allow, ask, allow)",
           [h.get("verdict") for h in held] == ["allow", "ask", "allow"], str([h.get("verdict") for h in held]))
        ck("fake Google saw no POST at all", not posts(), str(posts()))
        ck("…counted (broker_sentinel_shadowed +3)", vb.counter(box_s / "threads.db", "broker_sentinel_shadowed") - s0 == 3)
        sh = [e for e in ledger(box_s) if e.get("outcome") == "shadowed"]
        ck("ledger: three shadowed entries with class and destinations",
           [(e["class"], e["destinations"]) for e in sh] ==
           [("internal_send", ["dana@testco.example.com"]), ("external_send", ["buyer@client.example"]),
            ("internal_write", [])], str(sh))
        ck("ledger: nothing executed in shadow", not [e for e in ledger(box_s) if e.get("outcome") == "executed"])
    finally:
        for env in sandboxes:
            vb.stop_sandbox(env)
        srv.shutdown()
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
