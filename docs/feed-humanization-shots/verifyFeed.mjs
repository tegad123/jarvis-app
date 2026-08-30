/**
 * Feed humanization verification — the REALTOR-READABILITY GATE.
 *
 * Asserts against the RENDERED DOM, not the source: what the tenant actually
 * sees must contain no category letters, no '@', no bracket-ids and no
 * "(medium)"-style parentheticals.
 */
import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'

const BASE = process.argv[2]
const SHOTS = new URL('./shotsFeed/', import.meta.url).pathname
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

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
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
await page.waitForTimeout(1200)

console.log('\n=== 1. WHAT THE TENANT ACTUALLY SEES ===')
const rows = await page.locator('ol > li').evaluateAll((els) =>
  els.map((el) => {
    const art = el.querySelector('article')
    const ps = art ? [...art.querySelectorAll('p')] : []
    return { body: (ps[0]?.textContent || '').trim(), full: (art?.textContent || '').trim() }
  }),
)
rows.forEach((r, i) => console.log(`  ${i + 1}. ${r.body}`))

console.log('\n=== 2. READABILITY GATE (rendered DOM, not source) ===')
// SCOPE: the gate applies to events carrying the humanised schema. Legacy
// events predate it and are asserted separately in section 6 — holding them to
// rules that did not exist when they were written would be meaningless.
const events = await page.evaluate(async () =>
  (await (await fetch('/api/feed?limit=50', { credentials: 'include' })).json()).events)
const newIdx = events.map((e, i) => (e.headline ? i : -1)).filter((i) => i >= 0)
const legacyIdx = events.map((e, i) => (e.headline ? -1 : i)).filter((i) => i >= 0)
console.log(`    ${newIdx.length} humanised event(s), ${legacyIdx.length} legacy event(s)`)
check('there are humanised events to gate', newIdx.length >= 3, `${newIdx.length}`)

const visible = newIdx.map((i) => rows[i]?.full || '').join('\n')

const violations = {
  'email addresses (@)': /@/.exec(visible),
  'bracket ids [XXX-0000]': /\[[A-Za-z0-9][^\]]*\]/.exec(visible),
  'parentheticals (medium)/(high)': /\((?:high|medium|low)\)/i.exec(visible),
  'the word "Category"': /\bcategor(?:y|ies)\b/i.exec(visible),
  'internal task ids': /\bTASK-\d{8}-\d{4}\b/.exec(visible),
  'skill jargon "classified"': /\bclassified\b/i.exec(visible),
}
for (const [name, hit] of Object.entries(violations)) {
  check(`no ${name}`, hit === null, hit ? `found: ${JSON.stringify(hit[0])}` : '')
}

console.log('\n=== 3. HEADLINES READ AS SENTENCES ===')
const verbs = /^(Filed|Created|Completed|Saved|Added|Sent|Drafted|Assigned)\b/
const newRows = newIdx.map((i) => rows[i]).filter(Boolean)
const withVerb = newRows.filter((r) => verbs.test(r.body)).length
check('every humanised row starts with a plain verb', withVerb === newRows.length, `${withVerb}/${newRows.length}`)
check('rows are sentences, not telemetry', newRows.every((r) => r.body.split(' ').length >= 4))

console.log('\n=== 4. DETAIL BEHIND TAP-TO-EXPAND ===')
const detailBtns = page.getByRole('button', { name: 'Details' })
const nDetail = await detailBtns.count()
check('detail is collapsed by default', nDetail > 0, `${nDetail} rows offer Details`)
check('technical specifics NOT visible before expanding', !/Category [A-G]|confidence/i.test(visible))
if (nDetail > 0) {
  await detailBtns.first().click()
  await page.waitForTimeout(400)
  const expanded = await page.locator('ol > li article').first().innerText()
  console.log('    expanded detail:', expanded.split('\n').slice(-1)[0].slice(0, 100))
  check('expanding reveals the technical detail', /Category|confidence|priority|·/i.test(expanded))
  await page.screenshot({ path: SHOTS + 'g2-detail-expanded-light.png' })
  await detailBtns.first().click()
  await page.waitForTimeout(300)
}

console.log('\n=== 5. NO DUPLICATE SUBJECT CHIP ===')
const dupes = await page.locator('ol > li').evaluateAll((els) =>
  els.map((el) => {
    const art = el.querySelector('article')
    const body = (art?.querySelector('p')?.textContent || '').toLowerCase()
    const chips = [...(art?.querySelectorAll('ul li') || [])].map((c) => (c.textContent || '').trim())
    return chips.filter((c) => c && body.includes(c.toLowerCase().slice(0, 40)))
  }).flat(),
)
check('no chip repeats text already in the headline', dupes.length === 0, dupes.join(' | '))

console.log('\n=== 6. OLD EVENTS STILL RENDER (backward compat) ===')
check('there is at least one legacy event to prove fallback', legacyIdx.length >= 1, `${legacyIdx.length}`)
const legacyRows = legacyIdx.map((i) => rows[i]).filter(Boolean)
check('legacy rows render non-empty via the message fallback',
  legacyRows.length > 0 && legacyRows.every((r) => r.body.length > 5),
  legacyRows[0]?.body.slice(0, 70))
check('legacy rows are shown as-is (old telemetry preserved, not mangled)',
  legacyRows.some((r) => /classified|TASK-\d{8}/i.test(r.body)),
  'confirms the frontend does not string-mangle old events')

console.log('\n=== 7. THEMES + OVERFLOW ===')
await page.screenshot({ path: SHOTS + 'g1-feed-light.png', fullPage: false })
await page.getByRole('button', { name: /Switch to dark mode/ }).click()
await page.waitForTimeout(500)
check('dark bg', (await page.evaluate(() => getComputedStyle(document.body).backgroundColor)) === 'rgb(12, 14, 18)')
await page.screenshot({ path: SHOTS + 'g3-feed-dark.png' })
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
check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))

console.log(`\n${'='.repeat(52)}`)
console.log(`RESULT: ${pass} passed, ${fail} failed`)
await browser.close()
process.exit(fail === 0 ? 0 : 1)
