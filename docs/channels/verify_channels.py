#!/usr/bin/env python3
"""Team channels: people talk to each other, @jarvis brings Jarvis in.

Starts its OWN sandbox (port 8713, box /tmp/claude-0/-root/channels-sandbox)
with the gateway pointed at a stub this script runs, so every request Jarvis
would get is recorded and read back: a turn that should not reach Jarvis is a
stub count that did not move, and "Jarvis read the whole channel" is the
request body, not the reply.

Every rule is driven over HTTP and read back from the ledger: threads.db
(messages, thread_reads, channel_settings, counters). Each refusal has a
presence twin, and the context trim has a canary that makes it fire.

Usage:
  python3 docs/channels/verify_channels.py [-v]
  (VERIFY_APP_ROOT / VERIFY_SKILLS_ROOT as for the gate; never live)
"""

import argparse
import json
import os
import pathlib
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=int(os.environ.get("CHANNELS_PORT", "8713")))
ap.add_argument("--box", default=os.environ.get("CHANNELS_SANDBOX", "/tmp/claude-0/-root/channels-sandbox"))
ap.add_argument("--pins", default="/opt/jarvis-verify-fixture/SEEDED_PINS.json")
ap.add_argument("--keep", action="store_true", help="leave the sandbox running afterwards")
ap.add_argument("-v", "--verbose", action="store_true")
args = ap.parse_args()

if args.port in (8700, 8730, 8740, 8750, 8760, 8770, 8780):
    sys.exit(f"REFUSING: :{args.port} is a tenant. This script starts its own sandbox.")
APP = pathlib.Path(__file__).resolve().parents[2]
if str(APP).startswith("/root/jarvis-app"):
    sys.exit("REFUSING: this is live's tree. Run it from the platform checkout.")
BASE = f"http://127.0.0.1:{args.port}"
BOX = pathlib.Path(args.box)
THREADS = BOX / "threads.db"
RUN = secrets.token_hex(3)
PY = os.environ.get("VERIFY_PYTHON", "/opt/jarvis/venv/bin/python")
PINS = json.loads(pathlib.Path(args.pins).read_text())
PINS = {k: (v.get("pin") if isinstance(v, dict) else v) for k, v in PINS.items()}

ok, skipped = [], []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra and (not cond or args.verbose) else ""))


def skip(name, why):
    skipped.append(name)
    print(f"  SKIP  {name} — {why}")


# ── the stub gateway ─────────────────────────────────────────────────────────

CALLS = []
CALLS_LOCK = threading.Lock()


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with CALLS_LOCK:
            CALLS.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
            n = len(CALLS)
        last = body.get("messages", [{}])[-1].get("content")
        if "SLOW" in json.dumps(last):
            time.sleep(3)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for frame in ({"choices": [{"delta": {"content": f"Stub reply {n}"}}]},
                      {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {}}):
            self.wfile.write(f"data: {json.dumps(frame)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


STUB_PORT = free_port()
stub = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), Stub)
threading.Thread(target=stub.serve_forever, daemon=True).start()


def calls():
    with CALLS_LOCK:
        return len(CALLS)


def last_call():
    with CALLS_LOCK:
        return CALLS[-1]


# ── the sandbox ──────────────────────────────────────────────────────────────

ENV = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "TASKS_")) and k != "HERMES_HOME"}
ENV.update({"TASKS_PORT": str(args.port), "TASKS_SANDBOX": str(BOX),
            "HERMES_API_URL": f"http://127.0.0.1:{STUB_PORT}", "VERIFY_APP_ROOT": str(APP)})
SANDBOX = [str(APP / "docs/tasks/tasks_sandbox.sh")]


def sandbox(cmd):
    return subprocess.run(["bash", *SANDBOX, cmd], env=ENV, capture_output=True, text=True)


sandbox("stop")
r = sandbox("start")
if r.returncode != 0:
    sys.exit("sandbox did not start:\n" + r.stdout[-800:] + r.stderr[-800:])
print(f"team channels — {BASE} (box {BOX}, stub gateway :{STUB_PORT}, run {RUN})\n")


