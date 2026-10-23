#!/usr/bin/env python3
"""iMessage from each person's own Mac, end to end, without a tenant or a Mac.

Builds a fake Mac home: a Messages chat.db and a Contacts database with the
real tables and columns, including a message whose text is only in
attributedBody (as recent macOS stores it), a reaction, and a message older
than 30 days. Then a child process serves the real app routes over HTTP
against the verify sandbox's databases (docs/tasks/tasks_sandbox.sh, started
first), and the real helper (mac-helper/jarvis_imessage.py, run with the
system python3 and no packages) pairs and syncs against it. The real plugin
tool (run_action.py imessage_search) reads through the broker.

Proves:
- a wrong, reused or expired pairing code gets no token (presence twin: a
  fresh code does);
- the helper sends real texts with contact names, decodes attributedBody, and
  skips reactions and old messages; a second sync sends only what is new;
- texts are stored encrypted (presence twin: they decrypt);
- Jarvis reads only the chat turn's own person's texts: dana never sees
  omar's (presence twin: each sees their own); no turn gets nothing; someone
  with no Mac is told how to connect one; reading taints the run;
- the agent is told whether the Mac is connected and that it cannot send;
  the operator gets no Mac routes and no iMessage tool;
- disconnecting revokes the token (the helper is refused) and deletes that
  person's synced texts, not anyone else's;
- there is no send route.

--canary makes every read use omar's texts: the isolation check must FAIL.

Never :8700 or :8710. Prints no message text beyond the made-up fixtures.
"""
import json
import os
import pathlib
import secrets
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
SB = pathlib.Path(os.environ.get("TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox"))
SCR = pathlib.Path("/tmp/verify-imessage")
HELPER = APP_ROOT / "mac-helper" / "jarvis_imessage.py"
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
WORD = "zebra" + secrets.token_hex(3)
LONG = ("The inspection moved to Thursday at 10, " + WORD + ". ") * 4

ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({extra})" if extra and not cond else ""))


def apple(dt):
    return int((dt - APPLE_EPOCH).total_seconds() * 1e9)


def typedstream(text):
    raw = text.encode()
    n = bytes([len(raw)]) if len(raw) < 0x80 else b"\x81" + len(raw).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject"
            b"\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + n + raw +
            b"\x86\x84\x02iI\x01\x05\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i\x01\x92\x86\x86")


