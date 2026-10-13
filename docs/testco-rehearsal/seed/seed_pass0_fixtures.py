#!/usr/bin/env python3
import sys; sys.exit("RETIRED 2026-10-13: testco is retired (docs/platform/decisions.md); this script needs its running services or tree.")  # noqa: E702
"""Pass-0 fixtures — the states a claim-verification pass has to be able to see.

Each fixture exists so a specific claim can be FALSIFIED. A tester cannot check
"stale threads are surfaced" against a tenant where nothing is stale, and a
green result on an absent condition is the oldest false-green there is.

Everything lands inside testco and every address is RFC 2606 reserved.
Idempotent: re-running replaces the fixtures rather than stacking them.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testco_env                      # noqa: E402
testco_env.apply()
testco_env.assert_isolated()

from app import leads_store, threads, approvals   # noqa: E402
import notify                                     # noqa: E402

NOW = datetime.now(timezone.utc)
def ago(**kw):
    return (NOW - timedelta(**kw)).isoformat()

made = {}

# ── 1. a lead aged past the quiet threshold ──────────────────────────────────
# The UI calls a channel "Going quiet" after 48h; a lead with no touch for
# ~40 days is unambiguously past any nudge threshold anyone would set.
leads_store.init()
QUIET_NAME = "Wilhelmina Ashgrove (quiet-fixture)"
for row in leads_store.query():
    if row["name"] == QUIET_NAME:
        leads_store.set_archived(row["lead_id"], True)
quiet = leads_store.create(
    name=QUIET_NAME, created_by="jarvis", email="w.ashgrove@mailbox.example",
    property="1408 Thornbury Row", price_point="$420-480K", source="Website form",
    status="No Response", temperature="Warm", owner_person="dana",
    follow_up="Emailed, no reply", notes="PASS-0 FIXTURE: aged past the quiet threshold.",
    last_activity_at=ago(days=41), next_touch_at=ago(days=27),
)
# BACKDATE THE EMITTED EVENT. leads_store.create() emits lead.created at
# now(), correctly — but this lead is 41 days stale by construction, so the
# feed announced "New lead ... just now" for a row the tracker flags as having
# gone quiet. A tester checking the nudge claim would have seen the two
# contradict each other on the same screen. Matched on lead_id, which the
# store writes into the attachment.
_feed = Path(notify.feed_path())
if _feed.exists():
    _lines = []
    for _raw in _feed.read_text().splitlines():
        if not _raw.strip():
            continue
        _ev = json.loads(_raw)
        _lid = next((a.get("lead_id") for a in _ev.get("attachments") or []
                     if isinstance(a, dict) and a.get("lead_id")), None)
        if _ev.get("kind") == "lead.created" and _lid == quiet["lead_id"]:
            _ev["ts"] = quiet["last_activity_at"]
        _lines.append(json.dumps(_ev, ensure_ascii=False))
    _lines.sort(key=lambda l: json.loads(l)["ts"])
    _feed.write_text("\n".join(_lines) + "\n")

made["lead_past_quiet_threshold"] = {"lead_id": quiet["lead_id"], "name": QUIET_NAME,
                                     "last_activity_days_ago": 41,
                                     "feed_event_backdated": True}

# ── 2. an inbound thread aged past the 24h unanswered window ─────────────────
threads.init()
STALE = "Ravenscourt inquiry — unanswered (stale-fixture)"
existing = [t for t in threads.list_threads(unscoped=True, viewer="dana")
            if t["name"] == STALE]
if existing:
    ch = existing[0]
else:
    ch = threads.create_thread(STALE, created_by="jarvis", creator="dana")
    threads.add_member(ch["thread_id"], "dana", added_by="dana")
    # Inbound only, and deliberately NO assistant reply after it — the window
    # is measured from the last INBOUND message, so a trailing assistant turn
    # would reset it and the fixture would silently stop being stale.
    threads.append_messages(
        ch["thread_id"],
        {"role": "user",
         "content": "Hi — is 918 Ravenscourt Rd still available? We could come Saturday.",
         "ts": ago(hours=53)},
    )
made["thread_unanswered_past_24h"] = {"thread_id": ch["thread_id"], "name": STALE,
                                      "last_inbound_hours_ago": 53}

# ── 3. delegation targets, including the ones that must NOT quietly drop ─────
# The Blake failure mode, reproduced on purpose: a name that resolves to nobody
# must surface loudly. A silent drop here is how an assignment disappears.
DELEG = [
    ("registered",   "omar",              "a member the registry knows"),
    ("unresolvable", "Bartholomew Quill", "a name in no registry — must surface, never drop"),
    ("departed",     "priya-former",      "a key shaped like a person who has left"),
]
resolved = {}
sys.path.insert(0, "/opt/testco/skills/openclaw-imports/_shared")
import identity                          # noqa: E402
for label, target, why in DELEG:
    try:
        key = identity.resolve(target)
    except Exception:
        key = None
    resolved[label] = {"target": target, "resolves_to": key, "why": why}
made["delegation_targets"] = resolved

# ── 4. adversarial approval fixtures ─────────────────────────────────────────
# Four ways an approval can be illegitimate. Each is a claim the applier must
# refuse; a tester needs all four present to prove refusal rather than luck.
def apr(**kw):
    rec = {"id": f"apr_{uuid.uuid4().hex[:12]}", "tenant": "testco", **kw}
    approvals._append_jsonl(approvals.REQUESTS_PATH, rec)
    return rec["id"]

existing_sums = {i.get("summary") for i in approvals._read_jsonl(approvals.REQUESTS_PATH)}
adversarial = {}

specs = [
    ("forged", "ADVERSARIAL forged — id and signature do not match the payload",
     {"ts": ago(hours=3), "status": "pending", "skill": "owner-email",
      "payload": {"action": "send_email", "to": "rowan.castellane@mailbox.example",
                  "subject": "Forged request", "signature": "not-a-valid-signature",
                  "signed_id": "apr_totally_different", "reason": "adversarial fixture"}}),
    ("expired", "ADVERSARIAL expired — requested 20 days ago, long past any TTL",
     {"ts": ago(days=20), "status": "pending", "skill": "owner-email",
      "payload": {"action": "send_email", "to": "ingrid.m@mailbox.example",
                  "subject": "Stale request", "expires_at": ago(days=13),
                  "reason": "adversarial fixture"}}),
    ("replay", "ADVERSARIAL replay — already applied once, presented again",
     {"ts": ago(hours=6), "status": "pending", "skill": "google-calendar",
      "payload": {"action": "create_event", "title": "Replay probe",
                  "applied_at": ago(hours=5), "already_applied": True,
                  "reason": "adversarial fixture"}}),
    ("stale_expected_value", "ADVERSARIAL stale expected-value — state moved since it was written",
     {"ts": ago(hours=4), "status": "pending", "skill": "config", "kind": "config-change",
      # current="sheet" is the STALE expectation (the tenant is actually on
      # "store"), and proposed differs so the rendered diff is legible. An
      # earlier version set both to "sheet", which drew as "sheet -> sheet" —
      # a no-op change that told the tester nothing about what was being
      # refused. The claim under test is the expected-current mismatch, and
      # the card has to SHOW a change for that to be checkable.
      "payload": {"action": "apply_config",
                  "changes": [{"key": "skills.leads.source",
                               "current": "sheet",      # actual value is 'store'
                               "proposed": "store"}],
                  "reason": "adversarial fixture — expected-current no longer matches"}}),
]
for label, summary, body in specs:
    if summary in existing_sums:
        adversarial[label] = "already present"
        continue
    adversarial[label] = apr(summary=summary, **body)
made["adversarial_approvals"] = adversarial

# ── 5. ShowingSmart inbound fixture + property-templates sheet tab ───────────
# THE SHAPE HERE IS THE VENDOR'S, NOT OURS. The first version of these fixtures
# invented a plausible-looking format (Property:/Date-Time:/Buyer Agent: lines),
# and the skill that consumes them parses the format the production automation
# has been reading off real ShowingSmart mail for months. A fixture written from
# the same guess as the parser proves only that the guess is self-consistent —
# so these carry the real subject line and the real "Showing Agent Information"
# block, and the parser is the one that stayed put.
FIX = Path("/opt/testco/fixtures")
FIX.mkdir(parents=True, exist_ok=True)


def _showing_eml(msg_no, address, day_name, month, day, time_label, year,
                 time_range, agent_name, agent_email, closing, company=None):
    """One showing request in the vendor's own shape."""
    agent_lines = [agent_name]
    if company:
        agent_lines.append(company)
    if agent_email:
        agent_lines.append(agent_email)
    return (
        "From: ShowingSmart Notifications <notifications@showingsmart.example>\n"
        "To: assistant@testco.example.com\n"
        f"Subject: Showing: {address} ({day_name}, {month} {day} {time_label}) "
        "\u2013 ShowingSmart\n"
        "Date: " + NOW.strftime("%a, %d %b %Y %H:%M:%S +0000") + "\n"
        f"Message-ID: <ss-fixture-{msg_no}@showingsmart.example>\n"
        "\n"
        "A showing has been requested.\n"
        "\n"
        f"Property: {address}\n"
        f"Date: {month} {day}, {year}\n"
        f"Time: {time_range}\n"
        "\n"
        "Showing Agent Information\n"
        + "\n".join(agent_lines) + "\n"
        "\n"
        f"{closing}\n")


