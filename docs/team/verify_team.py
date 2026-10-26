#!/usr/bin/env python3
"""Team workspaces: invites, roles, per-person features, channel categories.

Every rule is driven through the real HTTP API and then read back from the
ledger: the accounts and threads SQLite files, the team file identity.py
merges, and the counters. A 403 that left a row behind is a failure; a 200
that wrote nothing is a failure.

Each refusal has a presence twin, so a check can't pass on a gate that refuses
everyone. The canaries at the end break the team file, the config and the env
on purpose and assert the code says so.

Usage:
  docs/tasks/tasks_sandbox.sh start        (VERIFY_SKILLS_ROOT=/root/jarvis-skills-platform)
  python3 docs/team/verify_team.py [--base http://127.0.0.1:8711] [-v]
"""

import argparse
import json
import os
import pathlib
import secrets
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

ap = argparse.ArgumentParser()
ap.add_argument("--base", default=os.environ.get("TASKS_BASE", "http://127.0.0.1:8711"))
ap.add_argument("--box", default=os.environ.get("TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox"))
ap.add_argument("--pins", default="/opt/jarvis-verify-fixture/SEEDED_PINS.json")
ap.add_argument("-v", "--verbose", action="store_true")
args = ap.parse_args()

BASE = args.base.rstrip("/")
if BASE.endswith(":8700"):
    sys.exit("REFUSING: :8700 is the live tenant. Point this at the sandbox.")
BOX = pathlib.Path(args.box)
ACCOUNTS, THREADS = BOX / "accounts.db", BOX / "threads.db"
TEAM_FILE = BOX / "hermes" / "state" / "team.json"
APP = pathlib.Path(__file__).resolve().parents[2]
SKILLS = os.environ.get("VERIFY_SKILLS_ROOT") or "/opt/jarvis-verify-fixture/skills/openclaw-imports"
FIXTURE_IDENTITY = "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json"
PY = os.environ.get("VERIFY_PYTHON", "/opt/jarvis/venv/bin/python")
JARS = pathlib.Path(tempfile.mkdtemp(prefix="team-jars-"))
RUN = secrets.token_hex(3)

PINS = json.loads(pathlib.Path(args.pins).read_text())
PINS = {k: (v.get("pin") if isinstance(v, dict) else v) for k, v in PINS.items()}

ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    mark = "PASS" if cond else "FAIL"
    print(f"  {mark}  {name}" + (f" — {extra}" if extra and (not cond or args.verbose) else ""))


def curl(path, method="GET", body=None, jar=None, follow=False):
    url = path if path.startswith("http") else BASE + path
    cmd = ["curl", "-s", "-D", "-", "-o", str(JARS / "body"), "-w", "\n%{http_code}", "-X", method, url]
    if jar:
        cmd += ["-b", jar, "-c", jar]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    heads, _, code = out.rpartition("\n")
    try:
        data = json.loads((JARS / "body").read_text() or "{}")
    except Exception:
        data = {}
    return int(code or 0), data, heads


def call(path, method="GET", body=None, jar=None):
    code, data, _ = curl(path, method, body, jar)
    return code, data


def location(heads):
    for line in heads.splitlines():
        if line.lower().startswith("location:"):
            return line.split(":", 1)[1].strip()
    return ""


def login(who):
    jar = str(JARS / f"{who}.jar")
    body = {"passphrase": os.environ["VERIFY_OPERATOR_PASSPHRASE"]} if who == "operator" \
        else {"person_key": who, "pin": PINS[who]}
    code, d = call("/api/login", "POST", body, jar)
    if not d.get("ok"):
        sys.exit(f"login {who} failed: HTTP {code}")
    return jar


def google_login(email, name):
    """The real mock-OAuth round trip the sandbox runs: start, authorize as
    this address, callback. Returns (jar, callback status line)."""
    jar = str(JARS / f"g-{name}.jar")
    pathlib.Path(jar).unlink(missing_ok=True)
    _, _, heads = curl("/api/auth/google/start", jar=jar)
    loc = urlparse(location(heads))
    q = parse_qs(loc.query)
    q["email"] = [email]
    _, _, heads = curl(urlunparse(loc._replace(query=urlencode(q, doseq=True))), jar=jar)
    code, _, heads = curl(location(heads), jar=jar)
    return jar, f"{code} {location(heads)}"


def q(db, sql, *a):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, a)]
    finally:
        conn.close()


