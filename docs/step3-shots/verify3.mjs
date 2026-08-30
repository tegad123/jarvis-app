/**
 * Build step 3 verification — the Ask tab, end to end through the tunnel.
 * Real browser, real HTTPS origin, real claude-sonnet-5 calls.
 */
import { chromium } from 'playwright'
import { readFileSync, mkdirSync, statSync } from 'node:fs'
import { execSync } from 'node:child_process'

const BASE = process.argv[2]
const SHOTS = new URL('./shots3/', import.meta.url).pathname
mkdirSync(SHOTS, { recursive: true })
const LOG = '/root/.hermes/logs/agent.log'

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

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
const page = await ctx.newPage()

const consoleErrors = []
const expected401 = (t) => /401/.test(t) && /Failed to load resource/.test(t)
page.on('console', (m) => { if (m.type() === 'error' && !expected401(m.text())) consoleErrors.push(m.text()) })
page.on('pageerror', (e) => consoleErrors.push(String(e)))
const foreign = new Set()
page.on('request', (r) => {
  const u = new URL(r.url())
  if (u.origin !== new URL(BASE).origin && u.protocol.startsWith('http')) foreign.add(u.origin)
})

const overflowCheck = async (label) => {
  const o = await page.evaluate(() => {
    const doc = document.documentElement
    const clipped = (el) => {
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const s = getComputedStyle(p)
        if (['hidden', 'auto', 'scroll'].includes(s.overflowX)) return true
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
  check(`no horizontal overflow (${label})`, o.sw <= o.cw && o.bad.length === 0, `sw=${o.sw} cw=${o.cw} ${o.bad.join(',')}`)
}

console.log('\n=== 1. LOGIN + OPEN ASK ===')
await page.goto(BASE, { waitUntil: 'networkidle' })
await page.getByPlaceholder('Passphrase').fill(passphrase())
await page.getByRole('button', { name: 'Continue' }).click()
await page.waitForSelector('ol > li', { timeout: 20000 })
await page.getByRole('button', { name: /^Ask$/ }).click()
await page.waitForTimeout(700)

console.log('\n=== 2. CONVERSATIONS LIST (design spec) ===')
const listText = await page.locator('main').innerText()
check('title "Conversations"', /Conversations/.test(listText))
check('subtitle "Jarvis keeps one channel per deal"', /Jarvis keeps one channel per deal/.test(listText))
check('pinned "Ask Jarvis"', /Ask Jarvis/.test(listText))
check('empty preview "Your direct line. Always here."', /Your direct line\. Always here\./.test(listText))
check('CHANNELS label', /CHANNELS/i.test(listText))
check('channels helper "opened by Jarvis as deals start"', /opened by Jarvis as deals start/.test(listText))
check('teaser name "128 Alder Ave"', /128 Alder Ave/.test(listText))
check('teaser EXAMPLE chip', /EXAMPLE/i.test(listText))
check(
  'teaser body copy exact (two sentences)',
  /When a new deal starts, Jarvis opens a channel for it\. The emails, tasks, and deadlines for that property all live in one thread\./.test(
    listText.replace(/\s+/g, ' '),
  ),
)
const pencil = await page.getByRole('button', { name: 'New conversation' }).count()
check('compose/pencil icon present', pencil === 1)
const teaserDashed = await page.evaluate(() => {
  const el = [...document.querySelectorAll('li')].find((e) => /128 Alder Ave/.test(e.textContent || ''))
  if (!el) return null
  const s = getComputedStyle(el)
  return { style: s.borderTopStyle, opacity: s.opacity, buttons: el.querySelectorAll('button').length }
})
check('teaser has dashed border', teaserDashed?.style === 'dashed', teaserDashed?.style)
check('teaser is ghosted (opacity < 1)', parseFloat(teaserDashed?.opacity ?? '1') < 1, teaserDashed?.opacity)
check('teaser is non-tappable (no button)', teaserDashed?.buttons === 0)
const glassPinned = await page.evaluate(() => {
  const b = [...document.querySelectorAll('button')].find((e) => /Ask Jarvis/.test(e.textContent || ''))
  return b ? b.className.includes('glass-featured') : false
})
check('pinned card uses glass tier 2', glassPinned)
await overflowCheck('list')
await page.screenshot({ path: SHOTS + 'a1-list-light.png' })

console.log('\n=== 3. THREAD EMPTY STATE ===')
await page.getByRole('button', { name: /Ask Jarvis/ }).click()
await page.waitForTimeout(500)
const emptyText = await page.locator('main').innerText()
check('empty-state copy', /Ask Jarvis anything about your leads, email, or schedule\./.test(emptyText))
for (const p of ['Which emails need me today?', "What's on the calendar this week?", 'Any warm leads I should chase?']) {
  check(`example prompt present: "${p.slice(0, 28)}…"`, emptyText.includes(p))
}
const micSlot = await page.evaluate(() => {
  const form = document.querySelector('form')
  return form ? [...form.querySelectorAll('span[aria-hidden="true"]')].some((s) => s.className.includes('w-9')) : false
})
check('mic slot reserved in composer', micSlot)
const inputRadius = await page.getByLabel('Message Jarvis').evaluate((el) => getComputedStyle(el).borderRadius)
check('composer field radius-pill', inputRadius === '22px', inputRadius)
await page.screenshot({ path: SHOTS + 'a2-thread-empty-light.png' })

console.log('\n=== 4. REAL ROUND-TRIP + PROGRESSIVE STREAMING ===')
const logSizeBefore = statSync(LOG).size
const q = 'In two short sentences, what is the capital of France and why is it famous?'
await page.getByLabel('Message Jarvis').fill(q)

/*
 * Progressive-rendering probe.
 *
 * NOT "count increasing samples": the gateway coalesces model output into very
 * few content chunks (measured: a 20-line answer arrives as exactly 2), and our
 * proxy forwards them 1:1 — verified by comparing our NDJSON delta count against
 * the gateway's raw SSE chunk count for the same prompt. Asserting on chunk
 * granularity would be asserting on the upstream model, which we do not control.
 *
 * What we DO control, and what "streams" actually means to a user, is that text
 * paints while the request is still open rather than appearing all at once when
 * it closes. So: sample whether assistant text is visible AND the composer is
 * still disabled (i.e. mid-stream).
 */
const samples = []
let sawTextMidStream = false
const sampler = setInterval(async () => {
  try {
    const s = await page.evaluate(() => {
      const lis = [...document.querySelectorAll('ol > li')]
      let assistantLen = 0
      for (let i = lis.length - 1; i >= 0; i--) {
        if (/^jarvis/i.test((lis[i].textContent || '').trim())) {
          assistantLen = (lis[i].textContent || '').length
          break
        }
      }
      const input = document.querySelector('input[aria-label="Message Jarvis"]')
      return { assistantLen, streaming: input?.disabled === true }
    })
    samples.push(s.assistantLen)
    if (s.assistantLen > 6 && s.streaming) sawTextMidStream = true
  } catch { /* page busy */ }
}, 100)

await page.getByRole('button', { name: 'Send' }).click()

// Thinking step should appear before any text.
let sawStep = false
for (let i = 0; i < 40; i++) {
  await page.waitForTimeout(100)
  const t = await page.locator('main').innerText()
  if (/THINKING|RUNNING A COMMAND|SEARCHING|LOOKING THROUGH|USING A SKILL/i.test(t)) { sawStep = true; break }
  if (/JARVIS/.test(t) && (await page.locator('ol > li').count()) > 1) break
}
check('named thinking step shown', sawStep)

await page.waitForFunction(
  () => document.querySelector('input[aria-label="Message Jarvis"]')?.disabled === false,
  null,
  { timeout: 180000 },
)
clearInterval(sampler)
await page.waitForTimeout(400)

const growing = samples.filter((v, i) => i > 0 && v > samples[i - 1]).length
check(
  'answer painted while the stream was still open (not buffered to the end)',
  sawTextMidStream,
  `${growing} growth steps across ${samples.length} samples — chunk count is upstream-determined`,
)

const answer = await page.evaluate(() => {
  const items = [...document.querySelectorAll('ol > li')]
  const last = items[items.length - 1]
  return (last?.textContent || '').replace(/^Jarvis/i, '').trim()
})
console.log('    answer:', answer.slice(0, 120))
check('non-empty reply rendered', answer.length > 10)
check('reply mentions Paris', /paris/i.test(answer))

const newLog = readFileSync(LOG, 'utf8').slice(logSizeBefore)
const turnLines = newLog.split('\n').filter((l) => l.includes('agent.turn_context: conversation turn:'))
console.log('    log:', turnLines[turnLines.length - 1]?.slice(0, 150))
check('gateway served via api_server', turnLines.some((l) => /platform=api_server/.test(l)))
check('model=claude-sonnet-5', turnLines.some((l) => /model=claude-sonnet-5/.test(l)))
check('provider=anthropic', turnLines.some((l) => /provider=anthropic/.test(l)))

check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))
check('no external origins', foreign.size === 0, [...foreign].join(', ') || 'none')
await overflowCheck('thread with content')
await page.screenshot({ path: SHOTS + 'a3-thread-answer-light.png' })

console.log('\n=== 5. HISTORY WITHIN SESSION ===')
await page.getByLabel('Message Jarvis').fill('Which city did I just ask you about? One word.')
await page.getByRole('button', { name: 'Send' }).click()
await page.waitForFunction(() => document.querySelector('input[aria-label="Message Jarvis"]')?.disabled === false, null, { timeout: 180000 })
await page.waitForTimeout(400)
const followup = await page.evaluate(() => {
  const items = [...document.querySelectorAll('ol > li')]
  return (items[items.length - 1]?.textContent || '').trim()
})
console.log('    follow-up:', followup.slice(0, 100))
check('second turn used prior context', /paris/i.test(followup))

console.log('\n=== 6. HISTORY SURVIVES RELOAD ===')
await page.reload({ waitUntil: 'networkidle' })
await page.getByRole('button', { name: /^Ask$/ }).click()
await page.waitForTimeout(600)
const listAfter = await page.locator('main').innerText()
check('list preview shows last message', /Jarvis:/.test(listAfter), listAfter.split('\n').slice(0, 6).join(' | ').slice(0, 90))
await page.getByRole('button', { name: /Ask Jarvis/ }).click()
await page.waitForTimeout(800)
const restored = await page.locator('main').innerText()
check('conversation rehydrated after reload', /capital of France/i.test(restored) && /paris/i.test(restored))
await page.screenshot({ path: SHOTS + 'a4-rehydrated-light.png' })

console.log('\n=== 7. DARK MODE ===')
await page.getByRole('button', { name: /Switch to dark mode/ }).click()
await page.waitForTimeout(500)
const darkBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
check('dark bg', darkBg === 'rgb(12, 14, 18)', darkBg)
const bubble = await page.evaluate(() => {
  const p = [...document.querySelectorAll('ol > li p')].find((e) => e.className.includes('rounded-card'))
  return p ? getComputedStyle(p).backgroundColor : null
})
check('user bubble is ink fill in dark', bubble === 'rgb(36, 66, 124)', bubble)
await overflowCheck('thread dark')
await page.screenshot({ path: SHOTS + 'a5-thread-dark.png' })
await page.getByRole('button', { name: 'Back to conversations' }).click()
await page.waitForTimeout(400)
await page.screenshot({ path: SHOTS + 'a6-list-dark.png' })
await overflowCheck('list dark')

console.log('\n=== 8. FEED UNAFFECTED (spot-check) ===')
await page.getByRole('button', { name: /^Feed$/ }).click()
await page.waitForTimeout(800)
check('feed still renders 3 events', (await page.locator('ol > li').count()) === 3, `${await page.locator('ol > li').count()}`)

console.log('\n=== 9. NEGATIVE: BACKEND DOWN MID-CONVERSATION ===')
await page.getByRole('button', { name: /^Ask$/ }).click()
await page.waitForTimeout(400)
await page.getByRole('button', { name: /Ask Jarvis/ }).click()
await page.waitForTimeout(400)
execSync('systemctl stop jarvis-app')
await page.getByLabel('Message Jarvis').fill('This should fail honestly.')
await page.getByRole('button', { name: 'Send' }).click()
let errShown = false
for (let i = 0; i < 25; i++) {
  await page.waitForTimeout(500)
  if (await page.locator('[role="alert"]').isVisible().catch(() => false)) { errShown = true; break }
}
check('honest inline error when backend is down', errShown)
const draftKept = await page.getByLabel('Message Jarvis').inputValue()
check('composed text preserved for retry', draftKept === 'This should fail honestly.', JSON.stringify(draftKept))
if (errShown) await page.screenshot({ path: SHOTS + 'a7-error-dark.png' })
execSync('systemctl start jarvis-app')

console.log(`\n${'='.repeat(52)}`)
console.log(`RESULT: ${pass} passed, ${fail} failed`)
console.log(`screenshots: ${SHOTS}`)
await browser.close()
process.exit(fail === 0 ? 0 : 1)
