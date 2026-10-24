/**
 * Jarvis Mono, in a real browser, as every seeded person and the operator.
 *
 * The API suites prove the rules; this proves the redesigned screens tell the
 * truth about them: Settings and the composer only where the server allows
 * them, every screen reachable from the sidebar, nothing wider than the
 * viewport, the phone's list-first flow, the dark default and the light switch,
 * a failed send that says so, and a new channel that is exactly one new row.
 *
 * Run against the sandbox only (its gateway is a dead port, so a sent message
 * fails closed):
 *   node docs/frontend/verify_mono_ui.mjs --base http://127.0.0.1:8711 --pass-file /tmp/vpg-operator.pass
 * --canary expects the operator to see Settings and a composer; those checks must FAIL.
 */
import { chromium } from '/usr/local/lib/hermes-agent/node_modules/playwright/index.mjs'
import { readFileSync, mkdirSync } from 'node:fs'

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
const PINS = JSON.parse(readFileSync(arg('--pins', '/opt/jarvis-verify-fixture/SEEDED_PINS.json'), 'utf8'))
const pinOf = (who) => (typeof PINS[who] === 'object' ? PINS[who].pin : PINS[who])
const PASS = readFileSync(arg('--pass-file', '/tmp/vpg-operator.pass'), 'utf8').trim()
const SHOTS = new URL('./mono-shots/', import.meta.url).pathname
mkdirSync(SHOTS, { recursive: true })

