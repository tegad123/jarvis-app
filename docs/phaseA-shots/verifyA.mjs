console.error("REFUSING: this script works on live (/root/jarvis-app or /root/.hermes); port it to the platform tree (VERIFY_APP_ROOT, VERIFY_SKILLS_ROOT) before running it"); process.exit(2)
/**
 * Phase A verification — the channel layer, through the tunnel.
 */
import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'
import { execSync } from 'node:child_process'

const BASE = process.argv[2]
const SHOTS = new URL('./shotsA/', import.meta.url).pathname
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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
const page = await ctx.newPage()
const consoleErrors = []
page.on('console', (m) => {
  const t = m.text()
  if (m.type() === 'error' && !(/Failed to load resource/.test(t) && /401|409/.test(t))) consoleErrors.push(t)
})
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

const openAsk = async () => {
  await page.getByRole('button', { name: /^Ask$/ }).click()
  await page.waitForTimeout(900)
}
const waitIdle = () =>
  page.waitForFunction(
    () => document.querySelector('input[aria-label="Message Jarvis"]')?.disabled === false,
    null, { timeout: 180000 })

console.log('\n=== 1. LOGIN, EMPTY CHANNEL LIST SHOWS TEASER ===')
await page.goto(BASE, { waitUntil: 'networkidle' })
await page.getByPlaceholder('Passphrase').fill(passphrase())
await page.getByRole('button', { name: 'Continue' }).click()
await page.waitForSelector('ol > li', { timeout: 20000 })
await openAsk()
let text = await page.locator('main').innerText()
check('pinned direct line present', /Ask Jarvis/.test(text))
check('teaser shown when zero channels', /128 Alder Ave/.test(text) && /EXAMPLE/i.test(text))
await overflowCheck('list, empty')
await page.screenshot({ path: SHOTS + 'c1-list-empty-light.png' })

console.log('\n=== 2. CREATE A CHANNEL VIA THE PENCIL ===')
await page.getByRole('button', { name: 'New conversation' }).click()
await page.waitForTimeout(400)
check('new-channel sheet opens', await page.getByPlaceholder('e.g. 128 Alder Ave').isVisible())
const radius = await page.getByPlaceholder('e.g. 128 Alder Ave').evaluate((el) => getComputedStyle(el).borderRadius)
check('name field is radius-pill', radius === '22px', radius)
await page.screenshot({ path: SHOTS + 'c2-new-channel-sheet.png' })
await page.getByPlaceholder('e.g. 128 Alder Ave').fill('2047 Columbia')
await page.getByRole('button', { name: 'Create' }).click()
await page.waitForTimeout(1500)
check('drops into the new channel', /2047 Columbia/.test(await page.locator('h2').first().innerText()))

console.log('\n=== 3. CHAT IN THE CHANNEL ===')
await page.getByLabel('Message Jarvis').fill('Reply with exactly the word ORANGE and nothing else.')
await page.getByRole('button', { name: 'Send' }).click()
await waitIdle()
await page.waitForTimeout(500)
let convo = await page.locator('main').innerText()
check('channel got a reply', /orange/i.test(convo), convo.slice(-60).replace(/\n/g, ' '))

console.log('\n=== 4. DIRECT LINE IS A SEPARATE HISTORY ===')
await page.getByRole('button', { name: 'Back to conversations' }).click()
await page.waitForTimeout(900)
text = await page.locator('main').innerText()
check('teaser gone once a channel exists', !/128 Alder Ave/.test(text))
check('channel row rendered', /2047 Columbia/.test(text))
const sparks = await page.evaluate(() => {
  const li = [...document.querySelectorAll('section ul > li')].find((e) => /2047 Columbia/.test(e.textContent || ''))
  return li ? li.querySelectorAll('svg').length : -1
})
// user-created channel: only the overflow (3-dot) icon, no spark mark
check('user channel has NO spark mark', sparks === 1, `${sparks} svg(s) in row`)

await page.getByRole('button', { name: /Ask Jarvis/ }).first().click()
await page.waitForTimeout(900)
convo = await page.locator('main').innerText()
check('direct line does NOT contain the channel message', !/orange/i.test(convo))
await page.getByLabel('Message Jarvis').fill('Reply with exactly the word BANANA and nothing else.')
await page.getByRole('button', { name: 'Send' }).click()
await waitIdle()
await page.waitForTimeout(500)
convo = await page.locator('main').innerText()
check('direct got its own reply', /banana/i.test(convo))
check('direct still has no ORANGE', !/orange/i.test(convo))
await page.screenshot({ path: SHOTS + 'c3-direct-interior.png' })

console.log('\n=== 5. HISTORIES SURVIVE A PAGE RELOAD ===')
await page.reload({ waitUntil: 'networkidle' })
await openAsk()
await page.getByRole('button', { name: /2047 Columbia/ }).first().click()
await page.waitForTimeout(1200)
convo = await page.locator('main').innerText()
check('channel history restored after reload', /orange/i.test(convo) && !/banana/i.test(convo))

