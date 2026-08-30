/**
 * End-to-end verification of the Jarvis SPA through the public tunnel.
 * Real browser, real HTTPS origin, real session cookie, real 34dev data.
 */
import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'

const BASE = process.argv[2]
const SHOTS = new URL('./shots/', import.meta.url).pathname
mkdirSync(SHOTS, { recursive: true })

// Read the passphrase straight from the env file. Never logged.
function passphrase() {
  for (const line of readFileSync('/root/.hermes/.env', 'utf8').split('\n')) {
    const m = /^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(line)
    if (m) return m[1].trim().replace(/^["']|["']$/g, '')
  }
  throw new Error('APP_PASSPHRASE not found')
}

let pass = 0
let fail = 0
const check = (name, ok, extra = '') => {
  console.log(`${ok ? '  PASS' : '  FAIL'}  ${name}${extra ? ' — ' + extra : ''}`)
  ok ? pass++ : fail++
}

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  ignoreHTTPSErrors: false,
})
const page = await ctx.newPage()

// The browser logs every non-2xx response as a console error. Two 401s are
// EXPECTED and are the design working: the initial /api/session probe (the
// httponly cookie can't be read, so 401 is how we learn we're logged out) and
// the deliberate wrong-passphrase test. Anything else is a real error.
const consoleErrors = []
const expected401 = (t) => /401/.test(t) && /Failed to load resource/.test(t)
page.on('console', (m) => {
  if (m.type() === 'error' && !expected401(m.text())) consoleErrors.push(m.text())
})
page.on('pageerror', (e) => consoleErrors.push(String(e)))

// Catch any request leaving our origin — the bundle must be self-contained.
const foreign = new Set()
page.on('request', (r) => {
  const u = new URL(r.url())
  if (u.origin !== new URL(BASE).origin && u.protocol.startsWith('http')) {
    foreign.add(u.origin)
  }
})

console.log(`\n=== 1. LOGIN SCREEN ===`)
await page.goto(BASE, { waitUntil: 'networkidle' })
check('login screen renders', await page.getByPlaceholder('Passphrase').isVisible())
check('asterisk mark present', (await page.locator('svg[aria-label="Jarvis"]').count()) > 0)
const btnBg = await page
  .getByRole('button', { name: 'Continue' })
  .evaluate((el) => getComputedStyle(el).backgroundColor)
check('accent button uses ink-500', btnBg === 'rgb(36, 66, 124)', btnBg)
const radius = await page
  .getByPlaceholder('Passphrase')
  .evaluate((el) => getComputedStyle(el).borderRadius)
check('passphrase field is radius-pill (22px)', radius === '22px', radius)
await page.screenshot({ path: SHOTS + '01-login-light.png' })

