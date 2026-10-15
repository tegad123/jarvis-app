"""Chat proxy — the browser's only route to the Hermes gateway.

WHY A PROXY AT ALL
    API_SERVER_KEY must never reach the browser (spec: "BFF pattern is
    non-negotiable"). This module is the sole holder of that key on the request
    path; the SPA authenticates with its own session cookie and never sees a
    gateway credential.

HISTORY OWNERSHIP — read this before changing anything
    The gateway forks on the X-Hermes-Session-Id header
    (gateway/platforms/api_server.py:3959):

      * header present -> the gateway loads history from state.db and IGNORES
        the `messages` array in the body except for its last user message;
      * header absent  -> `messages[:-1]` IS the history and the last message
        is the prompt.

    We take the second branch: we send NO session header and own the transcript
    ourselves. Sending both would feed the model two copies of the conversation
    and bill for both.

    UPDATED 2026-08-17 (Phase A) — the premise changed, the conclusion did not.
    This used to say "v1 is in-memory, no persistence, so we take the second
    branch". History is now DURABLE, in app/threads.py (SQLite), keyed by
    thread_id. That makes not sending the header MORE important, not less: we
    now hold a persistent transcript, and the gateway would hold its own
    separate one in state.db keyed by its session id. Two durable transcripts
    of the same conversation would drift apart silently — the gateway rotates
    its session on context compression, ours never rotates — and we would pay
    for both on every turn. Still: pick exactly one owner. If the gateway is
    ever made the owner, delete the message tables here in the same commit.

    Also note the key changed. History used to be keyed by
    (cookie sid, thread_id), so logging out lost the conversation. It is now
    keyed by thread_id alone: threads belong to the TENANT, not to a browser
    session. With v1's single shared passphrase that is the intended reading —
    revisit when per-user accounts land.

WIRE FORMAT
    Upstream is SSE. Downstream is NDJSON (one JSON object per line), because
    the browser has to POST — EventSource cannot — and line-delimited JSON has
    none of SSE's comment/multi-line-data parsing traps on the client side.
"""

import asyncio
import base64
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from . import attachments, config, threads

log = logging.getLogger("jarvis.chat")

# Re-exported so callers keep one import site for the thread vocabulary.
DIRECT_THREAD = threads.DIRECT_THREAD

# Bounds what we resend upstream each turn, and therefore the bill. The stored
# history is NOT capped — archiving is the only removal mechanism and the
# record is the point (see app/threads.py).
UPSTREAM_TURNS = threads.UPSTREAM_TURNS

GATEWAY_CHAT_URL = f"{config.GATEWAY_URL}/v1/chat/completions"

# The gateway sets no response timeout: a turn that runs tools can take
# minutes, and it emits an SSE keepalive comment only every 30s. A read timeout
# here would sever a perfectly healthy long turn, so read is unbounded and the
# other phases stay short.
TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)

# History lives in app/threads.py now. These thin wrappers keep the call sites
# in main.py unchanged and give one place to swap the store again if needed.


def get_history(thread_id: str) -> List[Dict]:
    """The stored transcript for a thread, oldest first."""
    return threads.get_history(thread_id)


def append_messages(thread_id: str, *messages: Dict) -> None:
    threads.append_messages(thread_id, *messages)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------- did the turn do what it said
#
# WHY THIS EXISTS, AND WHY IT LIVES HERE
#     The model can say "I've added that to your list" without having called
#     anything. It has done exactly that on this box: three runs of the same
#     phrase produced two confident confirmations and zero rows, and the model
#     later admitted, unprompted, that the confirmations were fabricated.
#
#     THE FIX IS NOT A BETTER PROMPT. A prompt makes fabrication less likely; it
#     cannot make it observable. This seam is the last place a turn passes
#     through before the person reads it, and it is the first place that can
#     compare what was SAID against what was WRITTEN. So the confirmation the
#     person sees is generated HERE, from the row, after re-reading it — the
#     model's prose never carries a confirmation on its own — and a claim with
#     no row behind it is corrected rather than passed along.
#
#     Applies only to app-native tenants: on a legacy tenant chat tasks land in
#     the skill's own list, and a guard that cannot see the store would either
#     stay silent or accuse every turn.

# What the PERSON asked for. The denominator of the hit-rate: how often someone
# asked for a task, whatever came of it.
_TASK_INTENT = re.compile(
    r"\b(track this|track that|add (?:a )?task|new task|make (?:a )?task"
    r"|remind me to|task for \w+|put (?:this|that|it) on (?:my|the) list"
    r"|add (?:this|that|it) to (?:my|the) (?:task )?list)\b",
    re.I,
)

