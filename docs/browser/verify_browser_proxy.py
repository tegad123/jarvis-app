#!/usr/bin/env python3
"""The browser egress's optional upstream proxy (plan section 4.7, step 16).

Runs the real app/browser_egress.py (python3 -I, a unix socket in a temp
directory) against two local stand-ins on loopback: a destination web
server that counts the connections it gets, and an upstream CONNECT proxy
that records what it was asked for. Proves:
- unset: the egress connects directly; the upstream sees nothing (and the
  unit renders no proxy line);
- set: an http request and a CONNECT both leave through the upstream, which
  is asked for the address the egress checked (not the name), with the
  credential from $CREDENTIALS_DIRECTORY when there is one; the log names the
  upstream host and port and never the credential;
- the destination is still checked first: a refused destination never
  reaches the upstream;
- an unreachable, refusing or private upstream fails the request 502 with
  one "upstream_failed" log line, and the destination gets no connection
  (no direct fallback);
- a malformed JARVIS_EGRESS_UPSTREAM stops the egress at start;
- the provisioner's browser_proxy block: valid blocks render the unit lines,
  bad ones are refused.

--canary runs a copy of the egress that falls back to a direct connection
when the upstream fails; the no-fallback checks must FAIL.

Touches nothing outside a temp directory. Never :8700 or :8710.
"""
import base64
import json
import os
import pathlib
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import provision_tenant as pt  # noqa: E402

CANARY = "--canary" in sys.argv
ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  ({str(extra)[:400]})"))


