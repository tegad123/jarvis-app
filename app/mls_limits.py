"""The MLS search settings, their defaults and the hard limits in code.

Standard library only: scripts/provision_tenant.py imports this file directly
to validate a manifest's `mls:` block, and app/mls.py reads the rendered
`app.mls` through the same check, so a value the provisioner refused can't
reach a run by another path. A setting may lower a limit, never raise it; a
value outside a limit is refused, not clamped.
"""
import re
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit

DEFAULTS: Dict[str, Any] = {
    "site": "harmls.com",
    "start_url": "",
    "max_page_loads_per_request": 10,
    "max_requests_per_person_per_day": 5,
    "max_page_loads_per_person_per_day": 40,
    "max_results": 25,
    "pause_seconds": [4, 9],
    "cooldown_hours_after_block": 24,
    "keep_days": 30,
}
CEILINGS = {
    "max_page_loads_per_request": 10,
    "max_requests_per_person_per_day": 20,
    "max_page_loads_per_person_per_day": 100,
    "max_results": 100,
    "keep_days": 90,
}
MIN_PAUSE_S = 3
MAX_PAUSE_S = 60
MIN_COOLDOWN_H = 1
MAX_COOLDOWN_H = 24 * 14
SITE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check(block: Any) -> Tuple[Dict[str, Any], List[str]]:
    """(settings with defaults filled in, problems). Any problem means the
    block is unusable as a whole; the caller refuses it."""
    if block is None:
        block = {}
    if not isinstance(block, dict):
        return dict(DEFAULTS), ["mls must be a mapping"]
    out = dict(DEFAULTS)
    bad: List[str] = []
    unknown = set(block) - set(DEFAULTS)
    if unknown:
        bad.append(f"mls has unknown keys: {sorted(unknown)}")
    out.update({k: block[k] for k in DEFAULTS if k in block})
    site = out["site"]
    if not isinstance(site, str) or not SITE.fullmatch(site) or site.startswith("www."):
        bad.append("mls.site must be a site name like harmls.com")
        site = ""
    url = out["start_url"]
    if not isinstance(url, str):
        bad.append("mls.start_url must be an https address")
    elif url:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not host or parts.username or parts.password or parts.port not in (None, 443):
            bad.append("mls.start_url must be a plain https address")
        elif site and host != site and not host.endswith("." + site):
            bad.append(f"mls.start_url must be on {site}")
    for k, top in CEILINGS.items():
        v = out[k]
        if not _whole(v) or not 1 <= v <= top:
            bad.append(f"mls.{k} must be a whole number, 1 to {top}")
    p = out["pause_seconds"]
    if not (isinstance(p, list) and len(p) == 2 and all(_whole(x) or isinstance(x, float) for x in p)
            and not any(isinstance(x, bool) for x in p) and MIN_PAUSE_S <= p[0] <= p[1] <= MAX_PAUSE_S):
        bad.append(f"mls.pause_seconds must be [low, high] seconds, {MIN_PAUSE_S} to {MAX_PAUSE_S}, low first")
    c = out["cooldown_hours_after_block"]
    if not _whole(c) or not MIN_COOLDOWN_H <= c <= MAX_COOLDOWN_H:
        bad.append(f"mls.cooldown_hours_after_block must be a whole number, {MIN_COOLDOWN_H} to {MAX_COOLDOWN_H}")
    if not bad and out["max_page_loads_per_request"] > out["max_page_loads_per_person_per_day"]:
        bad.append("mls.max_page_loads_per_request can't be above max_page_loads_per_person_per_day")
    return out, bad