# What the REPLY asserted. Tight on purpose: only past/perfect assertions that a
# task now exists. "I can add that if you like" is an offer, not a claim, and
# correcting an offer would train the person to ignore the corrections.
_TASK_CLAIM = re.compile(
    r"\bi(?:'ve| have)?\s+(?:just\s+)?(?:added|tracked|created|logged)\b"
    r"|\b(?:task|it|that|this)\s+(?:has been|have been|is|was|is now)\s+"
    r"(?:added|tracked|created|logged)\b"
    r"|\btask created\b"
    r"|\badded (?:it |that |this )?to your (?:task )?list\b"
    r"|\bconsider it (?:tracked|added|done)\b"
    r"|\bnow (?:tracked|on your list)\b"
    r"|\bthat'?s (?:tracked|added|on your list)\b",
    re.I,
)


def _bump(name: str, by: int = 1) -> None:
    try:
        threads.bump_counter(name, by)
    except Exception:
        pass


def _tasks_verifiable() -> bool:
    """True only when this tenant's tasks live somewhere this process can read."""
    try:
        from . import tasks_source
        return tasks_source.source() == "store"
    except Exception:
        return False


def _tasks_written_since(ts: str) -> Optional[List[Dict[str, Any]]]:
    """None when the store cannot be read: that is "could not check", which
    must never be reported to the person as "no task was created"."""
    try:
        from . import tasks_store
        rows = tasks_store.created_since(ts)
        # Both of the ways a chat turn can file work: the gateway's task skill
        # (source chat) and the meeting seam below (source meeting).
        return [r for r in rows if r["source"] in ("chat", "meeting")]
    except Exception as exc:
        log.warning("chat verify: tasks store unreadable (%s: %s)", type(exc).__name__, exc)
        _bump("chat_verify_store_unreadable")
        return None


def _confirmation(rows: List[Dict[str, Any]]) -> str:
    """The confirmation, built from the stored rows and nothing else.

    Carries the id ON PURPOSE, which is the opposite of the feed's rule. A feed
    headline is a sentence about the tenant's day and an id is noise in it; this
    is a receipt, and a receipt without a reference is the thing you cannot
    check later.
    """
    if len(rows) == 1:
        r = rows[0]
        return f"\u2713 Tracked: {r['title']} \u2014 {r['task_id']}"
    lines = [f"\u2713 Tracked {len(rows)} tasks:"]
    lines += [f"  \u2022 {r['title']} \u2014 {r['task_id']}" for r in rows]
    return "\n".join(lines)


UNCHECKED = (
    "\u26a0 Jarvis could not read your task list just now, so this reply is "
    "unconfirmed \u2014 check the Tasks tab."
)

CORRECTION = (
    "\u26a0 No task was created. Nothing was written to your list, despite what "
    "the reply above says \u2014 ask again, or add it on the Tasks tab."
)


async def _verify_turn(thread_id: str, message: str, reply: str,
                       started_at: str) -> Optional[Dict[str, Any]]:
    """Compare what the turn SAID against what it WROTE. Never raises.

    Returns the event to surface, or None when there is nothing to say.
    """
    if not _tasks_verifiable():
        return None

    asked = bool(_TASK_INTENT.search(message or ""))
    claimed = bool(_TASK_CLAIM.search(reply or ""))
    rows = _tasks_written_since(started_at)

    # LOOK TWICE BEFORE ACCUSING. The skill writes during the turn, but the
    # write and the last token are not ordered with respect to each other, and
    # calling a real creation a fabrication would be its own kind of lie.
    if not rows and rows is not None and (claimed or asked):
        for _ in range(8):
            await asyncio.sleep(0.25)
            rows = _tasks_written_since(started_at)
            if rows or rows is None:
                break
    if rows is None:
        return ({"type": "verified", "ok": None, "message": UNCHECKED}
                if (claimed or asked) else None)

    if asked:
        _bump("chat_task_attempts")
        _bump("chat_task_created" if rows else "chat_task_missed")
    elif rows:
        # A task the person did not explicitly ask for in those words still
        # counts as created; it just is not part of the hit-rate denominator.
        _bump("chat_task_created")

    if rows:
        return {"type": "verified", "ok": True, "message": _confirmation(rows),
                "task_ids": [r["task_id"] for r in rows]}
    if claimed:
        _bump("intent_claim_unbacked")
        return {"type": "verified", "ok": False, "message": CORRECTION}
    return None


