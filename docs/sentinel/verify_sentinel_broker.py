#!/usr/bin/env python3
"""Sentinel inside the broker, against the platform database: the audit log,
approvals by signed callback, grants, taint, rate limits, the source-record
rule, outbound proposals, and shadow mode's "would have done X".

Two sandboxes behind the fake Google from docs/broker/verify_broker.py:
  E (8714): enforce, writes on, .example boundary
  S (8715): shadow, writes ALSO on, so only shadow mode can be what stops a send

    VERIFY_OPERATOR_PASSPHRASE=... /opt/jarvis/venv/bin/python docs/sentinel/verify_sentinel_broker.py
"""
import http.cookiejar
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import HTTPServer
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "broker"))
sys.path.insert(0, str(HERE.parents[2]))
import verify_broker as vb  # noqa: E402
from app import sentinel  # noqa: E402

PINS = json.loads((Path(os.environ.get("VERIFY_FIXTURE_ROOT") or "/opt/jarvis-verify-fixture")
                   / "SEEDED_PINS.json").read_text())
PINS = {k: (v.get("pin") if isinstance(v, dict) else v) for k, v in PINS.items()}
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
def chat_post(target):
    import chat
    r = chat.adapter().post(target, "secret-chat-text")
    return {"posted": r.ok, "error": r.error}
def smtp(to):
    sys.path.insert(0, os.environ["SKILLS"] + "/owner-email/scripts")
    import send_gate
    return send_gate.gated_send(to=to, subject="secret-subject", body="secret-body", transport="smtp")
def file_approval(_):
    import approvals_producer
    return approvals_producer.file_request("verify-sentinel", "Send the verify brochure",
                                           {"action": "send_email", "to": "x@client.example", "subject": "s", "body": "b"})
for step in os.environ["STEPS"].split(";"):
    kind, _, arg = step.partition(":")
    fn = {"send": send, "chat": chat_post, "smtp": smtp, "file": file_approval,
          "read": lambda a: g.gmail().users().messages().get(userId="me", id=a).execute(),
          "profile": lambda a: g.gmail().users().getProfile(userId="me").execute(),
          "insert": lambda a: g.calendar().events().insert(calendarId="primary",
              body={"summary": "secret-title", "start": {"date": "2026-10-14"}, "end": {"date": "2026-10-14"}}).execute()}[kind]
    run(step, lambda: fn(arg))