console.log('\n=== 6. DURABILITY: SURVIVE AN APP-SERVICE RESTART ===')
execSync('systemctl restart jarvis-app')
await sleep(5000)
await page.reload({ waitUntil: 'networkidle' })
await openAsk()
text = await page.locator('main').innerText()
check('channel still listed after restart', /2047 Columbia/.test(text))
await page.getByRole('button', { name: /2047 Columbia/ }).first().click()
await page.waitForTimeout(1500)
convo = await page.locator('main').innerText()
check('CHANNEL HISTORY SURVIVED RESTART', /orange/i.test(convo), convo.slice(-70).replace(/\n/g, ' '))
await page.getByRole('button', { name: 'Back to conversations' }).click()
await page.waitForTimeout(800)
await page.getByRole('button', { name: /Ask Jarvis/ }).first().click()
await page.waitForTimeout(1200)
convo = await page.locator('main').innerText()
check('DIRECT HISTORY SURVIVED RESTART', /banana/i.test(convo))
check('still isolated after restart', !/orange/i.test(convo))
await page.getByRole('button', { name: 'Back to conversations' }).click()
await page.waitForTimeout(800)

console.log('\n=== 7. RENAME ===')
await page.getByRole('button', { name: /Options for 2047 Columbia/ }).click()
await page.waitForTimeout(300)
check('overflow menu opens', await page.getByRole('menuitem', { name: 'Rename' }).isVisible())
await page.screenshot({ path: SHOTS + 'c4-overflow-menu.png' })
await page.getByRole('menuitem', { name: 'Rename' }).click()
await page.waitForTimeout(400)
await page.getByPlaceholder('e.g. 128 Alder Ave').fill('2047 Columbia Ave')
await page.getByRole('button', { name: 'Save' }).click()
await page.waitForTimeout(1200)
text = await page.locator('main').innerText()
check('renamed in the list', /2047 Columbia Ave/.test(text))
await page.getByRole('button', { name: /2047 Columbia Ave/ }).first().click()
await page.waitForTimeout(1200)
convo = await page.locator('main').innerText()
check('rename kept the history (identity is the id, not the name)', /orange/i.test(convo))
await page.getByRole('button', { name: 'Back to conversations' }).click()
await page.waitForTimeout(800)
await overflowCheck('list with channel')
await page.screenshot({ path: SHOTS + 'c5-list-with-channel-light.png' })

console.log('\n=== 8. DARK MODE ===')
await page.getByRole('button', { name: /Switch to dark mode/ }).click()
await page.waitForTimeout(500)
check('dark bg', (await page.evaluate(() => getComputedStyle(document.body).backgroundColor)) === 'rgb(12, 14, 18)')
await overflowCheck('list dark')
await page.screenshot({ path: SHOTS + 'c6-list-dark.png' })

console.log('\n=== 9. ARCHIVE -> TEASER RETURNS ===')
await page.getByRole('button', { name: /Options for 2047 Columbia Ave/ }).click()
await page.waitForTimeout(300)
await page.getByRole('menuitem', { name: 'Archive' }).click()
await page.waitForTimeout(1500)
text = await page.locator('main').innerText()
check('archived channel gone from the list', !/2047 Columbia Ave/.test(text))
check('teaser RETURNS when all channels archived', /128 Alder Ave/.test(text))
check('direct line unaffected by archiving', /Ask Jarvis/.test(text))
await page.screenshot({ path: SHOTS + 'c7-archived-teaser-back-dark.png' })

console.log('\n=== 10. ARCHIVE IS A FLAG, NOT A DELETE ===')
const dump = execSync(
  `sqlite3_exec(){ :; }; /root/jarvis-app/.venv/bin/python - <<'PY'
import sqlite3, json, os
db = os.path.expanduser("~/.hermes/state/jarvis_threads.db")
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
th = [dict(r) for r in c.execute("SELECT thread_id,name,created_by,archived FROM threads")]
mc = c.execute("SELECT thread_id, COUNT(*) n FROM messages GROUP BY thread_id").fetchall()
print(json.dumps({"threads": th, "messages": {r[0]: r[1] for r in mc}}))
PY`,
  { shell: '/bin/bash' },
).toString().trim()
console.log('    db:', dump)
const db = JSON.parse(dump)
check('archived row still present in the DB', db.threads.some((t) => t.archived === 1))
check('its messages were NOT deleted', Object.values(db.messages).some((n) => n > 0))
check('direct is still created_by=jarvis and unarchived',
  db.threads.some((t) => t.thread_id === 'direct' && t.archived === 0 && t.created_by === 'jarvis'))

console.log('\n=== 11. HYGIENE ===')
check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))

console.log(`\n${'='.repeat(52)}`)
console.log(`RESULT: ${pass} passed, ${fail} failed`)
console.log(`screenshots: ${SHOTS}`)
await browser.close()
process.exit(fail === 0 ? 0 : 1)