# The matching request: this address has a row in the templates tab.
(FIX / "showingsmart_inbound.eml").write_text(_showing_eml(
    "001", "918 Ravenscourt Rd", "Saturday", "August", 29, "2:00 PM", 2026,
    "2:00 PM - 2:30 PM", "Rowan Castellane", "rowan.castellane@mailbox.example",
    "Confirm or decline in ShowingSmart."), encoding="utf-8")

# The unmatched request: parses cleanly, matches no property row, on purpose.
(FIX / "showingsmart_inbound_unmatched.eml").write_text(_showing_eml(
    "002", "77 Nowhere Ln", "Sunday", "August", 30, "11:00 AM", 2026,
    "11:00 AM - 11:30 AM", "Ingrid Marblewood", "ingrid.m@mailbox.example",
    "This address matches no property row, on purpose."), encoding="utf-8")

# No agent email anywhere in the block — the skill must refuse to guess one.
(FIX / "showingsmart_inbound_noemail.eml").write_text(_showing_eml(
    "003", "918 Ravenscourt Rd", "Monday", "August", 31, "10:00 AM", 2026,
    "10:00 AM - 10:30 AM", "Rowan Castellane", "",
    "The agent email is missing from this one, on purpose."), encoding="utf-8")

# Points at the row whose attachment links are malformed.
(FIX / "showingsmart_inbound_badlinks.eml").write_text(_showing_eml(
    "004", "118 Alderwood Ln", "Tuesday", "September", 1, "3:00 PM", 2026,
    "3:00 PM - 3:30 PM", "Teodor Vance", "tvance@mailbox.example",
    "This property's attachment links are malformed, on purpose."), encoding="utf-8")

