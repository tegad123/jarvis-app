"""Read sold comps from a page Jarvis already has open.

This does not log in, solve a check, or submit a form. It reads text the
browser already returned and stops when that text is a sign-in wall, a
human check, or a block. The numbers below are the shape of a comps table,
not a claim about any particular listing.
"""
from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List, Optional

MAX_PAGES = 3
PAUSE_SECONDS = 2.0

# A row the browser's reading of a results page can be matched against.
# Fixtures use this shape. Real pages are reduced to the same lines by
# comps_lines() before parsing.
ROW = re.compile(
    r"^(?P<address>.+?)\s*\|\s*(?P<sold>\d{4}-\d{2}-\d{2})\s*\|\s*"
    r"\$(?P<price>[\d,]+)\s*\|\s*(?P<beds>\d+(?:\.\d+)?)\s*bd\s*\|\s*"
    r"(?P<baths>\d+(?:\.\d+)?)\s*ba\s*\|\s*(?P<sqft>[\d,]+)\s*sqft\s*\|\s*"
    r"(?P<year>\d{4}|—)\s*\|\s*(?P<dom>\d+)\s*dom\s*\|\s*(?P<miles>\d+(?:\.\d+)?)\s*mi\s*$",
    re.I,
)

_STOP = (
    ("captcha", re.compile(r"prove you(?:'re| are) human|captcha|i am not a robot|verify you are human", re.I)),
    ("login", re.compile(
        r"sign in to (continue|your|har)|log in to (your|har)|enter your password|"
        r"session expired|please log in|authentication required", re.I)),
    ("blocked", re.compile(r"access denied|too many requests|\bhttp 403\b|\bhttp 429\b|blocked", re.I)),
)


def page_stop(text: str) -> Optional[str]:
    """login, captcha, or blocked. None when the page can be read."""
    sample = text or ""
    for kind, pat in _STOP:
        if pat.search(sample):
            return kind
    return None


def stop_message(kind: str) -> str:
    if kind == "captcha":
        return ("HAR is showing a check only a person can complete. Tap Take over, "
                "finish it yourself, then tell Jarvis to try again. Jarvis will not solve it.")
    if kind == "login":
        return ("HAR wants a sign-in. Tap Take over and sign in yourself, including any code "
                "the site sends. Jarvis will not type a password or a code.")
    return ("HAR refused this browser. Nothing was submitted. Try again later, or use Take over "
            "if the page is asking you to do something.")


def parse_comps(text: str) -> List[Dict[str, Any]]:
    """Rows from a sanitized results reading. Stops at a wall before parsing."""
    if page_stop(text):
        return []
    out = []
    for line in (text or "").splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        price = int(m.group("price").replace(",", ""))
        sqft = int(m.group("sqft").replace(",", ""))
        year = m.group("year")
        out.append({
            "address": m.group("address").strip(),
            "sold_date": m.group("sold"),
            "sold_price": price,
            "price_per_sqft": round(price / sqft) if sqft else None,
            "beds": _num(m.group("beds")),
            "baths": _num(m.group("baths")),
            "sqft": sqft,
            "year_built": int(year) if year.isdigit() else None,
            "dom": int(m.group("dom")),
            "distance_mi": float(m.group("miles")),
        })
    return out


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Median dollars per square foot, and a range around it. Empty when there are no rows."""
    ppsf = [r["price_per_sqft"] for r in rows if r.get("price_per_sqft")]
    if not ppsf:
        return {"count": len(rows), "median_price_per_sqft": None, "suggested_low": None, "suggested_high": None}
    med = int(statistics.median(ppsf))
    return {
        "count": len(rows),
        "median_price_per_sqft": med,
        "suggested_low": int(med * 0.95),
        "suggested_high": int(med * 1.05),
    }


def search_url(address: str, *, radius_mi: float = 0.5, months: int = 6,
               beds: Optional[str] = None, baths: Optional[str] = None,
               sqft: Optional[str] = None) -> str:
    """The public HAR sold-search address. Reading it is not a submission."""
    from urllib.parse import urlencode
    q = {
        "for_sale": "0",
        "sold_within_months": str(max(1, min(int(months), 24))),
        "radius": str(radius_mi),
        "streetaddress": " ".join((address or "").split())[:200],
    }
    if beds:
        q["beds"] = beds[:20]
    if baths:
        q["baths"] = baths[:20]
    if sqft:
        q["sqft"] = sqft[:40]
    return "https://www.har.com/search/dosearch?" + urlencode(q)


def collect(pages: List[str]) -> Dict[str, Any]:
    """Read up to MAX_PAGES snapshots. The first wall ends the read."""
    rows: List[Dict[str, Any]] = []
    seen = set()
    for i, page in enumerate(pages[:MAX_PAGES]):
        kind = page_stop(page)
        if kind:
            return {"ok": False, "stopped": kind, "say": stop_message(kind), "comps": rows,
                    "summary": summarize(rows), "pages_read": i}
        for row in parse_comps(page):
            key = (row["address"], row["sold_date"], row["sold_price"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return {"ok": True, "stopped": None, "say": "", "comps": rows, "summary": summarize(rows),
            "pages_read": min(len(pages), MAX_PAGES)}


def _num(raw: str) -> float:
    return float(raw) if "." in raw else int(raw)
