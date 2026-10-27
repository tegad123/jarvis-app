"""Team channels: people talk to each other, and Jarvis answers when called.

WHEN JARVIS ANSWERS IN A CHANNEL
    A workspace choice an admin makes in Settings (human decision 2026-10-27):
      mention  only a message that says @jarvis (the default);
      every    every message, the way channels worked before people could
               talk to each other in them.
    The direct line is not a channel and always answers.

WHO JARVIS IS, WHEN CALLED
    The person who called it: their role, their Google, their switches. It is
    the same turn as the direct line with one difference, the context: the
    channel's whole conversation, each line labelled with who said it, so it
    can refer to anything said there.

MENTIONS ARE PLAIN TEXT. "@Dana" is what was typed and what is stored. Who it
    names is worked out against the people in the channel when it is read, so
    there is nothing extra to keep in step with the message.
"""

import logging
import re
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import config
from . import threads as threads_mod

log = logging.getLogger("app.channels")

MODES = ("mention", "every")
MODE_KEY = "jarvis_replies"
DEFAULT_MODE = "mention"

# What @ calls the assistant. The product is Jarvis everywhere on screen;
# "assistant" and the tenant's own agent name (Nova, Tega's Assistant) also work.
JARVIS_ALIASES = ("jarvis", "assistant")

# The whole channel goes up with each call (human decision: "Jarvis can read
# the entire chat"). This bounds one request, not what is stored; a channel
# longer than this loses its OLDEST lines first, and the model is told so.
CONTEXT_CHARS = 400_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bump(name: str) -> None:
    try:
        threads_mod.bump_counter(name)
    except Exception:
        pass


def _tc():
    if str(config.SHARED) not in sys.path:
        sys.path.insert(0, str(config.SHARED))
    import tenant_config as tc  # noqa: PLC0415
    return tc


# ------------------------------------------------------------------ the setting


def mode() -> str:
    """The admin's choice once made; before that the tenant config's
    app.channels.jarvis_replies, else mention. Same order as the sidebar
    categories: config seeds it, the admins own it after."""
    conn = threads_mod._ensure()
    row = conn.execute("SELECT value FROM channel_settings WHERE key = ?", (MODE_KEY,)).fetchone()
    if row and row["value"] in MODES:
        return row["value"]
    if row:
        log.error("channels: stored %s=%r is not a mode; using the default", MODE_KEY, row["value"])
        _bump("channel_mode_invalid")
    try:
        seeded = _tc().get("app.channels.jarvis_replies", DEFAULT_MODE, announce=False)
    except Exception as exc:
        log.error("channels: tenant config unreadable (%s); using %s", type(exc).__name__, DEFAULT_MODE)
        _bump("channel_mode_config_unreadable")
        return DEFAULT_MODE
    return seeded if seeded in MODES else DEFAULT_MODE


