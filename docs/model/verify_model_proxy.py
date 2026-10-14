#!/usr/bin/env python3
"""The model proxy (app/model_proxy.py), driven by Hermes's own Anthropic
client against a fake Anthropic upstream.

  M (8716): key configured, upstream = the fake
  N (8717): no key configured

    VERIFY_OPERATOR_PASSPHRASE=... python docs/model/verify_model_proxy.py
"""
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "broker"))
import verify_broker as vb  # noqa: E402

HERMES = Path("/usr/local/lib/hermes-agent")
HERMES_PY = HERMES / "venv/bin/python"
UPSTREAM_KEY = "verify-upstream-key"
results = []


def ck(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f" — {detail}"))


class FakeAnthropic(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        FakeAnthropic.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                   "body": body})
        if self.path.startswith("/v1/messages/count_tokens"):
            return self._json(200, {"input_tokens": 7})
        if body.get("model") == "fail-model":
            return self._json(429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = [
                ("message_start", {"type": "message_start", "message": {
                    "id": "msg_v", "type": "message", "role": "assistant", "model": body["model"], "content": [],
                    "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 3, "output_tokens": 0}}}),
                ("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": "po"}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": "ng"}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn",
                                                                       "stop_sequence": None},
                                   "usage": {"output_tokens": 2}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            for name, data in events:
                self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.05)
            return
        return self._json(200, {"id": "msg_v", "type": "message", "role": "assistant", "model": body["model"],
                                "content": [{"type": "text", "text": "pong"}], "stop_reason": "end_turn",
                                "stop_sequence": None, "usage": {"input_tokens": 3, "output_tokens": 1}})

    def _json(self, code, obj):
        out = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


CLIENT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from agent.anthropic_adapter import build_anthropic_client
c = build_anthropic_client(sys.argv[2], sys.argv[3], timeout=60)
out = {}
def run(name, fn):
    try:
        out[name] = {"ok": fn()}
    except Exception as e:
        out[name] = {"raised": type(e).__name__, "status": getattr(e, "status_code", None), "msg": str(e)[:200]}
msg = [{"role": "user", "content": "ping"}]
run("create", lambda: c.messages.create(model="claude-verify", max_tokens=16, messages=msg).content[0].text)
def stream():
    with c.messages.stream(model="claude-verify", max_tokens=16, messages=msg) as s:
        return "".join(s.text_stream)
run("stream", stream)
run("count", lambda: c.messages.count_tokens(model="claude-verify", messages=msg).input_tokens)
run("fail", lambda: c.messages.create(model="fail-model", max_tokens=16, messages=msg))
print(json.dumps(out, default=str))
"""


def hermes_client(key, base):
    p = subprocess.run([str(HERMES_PY), "-c", CLIENT, str(HERMES), key, base], capture_output=True, text=True,
                       timeout=180, env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"})
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"_error": (p.stderr or p.stdout)[-800:]}


def post(port, path, headers):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/model/anthropic/{path}", method="POST",
                                 data=json.dumps({"model": "claude-verify", "max_tokens": 1,
                                                  "messages": [{"role": "user", "content": "x"}]}).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAnthropic)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    upstream = f"http://127.0.0.1:{srv.server_address[1]}"
    sandboxes = []
    try:
        import os
        os.environ.update(VERIFY_MODEL_UPSTREAM=upstream, VERIFY_MODEL_KEY=UPSTREAM_KEY)
        box, env = vb.start_sandbox("m", 8716, "0", "")
        sandboxes.append(env)
        key = vb.env_value(box / "hermes/.env", "API_SERVER_KEY")
        tdb = box / "threads.db"

        print("── M: who may call")
        ck("no key → 401", post(8716, "v1/messages", {})[0] == 401)
        ck("wrong key → 401", post(8716, "v1/messages", {"x-api-key": "nope"})[0] == 401)
        ck("forwarded (tunnel) request with the right key → 403",
           post(8716, "v1/messages", {"x-api-key": key, "CF-Connecting-IP": "203.0.113.9"})[0] == 403)
        ck("a path that is not proxied → 404", post(8716, "v1/complete", {"x-api-key": key})[0] == 404)
        ck("canary: nothing above reached the upstream", not FakeAnthropic.seen)

        print("── Hermes keeps the provisioned base_url (it drops non-/anthropic paths)")
        rule = subprocess.run([str(HERMES_PY), "-c", (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from hermes_cli.runtime_provider import _anthropic_base_url_override_ok as ok; "
            "print(ok(sys.argv[2]), ok(sys.argv[3]))"), str(HERMES),
            "http://127.0.0.1:8740/api/model/anthropic", "http://127.0.0.1:8740/api/model"],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"}).stdout.split()
        ck("Hermes accepts http://127.0.0.1:PORT/api/model/anthropic", rule[:1] == ["True"], str(rule))
        ck("canary: Hermes would discard /api/model (and call api.anthropic.com)", rule[1:2] == ["False"], str(rule))

        print("── M: Hermes's Anthropic client through the proxy")
        r0, e0 = vb.counter(tdb, "model_proxy_requests"), vb.counter(tdb, "model_proxy_upstream_error")
        out = hermes_client(key, "http://127.0.0.1:8716/api/model/anthropic")
        ck("client ran", "_error" not in out, str(out.get("_error")))
        ck("messages.create returns the model's text", out.get("create", {}).get("ok") == "pong", str(out.get("create")))
        ck("messages.stream streams the deltas", out.get("stream", {}).get("ok") == "pong", str(out.get("stream")))
        ck("count_tokens passes through", out.get("count", {}).get("ok") == 7, str(out.get("count")))
        ck("an upstream 429 reaches the client as a 429",
           out.get("fail", {}).get("status") == 429 and out["fail"].get("raised") == "RateLimitError",
           str(out.get("fail")))
        ck("…counted (model_proxy_upstream_error +1)", vb.counter(tdb, "model_proxy_upstream_error") - e0 == 1)
        ck("requests counted (model_proxy_requests +4)", vb.counter(tdb, "model_proxy_requests") - r0 == 4)
        keys = {s["headers"].get("x-api-key") for s in FakeAnthropic.seen}
        ck("upstream saw the platform key on every request, never the agent's",
           keys == {UPSTREAM_KEY} and len(FakeAnthropic.seen) == 4, str(len(FakeAnthropic.seen)))
        ck("the agent's key appears in no header the upstream saw",
           not any(key in v for s in FakeAnthropic.seen for v in s["headers"].values()))
        ck("no authorization header reached the upstream",
           not any("authorization" in s["headers"] for s in FakeAnthropic.seen))
        ck("the request body arrived intact", all(s["body"].get("messages") == [{"role": "user", "content": "ping"}]
                                                  for s in FakeAnthropic.seen))

        print("── N: no key configured")
        os.environ.pop("VERIFY_MODEL_KEY")
        box_n, env_n = vb.start_sandbox("n", 8717, "0", "")
        sandboxes.append(env_n)
        key_n = vb.env_value(box_n / "hermes/.env", "API_SERVER_KEY")
        FakeAnthropic.seen.clear()
        n0 = vb.counter(box_n / "threads.db", "model_proxy_not_configured")
        code, body = post(8717, "v1/messages", {"x-api-key": key_n})
        ck("503 model_not_configured", code == 503 and (body.get("error") or {}).get("type") == "model_not_configured",
           f"{code} {body}")
        ck("…counted (model_proxy_not_configured +1)",
           vb.counter(box_n / "threads.db", "model_proxy_not_configured") - n0 == 1)
        ck("…and nothing reached the upstream", not FakeAnthropic.seen)
    finally:
        for env in sandboxes:
            vb.stop_sandbox(env)
        srv.shutdown()
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