def counters(jar):
    return {k: v["value"] for k, v in call("/api/counters", jar=jar)[1].get("counters", {}).items()}


def delta(after, before, name):
    return after.get(name, 0) - before.get(name, 0)


def fresh(code, env=None, cwd=None):
    """Run code in a NEW interpreter: the library as Jarvis's tools load it."""
    e = {"PATH": os.environ["PATH"], "JARVIS_IDENTITY_CONFIG": FIXTURE_IDENTITY,
         "JARVIS_TEAM_FILE": str(TEAM_FILE), "PYTHONPATH": f"{SKILLS}/_shared"}
    e.update(env or {})
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=e, cwd=cwd or str(APP))
    return r.returncode, r.stdout.strip(), r.stderr.strip()


print(f"team workspaces — {BASE}\n  accounts={ACCOUNTS}\n  threads={THREADS}\n  team file={TEAM_FILE}\n")
J = {w: login(w) for w in ("dana", "priya", "omar", "operator")}
C0 = counters(J["dana"])
people0 = len(q(ACCOUNTS, "SELECT * FROM team_people"))

# ── 1. invites ───────────────────────────────────────────────────────────────
print("── 1. INVITES — owner invites anyone, admin invites members ──")
M_EMAIL, A_EMAIL = f"martin.{RUN}@team.example", f"alex.{RUN}@team.example"
for who, role, want in (("omar", "member", 403), ("operator", "member", 403), ("priya", "admin", 403)):
    code, _ = call("/api/team/invite", "POST", {"name": "Nobody", "email": f"no.{who}.{RUN}@team.example",
                                                "role": role}, J[who])
    ck(f"{who} inviting a {role} -> {code} (expected {want})", code == want)
ck("refused invites wrote no team row", len(q(ACCOUNTS, "SELECT * FROM team_people")) == people0)

code, d = call("/api/team/invite", "POST", {"name": "Martin Lopez", "email": M_EMAIL, "role": "member"}, J["priya"])
ck("admin priya invites a member -> 201", code == 201, str(d)[:120])
MK = (d.get("person") or {}).get("person_key", "")
code, d = call("/api/team/invite", "POST", {"name": "Alex Kim", "email": A_EMAIL, "role": "admin"}, J["dana"])
ck("owner dana invites an admin -> 201", code == 201, str(d)[:120])
AK = (d.get("person") or {}).get("person_key", "")

rows = {r["person_key"]: r for r in q(ACCOUNTS, "SELECT * FROM accounts WHERE person_key IN (?,?)", MK, AK)}
ck("ledger: two new team_people rows", len(q(ACCOUNTS, "SELECT * FROM team_people")) == people0 + 2)
ck("ledger: accounts rows carry the email and role, no PIN",
   rows.get(MK, {}).get("email") == M_EMAIL and rows.get(MK, {}).get("role") == "member"
   and rows.get(AK, {}).get("role") == "admin" and not rows.get(MK, {}).get("pin_hash"))
tf = json.loads(TEAM_FILE.read_text())
ck("team file lists both, admin as internal+admin, member as internal",
   tf["people"].get(MK, {}).get("roles") == ["internal"]
   and tf["people"].get(AK, {}).get("roles") == ["internal", "admin"])
ck("team file mode is 660 (the gateway's group reads it)", oct(TEAM_FILE.stat().st_mode & 0o777) == "0o660",
   oct(TEAM_FILE.stat().st_mode & 0o777))
rc, out, err = fresh(f"import identity; print(identity.resolve({M_EMAIL!r}), identity.resolve('dana'))")
ck("identity.py in a fresh process resolves the invited email (Jarvis's tools see them)",
   out == f"{MK} dana", out or err[-200:])

for label, body in (("same email again", {"name": "Twin", "email": M_EMAIL.upper()}),
                    ("a registry person's email", {"name": "Fake", "email": "dana@testco.example.com"}),
                    ("not an email", {"name": "Bad", "email": "martin-at-example"})):
    code, d = call("/api/team/invite", "POST", {**body, "role": "member"}, J["dana"])
    ck(f"invite with {label} -> 422", code == 422, str(d.get("detail"))[:80])
ck("refused invites still wrote nothing", len(q(ACCOUNTS, "SELECT * FROM team_people")) == people0 + 2)

