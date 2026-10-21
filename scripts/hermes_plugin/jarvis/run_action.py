"""One Jarvis tool action: run_action.py NAME, JSON arguments on stdin, one
JSON line on stdout for the model.

Runs as the tenant's agent user in the turn's environment. Every action that
acts for a person goes through skills authz with the app's actor reference
(JARVIS_ACTOR_MSG); Google and outbound go through the app broker, so Sentinel
decides every write. Replies carry a plain `say` for the model and never a
link: the connect button is the app's to draw.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

SKILLS = os.environ.get("JARVIS_SKILLS_ROOT", "")
AUTHZ = {t["name"]: t["authz"] for t in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                     "tools.json")))["tools"]}
sys.path.insert(0, os.path.join(SKILLS, "_shared"))

import app_broker  # noqa: E402
import authz  # noqa: E402

HOLD_SAY = ("Not done: it needs the owner's OK first. Say so in plain words (\"I'll need your OK before I do "
            "that\"). Do not suggest any other way to get it done.")
PRACTICE_SAY = ("Not done, because this assistant is in practice mode. Tell them: \"I'm in practice mode, so "
                "here's what I'd do instead of doing it\", then show exactly what you would have done.")


def out(**kw) -> None:
    print(json.dumps(kw, ensure_ascii=False))


def bump(name: str) -> None:
    try:
        app_broker.call("counters", {"op": "bump", "name": name})
    except Exception as exc:
        print(f"counter {name} not bumped: {exc}", file=sys.stderr)


def actor(tool: str, target: str) -> bool:
    try:
        authz.require(None, AUTHZ[tool], target[:60], actor_msg=os.environ.get("JARVIS_ACTOR_MSG", ""))
        return True
    except SystemExit:
        out(ok=False, refused=True, say="That needs the owner, so I can't do it for you. Nothing was changed.")
        return False


def connect_request() -> dict:
    try:
        return app_broker.call("chat/action", {"ref": os.environ.get("JARVIS_ACTOR_MSG", ""),
                                               "action": "connect_google"})
    except app_broker.BrokerError as exc:
        print(f"connect request refused: {exc}", file=sys.stderr)
        return {"shown": False, "why": "unavailable"}


def needs_google() -> None:
    r = connect_request()
    if r.get("shown"):
        say = ("Google isn't connected yet, so you can't do this. Say that plainly and tell them the app is "
               "showing a 'Connect your Google account' button below your reply. Do not write a link.")
    elif r.get("why") == "not_allowed":
        say = "Google isn't connected yet, and only the owner or an admin can connect it. Say so plainly."
    else:
        say = "Google isn't connected yet. Say so plainly; they can connect it in Settings."
    out(ok=False, needs="google", button_shown=bool(r.get("shown")), say=say)


def google_connected() -> bool:
    try:
        return app_broker.call("google/status").get("source") == "tenant_connection"
    except app_broker.BrokerError:
        return False


def google(api, version, method, params):
    return app_broker.call("google", {"api": api, "version": version, "method": method, "params": params})


def held(exc: "app_broker.BrokerError") -> None:
    err = exc.body.get("error")
    if err == "shadowed":
        out(ok=False, held=True, mode="practice", say=PRACTICE_SAY)
    elif err == "not_connected":
        needs_google()
    elif err in ("approval_required", "sentinel_denied", "writes_disabled", "recipient_refused"):
        out(ok=False, held=True, say=HOLD_SAY)
    elif err == "not_brokered":
        out(ok=False, held=True, say="Not sent: sending this from the app isn't switched on yet. Say so plainly "
                                     "and show what you would have sent. Do not suggest any other way to send it.")
    else:
        print(f"broker: {exc}", file=sys.stderr)
        out(ok=False, say="Something went wrong on my side, so I couldn't do that just now. Nothing was changed.")


def script(rel: str, argv: list) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, os.path.join(SKILLS, rel), *argv], capture_output=True, text=True,
                          timeout=60)


def list_tasks(a):
    if not actor("list_tasks", "open tasks"):
        return
    import app_tasks
    rows = app_tasks.open_tasks()
    if rows is None:
        return out(ok=False, say="I couldn't read the to-do list just now.")
    out(ok=True, tasks=[{"id": r.get("task_id"), "title": r.get("title") or r.get("task_summary"),
                         "due": r.get("due_date"), "status": r.get("status")} for r in rows[:50]])


def add_task(a):
    argv = ["--title", str(a["title"])[:200], "--source", "chat"]
    for k, flag in (("due_date", "--due-date"), ("priority", "--priority"), ("notes", "--notes")):
        if a.get(k):
            argv += [flag, str(a[k])[:2000]]
    p = script("task-manager/scripts/add_task.py", argv)
    if p.returncode == authz.DENIED_EXIT:
        return out(ok=False, refused=True, say="That needs the owner, so I can't add it for you.")
    if p.returncode != 0:
        print(p.stderr[-400:], file=sys.stderr)
        return out(ok=False, say="I couldn't add that to the list just now.")
    out(ok=True, task_id=p.stdout.strip().splitlines()[-1], title=a["title"])


def complete_task(a):
    if not (a.get("task_id") or a.get("match")):
        return out(ok=False, say="Ask which item they mean.")
    argv = ["--task-id", str(a["task_id"])] if a.get("task_id") else ["--match", str(a["match"])[:200]]
    p = script("task-manager/scripts/complete_task.py", argv)
    if p.returncode == authz.DENIED_EXIT:
        return out(ok=False, refused=True, say="That needs the owner, so I can't mark it done for you.")
    if p.returncode != 0:
        print(p.stderr[-400:], file=sys.stderr)
        return out(ok=False, say="I couldn't find that item or mark it done.")
    out(ok=True, say="Marked done.")


def calendar_upcoming(a):
    if not actor("calendar_upcoming", "upcoming"):
        return
    if not google_connected():
        return needs_google()
    now = datetime.now(timezone.utc)
    days = max(1, min(31, int(a.get("days") or 7)))
    try:
        res = google("calendar", "v3", "events.list", {
            "calendarId": "primary", "timeMin": now.isoformat(), "timeMax": (now + timedelta(days=days)).isoformat(),
            "singleEvents": True, "orderBy": "startTime", "maxResults": 25})["result"]
    except app_broker.BrokerError as exc:
        return held(exc)
    out(ok=True, events=[{"title": e.get("summary"), "start": (e.get("start") or {}).get("dateTime")
                          or (e.get("start") or {}).get("date"), "location": e.get("location")}
                         for e in res.get("items") or []])


def add_calendar_event(a):
    if not actor("add_calendar_event", a["title"]):
        return
    if not google_connected():
        return needs_google()
    body = {"summary": str(a["title"])[:200], "description": str(a.get("notes") or "")[:2000],
            "start": {"dateTime": a["start"]}, "end": {"dateTime": a["end"]}}
    try:
        google("calendar", "v3", "events.insert", {"calendarId": "primary", "body": body})
    except app_broker.BrokerError as exc:
        return held(exc)
    out(ok=True, say="Added to the calendar.")


def search_email(a):
    if not actor("search_email", a["query"]):
        return
    if not google_connected():
        return needs_google()
    n = max(1, min(10, int(a.get("max_results") or 5)))
    try:
        ids = google("gmail", "v1", "users.messages.list",
                     {"userId": "me", "q": str(a["query"])[:200], "maxResults": n})["result"].get("messages") or []
        found = []
        for m in ids[:n]:
            msg = google("gmail", "v1", "users.messages.get", {"userId": "me", "id": m["id"], "format": "metadata",
                                                               "metadataHeaders": ["From", "Subject", "Date"]})["result"]
            h = {x["name"]: x["value"] for x in (msg.get("payload") or {}).get("headers") or []}
            found.append({"from": h.get("From"), "subject": h.get("Subject"), "date": h.get("Date"),
                          "preview": msg.get("snippet")})
    except app_broker.BrokerError as exc:
        return held(exc)
    out(ok=True, emails=found)


def draft_email(a):
    if not actor("draft_email", a["to"]):
        return
    try:
        app_broker.outbound("email_send", "email", [a["to"]], f"send an email to {a['to']}",
                            {"to": a["to"], "subject": a["subject"], "body": a["body"]})
    except app_broker.BrokerError as exc:
        return held(exc)
    out(ok=False, held=True, say=HOLD_SAY)


def connect_google(a):
    r = connect_request()
    if r.get("why") == "already_connected":
        return out(ok=True, say="Google is already connected.")
    if r.get("shown"):
        return out(ok=True, say="The app is showing a 'Connect your Google account' button below your reply. "
                                "Tell them to tap it. Do not write a link.")
    if r.get("why") == "not_allowed":
        return out(ok=False, say="Only the owner or an admin can connect Google. Say so plainly.")
    out(ok=False, say="I couldn't show the connect button just now; they can connect Google in Settings.")


ACTIONS = {f.__name__: f for f in (list_tasks, add_task, complete_task, calendar_upcoming, add_calendar_event,
                                   search_email, draft_email, connect_google)}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in ACTIONS:
        out(ok=False, say="I can't do that.")
        sys.exit(2)
    bump(f"agent_tool_{name}")
    ACTIONS[name](json.loads(sys.stdin.read() or "{}"))
