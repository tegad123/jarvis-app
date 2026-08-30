"""Apply an approved config-change request to the tenant's runtime config.

THE PRODUCER THIS EXISTS FOR
    Workstream 5 tier 1 of the tenant-two spec: a user asks Jarvis in chat for a
    settings change, Jarvis maps it to specific config keys and proposes them as
    an APPROVALS ITEM with a human-readable diff; the owner approves; this
    applies it. Roughly 80% of "change requests" are settings, not code.

    No producer is wired yet. This is the receiving half, built first so the
    approvals contract absorbs its shape while that contract is still cheap to
    change (see "THIS CONTRACT IS DISPOSABLE" in the worklist).

THREE THINGS THIS REFUSES TO DO, AND WHY EACH IS STRUCTURAL

  1. NEVER-APPLIABLE KEYS ARE REFUSED BY CONSTRUCTION, NOT BY CONFIG.
     `safety.*`, `credentials.*` and `identity.*` can never be changed through
     this path. That is a code-level prefix check with no override, no config
     flag, and no "unless" — because the whole point of a chat-driven config
     path is that a persuasive request reaches it. `safety.allow_writes` is the
     kill switch that stands between this box and a customer's live mailbox;
     "the owner approved it in chat" is not the bar for arming that. Changing
     these is a deliberate, out-of-band act with the cutover checklist open.

  2. A STALE DIFF IS REFUSED. Each change carries the value the proposal was
     written against. If the live value has moved since, the change is refused
     and the request stays pending. Same reasoning as the 409 on re-deciding an
     already-decided request: the human agreed to a specific before/after, and
     silently applying it to a different `before` is not what they approved.

  3. PARTIAL APPLICATION IS IMPOSSIBLE. Every change in a request is validated
     BEFORE any is written, and the write is atomic (temp file + rename) with a
     timestamped backup. A request either lands whole or not at all — a config
     half-changed by a rejected-midway apply is worse than one not changed.
"""

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import config

if str(config.SHARED) not in sys.path:
    sys.path.insert(0, str(config.SHARED))

import tenant_config as tc  # noqa: E402

# Prefixes that this path may NEVER write. Not a setting — a constant, checked
# in code, with no override anywhere in the module.
NEVER_APPLIABLE = ("safety", "credentials", "identity")

# Positive allowlist of what a chat-driven proposal may touch. Deliberately
# narrow: a key absent here is refused, so adding capability is an explicit act
# rather than something that leaks in as config grows.
APPLIABLE_PREFIXES = (
    "skills.",
    "client.timezone",
    "client.display_name",
    "client.business_description",
)


class ConfigApplyError(RuntimeError):
    """Refusal to apply. The message is shown to the owner."""


def _runtime_path() -> Path:
    return Path(tc.CONFIG_PATH)


def _load_runtime() -> Dict[str, Any]:
    return json.loads(_runtime_path().read_text())


def _dotted_get(data: Dict[str, Any], key: str) -> Tuple[bool, Any]:
    """(found, value). `found` distinguishes a missing key from a null value."""
    cur: Any = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _dotted_set(data: Dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def check_key(key: str) -> None:
    """Raise unless this key may be written through the approvals path."""
    key = (key or "").strip()
    if not key:
        raise ConfigApplyError("A change is missing its config key.")

    root = key.split(".", 1)[0]
    if root in NEVER_APPLIABLE:
        raise ConfigApplyError(
            f"'{key}' can never be changed through an approval. Keys under "
            f"{', '.join(NEVER_APPLIABLE)} are excluded in code, not by configuration — "
            f"they govern whether this deployment may write to a customer's systems at "
            f"all, and that is changed deliberately and out of band."
        )
    if not any(key == p or key.startswith(p) for p in APPLIABLE_PREFIXES):
        raise ConfigApplyError(
            f"'{key}' is not in the set this path may change. Appliable: "
            f"{', '.join(APPLIABLE_PREFIXES)}."
        )


def validate(changes: List[Dict[str, Any]]) -> None:
    """Validate every change against key policy AND current values. Raises on first failure.

    Runs to completion over key policy before touching values, so a request that
    is refused for naming a forbidden key says so plainly rather than reporting
    a stale-value mismatch on some earlier key.
    """
    if not changes:
        raise ConfigApplyError("This request contains no changes to apply.")

    for change in changes:
        check_key(str(change.get("key", "")))

    runtime = _load_runtime()
    for change in changes:
        key = str(change["key"])
        found, live = _dotted_get(runtime, key)
        expected = change.get("current")
        if not found:
            if expected not in (None, ""):
                raise ConfigApplyError(
                    f"'{key}' no longer exists in the tenant config, but this request "
                    f"expected it to be {expected!r}. Nothing was changed."
                )
            continue
        if live != expected:
            raise ConfigApplyError(
                f"'{key}' has changed since this was proposed: it is now {live!r}, but the "
                f"request was written against {expected!r}. Nothing was changed — the "
                f"approval was for a specific before-and-after. Re-propose against the "
                f"current value."
            )


def apply_changes(changes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate then atomically write. Returns a summary of what moved."""
    validate(changes)

    path = _runtime_path()
    runtime = _load_runtime()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_suffix(path.suffix + f".pre-apply.{stamp}")
    shutil.copy2(path, backup)

    applied = []
    for change in changes:
        key = str(change["key"])
        _dotted_set(runtime, key, change.get("proposed"))
        applied.append({"key": key, "from": change.get("current"),
                        "to": change.get("proposed")})

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(runtime, indent=2) + "\n")
    tmp.replace(path)          # atomic on the same filesystem

    # Every reader caches. A change nobody sees until the next restart would
    # look like the approval did nothing.
    tc.reset_cache()

    return {"applied": applied, "backup": str(backup), "count": len(applied)}