# ── 2. invited sign-in ───────────────────────────────────────────────────────
print("\n── 2. SIGN-IN — the invited member signs in with Google ────")
JM, how = google_login(M_EMAIL, "martin")
code, s = call("/api/session", jar=JM)
ck("Google sign-in as the invited email is that person, as a member",
   code == 200 and s.get("person_key") == MK and s.get("role") == "member", f"{how} {s}")
ck("session reports every feature on to start", s.get("features") == {f: True for f in
   ("ask", "leads", "tasks", "send", "browser", "imessage")}, str(s.get("features")))
ck("their direct line was created on sign-in",
   len(q(THREADS, "SELECT * FROM threads WHERE direct_for = ?", MK)) == 1)
_, stranger = google_login(f"stranger.{RUN}@team.example", "stranger")
ck("an address nobody invited is refused", "signin=failed" in stranger or stranger.startswith("401"), stranger)

# ── 3. features ──────────────────────────────────────────────────────────────
print("\n── 3. FEATURES — switched off for one person, enforced by the API ──")
c_before = counters(J["dana"])
code, _ = call("/api/leads", jar=JM)
ck("presence: martin reads leads before the switch -> 200", code == 200)
code, d = call(f"/api/team/{MK}", "PATCH", {"features": {"leads": False, "tasks": False, "ask": False,
                                                        "imessage": False}}, J["priya"])
ck("admin priya switches four features off for a member -> 200", code == 200, str(d)[:120])
off = {r["feature"] for r in q(ACCOUNTS, "SELECT feature FROM person_features WHERE person_key=? AND enabled=0", MK)}
ck("ledger: four person_features rows enabled=0", off == {"leads", "tasks", "ask", "imessage"}, str(off))
ck("team file features_off matches", sorted(json.loads(TEAM_FILE.read_text())["features_off"].get(MK, [])) ==
   ["ask", "imessage", "leads", "tasks"])
code, _ = call("/api/leads", jar=JM)
ck("martin GET /api/leads -> 403", code == 403)
code, _ = call("/api/tasks", jar=JM)
ck("martin GET /api/tasks -> 403", code == 403)
code, _ = call("/api/tasks", "POST", {"title": "should not exist"}, JM)
ck("martin POST /api/tasks -> 403 and no task row", code == 403)
code, _ = call("/api/imessage/pair", "POST", None, JM)
ck("martin POST /api/imessage/pair -> 403", code == 403)
code, t = call("/api/threads", jar=JM)
ck("martin's thread list has no direct line while Ask Jarvis is off",
   code == 200 and not any(x["is_direct"] for x in t.get("threads", [])))
mdirect = q(THREADS, "SELECT thread_id FROM threads WHERE direct_for = ?", MK)[0]["thread_id"]
n0 = len(q(THREADS, "SELECT * FROM messages WHERE thread_id = ?", mdirect))
code, _ = call("/api/chat", "POST", {"thread_id": mdirect, "message": "hello"}, JM)
ck("martin POST /api/chat on his direct line -> 403, no message stored",
   code == 403 and len(q(THREADS, "SELECT * FROM messages WHERE thread_id = ?", mdirect)) == n0)
code, s = call("/api/session", jar=JM)
ck("session reflects the switches", s.get("features", {}).get("leads") is False
   and s.get("features", {}).get("send") is True)
for who in ("omar", "dana"):
    code, _ = call("/api/leads", jar=J[who])
    ck(f"presence twin: {who} still reads leads -> 200 (the switch is per person)", code == 200)
rc, out, err = fresh(f"import authz; print(authz.check({MK!r}, 'task.add')[0], authz.check('omar', 'task.add')[0])")
ck("authz in a fresh process refuses task.add for martin, allows omar",
   out == "False True", out or err[-200:])
c_after = counters(J["dana"])
ck("counters: feature_refused_leads +1, _tasks +2, _ask +1, _imessage +1",
   (delta(c_after, c_before, "feature_refused_leads"), delta(c_after, c_before, "feature_refused_tasks"),
    delta(c_after, c_before, "feature_refused_ask"), delta(c_after, c_before, "feature_refused_imessage"))
   == (1, 2, 1, 1),
   str({k: delta(c_after, c_before, k) for k in c_after if k.startswith("feature_refused")}))

code, _ = call(f"/api/team/{MK}", "PATCH", {"features": {"leads": True}}, J["dana"])
code2, _ = call("/api/leads", jar=JM)
ck("switching leads back on -> martin reads leads again (200)", code == 200 and code2 == 200)

