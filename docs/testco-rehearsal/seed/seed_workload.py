#!/usr/bin/env python3
import sys; sys.exit("RETIRED 2026-10-13: testco is retired (docs/platform/decisions.md); this script needs its running services or tree.")  # noqa: E702
"""Seed the TESTCO CLONE with a realistic synthetic workload for the tester.

WHAT "REAL EMIT PATHS" MEANS HERE, AND THE ONE PLACE IT BENDS
  Leads go through leads_store.create(), channels through
  threads.create_thread()/append_messages(), accounts through accounts.set_pin(),
  approvals through the same _append_jsonl() that approvals.seed() uses. All of
  those are the production functions, so the rows have production shape and the
  side effects (feed events on lead creation) happen for real.

  Feed events are built by notify.build_event() — the real constructor, so the
  real schema and the real demo marker — and written by notify._write_feed(),
  the real writer. The ONE deviation: `ts` is overwritten afterwards so the
  workload spans several days. notify.notify() stamps now() and takes no clock
  argument, and a feed where sixty events share one timestamp does not
  exercise the day-grouping, the "TODAY" hero, or relative times. The override
  touches `ts` and nothing else.

WHAT IS NOT SEEDED, AND WHY
  * THINKING STEPS are not storable. They are transient SSE frames
    (`hermes.tool.progress`) rendered while a reply streams; the message store
    has role/content/ts/partial/attachments and no step column. A finished
    transcript in production has no thinking steps in it either, so seeding
    them would be seeding something the product does not have. The channels
    below instead contain assistant turns that NARRATE the work, which is what
    the transcript actually looks like once the stream ends. A tester who wants
    to see thinking steps has to ask a live question — and can, the gateway is
    wired.
  * INLINE APPROVAL CARDS likewise do not exist in the transcript. Approvals
    are their own screen. What IS real is the COUPLING, and it is seeded: each
    pending approval names the deal channel it came from, and that channel's
    transcript contains the turn where Jarvis says it needs sign-off. That is
    the loop a tester should walk — read the channel, go to Approvals, decide.

Run:  python3 seed_workload.py [--wipe]
"""
import argparse
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import testco_env                      # noqa: E402
ENV = testco_env.apply()

import fixture_cast as F               # noqa: E402
from app import accounts, approvals, leads_store, threads   # noqa: E402
import notify                          # noqa: E402

RESOLVED = testco_env.assert_isolated()
RNG = random.Random(20260821)          # deterministic: a tester and I see the same box
NOW = datetime.now(timezone.utc)
TENANT = "testco"


def ago(days=0, hours=0, minutes=0) -> str:
    return (NOW - timedelta(days=days, hours=hours, minutes=minutes)).isoformat()


# The tenant's clock, not UTC. "Today" is only meaningful in a timezone, and the
# hero counts the day in whatever zone the browser reports — so a seeded event
# meant to read as "this morning" has to be placed in the TENANT'S morning, not
# N hours before a UTC now that may already be tomorrow there.
TZ = ZoneInfo("America/Chicago")


