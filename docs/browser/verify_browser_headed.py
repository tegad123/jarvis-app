#!/usr/bin/env python3
"""Each person's headed browser, live view and file isolation, without a tenant.

Runs the real egress proxy and the real browser worker in headed mode (full
Chromium on Xvfb) against a local test site. Chromium reads its managed
policy from a compiled-in path under /etc/opt, so this script re-runs itself
in a private mount namespace (unshare -m) with that directory on a tmpfs:
the host's /etc is never written, and the script checks that.

Proves (steps 3 and 5 of docs/plans/browser-har-comps.md):
- headed: the user agent has no "Headless", navigator.webdriver is not set,
  the window is 1440x900, two people get two different displays, a
  session-only cookie survives the browser closing and reopening, closing
  leaves no Xvfb behind, and with Xvfb missing the launch fails loudly and no
  browser starts (no quiet headless fallback);
- live view: a viewer gets the RFB greeting of its own person's display only
  through the worker; x11vnc has no TCP listener; handing back stops it;
- file isolation (mandatory): with dana's profile on disk, omar typing
  file:///<dana's profile> or file:///etc/hostname into his address bar sees
  neither, and a file picker cannot open. Canary (--canary): with the policy
  and the guard off, the same keystrokes DO show the file, so the check can
  fail.

Needs root (for the mount namespace). x11vnc comes from VERIFY_X11VNC, or
/usr/bin/x11vnc, or an unpacked copy in /tmp/x11vnc-local (apt-get download +
dpkg -x; nothing installed). Never :8700 or :8710.
"""
import http.server
import json
import os
import pathlib
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
import browser_launch as bl  # noqa: E402

SCR = pathlib.Path("/tmp/verify-browser-headed")
FULL = os.environ.get("VERIFY_CHROME", "/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
HOST_POLICY_DIR = pathlib.Path("/etc/opt")
CANARY = "--canary" in sys.argv
GUARD_ONLY = "--guard-only" in sys.argv
ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" ({extra})" if extra else ""), flush=True)


def x11vnc_bin() -> str:
    if os.environ.get("VERIFY_X11VNC"):
        return os.environ["VERIFY_X11VNC"]
    if os.access("/usr/bin/x11vnc", os.X_OK):
        return "/usr/bin/x11vnc"
    local = pathlib.Path("/tmp/x11vnc-local")
    if (local / "root/usr/bin/x11vnc").exists():
        wrap = local / "x11vnc.sh"
        wrap.write_text(f'#!/bin/sh\nLD_LIBRARY_PATH={local}/root/usr/lib/x86_64-linux-gnu '
                        f'exec {local}/root/usr/bin/x11vnc "$@"\n')
        wrap.chmod(0o755)
        return str(wrap)
    return ""


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def page(self, title, body, headers=()):
        data = f"<html><head><title>{title}</title></head><body>{body}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/ua":
            self.page("ua", "<p id=o>wait</p><script>setTimeout(function () {"
                            "document.getElementById('o').textContent = 'UA=' + "
                            "navigator.userAgent + ' WD=' + navigator.webdriver + ' OUTER=' + outerWidth + 'x' + "
                            "outerHeight + ' SCREEN=' + screen.width + 'x' + screen.height}, 600)</script>")
        elif path.startswith("/session/"):
            who = path.split("/")[2]
            self.page("session", f"<p>session for {who}</p>", [("Set-Cookie", f"sess={who}; Path=/")])
        elif path == "/picker":
            self.page("picker", "<input type=file id=f aria-label='Upload a file'>")
        else:
            self.page("get", f"<p>cookie is {self.headers.get('Cookie') or 'none'}</p>")


def descendants(pid: int):
    kids = {}
    for p in pathlib.Path("/proc").iterdir():
        if p.name.isdigit():
            try:
                st = (p / "stat").read_text()
                ppid = int(st[st.rfind(")") + 2:].split()[1])
                kids.setdefault(ppid, []).append(int(p.name))
            except (OSError, ValueError, IndexError):
                pass
    out, todo = [], [pid]
    while todo:
        for k in kids.get(todo.pop(), []):
            out.append(k)
            todo.append(k)
    return out