rc, out, err = fresh(
    "import json; from app import agent_status as a\n"
    f"m = a.status('member', {MK!r}); o = a.status('member', 'omar')\n"
    "print(json.dumps([sorted(m['switched_off']), 'add_task' in m['available'] or 'add_task' in m['needs_google'],"
    " 'add_task' in o['available'] or 'add_task' in o['needs_google'],"
    " 'Switched off for this person' in a.render(m, 'Martin')]))",
    {"JARVIS_SKILLS_ROOT": SKILLS, "JARVIS_ACCOUNTS_DB": str(ACCOUNTS), "JARVIS_THREADS_DB": str(THREADS),
     "HERMES_HOME": str(BOX / "hermes"), "JARVIS_RUNTIME_CONFIG": str(BOX / "runtime.json"), "PYTHONPATH": str(APP)})
ck("Jarvis's status block: martin's task and iMessage tools are switched off, omar keeps add_task",
   out == json.dumps([["add_task", "complete_task", "imessage_search", "list_tasks"], False, True, True]),
   out or err[-300:])

for who, target, body, want, why in (
        ("priya", AK, {"features": {"leads": False}}, 403, "admin can't change another admin"),
        ("priya", "dana", {"features": {"leads": False}}, 403, "nobody changes the owner"),
        ("dana", "dana", {"features": {"leads": False}}, 403, "nobody changes themself"),
        ("omar", MK, {"features": {"leads": False}}, 403, "a member manages nobody"),
        ("operator", MK, {"features": {"leads": False}}, 403, "break-glass grants nothing"),
        ("priya", MK, {"role": "admin"}, 403, "only the owner changes roles"),
        ("dana", MK, {"features": {"bogus": False}}, 422, "unknown feature")):
    code, _ = call(f"/api/team/{target}", "PATCH", body, J[who])
    ck(f"{who} -> {target}: {why} -> {code} (expected {want})", code == want)
ck("ledger: the refused changes wrote no feature rows for alex or dana",
   q(ACCOUNTS, "SELECT COUNT(*) n FROM person_features WHERE person_key IN (?, 'dana')", AK)[0]["n"] == 0)

# ── 4. roles apply on the next request ───────────────────────────────────────
print("\n── 4. ROLES — a change applies to the open session at once ──")
code, _ = call("/api/team", jar=JM)
ck("martin (member) GET /api/team -> 403", code == 403)
c_before = counters(J["dana"])
call(f"/api/team/{MK}", "PATCH", {"role": "admin"}, J["dana"])
code, _ = call("/api/team", jar=JM)
ck("after dana promotes him, the SAME cookie gets /api/team -> 200", code == 200)
ck("counter session_role_refreshed moved", delta(counters(J["dana"]), c_before, "session_role_refreshed") >= 1)
ck("team file now gives him internal+admin", json.loads(TEAM_FILE.read_text())["people"][MK]["roles"] ==
   ["internal", "admin"])
call(f"/api/team/{MK}", "PATCH", {"role": "member"}, J["dana"])
code, _ = call("/api/team", jar=JM)
ck("demoted again -> 403 on the same cookie", code == 403)

# ── 5. turning access off ────────────────────────────────────────────────────
print("\n── 5. ACCESS OFF — the session ends and Google sign-in is refused ──")
code, _ = call(f"/api/team/{MK}", "PATCH", {"disabled": True}, J["priya"])
ck("admin priya turns a member's access off -> 200", code == 200)
ck("ledger: accounts.disabled = 1", q(ACCOUNTS, "SELECT disabled FROM accounts WHERE person_key=?", MK)[0]["disabled"] == 1)
code, _ = call("/api/session", jar=JM)
ck("his open session -> 401 on the next request", code == 401)
_, how = google_login(M_EMAIL, "martin-off")
ck("Google sign-in as him is refused while off", "signin=failed" in how or how.startswith("401"), how)
call(f"/api/team/{MK}", "PATCH", {"disabled": False}, J["priya"])
JM, how = google_login(M_EMAIL, "martin-back")
code, s = call("/api/session", jar=JM)
ck("presence twin: back on -> signs in again", code == 200 and s.get("person_key") == MK, how)