# Reserved for the worked example baked into SKILL.md — kept separate so the
# documented run is never one the test suite has already consumed.
(FIX / "showingsmart_inbound_example.eml").write_text(_showing_eml(
    "005", "2204 Kestrel Row", "Wednesday", "September", 2, "1:00 PM", 2026,
    "1:00 PM - 1:30 PM", "Anouk Delacroix", "anouk.d@mailbox.example",
    "Confirm or decline in ShowingSmart.", company="Delacroix Homes"), encoding="utf-8")

# The property-templates tab, added to the existing sheet fixture. HEADER-
# DERIVED like every other tab — the importer matches on header text, never on
# position, so the tab carries real headers rather than an index contract.
sheet = json.loads((FIX / "sheet_fixture.json").read_text())
BANNER = ["TESTCO REALTY — LEAD TRACKER (FIXTURE COPY, NOT A LIVE SHEET)"]
_L918 = ("https://drive.google.com/uc?export=download&id=FIXTURE-918-disclosures&confirm=t,"
         "https://drive.google.com/uc?export=download&id=FIXTURE-918-survey&confirm=t")
# DELIBERATELY BROKEN: a space after the comma. A reader sees a tidy list; the
# second URL arrives with a leading space and resolves to nothing. This is the
# negative fixture for the attachment-links rule.
_L118 = ("https://drive.google.com/uc?export=download&id=FIXTURE-118-plat&confirm=t, "
         "https://drive.google.com/uc?export=download&id=FIXTURE-118-hoa&confirm=t")
