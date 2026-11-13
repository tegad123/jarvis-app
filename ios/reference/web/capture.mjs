#!/usr/bin/env node
// Photographs every phone screen of the web app against the stub server.
// No dependencies: it drives Chrome over the DevTools protocol with Node's
// built-in WebSocket (Node 22+).
//
//   node ios/reference/web/capture.mjs [outDir]
//
// CHROME=/path/to/chrome overrides the browser. The viewport is an iPhone's
// 393 x 852 points at 2x, which is the web app's phone layout (below 768px).
import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = fileURLToPath(new URL('.', import.meta.url))
const OUT = resolve(process.argv[2] || join(HERE, 'screens'))
const PORT = 8799
const DEBUG_PORT = 9339
const BASE = `http://127.0.0.1:${PORT}`
const CHROME = process.env.CHROME || 'google-chrome'
const W = 393
const H = 852

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitFor(fn, what, timeout = 15000) {
  const start = Date.now()
  for (;;) {
    try {
      const v = await fn()
      if (v) return v
    } catch {
      /* not yet */
    }
    if (Date.now() - start > timeout) throw new Error(`timed out waiting for ${what}`)
    await sleep(100)
  }
}

const server = spawn(process.execPath, [join(HERE, 'server.mjs'), String(PORT)], { stdio: 'inherit' })
const profile = await mkdtemp(join(tmpdir(), 'jarvis-ref-'))
const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    `--remote-debugging-port=${DEBUG_PORT}`,
    `--user-data-dir=${profile}`,
    '--no-sandbox',
    '--hide-scrollbars',
    '--disable-gpu',
    '--force-color-profile=srgb',
    'about:blank',
  ],
  { stdio: 'ignore' },
)