const results = []
const ck = (name, cond, extra = '') => {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? ' — ' + extra : ''}`)
  results.push(!!cond)
}

const DESKTOP = { width: 1280, height: 800 }
const PHONE = { width: 390, height: 844 }

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
  await page.waitForTimeout(1200)
}

const session = (page) => page.evaluate(() => fetch('/api/session', { credentials: 'include' }).then((r) => r.json()))
const threads = (page) => page.evaluate(() => fetch('/api/threads', { credentials: 'include' }).then((r) => r.json()))
const bg = (page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor)
const nav = (page) => page.getByRole('navigation', { name: 'Jarvis' })

// An element only overflows if no ancestor clips it.
const overflow = (page) =>
  page.evaluate(() => {
    const d = document.documentElement
    const clipped = (el) => {
      for (let q = el.parentElement; q && q !== document.body; q = q.parentElement)
        if (['hidden', 'auto', 'scroll'].includes(getComputedStyle(q).overflowX)) return true
      return false
    }
    const bad = []
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect()
      if (r.width > 0 && r.right > d.clientWidth + 1 && !clipped(el)) bad.push(el.tagName + '.' + String(el.className).slice(0, 40))
    }
    return bad.slice(0, 3)
  })

const SCREENS = [
  ['Activity', 'Activity'],
  ['Approvals', 'Approvals'],
  ['Tasks', 'Tasks'],
  ['Leads', 'Leads'],
  ['Settings', 'Settings'],
]

async function desktop(browser, who) {
  const ctx = await browser.newContext({ viewport: DESKTOP })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await signIn(page, who)
  const s = await session(page)
  const operator = s.role === 'operator'
  const expectOperator = operator !== CANARY // the canary pretends the operator is a person
  console.log(`\n${who} (role ${s.role}) — desktop`)

  ck('dark is the default (body is #0a0a0a)', (await bg(page)) === 'rgb(10, 10, 10)', await bg(page))
  for (const label of ['Activity', 'Approvals', 'Tasks', 'Leads'])
    ck(`sidebar offers ${label}`, (await nav(page).getByRole('button', { name: new RegExp(`^${label}( \\d+ waiting on you)?$`) }).count()) === 1)
  const settings = await nav(page).getByRole('button', { name: 'Settings', exact: true }).count()
  ck(expectOperator ? 'operator: no Settings in the sidebar' : 'Settings in the sidebar', expectOperator ? settings === 0 : settings === 1)

  const t = await threads(page)
  const direct = (t.threads || []).find((x) => x.is_direct)
  if (operator) {
    // Break-glass has no person, so no direct line: nothing opens until a channel is picked.
    ck('operator: no direct line, so the pane asks for a conversation', !direct && (await page.getByText('Pick a conversation.').count()) === 1)
    const channel = (t.threads || [])[0]
    await nav(page).getByRole('button', { name: new RegExp(channel.name) }).first().click()
    await page.waitForTimeout(800)
    ck('operator: a channel opens', (await page.getByRole('heading', { level: 1, name: channel.name }).count()) === 1, channel.name)
    ck('operator: the chat says why it is empty', (await page.getByText('Operator access shows this channel').count()) === 1)
  } else {
    ck('the direct line opens by default', direct && (await page.getByRole('heading', { level: 1, name: direct.name }).count()) === 1, direct?.name)
  }
  const composer = await page.getByRole('textbox', { name: 'Message Jarvis' }).count()
  ck(expectOperator ? 'operator: no composer' : 'composer present', expectOperator ? composer === 0 : composer === 1)
  ck(
    operator ? 'operator: rows say messages are hidden' : 'rows do not claim messages are hidden',
    operator
      ? (await nav(page).getByText('Messages hidden for operator access').count()) > 0
      : (await nav(page).getByText('Messages hidden for operator access').count()) === 0,
  )
  await page.screenshot({ path: `${SHOTS}${who}-desktop-chat.png` })

  for (const [label, title] of SCREENS) {
    const btn = nav(page).getByRole('button', { name: new RegExp(`^${label}( \\d+ waiting on you)?$`) })
    if (operator && label === 'Settings') continue
    if (!(await btn.count())) {
      ck(`${label} is in the sidebar to open`, false)
      continue
    }
    await btn.click()
    await page.waitForTimeout(900)
    ck(`${label} opens with its title`, (await page.getByRole('heading', { level: 1, name: title, exact: true }).count()) === 1)
    const bad = await overflow(page)
    ck(`${label}: nothing wider than the window`, bad.length === 0, bad.join(', '))
    await page.screenshot({ path: `${SHOTS}${who}-desktop-${label.toLowerCase()}.png`, fullPage: false })
    if (label === 'Tasks') {
      const n = await page.getByRole('button', { name: 'New task' }).count()
      ck(operator ? 'operator: no New task' : 'New task offered', operator ? n === 0 : n === 1)
    }
  }

  // Theme switch: light, then back, so the next person starts dark again.
  await nav(page).getByRole('button', { name: 'Switch to light' }).click()
  ck('the switch turns the page white', (await bg(page)) === 'rgb(255, 255, 255)', await bg(page))
  await page.screenshot({ path: `${SHOTS}${who}-desktop-light.png` })
  await nav(page).getByRole('button', { name: 'Switch to dark' }).click()
  ck('   and back to black', (await bg(page)) === 'rgb(10, 10, 10)')

  ck('no script errors', errors.length === 0, errors.slice(0, 2).join(' | '))
  await ctx.close()
  return s
}

async function phone(browser, who) {
  const ctx = await browser.newContext({ viewport: PHONE, isMobile: true, hasTouch: true })
  const page = await ctx.newPage()
  await signIn(page, who)
  console.log(`\n${who} — phone`)
  const box = await nav(page).boundingBox()
  ck('the phone opens on the list, full width', box && Math.round(box.width) === PHONE.width, box && `${Math.round(box.width)}px`)
  ck('   and no screen beside it', (await page.getByRole('heading', { level: 1 }).count()) === 0)
  await page.screenshot({ path: `${SHOTS}${who}-phone-list.png` })
  await nav(page).getByRole('button', { name: 'Tasks', exact: true }).click()
  await page.waitForTimeout(800)
  ck('Tasks replaces the list', (await page.getByRole('heading', { level: 1, name: 'Tasks' }).count()) === 1 && !(await nav(page).isVisible()))
  const bad = await overflow(page)
  ck('Tasks on a phone: nothing wider than the window', bad.length === 0, bad.join(', '))
  await page.screenshot({ path: `${SHOTS}${who}-phone-tasks.png` })
  await page.getByRole('button', { name: 'Back', exact: true }).click()
  await page.waitForTimeout(400)
  ck('Back returns to the list', await nav(page).isVisible())
  const t = await threads(page)
  const direct = (t.threads || []).find((x) => x.is_direct)
  await nav(page).getByRole('button', { name: new RegExp(direct.name) }).first().click()
  await page.waitForTimeout(800)
  ck('the direct line opens full screen', (await page.getByRole('heading', { level: 1, name: direct.name }).count()) === 1)
  await page.screenshot({ path: `${SHOTS}${who}-phone-chat.png` })
  await ctx.close()
}

async function chat(browser, who) {
  const ctx = await browser.newContext({ viewport: DESKTOP })
  const page = await ctx.newPage()
  await signIn(page, who)
  console.log(`\n${who} — chat`)
  const box = page.getByRole('textbox', { name: 'Message Jarvis' })
  await box.fill('hello from the mono check')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await page.waitForTimeout(2500)
  ck('the question shows as a bubble', (await page.getByText('hello from the mono check').count()) >= 1)
  const alert = page.locator('[role="alert"]')
  ck('a send the gateway cannot answer says so', (await alert.count()) >= 1, (await alert.first().textContent().catch(() => '')) || '')
  // Present is not enough: it has to be on screen, inside the transcript's visible window.
  const inView = await page.evaluate(() => {
    const a = document.querySelector('[role="alert"]')
    if (!a) return false
    let s = a.parentElement
    while (s && !['auto', 'scroll'].includes(getComputedStyle(s).overflowY)) s = s.parentElement
    const r = a.getBoundingClientRect()
    const box = (s ?? document.documentElement).getBoundingClientRect()
    return r.top >= box.top - 1 && r.bottom <= box.bottom + 1
  })
  ck('   and the chat follows it into view', inView)
  await page.screenshot({ path: `${SHOTS}${who}-desktop-send-error.png` })

  // A new channel is exactly one new row, and the app drops straight into it.
  const before = (await threads(page)).threads.length
  await nav(page).getByRole('button', { name: 'New channel' }).click()
  const name = `Mono check ${Date.now() % 100000}`
  await page.getByRole('dialog').getByRole('textbox').fill(name)
  await page.getByRole('dialog').getByRole('button', { name: 'Create' }).click()
  await page.waitForTimeout(1500)
  const after = (await threads(page)).threads.length
  ck(`a new channel is one new row (${before} -> ${after})`, after === before + 1)
  ck('   and it opens', (await page.getByRole('heading', { level: 1, name }).count()) === 1)
  ck('   with a members button', (await page.getByRole('button', { name: `Members of ${name}` }).count()) === 1)
  await page.getByRole('button', { name: `Members of ${name}` }).click()
  await page.waitForTimeout(400)
  ck('   which opens the members sheet', (await page.getByRole('dialog').count()) === 1)
  await page.screenshot({ path: `${SHOTS}${who}-desktop-members.png` })
  await ctx.close()
}

async function wrongPin(browser) {
  const ctx = await browser.newContext({ viewport: DESKTOP })
  const page = await ctx.newPage()
  await page.goto(BASE, { waitUntil: 'networkidle' })
  console.log('\nsign-in')
  await page.screenshot({ path: `${SHOTS}signin.png` })
  await page.getByRole('button', { name: 'Sign in with PIN' }).click()
  await page.getByRole('list', { name: 'Who is signing in' }).getByRole('button', { name: /tester/i }).click()
  await page.getByPlaceholder('PIN').fill('aaaa-bbbb-cccc')
  await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await page.waitForTimeout(1200)
  ck('a wrong PIN says so', (await page.getByText('That PIN is not right.').count()) === 1)
  ck('   and clears the field', (await page.getByPlaceholder('PIN').inputValue()) === '')
  await page.screenshot({ path: `${SHOTS}signin-wrong-pin.png` })
  await ctx.close()
}

const browser = await chromium.launch()
try {
  if (CANARY) {
    console.log('CANARY: expecting the operator to see Settings and a composer')
    await desktop(browser, 'operator')
  } else {
    await wrongPin(browser)
    for (const who of ['dana', 'priya', 'omar', 'tester', 'operator']) await desktop(browser, who)
    await phone(browser, 'dana')
    await chat(browser, 'dana')
  }
} finally {
  await browser.close()
}
const pass = results.filter(Boolean).length
console.log(`\n${pass}/${results.length} passed${CANARY ? ' (canary: failures expected)' : ''}`)
process.exit(pass === results.length ? 0 : 1)
