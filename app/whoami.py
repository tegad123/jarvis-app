"""Who is signed in?

Resolved through the shared identity registry (_shared/identity.py) — the same
lookup the skills use for privileged actions — so a new tenant changes
identity.json and nothing here. The owner's name is never hardcoded.

Failure is non-fatal by design: if the registry is missing or malformed the
session response simply omits display_name and the UI greets the user without
a name. A greeting is not worth a 500.
"""

import sys
from typing import Optional

from . import config

if str(config.SHARED) not in sys.path:
    sys.path.insert(0, str(config.SHARED))


def display_name_for(person_key: Optional[str]) -> Optional[str]:
    """Short display name for a person key, or None if unresolvable.

    WHY THIS REPLACED A BARE owner_display_name() ON EVERY SESSION: the app
    used to greet whoever signed in with the TENANT OWNER'S name, because that
    was the only name it knew. Once sessions carry a person that is simply
    wrong — a member read "Hi, Spencer", and so did a break-glass operator,
    which is the worst version of it: the screen asserts an identity the
    session does not have.
    """
    try:
        import identity  # noqa: PLC0415  (path must be set first)

        key = person_key or identity.owner_key()
        if not key:
            return None
        person = identity.load().get("people", {}).get(key) or {}
        name = person.get("display_name") or person.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except Exception:
        # Registry unreadable, no owner role configured, unexpected shape —
        # all of these degrade to "no name" rather than an error.
        return None
    return None
