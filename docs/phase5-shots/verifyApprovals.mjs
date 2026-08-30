import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'
const BASE = process.argv[2]
const SHOTS = new URL('./shotsApprovals/', import.meta.url).pathname
mkdirSync(SHOTS, { recursive: true })
const pass = (n, ok, x = '') => { console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${n}${x ? ' — ' + x : ''}`); return ok }
function passphrase() {
  for (const l of readFileSync('/root/.hermes/.env', 'utf8').split('\n')) {
    const m = /^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(l)
    if (m) return m[1].trim().replace(/^["']|["']$/g, '')
  }
  throw new Error('no passphrase')
}
let ok = []
const b = await chromium.launch({ headless: true })
const ctx = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
const p = await ctx.newPage()
const errs = []
p.on('console', m => { if (m.type() === 'error' && !/401/.test(m.text())) errs.push(m.text()) })
p.on('pageerror', e => errs.push(String(e)))
await p.goto(BASE, { waitUntil: 'networkidle' })
await p.getByPlaceholder('Passphrase').fill(passphrase())
await p.getByRole('button', { name: 'Continue' }).click()
await p.waitForSelector('ol > li', { timeout: 20000 })
await p.getByRole('button', { name: /Approvals/i }).click()
await p.waitForTimeout(1500)

const body = await p.locator('main').innerText()
console.log('\n=== the config-change card, as rendered ===')
const card = body.split('\n').filter(l => /digest|Digest|→/.test(l)).slice(0, 6)
card.forEach(l => console.log('   ', l))

ok.push(pass('config-change request is visible', /digest times/i.test(body)))
ok.push(pass('renders as a DIFF with an arrow, not a key/value dump', /→/.test(body)))
ok.push(pass('shows the human label, not raw JSON', !/\[object Object\]/.test(body)))
ok.push(pass('shows the dotted config key for precision', /skills\.task-manager/i.test(body)))
ok.push(pass('action-kind requests still render their flat list',
  /Send a follow-up|11 Tiel Way|2047 Columbia/.test(body)))
await p.screenshot({ path: SHOTS + 'a1-config-diff-light.png' })

await p.getByRole('button', { name: /Switch to dark mode/ }).click(); await p.waitForTimeout(500)
ok.push(pass('dark bg', (await p.evaluate(() => getComputedStyle(document.body).backgroundColor)) === 'rgb(12, 14, 18)'))
await p.screenshot({ path: SHOTS + 'a2-config-diff-dark.png' })
const o = await p.evaluate(() => {
  const d = document.documentElement
  const clipped = el => { for (let q = el.parentElement; q && q !== document.body; q = q.parentElement)
    if (['hidden','auto','scroll'].includes(getComputedStyle(q).overflowX)) return true; return false }
  const bad = []
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect()
    if (r.width > 0 && r.right > d.clientWidth + 1 && !clipped(el)) bad.push(el.tagName)
  }
  return { sw: d.scrollWidth, cw: d.clientWidth, bad: bad.slice(0, 3) }
})
ok.push(pass('no horizontal overflow', o.sw <= o.cw && !o.bad.length, `sw=${o.sw} cw=${o.cw} ${o.bad}`))
ok.push(pass('no console errors', errs.length === 0, errs.slice(0, 2).join(' | ')))
console.log(`\n${ok.filter(Boolean).length}/${ok.length} passed`)
await b.close()
process.exit(ok.every(Boolean) ? 0 : 1)
