"""Who may see a browser, and which gestures a standing read allowance covers.

A read allowance is per person and per site. It lets Jarvis open that site and
use search filters. Typing or submitting anything else still needs a fresh
approval. A person only ever reaches their own browser.
"""
from __future__ import annotations

import re

SEARCH = re.compile(
    r"\b(search|filter|beds?|baths?|sq\.?\s*ft|square feet|price|radius|sold|"
    r"min(?:imum)?|max(?:imum)?|zip|city|sort|page|next|previous|results?)\b",
    re.I,
)
# Contacting someone, saving a search, or signing in is not reading.
NOT_READING = re.compile(
    r"\b(contact|e-?mail|message|save search|alert me|share|agent|submit|send|"
    r"sign[\s-]?in|log[\s-]?in|password|register|create account|favorite|heart)\b",
    re.I,
)


def view_allowed(actor: str, subject: str) -> bool:
    """True only when the signed-in person is opening their own browser."""
    if not actor or not subject:
        return False
    return actor == subject


def read_only_gesture(op: str, name: str = "", text: str = "", submit: bool = False) -> bool:
    """Whether a standing 'always allow read' covers this one gesture.

    Opening, reading, scrolling and going back are reading. A click or a
    typed filter is reading only when the control is a search filter and the
    words are not a contact, a save, or a sign-in. Pressing Enter in a search
    box is the same. Anything else is not.
    """
    if op in ("open", "read", "scroll", "back", "status"):
        return True
    if op not in ("click", "type", "press"):
        return False
    blob = f"{name or ''} {text or ''}"
    if NOT_READING.search(blob):
        return False
    if op == "press":
        return bool(SEARCH.search(name or ""))
    if submit and not SEARCH.search(name or ""):
        return False
    return bool(SEARCH.search(name or "") or (op == "type" and SEARCH.search(text or "")))
