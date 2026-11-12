#!/usr/bin/env python3
"""The browser worker's start-up and launch arguments, without a tenant.

Proves:
- the worker starts under /usr/bin/python3 -I from a directory laid out the
  way provision_tenant.install_browser_code lays it out (BROWSER_CODE_FILES),
  and reaches `ready`; canary: with browser_launch.py left out of the copy it
  does not;
- the headless argument list is exactly the one the worker used before this
  build (a frozen copy below);
- the headed list leaves out --headless, sets no user agent, clears the
  automation marker, and opens a 1440x900 window;
- the managed policy check refuses a missing, unreadable or weakened policy
  (presence twin: the shipped policy passes);
- the profile preferences turn on "continue where you left off" and turn
  off password saving, and keep what Chromium already wrote;
- the browser unit renders the same for a headless tenant as before, and a
  headed tenant gets the mode, the full binary and the policy check.

Touches nothing outside a temp directory. Never :8700 or :8710.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))
import browser_launch as bl  # noqa: E402
import provision_tenant as pt  # noqa: E402

ok = []


def ck(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" ({extra})" if extra else ""))


# The worker's argument list before this build, copied from browser_worker.py
# at platform e017f36. Headless tenants must see no change at all.
def old_headless(profile, port):
    return [f"--user-data-dir={profile}", "--headless", "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-sync",
            "--disable-background-networking", "--disable-component-update", "--disable-default-apps",
            "--disable-extensions", "--disable-quic", "--mute-audio", "--hide-scrollbars",
            "--disable-features=IsolateOrigins,site-per-process,MediaRouter",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            f"--proxy-server=http://127.0.0.1:{port}", "--proxy-bypass-list=<-loopback>",
            "--window-size=1280,800", "--remote-debugging-pipe", "about:blank"]


def start_worker(code_dir: pathlib.Path, scratch: pathlib.Path):
    env = {"PATH": "/usr/bin:/bin", "JARVIS_BROWSER_BIN": "/bin/true", "JARVIS_BROWSER_PROFILE": str(scratch / "p"),
           "JARVIS_EGRESS_SOCKET": str(scratch / "egress.sock"), "JARVIS_BROWSER_LISTEN": str(scratch / "w.sock"),
           "JARVIS_BROWSER_TEST": "1"}
    p = subprocess.Popen(["/usr/bin/python3", "-I", str(code_dir / "browser_worker.py")], env=env, cwd="/",
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    end, out = time.time() + 10, ""
    while time.time() < end and p.poll() is None and '"op": "ready"' not in out:
        time.sleep(0.2)
        if (scratch / "w.sock").exists():
            out = "ready-socket"
            break
    p.terminate()
    try:
        rest = p.communicate(timeout=10)[0]
    except subprocess.TimeoutExpired:
        p.kill()
        rest = p.communicate()[0]
    return out + rest


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="verify-browser-launch-"))
    try:
        print("the worker starts under python3 -I from its copied files:")
        full = tmp / "code"
        full.mkdir()
        for f in pt.BROWSER_CODE_FILES:
            shutil.copyfile(ROOT / "app" / f, full / f)
        out = start_worker(full, tmp)
        ck("with every file in BROWSER_CODE_FILES it reaches ready", '"op": "ready"' in out, out[-160:])
        ck("browser_launch.py is one of the copied files", "browser_launch.py" in pt.BROWSER_CODE_FILES)
        short = tmp / "short"
        short.mkdir()
        for f in pt.BROWSER_CODE_FILES:
            if f != "browser_launch.py":
                shutil.copyfile(ROOT / "app" / f, short / f)
        (tmp / "w.sock").unlink(missing_ok=True)
        out = start_worker(short, tmp)
        ck("canary: without browser_launch.py it does not start", '"op": "ready"' not in out
           and "ModuleNotFoundError" in out, out.strip().splitlines()[-1][:120] if out.strip() else "")

        print("launch arguments:")
        prof = "/var/lib/jarvis-browser/t/people/dana"
        ck("headless is byte-for-byte the old list", bl.chrome_args(prof, 4242, "headless") == old_headless(prof, 4242))
        h = bl.chrome_args(prof, 4242, "headed")
        ck("headed has no --headless", "--headless" not in h)
        ck("headed sets no user agent", not any(a.startswith("--user-agent") for a in h))
        ck("headed clears the automation marker", "--disable-blink-features=AutomationControlled" in h)
        ck("headed opens 1440x900 at the corner", "--window-size=1440,900" in h and "--window-position=0,0" in h)
        ck("headed keeps the pipe, the proxy and no sandbox flag change",
           "--remote-debugging-pipe" in h and "--proxy-server=http://127.0.0.1:4242" in h and "--no-sandbox" in h)
        try:
            bl.chrome_args(prof, 1, "fast")
            ck("an unknown mode is refused", False)
        except ValueError:
            ck("an unknown mode is refused", True)
        ck("the headless shell cannot be headed; the full build can",
           not bl.headed_binary("/opt/jarvis/browser/chrome-headless-shell-1234/chrome-headless-shell")
           and bl.headed_binary("/opt/jarvis/browser/chrome-1234/chrome"))
        x = bl.xvfb_args("/usr/bin/Xvfb", 7)
        ck("Xvfb listens on neither TCP nor an abstract socket",
           x[x.index("-nolisten") + 1] == "tcp" and "local" in x and x[1] == ":7")
        ck("a display already in use is never handed out", bl.free_display({10, 11}) not in (10, 11))
        v = bl.x11vnc_args("/usr/bin/x11vnc", ":3", "/tmp/s/dana.sock")
        ck("x11vnc: unix socket only, nothing sent to the viewer's clipboard",
           v[v.index("-rfbport") + 1] == "0" and v[v.index("-rfbportv6") + 1] == "-1" and "-noipv6" in v
           and v[v.index("-seldir") + 1] == "recv" and "-nocmds" in v and "-permitfiletransfer" not in v)

        print("managed policy:")
        pol = tmp / "policy.json"
        ck("a missing policy is a problem", "missing" in bl.policy_problem(str(pol)))
        pol.write_text("{not json")
        ck("an unreadable policy is a problem", bl.policy_problem(str(pol)) != "")
        weak = dict(bl.POLICY, URLBlocklist=["chrome://*"])
        pol.write_text(json.dumps(weak))
        ck("a policy that allows file:// is a problem", "URLBlocklist" in bl.policy_problem(str(pol)))
        pol.write_text(json.dumps(dict(bl.POLICY, AllowFileSelectionDialogs=True)))
        ck("a policy that allows file pickers is a problem", "AllowFileSelectionDialogs" in bl.policy_problem(str(pol)))
        pol.write_text(json.dumps(bl.POLICY))
        ck("presence twin: the shipped policy passes", bl.policy_problem(str(pol)) == "")
        ck("the policy blocks file:// and allows only about:blank besides the web",
           "file://*" in bl.POLICY["URLBlocklist"] and bl.POLICY["URLAllowlist"] == ["about:blank"])

        print("profile preferences:")
        p = tmp / "people" / "dana"
        bl.prepare_profile(str(p))
        prefs = json.loads((p / "Default" / "Preferences").read_text())
        ck("a new profile continues where it left off", prefs["session"]["restore_on_startup"] == 1)
        ck("and saves no password", prefs["credentials_enable_service"] is False
           and prefs["profile"]["password_manager_enabled"] is False)
        prefs["browser"] = {"window_placement": {"left": 3}}
        prefs["session"]["restore_on_startup"] = 5
        (p / "Default" / "Preferences").write_text(json.dumps(prefs))
        bl.prepare_profile(str(p))
        again = json.loads((p / "Default" / "Preferences").read_text())
        ck("an existing profile is set back, and keeps Chromium's own settings",
           again["session"]["restore_on_startup"] == 1 and again["browser"]["window_placement"]["left"] == 3)

        print("the browser unit:")
        tpl = (ROOT / "scripts" / "templates" / "browser.service").read_text()
        base = {"SLUG": "t", "DISPLAY_NAME": "T", "WEB_USER": "jarvis-t-web", "BROWSER_CODE": "/opt/jarvis/browser-code/t",
                "BROWSER_MAX_OPEN": "2", "BROWSER_MEMORY_MAX": "1G"}
        headless = pt.browser_unit_vars({"browser": "enabled"})
        headed = pt.browser_unit_vars({"browser": "enabled", "browser_mode": "headed"})
        r1 = pt.fill(tpl, {**base, **headless})
        r2 = pt.fill(tpl, {**base, **headed})
        old = (ROOT / "docs" / "browser" / "browser.service.e017f36").read_text()
        ck("headless renders exactly as before plus one mode line",
           r1.replace("Environment=JARVIS_BROWSER_MODE=headless\n", "") == pt.fill(old, {**base, **headless}))
        ck("headless renders the headless shell and mode", f"JARVIS_BROWSER_BIN={pt.BROWSER_BIN}" in r1
           and "JARVIS_BROWSER_MODE=headless" in r1)
        ck("headed renders the full binary, the mode and Xvfb", f"JARVIS_BROWSER_BIN={pt.BROWSER_HEADED_BIN}" in r2
           and "JARVIS_BROWSER_MODE=headed" in r2 and "JARVIS_XVFB_BIN=/usr/bin/Xvfb" in r2
           and "JARVIS_X11VNC_BIN=/usr/bin/x11vnc" in r2)
        ck("no placeholder is left unfilled", "{{" not in r1 and "{{" not in r2)
        try:
            pt.browser_mode({"browser": "enabled", "browser_mode": "visible"})
            ck("an invalid browser_mode is refused", False)
        except pt.Refuse:
            ck("an invalid browser_mode is refused", True)
        except SystemExit:
            ck("an invalid browser_mode is refused", True)
        ck("absent browser_mode means headless", pt.browser_mode({"browser": "enabled"}) == "headless")
        print("the runtime installer (into a temp directory, never /opt or /etc):")
        import install_browser_runtime as ibr  # noqa: PLC0415
        src = tmp / "src"
        src.mkdir()
        (src / "chrome").write_text("#!/bin/sh\necho 'Chromium 151 test'\n")
        (src / "chrome").chmod(0o777)
        ibr.check = lambda runtime, binary: None
        rt = tmp / "opt" / "chrome-1234"
        ibr.install(src, rt, rt / "chrome")
        mode = (rt / "chrome").stat().st_mode & 0o777
        ck("the full build is copied root-owned and not writable by others", (rt / "chrome").stat().st_uid == 0
           and not mode & 0o022, oct(mode))
        pf = tmp / "etc" / "managed" / "jarvis.json"
        ibr.install_policy(pf)
        ck("the policy is written root-owned 0644 and passes the check", oct(pf.stat().st_mode & 0o777) == "0o644"
           and pf.stat().st_uid == 0 and bl.policy_problem(str(pf)) == "")
        try:
            ibr.install(tmp / "nowhere", tmp / "opt" / "x", tmp / "opt" / "x" / "chrome")
            ck("with no Chromium to copy it refuses and downloads nothing", False)
        except SystemExit as exc:
            ck("with no Chromium to copy it refuses and downloads nothing", "nothing is downloaded" in str(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{sum(ok)}/{len(ok)} passed")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
