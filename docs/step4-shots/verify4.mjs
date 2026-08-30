/**
 * Build step 4 verification — Approvals + Leads, through the tunnel.
 */
import { chromium } from 'playwright'
import { readFileSync, mkdirSync, existsSync } from 'node:fs'
import { execSync } from 'node:child_process'

const BASE = process.argv[2]
const SHOTS = new URL('./shots4/', import.meta.url).pathname
mkdirSync(SHOTS, { recursive: true })
const DECISIONS = '/root/.hermes/state/approvals_decisions.jsonl'

function passphrase() {
  for (const line of readFileSync('/root/.hermes/.env', 'utf8').split('\n')) {
    const m = /^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(line)
    if (m) return m[1].trim().replace(/^["']|["']$/g, '')
  }
  throw new Error('APP_PASSPHRASE not found')
}
const decisionLines = () =>
  existsSync(DECISIONS)
    ? readFileSync(DECISIONS, 'utf8').split('\n').filter((l) => l.trim())
    : []

let pass = 0, fail = 0
const check = (n, ok, extra = '') => {
  console.log(`${ok ? '  PASS' : '  FAIL'}  ${n}${extra ? ' — ' + extra : ''}`)
  ok ? pass++ : fail++
}

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
const page = await ctx.newPage()
const consoleErrors = []
// The browser logs every non-2xx as a console error. Two are EXPECTED and are
// the design working: the initial /api/session 401 probe, and — only during the
// deliberate sheet-unreachable test — the 503 that /api/leads answers WITH a
// body so the UI can render an honest error instead of an empty table.
let expectingLeads503 = false
const expectedNetworkError = (t) =>
  /Failed to load resource/.test(t) &&
  (/401/.test(t) || (expectingLeads503 && /503/.test(t)))
page.on('console', (m) => { if (m.type() === 'error' && !expectedNetworkError(m.text())) consoleErrors.push(m.text()) })
page.on('pageerror', (e) => consoleErrors.push(String(e)))

const overflowCheck = async (label) => {
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
  check(`no horizontal overflow (${label})`, o.sw <= o.cw && o.bad.length === 0, `sw=${o.sw} cw=${o.cw} ${o.bad.join(',')}`)
}

console.log('\n=== 1. LOGIN ===')
await page.goto(BASE, { waitUntil: 'networkidle' })
await page.getByPlaceholder('Passphrase').fill(passphrase())
await page.getByRole('button', { name: 'Continue' }).click()
await page.waitForSelector('ol > li', { timeout: 20000 })

console.log('\n=== 2. APPROVALS QUEUE ===')
await page.getByRole('button', { name: /^Approvals$/ }).click()
await page.waitForTimeout(1200)
let text = await page.locator('main').innerText()
check('screen title + blurb', /Approvals/.test(text) && /Nothing goes out without your say-so/.test(text))
const cards = await page.locator('main ul > li').count()
check('3 seeded fixtures render', cards === 3, `${cards} cards`)
check('warning dot+word "Needs approval"', /NEEDS APPROVAL/i.test(text))
check('producing skill shown', /email-router|spencer-email|email-to-calendar/i.test(text))
check('summary rendered', /2047 Columbia|William Mchenry|11 Tiel Way/.test(text))
check('payload details rendered', /CHANNEL NAME|SUBJECT|ATTENDEES|ACTION/i.test(text))
const dotColor = await page.evaluate(() => {
  const li = document.querySelector('main ul > li')
  const dot = li?.querySelector('span[aria-hidden="true"].rounded-full')
  return dot ? getComputedStyle(dot).backgroundColor : null
})
check('pending dot uses light.warning', dotColor === 'rgb(176, 122, 46)', dotColor)
const btns = await page.evaluate(() => {
  const li = document.querySelector('main ul > li')
  const es = [...(li?.querySelectorAll('button') ?? [])]
  const find = (t) => es.find((e) => e.textContent.trim() === t)
  const edit = find('Edit'), approve = find('Approve')
  if (!edit || !approve) return null
  const er = edit.getBoundingClientRect(), ar = approve.getBoundingClientRect()
  return {
    editDisabled: edit.disabled,
    approveDisabled: approve.disabled,
    sameWidth: Math.abs(er.width - ar.width) < 2,
    sameHeight: Math.abs(er.height - ar.height) < 2,
    hasReject: !!find('Reject'),
  }
})
check('dual Edit/Approve buttons present', btns !== null)
check('Edit and Approve are equal weight', btns?.sameWidth && btns?.sameHeight, JSON.stringify(btns))
check('Edit is inert in v1', btns?.editDisabled === true)
check('Approve is actionable', btns?.approveDisabled === false)
check('Reject available', btns?.hasReject === true)
await overflowCheck('approvals')
await page.screenshot({ path: SHOTS + 'b1-approvals-light.png', fullPage: true })

console.log('\n=== 3. APPROVE A FIXTURE -> DECISION ON DISK ===')
const before = decisionLines().length
await page.getByRole('button', { name: 'Approve' }).first().click()
await page.waitForTimeout(2500)
const after = decisionLines()
check('decision line appended to approvals_decisions.jsonl', after.length === before + 1, `${before} -> ${after.length}`)
if (after.length > before) {
  const rec = JSON.parse(after[after.length - 1])
  console.log('    decision record:', JSON.stringify(rec))
  check('record has id/decision/decided_by/ts', !!(rec.id && rec.decision && rec.decided_by && rec.ts))
  check('decision is "approved"', rec.decision === 'approved')
  check('decided_by is the identity owner', rec.decided_by === 'Spencer', rec.decided_by)
}
text = await page.locator('main').innerText()
check('card moves to a resolved state', /APPROVED/i.test(text))
check('"Decided" section appears', /Decided/i.test(text))
check('2 still pending', (await page.locator('main').innerText()).match(/NEEDS APPROVAL/gi)?.length === 2)
await page.screenshot({ path: SHOTS + 'b2-approvals-resolved-light.png', fullPage: true })

console.log('\n=== 4. DECISION SURVIVES RELOAD (status rollup) ===')
await page.reload({ waitUntil: 'networkidle' })
await page.getByRole('button', { name: /^Approvals$/ }).click()
await page.waitForTimeout(1200)
text = await page.locator('main').innerText()
check('approved state persists after reload', /APPROVED/i.test(text))
check('approved-by recorded in UI', /by Spencer/i.test(text), text.match(/by \w+/)?.[0])

console.log('\n=== 5. LEADS FROM THE REAL SHEET ===')
await page.getByRole('button', { name: /^Leads$/ }).click()
await page.waitForTimeout(3000)
text = await page.locator('main').innerText()
check('screen title', /Leads/.test(text))
check('subtitle names the tab count + sheet-is-write-surface', /5 agent tabs/.test(text) && /the sheet stays the place you edit/.test(text))
const rows = await page.locator('main ul > li').count()
check('36 lead rows render', rows === 36, `${rows} rows`)
check('status chips present', /NO RESPONSE|RESPONDED|NOT INTERESTED/i.test(text))
check('last-activity line present', /Contacted|Emailed|Called|No activity yet/i.test(text))
check('agent attribution present', /Spencer|Martin|Alex|Tega|Blake/.test(text))
const bars = await page.evaluate(() => {
  const li = document.querySelector('main ul > li')
  const meter = li?.querySelector('span[title]')
  return meter ? meter.querySelectorAll('span[aria-hidden="true"]').length : 0
})
check('temperature meter has 3 bars', bars === 3, `${bars}`)
await overflowCheck('leads')
await page.screenshot({ path: SHOTS + 'b3-leads-light.png' })

console.log('\n=== 6. LEAD FILTERS ===')
await page.getByRole('button', { name: 'Needs touch', exact: true }).click()
await page.waitForTimeout(400)
check('Needs touch -> 13', (await page.locator('main ul > li').count()) === 13, `${await page.locator('main ul > li').count()}`)
await page.getByRole('button', { name: 'New', exact: true }).click()
await page.waitForTimeout(400)
check('New -> 6', (await page.locator('main ul > li').count()) === 6, `${await page.locator('main ul > li').count()}`)
await page.getByRole('button', { name: 'Hot', exact: true }).click()
await page.waitForTimeout(400)
const hotRows = await page.locator('main ul > li').count()
const hotText = await page.locator('main').innerText()
check('Hot -> 0 rows with an honest empty message', hotRows === 0 && /No leads match Hot/i.test(hotText), `${hotRows} rows`)
await page.getByRole('button', { name: 'All', exact: true }).click()
await page.waitForTimeout(400)
check('All restores 36', (await page.locator('main ul > li').count()) === 36)

console.log('\n=== 7. DARK MODE ===')
await page.getByRole('button', { name: /Switch to dark mode/ }).click()
await page.waitForTimeout(500)
check('dark bg', (await page.evaluate(() => getComputedStyle(document.body).backgroundColor)) === 'rgb(12, 14, 18)')
const rowBg = await page.evaluate(() => {
  const li = document.querySelector('main ul > li')
  return li ? getComputedStyle(li).backgroundColor : null
})
check('lead rows use dark.surface (opaque body content)', rowBg === 'rgb(20, 24, 31)', rowBg)
await overflowCheck('leads dark')
await page.screenshot({ path: SHOTS + 'b4-leads-dark.png' })
await page.getByRole('button', { name: /^Approvals$/ }).click()
await page.waitForTimeout(900)
await overflowCheck('approvals dark')
await page.screenshot({ path: SHOTS + 'b5-approvals-dark.png', fullPage: true })

console.log('\n=== 8. SHEET UNREACHABLE -> HONEST ERROR ===')
expectingLeads503 = true
// Point the reader at a sheet id that cannot be opened, restart, re-check.
execSync(`mkdir -p /etc/systemd/system/jarvis-app.service.d && printf '[Service]\\nEnvironment=JARVIS_LEADS_SHEET_ID=definitely-not-a-real-sheet-id\\n' > /etc/systemd/system/jarvis-app.service.d/99-test-leads.conf && systemctl daemon-reload && systemctl restart jarvis-app`)
await new Promise((r) => setTimeout(r, 4000))
await page.reload({ waitUntil: 'networkidle' })
await page.getByRole('button', { name: /^Leads$/ }).click()
await page.waitForTimeout(4000)
const errVisible = await page.locator('[role="alert"]').isVisible().catch(() => false)
const errText = errVisible ? await page.locator('[role="alert"]').innerText() : ''
check('honest error shown when the sheet cannot be read', errVisible, errText.split('\n').slice(0, 2).join(' | '))
check('error names the failure, not an empty list', /could not open the lead tracker/i.test(errText), errText.slice(0, 80))
check('no rows silently rendered as "no leads"', (await page.locator('main ul > li').count()) === 0)
await page.screenshot({ path: SHOTS + 'b6-leads-error-dark.png' })
execSync('rm -f /etc/systemd/system/jarvis-app.service.d/99-test-leads.conf && rmdir --ignore-fail-on-non-empty /etc/systemd/system/jarvis-app.service.d && systemctl daemon-reload && systemctl restart jarvis-app')
await new Promise((r) => setTimeout(r, 4000))

console.log('\n=== 9. HYGIENE ===')
check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))

console.log(`\n${'='.repeat(52)}`)
console.log(`RESULT: ${pass} passed, ${fail} failed`)
console.log(`screenshots: ${SHOTS}`)
await browser.close()
process.exit(fail === 0 ? 0 : 1)
