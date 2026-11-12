"""Which browser gestures only read, for a standing read allowance (docs/SPEC.md 8.6).

An allowance is per person and per site. It covers clicking a link, tab,
pagination or sort control, ticking a listing-status or property-type box in
that site's own search form, and typing into a search or filter field (and
submitting that search). Nothing else: the rule is default-deny, and a
gesture it does not clearly recognise falls through to the ordinary 30-minute
task approval. browser.risky() runs before this and still holds payments,
deletions and the like one at a time.

Decided from facts the worker reports about the element (role, tag, input
type, link target, the form it sits in), never from what the model says it
is doing. Names are checked against a deny list first: contacting someone,
saving, sharing, signing in or anything that makes an account is never reading.
"""
import re
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

NOT_READING = re.compile(
    r"\b(contact|e-?mail|message|chat|call|phone|text me|save|saved|alert|notify|share|schedule|tour|showing|"
    r"favou?rites?|heart|like|follow|print|export|download|upload|attach|sign[\s-]?(in|up|out)|log[\s-]?(in|out)|"
    r"register|join|create|password|my account|profile|settings|subscribe|offer|apply now|apply online|request|"
    r"buy|pay|delete|remove|edit|add to|new listing|report|flag|hide|dismiss|close account)\b", re.I)
PAGING = re.compile(r"^(next|previous|prev|first|last|next page|previous page|more results|show more|load more|"
                    r"page \d{1,3}|go to page \d{1,3}|\d{1,3}|[‹›«»<>]{1,2}|(next|prev(ious)?) ?[›»>]{0,2})$", re.I)
SORT = re.compile(r"\bsort(ed)?\b|^(price|list price|sold price|close price|sold date|close date|date|beds?|baths?|"
                  r"sq\.? ?ft|square feet|address|distance|dom|days on market|year built)( ?[↑↓▲▼])?"
                  r"( (ascending|descending|asc|desc))?$", re.I)
SEARCH_BUTTON = re.compile(r"^(search|find|go|apply|apply filters?|filter|update|update results|refine|"
                           r"show results|view results|see results|results|count|search now)$", re.I)
FILTER_FIELD = re.compile(
    r"search|query|keyword|\bq\b|filter|address|street|zip|postal|city|county|subdivision|neighbou?rhood|area|"
    r"radius|distance|miles|\bbeds?\b|bedrooms?|\bbaths?\b|bathrooms?|price|sq\.? ?ft|square|footage|acre|lot ?size|"
    r"year|built|status|sold|closed|close ?date|date|\bfrom\b|\bto\b|\bmin\b|\bmax\b|minimum|maximum|days|\bdom\b|"
    r"property ?type|style|mls ?area|school|garage|stories", re.I)
SENSITIVE_FIELD = re.compile(r"e-?mail|phone|\btel\b|mobile|first.?name|last.?name|full.?name|your name|"
                             r"message|comment|note|password|passcode|one.?time|otp|(?<!zip )(?<!postal )\bcode\b|card|cvv|ssn|"
                             r"account|user(name)?|login|signature|recipient|subject|\bsend\b|\bcc\b", re.I)
# Exact labels only: a box named anything else ("Remember me", "I agree",
# "Email me updates") is not a search filter.
FILTER_TOGGLE = re.compile(
    r"^(active|active option( contract)?|option pending|pending|pending continue to show|sold|closed|"
    r"single[- ]family( detached)?|townhouse ?/ ?condo|townhouse|condo(minium)?|multi[- ]family|"
    r"lots?|land|lots? ?/ ?land|country homes? ?/ ?acreage)$", re.I)
TEXT_TYPES = ("", "text", "search", "number", "date", "month")


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def read_only_gesture(op: str, d: Dict[str, Any], text: str = "", submit: bool = False,
                      here: str = "") -> Optional[str]:
    """The kind of reading this gesture is ("link", "tab", "pagination",
    "sort", "filter", "search"), or None when it is not clearly reading."""
    role = _norm(d.get("role")).lower()
    tag = _norm(d.get("tag")).lower()
    itype = _norm(d.get("input_type")).lower()
    name = _norm(d.get("name"))
    field = _norm(d.get("field"))
    if NOT_READING.search(name):
        return None
    if op == "click":
        button = role == "button" or tag == "button" or itype in ("submit", "button", "image")
        if PAGING.match(name):
            return "pagination"
        if (role in ("columnheader", "button", "link") or button) and SORT.search(name):
            return "sort"
        if button:
            return "search" if SEARCH_BUTTON.match(name) else None
        if role == "checkbox" or itype == "checkbox":
            same_form = bool(_norm(d.get("form_method"))) and bool(here) and _norm(d.get("form_site")) == here
            return "filter" if same_form and FILTER_TOGGLE.match(name) else None
        if role == "tab":
            return "tab"
        if role == "link" and tag == "a":
            href = _norm(d.get("href"))
            scheme = urlsplit(href).scheme.lower()
            if d.get("download") or scheme not in ("http", "https"):
                return None
            if here and _norm(d.get("link_site")) != here:
                return None
            return "link"
        return None
    if op == "type":
        if tag == "textarea" or itype not in TEXT_TYPES or d.get("contenteditable"):
            return None
        if SENSITIVE_FIELD.search(f"{name} {field}"):
            return None
        t = text.strip()
        if "@" in t or len(t) > 120 or "\n" in text:
            return None
        searchbox = role == "searchbox" or itype == "search"
        filter_field = role in ("textbox", "combobox", "spinbutton", "searchbox") and \
            bool(FILTER_FIELD.search(f"{name} {field}"))
        if not (searchbox or filter_field):
            return None
        if submit and _norm(d.get("form_site")) and here and _norm(d.get("form_site")) != here:
            return None
        return "search"
    return None