def free_port(ip):
    s = socket.socket()
    s.bind((ip, 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Dest:
    """A one-page web server that counts its connections."""

    def __init__(self, ip):
        self.ip, self.hits = ip, 0
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((ip, 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.hits += 1
            try:
                c.settimeout(5)
                buf = b""
                while b"\r\n\r\n" not in buf:
                    d = c.recv(4096)
                    if not d:
                        break
                    buf += d
                body = b"dest-ok"
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
            except OSError:
                pass
            finally:
                c.close()


class Upstream:
    """A CONNECT proxy that records each request head, then tunnels (or
    answers `status` without tunnelling)."""

    def __init__(self, ip, status=200):
        self.ip, self.status, self.heads = ip, status, []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((ip, 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c):
        try:
            c.settimeout(10)
            buf = b""
            while b"\r\n\r\n" not in buf:
                d = c.recv(4096)
                if not d:
                    return
                buf += d
            head, _, rest = buf.partition(b"\r\n\r\n")
            self.heads.append(head.decode("latin-1"))
            if self.status != 200:
                c.sendall(b"HTTP/1.1 %d No\r\nContent-Length: 0\r\n\r\n" % self.status)
                return
            host, _, port = head.split(b"\r\n")[0].split(b" ")[1].decode().rpartition(":")
            u = socket.create_connection((host.strip("[]"), int(port)), 5)
            c.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                u.sendall(rest)

            def pipe(a, b):
                try:
                    while True:
                        d = a.recv(65536)
                        if not d:
                            break
                        b.sendall(d)
                except OSError:
                    pass
                finally:
                    try:
                        b.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
            t = threading.Thread(target=pipe, args=(u, c), daemon=True)
            t.start()
            pipe(c, u)
            t.join(10)
            u.close()
        except OSError:
            pass
        finally:
            c.close()


def egress_source(work):
    src = (ROOT / "app" / "browser_egress.py").read_text()
    if not CANARY:
        return ROOT / "app" / "browser_egress.py"
    broken = src.replace(
        "            if pair is None:\n                _refuse(cw, 502, uwhy)\n",
        "            if pair is None:\n                pair = await asyncio.open_connection(addr, port)\n"
        "            if pair is None:\n                _refuse(cw, 502, uwhy)\n")
    assert broken != src, "canary patch did not apply"
    p = work / "browser_egress_canary.py"
    p.write_text(broken)
    return p


def start_egress(work, name, allow, upstream=None, cred=None):
    sock = work / f"{name}.sock"
    env = {"PATH": "/usr/bin:/bin", "JARVIS_EGRESS_LISTEN": str(sock), "JARVIS_EGRESS_TEST_ALLOW": ",".join(allow)}
    if upstream is not None:
        env["JARVIS_EGRESS_UPSTREAM"] = upstream
    if cred is not None:
        d = work / f"{name}-creds"
        d.mkdir()
        (d / "upstream_auth").write_text(cred)
        env["CREDENTIALS_DIRECTORY"] = str(d)
    logf = open(work / f"{name}.log", "w")
    p = subprocess.Popen(["/usr/bin/python3", "-I", str(egress_source(work))], env=env, stdout=logf,
                         stderr=subprocess.STDOUT)
    for _ in range(100):
        if sock.exists() or p.poll() is not None:
            break
        time.sleep(0.05)
    return p, sock, work / f"{name}.log"


def ask(sock, raw, then=b""):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(30)
    s.connect(str(sock))
    s.sendall(raw)
    out = b""
    sent = False
    try:
        while True:
            d = s.recv(65536)
            if not d:
                break
            out += d
            if then and not sent and b"\r\n\r\n" in out:
                s.sendall(then)
                sent = True
    except OSError:
        pass
    s.close()
    return out.decode("latin-1")


def events(logp):
    out = []
    for ln in logp.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            out.append({"raw": ln})
    return out


def get(ip, port):
    return f"GET http://{ip}:{port}/page HTTP/1.1\r\nHost: {ip}:{port}\r\n\r\n".encode()


def provisioner_checks():
    base = {"slug": "verifyco", "browser": "enabled"}
    ck("provisioner: no browser_proxy renders no unit line", pt.egress_proxy_lines(base) == "")
    lines = pt.egress_proxy_lines({**base, "browser_proxy": {"host": "proxy.example.net", "port": 3128}})
    ck("provisioner: host and port render the upstream line and no credential",
       lines == "Environment=JARVIS_EGRESS_UPSTREAM=proxy.example.net:3128", lines)
    lines = pt.egress_proxy_lines({**base, "browser_proxy": {"host": "proxy.example.net", "port": 3128, "auth": True}})
    ck("provisioner: auth: true loads the credential file as a systemd credential (no secret in the unit)",
       lines.splitlines() == ["Environment=JARVIS_EGRESS_UPSTREAM=proxy.example.net:3128",
                              "LoadCredential=upstream_auth:/etc/jarvis/verifyco-browser-proxy-auth"], lines)
    tpl = (ROOT / "scripts" / "templates" / "egress.service").read_text()
    ck("the egress unit template carries the proxy lines placeholder", "{{EGRESS_PROXY_LINES}}" in tpl)

    def refused(m):
        try:
            pt.browser_proxy(m)
        except pt.Refuse:
            return True
        return False
    good = {"host": "proxy.example.net", "port": 3128}
    for label, block, man in [
        ("a URL as host", {**good, "host": "http://proxy.example.net"}, base),
        ("credentials in host", {**good, "host": "u:p@proxy.example.net"}, base),
        ("a private address", {**good, "host": "10.0.0.5"}, base),
        ("a loopback address", {**good, "host": "127.0.0.1"}, base),
        ("port 0", {**good, "port": 0}, base),
        ("port as text", {**good, "port": "3128"}, base),
        ("a boolean port", {**good, "port": True}, base),
        ("auth as text", {**good, "auth": "yes"}, base),
        ("an unknown key", {**good, "password": "x"}, base),
        ("not a mapping", "proxy.example.net:3128", base),
        ("the browser off", good, {"slug": "verifyco"}),
    ]:
        ck(f"provisioner refuses browser_proxy: {label}", refused({**man, "browser_proxy": block}))
    ck("provisioner: a public IPv4 upstream is accepted (presence twin)",
       pt.browser_proxy({**base, "browser_proxy": {"host": "1.1.1.1", "port": 8080}})
       == {"host": "1.1.1.1", "port": 8080, "auth": False})


def main() -> int:
    for k in ("AUTH_BASE", "APP_PORT"):
        if os.environ.get(k, "").endswith(("8700", "8710")):
            sys.exit("REFUSING: live or testco port")
    print("CANARY: an egress that falls back to a direct connection" if CANARY else "real egress")
    provisioner_checks()
    ip = f"127.{secrets.randbelow(250) + 3}.0.61"
    dest = Dest(ip)
    up = Upstream(ip)
    deny = Upstream(ip, status=407)
    dead = free_port(ip)
    secret = "verify-user:" + secrets.token_urlsafe(18)
    dpair, upair, npair, xpair = (f"{ip}:{p}" for p in (dest.port, up.port, deny.port, dead))
    work = pathlib.Path(tempfile.mkdtemp(prefix="verify-proxy-"))
    procs = []
    try:
        # 1. Unset: direct.
        p, s, lg = start_egress(work, "direct", [dpair]); procs.append(p)
        h0 = dest.hits
        r = ask(s, get(ip, dest.port))
        ck("unset: the request reaches the destination directly",
           "dest-ok" in r and dest.hits - h0 == 1 and up.heads == [], (r[:120], dest.hits - h0, up.heads))
        ev = [e for e in events(lg) if e.get("verdict") == "allowed"]
        ck("unset: logged allowed, not through an upstream", len(ev) == 1 and ev[0].get("via") == "test_allow", ev)

        # 2. Set, with a credential.
        p, s, lg = start_egress(work, "up", [dpair, upair], upstream=upair, cred=secret); procs.append(p)
        h0, n0 = dest.hits, len(up.heads)
        r = ask(s, get(ip, dest.port))
        heads = up.heads[n0:]
        ck("set: an http request leaves through the upstream and gets the page",
           "dest-ok" in r and len(heads) == 1 and dest.hits - h0 == 1, (r[:120], heads))
        want_auth = "Proxy-Authorization: Basic " + base64.b64encode(secret.encode()).decode()
        ck("set: the upstream is asked for the checked address, with the credential",
           bool(heads) and heads[0].startswith(f"CONNECT {dpair} HTTP/1.1") and want_auth in heads[0], heads)
        n1 = len(up.heads)
        r = ask(s, f"CONNECT {dpair} HTTP/1.1\r\nHost: {dpair}\r\n\r\n".encode(),
                then=f"GET /page HTTP/1.1\r\nHost: {dpair}\r\n\r\n".encode())
        ck("set: a CONNECT (https) also leaves through the upstream",
           "200 Connection Established" in r and "dest-ok" in r and len(up.heads) - n1 == 1, (r[:200], up.heads[n1:]))
        n2, h2 = len(up.heads), dest.hits
        r = ask(s, b"GET http://10.1.2.3/ HTTP/1.1\r\nHost: 10.1.2.3\r\n\r\n")
        ck("set: a private destination is refused before the upstream is asked",
           " 403 " in r.split("\r\n")[0] and len(up.heads) == n2 and dest.hits == h2, (r[:120], up.heads[n2:]))
        text = lg.read_text()
        start = [e for e in events(lg) if e.get("verdict") == "upstream"]
        ck("set: the log names the upstream host and port and says a credential is loaded",
           start == [{"ev": "egress", "verdict": "upstream", "upstream": upair, "auth": True}], start)
        ck("the log never carries the credential, plain or encoded",
           secret not in text and base64.b64encode(secret.encode()).decode() not in text
           and secret.split(":", 1)[1] not in text)
        ck("set: allowed lines say they went through the upstream",
           [e.get("via") for e in events(lg) if e.get("verdict") == "allowed"] == ["test_allow+upstream"] * 2,
           events(lg))

        # 3. Upstream failures never connect directly.
        for label, name, target, allow in [
                ("unreachable", "dead", xpair, [dpair, xpair]),
                ("refusing (407)", "deny", npair, [dpair, npair]),
                ("on a private address", "private", "10.255.255.1:3128", [dpair])]:
            p, s, lg = start_egress(work, name, allow, upstream=target); procs.append(p)
            h0 = dest.hits
            r = ask(s, get(ip, dest.port))
            fails = [e for e in events(lg) if e.get("verdict") == "upstream_failed"]
            ck(f"upstream {label}: the request fails 502, one upstream_failed line naming the upstream",
               " 502 " in (r.split("\r\n") or [""])[0] and len(fails) == 1 and fails[0].get("upstream") == target,
               (r[:120], fails))
            ck(f"upstream {label}: the destination gets no connection (no direct fallback)",
               dest.hits == h0 and "dest-ok" not in r, (dest.hits - h0, r[:120]))

        # 4. A malformed setting stops it at start.
        p, s, lg = start_egress(work, "bad", [dpair], upstream="no-port-here")
        rc = p.wait(10)
        ck("a malformed JARVIS_EGRESS_UPSTREAM stops the egress at start",
           rc == 2 and not s.exists() and any(e.get("verdict") == "refused" for e in events(lg)), (rc, lg.read_text()))
    finally:
        for p in procs:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        for srv in (dest, up, deny):
            srv.sock.close()
        subprocess.run(["rm", "-rf", str(work)])
    n, t = sum(ok), len(ok)
    print(f"\n{n}/{t} passed" + ("  (canary run: a FAIL above is the point)" if CANARY else ""))
    return 0 if n == t else 1


if __name__ == "__main__":
    sys.exit(main())