_L2204 = "https://drive.google.com/uc?export=download&id=FIXTURE-2204-disclosures&confirm=t"
sheet["tabs"]["Property Templates"] = [
    BANNER,
    ["Property Address", "Lockbox Code", "Showing Instructions",
     "Confirmation Template", "Decline Template", "Agent",
     "Email Subject", "Email Body HTML", "Attachment Links"],
    ["918 Ravenscourt Rd", "4417",
     "Park on the street; dog is crated in the utility room.",
     "Confirmed for {time} at {address}. Lockbox {lockbox}. {instructions}",
     "Sorry — {address} is not available at {time}. Could we offer another slot?",
     "Dana",
     "Showing information for 918 Ravenscourt Rd",
     "<p>Thanks for scheduling a showing at 918 Ravenscourt Rd.</p>"
     "<p>Lockbox 4417. Park on the street; the dog is crated in the utility room.</p>"
     "<p>Disclosures and the survey are attached.</p>",
     _L918],
    ["118 Alderwood Ln", "2290",
     "Side gate is unlocked; leave lights as found.",
     "Confirmed for {time} at {address}. Lockbox {lockbox}. {instructions}",
     "Sorry — {address} is not available at {time}.",
     "Dana",
     "Showing information for 118 Alderwood Ln",
     "<p>Thanks for scheduling a showing at 118 Alderwood Ln.</p>"
     "<p>Lockbox 2290. The side gate is unlocked; please leave lights as found.</p>",
     _L118],
    ["2204 Kestrel Row", "8801",
     "Shoes off. Seller works from home until noon.",
     "Confirmed for {time} at {address}. Lockbox {lockbox}. {instructions}",
     "Sorry — {address} is not available at {time}.",
     "Omar",
     "Showing information for 2204 Kestrel Row",
     "<p>Thanks for scheduling a showing at 2204 Kestrel Row.</p>"
     "<p>Lockbox 8801. Shoes off, please — the seller works from home until noon.</p>",
     _L2204],
]
(FIX / "sheet_fixture.json").write_text(json.dumps(sheet, indent=1, ensure_ascii=False) + "\n")

import csv
(FIX / "tabs").mkdir(exist_ok=True)
with open(FIX / "tabs" / "Property_Templates.csv", "w", newline="") as fh:
    csv.writer(fh).writerows(sheet["tabs"]["Property Templates"])

made["showingsmart_fixtures"] = {
    "matching": "showingsmart_inbound.eml — 918 Ravenscourt Rd, matches a template row",
    "unmatched": "showingsmart_inbound_unmatched.eml — 77 Nowhere Ln, matches nothing",
    "no_agent_email": "showingsmart_inbound_noemail.eml — agent block has no email line",
    "bad_links": "showingsmart_inbound_badlinks.eml — 118 Alderwood Ln, spaced attachment list",
    "worked_example": "showingsmart_inbound_example.eml — 2204 Kestrel Row, reserved for SKILL.md",
    "sheet_tab": ("Property Templates (3 rows, header-derived; Email Subject / "
                  "Email Body HTML / Attachment Links carry the send leg)"),
    "format": ("vendor shape: subject 'Showing: <address> (<Day>, <Month> <D> <H:MM> <AM/PM>)' "
               "plus a 'Showing Agent Information' block, name line then email"),
}

