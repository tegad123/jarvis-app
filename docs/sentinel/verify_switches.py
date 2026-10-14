#!/usr/bin/env python3
"""Per-piece kill switches (app/switches.py) fail closed, each on its own.

One sandbox port (8718), restarted per configuration, behind the fake Google
from docs/broker/verify_broker.py:
  all on              boots; reads work (the presence twin for every refusal)
  bad value           refuses boot
  sentinel on, bad DB refuses boot (no Postgres, no Sentinel, no app)
  sentinel off, no DB boots; reads work; writes, outbound, approvals 503
  broker off          every broker call 503 broker_disabled, counted
  model_proxy off     503 model_disabled, counted; on gives model_not_configured

    VERIFY_OPERATOR_PASSPHRASE=... /opt/jarvis/venv/bin/python docs/sentinel/verify_switches.py
"""
import os
import subprocess
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "broker"))
import verify_broker as vb  # noqa: E402
import verify_sentinel_broker as vsb  # noqa: E402

PORT = 8718
SWITCH_ENV = ("VERIFY_SWITCH_BROKER", "VERIFY_SWITCH_SENTINEL", "VERIFY_SWITCH_MODEL_PROXY", "VERIFY_PG_DSN")
BAD_DSN = "postgresql:///jarvis_verify_no_such_db?host=/var/run/postgresql&user=jarvis_verify"
results = []


def ck(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f" — {detail}"))


def configure(**switches):
    for k in SWITCH_ENV:
        os.environ.pop(k, None)
    os.environ.update(switches)


