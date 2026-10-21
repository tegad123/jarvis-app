#!/usr/bin/env python3
"""The tenant agent gets the Jarvis tools and nothing else.

Two halves:

1. Resolver (offline, real Hermes code): for each config given, a scratch
   HERMES_HOME holds that config.yaml and the Jarvis plugin, and a Hermes
   subprocess runs what the API server runs to build an agent:
   _get_platform_tools(_load_gateway_config(), "api_server") then
   model_tools.get_tool_definitions(). Prints the tool list.
     --before CFG   expected to include developer tools (presence twin: the
                    resolver does list them when a config allows them)
     --after CFG    must be exactly the plugin's tools
2. Ledger (--slug): the tenant app's model proxy records the tools on the
   latest real model request (agent_tools_seen.json). After a chat turn it
   must show the Jarvis tools offered, none stripped (Hermes itself already
   offers nothing else) and no developer tool sent.

    /usr/local/lib/hermes-agent/venv/bin/python docs/model/verify_agent_tools.py \\
        --before /opt/tega/hermes/config.yaml --after <rendered> [--slug tega]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "scripts" / "hermes_plugin" / "jarvis"
HERMES = Path("/usr/local/lib/hermes-agent")
ALLOWED = sorted(t["name"] for t in json.loads((PLUGIN / "tools.json").read_text())["tools"])
DEV = ("terminal", "process", "read_file", "write_file", "patch", "search_files", "execute_code",
       "delegate_task", "web_search", "web_extract", "skill_view", "skills_list", "skill_manage",
       "browser_navigate", "memory", "session_search", "image_generate", "vision_analyze")

PROBE = r'''
import json
from hermes_cli.plugins import discover_plugins
discover_plugins()
from gateway.run import _load_gateway_config
from hermes_cli.tools_config import _get_platform_tools
from model_tools import get_tool_definitions
ts = sorted(_get_platform_tools(_load_gateway_config(), "api_server"))
defs = get_tool_definitions(enabled_toolsets=ts, quiet_mode=True)
names = sorted((d.get("function") or d).get("name") for d in defs)
print("RESULT " + json.dumps({"toolsets": ts, "tools": names}))
'''

results = []


def check(label: str, ok: bool) -> bool:
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def resolve(cfg: Path, plugin: Path) -> dict:
    home = Path(tempfile.mkdtemp(prefix="agent-tools-"))
    try:
        shutil.copy(cfg, home / "config.yaml")
        shutil.copytree(plugin, home / "plugins" / "jarvis")
        env = {"HERMES_HOME": str(home), "HOME": str(home), "PATH": "/usr/bin:/bin",
               "JARVIS_SKILLS_ROOT": "/nonexistent"}
        p = subprocess.run([str(HERMES / "venv" / "bin" / "python"), "-c", PROBE], cwd=str(home), env=env,
                           capture_output=True, text=True, timeout=180)
        line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
        if not line:
            print(p.stderr[-1500:])
            return {"toolsets": [], "tools": []}
        return json.loads(line[-1][7:])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", type=Path)
    ap.add_argument("--after", type=Path)
    ap.add_argument("--plugin", type=Path, default=PLUGIN)
    ap.add_argument("--slug")
    a = ap.parse_args()

    if a.before:
        b = resolve(a.before, a.plugin)
        print(f"BEFORE ({a.before}): toolsets {b['toolsets']}\n  {len(b['tools'])} tools: {', '.join(b['tools'])}")
        check(f"presence twin: the old config gives the agent developer tools "
              f"({sorted(set(b['tools']) & set(DEV))})", bool(set(b["tools"]) & set(DEV)))
    if a.after:
        r = resolve(a.after, a.plugin)
        print(f"AFTER ({a.after}): toolsets {r['toolsets']}\n  {len(r['tools'])} tools: {', '.join(r['tools'])}")
        check("the agent gets exactly the Jarvis tools", r["tools"] == ALLOWED)
        check(f"   and no developer tool ({sorted(set(r['tools']) & set(DEV))})", not set(r["tools"]) & set(DEV))
    if a.slug:
        seen_p = Path(f"/opt/{a.slug}/app-private/agent_tools_seen.json")
        if not check(f"{seen_p} exists (the proxy recorded a real request)", seen_p.is_file()):
            return 1
        seen = json.loads(seen_p.read_text())
        print(f"LEDGER {seen['at']}: offered {seen['offered']}\n  sent {seen['sent']}; stripped {seen['stripped']}"
              f"\n  system prompt {seen['system_chars']} chars; technical words in it: {seen['system_word_counts']}")
        check("the model was offered only Jarvis tools", set(seen["offered"]) <= set(ALLOWED) and bool(seen["offered"]))
        check("   nothing had to be stripped (Hermes offers nothing else)", not seen["stripped"])
        check("   no developer tool reached the model", not set(seen["sent"]) & set(DEV))
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