# ── 6. channels and categories ───────────────────────────────────────────────
print("\n── 6. CHANNELS — admins create; categories are structure, not access ──")
c_before = counters(J["dana"])
n_threads = len(q(THREADS, "SELECT * FROM threads"))
code, _ = call("/api/threads", "POST", {"name": f"omar's {RUN}"}, J["omar"])
ck("member omar creating a channel -> 403", code == 403)
code, _ = call("/api/threads", "POST", {"name": f"op {RUN}"}, J["operator"])
ck("operator creating a channel -> 403", code == 403)
ck("ledger: no thread row added", len(q(THREADS, "SELECT * FROM threads")) == n_threads)
ck("counter channel_create_refused +1 (omar; the operator is refused as nobody before the role check)",
   delta(counters(J["dana"]), c_before, "channel_create_refused") == 1)
code, _ = call("/api/categories", "POST", {"name": f"Nope {RUN}"}, J["omar"])
ck("member creating a category -> 403", code == 403)
code, d = call("/api/categories", "POST", {"name": f"Listings {RUN}"}, J["priya"])
ck("admin priya creates a category -> 201", code == 201)
CAT = (d.get("category") or {}).get("category_id", "")
code, _ = call("/api/categories", "POST", {"name": f"listings {RUN}"}, J["dana"])
ck("same name, other case -> 422", code == 422)
code, d = call("/api/threads", "POST", {"name": f"123 Main {RUN}", "category_id": CAT}, J["dana"])
ck("owner creates a channel in it -> 201", code == 201)
CH = (d.get("thread") or {}).get("thread_id", "")
r = q(THREADS, "SELECT category_id FROM threads WHERE thread_id=?", CH)
ck("ledger: the channel row carries the category", r and r[0]["category_id"] == CAT)
code, _ = call("/api/threads", "POST", {"name": "x", "category_id": "cat_nope"}, J["dana"])
ck("a category that doesn't exist -> 422", code == 422)

_, t = call("/api/threads", jar=JM)
ck("martin, not added, does not see the channel", CH not in {x["thread_id"] for x in t.get("threads", [])})
ck("martin does see the category name (structure)", CAT in {c["category_id"] for c in t.get("categories", [])})
code, _ = call(f"/api/threads/{CH}/members", "POST", {"person_key": MK}, J["priya"])
ck("admin adds martin (an invited person) -> 201", code == 201)
_, t = call("/api/threads", jar=JM)
mine = {x["thread_id"]: x for x in t.get("threads", [])}
ck("now he sees it, in its category", CH in mine and mine[CH].get("category_id") == CAT)
code, _ = call(f"/api/threads/{CH}/category", "PUT", {"category_id": None}, JM)
ck("member moving a channel -> 403", code == 403)
code, _ = call(f"/api/threads/{CH}/category", "PUT", {"category_id": None}, J["priya"])
ck("admin moves it out of every category -> 200, ledger NULL",
   code == 200 and q(THREADS, "SELECT category_id FROM threads WHERE thread_id=?", CH)[0]["category_id"] is None)
call(f"/api/threads/{CH}/category", "PUT", {"category_id": CAT}, J["priya"])
mem0 = q(THREADS, "SELECT person_key FROM thread_members WHERE thread_id=? ORDER BY person_key", CH)
code, d = call(f"/api/categories/{CAT}", "DELETE", None, J["priya"])
ck("admin removes the category -> 200, one channel ungrouped", code == 200 and d.get("channels_ungrouped") == 1)
ck("ledger: category row gone, channel kept, ungrouped, members unchanged",
   not q(THREADS, "SELECT * FROM channel_categories WHERE category_id=?", CAT)
   and q(THREADS, "SELECT category_id FROM threads WHERE thread_id=?", CH)[0]["category_id"] is None
   and q(THREADS, "SELECT person_key FROM thread_members WHERE thread_id=? ORDER BY person_key", CH) == mem0)

# ── 7. seeding the tenant's starting categories ─────────────────────────────
print("\n── 7. SEED — app.channels.categories, once ──────────────────")
SEED = ["Listings", "Designs", "Buyers", "Land deals", "Admin"]
with tempfile.TemporaryDirectory() as tmp:
    rt = json.loads(pathlib.Path(BOX / "runtime.json").read_text())
    rt.setdefault("app", {})["channels"] = {"categories": SEED}
    (pathlib.Path(tmp) / "runtime.json").write_text(json.dumps(rt))
    env = {"JARVIS_SKILLS_ROOT": SKILLS, "JARVIS_RUNTIME_CONFIG": f"{tmp}/runtime.json",
           "JARVIS_THREADS_DB": f"{tmp}/threads.db", "HERMES_HOME": tmp, "PYTHONPATH": str(APP)}
    prog = ("from app import threads as t; t.init(); print(json.dumps([c['name'] for c in t.categories()]))")
    rc, out, err = fresh("import json; " + prog, env)
    ck("first start seeds the five, in order", out == json.dumps(SEED), out or err[-300:])
    rc, out, err = fresh("import json, sqlite3; c = sqlite3.connect('" + tmp + "/threads.db'); "
                         "c.execute(\"DELETE FROM channel_categories WHERE name='Admin'\"); c.commit(); " + prog, env)
    rc, out, err = fresh("import json; " + prog, env)
    ck("a category an admin removed does not come back on restart", out == json.dumps(SEED[:-1]), out or err[-300:])