def comm(pid: int) -> str:
    try:
        return pathlib.Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def pss_mb(pids) -> int:
    """Proportional set size: shared pages counted once, as a cgroup's
    MemoryMax sees them (RSS would count Chromium's shared pages per process)."""
    total = 0
    for pid in pids:
        try:
            for ln in pathlib.Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
                if ln.startswith("Pss:"):
                    total += int(ln.split()[1])
        except OSError:
            pass
    return total // 1024


def call(sock, req, timeout=60):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    s.connect(str(sock))
    s.sendall(json.dumps(req).encode() + b"\n")
    return json.loads(s.makefile().readline() or "{}")


def wait_for(path, seconds=10):
    end = time.time() + seconds
    while time.time() < end and not os.path.exists(path):
        time.sleep(0.1)
    return os.path.exists(path)


# ── a minimal RFB client: enough to see a greeting and type keys ────────────

class RFB:
    def __init__(self, sock_path, person):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(20)
        self.s.connect(str(sock_path))
        self.s.sendall(json.dumps({"op": "view", "person": person}).encode() + b"\n")
        line = b""
        while not line.endswith(b"\n"):
            c = self.s.recv(1)
            if not c:
                break
            line += c
        self.reply = json.loads(line or b"{}")

    def handshake(self) -> bytes:
        greet = self._exact(12)
        self.s.sendall(b"RFB 003.008\n")
        n = self._exact(1)[0]
        types = self._exact(n)
        if 1 not in types:
            raise RuntimeError(f"no 'None' security type: {list(types)}")
        self.s.sendall(b"\x01")
        if struct.unpack(">I", self._exact(4))[0] != 0:
            raise RuntimeError("security failed")
        self.s.sendall(b"\x01")
        w, h = struct.unpack(">HH", self._exact(4))
        self._exact(16)
        self._exact(struct.unpack(">I", self._exact(4))[0])
        self.size = (w, h)
        return greet

    def key(self, keysym, down):
        self.s.sendall(struct.pack(">BBxxI", 4, 1 if down else 0, keysym))

    def tap(self, keysym, mods=()):
        for m in mods:
            self.key(m, True)
        self.key(keysym, True)
        self.key(keysym, False)
        for m in reversed(mods):
            self.key(m, False)
        time.sleep(0.03)

    def type(self, text):
        for ch in text:
            self.tap(ord(ch))

    def _exact(self, n):
        b = b""
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c:
                raise RuntimeError("closed")
            b += c
        return b

    def close(self):
        self.s.close()


CTRL, RETURN = 0xFFE3, 0xFF0D


def address_bar(viewer, url):
    viewer.tap(ord("l"), (CTRL,))
    time.sleep(0.4)
    viewer.type(url)
    viewer.tap(RETURN)
    time.sleep(2.5)


