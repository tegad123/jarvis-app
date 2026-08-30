"""Which task list a tenant is on, and the shape the Tasks screen reads.

TWO HOMES, ONE SCREEN — the leads_source pattern, for the same reason. Tenant
zero's tasks live in the task-manager skill's own SQLite list and are read by
an agent that answers in chat; a fresh tenant's tasks live in the app's store
and are read on the Tasks tab. Both must work at the same time, and switching
a tenant over has to be a config change, not a frontend release.

    JARVIS_TASKS_SOURCE=store|legacy   env, for harnesses and sandboxes
    skills.tasks.source                tenant config
    (absent)                           follows skills.leads.source — a tenant
                                       whose leads are app-native is app-native

DELIBERATELY NOT IMPORTING leads_source. That module pulls in the Google Sheet
reader, and this one is imported by the task-manager SKILL from the gateway's
venv (via _shared/app_tasks.py). The rule for the seam is stdlib + config.
"""

import os
import sys
from typing import Any, Dict, Optional

from . import config, tasks_store


def _tc():
    p = str(config.SHARED)
    if p not in sys.path:
        sys.path.insert(0, p)
    import tenant_config as tc
    return tc


def source() -> str:
    explicit = (os.environ.get("JARVIS_TASKS_SOURCE") or "").strip().lower()
    if explicit in ("store", "legacy"):
        return explicit
    try:
        tc = _tc()
        configured = (tc.get("skills.tasks.source", "") or "").strip().lower()
        if configured in ("store", "legacy"):
            return configured
        leads = (tc.get("skills.leads.source", "") or "").strip().lower()
        return "store" if leads == "store" else "legacy"
    except Exception:
        # No readable config is not an app-native tenant. Conservative on
        # purpose: writing tasks into a store nobody reads is a silent loss.
        return "legacy"


LEGACY_SENTENCE = ("Tasks for this tenant still live in the assistant's own list; "
                   "creating in-app is disabled until it is migrated to app-native storage.")


def load(bodies: bool, status: Optional[Any] = None, priority: Optional[str] = None,
         assignee: Optional[str] = None, stale: Optional[bool] = None,
         archived: bool = False) -> Dict[str, Any]:
    """The Tasks screen payload.

    STRUCTURE, NOT BODIES — the threads rule, applied here. A task's title,
    who it is for, its priority and its dates are structure; `detail` is the
    body. When `bodies` is False (the break-glass operator) every detail is
    nulled, and the payload SAYS so with `bodies_visible`, so the screen can
    reflect the gate honestly rather than showing a blank that looks like
    "nobody wrote anything".
    """
    src = source()
    if src != "store":
        return {"tasks": [], "count": 0, "counts": {}, "source": src,
                "bodies_visible": bodies, "timezone": tasks_store.tz_name(),
                "notice": LEGACY_SENTENCE}
    rows = tasks_store.query(status=status, priority=priority, assignee=assignee,
                             stale=stale, archived=archived)
    if not bodies:
        for t in rows:
            t["detail"] = None
    return {"tasks": rows, "count": len(rows), "counts": tasks_store.counts(),
            "source": "store", "bodies_visible": bodies, "timezone": tasks_store.tz_name()}
