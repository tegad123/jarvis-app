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

--canary breaks three things on purpose; these checks must FAIL: the
person's browser feature is ignored (browser_feature_off), the re-check
keeps every row (three re-check checks), and an unknown page reads as empty
(two classify checks).
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
    n, t = sum(ok), len(ok)
    print(f"\n{n}/{t} passed" + ("  (canary run: a FAIL above is the point)" if CANARY else ""))
    return 0 if n == t else 1


if __name__ == "__main__":
    sys.exit(main())
