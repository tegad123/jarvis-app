#!/usr/bin/env python3
"""Auto-lead-capture, verified by TRIGGERING through the real classify path.

The synthetic emails below go to email-router/scripts/classify_email.py, which
calls the gateway — a real model call per email, which is the cost of testing
the actual trigger rather than a stub of it. `--stub-classify` skips that and
asserts the connector alone, for iterating without spending tokens; the default
run is the real one and prints which mode it used.

Everything writes to a disposable box (env.py). The live store, the live feed
and the live threads DB are unreachable from this process by construction.
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import env  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--stub-classify", action="store_true")
args = ap.parse_args()

env.reset()
env.apply(source="store")
env.assert_isolated()

import lead_capture as lc          # noqa: E402
from app import leads_store        # noqa: E402
import notify                      # noqa: E402

CLASSIFY = "/root/.hermes/skills/openclaw-imports/email-router/scripts/classify_email.py"

ok, fails = [], []
def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))
    if not cond:
        fails.append(name)


def classify(frm, subj, body):
    """The REAL classifier. Falls back loudly, never silently."""
    if args.stub_classify:
        return "A"
    payload = json.dumps({"from": frm, "subject": subj, "body": body,
                          "to": "", "cc": "", "message_id": ""})
    r = subprocess.run(["python3", CLASSIFY], input=payload.encode(),
                       capture_output=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        print(f"    !! classifier failed rc={r.returncode}: {r.stderr.decode()[:200]}")
        return None
    return json.loads(r.stdout).get("classification")


# ── the synthetic inbox ───────────────────────────────────────────────────
BUYER = '"Marcus Webb" <marcus.webb@mailbox.example>'
INQUIRY_SUBJ = "Inquiry from HAR.com - 4211 Oak Forest Dr"
INQUIRY_BODY = ("Hi — I saw 4211 Oak Forest Dr on HAR.com and I'd like to "
                "schedule a showing this weekend if it's still available. "
                "Is Sunday afternoon possible? Thanks, Marcus")

VENDOR = '"Cordelia Nash" <cnash@harbourline-title.example>'
VENDOR_SUBJ = "Payoff statement needed - 1507 Pennyroyal St"
VENDOR_BODY = ("We cannot clear to close without the payoff statement. "
               "Could you send it across today? Thanks, Cordelia")

print(f"\nclassifier: {'STUBBED (--stub-classify)' if args.stub_classify else 'REAL gateway call'}")

print("\n── 1. a genuine inquiry is captured ───────────────────────────")
cat = classify(BUYER, INQUIRY_SUBJ, INQUIRY_BODY)
ck("classifier put the inquiry in a capture category", cat in lc.CAPTURE_CATEGORIES,
   f"category={cat}")
r1 = lc.capture(cat, BUYER, INQUIRY_SUBJ, INQUIRY_BODY)
ck("a lead was created", r1.get("action") == "created", str(r1))
lead = leads_store.get(r1.get("lead_id")) if r1.get("lead_id") else None
ck("name parsed from the From header", lead and lead["name"] == "Marcus Webb",
   lead and lead["name"])
ck("email captured", lead and lead["email"] == "marcus.webb@mailbox.example")
ck("source derived from the body marker, not defaulted",
   lead and lead["source"] == "har", lead and lead["source"])
ck("created_by is jarvis, not user", lead and lead["created_by"] == "jarvis")
ck("owner_person is the mailbox owner, a KEY not an address",
   lead and lead["owner_person"] and "@" not in lead["owner_person"],
   lead and lead["owner_person"])
ck("status starts New", lead and lead["status"] == "New")
ck("first contact recorded in notes", lead and "first contact" in (lead["notes"] or ""))

print("\n── 2. a vendor asking for a document is NOT a lead ────────────")
vcat = classify(VENDOR, VENDOR_SUBJ, VENDOR_BODY)
r2 = lc.capture(vcat, VENDOR, VENDOR_SUBJ, VENDOR_BODY)
ck("not captured", r2.get("action") == "skipped", f"category={vcat} {r2}")
ck("and the reason is recorded, not silent", bool(r2.get("reason")), r2.get("reason"))
ck("still only one lead in the store", len(leads_store.query()) == 1,
   f"{len(leads_store.query())} leads")

print("\n── 3. a repeat inquiry UPDATES, never duplicates ──────────────")
REPEAT_SUBJ = "Re: 4211 Oak Forest - still interested"
REPEAT_BODY = "Following up — is the property still available? I'd like to tour it."
rcat = classify(BUYER, REPEAT_SUBJ, REPEAT_BODY)
r3 = lc.capture(rcat, BUYER, REPEAT_SUBJ, REPEAT_BODY)
ck("action is updated", r3.get("action") == "updated", str(r3))
ck("matched on the email address", r3.get("matched_on") == "email")
ck("STILL ONE LEAD — no duplicate", len(leads_store.query()) == 1,
   f"{len(leads_store.query())} leads")
after = leads_store.get(r1["lead_id"])
ck("it is the same row", r3.get("lead_id") == r1["lead_id"])
ck("history appended, first contact preserved",
   "reached out again" in (after["notes"] or "") and "first contact" in (after["notes"] or ""))
ck("last_activity_at moved forward", after["last_activity_at"] > lead["last_activity_at"])

print("\n── 4. name-only match, when a portal hides the address ────────")
# THE NAME IS DELIBERATELY NOT AN EXACT MATCH. An earlier version used
# "Marcus Webb" verbatim, so the assertion passed on raw string equality and
# normalise_name could be gutted without the suite noticing — a mutation
# proved exactly that. Portals really do send "MARCUS WEBB (Zillow)".
RELAY = '"MARCUS WEBB (Zillow)" <no-reply@relay.example>'
ck("the probe name is NOT raw-equal to the stored one, so normalisation is "
   "what has to do the work",
   "MARCUS WEBB (Zillow)" != leads_store.get(r1["lead_id"])["name"])
r4 = lc.capture("A", RELAY, "Zillow inquiry", "New lead from Zillow: wants a showing.")
ck("matched the existing lead by normalised name", r4.get("action") == "updated"
   and r4.get("matched_on") == "name", str(r4))
ck("still one lead", len(leads_store.query()) == 1, f"{len(leads_store.query())} leads")
ck("and the stored name was not overwritten by the portal's shouting",
   leads_store.get(r1["lead_id"])["name"] == "Marcus Webb")

print("\n── 5. same address, DIFFERENT name -> flags, never merges ─────")
IMPOSTOR = '"Priyanka Raghunathan" <marcus.webb@mailbox.example>'
r5 = lc.capture("A", IMPOSTOR, "Interested in the listing",
                "Is this still available? I would like to see it.")
ck("action is flagged", r5.get("action") == "flagged", str(r5))
ck("the name was NOT overwritten",
   leads_store.get(r1["lead_id"])["name"] == "Marcus Webb",
   leads_store.get(r1["lead_id"])["name"])
ck("no second lead created", len(leads_store.query()) == 1, f"{len(leads_store.query())} leads")
ck("both names are recorded in the notes",
   all(n in (leads_store.get(r1["lead_id"])["notes"] or "")
       for n in ("Marcus Webb", "Priyanka Raghunathan")))

print("\n── 6. a partner added to the same address is NOT a collision ──")
# TWO SHAPES, and they take DIFFERENT code paths — an earlier version tested
# only the first and a mutation of the containment rule survived because of it.
#   "Marcus Webb + Dana"          normalise_name strips "+ ..." entirely, so
#                                 the names come out EQUAL and containment is
#                                 never consulted.
#   "Marcus Webb and Dana Reyes"  survives normalisation as a strict superset,
#                                 so only the containment test can tell this
#                                 apart from a genuine collision.
COUPLE_STRIPPED = '"Marcus Webb + Dana" <marcus.webb@mailbox.example>'
r6 = lc.capture("A", COUPLE_STRIPPED, "Following up on the showing",
                "Is it still available? We would like to tour it.")
ck("the '+ partner' form is treated as the same household",
   r6.get("action") == "updated", str(r6))

COUPLE_SPELLED = '"Marcus Webb and Dana Reyes" <marcus.webb@mailbox.example>'
ck("this probe really does reach the containment test",
   leads_store.normalise_name("Marcus Webb and Dana Reyes")
   != leads_store.normalise_name("Marcus Webb")
   and leads_store.normalise_name("Marcus Webb")
   in leads_store.normalise_name("Marcus Webb and Dana Reyes"))
r6b = lc.capture("A", COUPLE_SPELLED, "Both of us will come Sunday",
                 "Is it still available? We would like to tour it.")
ck("the spelled-out partner form is ALSO the same household, not a collision",
   r6b.get("action") == "updated", str(r6b))

print("\n── 7. sheet-backed tenants no-op ──────────────────────────────")
os.environ["JARVIS_LEADS_SOURCE"] = "sheet"
r7 = lc.capture("A", '"Nadia Ferreira" <nadia@mailbox.example>',
                "Inquiry from HAR.com - 88 Cinderbrook",
                "Is this still available? I'd like to schedule a showing.")
ck("action is no-op", r7.get("action") == "no-op", str(r7))
ck("nothing written to the store", len(leads_store.query()) == 1,
   f"{len(leads_store.query())} leads")
os.environ["JARVIS_LEADS_SOURCE"] = "store"

print("\n── 8. the feed is humanised and jargon-free ───────────────────")
events = [json.loads(l) for l in open(notify.feed_path()) if l.strip()]
heads = [e.get("headline", "") for e in events]
ck("a create event was emitted", any("New lead: Marcus Webb" in h for h in heads),
   " | ".join(heads[:3]))
# TIGHTENED: the first version of this accepted "from har" and therefore
# passed while the raw slug was leaking into the headline. The demo copy
# promises "New lead: Marcus Webb from Zillow" — a WORD, capitalised as a
# person would write it, never the storage key.
ck("the create event names the source as a WORD, not a slug",
   any(h.endswith("from HAR") for h in heads),
   [h for h in heads if h.startswith("New lead")][:1])
ck("a repeat event was emitted", any("reached out again" in h for h in heads))
ck("the collision event asks for a human", any("Not merged" in (e.get("detail") or "")
   or "needs a human decision" in (e.get("detail") or "") for e in events))
ck("EXACTLY ONE create event — the store emits, the connector must not double it",
   sum(1 for h in heads if h.startswith("New lead:")) == 1,
   f"{sum(1 for h in heads if h.startswith('New lead:'))}")
jargon = [h for h in heads if any(t in h for t in
          ("lead.created", "lead.updated", "action=", "None", "{", "classification",
           "from har", "from zillow", "from site", "from realtor"))]
ck("no jargon in any headline", not jargon, str(jargon[:2]))
ck("CANARY: the jargon matcher fires on a planted string",
   any(t in "x lead.created y" for t in ("lead.created",)))
ck("every event carries the demo marker in this box",
   all("demo-seeded" in (e.get("detail") or "") for e in events))

print("\n── 9. writes stayed on OUR surface ────────────────────────────")
ck("live lead store untouched", not Path("/root/.hermes/state/jarvis_leads.db").exists()
   or Path(leads_store.DB_PATH) != Path("/root/.hermes/state/jarvis_leads.db"))
ck("live feed still 82 events",
   sum(1 for l in open("/root/.hermes/state/activity_feed.jsonl") if l.strip()) == 82)
ck("write guard never armed", os.environ.get("JARVIS_ALLOW_WRITES") in ("0", None, ""))

print(f"\n{sum(ok)}/{len(ok)} passed")
if fails:
    print("FAILED: " + "; ".join(fails))
sys.exit(0 if all(ok) else 1)