let failed = false
try {
  await waitFor(async () => (await fetch(`${BASE}/api/auth/methods`)).ok, 'the stub server')
  const target = await waitFor(async () => {
    const list = await (await fetch(`http://127.0.0.1:${DEBUG_PORT}/json`)).json()
    return list.find((t) => t.type === 'page')
  }, 'Chrome')

  const ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((ok, bad) => {
    ws.addEventListener('open', ok, { once: true })
    ws.addEventListener('error', bad, { once: true })
  })
  let seq = 0
  const waiting = new Map()
  ws.addEventListener('message', (m) => {
    const msg = JSON.parse(m.data)
    if (msg.id && waiting.has(msg.id)) {
      const { ok, bad } = waiting.get(msg.id)
      waiting.delete(msg.id)
      msg.error ? bad(new Error(msg.error.message)) : ok(msg.result)
    }
  })
  const send = (method, params = {}) =>
    new Promise((ok, bad) => {
      const id = ++seq
      waiting.set(id, { ok, bad })
      ws.send(JSON.stringify({ id, method, params }))
    })
  const evaluate = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text)
    return r.result.value
  }

  const viewport = (height = H) =>
    send('Emulation.setDeviceMetricsOverride', { width: W, height, deviceScaleFactor: 2, mobile: true })
  await send('Page.enable')
  await send('Runtime.enable')
  await viewport()
  await send('Emulation.setTouchEmulationEnabled', { enabled: true })

  const stub = (state) =>
    fetch(`${BASE}/__stub/set`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state) })

  const open = async (theme = 'dark') => {
    await send('Page.navigate', { url: BASE + '/' })
    await sleep(300)
    await evaluate(`localStorage.setItem('jarvis-mono-theme', ${JSON.stringify(theme)})`)
    await send('Page.navigate', { url: BASE + '/' })
    await waitFor(() => evaluate(`document.readyState === 'complete' && !!document.querySelector('#root > *')`), 'the app to load')
    await evaluate('document.fonts.ready.then(() => true)')
    await sleep(500)
  }

  // Clicks the first visible button, link or tab whose label or text matches.
  const click = async (label, { exact = true } = {}) => {
    const done = await evaluate(`(() => {
      const want = ${JSON.stringify(label)}
      const exact = ${exact}
      const els = [...document.querySelectorAll('button, a, [role="tab"], [role="menuitem"], [role="radio"], label')]
      const text = (e) => (e.getAttribute('aria-label') || e.innerText || '').replace(/\\s+/g, ' ').trim()
      const hit = els.find((e) => e.offsetParent !== null && (exact ? text(e) === want : text(e).includes(want)))
      if (!hit) return false
      hit.click()
      return true
    })()`)
    if (!done) throw new Error(`nothing to click for "${label}"`)
    await sleep(450)
  }

  const type = async (selector, value) => {
    await evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)})
      const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value').set
      set.call(el, ${JSON.stringify(value)})
      el.dispatchEvent(new Event('input', { bubbles: true }))
    })()`)
    await sleep(200)
  }

  await mkdir(OUT, { recursive: true })
  const taken = []
  const shot = async (name) => {
    await sleep(350)
    const { data } = await send('Page.captureScreenshot', { format: 'png' })
    await writeFile(join(OUT, `${name}.png`), Buffer.from(data, 'base64'))
    taken.push(name)
    console.log('  saved', name)
  }
  // The whole of a scrolling screen: grow the viewport to the content's height.
  const tall = async (name) => {
    const height = await evaluate(`(() => {
      let max = ${H}
      for (const el of document.querySelectorAll('main, main *, nav *')) {
        if (el.scrollHeight > el.clientHeight + 4 && getComputedStyle(el).overflowY !== 'visible') {
          max = Math.max(max, ${H} + el.scrollHeight - el.clientHeight)
        }
      }
      return Math.min(max, 6000)
    })()`)
    if (height > H + 8) {
      await viewport(height)
      await shot(`${name}-full`)
      await viewport()
      await sleep(200)
    }
  }
  // The same screen in viewport-sized pieces, scrolled, for reading at full size.
  const pages = async (name, within = 'main') => {
    const find = `(() => {
      const els = [...document.querySelectorAll(${JSON.stringify(within)} + ', ' + ${JSON.stringify(within)} + ' *')]
      return els.find((el) => el.scrollHeight > el.clientHeight + 4 && getComputedStyle(el).overflowY !== 'visible')
    })()`
    const extent = await evaluate(`(() => { const el = ${find}; return el ? [el.scrollHeight, el.clientHeight] : null })()`)
    if (!extent) return
    const [total, view] = extent
    const step = Math.max(200, view - 96)
    let n = 2
    for (let y = step; y < total - 40 && n < 9; y += step, n++) {
      await evaluate(`(() => { ${find}.scrollTop = ${y}; return true })()`)
      await shot(`${name}-p${n}`)
    }
    await evaluate(`(() => { ${find}.scrollTop = 0; return true })()`)
  }
  const back = () => click('Back', { exact: false })

  /* ------------------------------------------------------------- sign in */
  await stub({ signedIn: false, role: 'owner' })
  await open('dark')
  await shot('01-signin')
  await click('Sign in with PIN')
  await shot('02-signin-pin-people')
  await click('Dana Whitfield', { exact: false })
  await type('input[autocomplete="current-password"], input[type="password"], input[inputmode]', '4821-9033-17')
  await shot('03-signin-pin-entry')

  /* ---------------------------------------------------------------- home */
  await stub({ signedIn: true, role: 'owner' })
  await open('dark')
  await shot('04-home')

  await click('New channel')
  await shot('05-new-channel-sheet')
  await click('Close')

  /* ---------------------------------------------------------------- chat */
  await click('Ask Jarvis', { exact: false })
  await shot('06-ask-direct')
  await back()

  await stub({ emptyHistory: true })
  await click('Ask Jarvis', { exact: false })
  await shot('06b-ask-direct-empty')
  await back()
  await stub({ emptyHistory: false })

  await click('128 Alder Ave', { exact: false })
  await shot('07-channel')
  await click('Members of', { exact: false })
  await shot('08-channel-members-sheet')
  await click('Close')
  await back()

  await click('Team', { exact: false })
  await shot('09-channel-empty')
  await back()

  /* ------------------------------------------------------------ the tabs */
  await click('Activity')
  await shot('10-activity')
  await tall('10-activity')
  await back()

  await click('Approvals', { exact: false })
  await shot('11-approvals')
  await tall('11-approvals')
  await back()

  await click('Inbox', { exact: false })
  await shot('12-inbox')
  await back()

  await click('Tasks')
  await shot('13-tasks')
  await tall('13-tasks')
  await click('New task')
  await shot('14-tasks-new')
  await click('Cancel')
  await back()

  await click('Leads')
  await shot('15-leads')
  await tall('15-leads')
  await click('New lead')
  await shot('16-leads-new')
  await tall('16-leads-new')
  await click('Cancel')
  await click('Renee Castillo', { exact: false })
  await shot('17-lead-sheet')
  await pages('17-lead-sheet', '[role="dialog"]')
  await click('Close')
  await back()

  await click('Settings')
  await shot('19-settings')
  await pages('19-settings')
  await tall('19-settings')
  await back()

  /* --------------------------------------------------------------- light */
  await open('light')
  await shot('20-home-light')
  await click('Ask Jarvis', { exact: false })
  await shot('21-ask-direct-light')
  await back()
  await click('Leads')
  await shot('22-leads-light')
  await back()
  await click('Settings')
  await shot('22b-settings-light')

  /* -------------------------------------------------------------- member */
  await stub({ signedIn: true, role: 'member' })
  await open('dark')
  await shot('23-home-member')

  console.log(`\n${taken.length} screenshots in ${OUT}`)
  ws.close()
} catch (err) {
  failed = true
  console.error('capture failed:', err)
} finally {
  chrome.kill()
  server.kill()
  await sleep(300)
  await rm(profile, { recursive: true, force: true }).catch(() => {})
}
process.exit(failed ? 1 : 0)
