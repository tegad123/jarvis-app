#!/usr/bin/env python3
"""MLS search (docs/plans/browser-har-comps.md, steps 11 onward).

Runs against the verify sandbox's databases (docs/tasks/tasks_sandbox.sh,
started first; TASKS_SANDBOX names it) in a child process with a stand-in
tenant_config, so app.mls and app.browser are read exactly as the app reads
them. No browser, no network, never :8700 or :8710.

Step 11, availability and settings:
- each condition of plan section 7 off in turn gives its own `why` and moves
  its own counter by exactly one; all on gives available and moves none
  (the presence twin); count=False moves nothing;
- an invalid app.mls reads as config_invalid with mls_config_invalid +1, and
  the provisioner refuses the same block; out-of-range caps, an http or
  off-site start_url, unknown keys and a start_url on a headless or disabled
  browser are refused; a valid block renders with its defaults;
- there is no MLS person feature: accounts.FEATURES is unchanged.

Step 12 (fake reader, a fixed clock): the page budget, the daily request and
page-load caps across the tenant's local midnight, the 100-row and tenant
row caps, the re-check filter, parameter refusals, hand-worked summaries, the
cooldown, one run at a time, stale runs, retention and run scoping.

Step 13 (a real headless worker and egress, docs/mls/fixture_site.py on
loopback): every fixture is opened and read through browser.gate() with the
`tables` request, one ledger row each; each page gets its class; table, card
and embedded-data pages parse to the same hand-worked rows; agent contact
columns never reach a Listing; the search form's fields and boxes are found
and the read rule treats only status and type boxes as filters.

Step 14 (a real headless worker, egress and the fixture site, the broker
route through FastAPI's TestClient with real actor refs): without a read
allowance the run stops after one page and files one read card; with one,
Heights/active reads two pages, drops the other-status rows and records one
executed ledger row per load and gesture under the allowance; the form gets
only the filters asked for; comps get a suggested range; a filter the form
lacks, login, captcha, two-factor, ambiguous, unknown, empty and 429 pages
each end the run their own way before anything more is loaded; a shared
channel, no place, Take over and a busy tenant are refused with no run; the
tool shows in the person's status only when the browser is theirs and the
start page is set; the pauses are real.

Step 15 (a fake reader behind the real broker route, main.app with signed-in
sessions): a search asks for its card on its own turn and a refused one does
not; GET /api/mls/runs/{id} gives the run to its person and the same 404 to
the owner, an admin, another member, the operator and a missing run; the
card saved under the reply survives a reload for that person only; a
stopped run's card says why; past keep_days it says the results expired and
the stored rows are gone.

Step 17 (a real headless worker): removing a person closes their browser,
deletes their profile directory, connected sites, MLS runs and browser
allowances, and none of anyone else's; deleting the tenant removes every
profile; with the worker down both say they failed and are counted; with
the browser off both say browser_off.

--canary breaks six things on purpose; these checks must FAIL: the
person's browser feature is ignored (browser_feature_off), the re-check
keeps every row (three re-check checks), an unknown page reads as empty
(two classify checks), and every thread reads as the person's direct line
(the shared-channel check, and the no-run checks after it, which then see
the run it let through; mls_result for another person, read off their
channel, is refused direct_only instead of 404).
Step 15: a run is read for any person (the card's 404s for the owner,
admin, another member and the operator, and the card checks that depend on
the person).
Step 17: removing a person only closes their browser (the profile-deleted
check).
"""
import json
import os
import pathlib
import secrets
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SB = pathlib.Path(os.environ.get("TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox"))
HEADLESS = "/root/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell"
CANARY = "--canary" in sys.argv

ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({extra})" if extra and not cond else ""))


CHILD = r'''
import json, os, sys, types
sys.path.insert(0, os.environ["APP_ROOT"])
CFG = {}
tc = types.ModuleType("tenant_config")
def _get(key, default=None):
    cur = CFG
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
tc.get = _get
sys.modules["tenant_config"] = tc
from app import accounts, mls, threads
if os.environ.get("CANARY") == "1":
    accounts.feature_on = lambda person, feature: True
GOOD = {"browser": {"mode": "headed"}, "mls": {"site": "matrix.example", "start_url": "https://matrix.example/search"}}
def count(n):
    return threads.bump_counter(n, 0)
def why_with(cfg, person="omar", env_on=True):
    CFG.clear(); CFG["app"] = json.loads(json.dumps(cfg))
    if env_on:
        os.environ["JARVIS_BROWSER"] = "on"
    else:
        os.environ["JARVIS_BROWSER"] = "off"
    return mls.available_for(person)
WHYS = list(mls.UNAVAILABLE)
out = {"features": list(accounts.FEATURES), "cases": {}}
was_on = accounts.feature_on("omar", "browser")
accounts.set_feature("omar", "browser", True, by="dana")
cases = [
    ("browser_off", GOOD, "omar", False),
    ("not_headed", {**GOOD, "browser": {"mode": "headless"}}, "omar", True),
    ("not_headed_missing", {"mls": GOOD["mls"]}, "omar", True),
    ("config_invalid", {**GOOD, "mls": {**GOOD["mls"], "max_results": 500}}, "omar", True),
    ("not_configured", {**GOOD, "mls": {"site": "matrix.example"}}, "omar", True),
    ("not_a_person", GOOD, None, True),
    ("available", GOOD, "omar", True),
]
for name, cfg, person, env_on in cases:
    before = {w: count(f"mls_refused_{w}") for w in WHYS}
    inv0 = count("mls_config_invalid")
    why = why_with(cfg, person, env_on)
    after = {w: count(f"mls_refused_{w}") for w in WHYS}
    out["cases"][name] = {"why": why, "delta": {w: after[w] - before[w] for w in WHYS if after[w] != before[w]},
                          "invalid_delta": count("mls_config_invalid") - inv0}
accounts.set_feature("omar", "browser", False, by="dana")
before = count("mls_refused_browser_feature_off")
out["cases"]["browser_feature_off"] = {"why": why_with(GOOD), "delta": {
    "browser_feature_off": count("mls_refused_browser_feature_off") - before}}
accounts.set_feature("omar", "browser", True, by="dana")
before = {w: count(f"mls_refused_{w}") for w in WHYS}
CFG.clear(); CFG["app"] = json.loads(json.dumps(GOOD))
out["quiet_why"] = mls.available_for("omar", count=False)
out["quiet_delta"] = sum(count(f"mls_refused_{w}") - before[w] for w in WHYS)
accounts.set_feature("omar", "browser", was_on, by="dana")
out["restored"] = accounts.feature_on("omar", "browser") == was_on
print("RESULT " + json.dumps(out))
'''