def in_namespace() -> int:
    managed = HOST_POLICY_DIR / "chrome_for_testing" / "policies" / "managed"
    subprocess.run(["mount", "-t", "tmpfs", "-o", "mode=755", "tmpfs", str(HOST_POLICY_DIR)], check=True)
    if not (CANARY or GUARD_ONLY):
        managed.mkdir(parents=True)
        (managed / "jarvis.json").write_text(json.dumps(bl.POLICY))
    policy = str(managed / "jarvis.json")
    vnc = x11vnc_bin()
    subprocess.run(["rm", "-rf", str(SCR)], check=True)
    SCR.mkdir(parents=True)
    third = secrets.randbelow(250) + 3
    ip = f"127.{third}.0.9"
    srv = http.server.ThreadingHTTPServer((ip, 0), Site)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    site = f"http://{ip}:{port}"
    base = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC", "DISPLAY"))}
    eg = subprocess.Popen([sys.executable, "-I", str(ROOT / "app" / "browser_egress.py")],
                          env={**base, "JARVIS_EGRESS_LISTEN": str(SCR / "egress.sock"),
                               "JARVIS_EGRESS_TEST_ALLOW": f"{ip}:{port}"},
                          stdout=open(SCR / "egress.log", "w"), stderr=subprocess.STDOUT)
    wait_for(SCR / "egress.sock")

    def worker(name, **extra):
        env = {**base, "JARVIS_BROWSER_BIN": FULL, "JARVIS_BROWSER_PROFILE": str(SCR / "profiles"),
               "JARVIS_EGRESS_SOCKET": str(SCR / "egress.sock"), "JARVIS_BROWSER_LISTEN": str(SCR / f"{name}.sock"),
               "JARVIS_BROWSER_TEST": "1", "JARVIS_BROWSER_MAX_OPEN": "3", "JARVIS_BROWSER_MODE": "headed",
               "JARVIS_X11VNC_BIN": vnc or "/nonexistent/x11vnc", "JARVIS_BROWSER_POLICY": policy, **extra}
        p = subprocess.Popen([sys.executable, "-I", str(ROOT / "app" / "browser_worker.py")], env=env,
                             stdout=open(SCR / f"{name}.log", "w"), stderr=subprocess.STDOUT)
        wait_for(SCR / f"{name}.sock")
        return p, SCR / f"{name}.sock"

    if CANARY:
        wk, sock = worker("w", JARVIS_BROWSER_TEST_NO_GUARD="1", JARVIS_BROWSER_POLICY="/dev/null")
    elif GUARD_ONLY:
        wk, sock = worker("w", JARVIS_BROWSER_TEST_NO_POLICY="1", JARVIS_BROWSER_POLICY="/dev/null")
    else:
        wk, sock = worker("w")
    try:
        if CANARY or GUARD_ONLY:
            return canary(sock, site, vnc)
        print("headed browser (real Chromium on Xvfb):")
        r = call(sock, {"op": "open", "person": "dana", "url": f"{site}/ua"})
        ck("dana's headed browser opens the test site", r.get("ok") is True, r.get("error") or "")
        time.sleep(1)
        snap = call(sock, {"op": "read", "person": "dana"}).get("snapshot") or ""
        ua = snap[snap.find("UA="):][:400]
        ck("the user agent does not say Headless", "UA=Mozilla" in ua and "Headless" not in ua, ua[:120])
        ck("navigator.webdriver is not set", "WD=false" in ua or "WD=undefined" in ua, ua[ua.find("WD="):][:20])
        outer = ua[ua.find("OUTER=") + 6:].split()[0] if "OUTER=" in ua else "0x0"
        ow, oh = (int(x) for x in outer.split("x"))
        ck("the screen is 1440x900 and the window fills it", "SCREEN=1440x900" in ua and ow >= 1438 and oh >= 898,
           f"window {outer}")
        st_d = call(sock, {"op": "status", "person": "dana"})
        call(sock, {"op": "open", "person": "omar", "url": f"{site}/get"})
        st_o = call(sock, {"op": "status", "person": "omar"})
        ck("status reports headed mode", st_d.get("mode") == "headed" and st_o.get("mode") == "headed")
        ck("two people get two different displays", st_d.get("display") and st_o.get("display")
           and st_d["display"] != st_o["display"], f"{st_d.get('display')} {st_o.get('display')}")
        procs = descendants(wk.pid)
        xvfbs = [p for p in procs if comm(p) == "Xvfb"]
        ck("one Xvfb per running browser", len(xvfbs) == 2, len(xvfbs))
        mem = pss_mb(procs)
        print(f"  NOTE  two headed browsers with their displays use about {mem} MB (PSS)")
        (SCR / "memory.txt").write_text(str(mem))

        call(sock, {"op": "open", "person": "dana", "url": f"{site}/session/dana1"})
        call(sock, {"op": "close", "person": "dana"})
        r = call(sock, {"op": "open", "person": "dana", "url": f"{site}/get"})
        ck("a session-only cookie survives closing and reopening", "sess=dana1" in (r.get("snapshot") or ""),
           (r.get("snapshot") or "")[-60:])
        r = call(sock, {"op": "open", "person": "omar", "url": f"{site}/get"})
        ck("   and it is dana's alone", "sess=dana1" not in (r.get("snapshot") or ""))

        print("live view (x11vnc on a unix socket, through the worker):")
        if not vnc:
            ck("x11vnc is available for this check (partly proven without it)", False)
        else:
            v = RFB(sock, "omar")
            greet = v.handshake() if v.reply.get("ok") else b""
            ck("omar's viewer gets the RFB greeting of his display", greet.startswith(b"RFB 003.") and
               getattr(v, "size", None) == (1440, 900), f"{v.reply} {greet!r}")
            vprocs = [p for p in descendants(wk.pid) if comm(p) == "x11vnc"]
            ss = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True).stdout
            ck("x11vnc has no TCP listener", vprocs and not any(f"pid={p}," in ss for p in vprocs), len(vprocs))
            ck("status reports omar as being viewed", call(sock, {"op": "status", "person": "omar"}).get("viewing"))
            bad = RFB(sock, "nobody-running")
            ck("a person with no running browser gets no view", bad.reply.get("ok") is False, bad.reply)
            bad.close()

            print("file isolation (mandatory):")
            dana_profile = SCR / "profiles" / "dana"
            ck("dana's profile is on disk with her cookie store", (dana_profile / "Default").is_dir())
            address_bar(v, f"file://{dana_profile}/Default/")
            r = call(sock, {"op": "read", "person": "omar"})
            text = (r.get("snapshot") or "") + (r.get("url") or "")
            ck("omar typing file:///<dana's profile> sees no listing", "Cookies" not in text and
               "Preferences" not in text and "Index of" not in text, text[:120])
            address_bar(v, "file:///etc/hostname")
            hostname = pathlib.Path("/etc/hostname").read_text().strip()
            r = call(sock, {"op": "read", "person": "omar"})
            text = (r.get("snapshot") or "") + (r.get("url") or "")
            ck("omar typing file:///etc/hostname does not see it", hostname not in (r.get("snapshot") or ""),
               text[:120])
            address_bar(v, "chrome://settings/")
            r = call(sock, {"op": "read", "person": "omar"})
            ck("chrome://settings is not shown either", "Autofill" not in (r.get("snapshot") or ""),
               (r.get("snapshot") or "")[:80])
            st = call(sock, {"op": "status", "person": "omar"})
            ck("omar's tab never commits to a non-web address", (st.get("url") or "").startswith(("http", "about:")),
               st.get("url"))
            ck("no worker log line carries dana's profile path", str(dana_profile) not in (SCR / "w.log").read_text())
            r = call(sock, {"op": "open", "person": "omar", "url": f"{site}/get"})
            ck("presence twin: omar's browser still opens the web", r.get("ok") is True
               and "cookie is" in (r.get("snapshot") or ""))
            r = call(sock, {"op": "open", "person": "omar", "url": f"{site}/picker"})
            snap = r.get("snapshot") or ""
            ref = next((ln.split("]")[0].strip(" [") for ln in snap.splitlines() if "Upload a file" in ln), None)
            before = len([p for p in descendants(wk.pid)])
            if ref:
                call(sock, {"op": "click", "person": "omar", "ref": ref})
            time.sleep(1.5)
            st = call(sock, {"op": "status", "person": "omar"})
            ck("a file picker does not open (policy)", ref is not None and st.get("ok")
               and not any(comm(p) in ("zenity", "kdialog", "xdg-desktop-portal") for p in descendants(wk.pid)),
               f"ref={ref} procs {before}")
            v.close()
            call(sock, {"op": "view_stop", "person": "omar"})
            time.sleep(1)
            ck("handing back stops omar's x11vnc", not [p for p in descendants(wk.pid) if comm(p) == "x11vnc"])
            again = RFB(sock, "omar")
            ck("presence twin: a new view starts it again", again.reply.get("ok") is True)
            again.close()
            call(sock, {"op": "view_stop", "person": "omar"})

        for who in ("dana", "omar"):
            call(sock, {"op": "close", "person": who})
        time.sleep(1)
        left = [p for p in descendants(wk.pid) if comm(p) in ("Xvfb", "x11vnc", "chrome")]
        ck("closing leaves no Xvfb, x11vnc or Chromium behind", not left, left)

        print("no quiet headless fallback:")
        miss, msock = worker("m", JARVIS_XVFB_BIN="/nonexistent/Xvfb")
        r = call(msock, {"op": "open", "person": "dana", "url": f"{site}/get"})
        ck("with Xvfb missing the launch fails and says why", r.get("ok") is False
           and str(r.get("error", "")).startswith("headed_unavailable"), r.get("error"))
        ck("   and no browser started", not [p for p in descendants(miss.pid) if comm(p) == "chrome"])
        ck("   and it is logged", '"op": "headed_unavailable"' in (SCR / "m.log").read_text())
        miss.terminate()
        miss.wait(10)
    finally:
        wk.terminate()
        eg.terminate()
        wk.wait(20)
        eg.wait(10)
        srv.shutdown()
    print(f"{sum(ok)}/{len(ok)} passed")
    return 0 if all(ok) else 1