def q(sql, *a):
    conn = sqlite3.connect(f"file:{THREADS}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, a)]
    finally:
        conn.close()


def nmsg(tid):
    return q("SELECT COUNT(*) n FROM messages WHERE thread_id = ?", tid)[0]["n"]


def counter(name):
    r = q("SELECT value FROM counters WHERE name = ?", name)
    return r[0]["value"] if r else 0


def login(who):
    c = httpx.Client(base_url=BASE, timeout=30)
    body = {"passphrase": os.environ["VERIFY_OPERATOR_PASSPHRASE"]} if who == "operator" \
        else {"person_key": who, "pin": PINS[who]}
    r = c.post("/api/login", json=body)
    if r.status_code != 200 or not r.json().get("ok"):
        sys.exit(f"login {who} failed: HTTP {r.status_code}")
    return c


def post(c, tid, text, atts=None):
    """(status, [ndjson events])"""
    r = c.post("/api/chat", json={"thread_id": tid, "message": text, "attachments": atts or []}, timeout=60)
    if r.status_code != 200:
        return r.status_code, []
    return 200, [json.loads(x) for x in r.text.splitlines() if x.strip()]


def in_box(code):
    """Run app code in a NEW interpreter pointed only at this sandbox's box.
    Never import app/ into this process: app.config defaults HERMES_HOME to
    ~/.hermes, which is live's."""
    e = {"PATH": os.environ["PATH"], "PYTHONPATH": str(APP), "PYTHONDONTWRITEBYTECODE": "1",
         "HERMES_HOME": str(BOX / "hermes"), "JARVIS_RUNTIME_CONFIG": str(BOX / "runtime.json"),
         "JARVIS_SKILLS_ROOT": os.environ.get("VERIFY_SKILLS_ROOT") or "/opt/jarvis-verify-fixture/skills/openclaw-imports",
         "JARVIS_IDENTITY_CONFIG": "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json",
         "JARVIS_THREADS_DB": str(THREADS), "JARVIS_ACCOUNTS_DB": str(BOX / "accounts.db"),
         "HERMES_API_URL": f"http://127.0.0.1:{STUB_PORT}"}
    r = subprocess.run([PY, "-I", "-B", "-c", "import sys; sys.path.insert(0, %r)\n" % str(APP) + code],
                       capture_output=True, text=True, env=e, cwd=str(BOX))
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-600:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def thread_row(c, tid):
    return next((t for t in c.get("/api/threads").json()["threads"] if t["thread_id"] == tid), None)


try:
    if "VERIFY_OPERATOR_PASSPHRASE" not in os.environ:
        sys.exit("VERIFY_OPERATOR_PASSPHRASE is not set (the gate exports it from the fixture)")
    J = {w: login(w) for w in ("dana", "priya", "omar", "tester", "operator")}

    print("── 0. upgrade: authors and read positions ─────────────────────────")
    legacy = q("SELECT COUNT(*) n FROM messages WHERE author IS NOT NULL")[0]["n"]
    total0 = q("SELECT COUNT(*) n FROM messages")[0]["n"]
    ck("every message stored before this run has no author (none guessed)", legacy == 0, f"{legacy} of {total0}")
    if total0 == 0:
        skip("an older message reads back with author null", "the fixture holds no messages")
    else:
        tid0 = q("SELECT thread_id FROM messages ORDER BY id LIMIT 1")[0]["thread_id"]
        h = J["dana"].get("/api/chat/history", params={"thread_id": tid0})
        if h.status_code == 200 and h.json()["messages"]:
            m0 = h.json()["messages"][0]
            ck("an older message reads back with author null", m0.get("author") is None and m0.get("author_name") is None)
        else:
            skip("an older message reads back with author null", f"dana cannot read {tid0} (HTTP {h.status_code})")
    mem = q("SELECT COUNT(*) n FROM thread_members")[0]["n"]
    seeded = q("SELECT COUNT(*) n FROM thread_members m JOIN thread_reads r USING (thread_id, person_key)")[0]["n"]
    ck("every existing membership got a read position at upgrade", mem == seeded and counter("thread_reads_seeded") == 1,
       f"{seeded}/{mem}")
    lit = [t["name"] for t in J["dana"].get("/api/threads").json()["threads"] if t.get("unread")]
    ck("nothing that was already there shows as unread", lit == [], f"{lit}")

    print("\n── 1. channel and who may change it ───────────────────────────────")
    r = J["dana"].post("/api/threads", json={"name": f"12 Oak {RUN}"})
    ch = r.json()["thread"]["thread_id"]
    for k in ("omar", "priya"):
        J["dana"].post(f"/api/threads/{ch}/members", json={"person_key": k})
    members = {m["person_key"] for m in J["dana"].get(f"/api/threads/{ch}/members").json()["members"]}
    ck("owner made the channel and added omar and priya", members == {"dana", "omar", "priya"}, f"{members}")
    pos = {r["person_key"]: r["last_read_id"] for r in q("SELECT * FROM thread_reads WHERE thread_id = ?", ch)}
    ck("each of them starts with a read position", set(pos) == {"dana", "omar", "priya"}, f"{pos}")

    ck("a member cannot create a channel", J["omar"].post("/api/threads", json={"name": f"x {RUN}"}).status_code == 403)
    code = J["omar"].post(f"/api/threads/{ch}/members", json={"person_key": "tester"}).status_code
    ck("a member cannot add someone (403, no row)", code == 403 and not q(
        "SELECT 1 FROM thread_members WHERE thread_id = ? AND person_key = 'tester'", ch))
    code = J["omar"].patch(f"/api/threads/{ch}", json={"name": "hijacked"}).status_code
    name = q("SELECT name FROM threads WHERE thread_id = ?", ch)[0]["name"]
    ck("a member cannot rename the channel (403, name unchanged)", code == 403 and name == f"12 Oak {RUN}", f"{code} {name}")
    code = J["omar"].post(f"/api/threads/{ch}/archive").status_code
    arch = q("SELECT archived FROM threads WHERE thread_id = ?", ch)[0]["archived"]
    ck("a member cannot archive the channel (403, still open)", code == 403 and arch == 0, f"{code} {arch}")
    code = J["priya"].patch(f"/api/threads/{ch}", json={"name": f"12 Oak St {RUN}"}).status_code
    name = q("SELECT name FROM threads WHERE thread_id = ?", ch)[0]["name"]
    ck("presence twin: an admin can rename it", code == 200 and name == f"12 Oak St {RUN}", f"{code} {name}")

    print("\n── 2. people talk; Jarvis stays out unless called ────────────────")
    m0, c0 = nmsg(ch), calls()
    code, ev = post(J["omar"], ch, f"hi team, inspection is Thursday {RUN}")
    first = ev[0] if ev else {}
    ck("omar's message is posted, Jarvis not called", code == 200 and first.get("type") == "posted"
       and first.get("jarvis") is False and len(ev) == 1, f"{code} {ev}")
    rows = q("SELECT id, role, author, content FROM messages WHERE thread_id = ? ORDER BY id", ch)
    ck("ledger: one row, written by omar, no reply", nmsg(ch) - m0 == 1 and rows[-1]["author"] == "omar"
       and rows[-1]["role"] == "user", f"delta {nmsg(ch) - m0} {rows[-1:]}")
    ck("the stub gateway was not called", calls() - c0 == 0, f"delta {calls() - c0}")
    omar_id = rows[-1]["id"]
    ck("the posted event carries the saved id and omar's name",
       first.get("message", {}).get("id") == omar_id and first["message"].get("author") == "omar"
       and bool(first["message"].get("author_name")), f"{first.get('message')}")

    print("\n── 3. the others see it; unread and read ─────────────────────────")
    h = J["dana"].get("/api/chat/history", params={"thread_id": ch}).json()
    seen = [m for m in h["messages"] if m["id"] == omar_id]
    names = {p["person_key"]: p["name"] for p in J["dana"].get("/api/team/directory").json()["people"]}
    ck("dana's history shows omar's message by name", seen and seen[0]["author"] == "omar"
       and seen[0]["author_name"] == names.get("omar"), f"{seen}")
    after = J["dana"].get("/api/chat/history", params={"thread_id": ch, "after": omar_id}).json()
    before = J["dana"].get("/api/chat/history", params={"thread_id": ch, "after": omar_id - 1}).json()
    ck("the poll cursor: after=id returns only newer messages", after["messages"] == []
       and [m["id"] for m in before["messages"]] == [omar_id] and after["last_id"] == omar_id)
    td, tp, to = thread_row(J["dana"], ch), thread_row(J["priya"], ch), thread_row(J["omar"], ch)
    ck("unread for dana and priya, not for omar who wrote it",
       td["unread_count"] == 1 and tp["unread_count"] == 1 and to["unread_count"] == 0 and to["unread"] is False,
       f"{td['unread_count']} {tp['unread_count']} {to['unread_count']}")
    ck("the row's preview says who wrote it", td["preview_author_name"] == names.get("omar")
       and td["preview_mine"] is False and to["preview_mine"] is True)
    r = J["dana"].post(f"/api/threads/{ch}/read", json={"last_id": omar_id + 10_000})
    stored = q("SELECT last_read_id FROM thread_reads WHERE thread_id = ? AND person_key = 'dana'", ch)[0]["last_read_id"]
    ck("dana reads it: position clamped to the channel's last message", r.status_code == 200 and stored == omar_id,
       f"{r.status_code} {stored}")
    J["dana"].post(f"/api/threads/{ch}/read", json={"last_id": 0})
    stored2 = q("SELECT last_read_id FROM thread_reads WHERE thread_id = ? AND person_key = 'dana'", ch)[0]["last_read_id"]
    ck("a read position never moves back", stored2 == omar_id, f"{stored2}")
    ck("dana is caught up; priya still has it unread (presence twin)",
       thread_row(J["dana"], ch)["unread_count"] == 0 and thread_row(J["priya"], ch)["unread_count"] == 1)

    print("\n── 4. @mentions of people ────────────────────────────────────────")
    c0 = calls()
    post(J["omar"], ch, f"@{names.get('dana', 'Dana')} can you check the seller's disclosure? {RUN}")
    td, tp = thread_row(J["dana"], ch), thread_row(J["priya"], ch)
    ck("dana has a mention; priya has the message but no mention",
       td["mentions"] == 1 and tp["mentions"] == 0 and tp["unread_count"] == 2,
       f"dana {td['mentions']} priya {tp['mentions']}/{tp['unread_count']}")
    ck("a person mention does not call Jarvis", calls() - c0 == 0)

    print("\n── 5. @jarvis: answers the caller, reads the whole channel ───────")
    m0, c0, k0 = nmsg(ch), calls(), counter("channel_jarvis_called")
    code, ev = post(J["dana"], ch, f"@jarvis what did Omar need from me? {RUN}")
    types = [e["type"] for e in ev]
    ck("posted (jarvis true), then the reply, then saved", code == 200 and types[0] == "posted"
       and ev[0]["jarvis"] is True and "delta" in types and types[-1] == "saved", f"{types}")
    ck("the stub gateway was called once", calls() - c0 == 1, f"delta {calls() - c0}")
    body, hdr = last_call()["body"], last_call()["headers"]
    sysmsg = body["messages"][0]["content"]
    convo = "\n".join(m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
                      for m in body["messages"][1:])
    ck("context: omar's earlier lines, labelled with his name",
       f"{names.get('omar')}: hi team, inspection is Thursday {RUN}" in convo
       and f"{names.get('omar')}: @{names.get('dana')} can you check" in convo, convo[-300:])
    ck("the last turn is dana's @jarvis message", isinstance(body["messages"][-1]["content"], str)
       and body["messages"][-1]["content"].endswith(f"{names.get('dana')}: @jarvis what did Omar need from me? {RUN}"))
    ck("status block names the channel, its people and the caller",
       sysmsg.startswith("STATUS FROM THE APP") and f"12 Oak St {RUN}" in sysmsg and "People in it:" in sysmsg
       and f"called you with @" in sysmsg and f"Answer {names.get('dana')}" in sysmsg)
    kept, dropped = in_box("import json; from app import agent_status\n"
                           f"k, _, d = agent_status.shape_prompt({sysmsg!r}); print(json.dumps([k, d]))")
    ck("the model proxy's trim keeps the channel lines (one paragraph)",
       "\n\n" not in sysmsg and "Where: the team channel" in kept and not dropped, f"dropped {dropped}")
    ck("the turn acts as dana (actor reference sent)", bool(hdr.get("x-jarvis-actor-ref")))
    rows = q("SELECT id, role, author, content FROM messages WHERE thread_id = ? ORDER BY id", ch)
    ck("ledger: dana's message (author dana) and one reply (no author)", nmsg(ch) - m0 == 2
       and rows[-2]["author"] == "dana" and rows[-1]["role"] == "assistant" and rows[-1]["author"] is None
       and rows[-1]["content"].startswith("Stub reply"), f"{rows[-2:]}")
    ck("saved names the reply's row", ev[-1].get("ids") == [rows[-1]["id"]], f"{ev[-1]}")
    ck("counter channel_jarvis_called +1", counter("channel_jarvis_called") - k0 == 1)

    print("\n── 6. the whole channel, past the old 40-turn window ─────────────")
    for i in range(45):
        post(J["omar"], ch, f"note {i} {RUN}")
    c0 = calls()
    post(J["priya"], ch, f"@Jarvis sum up the notes {RUN}")
    convo = "\n".join(m["content"] for m in last_call()["body"]["messages"][1:] if isinstance(m["content"], str))
    total = nmsg(ch)
    lines = sum(1 for x in convo.splitlines() if x.startswith(f"{names.get('omar')}: "))
    ck("one call, and its context still holds the channel's first message",
       calls() - c0 == 1 and f"hi team, inspection is Thursday {RUN}" in convo and lines == 47,
       f"{lines} of omar's lines, {total} rows")

    print("\n── 7. canary: a channel too long for one call ────────────────────")
    big = J["dana"].post("/api/threads", json={"name": f"Long {RUN}"}).json()["thread"]["thread_id"]
    filler = "x" * 7_900
    for i in range(52):
        post(J["dana"], big, f"{i:02d} {filler}")
    k0, c0 = counter("channel_context_trimmed"), calls()
    post(J["dana"], big, f"@jarvis anything? {RUN}")
    msgs = last_call()["body"]["messages"][1:]
    size = sum(len(m["content"]) for m in msgs if isinstance(m["content"], str))
    ck("it fires: oldest left out, the model is told, counter +1",
       calls() - c0 == 1 and "earlier messages are in this channel, too long ago" in msgs[0]["content"]
       and counter("channel_context_trimmed") - k0 == 1 and size <= 400_200 and f"@jarvis anything? {RUN}" in msgs[-1]["content"],
       f"{size} chars, first: {msgs[0]['content'][:90]!r}")

    print("\n── 8. Settings: when Jarvis answers ──────────────────────────────")
    s = J["omar"].get("/api/channels/settings").json()
    ck("everyone reads it; the default is mention; a member can't change it",
       s["jarvis_replies"] == "mention" and s["can_change"] is False)
    code = J["omar"].put("/api/channels/settings", json={"jarvis_replies": "every"}).status_code
    ck("a member's change is refused (403, nothing stored)", code == 403 and not q("SELECT * FROM channel_settings"))
    code = J["priya"].put("/api/channels/settings", json={"jarvis_replies": "every"}).status_code
    row = q("SELECT * FROM channel_settings")
    ck("an admin switches it to every message", code == 200 and row and row[0]["value"] == "every"
       and row[0]["updated_by"] == "priya", f"{code} {row}")
    c0 = calls()
    code, ev = post(J["omar"], ch, f"anyone around? {RUN}")
    ck("now a plain message is answered", ev and ev[0]["jarvis"] is True and calls() - c0 == 1
       and "answer every message" in last_call()["body"]["messages"][0]["content"], f"{[e['type'] for e in ev]}")
    J["dana"].put("/api/channels/settings", json={"jarvis_replies": "mention"})
    c0 = calls()
    code, ev = post(J["omar"], ch, f"back to normal {RUN}")
    ck("back to mention: a plain message is not answered", ev and ev[0]["jarvis"] is False and calls() - c0 == 0)
    ck("a bad value is refused", J["dana"].put("/api/channels/settings", json={"jarvis_replies": "sometimes"}).status_code == 422)
    ck("the thread list carries the setting for the composer",
       J["omar"].get("/api/threads").json().get("jarvis_replies") == "mention")

    print("\n── 9. Ask Jarvis switched off for one person ─────────────────────")
    J["dana"].patch("/api/team/omar", json={"features": {"ask": False}})
    m0, c0, k0 = nmsg(ch), calls(), counter("channel_jarvis_refused_feature_off")
    code, ev = post(J["omar"], ch, f"@jarvis help {RUN}")
    ck("omar's message posts, Jarvis does not answer, and says why",
       ev and ev[0]["jarvis"] is False and any(e["type"] == "notice" and "switched off" in e["message"] for e in ev)
       and calls() - c0 == 0 and nmsg(ch) - m0 == 1 and counter("channel_jarvis_refused_feature_off") - k0 == 1,
       f"{ev}")
    J["dana"].patch("/api/team/omar", json={"features": {"ask": True}})
    c0 = calls()
    post(J["omar"], ch, f"@jarvis help again {RUN}")
    ck("presence twin: switched back on, Jarvis answers him", calls() - c0 == 1)

    print("\n── 10. outside the channel ───────────────────────────────────────")
    m0 = nmsg(ch)
    code, _ = post(J["tester"], ch, f"let me in {RUN}")
    ck("a non-member cannot post (404), read (404) or mark read (404)", code == 404
       and J["tester"].get("/api/chat/history", params={"thread_id": ch}).status_code == 404
       and J["tester"].post(f"/api/threads/{ch}/read", json={"last_id": 1}).status_code == 404
       and nmsg(ch) == m0)
    ck("and does not see it in the list", thread_row(J["tester"], ch) is None)
    code, _ = post(J["operator"], ch, "hello")
    to = thread_row(J["operator"], ch)
    ck("operator: no posting (403), sees the row without text or unread",
       code == 403 and to is not None and to["preview"] is None and to["unread"] is False and nmsg(ch) == m0)

    print("\n── 11. who's on the team: the directory ──────────────────────────")
    email = f"newhire-{RUN}@testco.example.com"
    r = J["dana"].post("/api/team/invite", json={"name": f"Newhire {RUN}", "email": email, "role": "member"})
    if r.status_code != 201:
        skip("an invited person (no PIN) can be added to a channel", f"invite returned {r.status_code}: {r.text[:120]}")
    else:
        key = r.json()["person"]["person_key"]
        in_dir = key in {p["person_key"] for p in J["omar"].get("/api/team/directory").json()["people"]}
        in_picker = key in {p["person_key"] for p in httpx.get(f"{BASE}/api/people").json()["people"]}
        code = J["dana"].post(f"/api/threads/{ch}/members", json={"person_key": key}).status_code
        ck("an invited person (no PIN) is in the directory, not the sign-in picker, and can be added",
           in_dir and not in_picker and code == 201, f"dir {in_dir} picker {in_picker} add {code}")

    print("\n── 12. the direct line is unchanged ──────────────────────────────")
    direct = next(t for t in J["omar"].get("/api/threads").json()["threads"] if t["is_direct"])["thread_id"]
    m0, c0 = nmsg(direct), calls()
    code, ev = post(J["omar"], direct, f"plain question, no mention {RUN}")
    rows = q("SELECT role, author FROM messages WHERE thread_id = ? ORDER BY id DESC LIMIT 2", direct)
    ck("no @ needed, no posted/saved lines, and now the question has its author",
       calls() - c0 == 1 and ev and ev[0]["type"] == "delta" and not any(e["type"] in ("posted", "saved") for e in ev)
       and nmsg(direct) - m0 == 2 and rows[1] == {"role": "user", "author": "omar"}, f"{[e['type'] for e in ev]} {rows}")
    tr = thread_row(J["omar"], direct)
    ck("he watched the reply arrive, so nothing on his line is unread", tr["unread_count"] == 0
       and q("SELECT last_read_id FROM thread_reads WHERE thread_id = ? AND person_key = 'omar'", direct)[0]["last_read_id"]
       == q("SELECT MAX(id) m FROM messages WHERE thread_id = ?", direct)[0]["m"], f"{tr['unread_count']}")
    # Something lands on his line that he did not watch (a background job, say):
    # nothing moves his position, so the "not written by them" filter alone
    # decides what counts. His own words never do; the new reply does.
    conn = sqlite3.connect(str(THREADS), timeout=10)
    with conn:
        conn.executemany("INSERT INTO messages (thread_id, role, content, ts, partial, author) VALUES (?, ?, ?, ?, 0, ?)",
                         [(direct, "user", f"from another device {RUN}", "2026-10-27T00:00:00+00:00", "omar"),
                          (direct, "assistant", f"later reply {RUN}", "2026-10-27T00:00:01+00:00", None)])
    conn.close()
    tr = thread_row(J["omar"], direct)
    ck("his own question is not unread to him; a reply he didn't see is", tr["unread_count"] == 1, f"{tr['unread_count']}")

    print("\n── 13. others see Jarvis working ─────────────────────────────────")
    res = {}
    t = threading.Thread(target=lambda: res.update(r=post(J["dana"], ch, f"@jarvis SLOW one please {RUN}")))
    t.start()
    busy = []
    for _ in range(20):
        time.sleep(0.25)
        busy = J["priya"].get("/api/chat/history", params={"thread_id": ch, "after": 10**9}).json()["jarvis_answering"]
        if busy:
            break
    t.join()
    done = J["priya"].get("/api/chat/history", params={"thread_id": ch, "after": 10**9}).json()["jarvis_answering"]
    ck("while it answers, priya sees whom; afterwards nothing", busy == [names.get("dana")] and done == [],
       f"during {busy} after {done}")
finally:
    if not args.keep:
        sandbox("stop")
    stub.shutdown()

n = len(ok)
print(f"\n{sum(ok)}/{n} passed" + (f", {len(skipped)} skipped" if skipped else ""))
sys.exit(0 if all(ok) else 1)