def recent(hours: float, minutes: int = 0) -> str:
    """`hours` ago — but never earlier than local midnight.

    WHY THE CLAMP. The "fresh activity" block exists so the TODAY hero is never
    empty on first open. Plain `ago(hours=5)` breaks that the moment the seeder
    runs near local midnight: at 00:54 tenant time every one of those events
    lands on YESTERDAY and the hero reads "Nothing yet today" over a full feed —
    which is exactly the flat first impression the block was written to prevent.
    Observed for real, seeding at 00:54 America/Chicago.

    Clamping to "since local midnight" keeps the events genuinely recent AND
    genuinely today, at any hour the seeder happens to be run.
    """
    local = datetime.now(TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    max_back = (local - midnight).total_seconds() / 3600.0
    # Leave a couple of minutes' margin so nothing lands exactly on midnight.
    back = min(hours, max(max_back - 0.05, 0.02))
    stamped = local - timedelta(hours=back, minutes=minutes if back == hours else 0)
    return stamped.astimezone(timezone.utc).isoformat()


def day_at(days_ago: int, hour: int, minute: int = 0) -> str:
    """A timestamp at a specific LOCAL hour, `days_ago` days back.

    Clamped to the past: seeding an event at 4pm when it is 2pm would put
    tomorrow's news in today's feed and make relative times read "in 2 hours".
    """
    local = datetime.now(TZ) - timedelta(days=days_ago)
    stamped = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if stamped > datetime.now(TZ):
        stamped -= timedelta(days=1)
    return stamped.astimezone(timezone.utc).isoformat()


def emit(destination, kind, headline, detail, *, skill, when,
         person=None, subject=None, attachments=None):
    """Real constructor + real writer, with the clock moved. See the docstring."""
    ev = notify.build_event(destination, headline, attachments=attachments, kind=kind,
                            skill=skill, tenant=TENANT, headline=headline,
                            detail=detail, person=person, subject=subject)
    ev["ts"] = when
    notify._write_feed(ev)
    return ev


# ─────────────────────────────────────────────────────────────── accounts ────

def seed_accounts():
    """One account per person in the registry. PINs are printed once, here,
    and belong in the tester handoff — they are not recoverable afterwards."""
    accounts.init()
    out = {}
    for key in F.AGENTS:
        out[key] = accounts.set_pin(key)
    return out


# ────────────────────────────────────────────────────────────────── leads ────

def seed_leads(n=40):
    """~40 leads with histories, spread across agents, statuses, temperatures.

    The distribution is deliberately uneven — a real tracker is mostly cold
    leads with a handful of hot ones, and a tester who sees an even split
    learns nothing about how the screen behaves under a realistic shape.
    """
    leads_store.init()
    weighted_status = (["New"] * 8 + ["No Response"] * 14 +
                       ["Responded"] * 12 + ["Not Interested"] * 6)
    weighted_temp = (["Hot"] * 6 + ["Warm"] * 12 + ["Cold"] * 16 + [""] * 6)

    made = []
    for i in range(n):
        name, email = F.BUYERS[i % len(F.BUYERS)]
        # Repeat buyers get distinguishable rows rather than duplicate names.
        # BY STREET, NOT BY HOUSE NUMBER. Splitting on the first token gave
        # "Bartholomew Reed (155)" — a bare number in parentheses, which reads
        # as a data-quality bug rather than as a disambiguation an agent would
        # actually write. The street name is what a person would say.
        if i >= len(F.BUYERS):
            _street = F.PROPERTIES[i % len(F.PROPERTIES)].split(None, 1)[-1]
            _street = _street.rsplit(" ", 1)[0] if " " in _street else _street
            name = f"{name} ({_street})"
        status = weighted_status[i % len(weighted_status)]
        temp = weighted_temp[i % len(weighted_temp)]
        owner = F.AGENTS[i % len(F.AGENTS)]
        age_days = RNG.randint(1, 95)
        lead = leads_store.create(
            name=name,
            created_by="jarvis" if i % 5 == 0 else "user",
            property=F.PROPERTIES[i % len(F.PROPERTIES)],
            price_point=F.PRICE_POINTS[i % len(F.PRICE_POINTS)],
            source=F.SOURCES[i % len(F.SOURCES)],
            email=email,
            phone=f"555-01{i:02d}",
            status=status,
            temperature=temp,
            follow_up=F.FOLLOW_UPS[i % len(F.FOLLOW_UPS)],
            owner_person=owner,
            notes=f"{F.SOURCES[i % len(F.SOURCES)]} — asked about {F.PROPERTIES[i % len(F.PROPERTIES)]}.",
            last_activity_at=ago(days=age_days),
            next_touch_at=ago(days=-RNG.randint(1, 21)) if status != "Not Interested" else None,
            external_ref=f"TC-{1000 + i}",
        )
        made.append(lead)
    return made


# ─────────────────────────────────────────────────────────────────── feed ────

def seed_feed(leads):
    """~60 events across every kind the UI knows, spread over eight days.

    Coverage is the point: kinds.ts styles email.classified / task.created /
    task.completed / info / warning / alert by name and prettifies anything
    else, so the set below deliberately includes an unrecognised kind
    (`deal.channel_created`) to exercise the fallback path on a real screen.
    """
    events = []
    B, V = F.BUYERS, F.VENDORS

    def A(*recs):
        return [r for r in recs if r]

    # --- email.classified across all five categories the router emits -------
    mails = [
        ("A", "high",   B[0], "Re: Sunday showing — 118 Alderwood Ln",
         "Filed an urgent email from {p} about a Sunday showing at 118 Alderwood Ln — needs a reply",
         "Buyer wants to move the showing an hour later."),
        ("C", "high",   V[0], "Survey discrepancy at 2204 Kestrel Row — need seller decision",
         "Filed an urgent email from {p} about a survey discrepancy at 2204 Kestrel Row — needs you to act",
         "Title flagged an encroachment; needs an owner decision on the credit."),
        ("B", "high",   V[0], "Closing @ 10:00 Thu — 77 Bellhaven Ct",
         "Filed an email from {p} about closing at 10:00 Thursday, 77 Bellhaven Ct — a date for your calendar",
         "Closing time confirmed by all parties; calendaring only."),
        ("A", "high",   V[1], "Rate lock expires Friday — 915 Thistledown Way",
         "Filed an urgent email from {p} about the rate lock expiring Friday on 915 Thistledown Way — needs a reply",
         "Lender needs the signed disclosure before the lock lapses."),
        ("C", "medium", V[2], "Inspection report — 3310 Copperbeam Dr",
         "Filed an email from {p} with the inspection report for 3310 Copperbeam Dr — needs you to act",
         "Four items flagged; two are likely repair requests."),
        ("G", "medium", B[1], "Question about the Marlowe Cross listing",
         "Filed an email from {p} asking about 46 Marlowe Cross — asked for your help",
         "Prospective buyer asking about availability and HOA dues."),
        ("B", "medium", V[3], "Appraisal scheduled Tue 2pm — 1801 Fennimore St",
         "Filed an email from {p} about the appraisal on Tuesday at 2pm, 1801 Fennimore St — a date for your calendar",
         "Appraiser confirmed the window."),
        ("A", "high",   B[2], "Offer questions — 620 Quillon Ave",
         "Filed an urgent email from {p} with offer questions on 620 Quillon Ave — needs a reply",
         "Buyer asking whether the seller will cover closing costs."),
        ("G", "medium", V[4], "Photo shoot availability — 254 Harrowgate Pl",
         "Filed an email from {p} about photo shoot availability for 254 Harrowgate Pl — asked for your help",
         "Photographer offering three windows this week."),
        ("D", "medium", None, "Weekly traffic — 1129 Sablewood Rd + 88 Cinderbrook Ln",
         "Filed a weekly traffic report for 1129 Sablewood Rd and 88 Cinderbrook Ln — filed for reference",
         "Automated listing traffic summary. No response required."),
        ("A", "high",   B[3], "Re: counteroffer on 402 Larkfield Ter",
         "Filed an urgent email from {p} about the counteroffer on 402 Larkfield Ter — needs a reply",
         "Buyer countered $8K under and asked for the fridge."),
        ("C", "high",   V[0], "Payoff statement missing — 1507 Pennyroyal St",
         "Filed an urgent email from {p} about a missing payoff statement for 1507 Pennyroyal St — needs you to act",
         "Title cannot clear to close without it."),
        ("B", "medium", V[2], "Re-inspection Wed 9:00 — 39 Windover Gate",
         "Filed an email from {p} about a re-inspection Wednesday at 9:00, 39 Windover Gate — a date for your calendar",
         "Re-inspection window confirmed."),
        ("D", "low",    None, "Market snapshot — week of the 14th",
         "Filed the weekly market snapshot — filed for reference",
         "Automated market summary. No response required."),
        ("G", "medium", B[4], "Relocation timeline — 760 Ashgrove Blvd",
         "Filed an email from {p} about a relocation timeline for 760 Ashgrove Blvd — asked for your help",
         "Buyer relocating in November, wants to understand the sequence."),
        ("A", "high",   B[5], "Second showing request — 2841 Verity Ln",
         "Filed an urgent email from {p} asking for a second showing at 2841 Verity Ln — needs a reply",
         "Buyer wants to bring a contractor."),
        ("C", "medium", V[1], "Condition list — 155 Drummond Walk",
         "Filed an email from {p} with underwriting conditions for 155 Drummond Walk — needs you to act",
         "Three conditions outstanding before clear-to-close."),
        ("D", "low",    None, "Weekly traffic — 918 Ravenscourt Rd",
         "Filed a weekly traffic report for 918 Ravenscourt Rd — filed for reference",
         "Automated listing traffic summary."),
    ]
    for i, (cat, conf, who, subject, head_t, why) in enumerate(mails):
        if who:
            pname, pmail = who[0], who[1]
            frm = f"{pname} <{pmail}>"
        else:
            pname, frm = None, f"{F.AUTOMATED[1]} <{F.AUTOMATED[0]}>"
        events.append(emit(
            "emails", "email.classified",
            head_t.format(p=pname or "an automated sender"),
            f"Category {cat} · confidence {conf} · from {frm} · subject: {subject} · {why}",
            skill="email-router", when=day_at(i // 3, 7 + (i * 3) % 11, (i * 17) % 60),
            person=pname, subject=subject,
            attachments=A({"type": "email", "message_id": f"msg-tc-{2000+i}",
                           "subject": subject, "from": frm,
                           "classification": cat, "confidence": conf,
                           "reasoning": why})))

    # --- tasks: created + completed, ASSIGNED so W3 feed scoping bites ------
    tasks = [
        ("Call {b} back about the Sunday showing", "high",  "dana",  0),
        ("Get the seller's decision on the survey credit", "high", "dana", 0),
        ("Send the signed disclosure to Piedmont", "high", "omar", 1),
        ("Order the re-inspection for 39 Windover Gate", "medium", "priya", 1),
        ("Chase the payoff statement from Harbourline", "high", "omar", 2),
        ("Book the Harrowgate photo shoot", "medium", "priya", 2),
        ("Draft the counteroffer response for 402 Larkfield", "high", "dana", 3),
        ("Pull comps for 760 Ashgrove Blvd", "medium", "priya", 4),
        ("Confirm the Bellhaven closing time with all parties", "medium", "omar", 4),
        ("Clear the three underwriting conditions on Drummond", "high", "omar", 5),
        ("Follow up with {b} on the second showing", "medium", "dana", 5),
        ("Update the seller on Copperbeam inspection items", "high", "priya", 6),
    ]
    for i, (summary, prio, owner, day) in enumerate(tasks):
        b = F.BUYERS[i % len(F.BUYERS)][0]
        text = summary.format(b=b)
        tid = f"TASK-TC-{3000+i}"
        who = F.AGENT_NAME[owner]
        events.append(emit(
            "tasks", "task.created",
            f"Created a task: {text} — for {who}"
            + (", urgent" if prio == "high" else ""),
            f"{tid} · priority {prio} · assignee {who} · source email",
            skill="task-manager", when=day_at(day, 8 + (i * 2) % 9, (i * 23) % 60),
            attachments=A({"type": "task", "task_id": tid, "title": text,
                           "assignee": f"{owner}@testco.example.com",
                           "assignee_name": who, "priority": prio,
                           "project": "Deals", "source": "email"})))
        if i % 3 == 0:
            events.append(emit(
                "tasks", "task.completed", f"Completed: {text}",
                f"{tid} · completed {ago(days=max(day - 1, 0))[:10]}",
                skill="task-manager", when=day_at(max(day - 1, 0), 9 + (i * 5) % 8, (i * 31) % 60),
                attachments=A({"type": "task", "task_id": tid, "title": text,
                               "assignee": f"{owner}@testco.example.com",
                               "assignee_name": who,
                               "completed_at": ago(days=max(day - 1, 0))})))

    # --- ops: info / warning / alert, and an UNRECOGNISED kind -------------
    ops = [
        ("info", "Ran the morning inbox pass — 14 emails filed, 3 needed you",
         "email-router · 14 classified · 3 category A/C", "email-router", 0, 6),
        ("info", "Sent the 5am task digest to the team",
         "task-manager · 9 open tasks across 3 people", "task-manager", 1, 5),
        ("warning", "Gmail push token expires in 5 days",
         "gmail-push-trigger · renew before the watch lapses or mail stops arriving",
         "gmail-push-trigger", 2, 9),
        ("warning", "Two leads have gone 30 days without a touch",
         "leads · 30-day threshold · owners notified", "leads", 3, 11),
        ("alert", "Could not reach the calendar API on the 4pm sweep",
         "google-calendar · HTTP 503 · retried 3 times · next sweep in 1 hour",
         "google-calendar", 4, 15),
        ("info", "Spend is tracking at $6.10 this month",
         "cost-guard · warn at $20 · pause at $30", "cost-guard", 5, 7),
        ("warning", "An inspection deadline lands tomorrow with no report filed",
         "email-router · 3310 Copperbeam Dr · option period ends", "email-router", 1, 13),
        ("info", "Weekly summary: 4 active deals, 40 leads, 12 open tasks",
         "ops · weekly rollup", "task-manager", 6, 8),
    ]
    for kind, head, detail, skill, day, hour in ops:
        events.append(emit("ops", kind, head, detail, skill=skill,
                           when=day_at(day, hour)))

    # --- FRESH ACTIVITY, in the last few hours ----------------------------
    #
    # WHY HOURS-AGO AND NOT day_at(0, ...). day_at places an event at a local
    # hour in the TENANT'S zone, which is right for "the 8am digest". But the
    # hero counts "today" in the BROWSER'S zone, and a tester's browser may be
    # anywhere — a headless run in UTC read "Nothing yet today" over a feed
    # full of events, because Chicago-morning events are the previous UTC day.
    # Events a couple of hours old are today in essentially every zone, so
    # these are what guarantee the hero is never empty on first open.
    fresh = [
        ("emails", "email.classified",
         f"Filed an urgent email from {B[0][0]} about the Sunday showing at "
         "118 Alderwood Ln — needs a reply",
         f"Category A · confidence high · from {B[0][0]} <{B[0][1]}> · "
         "subject: Re: Sunday showing — 118 Alderwood Ln · Buyer confirming the new time.",
         "email-router", 1, 10),
        ("tasks", "task.created",
         "Created a task: Confirm the new showing time with the listing agent — for Dana",
         "TASK-TC-3100 · priority medium · assignee Dana · source email",
         "task-manager", 1, 40),
        ("emails", "email.classified",
         f"Filed an email from {V[3][0]} about the appraisal result for 2204 Kestrel Row "
         "— filed for reference",
         f"Category D · confidence high · from {V[3][0]} <{V[3][1]}> · "
         "subject: Appraisal complete — 2204 Kestrel Row · Came in at value.",
         "email-router", 2, 15),
        ("ops", "info", "Ran the inbox pass — 6 emails filed, 1 needed you",
         "email-router · 6 classified · 1 category A", "email-router", 3, 5),
        ("tasks", "task.completed", "Completed: Book the Harrowgate photo shoot",
         "TASK-TC-3005 · completed today", "task-manager", 4, 20),
        ("ops", "warning", "The Copperbeam option period ends tomorrow",
         "email-router · 3310 Copperbeam Dr · repair request still unsent",
         "email-router", 5, 30),
    ]
    for dest, kind, head, det, skill, h, m in fresh:
        events.append(emit(dest, kind, head, det, skill=skill,
                           when=recent(h, m)))

    # Unrecognised kind, on purpose — proves prettifyKind() on a real screen.
    for day, addr in ((7, "118 Alderwood Ln"), (6, "2204 Kestrel Row")):
        events.append(emit(
            "deals", "deal.channel_created",
            f"Opened a channel for {addr} — the emails, tasks and dates for it now live in one place",
            f"deals · {addr} · opened automatically after 3 related emails",
            skill="email-router", when=day_at(day, 6, 40)))

    # NOTE: no lead.created events are emitted here. leads_store.create()
    # already emits one per lead through the production path — that is the
    # whole point of seeding through the real store — and adding a second would
    # be a fiction the product never produces. They are backdated instead, by
    # backdate_lead_events() below.
    return events


def backdate_lead_events(leads):
    """Move each store-emitted lead.created event back to when that lead arrived.

    WHY THIS IS NEEDED AND WHY IT IS A REWRITE, NOT AN EMIT. leads_store.create()
    stamps now(), correctly — a lead created today WAS created today. But seeding
    forty leads in one loop then produces forty events sharing one timestamp,
    which buries a week of real activity under a wall of today and makes the
    TODAY hero read 42. The leads themselves already carry a spread
    `last_activity_at`, so the events are moved to match it.

    Matched on `lead_id` in the attachment, which the store writes (W3 added it
    so a member's own leads reach their scoped feed) — not on line position,
    which would silently mis-assign if anything else wrote in between.
    """
    path = Path(notify.feed_path())
    when_for = {l["lead_id"]: l["last_activity_at"] for l in leads}
    lines, moved = [], 0
    for raw in path.read_text().splitlines():
        if not raw.strip():
            continue
        ev = json.loads(raw)
        lid = next((a.get("lead_id") for a in ev.get("attachments") or []
                    if isinstance(a, dict) and a.get("lead_id")), None)
        if ev.get("kind") == "lead.created" and lid in when_for:
            ev["ts"] = when_for[lid]
            moved += 1
        lines.append(json.dumps(ev, ensure_ascii=False))
    # Newest-last, the order read_feed() expects.
    lines.sort(key=lambda l: json.loads(l)["ts"])
    path.write_text("\n".join(lines) + "\n")
    return moved


# ─────────────────────────────────────────────────────────────── channels ────

CHANNELS = [
    ("118 Alderwood Ln", "dana", ["dana", "priya"], [
        ("assistant", "I opened this channel because three emails in five days "
         "referenced 118 Alderwood Ln. Rowan Castellane is the buyer, Harbourline "
         "Title is handling closing, and the option period ends on the 29th."),
        ("user", "What's outstanding?"),
        ("assistant", "Two things. Rowan asked to move Sunday's showing an hour "
         "later and hasn't had an answer, and the survey came back with an "
         "encroachment on the north fence line that needs a seller decision on "
         "the credit. I drafted a reply to Rowan; it's waiting for your approval."),
        ("user", "Send it, and tell Cordelia we'll have the survey answer Monday."),
        ("assistant", "The reply to Rowan is in your approvals queue — I don't send "
         "on your behalf without a yes. I can draft the note to Cordelia Nash now "
         "if you want it in the same batch."),
    ]),
    ("2204 Kestrel Row", "omar", ["omar", "dana"], [
        ("assistant", "2204 Kestrel Row is under contract. Appraisal is Tuesday at "
         "2pm with Meridian, and Piedmont Lending has three underwriting conditions "
         "outstanding."),
        ("user", "Which conditions?"),
        ("assistant", "Proof of the HOA transfer fee, an updated bank statement, and "
         "the signed disclosure Emmett Roscoe asked for on Thursday. The rate lock "
         "expires Friday, so the disclosure is the one with a clock on it."),
        ("user", "Remind me Thursday morning if it's still open."),
        ("assistant", "Done — I'll check the thread Thursday at 8am and only raise it "
         "if the signed copy hasn't come back."),
    ]),
    ("77 Bellhaven Ct — closing", "priya", ["priya", "dana", "omar"], [
        ("assistant", "Closing for 77 Bellhaven Ct is set for Thursday at 10:00 at "
         "Harbourline Title. All parties have confirmed."),
        ("user", "Anything left before then?"),
        ("assistant", "Final walkthrough isn't scheduled yet. Everything else is "
         "clear — payoff received, title clean, funds confirmed Tuesday."),
        ("user", "Book the walkthrough for Wednesday evening."),
        ("assistant", "I've put a request in for Wednesday 5:30pm. It needs your "
         "approval before it goes to the buyer's agent."),
    ]),
    ("3310 Copperbeam Dr — inspection", "dana", ["dana", "priya"], [
        ("assistant", "Keystone's inspection report for 3310 Copperbeam Dr came in. "
         "Four items flagged: a GFCI outlet, minor flashing on the back roof, a slow "
         "drain in the hall bath, and evidence of a past leak under the sink."),
        ("user", "Which of those are real?"),
        ("assistant", "The flashing and the past leak are worth a repair request; the "
         "outlet and the drain are cheap enough that asking for them tends to cost "
         "goodwill. The option period ends tomorrow, so the request has to go out today."),
        ("user", "Draft it with just those two."),
        ("assistant", "Drafted. It's in your approvals queue with the two items and "
         "the inspector's photos attached."),
    ]),
]


def seed_channels():
    threads.init()
    made = []
    for name, creator, members, turns in CHANNELS:
        ch = threads.create_thread(name, created_by="jarvis", creator=creator)
        for m in members:
            threads.add_member(ch["thread_id"], m, added_by=creator)
        base_day = 2 + len(made)
        msgs = []
        for i, (role, content) in enumerate(turns):
            msgs.append({"role": "assistant" if role == "assistant" else "user",
                         "content": content,
                         "ts": ago(days=base_day, hours=20 - i * 2)})
        threads.append_messages(ch["thread_id"], *msgs)
        made.append((ch, members))

    # A direct line per person, so the tester's first login is not an empty app.
    for key in F.AGENTS:
        d = threads.ensure_direct(key)
        threads.append_messages(
            d["thread_id"],
            {"role": "assistant",
             "content": f"Morning, {F.AGENT_NAME[key]}. Three things today: the "
                        "Bellhaven closing is Thursday, two of your leads have gone "
                        "quiet for a month, and the Copperbeam option period ends "
                        "tomorrow.",
             "ts": ago(days=0, hours=9)})
    return made


# ────────────────────────────────────────────────────────────── approvals ────

APPROVALS = [
    {"skill": "owner-email",
     "summary": "Send a reply to Rowan Castellane about the Sunday showing",
     "payload": {"action": "send_email",
                 "to": "rowan.castellane@mailbox.example",
                 "subject": "Re: Sunday showing — 118 Alderwood Ln",
                 "channel": "118 Alderwood Ln",
                 "body": "Sunday at 3pm works — I've moved it and let the listing "
                         "agent know. See you there.",
                 "reason": "Buyer asked to move the showing; no reply sent yet."}},
    {"skill": "email-router",
     "summary": "Send the repair request for 3310 Copperbeam Dr",
     "payload": {"action": "send_email",
                 "to": "sabine@keystone-inspect.example",
                 "subject": "Repair request — 3310 Copperbeam Dr",
                 "channel": "3310 Copperbeam Dr — inspection",
                 "body": "Two items from the report: the back-roof flashing and the "
                         "prior leak under the hall bath sink.",
                 "reason": "Option period ends tomorrow; request must go out today."}},
    {"skill": "google-calendar",
     "summary": "Book the final walkthrough for 77 Bellhaven Ct on Wednesday 5:30pm",
     "payload": {"action": "create_event",
                 "title": "Final walkthrough — 77 Bellhaven Ct",
                 "when": "Wednesday 17:30",
                 "channel": "77 Bellhaven Ct — closing",
                 "attendees": ["priya@testco.example.com", "cnash@harbourline-title.example"],
                 "reason": "Closing is Thursday 10:00 and no walkthrough is booked."}},
]


def seed_approvals():
    existing = {i.get("summary") for i in approvals._read_jsonl(approvals.REQUESTS_PATH)}
    written = 0
    for i, f in enumerate(APPROVALS):
        if f["summary"] in existing:
            continue
        approvals._append_jsonl(approvals.REQUESTS_PATH, {
            "id": f"apr_{uuid.uuid4().hex[:12]}",
            "ts": ago(days=0, hours=6 - i),
            "tenant": TENANT,
            "status": approvals.PENDING,
            **f,
        })
        written += 1
    return written


# ─────────────────────────────────────────────────────────────────── main ────

def wipe():
    """Clear seeded state so the seeder is re-runnable. Clone paths only —
    assert_isolated() has already refused if any of these left the tree."""
    for p in (RESOLVED["notify.feed_path()"], RESOLVED["approvals.REQUESTS"],
              RESOLVED["approvals.DECISIONS"]):
        Path(p).unlink(missing_ok=True)
    for key in ("threads.DB_PATH", "leads_store.DB_PATH", "accounts.DB_PATH"):
        base = Path(RESOLVED[key])
        for suffix in ("", "-wal", "-shm"):
            Path(str(base) + suffix).unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true",
                    help="clear the clone's seeded state first (re-runnable)")
    args = ap.parse_args()

    print(f"clone: {testco_env.CLONE}")
    print(f"feed:  {RESOLVED['notify.feed_path()']}\n")
    if args.wipe:
        wipe()
        print("wiped prior seeded state\n")

    pins = seed_accounts()
    print(f"accounts:  {len(pins)}")
    leads = seed_leads()
    print(f"leads:     {len(leads)}")
    events = seed_feed(leads)
    moved = backdate_lead_events(leads)
    total = sum(1 for l in open(RESOLVED["notify.feed_path()"]) if l.strip())
    print(f"feed:      {total} events "
          f"({len(events)} composed here + {moved} emitted by the lead store)")
    chans = seed_channels()
    print(f"channels:  {len(chans)} deal channels + {len(F.AGENTS)} direct lines")
    n_apr = seed_approvals()
    print(f"approvals: {n_apr} pending")

    out = testco_env.CLONE / "SEEDED_PINS.json"
    out.write_text(json.dumps(pins, indent=2) + "\n")
    out.chmod(0o600)
    print(f"\nPINs written to {out} (mode 600) — shown once by the store, "
          f"so this file is the only copy.")


if __name__ == "__main__":
    main()
