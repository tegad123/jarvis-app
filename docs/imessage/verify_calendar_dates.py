#!/usr/bin/env python3
"""Relative days in a text become a real America/Chicago start time.

No model, no Google, no tenant, no network. The Traci case is the one Spencer
sent on Monday 9 Nov 2026: she offered tomorrow night at 7:30, he answered at
7:52 PM that he would be there at 7:15.
"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.appointment_time import CHICAGO, resolve_appointment  # noqa: E402

ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" ({extra})" if extra and not cond else ""))


def at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=CHICAGO)


def iso(start):
    return start.isoformat(timespec="seconds") if start else None


def main() -> int:
    print("── Traci, Monday night ──")
    traci_at = at(2026, 11, 9, 18, 52)
    spencer_at = at(2026, 11, 9, 19, 52)
    prior = [("Traci: I have no openings but I can stay late tomorrow night if you come at 7:30pm", traci_at)]
    title = "Go to Traci's for haircut at 7:15 PM tomorrow"
    span = resolve_appointment(
        "Amazing I will be there at 7:15 PM, See you there",
        spencer_at,
        prior,
        [title],
    )
    ck("start is Tue 10 Nov 2026 7:15 PM CT",
       span is not None and iso(span[0]) == "2026-11-10T19:15:00-06:00",
       iso(span[0]) if span else "none")
    ck("default length is one hour",
       span is not None and iso(span[1]) == "2026-11-10T20:15:00-06:00",
       iso(span[1]) if span else "none")

    # The day lives only in her message; his text only confirms the clock.
    span = resolve_appointment(
        "Amazing I will be there at 7:15 PM, See you there",
        spencer_at,
        prior,
        ["Go to Traci's"],
    )
    ck("her 'tomorrow night' plus his 7:15 still lands on Tuesday",
       span is not None and iso(span[0]) == "2026-11-10T19:15:00-06:00",
       iso(span[0]) if span else "none")

    print("── no day, no event ──")
    span = resolve_appointment("I will be there at 7:15 PM", spencer_at, [], ["Be there at 7:15 PM"])
    ck("a clock with no day is not an event", span is None)

    print("── tonight ──")
    span = resolve_appointment("see you tonight at 7:15 PM", at(2026, 11, 9, 16, 0))
    ck("tonight is the same local date",
       span is not None and iso(span[0]) == "2026-11-09T19:15:00-06:00",
       iso(span[0]) if span else "none")

    print("── after midnight, tomorrow is the next calendar day ──")
    span = resolve_appointment("tomorrow at 7:15 PM", at(2026, 11, 10, 0, 30))
    ck("12:30 AM Tuesday 'tomorrow' is Wednesday",
       span is not None and iso(span[0]) == "2026-11-11T19:15:00-06:00",
       iso(span[0]) if span else "none")

    print("── weekday and next weekday ──")
    monday = at(2026, 11, 9, 18, 0)
    span = resolve_appointment("Tuesday at 7:15 PM", monday)
    ck("Monday 'Tuesday' is the next day",
       span is not None and iso(span[0]) == "2026-11-10T19:15:00-06:00",
       iso(span[0]) if span else "none")
    span = resolve_appointment("next Tuesday at 3 PM", monday)
    ck("Monday 'next Tuesday' skips the coming Tuesday",
       span is not None and iso(span[0]) == "2026-11-17T15:00:00-06:00",
       iso(span[0]) if span else "none")
    span = resolve_appointment("Tuesday at 3 PM", at(2026, 11, 10, 10, 0))
    ck("Tuesday morning 'Tuesday at 3' is still today",
       span is not None and iso(span[0]) == "2026-11-10T15:00:00-06:00",
       iso(span[0]) if span else "none")
    span = resolve_appointment("Tuesday at 3 PM", at(2026, 11, 10, 16, 0))
    ck("Tuesday after 3 'Tuesday at 3' is the following Tuesday",
       span is not None and iso(span[0]) == "2026-11-17T15:00:00-06:00",
       iso(span[0]) if span else "none")

    print("── explicit date ──")
    span = resolve_appointment("November 10 at 7:15 PM", monday)
    ck("a written month and day is that day",
       span is not None and iso(span[0]) == "2026-11-10T19:15:00-06:00",
       iso(span[0]) if span else "none")

    print("── stated length ──")
    span = resolve_appointment("tomorrow at 7:15 PM for 30 minutes", spencer_at)
    ck("for 30 minutes is not the one-hour default",
       span is not None and iso(span[1]) == "2026-11-10T19:45:00-06:00",
       iso(span[1]) if span else "none")

    print("── DST ──")
    # 2026-03-08 02:00 springs forward (CST to CDT). 2026-11-01 02:00 falls back.
    span = resolve_appointment("tomorrow at 7:15 PM", at(2026, 3, 7, 20, 0))
    ck("the night before spring-forward is CDT",
       span is not None and iso(span[0]) == "2026-03-08T19:15:00-05:00",
       iso(span[0]) if span else "none")
    before_fall = datetime(2026, 10, 31, 20, 0, tzinfo=ZoneInfo("America/Chicago"))
    span = resolve_appointment("tomorrow at 7:15 PM", before_fall)
    ck("the night before fall-back is CST",
       span is not None and iso(span[0]) == "2026-11-01T19:15:00-06:00",
       iso(span[0]) if span else "none")

    print("── the watch fills the plan the model left empty ──")
    import types
    ident = types.ModuleType("identity")
    ident.load = lambda force=False: {"people": {}}
    ident.resolve = lambda raw: None
    ident.display_name_for = lambda key: key or ""
    ident.feature_on = lambda *args, **kwargs: True
    sys.modules["identity"] = ident
    tenant = types.ModuleType("tenant_config")
    tenant.get = lambda path, default=None, announce=True: default
    tenant.timezone_name = lambda: "America/Chicago"
    tenant.announce_mismatch = lambda *args, **kwargs: None
    tenant.reset_cache = lambda: None
    sys.modules["tenant_config"] = tenant
    from app.imessage_watch import _complete_plan
    plan = {"tasks": [{"title": title, "deadline": ""}], "calendar": None}
    filled = _complete_plan(
        plan,
        "Amazing I will be there at 7:15 PM, See you there",
        "2026-11-09T19:52:00-06:00",
        prior,
    )
    ck("an empty calendar becomes the Tuesday 7:15 start",
       (filled.get("calendar") or {}).get("start", "").startswith("2026-11-10T19:15:00"))
    ck("the task deadline is that same day", filled["tasks"][0]["deadline"] == "2026-11-10")
    empty = _complete_plan(
        {"tasks": [{"title": "Be there at 7:15 PM", "deadline": ""}], "calendar": None},
        "I will be there at 7:15 PM",
        "2026-11-09T19:52:00-06:00",
        [],
    )
    ck("still no event when nobody named a day", empty.get("calendar") in (None, {}))

    print(f"\n{sum(ok)}/{len(ok)} passed")
    return 0 if ok and all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