# ------------------------------------------------------ the meeting seam
#
# WHY THE APP, AND NOT A GATEWAY SKILL. A transcript attached in Ask never
# reaches the gateway — documents are stored, not sent, and the proxy passes
# no thread or attachment reference upstream. The bytes exist only here. And
# a pasted transcript, sent upstream, would be read by the gateway's own task
# skill, which files source=chat rows of its own; the honesty guard would then
# confirm them, and the meeting would be on the list twice. So a turn that
# asks for a meeting to be processed is handled HERE, deterministically, and
# is NOT proxied: the receipt is built from the rows the handler wrote.
#
# THE MODEL STILL NEVER SEES THE DOCUMENT. The handler runs its own extraction
# call over the transcript text; the "Not sent to Jarvis" notice stays true.

_MEETING_INTENT = re.compile(
    r"\b(?:process|file|log|ingest|digest)\s+(?:this|that|the|these|my|our|attached)\s+"
    r"(?:meeting(?:\s+(?:notes|transcript|recording))?|transcript|call\s+(?:notes|transcript)"
    r"|notes\s+from\s+(?:the|a|our)\s+(?:meeting|call))\b"
    r"|\bextract\s+(?:the\s+)?(?:tasks|action\s+items)\s+from\s+(?:this|the|that)\s+"
    r"(?:meeting|transcript|call)\b",
    re.I,
)
MEETING_TIMEOUT = 300
TRANSCRIPT_MIMES = {"text/plain", "text/vtt"}


def _meeting_intake_enabled() -> bool:
    try:
        import sys as _s
        if str(config.SHARED) not in _s.path:
            _s.path.insert(0, str(config.SHARED))
        import tenant_config as tc
        return bool(tc.enabled("meeting-intake"))
    except Exception:
        return False


def _transcript_mod():
    try:
        import importlib.util
        path = config.SKILLS_ROOT / "meeting-intake" / "scripts" / "transcript.py"
        spec = importlib.util.spec_from_file_location("meeting_transcript", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _meeting_turn_input(message: str, attachment_ids: List[str]) -> Optional[Dict[str, Any]]:
    """The handler's payload, or None when this turn carries no transcript."""
    atts = []
    for rec in attachments.get_many(attachment_ids or []):
        if rec["mime"] in TRANSCRIPT_MIMES:
            try:
                data = Path(rec["path"]).read_bytes()
            except OSError:
                continue
            atts.append({"filename": rec["filename"], "mime": rec["mime"],
                         "data_b64": base64.b64encode(data).decode("ascii")})
    tm = _transcript_mod()
    if not atts and not (tm and tm.looks_like_transcript(message or "")):
        return None
    return {"from": "", "subject": "", "body": message or "", "to": "", "cc": "",
            "attachments": atts, "origin": "ask"}


async def _run_meeting_intake(payload: Dict[str, Any], thread_id: str,
                              person_key: Optional[str]) -> Dict[str, Any]:
    handler = config.SKILLS_ROOT / "meeting-intake" / "scripts" / "handle_meeting.py"
    payload = dict(payload, message_id=f"thread:{thread_id}")
    argv = [sys.executable, str(handler), "--origin", "ask", "--run-id", f"ask-{thread_id}"]
    if person_key:
        argv += ["--by", person_key]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=dict(os.environ))
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(payload).encode()),
                                          timeout=MEETING_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"rc": -1, "result": {"status": "timeout"}}
    except Exception as exc:
        return {"rc": -2, "result": {"status": "error", "reason": f"{type(exc).__name__}"}}
    lines = (out or b"").decode("utf-8", "replace").strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else {}
    except Exception:
        result = {"status": "unparseable"}
    return {"rc": proc.returncode, "result": result}


def _meeting_receipt(rc: int, res: Dict[str, Any]) -> Tuple[bool, str]:
    """The receipt, from the handler's own record of what it wrote."""
    status = res.get("status")
    title = res.get("title") or "the meeting"
    if rc == 0 and status == "done":
        n, m = int(res.get("tasks_created") or 0), int(res.get("tasks_assigned") or 0)
        if n == 0:
            return True, f"Filed nothing from {title} — no action items were spoken."
        lines = [f"\u2713 Filed {n} follow-up{'s' if n != 1 else ''} from {title} \u2014 {m} assigned"]
        try:
            from . import tasks_store
            for tid in res.get("task_ids") or []:
                row = tasks_store.get(tid)
                if row:
                    who = row.get("assignee_name") or "unassigned"
                    lines.append(f"  \u2022 {row['title']} \u2014 {who} \u2014 {tid}")
        except Exception:
            pass
        if res.get("unresolved"):
            lines.append("\u26a0 Couldn't match to anyone on the team: "
                         + ", ".join(res["unresolved"]) + " \u2014 those tasks are unassigned")
        return True, "\n".join(lines)
    if rc == 0 and status == "duplicate":
        return True, f"Already filed the follow-ups from {title} \u2014 nothing new was created."
    if rc == 2:
        return False, (f"\u26a0 Couldn't process that meeting: {res.get('reason') or 'unreadable'}. "
                       "Nothing was filed.")
    if status == "timeout":
        return False, ("\u26a0 Processing the meeting took too long \u2014 the transcript was "
                       "kept; try again in a moment.")
    return False, "\u26a0 The meeting could not be processed. Nothing was filed."


