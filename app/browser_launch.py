"""How the tenant browser is started: pure functions, standard library only.

Loaded by app/browser_worker.py, which runs under /usr/bin/python3 -I, so it
is copied next to the worker (scripts/provision_tenant.BROWSER_CODE_FILES).

Two modes. `headless` is the Chromium headless shell, exactly as before.
`headed` is the full Chromium as a normal desktop window on its own virtual
display (Xvfb), so a person can take it over live (x11vnc, only while they
have taken over) and sign in to a site themselves. Headed reports the
binary's own user agent: no override, because a hand-written version that
does not match the binary is itself a tell.
"""
import json
import os
import re

MODES = ("headless", "headed")
HEADLESS_SIZE = (1280, 800)
HEADED_SIZE = (1440, 900)
PERSON = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")

# The managed policy a headed browser must run under. A person in Take over
# has an address bar, and every profile in a tenant belongs to the same unix
# user: without this, one person could open file:// and read another's
# cookies, or upload them through a file picker.
POLICY_FILE = "/etc/opt/chrome_for_testing/policies/managed/jarvis.json"
POLICY = {
    "URLBlocklist": ["file://*", "chrome://*", "chrome-untrusted://*", "devtools://*", "view-source:*",
                     "chrome-extension://*", "chrome-search://*", "filesystem:*", "blob:file://*"],
    "URLAllowlist": ["about:blank"],
    "AllowFileSelectionDialogs": False,
    "DownloadRestrictions": 3,
    "DeveloperToolsAvailability": 2,
    "PasswordManagerEnabled": False,
    "AutofillAddressEnabled": False,
    "AutofillCreditCardEnabled": False,
    "PrintingEnabled": False,
    "BrowserSignin": 0,
    "SyncDisabled": True,
    "BrowserAddPersonEnabled": False,
    "BrowserGuestModeEnabled": False,
    "IncognitoModeAvailability": 1,
    "HomepageLocation": "about:blank",
    "NewTabPageLocation": "about:blank",
    "RestoreOnStartup": 1,
}
POLICY_MUST = ("URLBlocklist", "AllowFileSelectionDialogs", "DownloadRestrictions", "DeveloperToolsAvailability")


def size(mode: str):
    return HEADED_SIZE if mode == "headed" else HEADLESS_SIZE


def profile_dir(root: str, person: str) -> str:
    if not PERSON.fullmatch(person or ""):
        raise ValueError("person")
    return os.path.join(root, person)


def headed_binary(binary: str) -> bool:
    """The headless shell has no window; only the full build can be headed."""
    return bool(binary) and "headless" not in os.path.basename(binary).lower()


def chrome_args(profile: str, proxy_port: int, mode: str) -> list:
    """Everything after the binary. The headless list is the one the worker
    has always used, unchanged."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    w, h = size(mode)
    common = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--no-first-run",
              "--no-default-browser-check", "--disable-sync", "--disable-background-networking",
              "--disable-component-update", "--disable-default-apps", "--disable-extensions", "--disable-quic",
              "--mute-audio"]
    tail = ["--disable-features=IsolateOrigins,site-per-process,MediaRouter",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            f"--proxy-server=http://127.0.0.1:{proxy_port}", "--proxy-bypass-list=<-loopback>",
            f"--window-size={w},{h}"]
    if mode == "headless":
        return [f"--user-data-dir={profile}", "--headless", *common, "--hide-scrollbars", *tail,
                "--remote-debugging-pipe", "about:blank"]
    # Driving Chromium over the DevTools pipe sets navigator.webdriver; the
    # team chose to clear that marker (decision 4, PR for this build).
    return [f"--user-data-dir={profile}", *common, "--disable-blink-features=AutomationControlled", *tail,
            "--window-position=0,0", "--remote-debugging-pipe", "about:blank"]


def xvfb_args(binary: str, display_fd: int, mode: str = "headed") -> list:
    """Xvfb picks a free display itself (-displayfd) and listens on its unix
    socket only: no TCP, and no abstract socket (which a private /tmp would
    not hide)."""
    w, h = size(mode)
    return [binary, "-displayfd", str(display_fd), "-screen", "0", f"{w}x{h}x24", "-nolisten", "tcp",
            "-nolisten", "local", "-noreset"]


def x11vnc_args(binary: str, display: str, sock: str) -> list:
    """One display, served on a unix socket only (no TCP or IPv6 listener),
    no password (only the worker can reach the socket; the app decides who
    may see it), the remote clipboard never sent to the viewer, no remote
    control, no external commands, no file transfer (off unless asked)."""
    return [binary, "-display", display, "-unixsock", sock, "-rfbport", "0", "-rfbportv6", "-1", "-noipv6",
            "-nopw", "-forever", "-nevershared", "-seldir", "recv", "-noremote", "-nocmds", "-norc", "-quiet",
            "-noxdamage", "-nolookup", "-nobell"]


def prepare_profile(profile: str) -> None:
    """Before each headed launch (Chromium is not running for this profile):
    continue where it left off, so session-only sign-in cookies survive the
    browser closing when idle; and never save a password or autofill entry
    in the profile on disk."""
    path = os.path.join(profile, "Default", "Preferences")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        with open(path) as f:
            prefs = json.load(f)
        if not isinstance(prefs, dict):
            prefs = {}
    except (OSError, ValueError):
        prefs = {}
    prefs.setdefault("session", {})["restore_on_startup"] = 1
    prefs["credentials_enable_service"] = False
    prefs.setdefault("profile", {})["password_manager_enabled"] = False
    prefs["autofill"] = {**(prefs.get("autofill") or {}), "profile_enabled": False, "credit_card_enabled": False}
    tmp = path + ".jarvis"
    with open(tmp, "w") as f:
        json.dump(prefs, f)
    os.replace(tmp, path)


def policy_problem(path: str = POLICY_FILE) -> str:
    """Empty when the managed policy is in place and blocks what it must.
    A headed browser does not start otherwise."""
    try:
        with open(path) as f:
            got = json.load(f)
    except (OSError, ValueError) as exc:
        return f"managed policy {path} is missing or unreadable ({type(exc).__name__})"
    for k in POLICY_MUST:
        if got.get(k) != POLICY[k]:
            return f"managed policy {path} does not set {k} as required"
    return ""
