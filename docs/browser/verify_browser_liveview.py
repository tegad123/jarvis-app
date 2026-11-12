#!/usr/bin/env python3
"""The live view in the app, in a real browser (step 8 of
docs/plans/browser-har-comps.md).

Two scratch app instances on loopback over the verify sandbox's data
(docs/tasks/tasks_sandbox.sh, started first): one with a headed worker, one
with a headless worker. A Playwright browser signs omar in with his PIN
(loopback), opens the chat and presses Take over:
- headed: the big card shows the page live (a noVNC canvas the size of the
  headed window); typing into it reaches omar's page (the page's title, read
  back through the app); "I'm done" ends the live view and the worker stops
  x11vnc; a screenshot is saved;
- headless: no live view, the still picture as before; a screenshot is saved.
Runs in a private mount namespace (the managed policy at Chromium's
compiled-in path); the host's /etc is not written. Never :8700 or :8710.
"""
import json
import os
import pathlib
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import http.server
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "app"))
import browser_launch as bl  # noqa: E402
import verify_browser_headed as h  # noqa: E402

SB = pathlib.Path(os.environ.get("TASKS_SANDBOX", "/tmp/claude-0/-root/tasks-sandbox"))
SCR = pathlib.Path("/tmp/verify-browser-liveview")
SHOTS = HERE / "shots"
HEADLESS = "/root/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell"
PINS = json.loads(pathlib.Path("/opt/jarvis-verify-fixture/SEEDED_PINS.json").read_text())

UI = r'''
import { chromium } from '/usr/local/lib/hermes-agent/node_modules/playwright/index.mjs'
const [A, B, pin, shotLive, shotStill] = process.argv.slice(2)
const out = {}
const browser = await chromium.launch()
async function signIn(base) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } })
  const page = await ctx.newPage()
  await page.goto(base)
  out['login_' + base.slice(-4)] = (await page.request.post(base + '/api/login', { data: { person_key: 'omar', pin } })).status()
  // Earlier suites leave omar's browser asks pending in the sandbox; with an ask showing, the card offers no Take over.
  const ap = await (await page.request.get(base + '/api/approvals')).json()
  for (const a of ap.approvals || []) {
    if (a.status === 'pending' && String(a.payload?.type ?? '').startsWith('browser') && a.can_approve !== false)
      await page.request.post(`${base}/api/approvals/${encodeURIComponent(a.id)}`, { data: { decision: 'reject' } })
  }
  await page.goto(base, { waitUntil: 'networkidle' })
  return page
}
const status = async (page) => (await page.request.get(page.url().replace(/\/[^/]*$/, '') + '/api/browser/status')).json()
let p
try {
  p = await signIn(A)
  const card = p.getByRole('region', { name: 'Jarvis’s browser' })
  await card.getByRole('button', { name: 'Take over' }).click({ timeout: 30000 })
  const live = p.getByTestId('browser-live')
  await live.locator('canvas').waitFor({ timeout: 20000 })
  await p.waitForTimeout(3000)
  out.canvas = await live.locator('canvas').evaluate((c) => ({ w: c.width, h: c.height }))
  out.headed_status = await status(p)
  const box = await live.boundingBox()
  await p.mouse.click(box.x + box.width / 2, box.y + box.height * 0.6)
  await p.keyboard.type('hello', { delay: 80 })
  await p.waitForTimeout(1500)
  out.title_after_typing = (await status(p)).title
  await card.screenshot({ path: shotLive })
  await card.getByRole('button', { name: 'I’m done' }).click()
  await live.waitFor({ state: 'detached', timeout: 10000 }).then(() => (out.live_gone = true), () => (out.live_gone = false))
  out.img_after_done = await card.locator('img').count()

  const q = await signIn(B)
  const card2 = q.getByRole('region', { name: 'Jarvis’s browser' })
  await card2.getByRole('button', { name: 'Take over' }).click({ timeout: 30000 })
  await card2.getByRole('button', { name: 'I’m done' }).waitFor({ timeout: 10000 })
  await q.waitForTimeout(3000)
  out.headless_status = await status(q)
  out.headless_live = await q.getByTestId('browser-live').count()
  out.headless_img = await card2.locator('img').count()
  out.headless_typing_box = await card2.getByLabel('Type into the page').count()
  await card2.screenshot({ path: shotStill })
  await card2.getByRole('button', { name: 'I’m done' }).click()
} catch (e) {
  out.error = String(e).slice(0, 400)
  if (p) await p.screenshot({ path: shotLive.replace('.png', '-failed.png') }).catch(() => {})
} finally {
  await browser.close()
}
console.log('RESULT ' + JSON.stringify(out))
'''


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def counter(name: str) -> int:
    c = sqlite3.connect(f"file:{SB / 'threads.db'}?mode=ro", uri=True)
    row = c.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()
    return row[0] if row else 0


