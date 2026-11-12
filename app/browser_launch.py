"""How the tenant browser is launched.

Headed mode is a normal desktop Chromium on a virtual display (Xvfb), with a
persistent per-person profile. The headless shell build cannot show a window;
callers keep it headless and say so. Nothing here hides automation beyond
leaving out the headless flag and sending a normal desktop user agent.
"""
from __future__ import annotations

import os
import re

WIDTH, HEIGHT = 1440, 900
# A current desktop Chrome on Linux. It does not say Headless.
DESKTOP_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
PERSON = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def supports_headed(binary: str) -> bool:
    """chrome-headless-shell has no window. A full Chromium binary does."""
    return "headless" not in os.path.basename(binary or "").lower()


def display_for(person: str) -> str:
    """A stable X display for this person, :200 through :399. No TCP listen."""
    if not PERSON.fullmatch(person or ""):
        raise ValueError("person")
    n = 200 + (sum(ord(c) for c in person) % 200)
    return f":{n}"


def profile_dir(root: str, person: str) -> str:
    """Cookies and signed-in sessions live here and are not deleted on close."""
    if not PERSON.fullmatch(person or ""):
        raise ValueError("person")
    return os.path.join(root, person)


def chrome_args(profile: str, proxy_port: int, *, headed: bool, binary: str) -> list:
    """Argument list after the binary. Headed omits --headless."""
    use_headed = bool(headed) and supports_headed(binary)
    args = [
        f"--user-data-dir={profile}",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-quic",
        "--mute-audio",
        "--hide-scrollbars",
        "--disable-features=IsolateOrigins,site-per-process,MediaRouter",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        f"--user-agent={DESKTOP_UA}",
        f"--proxy-server=http://127.0.0.1:{proxy_port}",
        "--proxy-bypass-list=<-loopback>",
        f"--window-size={WIDTH},{HEIGHT}",
        "--window-position=0,0",
        "--remote-debugging-pipe",
    ]
    if use_headed:
        args.append("--use-gl=angle")
    else:
        args.extend(["--headless", "--disable-gpu"])
    args.append("about:blank")
    return args


def headed_requested() -> bool:
    return os.environ.get("JARVIS_BROWSER_HEADED", "").strip() == "1"