# ── 7. Meeting transcripts (meeting-intake) ────────────────────────────────
# Speaker-labelled lines with [MM:SS] stamps — the shape every recorder export
# and every human paste takes. Each fixture below names what it falsifies.
from email.message import EmailMessage as _EM


def _meeting_eml(msg_no, title, body, attachments=()):
    m = _EM()
    m["From"] = "Dana Whitlock <dana@testco.example.com>"
    m["To"] = "assistant@testco.example.com"
    m["Subject"] = f"Meeting: {title}"
    m["Date"] = NOW.strftime("%a, %d %b %Y %H:%M:%S +0000")
    m["Message-ID"] = f"<mt-fixture-{msg_no}@testco.example.com>"
    m.set_content(body)
    for fn, mime, data in attachments:
        mt, st = mime.split("/", 1)
        m.add_attachment(data, maintype=mt, subtype=st, filename=fn)
    return bytes(m)


CLEAN = """[00:00] Dana: Okay, recording. Tuesday listing sync. Let's go through 118 Alderwood first.
[00:14] Omar: The seller signed the disclosures last night. I'll upload the signed disclosures to the listing folder this afternoon.
[00:31] Dana: Good. Priya, can you schedule the photographer for Alderwood? Sometime this week.
[00:40] Priya: Yes, I'll schedule the photographer for 118 Alderwood.
[00:52] Dana: And I'll call the title company about the payoff letter — I want that done by Friday.
[01:05] Omar: Kestrel Row — the HOA still hasn't sent the resale certificate. I'll chase the HOA for the Kestrel Row resale certificate. That one is urgent, closing is in ten days.
[01:24] Dana: Urgent, agreed. Priya, one more: send Rowan the revised comps for Ravenscourt.
[01:33] Priya: Will do, I'll send Rowan the revised Ravenscourt comps.
[01:41] Dana: Last thing — I'll draft the price-reduction email for the Ravenscourt seller. Not this week, no rush.
[01:55] Omar: Anything else? No? Good sync.
[02:01] Dana: That's it. Stopping the recording.
"""
UNKNOWN = """[00:00] Dana: Quick one before the inspection. Marcus, can you walk the Kestrel Row property before the inspector arrives?
[00:09] Marcus: Sure, I'll walk Kestrel Row on Thursday morning.
[00:15] Dana: Thanks. Omar, you'll confirm the inspector's time?
[00:20] Omar: Yes, I'll confirm the inspector's arrival time with the buyer's agent.
[00:28] Dana: Done then. Stopping.
"""
ZERO = """[00:00] Dana: Is this thing on? Okay.
[00:05] Omar: The coffee machine upstairs is fixed, by the way.
[00:11] Priya: Finally. Did anyone catch the game last night?
[00:16] Dana: Half of it. Anyway, nothing on the agenda today — the Alderwood closing moved to next month and we covered Kestrel yesterday.
[00:30] Omar: So we're done?
[00:33] Dana: We're done. Enjoy the short meeting.
"""
VTT = """WEBVTT

00:00:00.000 --> 00:00:06.000
Dana: Recording the Thursday buyer call. Omar, where are we on the Alderwood appraisal?

00:00:06.500 --> 00:00:12.000
Omar: The appraiser comes Monday. I'll send the appraiser the updated survey before then.

00:00:12.500 --> 00:00:18.000
Priya: I'll book the conference room for the Alderwood closing walkthrough.

00:00:18.500 --> 00:00:22.000
Dana: Good. That's all for today.
"""
EXAMPLE = """[00:00] Dana: Friday pipeline review, recording on. Three properties, let's be quick.
[00:12] Omar: Alderwood first. The appraisal came in fine. I'll send the buyer's lender the appraisal report today.
[00:26] Dana: Good. Kestrel Row — Priya, where's the HOA?
[00:31] Priya: Still nothing. I'll call the HOA office Monday morning and get a date for the resale certificate.
[00:44] Dana: And I'll email the Kestrel seller the updated timeline, that's on me.
[00:55] Omar: Ravenscourt — the inspection is Tuesday. I'll confirm the inspector with the buyer's agent, and that's urgent, they've asked twice.
[01:10] Dana: Agreed, urgent. That's the list. Stopping.
"""
FIX.mkdir(parents=True, exist_ok=True)
(FIX / "meeting_clean.eml").write_bytes(_meeting_eml("001", "Tuesday listing sync", CLEAN))
# Marcus is not on the team, on purpose: the task must be unassigned and flagged.
(FIX / "meeting_unknown_name.eml").write_bytes(_meeting_eml("002", "Kestrel Row inspection prep", UNKNOWN))
# Chatter and no commitments: the summary must say so rather than invent.
(FIX / "meeting_zero_items.eml").write_bytes(_meeting_eml("003", "Wednesday stand-up", ZERO))
# A binary attachment and no transcript in the body: flagged, never guessed.
(FIX / "meeting_malformed.eml").write_bytes(_meeting_eml(
    "004", "Board pack", "Transcript attached.",
    [("notes.bin", "application/octet-stream", os.urandom(64))]))
