"""A stand-in for HAR MLS Matrix on loopback, serving docs/mls/fixtures/.

Made-up pages only. /Matrix/Search is the search form; posting it answers by
the Area field (see RESULTS_BY_AREA) and records every post so a test can
assert which filters were entered. /f/<name> serves any fixture as is;
/f/too_many.html answers 429 like a rate-limited site. /f/real/<name> serves
a sanitized captured page from REAL (docs/mls/FIXTURE-CAPTURE.md). /__log (for the test,
fetched directly, never through the browser) lists every load and post.
"""
import http.server
import json
import pathlib
import threading
from typing import Dict, List
from urllib.parse import parse_qs, urlsplit

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
REAL = FIXTURES / "real"
RESULTS_BY_AREA = {
    "heights": "results_table.html",
    "heights cards": "results_cards.html",
    "nowhere": "no_results.html",
    "height": "ambiguous.html",
    "wall": "login.html",
    "check": "captcha.html",
    "code": "two_factor.html",
    "busy": "too_many.html",
    "odd": "unknown.html",
}


class Site(http.server.BaseHTTPRequestHandler):
    posts: List[Dict[str, List[str]]] = []
    loads: List[str] = []
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def _send(self, name: str, code: int = 200, root: pathlib.Path = FIXTURES) -> None:
        body = (root / name).read_bytes() if name else b"<!doctype html><title>frame</title>"
        self.send_response(429 if name == "too_many.html" else code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlsplit(self.path)
        with self.lock:
            self.loads.append(u.path)
        if u.path == "/Matrix/Search":
            return self._send("search.html")
        if u.path == "/Matrix/Results":
            page = parse_qs(u.query).get("page", ["1"])[0]
            return self._send("results_table_p2.html" if page == "2" else "results_table.html")
        if u.path == "/__log":
            with self.lock:
                body = json.dumps({"loads": self.loads, "posts": self.posts}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if u.path == "/Matrix/Login":
            return self._send("login.html")
        if u.path == "/recaptcha/frame":
            return self._send("")
        if u.path.startswith("/f/real/"):
            name = u.path[8:]
            if "/" not in name and (REAL / name).is_file():
                return self._send(name, root=REAL)
        if u.path.startswith("/f/"):
            name = u.path[3:]
            if (FIXTURES / name).is_file() and "/" not in name:
                return self._send(name)
        self.send_error(404)

    def do_POST(self):
        u = urlsplit(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=False)
        with self.lock:
            self.loads.append(u.path)
            self.posts.append(form)
        if u.path == "/Matrix/Results":
            area = (form.get("area") or [""])[0].strip().lower()
            return self._send(RESULTS_BY_AREA.get(area, "results_table.html"))
        self.send_error(404)


def serve(ip: str):
    srv = http.server.ThreadingHTTPServer((ip, 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://{ip}:{srv.server_address[1]}"
