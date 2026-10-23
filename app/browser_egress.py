"""The browser's only way out: an HTTP proxy that reaches public addresses only.

Runs in `<slug>-egress.service` (DynamicUser, systemd IPAddressDeny on every
private, loopback and link-local range: the kernel refuses those even if this
code were wrong). The browser worker reaches it through `<slug>-egress.socket`,
a unix socket only the browser user can open.

It resolves each host itself and connects to the address it checked, so a DNS
answer cannot change between the check and the connection. CONNECT for https,
absolute-form requests for http (one request per connection). Ports 80, 443
and 8443 only. Every connection is one JSON line in the journal: host, port,
verdict. Standard library only.
"""
import asyncio
import ipaddress
import json
import os
import socket
import sys
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

PORTS = (80, 443, 8443)
HEAD_MAX = 16384
IDLE_S = 120
MAX_CONNECTIONS = 64
_active = 0


def log(**kw) -> None:
    print(json.dumps({"ev": "egress", **kw}), flush=True)


def public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not (addr.is_multicast or addr.is_reserved or addr.is_loopback
                                   or addr.is_link_local or addr.is_private or addr.is_unspecified)


def _test_allow() -> List[Tuple[str, int]]:
    """JARVIS_EGRESS_TEST_ALLOW=127.0.0.1:PORT,... lets the verifier reach its
    own local test site. Never rendered into a unit; announced when set."""
    out = []
    for item in os.environ.get("JARVIS_EGRESS_TEST_ALLOW", "").split(","):
        host, _, port = item.strip().rpartition(":")
        if host and port.isdigit():
            out.append((host, int(port)))
    return out


TEST_ALLOW = _test_allow()


async def resolve(host: str, port: int) -> Tuple[Optional[str], str]:
    """(address to connect to, reason). None when nothing public answers."""
    if (host, port) in TEST_ALLOW:
        return host, "test_allow"
    if port not in PORTS:
        return None, f"port {port} is not allowed"
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return None, "name does not resolve"
    addrs = [i[4][0] for i in infos]
    bad = [a for a in addrs if not public(a)]
    if bad:
        return None, "resolves to a private or local address"
    return (addrs[0], "public") if addrs else (None, "name does not resolve")


async def _pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await asyncio.wait_for(r.read(65536), IDLE_S)
            if not data:
                break
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.TimeoutError, asyncio.CancelledError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


def _refuse(w: asyncio.StreamWriter, code: int, why: str) -> None:
    body = f"Blocked by Jarvis: {why}\n".encode()
    w.write(f"HTTP/1.1 {code} Blocked\r\nX-Jarvis-Egress: blocked\r\nContent-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)


async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
    global _active
    _active += 1
    try:
        if _active > MAX_CONNECTIONS:
            _refuse(cw, 503, "too many connections")
            return
        try:
            head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), 30)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            return
        if len(head) > HEAD_MAX:
            _refuse(cw, 431, "request head too large")
            return
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = (lines[0].split(" ") + ["", "", ""])[:3]
        if method == "CONNECT":
            host, _, port_s = target.rpartition(":")
            host = host.strip("[]")
            port = int(port_s) if port_s.isdigit() else 0
            rest = b""
        else:
            u = urlsplit(target)
            if u.scheme != "http" or not u.hostname:
                _refuse(cw, 400, "only absolute http requests and CONNECT are proxied")
                log(verdict="blocked", why="bad request form", method=method[:10])
                return
            host, port = u.hostname, u.port or 80
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            kept = [ln for ln in lines[1:] if ln and not ln.lower().startswith(
                ("proxy-connection:", "connection:", "proxy-authorization:", "keep-alive:"))]
            rest = (f"{method} {path} {version}\r\n" + "\r\n".join(kept) + "\r\nConnection: close\r\n\r\n").encode(
                "latin-1")
        addr, why = await resolve(host, port)
        if addr is None:
            _refuse(cw, 403, why)
            log(verdict="blocked", host=host[:200], port=port, why=why)
            return
        try:
            ur, uw = await asyncio.wait_for(asyncio.open_connection(addr, port), 15)
        except (OSError, asyncio.TimeoutError):
            _refuse(cw, 502, "could not connect")
            log(verdict="failed", host=host[:200], port=port)
            return
        log(verdict="allowed", host=host[:200], port=port, via=why)
        if method == "CONNECT":
            cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await cw.drain()
        else:
            uw.write(rest)
            await uw.drain()
        await asyncio.gather(_pipe(cr, uw), _pipe(ur, cw))
    finally:
        _active -= 1
        try:
            cw.close()
        except Exception:
            pass


async def main() -> None:
    if TEST_ALLOW:
        log(verdict="test_mode", allow=[f"{h}:{p}" for h, p in TEST_ALLOW])
    fds = int(os.environ.get("LISTEN_FDS", "0") or 0)
    if fds >= 1 and os.environ.get("LISTEN_PID") == str(os.getpid()):
        server = await asyncio.start_unix_server(handle, sock=socket.socket(fileno=3), limit=HEAD_MAX * 2)
    else:
        path = os.environ.get("JARVIS_EGRESS_LISTEN", "")
        if not path:
            log(verdict="refused", why="no socket from systemd and no JARVIS_EGRESS_LISTEN")
            sys.exit(2)
        if os.path.exists(path):
            os.unlink(path)
        server = await asyncio.start_unix_server(handle, path=path, limit=HEAD_MAX * 2)
        os.chmod(path, 0o600)
    log(verdict="ready")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
