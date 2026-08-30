/**
 * Hero TODAY-counts verification.
 *
 * The bug: the hero said "TODAY" but counted only the 50 events the page
 * fetched, so past 50 events the counts silently became "the last 50" and could
 * go DOWN as events were added.
 *
 * The decisive assertion is not "the hero shows numbers" — it is that the
 * hero's numbers match the WHOLE DAY and therefore DIFFER from the fetch
 * window. If those two ever agree by construction, this test proves nothing.
 */
import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'

const BASE = process.argv[2]
const FEED = '/root/.hermes/state/activity_feed.jsonl'
const SHOTS = new URL('./shotsHero/', import.meta.url).pathname
mkdirSync(SHOTS, { recursive: true })

function passphrase() {
  for (const line of readFileSync('/root/.hermes/.env', 'utf8').split('\n')) {
    const m = /^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(line)
    if (m) return m[1].trim().replace(/^["']|["']$/g, '')
  }
  throw new Error('APP_PASSPHRASE not found')
}
let pass = 0, fail = 0
const check = (n, ok, extra = '') => {
  console.log(`${ok ? '  PASS' : '  FAIL'}  ${n}${extra ? ' — ' + extra : ''}`)
  ok ? pass++ : fail++
}

// --- Ground truth, computed here from the file, independent of the server. ---
const events = readFileSync(FEED, 'utf8').trim().split('\n').map((l) => JSON.parse(l))
const todayUTC = new Date().toISOString().slice(0, 10)
const isTodayUTC = (e) => (e.ts || '').slice(0, 10) === todayUTC
const tally = (rows) => rows.reduce((m, e) => ((m[e.kind] = (m[e.kind] || 0) + 1), m), {})

const truthWholeDay = tally(events.filter(isTodayUTC))
// What the OLD code would have produced: newest 50 by ts, then today-filtered.
const newest50 = [...events].sort((a, b) => (a.ts < b.ts ? 1 : -1)).slice(0, 50)
const truthWindow = tally(newest50.filter(isTodayUTC))

console.log('=== GROUND TRUTH (computed from the feed file, not the server) ===')
console.log('  whole day    :', JSON.stringify(truthWholeDay))
console.log('  fetch window :', JSON.stringify(truthWindow), '  <- what the OLD hero showed')
const differs = JSON.stringify(truthWholeDay) !== JSON.stringify(truthWindow)
check('the two genuinely differ, so this test can detect the bug', differs)

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({
  viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, timezoneId: 'UTC',
})
const page = await ctx.newPage()
const consoleErrors = []
page.on('console', (m) => {
  const t = m.text()
  if (m.type() === 'error' && !(/Failed to load resource/.test(t) && /401/.test(t))) consoleErrors.push(t)
})
page.on('pageerror', (e) => consoleErrors.push(String(e)))

await page.goto(BASE, { waitUntil: 'networkidle' })
await page.getByPlaceholder('Passphrase').fill(passphrase())
await page.getByRole('button', { name: 'Continue' }).click()
await page.waitForSelector('ol > li', { timeout: 20000 })
await page.waitForTimeout(1000)

console.log('\n=== 1. THE ENDPOINT ===')
const api = await page.evaluate(async () => {
  const r = await fetch('/api/feed/summary?tz=UTC', { credentials: 'include' })
  return { status: r.status, body: await r.json() }
})
check('/api/feed/summary answers 200', api.status === 200, `HTTP ${api.status}`)
check('counts match the whole day', JSON.stringify(api.body.counts) === JSON.stringify(truthWholeDay),
  JSON.stringify(api.body.counts))
check('counts are NOT the fetch window', JSON.stringify(api.body.counts) !== JSON.stringify(truthWindow))
check('echoes the timezone it used', api.body.timezone === 'UTC', api.body.timezone)
check('reports feed_total separately from today', api.body.feed_total === events.length,
  `${api.body.feed_total} vs ${events.length}`)

console.log('\n=== 2. THE HERO RENDERS THOSE COUNTS ===')
const hero = (await page.locator('section').first().innerText()).trim()
console.log('  hero text:', JSON.stringify(hero.replace(/\n/g, ' | ')))
// Expected phrasing, built from ground truth via the same word map the app uses.
const WORD = { 'email.classified': 'email filed', 'task.created': 'task created', 'task.completed': 'done' }
for (const [kind, n] of Object.entries(truthWholeDay)) {
  const word = WORD[kind]
  if (!word) continue
  check(`hero shows the whole-day count for ${kind} (${n})`,
    new RegExp(`\\b${n} ${word}\\b`).test(hero), `expected "${n} ${word}"`)
}
for (const [kind, n] of Object.entries(truthWindow)) {
  const word = WORD[kind]
  if (!word || truthWholeDay[kind] === n) continue
  check(`hero does NOT show the stale window count for ${kind} (${n})`,
    !new RegExp(`\\b${n} ${word}\\b`).test(hero), `must not contain "${n} ${word}"`)
}
check('hero still says TODAY', /today/i.test(hero))

// Monotonicity — the heart of the bug — is proved separately in
// proveMonotonic.sh, which emits a real event and asserts the count RISES.
// Asserting it here would mean mutating the feed mid-suite.

// Console must be clean on the HAPPY path. Asserted here, before section 4
// deliberately aborts a request — an abort logs its own ERR_FAILED, and folding
// that into the final check would either fail spuriously or force a filter so
// broad it would hide a real error.
check('no console errors on the happy path', consoleErrors.length === 0,
  consoleErrors.slice(0, 2).join(' | '))
const expectedAbortErrors = consoleErrors.length

console.log('\n=== 4. DEGRADED: summary fails -> no fabricated numbers ===')
await page.route('**/api/feed/summary*', (r) => r.abort())
await page.reload({ waitUntil: 'networkidle' })
await page.waitForSelector('ol > li', { timeout: 20000 })
await page.waitForTimeout(800)
const degraded = (await page.locator('section').first().innerText()).trim()
console.log('  hero text:', JSON.stringify(degraded.replace(/\n/g, ' | ')))
check('hero admits the summary is unavailable', /unavailable/i.test(degraded))
check('hero invents NO counts when it cannot know them', !/\d+ (email filed|task created|done)/.test(degraded))
check('the feed list still renders underneath', (await page.locator('ol > li').count()) > 0)
await page.screenshot({ path: SHOTS + 'h3-degraded.png' })
await page.unroute('**/api/feed/summary*')

console.log('\n=== 5. THEMES + OVERFLOW ===')
await page.reload({ waitUntil: 'networkidle' })
await page.waitForSelector('ol > li', { timeout: 20000 })
await page.waitForTimeout(800)
await page.screenshot({ path: SHOTS + 'h1-hero-light.png' })
await page.getByRole('button', { name: /Switch to dark mode/ }).click()
await page.waitForTimeout(500)
check('dark bg', (await page.evaluate(() => getComputedStyle(document.body).backgroundColor)) === 'rgb(12, 14, 18)')
await page.screenshot({ path: SHOTS + 'h2-hero-dark.png' })
const o = await page.evaluate(() => {
  const doc = document.documentElement
  const clipped = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (['hidden', 'auto', 'scroll'].includes(getComputedStyle(p).overflowX)) return true
    }
    return false
  }
  const bad = []
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect()
    if (r.width > 0 && r.right > doc.clientWidth + 1 && !clipped(el)) bad.push(el.tagName.toLowerCase())
  }
  return { sw: doc.scrollWidth, cw: doc.clientWidth, bad: bad.slice(0, 4) }
})
check('no horizontal overflow', o.sw <= o.cw && o.bad.length === 0, `sw=${o.sw} cw=${o.cw} ${o.bad.join(',')}`)
// Everything past the happy-path snapshot must be the aborted summary request
// and nothing else.
const lateErrors = consoleErrors.slice(expectedAbortErrors)
const unexpected = lateErrors.filter((e) => !/ERR_FAILED|Failed to load resource/.test(e))
check('no unexpected console errors', unexpected.length === 0, unexpected.slice(0, 2).join(' | '))
console.log(`    (${lateErrors.length} expected error(s) from the deliberate abort in section 4)`)

console.log(`\n${'='.repeat(52)}\nRESULT: ${pass} passed, ${fail} failed`)
await browser.close()
process.exit(fail === 0 ? 0 : 1)
