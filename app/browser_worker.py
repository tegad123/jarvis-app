"""The tenant's browser: one headless Chromium driven over a DevTools pipe.

Runs as the tenant's browser user (jarvis-<slug>-web) in `<slug>-browser.service`,
which has no network of its own (PrivateNetwork=yes). The only way out is the
egress proxy's unix socket (app/browser_egress.py): a forwarder on this
namespace's private loopback carries Chromium's proxy traffic to it. Chromium
is driven over --remote-debugging-pipe, never a port, so nothing else on the
host can drive it.

The app is the only client (`<slug>-browser.socket`, owned by the app user,
mode 0600). One JSON request per connection, one JSON line back. Every
request names a person: each person has their own Chromium and profile
(signed-in sessions never cross between people), at most MAX_OPEN at once. The agent
never talks to this process; the app decides every action first
(app/browser.py). Standard library only: this runs under /usr/bin/python3 -I.

Ops: status, open, read, describe, click, type, press, scroll, back,
fill_login, screen, human_click, human_type, human_key, close.
"""
import asyncio
import html
import json
import os
import re
import shutil
import signal
import socket
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, quote_plus, urlsplit

BIN = os.environ.get("JARVIS_BROWSER_BIN", "")
PROFILE = os.environ.get("JARVIS_BROWSER_PROFILE", "")
EGRESS = os.environ.get("JARVIS_EGRESS_SOCKET", "")
IDLE_CLOSE_S = int(os.environ.get("JARVIS_BROWSER_IDLE_S", "600"))
IDLE_EXIT_S = int(os.environ.get("JARVIS_BROWSER_EXIT_S", "1800"))
MAX_OPEN = int(os.environ.get("JARVIS_BROWSER_MAX_OPEN", "2"))
PERSON = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
WIDTH, HEIGHT = 1280, 800
SNAPSHOT_CHARS = 12000
LOAD_TIMEOUT_S = 15
INTERACTIVE = {"button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "menuitem", "tab",
               "switch", "option", "listbox", "slider", "spinbutton", "menuitemcheckbox", "menuitemradio",
               "treeitem", "textField", "TextField"}
TEXTUAL = {"StaticText", "heading", "img", "image", "alert", "status"}
KEYS = {"Enter": (13, "\r"), "Tab": (9, ""), "Escape": (27, ""), "Backspace": (8, ""), "ArrowDown": (40, ""),
        "ArrowUp": (38, ""), "ArrowLeft": (37, ""), "ArrowRight": (39, ""), "PageDown": (34, ""),
        "PageUp": (33, ""), "Home": (36, ""), "End": (35, ""), "Space": (32, " ")}
USERNAME_SELECTOR = ("input[autocomplete~=username], input[type=email], input[name*=user i], "
                     "input[name*=email i], input[id*=user i], input[id*=email i], input[name*=login i], "
                     "input[type=text], input:not([type])")


def log(**kw) -> None:
    print(json.dumps({"ev": "browser", **kw}), flush=True)


def site_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def refused_text(site: str, code: int) -> str:
    """The one sentence the person and the model both get when a page answers 4xx/5xx."""
    if code == 429:
        return (f"{site} is refusing this server right now (HTTP 429, too many requests). The page is empty; "
                f"clicking or retrying won't fix it soon.")
    if code in (401, 403):
        return f"{site} refused access from this server (HTTP {code}). The page is empty or an error page."
    if code == 404:
        return f"{site} says this page doesn't exist (HTTP 404)."
    if code >= 500:
        return f"{site} is having trouble right now (HTTP {code}). Try again later."
    return f"{site} refused the request (HTTP {code})."


class CDPError(RuntimeError):
    pass


