#!/usr/bin/env python3
"""Build the lead-sheet COPY fixture for exercising the W1 importer.

WHY A COPY FIXTURE AT ALL. The importer's whole design point is that it derives
the column map from the LIVE HEADER ROW rather than trusting a stored one —
that discipline exists because a stored map has been wrong twice on this tree.
A fixture that hands the importer a tidy pre-parsed list would test none of
that. So this reproduces the SHAPE of a real tenant tracker, warts included:

  * row 0 is a merged title BANNER, not headers (HEADER_ROW_INDEX = 1);
  * tab titles carry TRAILING SPACES, as the live one does;
  * headers are worded differently per tab ("Lead Temperature" vs "Temp",
    "Status Of Lead" vs "Lead Status") so header-TEXT matching is exercised
    rather than position;
  * "Thread ID" is present because the live tracker has it. NOTE: the importer
    DOES know this header — W1 added it to HEADER_FIELDS after finding the
    column — so it maps rather than reporting. An earlier draft of this file
    claimed the opposite; the run corrected it. A genuinely unknown column
    ("Referral Src") is included instead, to exercise the unmatched path;
  * TWO tabs must be skipped, by DIFFERENT mechanisms, because both matter:
      - "SEND LOG" is in the tenant's exclude_tabs, skipped by CONFIG;
      - "2025 ARCHIVE" is NOT in that list and has no lead-name column, so it
        must be skipped BY STRUCTURE. Without this tab the structural path is
        never taken, and a tenant who names their log something else would
        have their non-lead rows imported as leads;
  * one lead is CO-LISTED across two agents — the same external ref and the
    SAME name, which the importer must merge, not flag;
  * one genuine ID COLLISION — the same external ref with a DIFFERENT name,
    which the importer must shout about, because merging would destroy a lead;
  * one row's agent is a name the registry cannot resolve, which must be left
    NULL and reported rather than guessed.

Everything is fictional and every address ends in .example.

Outputs (into the clone, never this box):
  fixtures/testco-leads-sheet.xlsx   uploadable to Google Sheets for a real run
  fixtures/tabs/<Agent>.csv          the same data, readable without Excel
  fixtures/sheet_fixture.json        what the gspread shim replays
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testco_env                      # noqa: E402
testco_env.apply()
import fixture_cast as F               # noqa: E402

OUT = testco_env.CLONE / "fixtures"
BANNER = ["TESTCO REALTY — LEAD TRACKER (FIXTURE COPY, NOT A LIVE SHEET)"]

# Deliberately different wordings per tab. All must resolve via HEADER_FIELDS.
HEADERS_A = ["Date", "Lead Name", "Property Toured", "Price Point", "Notes",
             "Lead Temperature", "Follow-Up Status", "Status Of Lead",
             "Next Follow Up Date", "Assigned Agent", "Email", "Phone",
             "Lead ID", "Thread ID", "Referral Src"]
HEADERS_B = ["Date Added", "Client Name", "Address", "Budget", "Note",
             "Temp", "Follow Up Status", "Lead Status",
             "Next Touch", "Agent", "E-mail", "Mobile",
             "LeadID", "Channel ID", "Referral Src"]


def row(date, name, prop, price, notes, temp, follow, status, nxt,
        agent, email, phone, ref, thread="", referral=""):
    return [date, name, prop, price, notes, temp, follow, status, nxt,
            agent, email, phone, ref, thread, referral or "Website form"]


def build():
    b = F.BUYERS
    tabs = {}

    # ── Dana ────────────────────────────────────────────────────────────────
    tabs["Dana "] = [BANNER, HEADERS_A] + [          # note the trailing space
        row("2026-07-02", b[0][0], "118 Alderwood Ln", "$500-600K",
            "Wants a corner lot", "Hot", "Toured twice, wants another look",
            "Responded", "2026-08-25", "Dana", b[0][1], "555-0101", "TC-2001"),
        row("2026-06-18", b[1][0], "46 Marlowe Cross", "$340-410K",
            "Asked about HOA dues", "Warm", "Emailed 6/15, no reply yet",
            "No Response", "2026-08-23", "Dana", b[1][1], "555-0102", "TC-2002"),
        row("2026-05-29", b[2][0], "620 Quillon Ave", "$420-480K",
            "Pre-approved with Piedmont", "Warm", "Called Tuesday, left voicemail",
            "No Response", "2026-08-22", "Dana", b[2][1], "555-0103", "TC-2003"),
        # CO-LISTED: same ref, SAME name, also on Omar's tab. Must merge.
        row("2026-06-07", b[3][0], "402 Larkfield Ter", "$610-720K",
            "Co-listed with Omar", "Hot", "Countered under ask",
            "Responded", "2026-08-22", "Dana", b[3][1], "555-0104", "TC-2010"),
        row("2026-04-30", b[4][0], "760 Ashgrove Blvd", "$750-880K",
            "Relocating in November", "Cold", "Said to check back after the holidays",
            "Not Interested", "", "Dana", b[4][1], "555-0105", "TC-2004"),
    ]

    # ── Omar ────────────────────────────────────────────────────────────────
    tabs["Omar"] = [BANNER, HEADERS_B] + [
        row("2026-07-14", b[5][0], "2204 Kestrel Row", "$500-600K",
            "Under contract", "Hot", "Appraisal Tuesday",
            "Responded", "2026-08-24", "Omar", b[5][1], "555-0106", "TC-2005"),
        row("2026-06-25", b[6][0], "1801 Fennimore St", "$275-325K",
            "First-time buyer", "Warm", "Sent comps, waiting on them",
            "New", "2026-08-26", "Omar", b[6][1], "555-0107", "TC-2006"),
        # The co-listed row's second copy. Same ref, same name → one lead.
        row("2026-06-07", b[3][0], "402 Larkfield Ter", "$610-720K",
            "Co-listed with Dana", "Hot", "Countered under ask",
            "Responded", "2026-08-22", "Omar", b[3][1], "555-0104", "TC-2010"),
        # UNRESOLVABLE AGENT: nobody by this name is in the registry. Must be
        # left NULL and reported, never guessed onto whoever sorts first.
        row("2026-05-19", b[7][0], "155 Drummond Walk", "$420-480K",
            "Handed over from a departed agent", "Cold", "",
            "No Response", "", "Thaddeus Grimsby", b[7][1], "555-0108", "TC-2007"),
        row("", "", "", "", "", "", "", "", "", "", "", "", ""),   # blank row
    ]

    # ── Priya ───────────────────────────────────────────────────────────────
    tabs["Priya"] = [BANNER, HEADERS_A] + [
        row("2026-07-21", b[8][0], "77 Bellhaven Ct", "$900K-1.1M",
            "Closing Thursday", "Hot", "Walkthrough not booked",
            "Responded", "2026-08-21", "Priya", b[8][1], "555-0109", "TC-2008"),
        row("2026-06-11", b[9][0], "3310 Copperbeam Dr", "$610-720K",
            "Option period ends tomorrow", "Hot", "Repair request drafted",
            "Responded", "2026-08-21", "Priya", b[9][1], "555-0110", "TC-2009"),
        # GENUINE COLLISION: TC-2010 again, but a DIFFERENT person. Merging
        # this would destroy one of the two leads, so it must be shouted about.
        row("2026-05-02", b[10][0], "918 Ravenscourt Rd", "$340-410K",
            "Different lead, duplicate id typed by hand", "Cold",
            "Texted about the weekend slot", "No Response", "",
            "Priya", b[10][1], "555-0111", "TC-2010"),
        row("2026-04-15", b[11][0], "71 Fallowfield Way", "$275-325K",
            "Spouse still deciding", "Cold", "",
            "New", "2026-09-01", "Priya", b[11][1], "555-0112", "TC-2011"),
    ]

    # ── skipped by CONFIG (it is in the tenant's exclude_tabs) ─────────────
    tabs["SEND LOG"] = [
        BANNER,
        ["Sent At", "Template", "Recipient Count", "Notes"],
        ["2026-08-18", "Weekly check-in", "12", "no bounces"],
        ["2026-08-11", "New listing alert", "31", "2 bounces"],
    ]

    # ── skipped by STRUCTURE (not in exclude_tabs, no lead-name column) ─────
    tabs["2025 ARCHIVE"] = [
        BANNER,
        ["Quarter", "Closed Volume", "Units", "Notes"],
        ["Q4 2025", "$4.2M", "9", "best quarter"],
        ["Q3 2025", "$3.1M", "7", ""],
    ]
    return tabs


def main():
    tabs = build()
    (OUT / "tabs").mkdir(parents=True, exist_ok=True)

    (OUT / "sheet_fixture.json").write_text(
        json.dumps({"title": "Testco Realty — Lead Tracker (FIXTURE COPY)",
                    "tabs": tabs}, indent=1, ensure_ascii=False) + "\n")

    import csv
    for title, rows in tabs.items():
        with open(OUT / "tabs" / f"{title.strip().replace(' ', '_')}.csv",
                  "w", newline="") as fh:
            csv.writer(fh).writerows(rows)

    made_xlsx = False
    try:
        from openpyxl import Workbook
        wb = Workbook()
        wb.remove(wb.active)
        for title, rows in tabs.items():
            # Excel forbids trailing spaces in sheet names, so the xlsx cannot
            # carry that particular wart. The JSON fixture and the shim do, and
            # they are what the automated run uses.
            ws = wb.create_sheet(title.strip()[:31])
            for r in rows:
                ws.append(r)
        wb.save(OUT / "testco-leads-sheet.xlsx")
        made_xlsx = True
    except ImportError:
        pass

    n = sum(max(len(r) - 2, 0) for r in tabs.values())
    print(f"tabs:      {list(tabs)}")
    print(f"data rows: {n}")
    print(f"csv:       {OUT / 'tabs'}")
    print(f"json:      {OUT / 'sheet_fixture.json'}")
    print(f"xlsx:      {'written' if made_xlsx else 'SKIPPED (openpyxl not installed)'}")


if __name__ == "__main__":
    main()