print(json.dumps(out, default=str))
"""


def probe(port, key, box, steps, run_id=None, session=None, source="gmail:m1"):
    extra = {"STEPS": ";".join(steps), "JARVIS_SKILL": "verify-sentinel", "JARVIS_SOURCE": source,
             "JARVIS_RUNTIME_CONFIG": str(box / "runtime.json"), "JARVIS_IDENTITY_CONFIG": str(vb.FIXTURE_IDENTITY)}
    if run_id:
        extra["JARVIS_RUN_ID"] = run_id
    if session:
        extra["HERMES_SESSION_ID"] = session
    return vb.skill_probe(port, key, "", probe=PROBE, extra=extra)


def db(box):
    return psycopg.connect((box / "pg_dsn").read_text().strip(), autocommit=True, row_factory=dict_row)


def actions(box):
    with db(box) as c:
        return c.execute("SELECT * FROM actions ORDER BY id").fetchall()


def approvals(box):
    with db(box) as c:
        return c.execute("SELECT * FROM approvals ORDER BY created_at").fetchall()


def grants(box):
    with db(box) as c:
        return c.execute("SELECT * FROM grants").fetchall()


def posts():
    return [x for x in vb.FakeGoogle.seen if x["method"] == "POST"]


class Session:
    def __init__(self, port, who):
        self.port = port
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        if who == "operator":
            body = {"passphrase": os.environ.get("VERIFY_OPERATOR_PASSPHRASE", "")}
        elif who:
            body = {"person_key": who, "pin": PINS[who]}
        else:
            return
        code, d = self.call("/api/login", body)
        if code != 200:
            sys.exit(f"login {who} failed: HTTP {code} {d}")

    def call(self, path, body=None, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=None if body is None else json.dumps(body).encode(),
                                     method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}


def feed(box):
    p = box / "feed.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


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

        print("── E: a clean run's internal send executes; the audit log has it, structure only")
        vb.FakeGoogle.seen.clear()
        out = probe(8714, key, box, ["send:dana@testco.example.com"], run_id="run-clean")
        ck("internal send executed (empty Cc header did not erase the To)",
           (out.get("send:dana@testco.example.com", {}).get("ok") or {}).get("id") == "sent1", str(out))
        ck("fake Google saw it", len(posts()) == 1)
        A = actions(box)
        ex = [a for a in A if a["outcome"] == "executed"]
        ck("audit: one executed internal_send for run-clean, untainted, with its source",
           len(ex) == 1 and ex[0]["action_class"] == "internal_send" and ex[0]["run_id"] == "run-clean"
           and ex[0]["tainted"] is False and ex[0]["skill"] == "verify-sentinel"
           and (ex[0]["source_kind"], ex[0]["source_id"]) == ("gmail", "m1"), str(ex))
        ck("audit: the decision was recorded before execution (allowed precedes executed)",
           [a["outcome"] for a in A if a["run_id"] == "run-clean"] == ["allowed", "executed"])
        dump = json.dumps(A, default=str)
        ck("audit carries no subject or body", "secret-subject" not in dump and "secret-body" not in dump)
        ck("canary: the probe really sent that subject (it reached the fake)", any("raw" in x["body"] for x in posts()))
        with db(box) as c:
            try:
                c.execute("UPDATE actions SET reason = 'rewritten'")
                ck("the app role cannot rewrite the audit log", False, "UPDATE succeeded")
            except psycopg.Error:
                ck("the app role cannot rewrite the audit log", True)

        print("── E: an external send files an approval holding the exact action")
        vb.FakeGoogle.seen.clear()
        a0 = vb.counter(tdb, "broker_sentinel_ask")
        out = probe(8714, key, box, ["send:buyer@client.example"], run_id="run-clean")
        r = out.get("send:buyer@client.example", {})
        ck("ActionHeld, outcome approval_required, verdict ask",
           (r.get("raised"), r.get("outcome"), r.get("verdict")) == ("ActionHeld", "approval_required", "ask"), str(r))
        ck("…counted (broker_sentinel_ask +1)", vb.counter(tdb, "broker_sentinel_ask") - a0 == 1)
        ck("…fake Google saw no send", not posts())
        P = [p for p in approvals(box) if p["status"] == "pending"]
        ck("one pending approval: origin broker, external_send, run and source kept",
           len(P) == 1 and P[0]["origin"] == "broker" and P[0]["action_class"] == "external_send"
           and P[0]["run_id"] == "run-clean" and (P[0]["source_kind"], P[0]["source_id"]) == ("gmail", "m1"), str(P))
        apr = P[0] if P else {}
        ck("…holding the exact message to send", "raw" in json.dumps(apr.get("action_json") or {}))
        ck("…and the audit log has approval_required with that approval id",
           any(a["outcome"] == "approval_required" and a["approval_id"] == apr.get("id") for a in actions(box)))

        print("── E: only the owner's structured answer decides it")
        dana, op, anon = Session(8714, "dana"), Session(8714, "operator"), Session(8714, None)
        code, q = dana.call("/api/approvals")
        item = next((i for i in q.get("approvals", []) if i["id"] == apr.get("id")), {})
        cbs = item.get("callbacks") or {}
        ck("the owner's queue carries signed callbacks, every grant scope offered for one destination",
           sorted((cbs.get("approve") or {})) == ["always", "once", "session", "task"] and cbs.get("reject"), str(cbs)[:200])
        ck("operator cannot see the queue (403)", op.call("/api/approvals")[0] == 403)
        ck("no session → 401", anon.call("/api/approvals")[0] == 401)
        ck("the agent's key is not a session (401)",
           anon.call(f"/api/approvals/{apr.get('id')}", {"decision": "approve"},
                     {"Authorization": f"Bearer {key}"})[0] == 401)
        ck("free text as a callback ('yes') → 403", dana.call(f"/api/approvals/{apr.get('id')}", {"callback": "yes"})[0] == 403)
        v, b, m = (cbs.get("reject") or "a.b.c").split(".")
        forged = (cbs.get("approve") or {}).get("once", "a.b.c").split(".")[1]
        ck("an approve body under the reject signature → 403",
           dana.call(f"/api/approvals/{apr.get('id')}", {"callback": f"{v}.{forged}.{m}"})[0] == 403)
        ck("a callback for this approval is refused on another id (403)",
           dana.call("/api/approvals/apr_000000000000", {"callback": cbs.get("reject")})[0] in (403, 404))
        ck("a decision that contradicts its callback → 409",
           dana.call(f"/api/approvals/{apr.get('id')}", {"callback": cbs.get("reject"), "decision": "approve"})[0] == 409)
        ck("…all refusals counted", vb.counter(tdb, "approvals_callback_refused") >= 2)
        ck("…and it is still pending, nothing sent", approvals(box)[-1]["status"] == "pending" and not posts())

        vb.FakeGoogle.seen.clear()
        code, d = dana.call(f"/api/approvals/{apr.get('id')}", {"callback": cbs["approve"]["always"]})
        ex = (d.get("approval") or {}).get("execution") or {}
        ck("approve with scope 'always' → 200, executed through the gate", code == 200 and ex.get("state") == "executed",
           f"{code} {json.dumps(d)[:300]}")
        ck("…fake Google saw exactly that one send", len(posts()) == 1)
        G = grants(box)
        ck("…a grant bound to gmail, verify-sentinel, buyer@client.example, always",
           len(G) == 1 and (G[0]["connector"], G[0]["skill"], G[0]["destination"], G[0]["scope"]) ==
           ("gmail", "verify-sentinel", "buyer@client.example", "always"), str(G))
        A = actions(box)
        ck("audit: approved, granted, then executed under that approval",
           [a["outcome"] for a in A if a["approval_id"] == apr.get("id")][-4:] == ["approved", "granted", "allowed", "executed"],
           str([a["outcome"] for a in A if a["approval_id"] == apr.get("id")]))
        ck("a second answer to the same approval → 409",
           dana.call(f"/api/approvals/{apr.get('id')}", {"callback": cbs["reject"]})[0] == 409)

        print("── E: the grant covers that destination and skill, nothing wider")
        vb.FakeGoogle.seen.clear()
        out = probe(8714, key, box, ["read:mext", "send:buyer@client.example", "send:other@client.example"],
                    run_id="run-granted")
        ck("a tainted run's send to the granted address goes (9.5 exception)",
           (out.get("send:buyer@client.example", {}).get("ok") or {}).get("id") == "sent1", str(out))
        ck("…a different address in the same domain still needs approval",
           out.get("send:other@client.example", {}).get("outcome") == "approval_required", str(out))
        ck("…fake Google saw one send", len(posts()) == 1)

        print("── E: a rejected approval runs nothing")
        probe(8714, key, box, ["send:third@client.example"], run_id="run-clean")
        p2 = [p for p in approvals(box) if p["status"] == "pending"][-1]
        cb2 = next(i for i in dana.call("/api/approvals")[1]["approvals"] if i["id"] == p2["id"])["callbacks"]
        vb.FakeGoogle.seen.clear()
        code, d = dana.call(f"/api/approvals/{p2['id']}", {"callback": cb2["reject"]})
        ck("reject → 200, status rejected, no execution", code == 200 and d["approval"]["status"] == "rejected"
           and "execution" not in d["approval"], json.dumps(d)[:200])
        ck("…fake Google saw nothing", not posts())

        print("── E: source record rule")
        vb.FakeGoogle.seen.clear()
        out = probe(8714, key, box, ["insert:x"], run_id="run-src", source="")
        ck("a calendar insert with no source record is denied",
           out.get("insert:x", {}).get("outcome") == "sentinel_denied" and "source record" in out["insert:x"].get("msg", ""),
           str(out))
        ck("…and nothing reached Google", not posts())

        print("── E: taint follows the run, from the database")
        t0 = vb.counter(tdb, "broker_sentinel_run_tainted")
        probe(8714, key, box, ["read:m1", "insert:x"], run_id="run-internal-read")
        ck("reading mail from the org does not taint", vb.counter(tdb, "broker_sentinel_run_tainted") - t0 == 0)
        ck("…and its write is recorded untainted", any(
            a["run_id"] == "run-internal-read" and a["outcome"] == "executed" and a["tainted"] is False for a in actions(box)))
        probe(8714, key, box, ["read:mext", "insert:x"], run_id="run-taint")
        ck("reading mail from outside the org taints the run (+1)", vb.counter(tdb, "broker_sentinel_run_tainted") - t0 == 1)
        ck("…a taint row names the run", any(a["event"] == "taint" and a["run_id"] == "run-taint" for a in actions(box)))
        ck("…the run's next write is decided as tainted", any(
            a["run_id"] == "run-taint" and a["outcome"] == "executed" and a["tainted"] is True for a in actions(box)))
        with db(box) as c:
            c.execute("INSERT INTO actions (event, outcome, run_id, reason) VALUES ('taint','tainted','run-db','seeded')")
        probe(8714, key, box, ["insert:x"], run_id="run-db")
        ck("taint lives in the database, not app memory (a seeded taint row applies)", any(
            a["run_id"] == "run-db" and a["outcome"] == "executed" and a["tainted"] is True for a in actions(box)))
        probe(8714, key, box, ["insert:x"], run_id="run-fresh")
        ck("a new run starts clean", any(a["run_id"] == "run-fresh" and a["tainted"] is False
                                         for a in actions(box) if a["outcome"] == "executed"))
        probe(8714, key, box, ["insert:x"], session="sess-77")
        ck("the Hermes session id is the run when JARVIS_RUN_ID is absent", any(a["run_id"] == "sess-77" for a in actions(box)))
        probe(8714, key, box, ["insert:x"])
        ck("no run identity at all → decided as tainted",
           any(a["run_id"] == "" and a["tainted"] is True for a in actions(box) if a["outcome"] == "executed"))

        print("── E: rate limit from the audit log (3 per recipient per day)")
        now = sentinel.utcnow()
        with db(box) as c:
            for ts in (now - timedelta(minutes=5), now - timedelta(minutes=6), now - timedelta(days=2)):
                c.execute("INSERT INTO actions (ts, event, outcome, action_class, destinations) "
                          "VALUES (%s, 'decision', 'executed', 'internal_send', %s)", (ts, ["omar@testco.example.com"]))
        vb.FakeGoogle.seen.clear()
        out = probe(8714, key, box, ["send:omar@testco.example.com", "send:omar@testco.example.com"], run_id="run-rate")
        ck("third send today goes (two today, one two days ago does not count)", len(posts()) == 1, str(out))
        ck("a fourth is denied by the rate limit", any(
            x.get("outcome") == "sentinel_denied" and "rate limit" in x.get("msg", "") for x in out.values()), str(out))

        print("── E: skills propose, the app files; outbound with no transport is refused")
        n0 = len(approvals(box))
        out = probe(8714, key, box, ["file:", "chat:deals", "smtp:dana@testco.example.com"], run_id="run-out")
        fid = (out.get("file:", {}) or {}).get("ok")
        rows = approvals(box)
        ck("approvals_producer filed through the broker (one new producer row)",
           len(rows) == n0 + 1 and rows[-1]["id"] == fid and rows[-1]["origin"] == "producer", str(out.get("file:")))
        ck("a chat post in broker mode is not posted: not_brokered",
           (out.get("chat:deals", {}).get("ok") or {}).get("posted") is False
           and "not_brokered" in str(out["chat:deals"]["ok"].get("error")), str(out.get("chat:deals")))
        sm = out.get("smtp:dana@testco.example.com", {}).get("ok") or {}
        ck("an SMTP send in broker mode is not sent: not_brokered", sm.get("sent") is False
           and sm.get("reason") == "not_brokered", str(sm))
        ck("…both recorded as outbound not_brokered", len([a for a in actions(box) if a["event"] == "outbound"
                                                           and a["outcome"] == "not_brokered"]) == 2)

        print("── S: shadow mode (writes on, so only shadow can stop it)")
        box_s, env_s = vb.start_sandbox("s", 8715, "1", endpoint, sentinel_mode="shadow")
        sandboxes.append(env_s)
        key_s = vb.env_value(box_s / "hermes/.env", "API_SERVER_KEY")
        vb.FakeGoogle.seen.clear()
        s0 = vb.counter(box_s / "threads.db", "broker_sentinel_shadowed")
        f0 = len(feed(box_s))
        out = probe(8715, key_s, box_s, ["profile:", "send:dana@testco.example.com", "send:buyer@client.example",
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
        sh = [a for a in actions(box_s) if a["outcome"] == "shadowed"]
        ck("audit: three shadowed rows with class and destinations",
           [(a["action_class"], a["destinations"]) for a in sh] ==
           [("internal_send", ["dana@testco.example.com"]), ("external_send", ["buyer@client.example"]),
            ("internal_write", [])], str(sh))
        ck("audit: nothing executed, no approval filed in shadow",
           not [a for a in actions(box_s) if a["outcome"] == "executed"] and not approvals(box_s))
        F = [e for e in feed(box_s)[f0:] if e.get("kind") == "sentinel.would_have"]
        ck("feed: three 'would have' lines, each naming its source and held action",
           len(F) == 3 and all("source gmail:m1" in e.get("detail", "") and "held action #" in e.get("detail", "")
                               for e in F), json.dumps(F)[:400])
        ck("feed: headlines say what Jarvis would have done", [e.get("headline", "") for e in F][:2] == [
            "Practice mode: Jarvis would have tried to send an email to dana@testco.example.com",
            "Practice mode: Jarvis would have tried to send an email to buyer@client.example"], str([e.get("headline") for e in F]))
        ck("feed carries no subject, title or body", not any(s in json.dumps(F) for s in
                                                             ("secret-subject", "secret-body", "secret-title")))
        hid = sh[1]["id"] if len(sh) > 1 else 0
        dana_s, op_s = Session(8715, "dana"), Session(8715, "operator")
        code, h = dana_s.call(f"/api/sentinel/held/{hid}")
        ck("owner opens the held send and sees the full proposal", code == 200 and "raw" in json.dumps(h.get("action_json")),
           f"{code} {str(h)[:200]}")
        ck("operator cannot open it (structure, never bodies)", op_s.call(f"/api/sentinel/held/{hid}")[0] == 403)

        f1 = len(feed(box_s))
        out = probe(8715, key_s, box_s, ["chat:deals", "smtp:buyer@client.example", "insert:x"], run_id="run-shadow-2",
                    source="")
        ck("a chat post in shadow is held, not posted",
           (out.get("chat:deals", {}).get("ok") or {}).get("posted") is False
           and "shadowed" in str(out["chat:deals"]["ok"].get("error")), str(out.get("chat:deals")))
        sm = out.get("smtp:buyer@client.example", {}).get("ok") or {}
        ck("an SMTP send in shadow is held", sm.get("held") is True and sm.get("sent") is False, str(sm))
        ins = out.get("insert:x", {})
        ck("a write with no source in shadow is held with verdict deny",
           ins.get("outcome") == "shadowed" and ins.get("verdict") == "deny", str(ins))
        F2 = [e for e in feed(box_s)[f1:] if e.get("kind") == "sentinel.would_have"]
        ck("feed: three more 'would have' lines (chat, mail, insert)", len(F2) == 3, json.dumps(F2)[:300])
        ck("…the sourceless one says so", any("no source record" in e.get("detail", "") for e in F2))
        out_rows = [a for a in actions(box_s) if a["event"] == "outbound" and a["outcome"] == "shadowed"]
        code, h = dana_s.call(f"/api/sentinel/held/{out_rows[0]['id'] if out_rows else 0}")
        ck("owner sees the held chat post's text", code == 200 and "secret-chat-text" in json.dumps(h), f"{code} {str(h)[:200]}")
        ck("fake Google still saw no POST", not posts())
    finally:
        for env in sandboxes:
            vb.stop_sandbox(env)
        srv.shutdown()
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
