"""Per-turn actor references: who is signed in, for the agent's tools.

Every chat turn the app proxies to the gateway carries X-Jarvis-Actor-Ref, a
random reference minted here from the signed-in session. The gateway binds it
as JARVIS_ACTOR_MSG in the environment of that turn's tool calls, and the
skills' authz resolves it back through the broker (POST /api/broker/actor).
The person therefore comes from the session, never from the model's text.

A reference is valid while its turn runs and for GRACE_S after it ends, and
never longer than MAX_AGE_S. In memory only: the app is one process, a turn
lives inside it, and a restart ends every turn.
"""
from __future__ import annotations

import secrets
import threading
import time
from typing import Dict, Optional, Tuple

PREFIX = "app:"
MAX_AGE_S = 900
GRACE_S = 60
_MAX_LIVE = 1000

_lock = threading.Lock()
_refs: Dict[str, dict] = {}


def mint(person_key: str, thread_id: str) -> str:
    ref = PREFIX + secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        for k in [k for k, v in _refs.items() if _expired(v, now)]:
            del _refs[k]
        if len(_refs) >= _MAX_LIVE:
            del _refs[min(_refs, key=lambda k: _refs[k]["created"])]
        _refs[ref] = {"person_key": person_key, "thread_id": thread_id, "created": now, "ended": None}
    return ref


def end(ref: str) -> None:
    with _lock:
        if ref in _refs and _refs[ref]["ended"] is None:
            _refs[ref]["ended"] = time.time()


def _expired(entry: dict, now: float) -> bool:
    if now - entry["created"] > MAX_AGE_S:
        return True
    return entry["ended"] is not None and now - entry["ended"] > GRACE_S


def resolve(ref: str) -> Tuple[Optional[dict], str]:
    """(entry, "") for a live reference, else (None, "unknown" | "expired")."""
    now = time.time()
    with _lock:
        entry = _refs.get(ref)
        if entry is None:
            return None, "unknown"
        if _expired(entry, now):
            del _refs[ref]
            return None, "expired"
        return dict(entry, age_s=int(now - entry["created"])), ""


def active_count() -> int:
    """Turns still running (not ended), for the in-gateway guard."""
    now = time.time()
    with _lock:
        return sum(1 for v in _refs.values() if v["ended"] is None and now - v["created"] <= MAX_AGE_S)