# ------------------------------------------------------------- sse decoding


def _iter_sse_frames(buffer: str) -> Tuple[List[Tuple[Optional[str], str]], str]:
    """
    Split a text buffer into complete SSE frames.

    Returns (frames, remainder) where each frame is (event_name, data). Frames
    are blank-line delimited; `data:` lines accumulate; lines starting with ':'
    are comments (the gateway sends ': keepalive' every 30s of silence) and are
    dropped. The remainder is whatever trailing partial frame is left.
    """
    frames: List[Tuple[Optional[str], str]] = []
    while True:
        idx = buffer.find("\n\n")
        if idx == -1:
            return frames, buffer
        raw, buffer = buffer[:idx], buffer[idx + 2 :]
        event: Optional[str] = None
        data_lines: List[str] = []
        for line in raw.split("\n"):
            line = line.rstrip("\r")
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            frames.append((event, "\n".join(data_lines)))


def _ndjson(obj: Dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


# ----------------------------------------------------------------- streaming


async def stream_chat(
    thread_id: str, message: str, attachment_ids: Optional[List[str]] = None,
    person_key: Optional[str] = None,
) -> AsyncIterator[bytes]:
    """
    Proxy one turn. Yields NDJSON lines:

        {"type":"delta","text":"..."}
        {"type":"tool","status":"running"|"completed","tool":..,"label":..,"toolCallId":..}
        {"type":"done","finish_reason":"stop","usage":{...}}
        {"type":"verified","ok":true|false,"message":"...","task_ids":[...]}
        {"type":"error","message":"..."}

    Always yields at least one line — an empty stream would render as a silent
    failure, which is the thing the feed's error state exists to prevent.
    """
    history = get_history(thread_id)[-UPSTREAM_TURNS:]

    # Attachments: IMAGES are inlined as base64 data URLs so the model can
    # actually see them; DOCUMENTS are deliberately not sent (the gateway
    # rejects document parts, and inlining a PDF would fail the whole turn).
    # `skipped` explains anything we held back so the UI can say so.
    attachment_ids = list(attachment_ids or [])
    image_parts, skipped = attachments.inline_image_parts(attachment_ids)

    if image_parts:
        # Native OpenAI vision shape; the gateway's _normalize_multimodal_content
        # takes this verbatim and the Anthropic adapter turns it into real image
        # blocks. Text first, then images, mirroring how a person would send it.
        user_content: Any = ([{"type": "text", "text": message}] if message else []) + image_parts
    else:
        user_content = message

    # Only the CURRENT turn carries images. Replaying every past image on every
    # turn would multiply the bill and quickly exceed the 10 MB body cap; the
    # model has already described them into the transcript.
    payload = {
        # No system prompt: Hermes owns the assistant's identity and prompt
        # stack. "Jarvis" is a display name in the UI, not a persona we inject.
        "messages": [
            {"role": m["role"], "content": m["content"]} for m in history
        ]
        + [{"role": "user", "content": user_content}],
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {config.require('API_SERVER_KEY')}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "jarvis-app/1.0",
        # Deliberately NO X-Hermes-Session-Id — see the module docstring.
    }

    assistant_text: List[str] = []
    finished_cleanly = False
    # Stamped BEFORE the upstream call, so anything the turn writes falls
    # inside the window even if the model calls the tool on its first token.
    turn_started = _now()

    if skipped:
        # Honest, never a silent drop.
        yield _ndjson(
            {
                "type": "notice",
                "message": "Not sent to Jarvis: " + ", ".join(skipped),
            }
        )

    # A meeting turn is handled here and never proxied — see the seam above.
    if _tasks_verifiable() and _meeting_intake_enabled() and _MEETING_INTENT.search(message or ""):
        mi = _meeting_turn_input(message, attachment_ids)
        if mi is not None:
            yield _ndjson({"type": "tool", "status": "running", "tool": "meeting-intake",
                           "toolCallId": "meeting-1", "label": "Filing follow-ups from the meeting"})
            out = await _run_meeting_intake(mi, thread_id, person_key)
            yield _ndjson({"type": "tool", "status": "completed", "tool": "meeting-intake",
                           "toolCallId": "meeting-1"})
            ok, text = _meeting_receipt(out["rc"], out["result"])
            rows = _tasks_written_since(turn_started)
            yield _ndjson({"type": "delta", "text": text})
            yield _ndjson({"type": "done", "finish_reason": "stop", "usage": {}})
            yield _ndjson({"type": "verified", "ok": ok, "message": text,
                           "task_ids": [r["task_id"] for r in rows]})
            append_messages(
                thread_id,
                {"role": "user", "content": message, "ts": _now(), "attachments": attachment_ids},
                {"role": "assistant", "content": text, "ts": _now()},
            )
            _bump("meeting_ask_turns")
            return

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            async with client.stream(
                "POST", GATEWAY_CHAT_URL, json=payload, headers=headers
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    yield _ndjson(
                        {"type": "error", "message": _upstream_error(resp.status_code, body)}
                    )
                    return

                buffer = ""
                async for chunk in resp.aiter_text():
                    buffer += chunk
                    frames, buffer = _iter_sse_frames(buffer)
                    for event, data in frames:
                        if data == "[DONE]":
                            continue
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        # Tool lifecycle arrives as a NAMED event whose payload
                        # is not a chat.completion.chunk — branch on the name,
                        # never on the shape.
                        if event == "hermes.tool.progress":
                            out = {
                                "type": "tool",
                                "status": obj.get("status"),
                                "tool": obj.get("tool"),
                                "toolCallId": obj.get("toolCallId"),
                            }
                            # 'completed' payloads carry no label; the client
                            # caches the one from 'running'.
                            if obj.get("label"):
                                out["label"] = obj["label"]
                            yield _ndjson(out)
                            continue

                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}

                        text = delta.get("content")
                        if text:
                            assistant_text.append(text)
                            yield _ndjson({"type": "delta", "text": text})

                        finish = choice.get("finish_reason")
                        if finish:
                            if finish == "stop":
                                finished_cleanly = True
                                yield _ndjson(
                                    {
                                        "type": "done",
                                        "finish_reason": finish,
                                        "usage": obj.get("usage") or {},
                                    }
                                )
                            else:
                                err = (obj.get("error") or {}).get("message") or ""
                                yield _ndjson(
                                    {
                                        "type": "error",
                                        "message": _finish_error(finish, err),
                                        "partial": bool(assistant_text),
                                    }
                                )
    except httpx.RequestError as exc:
        yield _ndjson(
            {
                "type": "error",
                "message": "Could not reach Jarvis. The assistant service may be down.",
                "detail": exc.__class__.__name__,
            }
        )
        return
    except asyncio.CancelledError:
        # The browser went away. The gateway hard-interrupts its agent run when
        # our connection drops, which is what we want — an abandoned question
        # should stop the work rather than keep billing.
        raise

    # Persist the turn only if something was actually said. A failed turn with
    # partial text is still worth keeping so the next question has context.
    reply = "".join(assistant_text).strip()
    if reply:
        append_messages(
            thread_id,
            {
                "role": "user",
                "content": message,
                "ts": _now(),
                "attachments": attachment_ids,
            },
            {"role": "assistant", "content": reply, "ts": _now(), "partial": not finished_cleanly},
        )

    # THE LAST THING THE TURN DOES: check itself. Streamed so the person sees
    # it on this turn, and PERSISTED so it is still there tomorrow — a receipt
    # that only exists until you refresh is not a receipt.
    try:
        verdict = await _verify_turn(thread_id, message, reply, turn_started)
    except Exception:
        verdict = None
    if verdict:
        yield _ndjson(verdict)
        if reply:
            append_messages(
                thread_id,
                {"role": "assistant", "content": verdict["message"], "ts": _now()},
            )


def _upstream_error(status: int, body: str) -> str:
    if status == 429:
        return "Jarvis is busy with another task. Try again in a moment."
    if status in (401, 403):
        # Our own key is wrong/missing — an operator problem, not a user one.
        return "Jarvis could not authenticate with the assistant service."
    if status == 503:
        return "Jarvis is restarting. Try again shortly."
    detail = ""
    try:
        parsed = json.loads(body)
        detail = (parsed.get("error") or {}).get("message") or ""
    except Exception:
        pass
    return f"The assistant service returned an error ({status})." + (f" {detail}" if detail else "")


def _finish_error(finish_reason: str, message: str) -> str:
    if finish_reason == "length":
        return "That answer was cut short — the reply hit its length limit."
    base = "Jarvis stopped before finishing that answer."
    return f"{base} {message}".strip() if message else base