CHILD12 = r'''
import json, os, sys, types
from datetime import date, datetime, timedelta, timezone
sys.path.insert(0, os.environ["APP_ROOT"])
tc = types.ModuleType("tenant_config")
tc.get = lambda key, default=None: default
tc.timezone_name = lambda: "America/Chicago"
sys.modules["tenant_config"] = tc
from app import mls, mls_limits, threads
if os.environ.get("CANARY") == "1":
    mls.recheck = lambda q, rows: (list(rows), 0, 0)
L = mls.Listing
def count(n):
    return threads.bump_counter(n, 0)
def rows_of(who):
    return mls._db().execute("SELECT COUNT(*) FROM mls_runs WHERE person_key = ?", (who,)).fetchone()[0]
conn = mls._db()
with conn:
    conn.execute("DELETE FROM mls_runs WHERE person_key IN ('omar', 'dana', 'verify-mls')")
    conn.execute("DELETE FROM mls_runs WHERE state = 'running'")
S = dict(mls_limits.DEFAULTS, start_url="https://matrix.example/s", site="matrix.example")
T0 = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)        # 12:00 in Chicago
clock = [T0]
mls._now = lambda: clock[0]
TODAY = date(2026, 11, 12)
out = {}

def fake(rows=(), state="done", loads=1, **kw):
    def reader(q, budget, ctx):
        for _ in range(loads):
            budget.draw()
        return mls.Result(state, listings=list(rows), **kw)
    return reader

def search(who, params, reader, s=S):
    q = mls.parse_query(params, TODAY, s["max_results"])
    try:
        return mls.run_search(who, "t-" + who, q, reader, s)
    except mls.Refused as r:
        return {"refused": r.error, "status": r.status, **r.extra}

HEIGHTS = {"area": "Heights"}
# Budget
b = mls.Budget(10); c0 = count("mls_budget_exhausted"); drawn = 0
try:
    for _ in range(11):
        b.draw(); drawn += 1
except mls.BudgetExhausted:
    pass
out["budget"] = {"drawn": drawn, "delta": count("mls_budget_exhausted") - c0}
r = search("omar", HEIGHTS, fake(loads=11))
out["overrun"] = {"state": r["state"], "page_loads": r["page_loads"]}
# Daily request cap: one run used above, four more allowed, the sixth refused.
with conn:
    conn.execute("DELETE FROM mls_runs WHERE person_key = 'omar'")
got = [search("omar", HEIGHTS, fake()).get("state") for _ in range(5)]
c0, n0 = count("mls_refused_daily_cap"), rows_of("omar")
sixth = search("omar", HEIGHTS, fake())
out["daily"] = {"five": got, "sixth": sixth, "cap_delta": count("mls_refused_daily_cap") - c0,
                "row_delta": rows_of("omar") - n0}
clock[0] = T0 + timedelta(hours=11)       # 23:00 in Chicago, same local day
out["same_day_late"] = search("omar", HEIGHTS, fake()).get("refused")
clock[0] = T0 + timedelta(hours=12, minutes=30)   # 00:30 the next local day
out["next_day"] = search("omar", HEIGHTS, fake()).get("state")
clock[0] = T0
# Daily page-load cap and the budget it leaves
s2 = dict(S, max_page_loads_per_person_per_day=12)
with conn:
    conn.execute("DELETE FROM mls_runs WHERE person_key = 'dana'")
first = search("dana", HEIGHTS, fake(loads=10), s2)
seen = {}
def peek(q, budget, ctx):
    seen["limit"] = budget.limit
    return mls.Result("done")
second = search("dana", HEIGHTS, peek, s2)
third_c0 = count("mls_refused_daily_cap")
search("dana", HEIGHTS, fake(loads=2), s2)
third = search("dana", HEIGHTS, fake(), s2)
out["pages"] = {"first": first["page_loads"], "second_budget": seen.get("limit"), "third": third,
                "cap_delta": count("mls_refused_daily_cap") - third_c0}
# The 100-row cap
with conn:
    conn.execute("DELETE FROM mls_runs WHERE person_key = 'verify-mls'")
many = [L(address=f"{i} Oak St", status="active", list_price=300000 + i) for i in range(150)]
r = search("verify-mls", {**HEIGHTS, "max_results": 100}, fake(many), dict(S, max_results=100))
out["cap100"] = {"count": r["count"], "truncated": r["truncated"], "rows": len(r["listings"])}
s3 = dict(S, max_results=10)
r = search("verify-mls", {**HEIGHTS, "max_results": 100}, fake(many), s3)
out["cap_tenant"] = {"count": r["count"], "truncated": r["truncated"]}
r = search("verify-mls", HEIGHTS, fake(many[:20]))
out["cap_default"] = {"count": r["count"], "truncated": r["truncated"]}
# The re-check
q = {"area": "Heights", "statuses": ["active"], "price_min": 400000, "price_max": 600000, "sqft_min": 1500,
     "date_from": "2026-06-01"}
good = L(address="1 Good St", status="active", list_price=500000, sqft=1800, list_date="2026-09-01")
rows = [good,
        L(address="2 Pending St", status="pending", list_price=500000, sqft=1800, pending_date="2026-09-01"),
        L(address="3 Dear St", status="active", list_price=900000, sqft=1800, list_date="2026-09-01"),
        L(address="4 Old St", status="active", list_price=500000, sqft=1800, list_date="2026-01-02"),
        L(address="5 Nosize St", status="active", list_price=500000, sqft=None, list_date="2026-09-01")]
d0, u0 = count("mls_rows_dropped"), count("mls_rows_unverified")
with conn:
    conn.execute("DELETE FROM mls_runs WHERE person_key = 'verify-mls'")
r = search("verify-mls", q, fake(rows))
out["recheck"] = {"kept": [x["address"] for x in r["listings"]], "dropped": r["dropped"],
                  "unverified": r["unverified"], "dropped_delta": count("mls_rows_dropped") - d0,
                  "unverified_delta": count("mls_rows_unverified") - u0}
sold_q = {"statuses": ["sold"], "address": "10 Subject Ln", "date_from": "2026-05-12", "subject_sqft": 2000}
r = search("verify-mls", sold_q, fake([
    L(address="a", status="sold", sold_price=400000, sqft=2000, sold_date="2026-08-01", distance_miles=0.5),
    L(address="far", status="sold", sold_price=400000, sqft=2000, sold_date="2026-08-01", distance_miles=3.0),
    L(address="active", status="active", list_price=400000, sqft=2000, list_date="2026-08-01")]))
out["recheck_sold"] = {"kept": [x["address"] for x in r["listings"]], "dropped": r["dropped"]}
# Parameters
bad = {}
for label, p in [("no place", {"statuses": ["sold"]}), ("bad status", {**HEIGHTS, "statuses": ["leased"]}),
                 ("repeat status", {**HEIGHTS, "statuses": ["sold", "sold"]}),
                 ("radius without address", {**HEIGHTS, "radius_miles": 1}),
                 ("radius too big", {"address": "1 Main St", "radius_miles": 9}),
                 ("min above max", {**HEIGHTS, "price_min": 5, "price_max": 4}),
                 ("future date", {**HEIGHTS, "date_from": "2026-12-01"}),
                 ("too far back", {**HEIGHTS, "date_from": "2023-01-01"}),
                 ("from after to", {**HEIGHTS, "date_from": "2026-10-01", "date_to": "2026-09-01"}),
                 ("bad zip", {"zip_codes": ["7700"]}), ("eleven zips", {"zip_codes": [str(77000 + i) for i in range(11)]}),
                 ("unknown parameter", {**HEIGHTS, "agent_phone": "x"}),
                 ("max_results 101", {**HEIGHTS, "max_results": 101}),
                 ("bool as number", {**HEIGHTS, "beds_min": True}),
                 ("bad type", {**HEIGHTS, "property_types": ["castle"]})]:
    try:
        mls.parse_query(p, TODAY); bad[label] = None
    except mls.BadRequest as e:
        bad[label] = str(e)
out["bad"] = bad
ok_q = mls.parse_query({"zip_codes": ["77008"], "statuses": ["pending"], "beds_min": 3, "price_max": 600000,
                        "property_types": ["single_family"], "date_from": "2026-11-01"}, TODAY)
out["good_q"] = ok_q.given()
out["addr_q"] = mls.parse_query({"address": "123 Main St"}, TODAY).given()
# Summary, hand-worked
act = [L(address=str(i), status="active", list_price=p, sqft=2000, days_on_market=d)
       for i, (p, d) in enumerate([(400000, 10), (500000, 20), (600000, 30), (700000, 40)])]
out["sum_active"] = mls.summarize(mls.parse_query(HEIGHTS, TODAY), act)
sold = [L(address=str(i), status="sold", sold_price=v * 2000, sqft=2000) for i, v in enumerate([200, 210, 220, 230, 240])]
out["sum_sold"] = mls.summarize(mls.parse_query(sold_q, TODAY), sold)
out["sum_few"] = mls.summarize(mls.parse_query(sold_q, TODAY), sold[:2])
# Cooldown after a block
with conn:
    conn.execute("DELETE FROM mls_runs WHERE person_key = 'verify-mls'")
blk = search("verify-mls", HEIGHTS, fake(state="blocked", stop_kind="blocked", detail="x"))
c0 = count("mls_refused_cooldown")
cool = search("verify-mls", HEIGHTS, fake())
clock[0] = T0 + timedelta(hours=24, minutes=1)
after = search("verify-mls", HEIGHTS, fake())
clock[0] = T0
out["cooldown"] = {"blocked": blk["state"], "kind": blk.get("kind"), "then": cool.get("refused"),
                   "delta": count("mls_refused_cooldown") - c0, "after": after.get("state")}
# One run at a time; a stale one is failed and announced
with conn:
    conn.execute("DELETE FROM mls_runs WHERE person_key = 'verify-mls'")
    conn.execute("INSERT INTO mls_runs (person_key, thread_id, query_json, state, started_at) "
                 "VALUES ('dana', 't', '{}', 'running', ?)", ((T0 - timedelta(minutes=2)).isoformat(),))
c0 = count("mls_refused_busy")
busy = search("verify-mls", HEIGHTS, fake())
out["busy"] = {"refused": busy.get("refused"), "delta": count("mls_refused_busy") - c0}
clock[0] = T0 + timedelta(minutes=9)
s0 = count("mls_stale_failed")
fresh = search("verify-mls", HEIGHTS, fake())
out["stale"] = {"then": fresh.get("state"), "delta": count("mls_stale_failed") - s0}
clock[0] = T0
# Retention
with conn:
    conn.execute("DELETE FROM mls_runs WHERE person_key IN ('verify-mls', 'dana')")
    conn.execute("INSERT INTO mls_runs (person_key, thread_id, query_json, state, result_json, started_at, finished_at) "
                 "VALUES ('verify-mls', 't', '{}', 'done', '{\"x\":1}', ?, ?)",
                 ((T0 - timedelta(days=31)).isoformat(),) * 2)
    old_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO mls_runs (person_key, thread_id, query_json, state, result_json, started_at, finished_at) "
                 "VALUES ('verify-mls', 't', '{}', 'done', '{\"x\":2}', ?, ?)",
                 ((T0 - timedelta(days=29)).isoformat(),) * 2)
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
c0 = count("mls_results_cleared")
search("verify-mls", HEIGHTS, fake())
out["retention"] = {"old": mls.run_row(old_id, "verify-mls")["result_json"],
                    "new": mls.run_row(new_id, "verify-mls")["result_json"],
                    "delta": count("mls_results_cleared") - c0,
                    "old_row_kept": mls.run_row(old_id, "verify-mls") is not None}
out["scoping"] = {"owner": mls.run_row(new_id, "verify-mls") is not None, "other": mls.run_row(new_id, "omar")}
print("RESULT " + json.dumps(out, default=str))
with conn:
    conn.execute("DELETE FROM mls_runs WHERE person_key IN ('omar', 'dana', 'verify-mls')")
'''