console.log(`\n=== 2. NEGATIVE: WRONG PASSPHRASE ===`)
await page.getByPlaceholder('Passphrase').fill('definitely-not-the-passphrase')
await page.getByRole('button', { name: 'Continue' }).click()
await page.waitForSelector('[role="alert"]', { timeout: 10000 })
const errText = await page.locator('[role="alert"]').innerText()
check('styled 401 error shown', /didn't work/i.test(errText), JSON.stringify(errText))
check('still on login screen', await page.getByPlaceholder('Passphrase').isVisible())
await page.screenshot({ path: SHOTS + '02-login-error.png' })

console.log(`\n=== 3. REAL LOGIN ===`)
await page.getByPlaceholder('Passphrase').fill(passphrase())
await page.getByRole('button', { name: 'Continue' }).click()
await page.waitForSelector('ol > li', { timeout: 20000 })
const greeting = await page.locator('h1').first().innerText()
check('greeting uses identity display_name', greeting === 'Hi, Spencer', greeting)

console.log(`\n=== 4. THE THREE REAL 34dev EVENTS ===`)
const rows = await page.locator('ol > li').all()
check('3 feed rows', rows.length === 3, `got ${rows.length}`)

const rowData = await page.locator('ol > li').evaluateAll((els) =>
  els.map((el) => {
    const dot = el.querySelector('span[aria-hidden="true"].rounded-full')
    const spans = el.querySelectorAll('header span')
    return {
      word: spans[0]?.textContent?.trim(),
      wordFont: spans[0] ? getComputedStyle(spans[0]).fontFamily : '',
      wordColor: spans[0] ? getComputedStyle(spans[0]).color : '',
      skill: spans[1]?.textContent?.trim(),
      skillFont: spans[1] ? getComputedStyle(spans[1]).fontFamily : '',
      dotColor: dot ? getComputedStyle(dot).backgroundColor : '',
      dotSize: dot ? getComputedStyle(dot).width : '',
      message: el.querySelector('p')?.textContent?.trim().slice(0, 40),
    }
  }),
)
for (const r of rowData) console.log('   ', JSON.stringify(r))

// Newest first: email.classified, task.completed, task.created
const expect = [
  { word: 'Email filed', skill: 'email-router', dot: 'rgb(91, 130, 196)' },  // light.info
  { word: 'Done', skill: 'task-manager', dot: 'rgb(201, 205, 203)' },        // light.neutral
  { word: 'Task created', skill: 'task-manager', dot: 'rgb(30, 138, 90)' },  // light.success
]
expect.forEach((e, i) => {
  const r = rowData[i]
  check(`row ${i + 1} kind word "${e.word}"`, r?.word === e.word, r?.word)
  check(`row ${i + 1} skill "${e.skill}"`, r?.skill === e.skill, r?.skill)
  check(`row ${i + 1} dot colour`, r?.dotColor === e.dot, r?.dotColor)
})
check('kind words are IBM Plex Mono', rowData.every((r) => /Plex Mono/.test(r.wordFont)))
check('skill names are IBM Plex Mono', rowData.every((r) => /Plex Mono/.test(r.skillFont)))
check('spine dots are 13px', rowData.every((r) => r.dotSize === '13px'), rowData[0]?.dotSize)
check(
  '"Done" word uses text-secondary per tokens',
  rowData[1]?.wordColor === 'rgb(110, 117, 115)',
  rowData[1]?.wordColor,
)

console.log(`\n=== 5. SMOKED HERO ===`)
const hero = page.locator('section.smoked-hero')
const heroText = (await hero.innerText()).replace(/\n/g, ' | ')
console.log('    hero:', heroText)
check('hero shows aggregate counts', /1 email filed|1 done|1 task created/.test(heroText))
check('hero shows All clear (0 need you)', /All clear/.test(heroText))
const heroBg = await hero.evaluate((el) => getComputedStyle(el).backgroundImage)
check('hero is the opaque smoked gradient', /linear-gradient\(160deg/.test(heroBg))

await page.screenshot({ path: SHOTS + '03-feed-light.png', fullPage: true })

console.log(`\n=== 6. FILTERS ===`)
await page.getByRole('button', { name: 'task-manager', exact: true }).click()
await page.waitForTimeout(300)
check('skill=task-manager → 2 rows', (await page.locator('ol > li').count()) === 2)
await page.getByRole('button', { name: 'task-manager', exact: true }).click()
await page.waitForTimeout(300)
await page.getByRole('button', { name: 'email-router', exact: true }).click()
await page.waitForTimeout(300)
check('skill=email-router → 1 row', (await page.locator('ol > li').count()) === 1)
await page.getByRole('button', { name: 'email-router', exact: true }).click()
await page.waitForTimeout(300)
check('clearing filter restores 3 rows', (await page.locator('ol > li').count()) === 3)

console.log(`\n=== 7. DARK MODE ===`)
await page.getByRole('button', { name: /Switch to dark mode/ }).click()
await page.waitForTimeout(400)
const darkBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
check('body is dark.bg #0C0E12', darkBg === 'rgb(12, 14, 18)', darkBg)
const darkRow = await page
  .locator('ol li article')
  .first()
  .evaluate((el) => getComputedStyle(el).backgroundColor)
check('feed rows use dark.surface #14181F', darkRow === 'rgb(20, 24, 31)', darkRow)
const darkHeroBg = await hero.evaluate((el) => getComputedStyle(el).backgroundImage)
check('smoked hero identical in dark', darkHeroBg === heroBg)
await page.screenshot({ path: SHOTS + '04-feed-dark.png', fullPage: true })

console.log(`\n=== 8. TAB PLACEHOLDERS ===`)
for (const tab of ['Ask', 'Approvals', 'Leads']) {
  await page.getByRole('button', { name: new RegExp(`^${tab}$`) }).click()
  await page.waitForTimeout(250)
  const vis = await page.locator('section.glass-featured').isVisible()
  check(`${tab} renders a styled placeholder`, vis)
}
await page.screenshot({ path: SHOTS + '05-ask-dark.png' })
await page.getByRole('button', { name: /^Feed$/ }).click()
await page.waitForTimeout(300)
await page.getByRole('button', { name: /Switch to light mode/ }).click()
await page.waitForTimeout(300)

console.log(`\n=== 9. PWA INSTALLABILITY ===`)
const manifest = await page.evaluate(async () => {
  const link = document.querySelector('link[rel="manifest"]')
  if (!link) return null
  const res = await fetch(link.getAttribute('href'))
  return { status: res.status, body: await res.json() }
})
check('manifest served', manifest?.status === 200)
check('display: standalone', manifest?.body?.display === 'standalone')
check('start_url present', !!manifest?.body?.start_url)
check('name + short_name', !!manifest?.body?.name && !!manifest?.body?.short_name)
const icons = manifest?.body?.icons ?? []
check('has 192 icon', icons.some((i) => i.sizes === '192x192'))
check('has 512 icon', icons.some((i) => i.sizes === '512x512'))
check('has maskable icon', icons.some((i) => (i.purpose || '').includes('maskable')))
const iconOk = await page.evaluate(
  (srcs) =>
    Promise.all(
      srcs.map(
        (s) =>
          new Promise((res) => {
            const im = new Image()
            im.onload = () => res(`${im.naturalWidth}x${im.naturalHeight}`)
            im.onerror = () => res('ERROR')
            im.src = s
          }),
      ),
    ),
  icons.map((i) => i.src),
)
check('all declared icons load at size', !iconOk.includes('ERROR'), iconOk.join(' '))
check('served over HTTPS', new URL(BASE).protocol === 'https:')

const swState = await page.evaluate(async () => {
  if (!('serviceWorker' in navigator)) return 'unsupported'
  const reg = await navigator.serviceWorker.getRegistration()
  if (!reg) return 'none'
  return reg.active ? 'active' : reg.installing ? 'installing' : 'waiting'
})
check('service worker registered', ['active', 'installing', 'waiting'].includes(swState), swState)
const swFetch = await page.evaluate(async () => (await fetch('/sw.js')).status)
check('sw.js served from root scope', swFetch === 200)

console.log(`\n=== 9b. NO HORIZONTAL OVERFLOW AT 390px ===`)
// A phone-first layout must never scroll sideways. Checked explicitly because
// a single over-wide grid child silently drags every sibling off-screen.
const overflow = await page.evaluate(() => {
  const doc = document.documentElement
  // Elements inside a clipping ancestor are allowed past the edge: the ambient
  // blobs bleed off-screen by design, and the filter chips live in a
  // horizontally scrolling strip. Only unclipped overflow is a bug.
  const clipped = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const s = getComputedStyle(p)
      if (s.overflowX === 'hidden' || s.overflowX === 'auto' || s.overflowX === 'scroll') {
        return true
      }
    }
    return false
  }
  const offenders = []
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect()
    if (r.width > 0 && r.right > doc.clientWidth + 1 && !clipped(el)) {
      offenders.push(
        `${el.tagName.toLowerCase()}.${(el.className || '').toString().split(' ')[0]}` +
          ` right=${Math.round(r.right)}`,
      )
    }
  }
  return {
    scrollW: doc.scrollWidth,
    clientW: doc.clientWidth,
    offenders: offenders.slice(0, 5),
  }
})
check(
  'document does not scroll horizontally',
  overflow.scrollW <= overflow.clientW,
  `scrollWidth=${overflow.scrollW} clientWidth=${overflow.clientW}`,
)
check(
  'no element extends past the viewport',
  overflow.offenders.length === 0,
  overflow.offenders.join(', ') || 'none',
)

