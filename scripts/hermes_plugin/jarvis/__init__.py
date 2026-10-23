"""Jarvis tools for a tenant's Hermes gateway.

Each tool runs run_action.py with a fixed argument list and the turn's own
environment (build_subprocess_env: secrets scrubbed, session and
JARVIS_ACTOR_MSG injected), never through a shell. Installed root-owned by the
provisioner under the tenant's HERMES_HOME/plugins/jarvis.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
SPEC = json.loads((HERE / "tools.json").read_text())
RUNNER = HERE / "run_action.py"
TIMEOUT_S = 150
FAILED = json.dumps({"ok": False, "say": "Something went wrong on my side, so I couldn't do that just now. "
                                          "Nothing was changed."})
# A proposal that came back held is not filed again: the same tool with the
# same arguments within HELD_TTL_S answers with the first hold's own words
# (practice: nothing will run; live: it is in Approvals), logged and counted.
HELD_TTL_S = 3600
ALREADY = "This was already filed, so it was not filed again. "
# Element names (e12) are reused from page to page, so the same arguments can
# be a different action; only the browser task request is the same proposal twice.
NO_DEDUPE = {"browser_open", "browser_click", "browser_type", "browser_press", "browser_login"}
_held: dict = {}
_held_lock = threading.Lock()


def _run(python: str, argv: list, env: dict, stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run([python, str(RUNNER), *argv], input=stdin, env=env, capture_output=True, text=True,
                          timeout=TIMEOUT_S, cwd=str(HERE))


def _handler(name: str):
    def handle(args, **_kw) -> str:
        from tools.environments.local import build_subprocess_env
        env = build_subprocess_env()
        python = shutil.which("python3", path=env.get("PATH")) or "/usr/bin/python3"
        key = name + json.dumps(args or {}, sort_keys=True)
        now = time.time()
        with _held_lock:
            for k in [k for k, (t, _say) in _held.items() if t <= now]:
                del _held[k]
            again = _held.get(key)
        try:
            if again:
                logger.warning("jarvis tool %s: identical held proposal not filed again", name)
                _run(python, ["note", f"agent_tool_{name}_held_duplicate"], env, "")
                return json.dumps({"ok": False, "held": True, "already_waiting": True, "say": ALREADY + again[1]})
            proc = _run(python, [name], env, json.dumps(args or {}))
        except subprocess.TimeoutExpired:
            logger.error("jarvis tool %s timed out after %ss", name, TIMEOUT_S)
            return FAILED
        lines = proc.stdout.strip().splitlines()
        if proc.returncode != 0 or not lines:
            logger.error("jarvis tool %s failed (exit %s): %s", name, proc.returncode, proc.stderr[-400:])
            return FAILED
        try:
            res = json.loads(lines[-1])
            if res.get("held") and name not in NO_DEDUPE:
                with _held_lock:
                    _held[key] = (now + HELD_TTL_S, str(res.get("say") or ""))
        except ValueError:
            pass
        return lines[-1]
    return handle


def register(ctx) -> None:
    for tool in SPEC["tools"]:
        ctx.register_tool(
            name=tool["name"],
            toolset=SPEC["toolset"],
            schema={"name": tool["name"], "description": tool["description"], "parameters": tool["parameters"]},
            handler=_handler(tool["name"]),
            description=tool["label"],
        )