def step12(env) -> None:
    print("── step 12: shapes, caps, runs, summary")
    p = subprocess.run([sys.executable, "-c", CHILD12], env=env, cwd=str(ROOT), capture_output=True, text=True,
                       timeout=120)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(p.stderr[-3000:])
        ck("step 12 child ran", False)
        return
    o = json.loads(line[-1][7:])
    ck("budget: ten draws allowed, the eleventh raises and is counted",
       o["budget"] == {"drawn": 10, "delta": 1}, o["budget"])
    ck("a reader that tries an eleventh load ends failed with 10 page loads recorded",
       o["overrun"] == {"state": "failed", "page_loads": 10}, o["overrun"])
    d = o["daily"]
    ck("daily: five searches run", d["five"] == ["done"] * 5, d)
    ck("daily: the sixth is refused 429 daily_cap with a reset time, counted, and writes no row",
       d["sixth"].get("refused") == "daily_cap" and d["sixth"].get("status") == 429 and d["sixth"].get("resets_at")
       and d["cap_delta"] == 1 and d["row_delta"] == 0, d)
    ck("daily: still refused at 23:00 the same local day", o["same_day_late"] == "daily_cap", o["same_day_late"])
    ck("daily: allowed again just after local midnight", o["next_day"] == "done", o["next_day"])
    pg = o["pages"]
    ck("page cap: a later run's budget is what's left of the day (12 - 10 = 2)",
       pg["first"] == 10 and pg["second_budget"] == 2, pg)
    ck("page cap: once the day's page loads are used, refused daily_cap and counted",
       pg["third"].get("refused") == "daily_cap" and pg["cap_delta"] == 1, pg)
    ck("100-row cap: 150 offered, 100 returned, truncated",
       o["cap100"] == {"count": 100, "truncated": True, "rows": 100}, o["cap100"])
    ck("tenant max_results 10 caps an asked-for 100", o["cap_tenant"] == {"count": 10, "truncated": True},
       o["cap_tenant"])
    ck("default 25: 20 offered, 20 returned, not truncated (presence twin)",
       o["cap_default"] == {"count": 20, "truncated": False}, o["cap_default"])
    rc = o["recheck"]
    ck("re-check drops wrong status, over price and before the date for its status",
       rc["dropped"] == 3 and rc["dropped_delta"] == 3, rc)
    ck("re-check keeps the matching row and the one missing sqft, marked unverified",
       rc["kept"] == ["1 Good St", "5 Nosize St"] and rc["unverified"] == 1 and rc["unverified_delta"] == 1, rc)
    ck("re-check on a comps search drops the row outside the radius and the active one",
       o["recheck_sold"] == {"kept": ["a"], "dropped": 2}, o["recheck_sold"])
    bad = o["bad"]
    for label, msg in bad.items():
        ck(f"parameters refused: {label}", msg is not None, msg)
    ck("parameters: a natural-language-shaped search parses to exactly the given filters",
       o["good_q"] == {"statuses": ["pending"], "zip_codes": ["77008"], "beds_min": 3, "price_max": 600000,
                       "property_types": ["single_family"], "date_from": "2026-11-01"}, o["good_q"])
    ck("parameters: an address alone defaults to a 1-mile radius, active",
       o["addr_q"] == {"statuses": ["active"], "address": "123 Main St", "radius_miles": 1.0}, o["addr_q"])
    sa = o["sum_active"]
    ck("summary (active), hand-worked: median 550000, quartiles 475000-625000, $275/sqft, 25 days",
       sa.get("median_price") == 550000 and sa.get("price_low") == 475000 and sa.get("price_high") == 625000
       and sa.get("median_price_per_sqft") == 275 and sa.get("median_days_on_market") == 25
       and sa["by_status"] == {"active": 4} and "list prices" in sa["note"] and "suggested_low" not in sa, sa)
    ss = o["sum_sold"]
    ck("summary (sold comps), hand-worked: $210-$230/sqft x 2000 = 420000-460000, not an appraisal",
       ss.get("suggested_low") == 420000 and ss.get("suggested_high") == 460000
       and "Not an appraisal" in ss["note"] and "sold prices" in ss["note"], ss)
    ck("summary: fewer than three sales gives no suggested range and says so",
       "suggested_low" not in o["sum_few"] and "Too few" in o["sum_few"]["note"], o["sum_few"])
    cd = o["cooldown"]
    ck("cooldown: a block ends the run blocked; the next search is refused cooling_off and counted",
       cd["blocked"] == "blocked" and cd["kind"] == "blocked" and cd["then"] == "cooling_off" and cd["delta"] == 1, cd)
    ck("cooldown: allowed again after 24 hours (presence twin)", cd["after"] == "done", cd)
    ck("busy: a run going anywhere on the tenant refuses another, counted",
       o["busy"] == {"refused": "busy", "delta": 1}, o["busy"])
    ck("stale: a run still running after ten minutes is failed, counted, and the next search runs",
       o["stale"] == {"then": "done", "delta": 1}, o["stale"])
    rt = o["retention"]
    ck("retention: results older than 30 days are cleared, the row kept, counted",
       rt["old"] is None and rt["old_row_kept"] and rt["delta"] == 1, rt)
    ck("retention: results 29 days old are kept (presence twin)", rt["new"] == '{"x":2}', rt)
    ck("a run is readable by the person who started it and nobody else",
       o["scoping"] == {"owner": True, "other": None}, o["scoping"])


CHILD13 = r'''
import json, os, re, sys
sys.path.insert(0, os.environ["APP_ROOT"])
from app import browser, browser_policy as bp, mls_matrix as mm, platform_db, threads
if os.environ.get("CANARY") == "1":
    real = mm.classify
    mm.classify = lambda p: ("empty", {}) if real(p)[0] == "unknown" else real(p)
SITE = os.environ["SITE"]
K = SITE.split("//")[1].split(":")[0]
ctx = {"run": "verify-mls-13-" + os.urandom(4).hex(), "session": "", "task": "", "skill": "jarvis",
       "source": "verify:mls"}
browser.call("close", "omar")
out = {"pages": {}}
def ledger(op):
    with platform_db.connect() as c:
        return c.execute("SELECT count(*) AS n FROM actions WHERE run_id=%s AND method=%s AND outcome='executed'",
                         (ctx["run"], op)).fetchone()["n"]
st, body = browser.gate("tables", {}, ctx, "omar")
out["tables_nothing_open"] = [st, body.get("error")]
for name in ("search.html", "results_table.html", "results_table_p2.html", "results_cards.html", "results_json.html",
             "no_results.html", "ambiguous.html", "login.html", "captcha.html", "two_factor.html", "too_many.html",
             "unknown.html"):
    st, _ = browser.gate("open", {"url": SITE + "/f/" + name}, ctx, "omar")
    st2, page = browser.gate("tables", {"snapshot": True}, ctx, "omar")
    cls, det = mm.classify(page)
    rows = [r.__dict__ for r in det.get("rows", [])]
    out["pages"][name] = {"open": st, "tables": st2, "class": cls, "rows": rows, "choices": det.get("choices"),
                          "keys": sorted(page), "fields": sorted(mm.search_fields(page.get("snapshot") or ""))}
    if name == "search.html":
        snap = page.get("snapshot") or ""
        sb, tb = mm.boxes(snap, mm.STATUS_BOXES), mm.boxes(snap, mm.TYPE_BOXES)
        out["boxes"] = {"status": sorted(sb), "type": sorted(tb)}
        verdicts = {}
        for label in ("Active", "Townhouse/Condo", "Remember my criteria", "Email me new listings"):
            m = re.search(r"\[(e\d+)\] checkbox \"" + re.escape(label) + '"', snap)
            d = browser.call("describe", "omar", ref=m.group(1)) if m else {}
            verdicts[label] = bp.read_only_gesture("click", d, "", False, K) if m else "no element"
        out["box_verdicts"] = verdicts
out["ledger_tables"] = ledger("tables")
out["ledger_open"] = ledger("open")
print("RESULT " + json.dumps(out, default=str))
'''

STATUS = {"Active": "active", "Option Pending": "pending", "Pending": "pending", "Sold": "sold"}


def expected_rows(raw):
    """rows.json written out by hand-checkable rules, independent of the parser."""
    def num(s):
        return int(s.replace("$", "").replace(",", "")) if s else None

    def day(s):
        if not s:
            return None
        m, d, y = s.split("/")
        return f"{y}-{int(m):02d}-{int(d):02d}"
    out = []
    for r in raw:
        full, half = r["baths"].split("/")
        out.append({"address": r["address"], "status": STATUS[r["st"]], "list_price": num(r["lp"]),
                    "sold_price": num(r["sp"]), "list_date": day(r["ld"]), "pending_date": day(r["pd"]),
                    "sold_date": day(r["cd"]), "beds": int(r["beds"]), "baths": int(full) + 0.5 * int(half),
                    "sqft": num(r["sqft"]), "year_built": int(r["yb"]),
                    "property_type": {"Single-Family": "single_family", "Townhouse/Condo": "townhouse_condo"}[r["type"]],
                    "area": r["sub"], "zip_code": r["zip"], "days_on_market": int(r["dom"]), "mls_number": r["mls"],
                    "distance_miles": None})
    return out


def start_worker(scr: pathlib.Path, allow: str):
    sys.path.insert(0, str(ROOT / "docs" / "browser"))
    import verify_browser_headed as h  # noqa: PLC0415
    subprocess.run(["rm", "-rf", str(scr)], check=True)
    scr.mkdir(parents=True)
    base = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC", "DISPLAY"))}
    eg = subprocess.Popen([sys.executable, "-I", str(ROOT / "app" / "browser_egress.py")],
                          env={**base, "JARVIS_EGRESS_LISTEN": str(scr / "egress.sock"), "JARVIS_EGRESS_TEST_ALLOW": allow},
                          stdout=open(scr / "egress.log", "w"), stderr=subprocess.STDOUT)
    h.wait_for(scr / "egress.sock")
    w = subprocess.Popen([sys.executable, "-I", str(ROOT / "app" / "browser_worker.py")],
                         env={**base, "JARVIS_BROWSER_BIN": HEADLESS, "JARVIS_BROWSER_PROFILE": str(scr / "profiles"),
                              "JARVIS_EGRESS_SOCKET": str(scr / "egress.sock"),
                              "JARVIS_BROWSER_LISTEN": str(scr / "w.sock"), "JARVIS_BROWSER_TEST": "1",
                              "JARVIS_BROWSER_MAX_OPEN": "3"},
                         stdout=open(scr / "w.log", "w"), stderr=subprocess.STDOUT)
    h.wait_for(scr / "w.sock")
    return eg, w