def make_mac(home, who, texts):
    """A Messages chat.db and a Contacts database with the real schema (the
    columns the helper reads, plus neighbours it must ignore)."""
    msgs = home / "Library" / "Messages"
    book = home / "Library" / "Application Support" / "AddressBook" / "Sources" / "A1B2"
    msgs.mkdir(parents=True, exist_ok=True)
    book.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(msgs / "chat.db")
    db.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, id TEXT NOT NULL, country TEXT,
            service TEXT NOT NULL, uncanonicalized_id TEXT, person_centric_id TEXT, UNIQUE (id, service));
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, style INTEGER,
            state INTEGER, account_id TEXT, properties BLOB, chat_identifier TEXT, service_name TEXT,
            room_name TEXT, account_login TEXT, is_archived INTEGER DEFAULT 0, last_addressed_handle TEXT,
            display_name TEXT, group_id TEXT, is_filtered INTEGER DEFAULT 0, successful_query INTEGER);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, text TEXT,
            replace INTEGER DEFAULT 0, service_center TEXT, handle_id INTEGER DEFAULT 0, subject TEXT,
            country TEXT, attributedBody BLOB, version INTEGER DEFAULT 0, type INTEGER DEFAULT 0,
            service TEXT, account TEXT, account_guid TEXT, error INTEGER DEFAULT 0, date INTEGER,
            date_read INTEGER, date_delivered INTEGER, is_delivered INTEGER DEFAULT 0,
            is_finished INTEGER DEFAULT 0, is_emote INTEGER DEFAULT 0, is_from_me INTEGER DEFAULT 0,
            is_empty INTEGER DEFAULT 0, is_read INTEGER DEFAULT 0, cache_has_attachments INTEGER DEFAULT 0,
            item_type INTEGER DEFAULT 0, other_handle INTEGER DEFAULT 0, group_title TEXT,
            associated_message_guid TEXT, associated_message_type INTEGER DEFAULT 0,
            thread_originator_guid TEXT);
        CREATE TABLE chat_message_join (chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE,
            message_id INTEGER REFERENCES message (ROWID) ON DELETE CASCADE, message_date INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, message_id));
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER, UNIQUE(chat_id, handle_id));
    """)
    db.execute("INSERT INTO handle (id, service) VALUES ('+15125550142', 'iMessage')")
    db.execute("INSERT INTO chat (guid, chat_identifier, service_name, display_name) "
               "VALUES ('iMessage;-;+15125550142', '+15125550142', 'iMessage', '')")
    db.execute("INSERT INTO chat_handle_join VALUES (1, 1)")
    db.commit()
    db.close()
    ab = sqlite3.connect(book / "AddressBook-v22.abcddb")
    ab.executescript("""
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZFIRSTNAME VARCHAR,
            ZLASTNAME VARCHAR, ZORGANIZATION VARCHAR, ZNICKNAME VARCHAR);
        CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER VARCHAR,
            ZLABEL VARCHAR);
        CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS VARCHAR,
            ZLABEL VARCHAR);
        INSERT INTO ZABCDRECORD VALUES (1, 22, 'Brad', 'Inspector', NULL, NULL);
        INSERT INTO ZABCDPHONENUMBER VALUES (1, 1, '(512) 555-0142', '_$!<Mobile>!$_');
    """)
    ab.commit()
    ab.close()
    add_messages(home, who, texts)


def add_messages(home, who, texts):
    db = sqlite3.connect(home / "Library" / "Messages" / "chat.db")
    now = datetime.now(timezone.utc)
    for kind, text, age in texts:
        guid = f"{who}-{secrets.token_hex(6)}"
        when = apple(now - age)
        if kind == "attributed":
            cur = db.execute("INSERT INTO message (guid, text, attributedBody, handle_id, date, is_from_me) "
                             "VALUES (?, NULL, ?, 1, ?, 0)", (guid, typedstream(text), when))
        elif kind == "reaction":
            cur = db.execute("INSERT INTO message (guid, text, handle_id, date, associated_message_type, "
                             "associated_message_guid) VALUES (?, ?, 1, ?, 2000, 'p:0/x')", (guid, text, when))
        else:
            cur = db.execute("INSERT INTO message (guid, text, handle_id, date, is_from_me) VALUES (?, ?, 1, ?, ?)",
                             (guid, text, when, 1 if kind == "mine" else 0))
        db.execute("INSERT INTO chat_message_join VALUES (1, ?, ?)", (cur.lastrowid, when))
    db.commit()
    db.close()


CHILD = r'''
import json, os, pathlib, socket, sqlite3, subprocess, sys, threading, time
sys.path.insert(0, os.environ["APP_ROOT"])
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import actor_refs, agent_status, auth, broker, imessage, platform_store as store
broker._authorize = lambda request: None
if os.environ.get("CANARY") == "1":
    real = imessage.search
    imessage.search = lambda person, *a, **k: real("omar", *a, **k)
SCR, HELPER, PLUGIN = pathlib.Path(os.environ["SCR"]), os.environ["HELPER"], os.environ["PLUGIN"]
WORD = os.environ["WORD"]
app = FastAPI(); app.include_router(imessage.broker_router); app.include_router(imessage.router)
SESS = {}
app.dependency_overrides[auth.current_session] = lambda: SESS
def as_person(key, role):
    SESS.clear(); SESS.update({"person_key": key, "role": role})
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)
URL = f"http://127.0.0.1:{port}"
c = TestClient(app)
out = {}
for who in ("dana", "omar", "priya"):
    for d in imessage.devices(who):
        imessage.disconnect(who, d["device_id"])

def helper(who, *args):
    home = SCR / who
    p = subprocess.run(["/usr/bin/python3", "-I", HELPER, "--config", str(home / "helper.json"),
                        "--db", str(home / "Library/Messages/chat.db"),
                        "--contacts", str(home / "Library/Application Support/AddressBook"), *args],
                       capture_output=True, text=True, timeout=120, env={"PATH": "/usr/bin:/bin", "HOME": str(home)})
    return p.returncode, (p.stdout + p.stderr).strip()

as_person("omar", "member")
out["claim_wrong"] = c.post("/api/mac/claim", json={"code": "AAAA-BBBB"}).status_code
conn = imessage._db()
conn.execute("INSERT INTO mac_pairings (code_hash, person_key, expires_at) VALUES (?,?,?)",
             (imessage._hash("ZZZZYYYY"), "omar", "2000-01-01T00:00:00+00:00")); conn.commit()
out["claim_expired"] = c.post("/api/mac/claim", json={"code": "ZZZZ-YYYY"}).status_code
code = c.post("/api/mac/pair").json()["code"]
out["pair_omar"] = list(helper("omar", "pair", "--server", URL, "--code", code, "--name", "Omar's MacBook"))
out["claim_reused"] = c.post("/api/mac/claim", json={"code": code}).status_code
cfg = json.loads((SCR / "omar" / "helper.json").read_text())
out["config_mode"] = oct((SCR / "omar" / "helper.json").stat().st_mode & 0o777)
out["sync1"] = list(helper("omar", "sync"))
out["count1"] = imessage.message_count("omar")
out["sync2"] = list(helper("omar", "sync"))
subprocess.run([sys.executable, os.environ["ADD"], "omar"], check=True)
out["sync3"] = list(helper("omar", "sync"))
out["count3"] = imessage.message_count("omar")
raw = b"".join(r["text_cipher"] for r in conn.execute("SELECT text_cipher FROM imessages WHERE person_key='omar'"))
out["at_rest"] = [WORD.encode() in raw, any(WORD in t["text"] for t in imessage.search("omar", days=30, limit=50))]

as_person("dana", "owner")
code = c.post("/api/mac/pair").json()["code"]
out["pair_dana"] = list(helper("dana", "pair", "--server", URL, "--code", code))
out["sync_dana"] = list(helper("dana", "sync"))
out["status_dana"] = c.get("/api/mac/status").json()

refs = {w: actor_refs.mint(w, f"t-{w}", r) for w, r in (("dana", "owner"), ("omar", "member"), ("priya", "admin"))}
def read(who, run, **kw):
    r = c.post("/api/broker/imessage", json={"ref": refs[who], **kw}, headers={"x-jarvis-run": run})
    return r.status_code, r.json()
run_o, run_d = f"verify-imessage-omar-{os.getpid()}", f"verify-imessage-dana-{os.getpid()}"
code_o, r_o = read("omar", run_o, query=WORD, days=30)
code_d, r_d = read("dana", run_d, days=30, limit=50)
blob = lambda r: json.dumps(r.get("messages") or [])
out["read_omar"] = [code_o, "Thursday at 10" in blob(r_o), WORD in blob(r_o), "Brad Inspector" in blob(r_o),
                    bool(r_o.get("note"))]
out["read_dana"] = [code_d, WORD in blob(r_d), "dana-only" in blob(r_d)]
out["contact"] = [code_o, read("omar", run_o, contact="brad", days=30, limit=50)[1].get("messages", [])[:1]]
out["reaction_or_old"] = ["Loved" in blob(read("omar", run_o, days=30, limit=50)[1]),
                          "ancient" in blob(read("omar", run_o, days=30, limit=50)[1])]
out["no_turn"] = c.post("/api/broker/imessage", json={"ref": "app:" + "x" * 32}).status_code
out["no_mac"] = list(read("priya", "verify-imessage-priya"))
out["tainted"] = [store.is_tainted(run_o), store.is_tainted(run_d), store.is_tainted("verify-imessage-never")]

def plugin(who, args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CANARY")}
    env.update({"JARVIS_APP_BROKER_URL": URL, "API_SERVER_KEY": "verify-not-a-key", "JARVIS_ACTOR_MSG": refs[who],
                "JARVIS_RUN_ID": f"verify-imessage-plugin-{who}-{os.getpid()}"})
    p = subprocess.run([sys.executable, PLUGIN, "imessage_search"], input=json.dumps(args), capture_output=True,
                       text=True, env=env, timeout=60)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"raw": (p.stdout + p.stderr)[-400:]}
po, pp = plugin("omar", {"query": WORD, "days": 30}), plugin("priya", {})
out["plugin"] = [po.get("ok"), WORD in json.dumps(po), pp.get("ok"), "Connect a Mac" in (pp.get("say") or "")]

def told(person, role):
    st = agent_status.status(role, person)
    return ["imessage_search" in st["available"], agent_status.render(st, person)]
o_av, o_txt = told("omar", "member"); p_av, p_txt = told("priya", "admin"); op_av, _ = told("", "operator")
out["agent"] = [o_av, "Mac is connected" in o_txt and "cannot send" in o_txt, p_av,
                "isn't connected" in p_txt, op_av]
as_person(None, "operator")
out["operator"] = [c.get("/api/mac/status").status_code, c.post("/api/mac/pair").status_code]
paths = [r.path for r in imessage.router.routes + imessage.broker_router.routes]
out["send_routes"] = [p for p in paths if "send" in p.lower()] + ([] if "/api/mac/messages" in paths else ["?"])

as_person("omar", "member")
dana_before = imessage.message_count("dana")
out["disconnect"] = c.delete(f"/api/mac/devices/{cfg['device_id']}").status_code
out["after_disconnect"] = [imessage.message_count("omar"), imessage.message_count("dana") - dana_before]
out["sync_revoked"] = list(helper("omar", "sync"))
out["post_revoked"] = c.post("/api/mac/messages", json={"messages": []},
                             headers={"Authorization": "Bearer " + cfg["token"]}).status_code
as_person("dana", "owner")
for d in imessage.devices("dana"):
    imessage.disconnect("dana", d["device_id"])
server.should_exit = True
print("RESULT " + json.dumps(out))
'''

ADD = r'''
import pathlib, sys
from datetime import timedelta
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import verify_imessage as v
v.add_messages(v.SCR / sys.argv[1], sys.argv[1], [("text", "One more thing: bring the keys", timedelta(minutes=1))])
'''


def main() -> int:
    for k in ("AUTH_BASE", "APP_PORT"):
        if os.environ.get(k, "").endswith(("8700", "8710")):
            sys.exit("REFUSING: live or testco port")
    canary = "--canary" in sys.argv
    subprocess.run(["rm", "-rf", str(SCR)], check=True)
    SCR.mkdir(parents=True)
    make_mac(SCR / "omar", "omar", [
        ("text", "Can you send me the disclosure? " + WORD, timedelta(hours=2)),
        ("mine", "Sure, sending it tonight", timedelta(hours=1)),
        ("attributed", LONG, timedelta(minutes=30)),
        ("reaction", "Loved \u201cSure, sending it tonight\u201d", timedelta(minutes=20)),
        ("text", "ancient history", timedelta(days=45)),
    ])
    make_mac(SCR / "dana", "dana", [("text", "dana-only: offer accepted", timedelta(hours=3))])
    add = SCR / "add.py"
    add.write_text(ADD.replace("pathlib.Path(__file__).parent", repr(str(pathlib.Path(__file__).parent))))

    base = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC"))}
    env = dict(base)
    env.update({
        "APP_ROOT": str(APP_ROOT), "APP_GOOGLE_TOKEN_KEY": (SB / "token_key").read_text().strip(),
        "HERMES_HOME": str(SB / "hermes"), "JARVIS_APP_ROOT": str(APP_ROOT),
        "JARVIS_SKILLS_ROOT": os.environ.get("VERIFY_SKILLS_ROOT", "/opt/jarvis-verify-fixture/skills/openclaw-imports"),
        "JARVIS_RUNTIME_CONFIG": str(SB / "runtime.json"),
        "JARVIS_IDENTITY_CONFIG": "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json",
        "JARVIS_ACCOUNTS_DB": str(SB / "accounts.db"), "JARVIS_THREADS_DB": str(SB / "threads.db"),
        "JARVIS_TASKS_DB": str(SB / "tasks.db"), "JARVIS_LEADS_DB": str(SB / "leads.db"),
        "JARVIS_ACTIVITY_FEED": str(SB / "feed.jsonl"), "JARVIS_PG_DSN": (SB / "pg_dsn").read_text().strip(),
        "JARVIS_SWITCH_BROKER": "on", "JARVIS_SWITCH_SENTINEL": "on", "JARVIS_SENTINEL_MODE": "enforce",
        "JARVIS_BROWSER": "off", "SCR": str(SCR), "HELPER": str(HELPER), "WORD": WORD, "ADD": str(add),
        "PLUGIN": str(APP_ROOT / "scripts" / "hermes_plugin" / "jarvis" / "run_action.py"),
    })
    if canary:
        env["CANARY"] = "1"
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(APP_ROOT), capture_output=True, text=True,
                       timeout=600)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(p.stdout[-1500:], p.stderr[-2500:])
        ck("child ran", False)
        return 1
    o = json.loads(line[-1][7:])

    print("pairing:")
    ck("a wrong code gets no token", o["claim_wrong"] == 403, o["claim_wrong"])
    ck("an expired code gets no token", o["claim_expired"] == 403, o["claim_expired"])
    ck("presence twin: a fresh code pairs the helper", o["pair_omar"][0] == 0 and "Paired" in o["pair_omar"][1],
       o["pair_omar"])
    ck("a used code cannot pair again", o["claim_reused"] == 403, o["claim_reused"])
    ck("the helper's token file is private (0600)", o["config_mode"] == "0o600", o["config_mode"])
    print("syncing from the Mac (real helper, system python3, fixture chat.db):")
    ck("first sync sends the 3 real texts (no reaction, nothing older than 30 days)",
       o["sync1"][0] == 0 and o["count1"] == 3, [o["sync1"], o["count1"]])
    ck("a second sync sends nothing new", o["sync2"][0] == 0 and "Synced 0" in o["sync2"][1], o["sync2"])
    ck("a new message syncs (+1)", "Synced 1" in o["sync3"][1] and o["count3"] - o["count1"] == 1,
       [o["sync3"], o["count3"]])
    ck("texts are encrypted at rest", o["at_rest"][0] is False)
    ck("presence twin: they decrypt for their person", o["at_rest"][1] is True)
    print("Jarvis reads only the chat turn's person:")
    ck("omar's turn finds his text, with the contact's name, marked as outside content",
       o["read_omar"][0] == 200 and o["read_omar"][2] and o["read_omar"][3] and o["read_omar"][4], o["read_omar"])
    ck("the text that was only in attributedBody came through", o["read_omar"][1] is True, o["read_omar"])
    ck("dana's turn never sees omar's texts", o["read_dana"][0] == 200 and o["read_dana"][1] is False, o["read_dana"])
    ck("presence twin: dana's turn sees her own", o["read_dana"][2] is True, o["read_dana"])
    ck("search by contact name works", bool(o["contact"][1]), o["contact"])
    ck("reactions and old messages never arrive", o["reaction_or_old"] == [False, False], o["reaction_or_old"])
    ck("no running chat turn gets nothing", o["no_turn"] == 403, o["no_turn"])
    ck("someone with no Mac is told none is connected", o["no_mac"][0] == 409
       and o["no_mac"][1].get("error") == "no_mac", o["no_mac"])
    ck("reading texts taints the run (both readers)", o["tainted"][:2] == [True, True], o["tainted"])
    ck("presence twin: a run that read nothing is not tainted", o["tainted"][2] is False, o["tainted"])
    print("the agent's tool and wording:")
    ck("the real plugin tool returns omar's text", o["plugin"][:2] == [True, True], o["plugin"])
    ck("without a Mac it says how to connect one", o["plugin"][2] is False and o["plugin"][3], o["plugin"])
    ck("the agent is told omar's Mac is connected and it cannot send", o["agent"][0] and o["agent"][1], o["agent"])
    ck("priya has the tool but is told no Mac is connected", o["agent"][2] and o["agent"][3], o["agent"])
    ck("the operator has no iMessage tool", o["agent"][4] is False, o["agent"])
    ck("the operator gets no Mac routes", all(x == 403 for x in o["operator"]), o["operator"])
    ck("there is no send route", o["send_routes"] == [], o["send_routes"])
    print("disconnecting:")
    ck("disconnect works", o["disconnect"] == 200, o["disconnect"])
    ck("omar's synced texts are deleted, dana's are untouched", o["after_disconnect"] == [0, 0],
       o["after_disconnect"])
    ck("the helper is refused afterwards", o["sync_revoked"][0] != 0 and "401" in o["sync_revoked"][1],
       o["sync_revoked"])
    ck("the old token is refused", o["post_revoked"] == 401, o["post_revoked"])

    passed = sum(ok)
    print(f"\n{passed}/{len(ok)} passed" + ("  (canary run: the isolation check is expected to FAIL)" if canary else ""))
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