# The transcript as a WebVTT attachment, the way a recorder exports it.
(FIX / "meeting_attachment.eml").write_bytes(_meeting_eml(
    "005", "Thursday buyer call", "Transcript attached from the recorder.",
    [("transcript.vtt", "text/vtt", VTT.encode("utf-8"))]))
# Reserved for the worked example in SKILL.md — never consumed by the suite.
(FIX / "meeting_example.eml").write_bytes(_meeting_eml("006", "Friday pipeline review", EXAMPLE))

made["meeting_fixtures"] = {
    "clean": "meeting_clean.eml — 6 spoken commitments across Dana/Omar/Priya, one 'by Friday', one 'urgent'",
    "unknown_name": "meeting_unknown_name.eml — Marcus is not on the team; Omar is",
    "zero_items": "meeting_zero_items.eml — chatter, no commitments",
    "malformed": "meeting_malformed.eml — notes.bin attachment, no transcript",
    "attachment_vtt": "meeting_attachment.eml — WebVTT transcript as transcript.vtt",
    "worked_example": "meeting_example.eml — reserved for SKILL.md",
    "format": "subject 'Meeting: <title>'; '[MM:SS] Name:' lines in the body, or a .txt/.vtt attachment",
}

# ── 7b. digest-reply fixtures ───────────────────────────────────────────────
# What a person types back to the task digest. In-Reply-To is the literal
# <LATEST_DIGEST>: the poller's --reply-eml seam swaps in the Message-ID of the
# newest digest this tenant has sent, because a fixture written today cannot
# know the id of a digest sent tomorrow. From is the owner so complete_task
# accepts the actor; the confirmation goes to her and CCs the roster — all of
# it captured by the boundary.
def _reply_eml(msg_no, body):
    m = _EM()
    m["From"] = "Dana Whitlock <dana@testco.example.com>"
    m["To"] = "assistant@testco.example.com"
    m["Subject"] = "Re: Your task digest"
    m["Date"] = NOW.strftime("%a, %d %b %Y %H:%M:%S +0000")
    m["Message-ID"] = f"<dr-fixture-{msg_no}@testco.example.com>"
    m["In-Reply-To"] = "<LATEST_DIGEST>"
    m.set_content(body)
    return bytes(m)


(FIX / "digest_reply_done1.eml").write_bytes(_reply_eml(1, "done 1\n"))
(FIX / "digest_reply_done_two.eml").write_bytes(_reply_eml(2, "done 2, 3\n\nThanks!\n"))
(FIX / "digest_reply_priority.eml").write_bytes(_reply_eml(3, "priority 1 high\n"))
(FIX / "digest_reply_nonsense.eml").write_bytes(_reply_eml(4, "Looks good, nothing from me today.\n"))
made["digest_reply_fixtures"] = {
    "done1": "digest_reply_done1.eml — 'done 1' completes position 1 of the latest digest",
    "done_two": "digest_reply_done_two.eml — 'done 2, 3'",
    "priority": "digest_reply_priority.eml — 'priority 1 high'",
    "nonsense": "digest_reply_nonsense.eml — no command; the confirmation says so",
    "format": "In-Reply-To <LATEST_DIGEST> resolves to the newest digest at injection time",
}