def canary(sock, site, vnc) -> int:
    if CANARY:
        print("canary: no policy and no guard; the isolation check MUST now fail:")
    else:
        print("the guard alone (no managed policy): the backstop must still hold:")
    call(sock, {"op": "open", "person": "omar", "url": f"{site}/get"})
    if not vnc:
        print("  x11vnc missing: this check cannot run")
        return 1
    v = RFB(sock, "omar")
    v.handshake()
    address_bar(v, "file:///etc/hostname")
    hostname = pathlib.Path("/etc/hostname").read_text().strip()
    r = call(sock, {"op": "read", "person": "omar"})
    ck("omar typing file:///etc/hostname does not see it", hostname not in (r.get("snapshot") or ""),
       (r.get("snapshot") or "")[:80])
    if GUARD_ONLY:
        st = call(sock, {"op": "status", "person": "omar"})
        ck("the guard sent the tab back to about:blank and counted it",
           st.get("url") == "about:blank" and st.get("guarded", 0) >= 1, f"{st.get('url')} {st.get('guarded')}")
        wlog = (SCR / "w.log").read_text()
        ck("and logged it, scheme only", '"op": "nonweb_blocked"' in wlog and '"scheme": "file"' in wlog
           and "/etc/hostname" not in wlog)
    v.close()
    call(sock, {"op": "close", "person": "omar"})
    if CANARY:
        print(f"{sum(ok)}/{len(ok)} passed (canary: the check above MUST fail)")
        return 0 if not all(ok) else 1
    print(f"{sum(ok)}/{len(ok)} passed")
    return 0 if all(ok) else 1


def main() -> int:
    if os.environ.get("_VERIFY_HEADED_NS") == "1":
        return in_namespace()
    if os.geteuid() != 0:
        sys.exit("REFUSED: run as root (needs a private mount namespace)")
    before = sorted(str(p) for p in HOST_POLICY_DIR.rglob("*")) if HOST_POLICY_DIR.exists() else None
    rc = subprocess.run(["unshare", "-m", "--propagation", "private", sys.executable, *sys.argv],
                        env={**os.environ, "_VERIFY_HEADED_NS": "1"}).returncode
    after = sorted(str(p) for p in HOST_POLICY_DIR.rglob("*")) if HOST_POLICY_DIR.exists() else None
    print(f"  {'PASS' if before == after else 'FAIL'}  the host's {HOST_POLICY_DIR} is unchanged")
    return rc if before == after else 1


if __name__ == "__main__":
    sys.exit(main())
