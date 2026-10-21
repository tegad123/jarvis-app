#!/usr/bin/env python3
"""Hermes local modification #2 (X-Jarvis-Actor-Ref): inert without the header.

Live's gateway runs the same Hermes install, so the patch must change nothing
for a caller that does not send the header. This compares two trees with the
real Hermes code, never a mock:

  PRE   git archive of the install's HEAD before the patch (or --pre-ref)
  POST  that tree with docs/platform/hermes-actor-ref.patch applied
        (or, with --post-installed, the running install itself)

Each tree runs the same probe in its own interpreter (PYTHONPATH first; the
probe reports the files it loaded, and the check refuses a probe that loaded
the wrong tree). The probe records, as JSON:

  - the subprocess environment Hermes builds for a tool
    (tools.environments.local._sanitize_subprocess_env), with no session bound,
    with an API-server session bound (no reference), after clearing it, and
    with an inherited JARVIS_ACTOR_MSG in the parent environment;
  - what the API server's real middleware binds for requests: no header,
    a valid reference with the right key from loopback, the wrong key, no key,
    a non-loopback caller, and a malformed reference.

PRE and POST must be byte-identical everywhere no reference is bound; POST
must bind a reference only for an authenticated loopback caller and add it to
the tool environment only then, and drop it again after the turn.

    /usr/local/lib/hermes-agent/venv/bin/python docs/platform/verify_hermes_actor_ref.py
    ... --post-installed      after the patch is applied to the install
    ... --canary-leak         POST also binds JARVIS_ACTOR_MSG="" on every turn
                              (the first draft's behaviour); inertness must fail
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERMES = Path("/usr/local/lib/hermes-agent")
PY = HERMES / "venv" / "bin" / "python"
PATCH = Path(__file__).resolve().parent / "hermes-actor-ref.patch"
KEY = "k" * 48
REF = "app:" + "R" * 32

PROBE = r'''
import asyncio, json, os, sys
from pathlib import Path
import gateway.session_context as sc
import tools.environments.local as local
import gateway.platforms.api_server as api
out = {"files": [sc.__file__, local.__file__, api.__file__]}
base = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "PROBE_KEEP": "1"}

def env(extra_base=None):
    b = dict(base, **(extra_base or {}))
    e = local._sanitize_subprocess_env(b, None)
    return {k: e[k] for k in sorted(e) if k.startswith(("HERMES_SESSION", "HERMES_UI", "HERMES_CRON", "JARVIS_", "PROBE_"))}

out["env_unbound"] = env()
out["env_inherited_unbound"] = env({"JARVIS_ACTOR_MSG": "inherited"})
kw = dict(platform="api_server", chat_id="c1", session_key="k1", session_id="s1",
          async_delivery=False, cron_session="")
t = sc.set_session_vars(**kw)
out["env_api_bound_noref"] = env()
out["env_api_bound_noref_inherited"] = env({"JARVIS_ACTOR_MSG": "inherited"})
sc.clear_session_vars(t)
out["env_after_clear"] = env()
if "actor_ref" in sc.set_session_vars.__code__.co_varnames:
    if os.environ.get("CANARY_LEAK"):
        orig = sc.set_session_vars
        def leaky(*a, **k):
            k.setdefault("actor_ref", "")
            r = orig(*a, **k)
            sc._VAR_MAP["JARVIS_ACTOR_MSG"] = sc._SESSION_ACTOR_REF
            return r
        sc.set_session_vars = leaky
        t = sc.set_session_vars(**kw)
        out["env_api_bound_noref"] = env()
        sc.clear_session_vars(t)
        del sc._VAR_MAP["JARVIS_ACTOR_MSG"]
    t = sc.set_session_vars(actor_ref=sys.argv[2], **kw)
    out["env_api_bound_ref"] = env()
    sc.clear_session_vars(t)
    out["env_after_ref_cleared"] = env()

from gateway.config import PlatformConfig
from aiohttp.test_utils import make_mocked_request
from unittest import mock

async def through(headers, remote="127.0.0.1", key=sys.argv[1]):
    os.environ.pop("API_SERVER_KEY", None)
    ad = api.APIServerAdapter(PlatformConfig(enabled=True, extra={"key": key} if key else {}))
    mw = ad._make_profile_prefix_middleware()
    seen = {}
    async def handler(req):
        seen["bound"] = getattr(api, "_api_request_actor_ref", None).get() if hasattr(api, "_api_request_actor_ref") else None
        return api.web.Response(text="ok")
    req = make_mocked_request("POST", "/v1/chat/completions", headers=headers)
    with mock.patch.object(type(req), "remote", new_callable=mock.PropertyMock, return_value=remote):
        r = await mw(req, handler)
    return {"status": r.status, "bound": seen.get("bound")}

good = {"Authorization": f"Bearer {sys.argv[1]}"}
cases = {
    "no_header": through(good),
    "ref_ok": through(dict(good, **{"X-Jarvis-Actor-Ref": sys.argv[2]})),
    "ref_wrong_key": through({"Authorization": "Bearer wrong", "X-Jarvis-Actor-Ref": sys.argv[2]}),
    "ref_no_auth": through({"X-Jarvis-Actor-Ref": sys.argv[2]}),
    "ref_not_loopback": through(dict(good, **{"X-Jarvis-Actor-Ref": sys.argv[2]}), remote="203.0.113.9"),
    "ref_malformed": through(dict(good, **{"X-Jarvis-Actor-Ref": "tegad8@gmail.com"})),
    "ref_server_without_key": through(dict(good, **{"X-Jarvis-Actor-Ref": sys.argv[2]}), key=""),
}
async def run_all():
    return {k: await v for k, v in cases.items()}
out["middleware"] = asyncio.run(run_all())
print(json.dumps(out, sort_keys=True))
'''

PASS = FAIL = 0


def check(label: str, ok: bool) -> bool:
    global PASS, FAIL
    PASS, FAIL = PASS + bool(ok), FAIL + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def probe(tree: Path, canary: bool) -> dict:
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(tree), "PYTHONDONTWRITEBYTECODE": "1",
           "HERMES_HOME": tempfile.mkdtemp(prefix="hprobe-home-")}
    if canary:
        env["CANARY_LEAK"] = "1"
    r = subprocess.run([str(PY), "-c", PROBE, KEY, REF], cwd=str(tree), env=env,
                       capture_output=True, text=True, timeout=180)
    shutil.rmtree(env["HERMES_HOME"], ignore_errors=True)
    if r.returncode != 0:
        print(r.stderr[-3000:])
        sys.exit(f"probe failed in {tree}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def archive(ref: str, dest: Path) -> None:
    dest.mkdir(parents=True)
    a = subprocess.run(["git", "-C", str(HERMES), "archive", ref], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(dest)], input=a.stdout, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre-ref", default="HEAD", help="Hermes commit before the patch")
    ap.add_argument("--post-installed", action="store_true")
    ap.add_argument("--canary-leak", action="store_true")
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="hermes-actor-ref-"))
    try:
        pre = work / "pre"
        archive(a.pre_ref, pre)
        if a.post_installed:
            post = HERMES
        else:
            post = work / "post"
            shutil.copytree(pre, post, symlinks=True)
            subprocess.run(["patch", "-s", "-p1", "-d", str(post), "-i", str(PATCH)], check=True)
        print(f"PRE {a.pre_ref} at {pre}; POST {post}")
        P0, P1 = probe(pre, False), probe(post, a.canary_leak)
        check(f"PRE loaded its own tree ({sorted({str(Path(f).parent.parent) for f in P0['files']})})",
              all(f.startswith(str(pre) + "/") for f in P0["files"]))
        check(f"POST loaded its own tree ({sorted({str(Path(f).parent.parent) for f in P1['files']})})",
              all(f.startswith(str(post) + "/") for f in P1["files"]))
        check("PRE has no actor reference code (the comparison is between different code)",
              "env_api_bound_ref" not in P0 and P0["middleware"]["ref_ok"]["bound"] is None)

        print("No reference bound: PRE and POST byte-identical")
        for k in ("env_unbound", "env_inherited_unbound", "env_api_bound_noref",
                  "env_api_bound_noref_inherited", "env_after_clear"):
            same = json.dumps(P0[k], sort_keys=True) == json.dumps(P1[k], sort_keys=True)
            check(f"   tool env {k}: identical ({len(P1[k])} vars; JARVIS_ACTOR_MSG "
                  f"{'present=' + repr(P1[k]['JARVIS_ACTOR_MSG']) if 'JARVIS_ACTOR_MSG' in P1[k] else 'absent'})",
                  same)
        check("   presence twin: an API-server session is really bound in that env "
              f"(HERMES_SESSION_PLATFORM={P1['env_api_bound_noref'].get('HERMES_SESSION_PLATFORM')!r})",
              P1["env_api_bound_noref"].get("HERMES_SESSION_PLATFORM") == "api_server")
        for k in ("no_header", "ref_wrong_key", "ref_no_auth", "ref_not_loopback", "ref_malformed",
                  "ref_server_without_key"):
            m0, m1 = P0["middleware"][k], P1["middleware"][k]
            check(f"   middleware {k}: same status as before ({m0['status']} -> {m1['status']}), "
                  f"nothing bound ({m1['bound']!r})", m0["status"] == m1["status"] and not m1["bound"])

        print("Reference bound (POST only)")
        check(f"   the app's request (loopback, right key) binds it ({P1['middleware']['ref_ok']['bound']!r})",
              P1["middleware"]["ref_ok"]["bound"] == REF)
        check("   the tool env carries it as JARVIS_ACTOR_MSG",
              P1.get("env_api_bound_ref", {}).get("JARVIS_ACTOR_MSG") == REF)
        check("   it overrides an inherited value only while bound, and is gone after the turn "
              f"({'JARVIS_ACTOR_MSG' in P1.get('env_after_ref_cleared', {})})",
              "JARVIS_ACTOR_MSG" not in P1.get("env_after_ref_cleared", {"JARVIS_ACTOR_MSG": 1}))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
