"""Per-piece kill switches: JARVIS_SWITCH_BROKER, JARVIS_SWITCH_SENTINEL,
JARVIS_SWITCH_MODEL_PROXY, each `on` (default) or `off`.

Off always fails closed, never open:
- broker off: every /api/broker endpoint answers 503 broker_disabled, so the
  agent can neither read Google nor ask for a write.
- sentinel off: every write, outbound proposal and approval answers 503
  sentinel_off; reads still work. The app then boots without the platform
  database, which is the point of the switch.
- model_proxy off: /api/model answers 503 model_disabled.

Any other value refuses boot, so a typo cannot read as `on`.
"""
import os
from typing import Dict

NAMES = ("broker", "sentinel", "model_proxy")


class BadSwitch(ValueError):
    pass


def state() -> Dict[str, bool]:
    out = {}
    for n in NAMES:
        raw = os.environ.get(f"JARVIS_SWITCH_{n.upper()}", "on").strip().lower() or "on"
        if raw not in ("on", "off"):
            raise BadSwitch(f"JARVIS_SWITCH_{n.upper()} must be on or off")
        out[n] = raw == "on"
    return out


def on(name: str) -> bool:
    return state()[name]