console.log(`\n=== 10. SELF-CONTAINED BUNDLE ===`)
check('no external origins requested', foreign.size === 0, [...foreign].join(', ') || 'none')
check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))

console.log(`\n=== 11. POLLING (15s) ===`)
const before = await page.locator('ol > li').count()
console.log(`    rows before seeding: ${before}`)
const { execSync } = await import('node:child_process')
execSync(
  `cd /root/.hermes/skills/openclaw-imports/_shared && python3 notify.py send tasks ` +
    `"Approve new deal channel for 2047 Columbia" --kind needs_approval --skill email-router`,
  { stdio: 'pipe' },
)
console.log('    seeded a needs_approval event; waiting up to 20s for the poll…')
let appeared = false
for (let i = 0; i < 20; i++) {
  await page.waitForTimeout(1000)
  if ((await page.locator('ol > li').count()) > before) {
    appeared = true
    console.log(`    appeared after ~${i + 1}s`)
    break
  }
}
check('new event appears via 15s polling', appeared)
if (appeared) {
  const newRow = await page.locator('ol > li').first().innerText()
  check('new row maps needs_ → warning word', /Needs Approval/i.test(newRow), newRow.split('\n')[0])
  const newDot = await page
    .locator('ol li span[aria-hidden="true"].rounded-full')
    .first()
    .evaluate((el) => getComputedStyle(el).backgroundColor)
  check('warning dot colour (light.warning)', newDot === 'rgb(176, 122, 46)', newDot)
  const heroNow = (await hero.innerText()).replace(/\n/g, ' | ')
  console.log('    hero now:', heroNow)
  check('hero flips to "1 thing needs you"', /1 thing needs you/.test(heroNow), heroNow)
  await page.screenshot({ path: SHOTS + '06-feed-needsyou.png', fullPage: true })
}

console.log(`\n=== 12. NEGATIVE: BACKEND DOWN ===`)
execSync('systemctl stop jarvis-app')
await page.waitForTimeout(1000)
await page.evaluate(() => window.dispatchEvent(new Event('focus')))
// Force an immediate poll by flipping visibility.
await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
let errShown = false
for (let i = 0; i < 22; i++) {
  await page.waitForTimeout(1000)
  if (await page.locator('[role="alert"]').isVisible().catch(() => false)) {
    errShown = true
    break
  }
}
check('honest error state shown when backend is down', errShown)
const stillRows = await page.locator('ol > li').count()
check('last-good events still on screen (never a blank list)', stillRows > 0, `${stillRows} rows`)
if (errShown) await page.screenshot({ path: SHOTS + '07-error-state.png', fullPage: true })
execSync('systemctl start jarvis-app')

console.log(`\n${'='.repeat(52)}`)
console.log(`RESULT: ${pass} passed, ${fail} failed`)
console.log(`screenshots: ${SHOTS}`)
await browser.close()
process.exit(fail === 0 ? 0 : 1)