def step13(env) -> None:
    print("── step 13: reading pages")
    sys.path.insert(0, str(HERE))
    import fixture_site  # noqa: PLC0415
    ip = f"127.{secrets.randbelow(250) + 3}.0.31"
    srv, site = fixture_site.serve(ip)
    scr = pathlib.Path("/tmp/verify-mls-13")
    eg, w = start_worker(scr, site.split("//")[1])
    try:
        p = subprocess.run([sys.executable, "-c", CHILD13],
                           env={**env, "JARVIS_BROWSER_SOCKET": str(scr / "w.sock"), "SITE": site,
                                "JARVIS_SWITCH_BROKER": "on", "JARVIS_SWITCH_SENTINEL": "on",
                                "JARVIS_SENTINEL_MODE": "enforce"},
                           cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    finally:
        w.terminate(); eg.terminate(); w.wait(20); srv.shutdown()
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(p.stderr[-3000:])
        ck("step 13 child ran", False)
        return
    o = json.loads(line[-1][7:])
    pg = o["pages"]
    raw = json.loads((HERE / "fixtures" / "rows.json").read_text())
    want1, want2 = expected_rows(raw["page1"]), expected_rows(raw["page2"])
    ck("tables with nothing open is refused nothing_open", o["tables_nothing_open"] == [409, "nothing_open"],
       o["tables_nothing_open"])
    ck("every fixture opened and was read through the gate",
       all(v["open"] == 200 and v["tables"] in (200, 502) for v in pg.values()),
       {k: (v["open"], v["tables"]) for k, v in pg.items()})
    ck("each tables read is one executed ledger row, as is each open",
       o["ledger_tables"] == len(pg) and o["ledger_open"] == len(pg), (o["ledger_tables"], o["ledger_open"]))
    want_cls = {"search.html": "search", "results_table.html": "results", "results_table_p2.html": "results",
                "results_cards.html": "results", "results_json.html": "results", "no_results.html": "empty",
                "ambiguous.html": "ambiguous", "login.html": "login", "captcha.html": "captcha",
                "two_factor.html": "two_factor", "too_many.html": "blocked", "unknown.html": "unknown"}
    for name, cls in want_cls.items():
        ck(f"classify {name} → {cls}", pg[name]["class"] == cls, pg[name]["class"])
    ck("unknown page is unreadable, never an empty result", pg["unknown.html"]["class"] == "unknown"
       and pg["unknown.html"]["rows"] == [], pg["unknown.html"])
    ck("the results table parses to the hand-worked rows (statuses, F/H baths, dates, prices)",
       pg["results_table.html"]["rows"] == want1, pg["results_table.html"]["rows"][:2])
    ck("page 2 parses to its three rows", pg["results_table_p2.html"]["rows"] == want2)
    ck("the card and the embedded-data pages parse to the same rows as the table",
       pg["results_cards.html"]["rows"] == want1 and pg["results_json.html"]["rows"] == want1,
       (pg["results_cards.html"]["rows"][:1], pg["results_json.html"]["rows"][:1]))
    blob = json.dumps([pg[n]["rows"] for n in pg])
    ck("listing agents' names, phones, emails and remarks never reach a Listing",
       not any(s in blob for s in ("Pat Fixture", "555", "agent.example", "Lovely")) and "1201 Fixture St" in blob)
    ck("no-results page gives zero rows", pg["no_results.html"]["rows"] == [])
    ck("ambiguous page lists the site's choices",
       pg["ambiguous.html"]["choices"] == ["Heights Fixture Addn", "Heights Fixture Terrace", "Sample Heights"],
       pg["ambiguous.html"]["choices"])
    ck("the search page's filter fields are all found by label",
       pg["search.html"]["fields"] == sorted(["area", "zip_codes", "address", "radius_miles", "price_min", "price_max",
                                             "beds_min", "beds_max", "baths_min", "baths_max", "sqft_min", "sqft_max",
                                             "year_built_min", "year_built_max", "date_from", "date_to"]),
       pg["search.html"]["fields"])
    ck("status and type boxes are found", o["boxes"] == {
        "status": ["active", "pending", "sold"],
        "type": ["lot_land", "multi_family", "single_family", "townhouse_condo"]}, o["boxes"])
    v = o["box_verdicts"]
    ck("read rule: ticking Active or Townhouse/Condo in the site's own form is a filter",
       v["Active"] == "filter" and v["Townhouse/Condo"] == "filter", v)
    ck("read rule: 'Remember my criteria' and 'Email me new listings' are not reading",
       v["Remember my criteria"] is None and v["Email me new listings"] is None, v)
    ck("tables reply carries only the page keys and the five table keys",
       set(pg["results_table.html"]["keys"]) <= {"ok", "url", "title", "site", "snapshot", "http_status",
                                                 "site_refused", "tables", "cards", "data", "lists", "signals"},
       pg["results_table.html"]["keys"])


CHILD14 = r'''
import json, os, sys, time, types, urllib.request
from datetime import timedelta
sys.path.insert(0, os.environ["APP_ROOT"])
tc = types.ModuleType("tenant_config")
CFG = {"app": {"browser": {"mode": "headed"}}}
def _get(key, default=None):
    cur = CFG
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
tc.get = _get
tc.timezone_name = lambda: "America/Chicago"
sys.modules["tenant_config"] = tc
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import (accounts, actor_refs, agent_status, browser, mls, mls_limits, mls_matrix, platform_db,
                 platform_store as store, sentinel, threads)
SITE = os.environ["SITE"]
K = SITE.split("//")[1].split(":")[0]
# The fixture site is http on loopback, which app.mls refuses (step 11 proves
# that). Same shape and the same checks otherwise: settings() as it returns.
S = dict(mls_limits.DEFAULTS, site=K, start_url=SITE + "/Matrix/Search", pause_seconds=[3, 3],
         max_requests_per_person_per_day=20, max_page_loads_per_person_per_day=100, max_results=100)
CUR = {"s": S}
mls.settings = lambda: (dict(CUR["s"]), True)
SLEEPS = []
real_sleep = mls_matrix._sleep
mls_matrix._sleep = lambda v: SLEEPS.append(v)
if os.environ.get("CANARY") == "1":
    _get_thread = threads.get_thread
    threads.get_thread = lambda tid: dict(_get_thread(tid) or {}, direct_for="omar")
app = FastAPI(); app.include_router(mls.broker_router)
c = TestClient(app, client=("127.0.0.1", 50123))
H = {"Authorization": "Bearer " + os.environ["API_SERVER_KEY"]}
def count(n):
    return threads.bump_counter(n, 0)
def log():
    lg = json.loads(urllib.request.urlopen(SITE + "/__log", timeout=10).read())
    return {"loads": [x for x in lg["loads"] if x != "/__log"], "posts": lg["posts"]}
DM = threads.ensure_direct("omar")["thread_id"]
CH = threads.create_thread("verify-mls " + os.urandom(3).hex())["thread_id"]
REF = actor_refs.mint("omar", DM, "member")
REF_CH = actor_refs.mint("omar", CH, "member")
REF_DANA = actor_refs.mint("dana", threads.ensure_direct("dana")["thread_id"], "owner")
def search(ref=None, **params):
    r = c.post("/api/broker/mls", json={"ref": ref or REF, "op": "search", **params}, headers=H)
    return r.status_code, r.json()
def ledger(run_id, method=None):
    with platform_db.connect() as cx:
        q = "SELECT method, count(*) AS n FROM actions WHERE run_id=%s AND outcome='executed' GROUP BY method"
        return {r["method"]: r["n"] for r in cx.execute(q, (f"mls-{run_id}",)).fetchall()}
def allowance(who, site):
    aid = store.create_approval(origin="seed", user_id=who, kind="action", skill="jarvis", summary="verify read",
                                action={"type": "browser_read", "site": site}, status="approved",
                                decided_at=sentinel.utcnow(), decided_by=who)
    act = sentinel.Action(connector=browser.READ_CONNECTOR, method="read", cls=sentinel.EXTERNAL_SEND,
                          destinations=(f"@{site}",), skill="jarvis", user=who)
    g = sentinel.grant_from_approval(aid, act, sentinel.SCOPE_ALWAYS, sentinel.utcnow())
    store.create_grant(g, approval_id=aid, action_class=sentinel.EXTERNAL_SEND, created_by=who)
    return g.id
def pending_reads(who, site):
    return [r["id"] for r in store.list_approvals() if r["status"] == "pending" and r.get("user_id") == who
            and (r.get("action_json") or {}).get("type") == "browser_read" and (r.get("action_json") or {}).get("site") == site]
def clean():
    conn = mls._db()
    with conn:
        conn.execute("DELETE FROM mls_runs WHERE person_key IN ('omar', 'dana')")
    browser._clear_needs_you("omar")
clean()
for aid in pending_reads("omar", K):
    store.resolve_approval(aid, "rejected", "omar", {"reason": "verify cleanup"})
accounts.set_feature("omar", "browser", True, by="dana")
browser.call("close", "omar")
today = mls._today()
out = {}

# 1. No read allowance yet: the first gesture files the card and the run stops for the person.
st, b = search(area="Heights")
out["no_allowance"] = {"st": st, "error": b.get("error"), "kind": b.get("kind"), "page_loads": b.get("page_loads"),
                       "pending": len(pending_reads("omar", K))}
grant = allowance("omar", K)
clean()

# 2. "Active listings in the Heights": the whole flow.
n0 = len(log()["posts"]); SLEEPS.clear(); d0 = count("mls_rows_dropped")
st, b = search(area="Heights")
lg = log()
out["heights"] = {"st": st, "state": b.get("state"), "detail": b.get("detail"), "count": b.get("count"), "dropped": b.get("dropped"),
                  "addresses": [x["address"] for x in b.get("listings") or []], "page_loads": b.get("page_loads"),
                  "summary": b.get("summary"), "query": b.get("query"), "ledger": ledger(b.get("run_id")),
                  "posts": lg["posts"][n0:], "sleeps": list(SLEEPS), "dropped_delta": count("mls_rows_dropped") - d0,
                  "grant_rows": 0}
with platform_db.connect() as cx:
    out["heights"]["grant_rows"] = cx.execute("SELECT count(*) AS n FROM actions WHERE run_id=%s AND grant_id=%s",
                                              (f"mls-{b.get('run_id')}", grant)).fetchone()["n"]
# mls_result reads the same run back; another person can't.
rid = b.get("run_id")
r = c.post("/api/broker/mls", json={"ref": REF, "op": "result", "run_id": rid}, headers=H)
out["result_same"] = [r.status_code, r.json().get("count")]
r = c.post("/api/broker/mls", json={"ref": REF_DANA, "op": "result", "run_id": rid}, headers=H)
out["result_other"] = [r.status_code, r.json().get("error")]

# 3. Comps: sold near an address with the subject's size.
n0 = len(log()["posts"])
st, b = search(statuses=["sold"], address="1300 Sample Ave", subject_sqft=2000,
               date_from=(today - timedelta(days=180)).isoformat())
out["comps"] = {"st": st, "count": b.get("count"), "addresses": sorted(x["address"] for x in b.get("listings") or []),
                "unverified": b.get("unverified"), "summary": b.get("summary"), "posts": log()["posts"][n0:]}

# 4. Every filter typed: 3-bed single-family under 600k in 77008, pending this month.
n0 = len(log()["posts"])
st, b = search(statuses=["pending"], zip_codes=["77008"], beds_min=3, price_max=600000,
               property_types=["single_family"], date_from=today.replace(day=1).isoformat())
out["pending"] = {"st": st, "count": b.get("count"), "addresses": [x["address"] for x in b.get("listings") or []],
                  "posts": log()["posts"][n0:]}

# 5. A filter the form doesn't have stops the run, naming it.
CUR["s"] = dict(S, start_url=SITE + "/f/search_basic.html")
n0 = len(log()["loads"])
st, b = search(area="Heights", price_min=300000)
out["missing_filter"] = {"st": st, "error": b.get("error"), "detail": b.get("detail"),
                         "loads_after_start": [x for x in log()["loads"][n0:] if x != "/f/search_basic.html"]}
CUR["s"] = S

# 6. Stops: sign-in wall, human check, code; each asks the person and loads nothing more.
for area, kind in (("wall", "login"), ("check", "captcha"), ("code", "two_factor")):
    clean()
    n0 = len(log()["loads"])
    st, b = search(area=area)
    loads = log()["loads"][n0:]
    out[f"stop_{kind}"] = {"st": st, "error": b.get("error"), "kind": b.get("kind"), "page_loads": b.get("page_loads"),
                           "loads": loads, "needs_you": bool(browser.needs_you("omar")),
                           "site_state": (browser.site_row("omar", K) or {}).get("state")}
clean()
# 7. Ambiguous, unreadable, no results.
st, b = search(area="Height")
out["ambiguous"] = {"st": st, "error": b.get("error"), "choices": b.get("choices")}
st, b = search(area="odd")
out["unreadable"] = {"st": st, "error": b.get("error")}
st, b = search(area="Nowhere")
out["empty"] = {"st": st, "state": b.get("state"), "count": b.get("count")}
# 8. A block: the run ends blocked and the next search waits out the cooldown.
c0 = count("mls_refused_cooldown")
st, b = search(area="busy")
st2, b2 = search(area="Heights")
out["blocked"] = {"st": st, "error": b.get("error"), "then": [st2, b2.get("error"), bool(b2.get("until"))],
                  "delta": count("mls_refused_cooldown") - c0}
clean()
# 9. Refusals before anything runs.
def runs():
    return mls._db().execute("SELECT count(*) FROM mls_runs WHERE person_key='omar'").fetchone()[0]
r0, c0 = runs(), count("mls_refused_channel")
st, b = search(ref=REF_CH, area="Heights")
out["channel"] = {"st": st, "error": b.get("error"), "delta": count("mls_refused_channel") - c0, "runs": runs() - r0}
st, b = search(statuses=["sold"])
out["no_place"] = {"st": st, "error": b.get("error"), "runs": runs() - r0}
with browser._takeover_lock:
    browser._takeover["omar"] = time.monotonic()
st, b = search(area="Heights")
with browser._takeover_lock:
    browser._takeover.pop("omar", None)
out["taken_over"] = {"st": st, "error": b.get("error"), "runs": runs() - r0}
conn = mls._db()
with conn:
    conn.execute("INSERT INTO mls_runs (person_key, thread_id, query_json, state, started_at) VALUES "
                 "('dana', 't', '{}', 'running', ?)", (sentinel.utcnow().isoformat(),))
st, b = search(area="Heights")
out["busy"] = {"st": st, "error": b.get("error")}
clean()
accounts.set_feature("omar", "browser", False, by="dana")
st, b = search(area="Heights")
stat_off = agent_status.status("member", "omar")
accounts.set_feature("omar", "browser", True, by="dana")
stat_on = agent_status.status("member", "omar")
out["feature_off"] = {"st": st, "error": b.get("error"), "why": b.get("why"),
                      "off_available": "search_mls" in stat_off["available"],
                      "off_switched": "search_mls" in stat_off["switched_off"],
                      "on_available": "search_mls" in stat_on["available"],
                      "on_line": "MLS search" in agent_status.render(stat_on, "Omar")}
out["allowed_ready"] = "search_mls" in agent_status.allowed_now()
CUR["s"] = dict(S, start_url="")
out["allowed_unconfigured"] = "search_mls" in agent_status.allowed_now()
out["status_unconfigured"] = "search_mls" in agent_status.status("member", "omar")["available"]
st, b = search(area="Heights")
out["unconfigured"] = {"st": st, "error": b.get("error"), "why": b.get("why")}
CUR["s"] = S
# 10. Pauses are real: three page loads at 3 seconds each.
mls_matrix._sleep = real_sleep
t0 = time.monotonic()
st, b = search(area="Heights")
out["timed"] = {"st": st, "page_loads": b.get("page_loads"), "elapsed": time.monotonic() - t0}
clean()
store.revoke_grant(grant, by="omar", reason="verify done")
for aid in pending_reads("omar", K):
    store.resolve_approval(aid, "rejected", "omar", {"reason": "verify cleanup"})
print("RESULT " + json.dumps(out, default=str))
'''


def step14(env) -> None:
    print("── step 14: the run and the tool")
    sys.path.insert(0, str(HERE))
    import fixture_site  # noqa: PLC0415
    ip = f"127.{secrets.randbelow(250) + 3}.0.41"
    srv, site = fixture_site.serve(ip)
    scr = pathlib.Path("/tmp/verify-mls-14")
    eg, w = start_worker(scr, site.split("//")[1])
    try:
        p = subprocess.run([sys.executable, "-c", CHILD14],
                           env={**env, "JARVIS_BROWSER_SOCKET": str(scr / "w.sock"), "SITE": site,
                                "JARVIS_SWITCH_BROKER": "on", "JARVIS_SWITCH_SENTINEL": "on",
                                "JARVIS_SENTINEL_MODE": "enforce", "JARVIS_ALLOW_WRITES": "1",
                                "API_SERVER_KEY": secrets.token_urlsafe(24)},
                           cwd=str(ROOT), capture_output=True, text=True, timeout=600)
    finally:
        w.terminate(); eg.terminate(); w.wait(20); srv.shutdown()
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(p.stderr[-4000:])
        ck("step 14 child ran", False)
        return
    o = json.loads(line[-1][7:])
    na = o["no_allowance"]
    ck("no read allowance: the run stops needs_person/allow_reading after one page and files one read card",
       na == {"st": 409, "error": "needs_person", "kind": "allow_reading", "page_loads": 1, "pending": 1}, na)
    h = o["heights"]
    ck("Heights active: done with the four active rows across two pages",
       h["st"] == 200 and h["state"] == "done" and h["count"] == 4 and sorted(h["addresses"]) ==
       sorted(["1201 Fixture St", "15 Mock Ct #2", "900 Placeholder Rd", "44 Invented Ln"]), h)
    ck("Heights active: the five other-status rows the site returned are dropped and counted",
       h["dropped"] == 5 and h["dropped_delta"] == 5, h)
    ck("Heights active: three page loads (start, search, next), a pause before each",
       h["page_loads"] == 3 and len(h["sleeps"]) == 3 and all(x == 3 for x in h["sleeps"]), h)
    ck("Heights active: one executed ledger row per page load and per gesture",
       h["ledger"].get("open") == 1 and h["ledger"].get("click", 0) == 3 and h["ledger"].get("type") == 1
       and h["ledger"].get("tables") == 3, h["ledger"])
    ck("Heights active: the gestures ran under the person's read allowance (grant id on the rows)",
       h["grant_rows"] == 4, h["grant_rows"])
    ck("Heights active: the form posted Area=Heights and only the Active box",
       len(h["posts"]) == 1 and h["posts"][0].get("area") == ["Heights"] and h["posts"][0].get("st") == ["Active"]
       and "remember" not in h["posts"][0] and "alert" not in h["posts"][0], h["posts"])
    ck("Heights active: the summary is list prices with the filters used",
       "list prices" in (h["summary"] or {}).get("note", "") and h["query"] == {"statuses": ["active"], "area": "Heights"},
       (h["summary"], h["query"]))
    ck("mls_result reads the same run back for the person", o["result_same"] == [200, 4], o["result_same"])
    ck("mls_result: another person gets 404 for it", o["result_other"] == [404, "not_found"], o["result_other"])
    cp = o["comps"]
    ck("comps: three sold rows, unverified for distance (the page shows none), with a suggested range",
       cp["st"] == 200 and cp["count"] == 3 and cp["addresses"] == ["1300 Sample Ave", "1410 Sample Ave", "48 Invented Ln"]
       and cp["unverified"] == 3 and (cp["summary"] or {}).get("suggested_low") == 494000
       and (cp["summary"] or {}).get("suggested_high") == 510000, cp)
    post = (cp["posts"] or [{}])[0]
    ck("comps: the form posted the address, a 1-mile radius, a date and only Sold",
       post.get("addr") == ["1300 Sample Ave"] and post.get("radius") == ["1"] and post.get("st") == ["Sold"]
       and bool(post.get("dfrom")), cp["posts"])
    pd = o["pending"]
    post = (pd["posts"] or [{}])[0]
    ck("every filter typed: zip, beds, max price, type box and date reach the form",
       post.get("zip") == ["77008"] and post.get("bmin") == ["3"] and post.get("pmax") == ["600000"]
       and post.get("pt") == ["Single-Family"] and post.get("st") == ["Pending"] and bool(post.get("dfrom")), pd["posts"])
    mf = o["missing_filter"]
    ck("a filter the form lacks ends unreadable naming it, before any search is posted",
       mf["st"] == 502 and mf["error"] == "unreadable" and "price min" in (mf["detail"] or "")
       and mf["loads_after_start"] == [], mf)
    for kind in ("login", "captcha", "two_factor"):
        s = o[f"stop_{kind}"]
        ck(f"{kind}: stops needs_person/{kind}, asks the person, marks the site needs_signin, loads nothing more",
           s["st"] == 409 and s["error"] == "needs_person" and s["kind"] == kind and s["needs_you"]
           and s["site_state"] == "needs_signin" and s["page_loads"] == 2
           and s["loads"] == ["/Matrix/Search", "/Matrix/Results"] + (["/recaptcha/frame"] if kind == "captcha" else []), s)
    ck("ambiguous area returns the site's choices",
       o["ambiguous"]["st"] == 409 and o["ambiguous"]["error"] == "ambiguous" and len(o["ambiguous"]["choices"] or []) == 3,
       o["ambiguous"])
    ck("an unknown page is unreadable", o["unreadable"] == {"st": 502, "error": "unreadable"}, o["unreadable"])
    ck("no results is done with zero rows", o["empty"] == {"st": 200, "state": "done", "count": 0}, o["empty"])
    bl = o["blocked"]
    ck("a 429 ends the run blocked; the next search is refused cooling_off with a time, counted",
       bl["st"] == 409 and bl["error"] == "blocked" and bl["then"] == [409, "cooling_off", True] and bl["delta"] == 1, bl)
    ck("a shared channel is refused direct_only, counted, no run", o["channel"] == {
        "st": 403, "error": "direct_only", "delta": 1, "runs": 0}, o["channel"])
    ck("no place is refused bad_request, no run", o["no_place"] == {"st": 400, "error": "bad_request", "runs": 0},
       o["no_place"])
    ck("during Take over the search is refused taken_over, no run",
       o["taken_over"] == {"st": 409, "error": "taken_over", "runs": 0}, o["taken_over"])
    ck("a run going on the tenant refuses another busy", o["busy"] == {"st": 409, "error": "busy"}, o["busy"])
    fo = o["feature_off"]
    ck("browser off for the person: refused unavailable/browser_feature_off; the tool is switched off in their status",
       fo["st"] == 403 and fo["error"] == "unavailable" and fo["why"] == "browser_feature_off"
       and not fo["off_available"] and fo["off_switched"], fo)
    ck("browser on for the person: the tool is in their status with its line (presence twin)",
       fo["on_available"] and fo["on_line"], fo)
    ck("set up: the model may be offered search_mls; no start page: it may not, and their status drops it",
       o["allowed_ready"] and not o["allowed_unconfigured"] and not o["status_unconfigured"],
       (o["allowed_ready"], o["allowed_unconfigured"], o["status_unconfigured"]))
    ck("no start page: refused unavailable/not_configured",
       o["unconfigured"] == {"st": 403, "error": "unavailable", "why": "not_configured"}, o["unconfigured"])
    t = o["timed"]
    ck("pauses are real: three loads at 3 seconds take at least 9 seconds",
       t["st"] == 200 and t["page_loads"] == 3 and t["elapsed"] >= 9, t)

CHILD15 = r"""
import json, os, sys, types
from datetime import timedelta
sys.path.insert(0, os.environ["APP_ROOT"])
tc = types.ModuleType("tenant_config")
CFG = {"app": {"browser": {"mode": "headed"}}}
def _get(key, default=None):
    cur = CFG
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
tc.get = _get
tc.timezone_name = lambda: "America/Chicago"
sys.modules["tenant_config"] = tc
from fastapi.testclient import TestClient
from app import (accounts, actor_refs, agent_status, auth, main, mls, mls_limits, mls_matrix, threads)
S = dict(mls_limits.DEFAULTS, site="fixture.example", start_url="https://matrix.fixture.example/Matrix/Search",
         max_results=100)
mls.settings = lambda: (dict(S), True)
NEXT = {"res": None}
def fake_read(q, budget, ctx, gate=None):
    budget.draw()
    return NEXT["res"]
mls_matrix.read = fake_read
if os.environ.get("CANARY") == "1":
    def _any_person(run_id, person):
        row = mls._db().execute("SELECT * FROM mls_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None
    mls.run_row = _any_person
ROWS = [mls.Listing(address="1201 Fixture St", status="active", list_price=450000, beds=3, baths=2.0, sqft=1800,
                    list_date="2026-10-01", mls_number="F-1"),
        mls.Listing(address="44 Invented Ln", status="active", list_price=520000, beds=4, baths=3.0, sqft=2100,
                    list_date="2026-10-03", mls_number="F-2")]
SESS = {k: {"person_key": k, "role": r, "kind": "person"} for k, r in
        (("dana", "owner"), ("priya", "admin"), ("omar", "member"), ("maya", "member"))}
SESS["op"] = {"person_key": None, "role": "operator", "operator": "operator", "kind": "operator"}
who = {"v": "omar"}
main.app.dependency_overrides[auth.current_session] = lambda: SESS[who["v"]]
c = TestClient(main.app, client=("127.0.0.1", 50124))
H = {"Authorization": "Bearer " + os.environ["API_SERVER_KEY"]}
def count(n):
    return threads.bump_counter(n, 0)
def as_(k):
    who["v"] = k
    return c
with mls._db() as conn:
    conn.execute("DELETE FROM mls_runs WHERE person_key IN ('omar', 'dana')")
DM = threads.ensure_direct("omar")["thread_id"]
CH = threads.create_thread("verify-mls15 " + os.urandom(3).hex())["thread_id"]
out = {}
def search(ref, **params):
    r = c.post("/api/broker/mls", json={"ref": ref, "op": "search", **params}, headers=H)
    return r.status_code, r.json()

# 1. A search in the person's direct line asks for its card on that turn.
REF = actor_refs.mint("omar", DM, "member")
NEXT["res"] = mls.Result("done", listings=ROWS)
st, b = search(REF, area="Heights", statuses=["active"])
rid = b.get("run_id")
out["search"] = {"st": st, "state": b.get("state"), "actions": actor_refs.actions(REF), "rid": rid}
# 2. A refused search (shared channel) asks for nothing.
REF_CH = actor_refs.mint("omar", CH, "member")
st2, b2 = search(REF_CH, area="Heights")
out["channel"] = {"st": st2, "error": b2.get("error"), "actions": actor_refs.actions(REF_CH)}
# 3. mls_result on a later turn asks for the card again; another person's turn does not.
REF2 = actor_refs.mint("omar", DM, "member")
r = c.post("/api/broker/mls", json={"ref": REF2, "op": "result", "run_id": rid}, headers=H)
out["result_again"] = {"st": r.status_code, "actions": actor_refs.actions(REF2)}
REF_D = actor_refs.mint("dana", threads.ensure_direct("dana")["thread_id"], "owner")
r = c.post("/api/broker/mls", json={"ref": REF_D, "op": "result", "run_id": rid}, headers=H)
out["result_dana"] = {"st": r.status_code, "actions": actor_refs.actions(REF_D)}
# 4. The chat turn draws it only for that person in that thread.
out["card_ok"] = [agent_status.mls_card_ok(rid, "omar", DM), agent_status.mls_card_ok(rid, "omar", CH),
                  agent_status.mls_card_ok(rid, "dana", DM), agent_status.mls_card_ok(rid, "", DM)]
# 5. The card route: the person gets the run; owner, admin, another member and the operator get 404.
r = as_("omar").get(f"/api/mls/runs/{rid}")
body = r.json()
out["card_omar"] = {"st": r.status_code, "state": body.get("state"), "count": body.get("count"),
                    "addresses": [l["address"] for l in body.get("listings") or []],
                    "keys": sorted((body.get("listings") or [{}])[0]), "note": (body.get("summary") or {}).get("note")}
n0 = count("mls_card_not_found")
out["card_others"] = {k: as_(k).get(f"/api/mls/runs/{rid}").status_code for k in ("dana", "priya", "maya", "op")}
out["card_not_found_delta"] = count("mls_card_not_found") - n0
out["card_none"] = as_("omar").get("/api/mls/runs/999999999").status_code
# 6. Saved under the reply, it survives a reload for the person and nobody else.
mid = threads.append_messages(DM, {"role": "assistant", "content": "Here are the listings.",
                                   "actions": [f"mls:{rid}", "mls:abc", "mls:0", "open_url"]})[-1]
h = as_("omar").get("/api/chat/history", params={"thread_id": DM}).json()
msg = next((m for m in h.get("messages", []) if m.get("id") == mid), {})
out["reload"] = msg.get("actions")
out["reload_dana"] = agent_status.live_actions([f"mls:{rid}"], "owner", "dana")
out["reload_op"] = agent_status.live_actions([f"mls:{rid}"], "operator", "")
# 7. A stopped run's card says why, from the stored run.
REF3 = actor_refs.mint("omar", DM, "member")
NEXT["res"] = mls.Result("needs_person", stop_kind="login", detail="Sign in to Matrix in the browser window.")
st3, b3 = search(REF3, area="Heights")
r = as_("omar").get(f"/api/mls/runs/{b3.get('run_id')}")
out["stopped"] = {"st": st3, "card": r.status_code, "state": r.json().get("state"), "kind": r.json().get("kind"),
                  "actions": actor_refs.actions(REF3)}
# 8. Past keep_days the card says the results expired; the stored rows are gone.
with mls._db() as conn:
    conn.execute("UPDATE mls_runs SET finished_at = ? WHERE id = ?",
                 ((mls._now() - timedelta(days=S["keep_days"] + 1)).isoformat(), rid))
e0 = count("mls_results_cleared")
r = as_("omar").get(f"/api/mls/runs/{rid}")
row = mls._db().execute("SELECT result_json, result_count, state FROM mls_runs WHERE id = ?", (rid,)).fetchone()
out["expired"] = {"st": r.status_code, "expired": r.json().get("expired"), "detail": r.json().get("detail"),
                  "listings": r.json().get("listings"), "cleared_delta": count("mls_results_cleared") - e0,
                  "stored": row["result_json"], "state": row["state"]}
h = as_("omar").get("/api/chat/history", params={"thread_id": DM}).json()
msg = next((m for m in h.get("messages", []) if m.get("id") == mid), {})
out["reload_expired"] = msg.get("actions")
for ref in (REF, REF_CH, REF2, REF_D, REF3):
    actor_refs.end(ref)
print("RESULT " + json.dumps(out, default=str))
"""


def step15(env) -> None:
    print("── step 15: the results card")
    p = subprocess.run([sys.executable, "-c", CHILD15],
                       env={**env, "JARVIS_SWITCH_BROKER": "on", "API_SERVER_KEY": secrets.token_urlsafe(24)},
                       cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(p.stderr[-4000:])
        ck("step 15 child ran", False)
        return
    o = json.loads(line[-1][7:])
    s = o["search"]
    rid = s["rid"]
    ck("a search in the direct line asks for its card on that turn (presence)",
       s["st"] == 200 and s["state"] == "done" and s["actions"] == [f"mls:{rid}"], s)
    ck("a search refused in a shared channel asks for no card", o["channel"] == {
        "st": 403, "error": "direct_only", "actions": []}, o["channel"])
    ck("mls_result on a later turn asks for the same card", o["result_again"] == {
        "st": 200, "actions": [f"mls:{rid}"]}, o["result_again"])
    ck("mls_result from another person's turn: 404 and no card", o["result_dana"] == {"st": 404, "actions": []},
       o["result_dana"])
    ck("the turn draws the card only for that person in that thread", o["card_ok"] == [True, False, False, False],
       o["card_ok"])
    co = o["card_omar"]
    ck("the card route gives the person their run, from the stored rows",
       co["st"] == 200 and co["state"] == "done" and co["count"] == 2
       and co["addresses"] == ["1201 Fixture St", "44 Invented Ln"] and "list prices" in (co["note"] or ""), co)
    ck("no listing field beyond the plan's shape (no agent contact, no link)",
       co["keys"] == sorted(["address", "status", "list_price", "sold_price", "list_date", "pending_date", "sold_date",
                             "beds", "baths", "sqft", "year_built", "property_type", "area", "zip_code",
                             "days_on_market", "mls_number", "distance_miles", "price_per_sqft"]), co["keys"])
    ck("owner, admin, another member and the operator each get 404 for it, counted",
       o["card_others"] == {"dana": 404, "priya": 404, "maya": 404, "op": 404} and o["card_not_found_delta"] == 4,
       (o["card_others"], o["card_not_found_delta"]))
    ck("a run that does not exist is the same 404", o["card_none"] == 404, o["card_none"])
    ck("the card survives a reload; forged and unknown actions are not drawn",
       o["reload"] == [{"action": "mls", "run_id": rid, "label": "MLS search results"}], o["reload"])
    ck("saved under the reply, it is not drawn for the owner or the operator",
       o["reload_dana"] == [] and o["reload_op"] == [], (o["reload_dana"], o["reload_op"]))
    so = o["stopped"]
    ck("a stopped run's card carries its state and kind; the turn still asks for it",
       so["st"] == 409 and so["card"] == 200 and so["state"] == "needs_person" and so["kind"] == "login"
       and len(so["actions"]) == 1, so)
    ex = o["expired"]
    ck("past keep_days the card says these results have expired, not an empty table",
       ex["st"] == 200 and ex["expired"] is True and "expired" in (ex["detail"] or "") and ex["listings"] is None, ex)
    ck("expiry clears the stored rows (ledger: result_json NULL, counter +1, the run stays done)",
       ex["stored"] is None and ex["cleared_delta"] == 1 and ex["state"] == "done", ex)
    ck("an expired card is still drawn after a reload (it says expired, it does not vanish)",
       o["reload_expired"] == [{"action": "mls", "run_id": rid, "label": "MLS search results"}], o["reload_expired"])

CHILD17 = r"""
import json, os, sys, pathlib
sys.path.insert(0, os.environ["APP_ROOT"])
from app import accounts, browser, mls, platform_store as store, sentinel, threads
SITE = os.environ["SITE"]
K = SITE.split("//")[1].split(":")[0]
PROFILES = pathlib.Path(os.environ["PROFILES"])
if os.environ.get("CANARY") == "1":
    _call = browser.call
    browser.call = lambda op, person, timeout=100, **kw: _call("close" if op == "erase_profile" else op, person,
                                                              timeout, **kw)
def count(n):
    return threads.bump_counter(n, 0)
def allowance(who, site):
    aid = store.create_approval(origin="seed", user_id=who, kind="action", skill="jarvis", summary="verify read",
                                action={"type": "browser_read", "site": site}, status="approved",
                                decided_at=sentinel.utcnow(), decided_by=who)
    act = sentinel.Action(connector=browser.READ_CONNECTOR, method="read", cls=sentinel.EXTERNAL_SEND,
                          destinations=(f"@{site}",), skill="jarvis", user=who)
    g = sentinel.grant_from_approval(aid, act, sentinel.SCOPE_ALWAYS, sentinel.utcnow())
    store.create_grant(g, approval_id=aid, action_class=sentinel.EXTERNAL_SEND, created_by=who)
    return g.id
def live(who):
    return [g.id for g in store.active_grants(who, browser.READ_CONNECTOR, "jarvis")]
out = {}
for who in ("omar", "dana"):
    browser.call("open", who, timeout=60, url=SITE + "/Matrix/Search")
    c = browser._db()
    c.execute("INSERT OR REPLACE INTO browser_sites (person_key, site, state, connected_at) VALUES (?,?,?,?)",
              (who, K, "connected", "t"))
    c.commit()
    with mls._db() as conn:
        conn.execute("INSERT INTO mls_runs (person_key, thread_id, query_json, state, started_at) "
                     "VALUES (?, 'dm', '{}', 'done', '2000-01-01T00:00:00+00:00')", (who,))
g_omar, g_dana = allowance("omar", K), allowance("dana", K)
out["before"] = {"profiles": sorted(p.name for p in PROFILES.iterdir()), "omar_grant": g_omar in live("omar"),
                 "dana_grant": g_dana in live("dana"), "running": browser.call("status", "omar").get("running")}
def held(who):
    return {"sites": browser._db().execute("SELECT COUNT(*) FROM browser_sites WHERE person_key=?", (who,)).fetchone()[0],
            "runs": mls._db().execute("SELECT COUNT(*) FROM mls_runs WHERE person_key=?", (who,)).fetchone()[0],
            "grants": sum(len(store.active_grants(who, c, "jarvis")) for c in (browser.CONNECTOR, browser.READ_CONNECTOR))}
out["omar_held"], out["dana_held"] = held("omar"), held("dana")
e0, f0 = count("browser_person_erased"), count("browser_person_erase_failed")
r = browser.erase_person("omar", "dana")
out["omar_after"], out["dana_after"] = held("omar"), held("dana")
runs = {k: n for k, n in mls._db().execute("SELECT person_key, COUNT(*) FROM mls_runs WHERE person_key IN ('omar','dana') "
                                           "GROUP BY person_key").fetchall()}
sites = {k: n for k, n in browser._db().execute("SELECT person_key, COUNT(*) FROM browser_sites WHERE site=? "
                                                "GROUP BY person_key", (K,)).fetchall()}
out["person"] = {"result": r, "profiles": sorted(p.name for p in PROFILES.iterdir()),
                 "omar_grant": g_omar in live("omar"), "dana_grant": g_dana in live("dana"),
                 "runs": runs, "sites": sites, "erased_delta": count("browser_person_erased") - e0,
                 "failed_delta": count("browser_person_erase_failed") - f0,
                 "dana_running": browser.call("status", "dana").get("running")}
store.revoke_grant(g_dana, by="dana", reason="verify done")
a0 = count("browser_profiles_erased")
out["all"] = {"result": browser.erase_all(), "profiles": sorted(p.name for p in PROFILES.iterdir()),
              "delta": count("browser_profiles_erased") - a0}
os.environ["JARVIS_BROWSER_SOCKET"] = "/nonexistent/worker.sock"
f1, af = count("browser_person_erase_failed"), count("browser_profiles_erase_failed")
r = browser.erase_person("maya", "dana")
out["down"] = {"person": r, "person_failed_delta": count("browser_person_erase_failed") - f1,
               "all": browser.erase_all(), "all_failed_delta": count("browser_profiles_erase_failed") - af}
os.environ["JARVIS_BROWSER"] = "off"
out["off"] = {"person": browser.erase_person("maya", "dana")["profile"], "all": browser.erase_all()}
print("RESULT " + json.dumps(out, default=str))
"""


def step17(env) -> None:
    print("── step 17: removing a person or the tenant clears the browser")
    sys.path.insert(0, str(HERE))
    import fixture_site  # noqa: PLC0415
    ip = f"127.{secrets.randbelow(250) + 3}.0.71"
    srv, site = fixture_site.serve(ip)
    scr = pathlib.Path("/tmp/verify-mls-17")
    eg, w = start_worker(scr, site.split("//")[1])
    try:
        p = subprocess.run([sys.executable, "-c", CHILD17],
                           env={**env, "JARVIS_BROWSER_SOCKET": str(scr / "w.sock"), "SITE": site,
                                "PROFILES": str(scr / "profiles")},
                           cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    finally:
        w.terminate(); eg.terminate(); w.wait(20); srv.shutdown()
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(p.stderr[-4000:])
        ck("step 17 child ran", False)
        return
    o = json.loads(line[-1][7:])
    b = o["before"]
    ck("seeded: omar and dana each have a profile on disk and a read allowance (presence)",
       b["profiles"] == ["dana", "omar"] and b["omar_grant"] and b["dana_grant"] and b["running"], b)
    pr = o["person"]
    ck("removing omar closes his browser and deletes his profile directory; dana's stays and keeps running",
       pr["result"]["profile"] == "erased" and pr["profiles"] == ["dana"] and pr["dana_running"], pr)
    ck("removing omar revokes his allowance and not dana's", not pr["omar_grant"] and pr["dana_grant"], pr)
    oh, dh = o["omar_held"], o["dana_held"]
    ck("removing omar deletes all his connected sites, MLS runs and allowances (what he held, by count)",
       oh["sites"] >= 1 and oh["runs"] >= 1 and oh["grants"] >= 1
       and pr["result"]["sites"] == oh["sites"] and pr["result"]["mls_runs"] == oh["runs"]
       and pr["result"]["allowances_revoked"] == oh["grants"]
       and o["omar_after"] == {"sites": 0, "runs": 0, "grants": 0}, (oh, pr["result"], o["omar_after"]))
    ck("   and none of dana's", o["dana_after"] == dh and dh["sites"] >= 1 and dh["runs"] >= 1 and dh["grants"] >= 1,
       (dh, o["dana_after"]))
    ck("removing a person is counted once, no failure", pr["erased_delta"] == 1 and pr["failed_delta"] == 0
       and pr["result"]["failed"] == [], pr)
    al = o["all"]
    ck("deleting the tenant removes every profile, counted",
       al["result"] == "erased" and al["profiles"] == [] and al["delta"] == 1, al)
    dn = o["down"]
    ck("worker down: removing a person says the profile failed, counted, and still clears the rows",
       dn["person"]["profile"] == "failed" and dn["person"]["failed"] == ["profile"] and dn["person_failed_delta"] == 1, dn)
    ck("worker down: deleting the tenant says failed, counted (the caller reports it)",
       dn["all"] == "failed" and dn["all_failed_delta"] == 1, dn)
    ck("browser off: nothing to erase, and it says so (browser_off, not erased)",
       o["off"] == {"person": "browser_off", "all": "browser_off"}, o["off"])


def provisioner_checks():
    sys.path.insert(0, str(ROOT / "scripts"))
    import provision_tenant as pt  # noqa: PLC0415

    def refused(manifest):
        try:
            pt.mls(manifest)
        except pt.Refuse:
            return True
        return False

    headed = {"browser": "enabled", "browser_mode": "headed"}
    good = {"site": "harmls.com", "start_url": "https://matrix.harmls.com/Matrix/Search"}
    r = pt.mls({**headed, "mls": good})
    ck("provisioner: a valid block renders with its defaults filled in",
       r["start_url"] == good["start_url"] and r["max_results"] == 25 and r["pause_seconds"] == [4, 9]
       and r["max_page_loads_per_request"] == 10 and "available" not in r, r)
    ck("provisioner: no mls block renders unconfigured (no start_url), not refused",
       pt.mls({})["start_url"] == "")
    for label, block, man in [
        ("max_results above 100", {**good, "max_results": 101}, headed),
        ("page loads per request above 10", {**good, "max_page_loads_per_request": 11}, headed),
        ("daily requests above 20", {**good, "max_requests_per_person_per_day": 21}, headed),
        ("daily page loads above 100", {**good, "max_page_loads_per_person_per_day": 101}, headed),
        ("keep_days above 90", {**good, "keep_days": 91}, headed),
        ("pause under 3 seconds", {**good, "pause_seconds": [1, 5]}, headed),
        ("pause high before low", {**good, "pause_seconds": [9, 4]}, headed),
        ("cooldown 0 hours", {**good, "cooldown_hours_after_block": 0}, headed),
        ("a boolean as a number", {**good, "max_results": True}, headed),
        ("an http start_url", {**good, "start_url": "http://matrix.harmls.com/x"}, headed),
        ("an off-site start_url", {**good, "start_url": "https://evil.example/x"}, headed),
        ("credentials in start_url", {**good, "start_url": "https://u:p@matrix.harmls.com/x"}, headed),
        ("an unknown key", {**good, "available": True}, headed),
        ("per-request above per-day", {**good, "max_page_loads_per_person_per_day": 5}, headed),
        ("start_url with a headless browser", good, {"browser": "enabled"}),
        ("start_url with the browser off", good, {"browser_mode": "headed"}),
    ]:
        ck(f"provisioner refuses: {label}", refused({**man, "mls": block}))
    ck("provisioner: a lower cap than the limit is accepted (presence twin)",
       pt.mls({**headed, "mls": {**good, "max_results": 10, "keep_days": 7}})["max_results"] == 10)


def main() -> int:
    for k in ("AUTH_BASE", "APP_PORT"):
        if os.environ.get(k, "").endswith(("8700", "8710")):
            sys.exit("REFUSING: live or testco port")
    print("── step 11: availability and settings")
    provisioner_checks()
    base = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC", "DISPLAY"))}
    env = {**base,
           "APP_ROOT": str(ROOT), "APP_GOOGLE_TOKEN_KEY": (SB / "token_key").read_text().strip(),
           "HERMES_HOME": str(SB / "hermes"), "JARVIS_APP_ROOT": str(ROOT),
           "JARVIS_SKILLS_ROOT": "/opt/jarvis-verify-fixture/skills/openclaw-imports",
           "JARVIS_RUNTIME_CONFIG": str(SB / "runtime.json"),
           "JARVIS_IDENTITY_CONFIG": "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json",
           "JARVIS_ACCOUNTS_DB": str(SB / "accounts.db"), "JARVIS_THREADS_DB": str(SB / "threads.db"),
           "JARVIS_TASKS_DB": str(SB / "tasks.db"), "JARVIS_LEADS_DB": str(SB / "leads.db"),
           "JARVIS_ACTIVITY_FEED": str(SB / "feed.jsonl"), "JARVIS_PG_DSN": (SB / "pg_dsn").read_text().strip(),
           "JARVIS_BROWSER": "on", "JARVIS_BROWSER_SOCKET": "/nonexistent/w.sock",
           "CANARY": "1" if CANARY else "0"}
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(ROOT), capture_output=True, text=True,
                       timeout=120)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not line:
        print(p.stderr[-3000:])
        ck("child ran", False)
        return 1
    o = json.loads(line[-1][7:])
    c = o["cases"]
    for why in ("browser_off", "not_headed", "config_invalid", "not_configured", "not_a_person"):
        ck(f"{why}: refused with its own reason, its counter +1 and no other",
           c[why]["why"] == why and c[why]["delta"] == {why: 1}, c[why])
    ck("not_headed: an absent app.browser reads as headless", c["not_headed_missing"]["why"] == "not_headed",
       c["not_headed_missing"])
    ck("config_invalid: mls_config_invalid +1", c["config_invalid"]["invalid_delta"] == 1, c["config_invalid"])
    ck("browser_feature_off: refused for a person whose browser is off, its counter +1",
       c["browser_feature_off"]["why"] == "browser_feature_off"
       and c["browser_feature_off"]["delta"] == {"browser_feature_off": 1}, c["browser_feature_off"])
    ck("available: everything on gives None and moves no refusal counter (presence twin)",
       c["available"]["why"] is None and c["available"]["delta"] == {} and c["available"]["invalid_delta"] == 0,
       c["available"])
    ck("count=False reports the reason without moving a counter", o["quiet_why"] is None and o["quiet_delta"] == 0, o)
    ck("no MLS person feature: FEATURES unchanged",
       o["features"] == ["ask", "leads", "tasks", "send", "browser", "imessage"], o["features"])
    ck("omar's browser feature restored", o["restored"])
    step12(env)
    step13(env)
    step14(env)
    step15(env)
    step17(env)
    n, t = sum(ok), len(ok)
    print(f"\n{n}/{t} passed" + ("  (canary run: a FAIL above is the point)" if CANARY else ""))
    return 0 if n == t else 1


if __name__ == "__main__":
    sys.exit(main())
