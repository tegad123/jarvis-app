"""MLS search: property searches on HAR MLS Matrix, run through the asking
person's own signed-in browser (docs/plans/browser-har-comps.md).

There is no MLS switch. A person may search exactly when available_for()
finds every condition in plan section 7 true, walked in that order; the first
that fails is the answer and bumps mls_refused_<why>.
"""
import logging
import sys
from typing import Any, Dict, Optional, Tuple

from . import accounts
from . import browser
from . import config
from . import mls_limits
from . import threads as threads_mod

log = logging.getLogger("app.mls")

UNAVAILABLE = {
    "browser_off": "The web browser isn't on for this team.",
    "not_headed": "MLS search needs the browser in its live mode, so you can sign in to Matrix yourself.",
    "config_invalid": "The MLS search settings for this team aren't valid. Ask your operator.",
    "not_configured": "MLS search isn't set up for this team yet (no Matrix start page). Ask your operator.",
    "not_a_person": "MLS search runs for a signed-in person only.",
    "browser_feature_off": "Your web browser is switched off. Ask your admin.",
}


def _tc():
    if str(config.SHARED) not in sys.path:
        sys.path.insert(0, str(config.SHARED))
    import tenant_config as tc  # noqa: PLC0415
    return tc


def settings() -> Tuple[Dict[str, Any], bool]:
    """(app.mls with defaults, valid). An unreadable or invalid block is
    announced and reads as unavailable; nothing is clamped."""
    try:
        raw = _tc().get("app.mls", None)
    except Exception as exc:
        threads_mod.bump_counter("mls_config_unreadable")
        log.error("mls: config unreadable (%s); MLS search is unavailable", type(exc).__name__)
        return dict(mls_limits.DEFAULTS), False
    out, bad = mls_limits.check(raw)
    if bad:
        threads_mod.bump_counter("mls_config_invalid")
        log.error("mls: app.mls refused (%d problems); MLS search is unavailable", len(bad))
        return out, False
    return out, True


def browser_mode() -> str:
    try:
        mode = (_tc().get("app.browser", {}) or {}).get("mode")
    except Exception as exc:
        threads_mod.bump_counter("mls_config_unreadable")
        log.error("mls: app.browser unreadable (%s); treated as headless", type(exc).__name__)
        return "headless"
    return mode if mode in ("headless", "headed") else "headless"


def available_for(person: Optional[str], *, count: bool = True) -> Optional[str]:
    """None when this person may search; otherwise the first condition that is off."""
    why = _first_off(person)
    if why and count:
        threads_mod.bump_counter(f"mls_refused_{why}")
    return why


def _first_off(person: Optional[str]) -> Optional[str]:
    if not browser.enabled():
        return "browser_off"
    if browser_mode() != "headed":
        return "not_headed"
    s, valid = settings()
    if not valid:
        return "config_invalid"
    if not s["start_url"]:
        return "not_configured"
    if not person:
        return "not_a_person"
    if not accounts.feature_on(person, "browser"):
        return "browser_feature_off"
    return None