class CDP:
    """DevTools protocol over the pipe: NUL-terminated JSON both ways."""

    def __init__(self, rfd: int, wfd: int):
        self.rfd, self.wfd = rfd, wfd
        self.next_id = 0
        self.waiting: Dict[int, asyncio.Future] = {}
        self.listeners: List = []
        self.buf = b""
        self.closed = False

    def start(self) -> None:
        asyncio.get_running_loop().add_reader(self.rfd, self._readable)

    def _readable(self) -> None:
        try:
            chunk = os.read(self.rfd, 1 << 20)
        except OSError:
            chunk = b""
        if not chunk:
            self.closed = True
            asyncio.get_running_loop().remove_reader(self.rfd)
            for f in self.waiting.values():
                if not f.done():
                    f.set_exception(CDPError("browser exited"))
            return
        self.buf += chunk
        while b"\0" in self.buf:
            raw, self.buf = self.buf.split(b"\0", 1)
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.waiting:
                fut = self.waiting.pop(msg["id"])
                if not fut.done():
                    if "error" in msg:
                        fut.set_exception(CDPError(str(msg["error"].get("message"))))
                    else:
                        fut.set_result(msg.get("result") or {})
            else:
                for fn in list(self.listeners):
                    fn(msg)

    async def send(self, method: str, params: Optional[dict] = None, session: Optional[str] = None,
                   timeout: float = 20) -> dict:
        if self.closed:
            raise CDPError("browser exited")
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        fut = asyncio.get_running_loop().create_future()
        self.waiting[self.next_id] = fut
        data = json.dumps(msg).encode() + b"\0"
        while data:
            n = os.write(self.wfd, data)
            data = data[n:]
        return await asyncio.wait_for(fut, timeout)


