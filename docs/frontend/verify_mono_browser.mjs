/**
 * The browser card (ComputerCard via BrowserWindow) in a real browser.
 *
 * The sandbox's tenant has the browser switched off, so the card's own API is
 * answered here, in the server's shapes and nothing more: /api/browser/status
 * and /screen as app/browser.py returns them, /api/approvals items as
 * app/approvals.py:_view builds them. Every request the card makes is
 * recorded, so the checks are on what it SENT, not on what it drew.
 *
 *   node docs/frontend/verify_mono_browser.mjs --base http://127.0.0.1:8711 --pass-file /tmp/vpg-operator.pass
 * --canary leaves the browser switched off; the card checks must FAIL.
 */
import { chromium } from '/usr/local/lib/hermes-agent/node_modules/playwright/index.mjs'
import { readFileSync } from 'node:fs'

const arg = (n, d) => {
  const i = process.argv.indexOf(n)
  return i > -1 ? process.argv[i + 1] : d
}
const BASE = arg('--base', 'http://127.0.0.1:8711')
if (!/^http:\/\/127\.0\.0\.1:87(1[1-9])$/.test(BASE)) {
  console.log(`REFUSING: ${BASE} is not a local sandbox port`)
  process.exit(2)
}
const CANARY = process.argv.includes('--canary')
const PINS = JSON.parse(readFileSync('/opt/jarvis-verify-fixture/SEEDED_PINS.json', 'utf8'))
const pinOf = (who) => (typeof PINS[who] === 'object' ? PINS[who].pin : PINS[who])
const PASS = readFileSync(arg('--pass-file', '/tmp/vpg-operator.pass'), 'utf8').trim()