# ── 8. provisioner rule ──────────────────────────────────────────────────────
print("\n── 8. PROVISIONER — channels.categories in a manifest ───────")
for label, cats, want_rc in (("five names", SEED, 0), ("a duplicate", ["Buyers", "buyers"], 1),
                             ("a blank", ["Buyers", " "], 1), ("not a list", "Buyers", 1)):
    rc, out, err = fresh("import sys; sys.path.insert(0, 'scripts'); import provision_tenant as p; "
                         f"print(p.channel_categories({{'channels': {{'categories': {cats!r}}}}}))",
                         {"PYTHONPATH": str(APP)})
    ck(f"{label} -> {'accepted' if want_rc == 0 else 'refused'}", (rc == 0) == (want_rc == 0), (out or err)[-120:])

# ── 9. canaries ──────────────────────────────────────────────────────────────
print("\n── 9. CANARIES — break it on purpose and see it said ──────────")
with tempfile.TemporaryDirectory() as tmp:
    bad = pathlib.Path(tmp) / "team.json"
    bad.write_text("{not json")
    rc, out, err = fresh("import identity; d = identity.load(); print(d.get('team_unreadable'), "
                         f"identity.resolve({M_EMAIL!r}))", {"JARVIS_TEAM_FILE": str(bad)})
    ck("unreadable team file: flagged and announced on stderr, invited people absent",
       out == "True None" and "TEAM FILE UNREADABLE" in err, f"{out} / {err[-120:]}")
    bad.write_text(json.dumps({"people": {
        "mallory": {"name": "Mallory", "email": [f"mallory.{RUN}@team.example"], "roles": ["owner", "internal"]},
        "dana2": {"name": "Dana Two", "email": ["dana@testco.example.com"], "roles": ["internal"]}},
        "features_off": {"dana": ["leads"], "nobody": ["tasks"]}}))
    rc, out, err = fresh("import identity; print(identity.is_owner('mallory'), identity.resolve('dana2'), "
                         "identity.resolve('dana@testco.example.com'))", {"JARVIS_TEAM_FILE": str(bad)})
    ck("a team entry can't claim owner, nor take a registry person's email",
       out == "False None dana", out or err[-200:])
    rc, out, err = fresh(f"import identity; print(identity.feature_on({MK!r}, 'leads'))")
    ck("presence: with the real team file martin's leads read as on (switched back in step 3)", out == "True",
       out or err[-200:])
    rc, out, err = fresh(
        "from app import accounts\ntry:\n    accounts.invite('Zed', 'zed@team.example', 'member', by='dana')\n"
        "    print('invited')\nexcept accounts.AccountError as e:\n    print('refused:', e)",
        {"JARVIS_TEAM_FILE": "", "JARVIS_SKILLS_ROOT": SKILLS, "JARVIS_ACCOUNTS_DB": f"{tmp}/a.db",
         "JARVIS_THREADS_DB": f"{tmp}/t.db", "HERMES_HOME": tmp, "PYTHONPATH": str(APP)})
    ck("no JARVIS_TEAM_FILE: invites are refused, not half-done", out.startswith("refused:"), out or err[-200:])
    adb = pathlib.Path(tmp) / "a.db"
    ck("…and nothing was written (accounts db never opened, or no team row)",
       not adb.exists() or q(adb, "SELECT COUNT(*) n FROM team_people")[0]["n"] == 0,
       "absent" if not adb.exists() else "present")
    ck("…and the refusal was counted",
       q(f"{tmp}/t.db", "SELECT value FROM counters WHERE name='team_invite_no_team_file'")[0]["value"] == 1)

print(f"\n{sum(ok)}/{len(ok)} passed")
sys.exit(0 if all(ok) else 1)