class Browser:
    def __init__(self, person: str, profile: str, forward_port: int):
        self.person, self.profile = person, profile
        self.lock = asyncio.Lock()
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.cdp: Optional[CDP] = None
        self.session: Optional[str] = None
        self.target: Optional[str] = None
        self.refs: Dict[str, int] = {}
        self.dialogs: List[str] = []
        self.secrets: set = set()
        self.forward_port = forward_port
        self.last_used = time.monotonic()

    # ── lifecycle ──────────────────────────────────────────────────────────
    async def ensure(self) -> None:
        if self.cdp and not self.cdp.closed and self.session:
            return
        await self.close()
        os.makedirs(self.profile, mode=0o700, exist_ok=True)
        to_chrome_r, to_chrome_w = os.pipe()
        from_chrome_r, from_chrome_w = os.pipe()
        args = [f"--user-data-dir={self.profile}", "--headless", "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-sync",
                "--disable-background-networking", "--disable-component-update", "--disable-default-apps",
                "--disable-extensions", "--disable-quic", "--mute-audio", "--hide-scrollbars",
                "--disable-features=IsolateOrigins,site-per-process,MediaRouter",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                f"--proxy-server=http://127.0.0.1:{self.forward_port}", "--proxy-bypass-list=<-loopback>",
                f"--window-size={WIDTH},{HEIGHT}", "--remote-debugging-pipe", "about:blank"]
        # bash, not sh: dash cannot redirect descriptors above 9.
        shell = (f'exec "$0" "$@" 3<&{to_chrome_r} 4>&{from_chrome_w} {to_chrome_r}<&- {from_chrome_w}>&- '
                 f'{to_chrome_w}>&- {from_chrome_r}<&-')
        self.proc = await asyncio.create_subprocess_exec(
            "/bin/bash", "-c", shell, BIN, *args, pass_fds=(to_chrome_r, from_chrome_w, to_chrome_w, from_chrome_r),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True)
        os.close(to_chrome_r)
        os.close(from_chrome_w)
        os.set_blocking(from_chrome_r, False)
        self.cdp = CDP(from_chrome_r, to_chrome_w)
        self.cdp.listeners.append(self._event)
        self.cdp.start()
        await self.cdp.send("Browser.setDownloadBehavior", {"behavior": "deny"})
        await self.cdp.send("Target.setDiscoverTargets", {"discover": True})
        pages = [t for t in (await self.cdp.send("Target.getTargets"))["targetInfos"] if t["type"] == "page"]
        target = pages[0]["targetId"] if pages else \
            (await self.cdp.send("Target.createTarget", {"url": "about:blank"}))["targetId"]
        await self._attach(target)
        log(op="launch", person=self.person, pid=self.proc.pid)

    async def _attach(self, target: str) -> None:
        r = await self.cdp.send("Target.attachToTarget", {"targetId": target, "flatten": True})
        self.session, self.target, self.refs = r["sessionId"], target, {}
        for m in ("Page.enable", "DOM.enable", "Accessibility.enable", "Runtime.enable"):
            await self.cdp.send(m, session=self.session)
        await self.cdp.send("Emulation.setDeviceMetricsOverride",
                            {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1, "mobile": False},
                            session=self.session)

    def _event(self, msg: dict) -> None:
        method, p = msg.get("method"), msg.get("params") or {}
        if method == "Target.targetCreated":
            info = p.get("targetInfo") or {}
            if info.get("type") == "page" and info.get("openerId"):
                asyncio.ensure_future(self._attach(info["targetId"]))
        elif method == "Target.targetDestroyed" and p.get("targetId") == self.target:
            self.session = None
        elif method == "Page.javascriptDialogOpening" and msg.get("sessionId") == self.session:
            kind, text = p.get("type"), str(p.get("message") or "")[:300]
            self.dialogs.append(f"{kind}: {text}")
            asyncio.ensure_future(self.cdp.send("Page.handleJavaScriptDialog",
                                                {"accept": kind == "alert"}, session=self.session))

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            # Browser.close lets Chromium write its cookie store, which keeps
            # the person signed in next time; a signal loses recent sign-ins.
            try:
                if self.cdp and not self.cdp.closed:
                    await self.cdp.send("Browser.close", timeout=5)
                await asyncio.wait_for(self.proc.wait(), 10)
            except (CDPError, asyncio.TimeoutError, OSError):
                log(op="close_forced", person=self.person)
        if self.proc and self.proc.returncode is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                await asyncio.wait_for(self.proc.wait(), 5)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            log(op="closed", person=self.person)
        if self.cdp:
            for fd in (self.cdp.rfd, self.cdp.wfd):
                try:
                    asyncio.get_running_loop().remove_reader(fd)
                except Exception:
                    pass
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.proc, self.cdp, self.session, self.target, self.refs = None, None, None, None, {}

    # ── helpers ────────────────────────────────────────────────────────────
    async def s(self, method: str, params: Optional[dict] = None, timeout: float = 20) -> dict:
        if not self.session:
            await self.ensure()
        return await self.cdp.send(method, params, session=self.session, timeout=timeout)

    async def eval(self, expr: str) -> Any:
        r = await self.s("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return (r.get("result") or {}).get("value")

    async def settle(self, wait_nav: bool = False) -> None:
        await asyncio.sleep(0.4 if wait_nav else 0.15)
        deadline = time.monotonic() + LOAD_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                if await self.eval("document.readyState") == "complete":
                    break
            except (CDPError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(0.25)
        await asyncio.sleep(0.3)

    async def where(self) -> Dict[str, Any]:
        try:
            url = await self.eval("location.href") or ""
            title = await self.eval("document.title") or ""
            code = await self.eval("(performance.getEntriesByType('navigation')[0] || {}).responseStatus || 0")
        except (CDPError, asyncio.TimeoutError):
            url, title, code = "", "", 0
        out: Dict[str, Any] = {"url": str(url), "title": str(title)[:200], "site": site_of(str(url))}
        if isinstance(code, int) and code:
            out["http_status"] = code
            if code >= 400:
                out["site_refused"] = refused_text(out["site"] or "The site", code)
        return out

    def _node(self, ref: str) -> int:
        if ref not in self.refs:
            raise CDPError(f"unknown element {ref}: read the page again")
        return self.refs[ref]

    async def _attrs(self, backend: int) -> Dict[str, str]:
        node = (await self.s("DOM.describeNode", {"backendNodeId": backend}))["node"]
        a = node.get("attributes") or []
        return {"_tag": str(node.get("nodeName") or "").lower(), **{a[i]: a[i + 1] for i in range(0, len(a), 2)}}

    async def _center(self, backend: int):
        try:
            await self.s("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend})
        except CDPError:
            pass
        quads = (await self.s("DOM.getContentQuads", {"backendNodeId": backend})).get("quads") or []
        if not quads:
            raise CDPError("that element is not visible on the page")
        q = quads[0]
        return sum(q[0::2]) / 4, sum(q[1::2]) / 4

    async def _mouse_click(self, x: float, y: float) -> None:
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.s("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left",
                                                      "clickCount": 0 if kind == "mouseMoved" else 1})

    async def _key(self, name: str) -> None:
        code, text = KEYS[name]
        down = {"type": "keyDown", "key": " " if name == "Space" else name, "code": name,
                "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
        if text:
            down["text"] = text
        await self.s("Input.dispatchKeyEvent", down)
        await self.s("Input.dispatchKeyEvent", {**down, "type": "keyUp", "text": ""})

    async def _fill(self, backend: int, text: str) -> None:
        ref = {"backendNodeId": backend}
        await self.s("DOM.focus", ref)
        obj = (await self.s("DOM.resolveNode", ref))["object"]["objectId"]
        await self.s("Runtime.callFunctionOn", {"objectId": obj, "functionDeclaration":
                     "function(){ if (this.select) this.select(); else if (this.isContentEditable) "
                     "document.execCommand('selectAll'); }"})
        await self.s("Input.insertText", {"text": text})

    # ── the page as text ───────────────────────────────────────────────────
    async def snapshot(self) -> str:
        nodes = (await self.s("Accessibility.getFullAXTree", timeout=30)).get("nodes") or []
        by_id = {n["nodeId"]: n for n in nodes}
        root = next((n for n in nodes if not n.get("parentId")), None)
        self.refs = {}
        lines: List[str] = []
        size = 0
        last_text = ""

        def val(n, key):
            return str(((n.get(key) or {}).get("value")) or "").strip()

        async def walk(n, depth):
            nonlocal size, last_text
            if size > SNAPSHOT_CHARS:
                return
            role, name = val(n, "role"), re.sub(r"\s+", " ", val(n, "name"))[:200]
            line = None
            if not n.get("ignored"):
                if role in INTERACTIVE and n.get("backendDOMNodeId"):
                    ref = f"e{len(self.refs) + 1}"
                    self.refs[ref] = n["backendDOMNodeId"]
                    extra = ""
                    if role in ("textbox", "searchbox", "combobox", "textField", "TextField"):
                        attrs = await self._attrs(n["backendDOMNodeId"])
                        if attrs.get("type", "").lower() == "password":
                            extra = " (password field: use the saved login)"
                        else:
                            v = re.sub(r"\s+", " ", val(n, "value"))[:120]
                            extra = f' value="{v}"' if v else ""
                    for p in n.get("properties") or []:
                        if p.get("name") in ("checked", "selected", "expanded", "disabled") and \
                                (p.get("value") or {}).get("value") not in (None, False, "false"):
                            extra += f" [{p['name']}]"
                    line = f"[{ref}] {role} \"{name}\"{extra}"
                elif role == "heading" and name:
                    level = next((str((p.get("value") or {}).get("value")) for p in n.get("properties") or []
                                  if p.get("name") == "level"), "")
                    line = f"{'#' * int(level or 2)} {name}"
                elif role in TEXTUAL and name and name != last_text:
                    line = name if role == "StaticText" else f"({role}) {name}"
            if line:
                lines.append("  " * min(depth, 6) + line)
                size += len(line) + 1
                last_text = name
                if role in INTERACTIVE or role == "heading":
                    return
            for cid in n.get("childIds") or []:
                if cid in by_id:
                    await walk(by_id[cid], depth + (1 if line else 0))

        if root:
            await walk(root, 0)
        text = "\n".join(lines)
        if size > SNAPSHOT_CHARS:
            text += "\n… (page continues; scroll down and read again for more)"
        return text

    async def page(self, with_snapshot: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {"ok": True, **(await self.where())}
        if with_snapshot:
            out["snapshot"] = await self.snapshot()
            if out["snapshot"].startswith("Blocked by Jarvis:"):
                out["blocked"] = out["snapshot"].split(":", 1)[1].strip()[:200]
            elif out.get("site_refused"):
                out["snapshot"] = f"THE SITE REFUSED THIS PAGE: {out['site_refused']}\n\n{out['snapshot']}"
        if out.get("site_refused"):
            log(op="site_refused", person=self.person, site=out.get("site"), status=out.get("http_status"))
        if self.dialogs:
            out["dialogs"], self.dialogs = self.dialogs, []
        return out

    async def check_site(self, expect: Optional[str]) -> None:
        if expect and (await self.where())["site"] != expect:
            raise CDPError("the page changed to a different site; read it again")

    # ── ops ────────────────────────────────────────────────────────────────
    async def op(self, req: Dict[str, Any]) -> Dict[str, Any]:
        op = req.get("op")
        if op == "status":
            running = bool(self.cdp and not self.cdp.closed and self.session)
            return {"ok": True, "running": running, **((await self.where()) if running else {})}
        if op == "close":
            await self.close()
            return {"ok": True, "running": False}
        await self.ensure()
        if op == "open":
            url = str(req.get("url") or "")
            if urlsplit(url).scheme not in ("http", "https"):
                raise CDPError("only http and https addresses can be opened")
            r = await self.s("Page.navigate", {"url": url}, timeout=LOAD_TIMEOUT_S + 5)
            await self.settle(True)
            out = await self.page()
            if r.get("errorText"):
                out["load_error"] = r["errorText"]
            return out
        if op == "read":
            return await self.page()
        if op == "describe":
            backend = self._node(str(req.get("ref")))
            attrs = await self._attrs(backend)
            ax = (await self.s("Accessibility.getPartialAXTree", {"backendNodeId": backend,
                                                                  "fetchRelatives": False})).get("nodes") or [{}]
            name = str(((ax[0].get("name") or {}).get("value")) or "")[:200]
            role = str(((ax[0].get("role") or {}).get("value")) or "")
            return {"ok": True, **(await self.where()), "role": role, "name": name, "tag": attrs["_tag"],
                    "input_type": attrs.get("type", "").lower(),
                    "field": " ".join(attrs.get(k, "") for k in ("name", "id", "autocomplete", "placeholder",
                                                                   "aria-label"))[:300]}
        if op == "click":
            await self.check_site(req.get("expect_site"))
            x, y = await self._center(self._node(str(req.get("ref"))))
            await self._mouse_click(x, y)
            await self.settle(True)
            return await self.page()
        if op == "type":
            await self.check_site(req.get("expect_site"))
            backend = self._node(str(req.get("ref")))
            if (await self._attrs(backend)).get("type", "").lower() == "password":
                raise CDPError("password fields are filled only from the saved login")
            await self._fill(backend=backend, text=str(req.get("text") or ""))
            if req.get("submit"):
                await self._key("Enter")
                await self.settle(True)
            return await self.page()
        if op == "press":
            await self.check_site(req.get("expect_site"))
            key = str(req.get("key") or "")
            if key not in KEYS:
                raise CDPError(f"key must be one of {sorted(KEYS)}")
            await self._key(key)
            await self.settle(key == "Enter")
            return await self.page()
        if op == "scroll":
            dy = -HEIGHT * 0.8 if req.get("direction") == "up" else HEIGHT * 0.8
            await self.s("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": WIDTH / 2, "y": HEIGHT / 2,
                                                      "deltaX": 0, "deltaY": dy})
            await asyncio.sleep(0.5)
            return await self.page()
        if op == "back":
            h = await self.s("Page.getNavigationHistory")
            if h["currentIndex"] > 0:
                await self.s("Page.navigateToHistoryEntry", {"entryId": h["entries"][h["currentIndex"] - 1]["id"]})
                await self.settle(True)
            return await self.page()
        if op == "fill_login":
            return await self.fill_login(req)
        if op == "screen":
            shot = await self.s("Page.captureScreenshot", {"format": "jpeg", "quality": 60})
            return {"ok": True, **(await self.where()), "jpeg": shot["data"], "width": WIDTH, "height": HEIGHT}
        if op == "human_click":
            await self._mouse_click(float(req["x"]), float(req["y"]))
            await self.settle(True)
            return {"ok": True, **(await self.where())}
        if op == "human_type":
            await self.s("Input.insertText", {"text": str(req.get("text") or "")[:2000]})
            return {"ok": True}
        if op == "human_key":
            if req.get("key") not in KEYS:
                raise CDPError("unknown key")
            await self._key(req["key"])
            await self.settle(req["key"] == "Enter")
            return {"ok": True, **(await self.where())}
        raise CDPError(f"unknown op {op!r}")

    async def _visible(self, selector: str) -> Optional[int]:
        """The first rendered match, as a backend node id: every getDocument
        call invalidates the nodeIds handed out before it."""
        root = (await self.s("DOM.getDocument", {"depth": 0, "pierce": True}))["root"]["nodeId"]
        for nid in (await self.s("DOM.querySelectorAll", {"nodeId": root, "selector": selector}))["nodeIds"]:
            try:
                box = await self.s("DOM.getBoxModel", {"nodeId": nid})
            except CDPError:
                continue
            m = box.get("model") or {}
            if m.get("width", 0) > 2 and m.get("height", 0) > 2:
                return (await self.s("DOM.describeNode", {"nodeId": nid}))["node"]["backendNodeId"]
        return None

    async def fill_login(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """The saved login, typed into the page's own form. The values never
        appear in a reply, a log line or a snapshot."""
        await self.check_site(req.get("expect_site"))
        username, password = str(req.get("username") or ""), str(req.get("password") or "")
        steps = []
        pw = await self._visible("input[type=password]")
        user = await self._visible(USERNAME_SELECTOR)
        if pw is None and user is None:
            return {"ok": True, **(await self.page()), "login": "no_login_form"}
        if pw is None:
            await self._fill(backend=user, text=username)
            await self._key("Enter")
            steps.append("username")
            await self.settle(True)
            for _ in range(20):
                pw = await self._visible("input[type=password]")
                if pw is not None:
                    break
                await asyncio.sleep(0.4)
            if pw is None:
                out = await self.page()
                return {**out, "login": "username_only", "steps": steps}
        elif user is not None and username:
            await self._fill(backend=user, text=username)
            steps.append("username")
        if password:
            self.secrets.add(password)
        await self._fill(backend=pw, text=password)
        steps.append("password")
        await self._key("Enter")
        await self.settle(True)
        return {**(await self.page()), "login": "submitted", "steps": steps}

    def redact(self, out: Dict[str, Any]) -> Dict[str, Any]:
        """No reply carries a saved password, in any spelling a page might
        echo it in (a GET form puts it in the address)."""
        forms = set()
        for s in self.secrets:
            forms |= {s, quote(s, safe=""), quote_plus(s), html.escape(s), json.dumps(s)[1:-1]}
        forms = sorted((f for f in forms if len(f) >= 4), key=len, reverse=True)
        if not forms:
            return out
        hit = False

        def scrub(v):
            nonlocal hit
            if isinstance(v, str):
                for f in forms:
                    if f in v:
                        hit = True
                        v = v.replace(f, "[saved password]")
                return v
            if isinstance(v, dict):
                return {k: scrub(x) for k, x in v.items()}
            if isinstance(v, list):
                return [scrub(x) for x in v]
            return v

        out = scrub(out)
        if hit:
            out["redacted"] = True
        return out


# ── the forwarder: this namespace's loopback -> the egress proxy ──────────────

async def _pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await r.read(65536)
            if not data:
                break
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def _forward(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
    try:
        er, ew = await asyncio.open_unix_connection(EGRESS)
    except OSError:
        cw.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        cw.close()
        log(op="egress_unreachable")
        return
    await asyncio.gather(_pipe(cr, ew), _pipe(er, cw))


def _private_namespace() -> bool:
    try:
        return sorted(os.listdir("/sys/class/net")) == ["lo"]
    except OSError:
        return False


async def main() -> None:
    if not BIN or not os.path.isfile(BIN) or not PROFILE or not EGRESS:
        log(op="refused", why="JARVIS_BROWSER_BIN, JARVIS_BROWSER_PROFILE and JARVIS_EGRESS_SOCKET are required")
        sys.exit(2)
    if not _private_namespace() and os.environ.get("JARVIS_BROWSER_TEST") != "1":
        log(op="refused", why="not in a private network namespace (PrivateNetwork=yes)")
        sys.exit(2)
    if os.environ.get("JARVIS_BROWSER_TEST") == "1":
        log(op="test_mode", why="JARVIS_BROWSER_TEST=1: the network namespace is not checked")
    fwd = await asyncio.start_server(_forward, "127.0.0.1", 0)
    port = fwd.sockets[0].getsockname()[1]
    browsers: Dict[str, Browser] = {}
    launch = asyncio.Lock()
    started = time.monotonic()

    async def browser_for(person: str, launching: bool) -> Browser:
        """This person's own browser and profile. At most MAX_OPEN run at once;
        the one idle longest is closed (its profile, and so its sign-ins, stay)."""
        async with launch:
            b = browsers.get(person)
            if b is None:
                b = browsers[person] = Browser(person, os.path.join(PROFILE, person), port)
            running = [x for x in browsers.values() if x.proc and x is not b]
            if launching and not b.proc and len(running) >= MAX_OPEN:
                idle_one = min((x for x in running if not x.lock.locked()), key=lambda x: x.last_used, default=None)
                if idle_one is None:
                    raise CDPError("busy: every browser on this assistant is in use; try again in a minute")
                log(op="evicted", person=idle_one.person, for_person=person)
                await idle_one.close()
            return b

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(r.readline(), 10)
            req = json.loads(line or b"{}")
            person = str(req.get("person") or "")
            if not PERSON.fullmatch(person):
                out = {"ok": False, "error": "every request names the person whose browser it is"}
            else:
                try:
                    b = await browser_for(person, req.get("op") not in ("status", "close"))
                    async with b.lock:
                        b.last_used = time.monotonic()
                        try:
                            out = await asyncio.wait_for(b.op(req), 90)
                        except (CDPError, asyncio.TimeoutError, KeyError, ValueError) as exc:
                            out = {"ok": False, "error": str(exc) or type(exc).__name__}
                        out = b.redact(out)
                        b.last_used = time.monotonic()
                except CDPError as exc:
                    out = {"ok": False, "error": str(exc)}
            log(op=req.get("op"), person=person[:64], ok=out.get("ok"), site=out.get("site"))
            w.write(json.dumps(out).encode() + b"\n")
            await w.drain()
        except Exception as exc:
            log(op="handler_error", error=type(exc).__name__)
        finally:
            w.close()

    fds = int(os.environ.get("LISTEN_FDS", "0") or 0)
    if fds >= 1 and os.environ.get("LISTEN_PID") == str(os.getpid()):
        sock = socket.socket(fileno=3)
        server = await asyncio.start_unix_server(handle, sock=sock)
    else:
        path = os.environ.get("JARVIS_BROWSER_LISTEN", "")
        if not path:
            log(op="refused", why="no socket from systemd and no JARVIS_BROWSER_LISTEN")
            sys.exit(2)
        if os.path.exists(path):
            os.unlink(path)
        server = await asyncio.start_unix_server(handle, path=path)
        os.chmod(path, 0o600)
    log(op="ready", forward_port=port, max_open=MAX_OPEN, chrome=os.path.basename(os.path.dirname(BIN)))
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)

    async def idle() -> None:
        while not stop.is_set():
            await asyncio.sleep(15)
            now = time.monotonic()
            for b in list(browsers.values()):
                if b.proc and now - b.last_used > IDLE_CLOSE_S and not b.lock.locked():
                    await b.close()
            last = max([b.last_used for b in browsers.values()] + [started])
            if now - last > IDLE_EXIT_S and not any(b.lock.locked() for b in browsers.values()):
                log(op="idle_exit")
                stop.set()

    task = asyncio.ensure_future(idle())
    await stop.wait()
    task.cancel()
    for b in browsers.values():
        await b.close()
        shutil.rmtree(os.path.join(b.profile, "Default", "Cache"), ignore_errors=True)
    server.close()
    fwd.close()


if __name__ == "__main__":
    asyncio.run(main())