# ── 6. the sign-in PINs ──────────────────────────────────────────────────────
# RECORDED ON PURPOSE. Every identity in this tenant is fictional and it can
# only be reached over loopback, so these are demo credentials, not secrets —
# and an onboarding tester who cannot log in has no way to verify anything.
#
# Carried through from SEEDED_PINS.json rather than re-minted here: the account
# store prints a PIN exactly once and keeps only a hash, so that file is the
# single source. Copying it in means a re-seed does not silently drop the PINs
# from this inventory — which it did, because this file is rewritten wholesale
# on every run.
try:
    pins = json.loads(Path("/opt/testco/SEEDED_PINS.json").read_text())
    # The ROLE comes from the accounts store, not a map typed here: a person
    # promoted or a tester minted at member level must read back as they are.
    import sqlite3 as _sq
    _roles = {}
    try:
        _c = _sq.connect("/opt/testco/hermes/state/jarvis_accounts.db")
        _roles = dict(_c.execute("SELECT person_key, role FROM accounts"))
    except Exception:
        pass
    made["accounts"] = {
        k: {"pin": v, "role": _roles.get(k, "unknown — not in the accounts store"),
            **({"purpose": "dedicated QA contractor account — not part of the fixture cast; "
                           "revoke on its own with accounts_cli disable tester"} if k == "tester" else {})}
        for k, v in pins.items()
    }
except Exception as exc:
    made["accounts"] = {"error": f"SEEDED_PINS.json unreadable: {exc} — "
                                 f"re-mint with accounts_cli set-pin"}

# The operator passphrase, read from the tenant's own .env. Same reasoning as
# the PINs above: this file is rewritten wholesale on every run, so anything
# not re-read here is silently dropped on the next re-seed. Unlike the PINs
# this one is NOT re-minted by seeding — mint_secrets() refuses to rotate an
# existing value on purpose — so it survives a re-seed and only changes when
# somebody rotates it deliberately.
try:
    import re as _re
    _env = Path("/opt/testco/hermes/.env").read_text()
    _m = _re.search(r"^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$", _env, _re.M)
    if _m:
        _prev = made.get("operator_credentials") or {}
        made["operator_credentials"] = {
            **_prev,
            "passphrase": _m.group(1).strip().strip('"\''),
            "role": "operator",
            "label": (_re.search(r"^\s*APP_OPERATOR_LABEL=(.*)$", _env, _re.M) or [None, "operator"])[1].strip(),
            "how_to_use": "Sign-in screen -> 'Operator access' -> enter this passphrase.",
        }
    # Named operator passphrases (APP_OPERATOR_PASSPHRASES=label=pass;label=pass):
    # one per person doing support, so the journal says who. Same role.
    _n = _re.search(r"^\s*APP_OPERATOR_PASSPHRASES=(.*)$", _env, _re.M)
    if _n:
        named = {}
        for pair in _n.group(1).strip().strip('"\'').split(";"):
            if "=" in pair:
                lab, val = pair.split("=", 1)
                named[lab.strip()] = {"passphrase": val.strip(), "role": "operator",
                                      "journal_as": f"operator:{lab.strip()}"}
        made["operator_credentials"]["named_operators"] = named
except Exception as exc:
    made["operator_credentials"] = {"error": f".env unreadable: {exc}"}

inv = Path("/opt/testco/fixtures/PASS0_INVENTORY.json")
inv.write_text(json.dumps(made, indent=2, ensure_ascii=False) + "\n")
# Same mode as SEEDED_PINS.json now that it carries the same material.
inv.chmod(0o600)
print(json.dumps(made, indent=2)[:2000])
print("\ninventory -> /opt/testco/fixtures/PASS0_INVENTORY.json")