def set_mode(value: str, by: Optional[str]) -> Dict[str, Any]:
    if value not in MODES:
        raise ValueError("Choose mention or every.")
    conn = threads_mod._ensure()
    with conn:
        conn.execute(
            """INSERT INTO channel_settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                 updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
            (MODE_KEY, value, by, _now()))
    _bump(f"channel_mode_set_{value}")
    return settings()


def settings() -> Dict[str, Any]:
    conn = threads_mod._ensure()
    row = conn.execute("SELECT updated_by, updated_at FROM channel_settings WHERE key = ?",
                       (MODE_KEY,)).fetchone()
    return {"jarvis_replies": mode(), "updated_by": row["updated_by"] if row else None,
            "updated_at": row["updated_at"] if row else None}


# ------------------------------------------------------------------ names


def agent_name() -> Optional[str]:
    """The tenant's own name for its assistant (identity role 'assistant')."""
    try:
        if str(config.SHARED) not in sys.path:
            sys.path.insert(0, str(config.SHARED))
        import identity  # noqa: PLC0415
        for rec in (identity.load().get("people") or {}).values():
            if "assistant" in (rec.get("roles") or []):
                name = rec.get("display_name") or rec.get("name")
                return name.strip() if isinstance(name, str) and name.strip() else None
    except Exception:
        return None
    return None


def directory() -> Dict[str, Dict[str, Any]]:
    """Everyone on the team, by person key: the names an @ can use."""
    from . import accounts  # noqa: PLC0415
    out = {}
    for r in accounts.roster():
        names = {r["person_key"]}
        for n in (r.get("name"), r.get("full_name")):
            if isinstance(n, str) and n.strip():
                names.add(n.strip())
                names.add(n.strip().split()[0])
        out[r["person_key"]] = {"person_key": r["person_key"], "name": r["name"],
                                "title": r.get("title") or "", "role": r["role"],
                                "disabled": r["disabled"], "aliases": names}
    return out


def name_of(person_key: Optional[str], people: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[str]:
    if not person_key:
        return None
    rec = (people if people is not None else directory()).get(person_key)
    return rec["name"] if rec else person_key


# ------------------------------------------------------------------ mentions


def _alias_re(alias: str) -> "re.Pattern":
    # @ not glued to a word before it (an email address is not a mention), and
    # the name not running on into more letters (@jarvisbot is not @jarvis).
    # A possessive is still the person: "@Omar's file" names Omar.
    return re.compile(r"(?<![\w@.])@" + re.escape(alias) + r"(?![\w-])", re.I)


def _jarvis_aliases() -> List[str]:
    own = agent_name()
    return sorted({*JARVIS_ALIASES, *([own] if own else [])}, key=len, reverse=True)


def calls_jarvis(text: str) -> bool:
    return any(_alias_re(a).search(text or "") for a in _jarvis_aliases())


def mentioned(text: str, people: Dict[str, Dict[str, Any]],
              among: Optional[Iterable[str]] = None) -> Set[str]:
    """Person keys an @ in this text names. Longest name first, so "@Dana Ruiz"
    is Dana Ruiz even when a "Dana" is also on the team."""
    text = text or ""
    if "@" not in text:
        return set()
    keys = set(among) if among is not None else set(people)
    pairs = sorted(((a, k) for k in keys if k in people for a in people[k]["aliases"]),
                   key=lambda p: -len(p[0]))
    found: Set[str] = set()
    # The assistant's own name goes first: "@Tega's Assistant" calls Jarvis,
    # it does not also ping Tega.
    taken: List[Tuple[int, int]] = [m.span() for a in _jarvis_aliases() for m in _alias_re(a).finditer(text)]
    for alias, key in pairs:
        for m in _alias_re(alias).finditer(text):
            span = m.span()
            if any(s < span[1] and span[0] < e for s, e in taken):
                continue
            taken.append(span)
            found.add(key)
    return found


# ------------------------------------------------------------------ live turns

# Which channels have a Jarvis answer in progress, and for whom, so the people
# watching a channel see "Jarvis is answering Dana" instead of silence. The app
# is one process (app/main.py), so memory is the whole truth here.
_busy: Dict[str, List[str]] = {}
_busy_lock = threading.Lock()


def turn_started(thread_id: str, person_key: Optional[str]) -> None:
    with _busy_lock:
        _busy.setdefault(thread_id, []).append(person_key or "")


def turn_ended(thread_id: str, person_key: Optional[str]) -> None:
    with _busy_lock:
        who = _busy.get(thread_id) or []
        if (person_key or "") in who:
            who.remove(person_key or "")
        if not who:
            _busy.pop(thread_id, None)


def answering(thread_id: str) -> List[str]:
    with _busy_lock:
        return list(_busy.get(thread_id) or [])


# ------------------------------------------------------------------ the context


def _line(m: Dict[str, Any], people: Dict[str, Dict[str, Any]]) -> str:
    who = name_of(m.get("author"), people) if m.get("author") else "Someone (an earlier message, sender not recorded)"
    text = m.get("content") or ""
    files = m.get("attachment_names") or []
    if files:
        text = (text + " " if text else "") + "[attached: " + ", ".join(files) + "]"
    return f"{who}: {text}"


def upstream(history: List[Dict[str, Any]], people: Dict[str, Dict[str, Any]],
             budget: int = CONTEXT_CHARS) -> Tuple[List[Dict[str, str]], int]:
    """The channel as the model reads it: each run of people's messages becomes
    one user turn of "Name: text" lines, Jarvis's own replies stay assistant
    turns. Ends on the people's turn that holds the message being answered.

    Returns (messages, how many of the oldest stored messages did not fit).
    Over budget the OLDEST go first; the caller announces it, and the model is
    told in the first turn it does get."""
    groups: List[Dict[str, Any]] = []
    for m in history:
        if m["role"] == "user":
            if groups and groups[-1]["role"] == "user":
                groups[-1]["lines"].append(_line(m, people))
                groups[-1]["n"] += 1
            else:
                groups.append({"role": "user", "lines": [_line(m, people)], "n": 1})
        else:
            groups.append({"role": "assistant", "lines": [m.get("content") or ""], "n": 1})

    kept: List[Dict[str, Any]] = []
    used, dropped = 0, 0
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        size = sum(len(x) + 1 for x in g["lines"])
        if used + size <= budget:
            kept.insert(0, g)
            used += size
            continue
        if not kept:
            # The newest turn alone is over budget: keep its newest lines.
            lines, room = [], budget
            for x in reversed(g["lines"]):
                if len(x) + 1 > room:
                    break
                lines.insert(0, x)
                room -= len(x) + 1
            lines = lines or [g["lines"][-1][-budget:]]
            kept.insert(0, {**g, "lines": lines})
            dropped = g["n"] - len(lines) + sum(x["n"] for x in groups[:i])
        else:
            # Everything older goes too: the transcript never has a hole in it.
            dropped = sum(x["n"] for x in groups[: i + 1])
        break
    out = [{"role": g["role"], "content": "\n".join(g["lines"])} for g in kept]
    note = (f"({dropped} earlier message{'s are' if dropped != 1 else ' is'} in this channel, too long ago "
            "to include here.)") if dropped else ""
    if out and out[0]["role"] == "assistant":
        # The model reads a conversation that starts with a person.
        out.insert(0, {"role": "user", "content": note or "(The channel starts with this reply of yours.)"})
    elif note and out:
        out[0]["content"] = note + "\n" + out[0]["content"]
    return out, dropped


def status_lines(thread: Dict[str, Any], caller: Optional[str], members: List[str],
                 people: Dict[str, Dict[str, Any]], how: str) -> str:
    """Appended to the app's status block (same paragraph: the model proxy keeps
    only that block, the persona and the date). `how` is "mention" when the
    person typed @jarvis, "every" when the channel answers every message."""
    who = name_of(caller, people) or "someone"
    names = []
    for k in members:
        rec = people.get(k)
        names.append(f"{rec['name']} ({rec['role']})" if rec else k)
    called = (f"{who} called you with @ just now" if how == "mention"
              else f"this channel has you answer every message; the last one is from {who}")
    return "\n".join([
        f"- Where: the team channel \"{thread['name']}\", not a private chat. People in it: "
        + (", ".join(names) if names else "nobody listed") + ".",
        "- The conversation below is the whole channel, oldest first. Several people talk here; each of their "
        "lines starts with the name of who wrote it. Use any of it when it helps.",
        f"- {called}. Answer {who}. Everyone in the channel reads your reply. Your tools act as {who}, with "
        f"{who}'s own access; their email and calendar are theirs, so share only what {who} asked for.",
    ])
