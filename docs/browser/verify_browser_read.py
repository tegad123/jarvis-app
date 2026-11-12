#!/usr/bin/env python3
"""The standing read allowance (step 10 of docs/plans/browser-har-comps.md).

A fixture listings page on loopback, real egress, a real (headless) worker,
the real gate (browser.gate) and approvals (approvals.decide) against the
verify sandbox's platform database (docs/tasks/tasks_sandbox.sh first).

Proves:
- the rule: a table of element facts, each with the expected answer, the
  deny cases beside the allow cases;
- with no allowance, following a link asks once (a browser_read card offering
  "for 30 minutes" and "always"); asking again reuses the same card;
- once allowed (always): following a link and submitting a search run with no
  new approval, and their ledger rows carry the read grant's id;
- still held: typing into an email field or a text area, a "Contact" link, a
  posting form's "Send" button, a link to another site, and a risky button;
- canary: an always read grant alone does NOT satisfy _active_grant() (and,
  as its presence twin, it does satisfy the read lookup);
- after revoking, the same link click asks again; a plain approve then gives
  30 minutes, not always;
- omar's allowance does nothing for dana; dana cannot approve omar's card,
  nor can the owner; "once" is not a scope this card offers.
--canary points the read lookup at the acting connector: the "still held"
and canary checks must FAIL. Never :8700 or :8710.
"""
import http.server
import json
import os
import pathlib
import secrets
import subprocess
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "app"))
import verify_browser_headed as h  # noqa: E402

SB = pathlib.Path(os.environ.get("TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox"))
SCR = pathlib.Path("/tmp/verify-browser-read")
HEADLESS = "/root/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell"
CANARY = "--canary" in sys.argv
OTHER = {"url": ""}