def boot_refused(endpoint, **switches):
    """Start must fail and the app log must say why."""
    configure(**switches)
    box = Path("/tmp/claude-0/-root/broker-sandbox-sw")
    env = dict(os.environ, TASKS_SANDBOX=str(box), TASKS_PORT=str(PORT), VERIFY_ALLOW_WRITES="1",
               VERIFY_GOOGLE_API_ENDPOINT=endpoint, VERIFY_APP_ROOT=str(vb.APP), VERIFY_SKILLS_ROOT=str(vb.SKILLS))
    p = subprocess.run(["bash", str(vb.SANDBOX), "start"], env=env, capture_output=True, text=True, timeout=120)
    log = (box / "app.log").read_text() if (box / "app.log").exists() else ""
    vb.stop_sandbox(env)
    return p.returncode != 0, log


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), vb.FakeGoogle)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{srv.server_address[1]}/"
    env = None
    try:
        print("── all on: boots, reads work")
        configure()
        box, env = vb.start_sandbox("sw", PORT, "1", endpoint)
        key = vb.env_value(box / "hermes/.env", "API_SERVER_KEY")
        dana = vsb.Session(PORT, "dana")
        h = dana.call("/api/health")[1]
        ck("health reports every switch on", h.get("switches") == {"broker": "on", "sentinel": "on", "model_proxy": "on"},
           str(h.get("switches")))
        out = vsb.probe(PORT, key, box, ["profile:"])
        ck("a read goes through the broker", "ok" in out.get("profile:", {}), str(out))
        ck("the owner's approvals queue answers", dana.call("/api/approvals")[0] == 200)
        vb.stop_sandbox(env)
        env = None

        print("── a switch value that is not on/off refuses boot")
        refused, log = boot_refused(endpoint, VERIFY_SWITCH_BROKER="yes")
        ck("JARVIS_SWITCH_BROKER=yes: the app does not come up", refused)
        ck("…and the log names the switch", "JARVIS_SWITCH_BROKER must be on or off" in log, log[-300:])

        print("── Sentinel on without its database refuses boot")
        refused, log = boot_refused(endpoint, VERIFY_PG_DSN=BAD_DSN)
        ck("unreachable platform database: the app does not come up", refused)
        ck("…and the log says Sentinel refused", "REFUSED: Sentinel is on" in log, log[-300:])
        refused, log = boot_refused(endpoint, VERIFY_PG_DSN="")
        ck("no JARVIS_PG_DSN at all: the app does not come up", refused)
        ck("…and the log says Sentinel refused", "REFUSED: Sentinel is on" in log, log[-300:])

        print("── Sentinel off: boots without the database, writes fail closed")
        configure(VERIFY_SWITCH_SENTINEL="off", VERIFY_PG_DSN="")
        box, env = vb.start_sandbox("sw", PORT, "1", endpoint)
        key = vb.env_value(box / "hermes/.env", "API_SERVER_KEY")
        tdb = box / "threads.db"
        dana = vsb.Session(PORT, "dana")
        h = dana.call("/api/health")[1]
        ck("health reports sentinel off", (h.get("switches") or {}).get("sentinel") == "off", str(h.get("switches")))
        vb.FakeGoogle.seen.clear()
        c0 = vb.counter(tdb, "broker_sentinel_switch_off")
        out = vsb.probe(PORT, key, box, ["profile:", "send:dana@testco.example.com", "chat:verify-room"])
        ck("a read still works", "ok" in out.get("profile:", {}), str(out))
        s = out.get("send:dana@testco.example.com", {})
        ck("an internal send is refused with sentinel_off", "sentinel_off" in (s.get("msg") or ""), str(s))
        c = out.get("chat:verify-room", {})
        ck("a chat post is not delivered", (c.get("ok") or {}).get("posted") is False or "raised" in c, str(c))
        ck("fake Google saw no write", not vsb.posts(), str(vsb.posts()))
        ck("…refusals counted (broker_sentinel_switch_off +2)", vb.counter(tdb, "broker_sentinel_switch_off") - c0 >= 2,
           f"{c0}->{vb.counter(tdb, 'broker_sentinel_switch_off')}")
        ck("the approvals queue answers 503", dana.call("/api/approvals")[0] == 503)
        vb.stop_sandbox(env)
        env = None

        print("── broker off: every broker call 503, counted")
        configure(VERIFY_SWITCH_BROKER="off")
        box, env = vb.start_sandbox("sw", PORT, "1", endpoint)
        key = vb.env_value(box / "hermes/.env", "API_SERVER_KEY")
        tdb = box / "threads.db"
        vb.FakeGoogle.seen.clear()
        c0 = vb.counter(tdb, "broker_switch_off")
        out = vsb.probe(PORT, key, box, ["profile:", "send:dana@testco.example.com"])
        ck("a read is refused with broker_disabled", "broker_disabled" in (out.get("profile:", {}).get("msg") or ""), str(out))
        ck("a send is refused with broker_disabled",
           "broker_disabled" in (out.get("send:dana@testco.example.com", {}).get("msg") or ""), str(out))
        ck("fake Google saw nothing at all", not vb.FakeGoogle.seen, str(vb.FakeGoogle.seen))
        ck("…counted (broker_switch_off +2)", vb.counter(tdb, "broker_switch_off") - c0 >= 2)
        code, d = vb.http(PORT, "google/status", None, {"Authorization": f"Bearer {key}"})
        ck("GET /api/broker/google/status → 503", code == 503, f"{code} {d}")
        vb.stop_sandbox(env)
        env = None

        print("── model_proxy off: 503 model_disabled, counted")
        for state, want in (("off", "model_disabled"), ("on", "model_not_configured")):
            configure(VERIFY_SWITCH_MODEL_PROXY=state)
            box, env = vb.start_sandbox("sw", PORT, "1", endpoint)
            key = vb.env_value(box / "hermes/.env", "API_SERVER_KEY")
            tdb = box / "threads.db"
            c0 = vb.counter(tdb, "model_proxy_switch_off")
            s = vsb.Session(PORT, None)
            code, d = s.call("/api/model/anthropic/v1/messages", {"model": "claude-sonnet-5-5", "max_tokens": 1,
                             "messages": [{"role": "user", "content": "hi"}]}, {"x-api-key": key})
            got = (d.get("error") or {}).get("type") if isinstance(d.get("error"), dict) else None
            ck(f"model_proxy {state}: 503 {want}", code == 503 and got == want, f"{code} {d}")
            ck(f"…model_proxy_switch_off {'+1' if state == 'off' else 'unchanged'}",
               vb.counter(tdb, "model_proxy_switch_off") - c0 == (1 if state == "off" else 0))
            vb.stop_sandbox(env)
            env = None
    finally:
        if env:
            vb.stop_sandbox(env)
        configure()
        srv.shutdown()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
