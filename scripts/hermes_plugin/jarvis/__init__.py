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
from pathlib import Path

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
SPEC = json.loads((HERE / "tools.json").read_text())
RUNNER = HERE / "run_action.py"
TIMEOUT_S = 90
FAILED = json.dumps({"ok": False, "say": "Something went wrong on my side, so I couldn't do that just now. "
                                          "Nothing was changed."})


def _handler(name: str):
    def handle(args, **_kw) -> str:
        from tools.environments.local import build_subprocess_env
        env = build_subprocess_env()
        python = shutil.which("python3", path=env.get("PATH")) or "/usr/bin/python3"
        try:
            proc = subprocess.run([python, str(RUNNER), name], input=json.dumps(args or {}), env=env,
                                  capture_output=True, text=True, timeout=TIMEOUT_S, cwd=str(HERE))
        except subprocess.TimeoutExpired:
            logger.error("jarvis tool %s timed out after %ss", name, TIMEOUT_S)
            return FAILED
        lines = proc.stdout.strip().splitlines()
        if proc.returncode != 0 or not lines:
            logger.error("jarvis tool %s failed (exit %s): %s", name, proc.returncode, proc.stderr[-400:])
            return FAILED
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
