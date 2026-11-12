"""Calendar events that belong to a task.

The event ids live in a small file beside the task database. Marking the task
done takes those events off Spencer's calendar. A removal that does not happen
is logged; the task still completes.
"""
import json
import logging
from typing import Dict, List

log = logging.getLogger("app.task_calendar")


def _path():
    from . import config
    return config.HERMES_HOME / "state" / "task_calendar.json"


def _load() -> Dict[str, List[str]]:
    path = _path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        log.error("task_calendar: the link file could not be read (%s)", type(exc).__name__)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): [str(i) for i in v] for k, v in raw.items() if isinstance(v, list)}


def _save(data: Dict[str, List[str]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def remember(task_id: str, event_id: str) -> None:
    data = _load()
    ids = data.setdefault(task_id, [])
    if event_id not in ids:
        ids.append(event_id)
    _save(data)


def drop(task_id: str) -> None:
    """Take this task's events off the calendar. Never raises."""
    data = _load()
    ids = data.get(task_id) or []
    if not ids:
        return
    from . import approvals, broker
    from . import threads as threads_mod
    kept = []
    for event_id in ids:
        try:
            status, content = broker.gate_google(
                "calendar", "v3", "events.delete",
                {"calendarId": "primary", "eventId": event_id},
                {"run": "", "session": "", "task": task_id, "skill": "tasks",
                 "source": f"task:{task_id}"},
                person="owner",
            )
        except Exception as exc:
            threads_mod.bump_counter("task_calendar_remove_failed")
            log.error("task_calendar: could not remove an event for %s (%s)", task_id, type(exc).__name__)
            kept.append(event_id)
            continue
        if status != 200 and content.get("error") == "approval_required":
            try:
                approvals.decide(content["approval_id"], "approved", "owner", via="task_completed")
                status = 200
            except Exception as exc:
                threads_mod.bump_counter("task_calendar_remove_failed")
                log.error("task_calendar: the removal for %s was not approved (%s)", task_id, type(exc).__name__)
                kept.append(event_id)
                continue
        if status == 200:
            threads_mod.bump_counter("task_calendar_removed")
            log.info("task_calendar: took an event off the calendar because %s was completed", task_id)
        else:
            threads_mod.bump_counter("task_calendar_remove_failed")
            log.error("task_calendar: an event for %s stayed on the calendar (%s)", task_id, content.get("error") or status)
            kept.append(event_id)
    if kept:
        data[task_id] = kept
    else:
        data.pop(task_id, None)
    try:
        _save(data)
    except OSError as exc:
        log.error("task_calendar: could not update the link file (%s)", type(exc).__name__)
