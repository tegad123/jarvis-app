#!/usr/bin/env python3
"""add_calendar_event invites the addresses it was given, and only those.

The old tool inserted a title and a time and told the model "Invites nobody."
This checks the params that events.insert would receive, and how Sentinel
classifies them. It does not call Google.

  /opt/jarvis/venv/bin/python docs/model/verify_calendar_guests.py
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILLS = Path(os.environ.get("JARVIS_SKILLS_ROOT", "/opt/dev34/skills/openclaw-imports"))
os.environ["JARVIS_SKILLS_ROOT"] = str(SKILLS)
os.environ.setdefault("JARVIS_IDENTITY_CONFIG", str(SKILLS / "_shared" / "identity.json"))
os.environ.setdefault("JARVIS_RUNTIME_CONFIG", str(SKILLS / "_shared" / "runtime.json"))
sys.path.insert(0, str(SKILLS / "_shared"))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "hermes_plugin" / "jarvis"))

import run_action  # noqa: E402
from app import sentinel  # noqa: E402

results = []


def check(label, ok):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return bool(ok)


def main() -> int:
    spec = json.loads((REPO / "scripts" / "hermes_plugin" / "jarvis" / "tools.json").read_text())
    tool = next(t for t in spec["tools"] if t["name"] == "add_calendar_event")
    props = tool["parameters"]["properties"]
    check("the tool offers an attendees field", "attendees" in props and props["attendees"]["type"] == "array")
    check("the description no longer says it invites nobody", "Invites nobody" not in tool["description"])
    check("the description tells the model to pass the addresses", "attendees" in tool["description"])

    bare, invited, skipped = run_action.calendar_insert_params(
        "Photos at Adele", "2026-11-02T15:00:00-06:00", "2026-11-02T16:00:00-06:00")
    check("no guests: the body has no attendees", "attendees" not in bare["body"])
    check("no guests: sendUpdates is absent (nobody is notified)", "sendUpdates" not in bare)
    check("no guests: invited and skipped are empty", invited == [] and skipped == [])
    check("no guests classifies as an internal write",
          sentinel.classify_google("calendar", "events.insert", bare, ["assistant.invalid"])[0]
          == sentinel.INTERNAL_WRITE)

    params, invited, skipped = run_action.calendar_insert_params(
        "Photos at Adele", "2026-11-05T12:00:00-06:00", "2026-11-05T13:00:00-06:00",
        attendees=["Cassandra <cassandra@34dev.com>", "eddie@34dev.com", "eddie@34dev.com", "the owner"])
    emails = [a["email"] for a in params["body"]["attendees"]]
    check("named addresses are the guests", emails == ["cassandra@34dev.com", "eddie@34dev.com"])
    check("a repeated address is invited once", emails.count("eddie@34dev.com") == 1)
    check("a token with no address is reported, not invited", skipped == ["the owner"])
    check("guests set sendUpdates to all", params.get("sendUpdates") == "all")
    check("an outside domain is an external send",
          sentinel.classify_google("calendar", "events.insert", params, ["assistant.invalid"])
          == (sentinel.EXTERNAL_SEND, ("cassandra@34dev.com", "eddie@34dev.com")))
    check("the same guests on the team domain are an internal send",
          sentinel.classify_google("calendar", "events.insert", params, ["34dev.com"])
          == (sentinel.INTERNAL_SEND, ("cassandra@34dev.com", "eddie@34dev.com")))

    many = [f"p{i}@34dev.com" for i in range(run_action.MAX_GUESTS + 1)]
    _params, invited, skipped = run_action.calendar_insert_params(
        "overflow", "2026-11-05T12:00:00-06:00", "2026-11-05T13:00:00-06:00", attendees=many)
    check(f"the guest list stops at {run_action.MAX_GUESTS}", len(invited) == run_action.MAX_GUESTS)
    check("the address past the cap is reported, not invited", skipped == [f"p{run_action.MAX_GUESTS}@34dev.com"])

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
