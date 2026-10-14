#!/usr/bin/env python3
"""Sentinel's deterministic gate (app/sentinel.py), offline. Each refusal has
a twin showing the same action passes without the refusing condition.

    python3 docs/sentinel/verify_sentinel_gate.py
"""
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import sentinel as s  # noqa: E402

results = []


def ck(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f" — {detail}"))


ORG = ["acme.example"]
NOW = s.utcnow()


def act(cls, dests=(), tainted=False, **kw):
    base = dict(connector="gmail", method="users.messages.send", cls=cls, destinations=tuple(dests),
                skill="showing-pipeline", user="eddie", run_id="run-1", session_id="sess-1",
                task_id="task-1", tainted=tainted)
    base.update(kw)
    return s.Action(**base)


def grant(**kw):
    g = dict(id="g1", user="eddie", connector="gmail", skill="showing-pipeline",
             destination="buyer@client.example", scope=s.SCOPE_ALWAYS)
    g.update(kw)
    return s.Grant(**g)


def verdict(a, mode=None, **kw):
    return s.decide(a, mode=mode or s.effective_mode(a.cls), now=kw.pop("now", NOW), **kw).verdict


def main() -> int:
    print("── 9.1 classification")
    c = s.classify_google
    ck("gmail messages.get is read", c("gmail", "users.messages.get", {}, ORG)[0] == s.READ)
    ck("send to the org only is internal_send",
       c("gmail", "users.messages.send", {}, ORG, ["pat@acme.example"])[0] == s.INTERNAL_SEND)
    ck("send with one outside recipient is external_send",
       c("gmail", "users.messages.send", {}, ORG, ["pat@acme.example", "x@client.example"]) ==
       (s.EXTERNAL_SEND, ("pat@acme.example", "x@client.example")))
    ck("send with no parseable recipient is locked", c("gmail", "users.messages.send", {}, ORG, [])[0] == s.LOCKED)
    ck("calendar insert with no attendees is internal_write",
       c("calendar", "events.insert", {"body": {}}, ORG)[0] == s.INTERNAL_WRITE)
    ext = {"body": {"attendees": [{"email": "x@client.example"}]}, "sendUpdates": "all"}
    ck("calendar insert that invites an outsider is external_send",
       c("calendar", "events.insert", ext, ORG) == (s.EXTERNAL_SEND, ("x@client.example",)))
    ck("…twin: the same invite with sendUpdates=none notifies nobody (internal_write)",
       c("calendar", "events.insert", {**ext, "sendUpdates": "none"}, ORG)[0] == s.INTERNAL_WRITE)
    ck("calendar delete is destructive", c("calendar", "events.delete", {}, ORG)[0] == s.DESTRUCTIVE)
    ck("an unknown method is locked", c("gmail", "users.settings.delegates.create", {}, ORG)[0] == s.LOCKED)
    ck("recipient parsing reads To, Cc and Bcc",
       s.recipients_from_headers({"To": "A <a@x.example>", "Cc": "b@y.example", "Bcc": "c@z.example"})
       == ("a@x.example", "b@y.example", "c@z.example"))
    ck("an empty Cc does not erase the To addresses",
       s.parse_recipients(["a@x.example", "", "  "]) == ("a@x.example",))
    ck("a malformed recipient header is refused, not dropped",
       _raises(lambda: s.parse_recipients(["a@x.example", "not an address"])))

    print("── 9.1/9.2 modes")
    ck("defaults: external_send approve, internal_send auto",
       s.effective_mode(s.EXTERNAL_SEND) == s.APPROVE and s.effective_mode(s.INTERNAL_SEND) == s.AUTO)
    ck("user override loosens external_send to auto", s.effective_mode(s.EXTERNAL_SEND, s.AUTO) == s.AUTO)
    ck("org ceiling wins over a looser user override",
       s.effective_mode(s.EXTERNAL_SEND, s.AUTO, org_ceiling=s.APPROVE) == s.APPROVE)
    ck("…twin: a stricter user choice survives a looser ceiling",
       s.effective_mode(s.INTERNAL_SEND, s.BLOCK, org_ceiling=s.APPROVE) == s.BLOCK)
    ck("locked ignores a user override of auto", s.effective_mode(s.LOCKED, s.AUTO) == s.APPROVE)
    ck("…but the org can block locked", s.effective_mode(s.LOCKED, None, s.BLOCK) == s.BLOCK)
    ck("unknown class → block", s.effective_mode("teleport") == s.BLOCK)

    print("── 9.4 deterministic verdicts")
    ck("read → allow", verdict(act(s.READ, method="users.messages.get")) == s.ALLOW)
    ck("internal_send, clean run → allow", verdict(act(s.INTERNAL_SEND, ["pat@acme.example"])) == s.ALLOW)
    ck("external_send default → ask", verdict(act(s.EXTERNAL_SEND, ["buyer@client.example"])) == s.ASK)
    ck("external_send with a matching grant → allow",
       verdict(act(s.EXTERNAL_SEND, ["buyer@client.example"]), grants=[grant()]) == s.ALLOW)
    ck("locked → ask even with a grant",
       verdict(act(s.LOCKED, ["buyer@client.example"]), grants=[grant()]) == s.ASK)
    ck("blocked mode → deny", verdict(act(s.INTERNAL_SEND, ["pat@acme.example"]), mode=s.BLOCK) == s.DENY)
    ck("boundary refusal → deny even when auto",
       verdict(act(s.INTERNAL_SEND, ["pat@acme.example"]), boundary_refusal="outside suffix") == s.DENY)
    ck("unknown class → deny", verdict(act("teleport"), mode=s.AUTO) == s.DENY)

    print("── 9.4 check 4: rate limits")
    a = act(s.EXTERNAL_SEND, ["buyer@client.example"])
    ck("20 external sends this hour → deny, even with a grant",
       verdict(a, grants=[grant()], rates=s.Rates(external_sends_last_hour=20)) == s.DENY)
    ck("…twin: 19 → allow with the grant",
       verdict(a, grants=[grant()], rates=s.Rates(external_sends_last_hour=19)) == s.ALLOW)
    ck("3 sends to this recipient today → deny",
       verdict(a, grants=[grant()], rates=s.Rates(per_recipient_today={"buyer@client.example": 3})) == s.DENY)
    ck("…twin: 3 to someone else does not count",
       verdict(a, grants=[grant()], rates=s.Rates(per_recipient_today={"other@client.example": 3})) == s.ALLOW)

    print("── 9.5 taint")
    auto_ext = act(s.EXTERNAL_SEND, ["buyer@client.example"], tainted=True)
    ck("tainted run, external_send set to auto → ask", verdict(auto_ext, mode=s.AUTO) == s.ASK)
    ck("…twin: clean run, same action → allow", verdict(act(s.EXTERNAL_SEND, ["buyer@client.example"]),
                                                       mode=s.AUTO) == s.ALLOW)
    ck("tainted run, covered by a grant for that destination and skill → allow",
       verdict(auto_ext, mode=s.AUTO, grants=[grant()]) == s.ALLOW)
    ck("tainted run, grant for a different skill → ask",
       verdict(auto_ext, mode=s.AUTO, grants=[grant(skill="daily-digest")]) == s.ASK)
    ck("tainted run, destructive on auto → ask",
       verdict(act(s.DESTRUCTIVE, ["primary"], tainted=True, connector="calendar"), mode=s.AUTO) == s.ASK)
    ck("tainted run, internal_send stays auto",
       verdict(act(s.INTERNAL_SEND, ["pat@acme.example"], tainted=True)) == s.ALLOW)
    ck("the Action default is tainted (no run identity fails closed)", s.Action("gmail", "m", s.READ).tainted)
    ck("email from outside the org is untrusted",
       s.untrusted_sender([{"name": "From", "value": "Lead <lead@client.example>"}], ORG))
    ck("…twin: email from the org is not", not s.untrusted_sender(
        [{"name": "From", "value": "Pat <pat@acme.example>"}], ORG))
    ck("no From header is untrusted", s.untrusted_sender([], ORG))

    print("── 9.3 grant scope and binding")
    a = act(s.EXTERNAL_SEND, ["buyer@client.example"])
    cov = lambda g, x=a, now=NOW: s.grant_covers(g, x, now)  # noqa: E731
    ck("grant for one address does not cover another address",
       not cov(grant(), act(s.EXTERNAL_SEND, ["other@client.example"])))
    ck("domain grant covers an address in that domain", cov(grant(destination="@client.example")))
    ck("…but not a send that also goes outside it",
       not cov(grant(destination="@client.example"),
               act(s.EXTERNAL_SEND, ["buyer@client.example", "x@else.example"])))
    ck("grant for another connector does not cover", not cov(grant(connector="calendar")))
    ck("grant for another user does not cover", not cov(grant(user="blake")))
    ck("once: covers until used", cov(grant(scope=s.SCOPE_ONCE)) and not cov(grant(scope=s.SCOPE_ONCE, used=True)))
    ck("session: same session only", cov(grant(scope=s.SCOPE_SESSION, session_id="sess-1"))
       and not cov(grant(scope=s.SCOPE_SESSION, session_id="sess-2")))
    ck("task: same task only", cov(grant(scope=s.SCOPE_TASK, task_id="task-1"))
       and not cov(grant(scope=s.SCOPE_TASK, task_id="task-9")))
    ck("until: covers before expiry, not after",
       cov(grant(scope=s.SCOPE_UNTIL, expires_at=NOW + timedelta(hours=1)))
       and not cov(grant(scope=s.SCOPE_UNTIL, expires_at=NOW - timedelta(seconds=1))))
    ck("until with no expiry covers nothing", not cov(grant(scope=s.SCOPE_UNTIL)))
    g = s.grant_from_approval("ap1", a, s.SCOPE_SESSION, NOW)
    ck("an approval's grant binds its connector, skill, destination and session",
       (g.connector, g.skill, g.destination, g.session_id) ==
       ("gmail", "showing-pipeline", "buyer@client.example", "sess-1"))
    for bad, why in ((lambda: s.grant_from_approval("x", act(s.EXTERNAL_SEND, ["a@b.example", "c@d.example"]),
                                                    s.SCOPE_ALWAYS, NOW), "two destinations"),
                     (lambda: s.grant_from_approval("x", act(s.LOCKED, ["a@b.example"]), s.SCOPE_ALWAYS, NOW),
                      "locked beyond once"),
                     (lambda: s.grant_from_approval("x", a, s.SCOPE_UNTIL, NOW), "until without duration")):
        try:
            bad()
            ck(f"grant refused: {why}", False, "no error")
        except ValueError:
            ck(f"grant refused: {why}", True)

    print("── 9.3 signed structured callbacks")
    key = b"k" * 32
    exp = NOW + timedelta(hours=24)
    tok = s.sign_callback(key, approval_id="ap1", user="eddie", answer="approve", scope="once", expires_at=exp)
    ck("a signed callback verifies for its user",
       s.verify_callback(key, tok, now=NOW, user="eddie")["answer"] == "approve")

    def refused(token, user="eddie", now=NOW, k=key):
        try:
            s.verify_callback(k, token, now=now, user=user)
            return False
        except s.CallbackRefused:
            return True
    ver, body, mac = tok.split(".")
    forged = s.sign_callback(key, approval_id="ap1", user="eddie", answer="reject", scope="once",
                             expires_at=exp).split(".")[1]
    ck("free text 'yes' is refused", refused("yes"))
    ck("a body swapped under the old signature is refused", refused(f"{ver}.{forged}.{mac}"))
    ck("a token signed with another key is refused", refused(tok, k=b"x" * 32))
    ck("a token delivered by another user's session is refused", refused(tok, user="blake"))
    ck("an expired token is refused", refused(tok, now=exp + timedelta(seconds=1)))
    ck("sign refuses an answer that is not approve/reject", _raises(lambda: s.sign_callback(
        key, approval_id="a", user="u", answer="yes", scope="once", expires_at=exp)))

    print("── ledger summary carries structure, not bodies")
    d = s.decide(act(s.EXTERNAL_SEND, ["buyer@client.example"]), mode=s.APPROVE, now=NOW)
    summ = s.summarize(act(s.EXTERNAL_SEND, ["buyer@client.example"]), d)
    ck("summary has class, destinations, verdict", (summ["class"], summ["destinations"], summ["verdict"]) ==
       (s.EXTERNAL_SEND, ["buyer@client.example"], s.ASK))
    ck("summary has no body field", not any(k in summ for k in ("body", "raw", "params")))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


def _raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


if __name__ == "__main__":
    sys.exit(main())
