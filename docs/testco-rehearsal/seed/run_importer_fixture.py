#!/usr/bin/env python3
import sys; sys.exit("RETIRED 2026-10-13: testco is retired (docs/platform/decisions.md); this script needs its running services or tree.")  # noqa: E702
"""Run the REAL W1 importer against the lead-sheet COPY fixture.

WHAT IS AND IS NOT STUBBED. Exactly one thing is replaced: the gspread
TRANSPORT. `google_creds.gspread_client()` is swapped for an object that
replays fixtures/sheet_fixture.json through the same four calls the importer
makes — open_by_key / worksheets / title / get_all_values. Everything after
that is production code: derive_map(), the header-text matching, the
co-listing and collision logic, identity.resolve() against the tenant's real
registry, and leads_store.upsert_external().

That split is the point. Stubbing the parsing would test the fixture; stubbing
only the network tests the importer.

  --dry-run   derive and report, write nothing   (default)
  --execute   write into the CLONE's lead store
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testco_env                      # noqa: E402
testco_env.apply()

# --store must be honoured BEFORE the app modules import, because leads_store
# resolves DB_PATH at import time from the env. It also has to come after
# apply(), which sets that same variable — an outer `JARVIS_LEADS_DB=... python
# run_importer_fixture.py` is silently overwritten by apply() and lands in the
# seeded store instead. That happened once; hence a real flag rather than a
# convention.
if "--store" in sys.argv:
    _i = sys.argv.index("--store")
    _target = Path(sys.argv[_i + 1]).resolve()
    if not str(_target).startswith(str(testco_env.CLONE.resolve())):
        sys.exit(f"REFUSING: --store {_target} is outside the clone")
    os.environ["JARVIS_LEADS_DB"] = str(_target)
    # AND THE FEED. leads_store.create() emits a lead.created event, so
    # redirecting only the database sends this run's events into the seeded
    # workload's feed — eleven of them, the first time. Isolating the database
    # is not isolating the side effects (standing PATTERN 3); a --store that
    # does not move the feed too is not an isolated store.
    os.environ["JARVIS_ACTIVITY_FEED"] = str(_target.with_suffix(".feed.jsonl"))
    del sys.argv[_i:_i + 2]

testco_env.assert_isolated()

import google_creds                    # noqa: E402
import tenant_config as tc             # noqa: E402
from app import leads_import, leads_store   # noqa: E402

FIXTURE = testco_env.CLONE / "fixtures" / "sheet_fixture.json"


class _Worksheet:
    def __init__(self, title, rows):
        self.title = title          # trailing spaces preserved, as live
        self._rows = rows

    def get_all_values(self):
        return [list(r) for r in self._rows]


class _Spreadsheet:
    def __init__(self, data):
        self.title = data["title"]
        self._ws = [_Worksheet(t, rows) for t, rows in data["tabs"].items()]

    def worksheets(self):
        return list(self._ws)


class _Client:
    def __init__(self, data):
        self._data = data

    def open_by_key(self, key):
        # The importer passes whatever sheet_id config holds. A fixture run
        # must never be mistaken for a live one, so the id is asserted.
        if key != FIXTURE_SHEET_ID:
            raise AssertionError(
                f"fixture client asked for sheet id {key!r}; this shim only "
                f"serves the fixture id {FIXTURE_SHEET_ID!r}. Refusing — a real "
                f"sheet id here would mean the config is pointing at live data."
            )
        return _Spreadsheet(self._data)


FIXTURE_SHEET_ID = "FIXTURE-testco-lead-tracker-copy"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--store", metavar="PATH",
                    help="write into this lead DB instead of the clone's "
                         "(must be inside the clone; handled before imports)")
    args = ap.parse_args()

    data = json.loads(FIXTURE.read_text())

    # Point config at the fixture id, and swap ONLY the transport.
    tc.load.cache_clear() if hasattr(tc.load, "cache_clear") else None
    leads_import.tc = _FixtureConfig(tc)
    google_creds.gspread_client = lambda *a, **k: _Client(data)

    before = len(leads_store.query()) if args.execute else 0
    rc = leads_import.run(dry_run=not args.execute)
    if args.execute:
        after = leads_store.query()
        print(f"\nstore now holds {len(after)} active leads "
              f"({len(after) - before:+d} from this run)")
    return rc


class _FixtureConfig:
    """tenant_config, with the sheet id forced to the fixture's.

    A wrapper rather than a config edit: writing FIXTURE-... into the clone's
    runtime.json would leave the clone permanently pointed at a sheet that does
    not exist, and a tester who later uploads the real copy would have to undo
    it. The override lives for the length of this process only.
    """
    def __init__(self, real):
        self._real = real

    def get(self, key, default=None):
        if key == "skills.lead-reply.sheet_id":
            return FIXTURE_SHEET_ID
        if key == "skills.lead-reply.exclude_tabs":
            # SEND LOG is skipped BY STRUCTURE below, not by this list — the
            # list is left as the tenant has it so that path is exercised too.
            return self._real.get(key, []) or []
        return self._real.get(key, default)

    def require(self, key):
        return self._real.require(key)


if __name__ == "__main__":
    sys.exit(main())