def main_ns() -> int:
    subprocess.run(["mount", "-t", "tmpfs", "-o", "mode=755", "tmpfs", "/etc/opt"], check=True)
    managed = pathlib.Path("/etc/opt/chrome_for_testing/policies/managed")
    managed.mkdir(parents=True)
    (managed / "jarvis.json").write_text(json.dumps(bl.POLICY))
    vnc = h.x11vnc_bin()
    if not vnc:
        print("x11vnc missing: cannot prove the live view")
        return 1
    subprocess.run(["rm", "-rf", str(SCR)], check=True)
    SCR.mkdir(parents=True)
    SHOTS.mkdir(exist_ok=True)
    ip = f"127.{secrets.randbelow(250) + 3}.0.12"
    srv = http.server.ThreadingHTTPServer((ip, 0), h.Site)
    sport = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    site = f"http://{ip}:{sport}"
    base = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_", "APP_", "ANTHROPIC", "DISPLAY"))}
    procs = [subprocess.Popen([sys.executable, "-I", str(ROOT / "app" / "browser_egress.py")],
                              env={**base, "JARVIS_EGRESS_LISTEN": str(SCR / "egress.sock"),
                                   "JARVIS_EGRESS_TEST_ALLOW": f"{ip}:{sport}"},
                              stdout=open(SCR / "egress.log", "w"), stderr=subprocess.STDOUT)]
    h.wait_for(SCR / "egress.sock")
    for name, mode, binary in (("w", "headed", h.FULL), ("hl", "headless", HEADLESS)):
        procs.append(subprocess.Popen(
            [sys.executable, "-I", str(ROOT / "app" / "browser_worker.py")],
            env={**base, "JARVIS_BROWSER_BIN": binary, "JARVIS_BROWSER_PROFILE": str(SCR / f"profiles-{name}"),
                 "JARVIS_EGRESS_SOCKET": str(SCR / "egress.sock"), "JARVIS_BROWSER_LISTEN": str(SCR / f"{name}.sock"),
                 "JARVIS_BROWSER_TEST": "1", "JARVIS_BROWSER_MAX_OPEN": "3", "JARVIS_BROWSER_MODE": mode,
                 "JARVIS_X11VNC_BIN": vnc, "JARVIS_BROWSER_POLICY": str(managed / "jarvis.json")},
            stdout=open(SCR / f"{name}.log", "w"), stderr=subprocess.STDOUT))
        h.wait_for(SCR / f"{name}.sock")
    ports = {"w": free_port(), "hl": free_port()}
    for name, port in ports.items():
        if port in (8700, 8710):
            sys.exit("REFUSING: live or testco port")
        env = dict(base)
        env.update({
            "APP_GOOGLE_OAUTH_MOCK": "1", "APP_GOOGLE_TOKEN_KEY": (SB / "token_key").read_text().strip(),
            "APP_PUBLIC_ORIGIN": f"http://127.0.0.1:{port}", "APP_COOKIE_SECURE": "0",
            "HERMES_HOME": str(SB / "hermes"), "JARVIS_APP_ROOT": str(ROOT),
            "JARVIS_SKILLS_ROOT": "/opt/jarvis-verify-fixture/skills/openclaw-imports",
            "JARVIS_RUNTIME_CONFIG": str(SB / "runtime.json"),
            "JARVIS_IDENTITY_CONFIG": "/opt/jarvis-verify-fixture/skills/openclaw-imports/_shared/identity.json",
            "JARVIS_ACCOUNTS_DB": str(SB / "accounts.db"), "JARVIS_THREADS_DB": str(SB / "threads.db"),
            "JARVIS_TASKS_DB": str(SB / "tasks.db"), "JARVIS_LEADS_DB": str(SB / "leads.db"),
            "JARVIS_TEAM_FILE": str(SB / "hermes/state/team.json"), "JARVIS_TASKS_SOURCE": "store",
            "JARVIS_ACTIVITY_FEED": str(SB / "feed.jsonl"), "JARVIS_PG_DSN": (SB / "pg_dsn").read_text().strip(),
            "JARVIS_ALLOW_WRITES": "1", "JARVIS_SENTINEL_MODE": "enforce", "JARVIS_SWITCH_BROKER": "on",
            "JARVIS_SWITCH_SENTINEL": "on", "JARVIS_BROWSER": "on",
            "JARVIS_BROWSER_SOCKET": str(SCR / f"{name}.sock"), "APP_PORT": str(port), "APP_HOST": "127.0.0.1"})
        procs.append(subprocess.Popen([sys.executable, "-m", "app.main"], env=env, cwd=str(ROOT),
                                      stdout=open(SCR / f"app-{name}.log", "w"), stderr=subprocess.STDOUT))
    o = {}
    try:
        for port in ports.values():
            for _ in range(60):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2)
                    break
                except OSError:
                    time.sleep(0.5)
        h.call(SCR / "w.sock", {"op": "open", "person": "omar", "url": f"{site}/type"})
        h.call(SCR / "hl.sock", {"op": "open", "person": "omar", "url": f"{site}/type"})
        opened0 = counter("browser_view_opened")
        ui = SCR / "ui.mjs"
        ui.write_text(UI)
        pin = PINS["omar"]["pin"] if isinstance(PINS["omar"], dict) else PINS["omar"]
        p = subprocess.run(["node", str(ui), f"http://127.0.0.1:{ports['w']}", f"http://127.0.0.1:{ports['hl']}",
                            pin, str(SHOTS / "live-view.png"), str(SHOTS / "live-view-headless.png")],
                           capture_output=True, text=True, timeout=240)
        line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
        o = json.loads(line[-1][7:]) if line else {"error": p.stderr[-1500:]}
        o["opened_delta"] = counter("browser_view_opened") - opened0
        time.sleep(1)
        o["after"] = h.call(SCR / "w.sock", {"op": "status", "person": "omar"})
        wlog = (SCR / "w.log").read_text()
    finally:
        for pr in procs:
            pr.terminate()
        for pr in procs:
            try:
                pr.wait(20)
            except subprocess.TimeoutExpired:
                pr.kill()
        srv.shutdown()

    if o.get("error"):
        print("UI run failed:", o["error"])
    print("headed tenant, Take over:")
    h.ck("omar signs in (PIN, loopback) on both instances", o.get(f"login_{str(ports['w'])[-4:]}") == 200
         and o.get(f"login_{str(ports['hl'])[-4:]}") == 200)
    h.ck("status says the live view is available", (o.get("headed_status") or {}).get("live_view") is True)
    h.ck("the big card shows the page live, a canvas the headed window's size",
         o.get("canvas") == {"w": bl.HEADED_SIZE[0], "h": bl.HEADED_SIZE[1]}, o.get("canvas"))
    h.ck("   the app opened exactly one view (+1)", o.get("opened_delta") == 1, o.get("opened_delta"))
    h.ck("typing into the live view reaches omar's page", o.get("title_after_typing") == "typed:hello",
         o.get("title_after_typing"))
    h.ck("\"I'm done\" ends the live view and the still comes back", o.get("live_gone") is True
         and o.get("img_after_done") == 1)
    h.ck("   and the worker stopped x11vnc", (o.get("after") or {}).get("viewing") is False
         and '"op": "view_stopped", "person": "omar"' in wlog)
    print("headless tenant, Take over:")
    h.ck("status says no live view", (o.get("headless_status") or {}).get("live_view") is False)
    h.ck("no live view; the still picture and the typing box, as before", o.get("headless_live") == 0
         and o.get("headless_img") == 1 and o.get("headless_typing_box") == 1)
    h.ck("screenshots saved", (SHOTS / "live-view.png").exists() and (SHOTS / "live-view-headless.png").exists())
    print(f"{sum(h.ok)}/{len(h.ok)} passed")
    return 0 if all(h.ok) else 1


def main() -> int:
    if os.environ.get("_VERIFY_LIVEVIEW_NS") == "1":
        return main_ns()
    if os.geteuid() != 0:
        sys.exit("REFUSED: run as root (needs a private mount namespace)")
    return subprocess.run(["unshare", "-m", "--propagation", "private", sys.executable, *sys.argv],
                          env={**os.environ, "_VERIFY_LIVEVIEW_NS": "1"}).returncode


if __name__ == "__main__":
    sys.exit(main())