const results = []
const ck = (name, cond, extra = '') => {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? ' — ' + extra : ''}`)
  results.push(!!cond)
}

const W = 1280
const H = 800

function fakeBrowser(page, jpeg, who) {
  const state = { takenOver: false, pending: true, sent: [] }
  const ask = () => ({
    id: 'appr-browser-1', ts: new Date().toISOString(), tenant: 'testco', origin: 'broker', kind: 'action',
    skill: 'browser', summary: 'On zillow.com: save 118 Alderwood to favourites',
    payload: { type: 'browser_task', site: 'zillow.com', task: 'save 118 Alderwood to favourites' },
    action_class: 'act', reason: 'acting on a site needs your say-so', source: null, requires_role: null,
    status: state.pending ? 'pending' : 'approved', for_person: who, expires_at: null,
    ...(state.pending ? { can_approve: true, can_reject: true } : { decided_by: who, decided_at: new Date().toISOString() }),
  })
  const json = (route, body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  page.route('**/api/browser/status', (r) =>
    json(r, CANARY ? { enabled: false } : { enabled: true, running: true, url: 'https://www.zillow.com/', title: 'Zillow', taken_over: state.takenOver, access: [] }),
  )
  page.route('**/api/browser/screen', (r) =>
    json(r, { running: true, url: 'https://www.zillow.com/', title: 'Zillow', jpeg, width: W, height: H, taken_over: state.takenOver }),
  )
  page.route('**/api/browser/takeover', (r) => {
    const body = JSON.parse(r.request().postData() || '{}')
    state.sent.push({ path: 'takeover', body })
    state.takenOver = !!body.on
    return json(r, { taken_over: state.takenOver })
  })
  page.route('**/api/browser/input', (r) => {
    state.sent.push({ path: 'input', body: JSON.parse(r.request().postData() || '{}') })
    return json(r, { ok: true, url: 'https://www.zillow.com/' })
  })
  page.route('**/api/approvals', (r) => {
    const a = ask()
    return json(r, { approvals: [a], count: 1, counts: { pending: state.pending ? 1 : 0, approved: state.pending ? 0 : 1, rejected: 0 }, requests_path: '' })
  })
  page.route('**/api/approvals/appr-browser-1', (r) => {
    state.sent.push({ path: 'decide', body: JSON.parse(r.request().postData() || '{}') })
    state.pending = false
    return json(r, { ok: true, approval: ask() })
  })
  return state
}

async function signIn(page, who) {
  await page.goto(BASE, { waitUntil: 'networkidle' })
  if (who === 'operator') {
    await page.getByRole('button', { name: 'Operator access' }).click()
    await page.getByPlaceholder('Operator passphrase').fill(PASS)
  } else {
    await page.getByRole('button', { name: 'Sign in with PIN' }).click()
    await page.getByRole('list', { name: 'Who is signing in' }).getByRole('button', { name: new RegExp(who, 'i') }).click()
    await page.getByPlaceholder('PIN').fill(pinOf(who))
  }
  await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await page.waitForResponse((r) => r.url().endsWith('/api/session') && r.status() === 200, { timeout: 8000 })
  await page.waitForTimeout(2500)
}

const browser = await chromium.launch()
try {
  // A real JPEG for the picture, so the <img> decodes like the worker's would.
  const shot = await browser.newPage({ viewport: { width: W, height: H } })
  await shot.setContent('<body style="margin:0;background:#1d4ed8;color:#fff;font:48px sans-serif;display:grid;place-items:center;height:100vh">a page</body>')
  const jpeg = (await shot.screenshot({ type: 'jpeg', quality: 60 })).toString('base64')
  await shot.close()

  console.log(CANARY ? 'CANARY: browser switched off' : 'dana — the browser card')
  const ctx = await browser.newContext({ viewport: { width: W, height: H } })
  const page = await ctx.newPage()
  const state = fakeBrowser(page, jpeg, 'dana')
  await signIn(page, 'dana')
  const card = page.getByRole('region', { name: 'Jarvis’s browser' })

  ck('the card shows while the browser runs', (await card.count()) === 1)
  ck('   with the page title', (await card.getByText('Zillow').count()) >= 1)
  ck('   and the ask, as a 30-minute allow', (await card.getByRole('button', { name: 'Allow for 30 min' }).count()) === 1)
  await page.screenshot({ path: new URL('./mono-shots/browser-ask.png', import.meta.url).pathname })

  if ((await card.count()) === 1) {
    await card.getByRole('button', { name: 'Allow for 30 min' }).click()
    await page.waitForTimeout(800)
    const decide = state.sent.find((s) => s.path === 'decide')
    ck('Allow sends approve for that request', decide?.body?.decision === 'approve', JSON.stringify(decide?.body))
    ck('   and the ask goes away', (await card.getByRole('button', { name: 'Allow for 30 min' }).count()) === 0)

    await card.getByRole('button', { name: 'Take over' }).click()
    await page.waitForTimeout(1800)
    ck('Take over sends on', state.sent.some((s) => s.path === 'takeover' && s.body.on === true))
    ck('   and the card says it is yours', (await card.getByText('You have the browser').count()) === 1)
    ck('   expanded, with the typing field', (await card.getByRole('textbox', { name: 'Type into the page' }).count()) === 1)

    // The card reports where on the picture; the page wants its own pixels.
    const pic = card.getByRole('button', { name: 'Click on the page' })
    const box = await pic.boundingBox()
    await page.mouse.click(box.x + box.width * 0.25, box.y + box.height * 0.5)
    await page.waitForTimeout(800)
    const click = state.sent.find((s) => s.path === 'input' && s.body.kind === 'click')
    ck(
      `a click on the picture lands on the page's pixels (${click ? `${Math.round(click.body.x)},${Math.round(click.body.y)}` : 'none'}; want ≈${W * 0.25},${H * 0.5})`,
      click && Math.abs(click.body.x - W * 0.25) < W * 0.02 && Math.abs(click.body.y - H * 0.5) < H * 0.03,
    )
    await card.getByRole('textbox', { name: 'Type into the page' }).fill('118 Alderwood')
    await card.getByRole('button', { name: 'Type', exact: true }).click()
    await page.waitForTimeout(800)
    ck('Type sends the text to the page', state.sent.some((s) => s.path === 'input' && s.body.kind === 'type' && s.body.text === '118 Alderwood'))
    ck('   and nothing went into the chat', (await page.getByRole('textbox', { name: 'Message Jarvis' }).inputValue()) === '')
    await page.screenshot({ path: new URL('./mono-shots/browser-yours.png', import.meta.url).pathname })

    await card.getByRole('button', { name: 'I’m done' }).click()
    await page.waitForTimeout(1800)
    ck('I’m done hands it back', state.sent.some((s) => s.path === 'takeover' && s.body.on === false))

    await card.getByRole('button', { name: 'Hide the browser' }).click()
    await page.waitForTimeout(500)
    ck('Hide takes the card away', (await card.count()) === 0)
    const reopen = page.getByRole('button', { name: 'Browser', exact: true })
    ck('   and the header offers it back', (await reopen.count()) === 1)
    await reopen.click()
    await page.waitForTimeout(500)
    ck('   which brings it back', (await card.count()) === 1)
  }
  await ctx.close()

  if (!CANARY) {
    console.log('\noperator — never the card')
    const octx = await browser.newContext({ viewport: { width: W, height: H } })
    const op = await octx.newPage()
    fakeBrowser(op, jpeg, 'dana')
    await signIn(op, 'operator')
    await op.locator('nav').getByRole('button', { name: /verify-tasks-chat|Alderwood|Ravenscourt|Kestrel/ }).first().click()
    await op.waitForTimeout(2500)
    ck('the operator sees no browser card, even with one running', (await op.getByRole('region', { name: 'Jarvis’s browser' }).count()) === 0)
    ck('   (the channel did open)', (await op.getByText('Operator access shows this channel').count()) === 1)
    await octx.close()
  }
} finally {
  await browser.close()
}
const pass = results.filter(Boolean).length
console.log(`\n${pass}/${results.length} passed${CANARY ? ' (canary: failures expected)' : ''}`)
process.exit(pass === results.length ? 0 : 1)
