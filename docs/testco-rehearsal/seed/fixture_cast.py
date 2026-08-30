"""The testco cast — ALL FICTIONAL, all on .example domains.

VOICE AND RULES, inherited from the humanization/demo work:
  * Domain-correct: Testco Realty is a brokerage, so the vocabulary is
    showings, inspections, title, appraisal, escrow, walkthroughs.
  * Every counterparty is invented and every address is invented. The surname
    register follows the existing demo fixtures (Castellane / Marblewood /
    Veskandar) — plausible, memorable, and unmistakably not a real client list.
  * EVERY email domain ends in `.example`. That TLD is RFC 2606 reserved and can
    never resolve, so a fixture that escapes into a real send path fails closed
    instead of mailing a stranger.
  * Internal staff come from the clone's identity registry (dana / omar /
    priya on testco.example.com) and are NEVER invented here — the registry is
    the tenant's source of truth and a second list would drift from it.

Nothing in this file may name a real person, a real property, or 34dev.
"""

# Internal — must match testco identity.json exactly.
AGENTS = ["dana", "omar", "priya"]
AGENT_NAME = {"dana": "Dana", "omar": "Omar", "priya": "Priya"}

# External counterparties. Buyers/sellers on consumer-ish domains, vendors on
# their own — the mix a real inbox has.
BUYERS = [
    ("Rowan Castellane",   "rowan.castellane@mailbox.example"),
    ("Ingrid Marblewood",  "ingrid.m@mailbox.example"),
    ("Teodor Vance",       "tvance@mailbox.example"),
    ("Marisol Quintero",   "marisol.q@mailbox.example"),
    ("Bartholomew Reed",   "b.reed@mailbox.example"),
    ("Anouk Delacroix",    "anouk.d@mailbox.example"),
    ("Sundeep Ravikumar",  "sundeep.r@mailbox.example"),
    ("Georgina Pell",      "gpell@mailbox.example"),
    ("Hollis Wren",        "hollis.wren@mailbox.example"),
    ("Constance Okoye",    "c.okoye@mailbox.example"),
    ("Ferris Lindqvist",   "ferris.l@mailbox.example"),
    ("Marguerite Toussaint","m.toussaint@mailbox.example"),
]

VENDORS = [
    ("Cordelia Nash",   "cnash@harbourline-title.example",   "Harbourline Title"),
    ("Emmett Roscoe",   "eroscoe@piedmont-lending.example",  "Piedmont Lending"),
    ("Sabine Aldritch", "sabine@keystone-inspect.example",   "Keystone Inspections"),
    ("Wendell Fairbanks","wfairbanks@meridian-appraisal.example", "Meridian Appraisal"),
    ("Perpetua Iyer",   "perpetua@brightframe-photo.example", "Brightframe Photo"),
]

AUTOMATED = ("listings@marketwatch-feed.example", "MarketWatch Feed")

# Invented streets in an invented market. No real address anywhere.
PROPERTIES = [
    "118 Alderwood Ln", "2204 Kestrel Row", "77 Bellhaven Ct", "915 Thistledown Way",
    "3310 Copperbeam Dr", "46 Marlowe Cross", "1801 Fennimore St", "620 Quillon Ave",
    "254 Harrowgate Pl", "1129 Sablewood Rd", "88 Cinderbrook Ln", "402 Larkfield Ter",
    "1507 Pennyroyal St", "39 Windover Gate", "760 Ashgrove Blvd", "2841 Verity Ln",
    "155 Drummond Walk", "918 Ravenscourt Rd", "71 Fallowfield Way", "2620 Ostmark St",
]

PRICE_POINTS = ["$275-325K", "$340-410K", "$420-480K", "$500-600K",
                "$610-720K", "$750-880K", "$900K-1.1M"]

# The closed vocabularies the store enforces (app/leads_store.py).
STATUSES = ["New", "No Response", "Responded", "Not Interested"]
TEMPERATURES = ["Hot", "Warm", "Cold", ""]

# Free text, per-agent, exactly as the sheet had it — never a closed set.
FOLLOW_UPS = [
    "Emailed 6/15, no reply yet", "Called Tuesday, left voicemail",
    "Texted about the weekend slot", "Sent comps, waiting on them",
    "Toured twice, wants to see it again", "Asked for a lender intro",
    "Said to check back after the holidays", "Pre-approval expires next month",
    "Wants a corner lot only", "Spouse still deciding", "",
]

SOURCES = ["Website form", "Referral", "Open house", "MarketWatch Feed",
           "Sign call", "Past client", "Walk-in"]

def all_example_domains() -> list:
    """Every address this fixture can emit — for the .example assertion."""
    return [e for _, e in BUYERS] + [e for _, e, _ in VENDORS] + [AUTOMATED[0]]