class Listings(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _page(self, title, body):
        data = f"<html><head><title>{title}</title></head><body>{body}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        q = self.path.partition("?")[2]
        self._page("Listings " + (q or "page 1"), (
            "<h1>Results</h1><a href='/list?page=2'>Next</a> <a href='/contact'>Contact agent</a> "
            f"<a href='{OTHER['url']}/x'>Other site</a>"
            "<form method=get action='/list'><input type=search name=q aria-label='Search listings'>"
            "<button>Search</button></form>"
            "<form method=post action='/contact'><input type=email name=email aria-label='Email'>"
            "<textarea name=msg aria-label='Message'></textarea><button>Send message</button></form>"
            "<button>Buy now</button>"))

    def do_POST(self):
        self._page("posted", "<p>posted</p>")


CHILD = r'''
import json, os, re, sys
sys.path.insert(0, os.environ["APP_ROOT"])
from app import approvals, browser, browser_policy as bp, platform_db, platform_store as store, sentinel, threads
if os.environ.get("CANARY") == "1":
    browser.READ_CONNECTOR = browser.CONNECTOR
SITE = os.environ["SITE"]
K = SITE.split("//")[1].split(":")[0]
ctx = {"run": "verify-read-" + os.urandom(4).hex(), "session": "", "task": "", "skill": "jarvis", "source": "verify:read"}
out = {}
def snap(who):
    return browser.call("read", who).get("snapshot", "")
def ref(s, role, name):
    m = re.search(r"\[(e\d+)\] " + role + r' "' + re.escape(name) + '"', s)
    return m.group(1) if m else None
def g(who, op, **a):
    st, body = browser.gate(op, a, ctx, who)
    return [st, body.get("error"), body.get("approval_id"), body.get("reading"), body.get("url") or ""]
def executed_with(grant_id):
    with platform_db.connect() as c:
        return c.execute("SELECT count(*) AS n FROM actions WHERE grant_id=%s AND outcome='executed'",
                         (grant_id,)).fetchone()["n"]
# The rule itself.
cases = [
    ("click", {"role": "link", "tag": "a", "name": "Next", "href": "x"}, "", "pagination"),
    ("click", {"role": "link", "tag": "a", "name": "3 Maple Ln", "href": SITE + "/l/3", "link_site": K}, "", "link"),
    ("click", {"role": "link", "tag": "a", "name": "Elsewhere", "href": "https://b.example/", "link_site": "b.example"}, "", None),
    ("click", {"role": "link", "tag": "a", "name": "Mail us", "href": "mailto:a@b.example"}, "", None),
    ("click", {"role": "link", "tag": "a", "name": "Brochure", "href": SITE + "/b.pdf", "link_site": K, "download": True}, "", None),
    ("click", {"role": "link", "tag": "a", "name": "Contact agent", "href": SITE + "/c", "link_site": K}, "", None),
    ("click", {"role": "link", "tag": "a", "name": "Save search", "href": SITE + "/s", "link_site": K}, "", None),
    ("click", {"role": "tab", "tag": "div", "name": "Sold"}, "", "tab"),
    ("click", {"role": "columnheader", "tag": "th", "name": "Sold Price"}, "", "sort"),
    ("click", {"role": "button", "tag": "button", "name": "Search"}, "", "search"),
    ("click", {"role": "button", "tag": "button", "name": "Send message"}, "", None),
    ("click", {"role": "button", "tag": "button", "name": "Sign in"}, "", None),
    ("click", {"role": "button", "tag": "button", "name": "Schedule a tour"}, "", None),
    ("click", {"role": "checkbox", "tag": "input", "name": "Sold", "input_type": "checkbox"}, "", None),
    ("type", {"role": "searchbox", "tag": "input", "input_type": "search", "name": "Search listings"}, "cypress", "search"),
    ("type", {"role": "textbox", "tag": "input", "input_type": "text", "name": "Min price", "form_method": "post"}, "300000", "search"),
    ("type", {"role": "textbox", "tag": "input", "input_type": "text", "name": "Zip code"}, "77002", "search"),
    ("type", {"role": "textbox", "tag": "input", "input_type": "email", "name": "Email"}, "x", None),
    ("type", {"role": "textbox", "tag": "input", "input_type": "text", "name": "Your name"}, "Omar", None),
    ("type", {"role": "textbox", "tag": "input", "input_type": "text", "name": "Phone"}, "555", None),
    ("type", {"role": "textbox", "tag": "input", "input_type": "text", "name": "Verification code"}, "123456", None),
    ("type", {"role": "textbox", "tag": "input", "input_type": "text", "name": "Enter the code we sent"}, "123456", None),
    ("type", {"role": "textbox", "tag": "textarea", "name": "Search notes"}, "x", None),
    ("type", {"role": "textbox", "tag": "input", "input_type": "text", "name": "Send to"}, "x", None),
    ("type", {"role": "searchbox", "tag": "input", "input_type": "search", "name": "Search"}, "a@b.example", None),
    ("type", {"role": "textbox", "tag": "input", "input_type": "password", "name": "Search"}, "x", None),
    ("type", {"role": "textbox", "tag": "input", "input_type": "text", "name": "Comments"}, "x", None),
    ("press", {"role": "", "name": ""}, "", None),
]
out["rule"] = [[op, d.get("name"), want, bp.read_only_gesture(op, d, t, False, K)] for op, d, t, want in cases]
browser.call("close", "omar"); browser.call("close", "dana")
for who in ("omar", "dana"):
    for gr in store.active_grants(who, browser.READ_CONNECTOR, "jarvis"):
        if gr.destination == f"@{K}":
            store.revoke_grant(gr.id, by="verify", reason="verify start")
out["open"] = g("omar", "open", url=SITE + "/list")
s = snap("omar")
out["ask1"] = g("omar", "click", element=ref(s, "link", "Next"))
out["ask2"] = g("omar", "click", element=ref(s, "link", "Next"))
aid = out["ask1"][2]
row = store.get_approval(aid) if aid else None
out["card"] = {"type": (row or {}).get("action_json", {}).get("type"), "scopes": approvals._scopes_for(row) if row else [],
               "default": approvals.default_scope(aid) if aid else None,
               "display": sorted(browser.approval_display(row["action_json"]) or {}) if row else []}
def tryd(**kw):
    try:
        approvals.decide(aid, "approved", **kw); return "ok"
    except (PermissionError, ValueError, LookupError) as exc:
        return type(exc).__name__
out["dana_decides"] = tryd(decided_by="dana", scope="always", role="admin")
out["owner_decides"] = tryd(decided_by="dana", scope="always", role="owner")
out["once_scope"] = tryd(decided_by="omar", scope="once", role="member")
out["approve_always"] = tryd(decided_by="omar", scope="always", role="member")
rg = browser._read_grant(K, "omar")
out["canary_active_grant"] = browser._active_grant(K, "omar") is None
out["read_grant"] = [rg.scope, rg.expires_at is None] if rg else None
c0 = threads.bump_counter("browser_read_gesture", 0)
s = snap("omar")
out["next"] = g("omar", "click", element=ref(s, "link", "Next"))
s = snap("omar")
out["search"] = g("omar", "type", element=ref(s, "searchbox", "Search listings"), text="cypress", submit=True)
out["ledger_with_grant"] = executed_with(rg.id) if rg else 0
out["gesture_delta"] = threads.bump_counter("browser_read_gesture", 0) - c0
s = snap("omar")
held = {}
held["email"] = g("omar", "type", element=ref(s, "textbox", "Email"), text="me@x.example")
held["textarea"] = g("omar", "type", element=ref(s, "textbox", "Message"), text="hello")
held["contact"] = g("omar", "click", element=ref(s, "link", "Contact agent"))
held["send"] = g("omar", "click", element=ref(s, "button", "Send message"))
held["other_site"] = g("omar", "click", element=ref(s, "link", "Other site"))
held["buy"] = g("omar", "click", element=ref(s, "button", "Buy now"))
out["held"] = held
out["dana_open"] = g("dana", "open", url=SITE + "/list")
sd = snap("dana")
out["dana_next"] = g("dana", "click", element=ref(sd, "link", "Next"))
out["revoked"] = browser.revoke_reading("omar", K, "omar", "verify")
s = snap("omar")
out["after_revoke"] = g("omar", "click", element=ref(s, "link", "Next"))
aid2 = out["after_revoke"][2]
if aid2:
    approvals.decide(aid2, "approved", "omar", scope=approvals.default_scope(aid2), role="member")
rg2 = browser._read_grant(K, "omar")
out["plain_approve"] = [rg2.scope, rg2.expires_at is not None] if rg2 else None
s = snap("omar")
out["next_again"] = g("omar", "click", element=ref(s, "link", "Next"))
out["k"] = K
print("RESULT " + json.dumps(out, default=str))
'''


def main() -> int:
    for k in ("AUTH_BASE", "APP_PORT"):
        if os.environ.get(k, "").endswith(("8700", "8710")):
            sys.exit("REFUSING: live or testco port")
    subprocess.run(["rm", "-rf", str(SCR)], check=True)
    SCR.mkdir(parents=True)
    ip1, ip2 = (f"127.{secrets.randbelow(250) + 3}.0.{n}" for n in (31, 32))
    other = http.server.ThreadingHTTPServer((ip2, 0), h.Site)
    OTHER["url"] = f"http://{ip2}:{other.server_address[1]}"
    srv = http.server.ThreadingHTTPServer((ip1, 0), Listings)
    site = f"http://{ip1}:{srv.server_address[1]}"
    for s in (srv, other):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    base = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC", "DISPLAY"))}
    eg = subprocess.Popen([sys.executable, "-I", str(ROOT / "app" / "browser_egress.py")],
                          env={**base, "JARVIS_EGRESS_LISTEN": str(SCR / "egress.sock"),
                               "JARVIS_EGRESS_TEST_ALLOW": f"{site.split('//')[1]},{OTHER['url'].split('//')[1]}"},
                          stdout=open(SCR / "egress.log", "w"), stderr=subprocess.STDOUT)
    h.wait_for(SCR / "egress.sock")
    w = subprocess.Popen([sys.executable, "-I", str(ROOT / "app" / "browser_worker.py")],
                         env={**base, "JARVIS_BROWSER_BIN": HEADLESS, "JARVIS_BROWSER_PROFILE": str(SCR / "profiles"),
                              "JARVIS_EGRESS_SOCKET": str(SCR / "egress.sock"),
                              "JARVIS_BROWSER_LISTEN": str(SCR / "w.sock"), "JARVIS_BROWSER_TEST": "1",
                              "JARVIS_BROWSER_MAX_OPEN": "3"},
                         stdout=open(SCR / "w.log", "w"), stderr=subprocess.STDOUT)
    h.wait_for(SCR / "w.sock")
    try:
        env = dict(base)
        env.update({
            "APP_ROOT": str(ROOT), "APP_GOOGLE_TOKEN_KEY": (SB / "token_key").read_text().strip(),
            "APP_SESSION_SECRET": "verify-read-" + "x" * 32,
            "HERMES_HOME": str(SB / "hermes"), "JARVIS_APP_ROOT": str(ROOT),
            "JARVIS_SKILLS_ROOT": "/opt/jarvis-verify-fixture/skills/openclaw-imports",
            "JARVIS_RUNTIME_CONFIG": str(SB / "runtime.json"),
            "JARVIS_IDENTITY_CONFIG": "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json",
            "JARVIS_ACCOUNTS_DB": str(SB / "accounts.db"), "JARVIS_THREADS_DB": str(SB / "threads.db"),
            "JARVIS_TASKS_DB": str(SB / "tasks.db"), "JARVIS_LEADS_DB": str(SB / "leads.db"),
            "JARVIS_ACTIVITY_FEED": str(SB / "feed.jsonl"), "JARVIS_PG_DSN": (SB / "pg_dsn").read_text().strip(),
            "JARVIS_SWITCH_BROKER": "on", "JARVIS_SWITCH_SENTINEL": "on", "JARVIS_SENTINEL_MODE": "enforce",
            "JARVIS_ALLOW_WRITES": "1", "JARVIS_BROWSER": "on", "JARVIS_BROWSER_SOCKET": str(SCR / "w.sock"),
            "SITE": site, "CANARY": "1" if CANARY else "0"})
        p = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=300)
        line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
        if not line:
            print(p.stderr[-3000:])
            h.ck("child ran", False)
            return 1
        o = json.loads(line[-1][7:])
    finally:
        w.terminate()
        eg.terminate()
        w.wait(20)
        srv.shutdown()
        other.shutdown()

    print("the rule (facts in, kind or nothing out):")
    for op, name, want, got in o["rule"]:
        h.ck(f"{op} {name!r} -> {want}", got == want, got)
    print("asking:")
    h.ck("opening the page needs nothing", o["open"][0] == 200, o["open"])
    h.ck("following a link with no allowance files one browser_read card", o["ask1"][:2] == [403, "approval_required"]
         and o["ask1"][3] == "pagination" and o["card"]["type"] == "browser_read", o["ask1"])
    h.ck("   asking again reuses it", o["ask2"][2] == o["ask1"][2], o["ask2"])
    h.ck("   it offers 30 minutes and always; a plain tap means 30 minutes", o["card"]["scopes"] == ["until", "always"]
         and o["card"]["default"] == "until", o["card"])
    h.ck("   the card says what it covers", "access" in o["card"]["display"], o["card"]["display"])
    h.ck("   an admin cannot even see omar's card; the owner sees it and cannot approve it",
         o["dana_decides"] == "LookupError" and o["owner_decides"] == "PermissionError",
         [o["dana_decides"], o["owner_decides"]])
    h.ck("   'once' is not offered", o["once_scope"] == "ValueError", o["once_scope"])
    h.ck("   omar approves 'always'", o["approve_always"] == "ok" and o["read_grant"] == ["always", True], o["read_grant"])
    print("canary: the read allowance is not the acting grant")
    h.ck("_active_grant() finds nothing for the site", o["canary_active_grant"] is True)
    h.ck("   presence twin: the read lookup finds the always grant", o["read_grant"] is not None)
    print("allowed:")
    h.ck("following the link runs with no new approval", o["next"][0] == 200 and "page=2" in o["next"][4], o["next"])
    h.ck("submitting the search runs too", o["search"][0] == 200 and "q=cypress" in o["search"][4], o["search"])
    h.ck("   both ledger rows carry the read grant's id", o["ledger_with_grant"] == 2, o["ledger_with_grant"])
    h.ck("   counted (+2)", o["gesture_delta"] == 2, o["gesture_delta"])
    print("still held:")
    for k, label in (("email", "typing into an email field"), ("textarea", "typing into a text area"),
                     ("contact", "a 'Contact agent' link"), ("send", "a posting form's 'Send message'"),
                     ("other_site", "a link to another site")):
        h.ck(label, o["held"][k][:2] == [403, "needs_task_approval"], o["held"][k])
    h.ck("a risky button is held one at a time", o["held"]["buy"][:2] == [403, "approval_required"]
         and o["held"]["buy"][3] is None, o["held"]["buy"])
    print("per person, and revoking:")
    h.ck("omar's allowance does nothing for dana", o["dana_open"][0] == 200 and o["dana_next"][:2] ==
         [403, "approval_required"] and o["dana_next"][2] != o["ask1"][2], o["dana_next"])
    h.ck("revoking ends it (one grant)", o["revoked"] == 1, o["revoked"])
    h.ck("   the same link click asks again, a new card", o["after_revoke"][:2] == [403, "approval_required"]
         and o["after_revoke"][2] not in (None, o["ask1"][2]), o["after_revoke"])
    h.ck("   a plain approve gives 30 minutes, not always", o["plain_approve"] == ["until", True], o["plain_approve"])
    h.ck("   and the click runs", o["next_again"][0] == 200, o["next_again"])
    print(f"{sum(h.ok)}/{len(h.ok)} passed" + (" (canary: held and canary checks MUST fail)" if CANARY else ""))
    return 0 if all(h.ok) else 1


if __name__ == "__main__":
    sys.exit(main())
