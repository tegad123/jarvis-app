#!/usr/bin/env python3
import sys; sys.exit("RETIRED 2026-10-13: testco is retired (docs/platform/decisions.md); this script needs its running services or tree.")  # noqa: E702
"""Build the forbidden-term list for the testco DOM gate.

STANDING PATTERN 1b, APPLIED EXACTLY. A redaction list DERIVES AND UNIONS —
it never replaces a hand list, and it reads the PRODUCTION registry FROM DISK
rather than through identity.load(), because this process has JARVIS_IDENTITY_CONFIG
pointed at the TESTCO fixture registry and identity.load() would hand back
Dana/Omar/Priya — the very names that are supposed to be present.

Direction check, which Pattern 1b insists on: an EXTRA term over-redacts and
fails the gate on a false alarm (annoying, safe); a MISSING term lets a real
person's name ship inside a demo image for a tester (the thing we are here to
prevent). So the list errs long, and the hand entries survive on their own if
the registry cannot be read at all.
"""
import json
import os
import re
from pathlib import Path

LIVE = Path("/root/.hermes")
SKILLS = LIVE / "skills" / "openclaw-imports"

# Hand list — survives on its own. Tenant slugs, domains, and the deal channel
# names this box has carried since the first build.
# The registry of the tenant the image is being handed TO. Set by the caller;
# its people are legitimately present and are subtracted from the term list.
TENANT_REGISTRY = os.environ.get("LEAK_TERMS_TENANT_REGISTRY", "")

HAND = [
    "34dev", "cqhouston", "spencerhuck", "34 Development",
    "Fairmont", "Larkspur", "Brook Ct", "Tiel Way", "Windmere",
    "Oak Forest", "Rusk St", "E 25th", "W 27th", "Columbia",
    "har-listings", "lonestar-title", "stewart-title", "gulfcoast-insure",
    "shoothouse", "sobharealty", "HAR.com",
]


def _from_registry() -> list:
    """Real people: display names, first names, addresses, and local parts."""
    out = []
    try:
        data = json.loads((SKILLS / "_shared" / "identity.json").read_text())
    except Exception:
        return out                      # hand list stands alone
    for key, person in (data.get("people") or {}).items():
        if key.startswith("_"):
            continue
        out.append(key)
        for field in ("display_name", "name"):
            v = person.get(field)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
                out.append(v.strip().split()[0])
        for addr in person.get("email") or []:
            out.append(addr)
            out.append(addr.split("@")[0])
    for alias in (data.get("aliases") or {}):
        if not alias.startswith("_"):
            out.append(alias)
    out += [d for d in data.get("internal_domains") or []]
    return out


def _from_feed() -> list:
    """Real counterparties and subjects the live feed has recorded."""
    out = []
    p = LIVE / "state" / "activity_feed.jsonl"
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("person"):
            out.append(e["person"])
            out.append(str(e["person"]).split()[0])
        for att in e.get("attachments") or []:
            if isinstance(att, dict):
                frm = att.get("from") or ""
                for addr in re.findall(r"[\w.+-]+@[\w.-]+", frm):
                    out.append(addr)
                    out.append(addr.split("@")[1])
    return out


def _legitimately_present() -> set:
    """Terms that MUST appear and therefore cannot be leaks.

    Subtracting is not the same as shortening the list — the derivation still
    errs long, and this removes only terms with a positive reason to be on the
    screen. Two sources, both principled:

      * THE PRODUCT'S OWN NAME. The live registry has an `assistant` person
        named Jarvis at jarvis@34dev.com, so "jarvis" derives as a real-person
        term. It is the product; it is on every screen of every tenant.

      * THE NEW TENANT'S OWN REGISTRY. The testco fixture cast collides with
        the 34dev demo cast on a first name: 34dev's demo events name a title
        officer "Priya Raman", and Testco employs a "Priya". A term that names
        a person in the RECEIVING tenant's registry cannot be evidence of the
        previous tenant's data, so it comes out.
        >>> The collision is itself a finding: two fixture casts written months
            apart shared a first name, and the gate could not tell them apart.
            Fixture casts should be kept disjoint on first names.
    """
    out = {"jarvis", "assistant"}
    try:
        data = json.loads(
            (Path(TENANT_REGISTRY)).read_text()) if TENANT_REGISTRY else {}
    except Exception:
        return out
    for key, person in (data.get("people") or {}).items():
        if key.startswith("_"):
            continue
        out.add(key.lower())
        for field in ("display_name", "name"):
            v = person.get(field)
            if isinstance(v, str) and v.strip():
                out.add(v.strip().lower())
                out.add(v.strip().split()[0].lower())
    return out


def terms() -> list:
    keep_out = _legitimately_present()
    seen, out = set(), []
    for t in HAND + _from_registry() + _from_feed():
        t = (t or "").strip()
        # One- and two-character fragments match everything; they would make the
        # gate fail on the word "of" rather than on a leak.
        if len(t) < 3:
            continue
        if t.lower() in seen or t.lower() in keep_out:
            continue
        seen.add(t.lower())
        out.append(t)
    return sorted(out, key=str.lower)


if __name__ == "__main__":
    t = terms()
    print(json.dumps(t, indent=1))
    print(f"\n{len(t)} terms ({len(HAND)} hand + derived)")
