#!/usr/bin/env node
/**
 * The open-house lead tracker in the real built app (static/), signed in for
 * real, a member and the owner side by side.
 *
 * Starts docs/leads/leads_ui_server.py on 127.0.0.1:8719 against a temp box
 * (Sentinel off, nothing can send). The follow-up clock is pinned to
 * 2026-10-28. Checks, each against the API as well as the screen:
 *   - Omar (member) lands on Today: his two due leads, overdue first, not his
 *     later one, not Maya's; no "Who" chips (the owner has them — twin);
 *   - quick add: a Warm lead is his, due in two days (+1 lead in the ledger);
 *   - Log contact on Sarah, "Didn't reach them": the timeline says so, the
 *     API says No Response, next in 7 days, +1 timeline row; Today drops to 1;
 *   - Settings → Leads is the owner's, not a member's (twin: the member's
 *     Settings still renders);
 *   - the owner imports a .csv: preview, Import held until the unknown agent
 *     is answered, then 2 new (+2 in the ledger); the same file again: 0 new.
 *
 *   node docs/leads/verify_leads_ui.mjs [--shots DIR]
 *
 * Playwright: the server's copy (/usr/local/lib/hermes-agent) when present,
 * else LT_PLAYWRIGHT (a playwright or playwright-core entry), said aloud.
 * Chrome: LT_CHROME, else Playwright's own. Python packages: LT_PYTHONPATH.
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, mkdirSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
if (repo.startsWith('/root/jarvis-app')) { console.error("REFUSING: this is live's tree"); process.exit(2) }
const args = process.argv.slice(2)
const shots = args.includes('--shots') ? resolve(args[args.indexOf('--shots') + 1]) : mkdtempSync(join(tmpdir(), 'leads-ui-'))
mkdirSync(shots, { recursive: true })

const SERVER_PW = '/usr/local/lib/hermes-agent/node_modules/playwright/index.mjs'
let pwPath = SERVER_PW
if (!existsSync(SERVER_PW)) {
  if (!process.env.LT_PLAYWRIGHT) { console.error(`No Playwright at ${SERVER_PW}; set LT_PLAYWRIGHT`); process.exit(2) }
  pwPath = resolve(process.env.LT_PLAYWRIGHT)
  console.log(`NOTE: Playwright from LT_PLAYWRIGHT (${pwPath}), not the server's copy`)
}
const pw = await import(pathToFileURL(pwPath).href)
const chromium = pw.chromium ?? pw.default.chromium

const PORT = 8719
const BASE = `http://127.0.0.1:${PORT}`
const TODAY = '2026-10-28'
const BOX = mkdtempSync(join(tmpdir(), 'leads-ui-box-'))

let pass = 0, total = 0
const check = (label, ok, extra = '') => { total++; if (ok) pass++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${!ok && extra !== '' ? ` — ${extra}` : ''}`) }

const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^(JARVIS_|HERMES_|ANTHROPIC|APP_)/.test(k)))
env.LT_TODAY = TODAY
if (process.env.LT_PYTHONPATH) env.PYTHONPATH = process.env.LT_PYTHONPATH
const server = spawn('python3', [join(repo, 'docs/leads/leads_ui_server.py'), BOX, String(PORT)], { env, stdio: ['ignore', 'ignore', 'pipe'] })
let serverErr = ''
server.stderr.on('data', (d) => (serverErr += d))
let up = false
for (let i = 0; i < 60 && !up; i++) {
  await new Promise((r) => setTimeout(r, 500))
  up = await fetch(`${BASE}/api/ping`).then((r) => r.ok, () => false)
}
if (!up) { console.error(`server did not start:\n${serverErr.slice(-2000)}`); server.kill(); process.exit(1) }
const PINS = JSON.parse(readFileSync(join(BOX, 'pins.json'), 'utf8'))
console.log(`leads UI — ${BASE}, today pinned to ${TODAY}, shots ${shots}\n`)

const browser = await chromium.launch(process.env.LT_CHROME ? { executablePath: process.env.LT_CHROME } : {})
async function person(who) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } })
  const r = await ctx.request.post(`${BASE}/api/login`, { data: { person_key: who, pin: PINS[who] } })
  if (!r.ok()) throw new Error(`login ${who}: ${r.status()}`)
  const page = await ctx.newPage()
  await page.goto(BASE)
  return { page, api: ctx.request }
}
const leadsOf = async (p) => (await (await p.api.get(`${BASE}/api/leads`)).json()).leads
const goLeads = async (p) => {
  await p.page.locator('nav').getByRole('button', { name: /^Leads/ }).first().click()
  await p.page.getByRole('group', { name: 'Show' }).waitFor({ timeout: 8000 })
}
const goSettings = async (p) => {
  await p.page.locator('nav').getByRole('button', { name: /^Settings/ }).first().click()
  await p.page.locator('main').getByRole('heading', { name: 'Your Google account' }).waitFor({ timeout: 8000 }).catch(() => {})
}
const rowNames = async (page) => page.locator('main').getByRole('button', { name: /^(Sarah Lee|Olly Overdue|Wes Later|Mo Maya|Tina Open)$/ }).allInnerTexts()
const plus = (days) => new Date(Date.parse(`${TODAY}T12:00:00Z`) + days * 864e5).toISOString().slice(0, 10)

try {
  const omar = await person('omar')
  const dana = await person('dana')

  console.log('── Today, as a member ──────────────────────────────────')
  await goLeads(omar)
  const show = omar.page.getByRole('group', { name: 'Show' })
  const todayChip = show.getByRole('button', { name: /^Today \(\d+\)$/ })
  check('Leads opens on Today, pressed, with his count (2)',
    (await todayChip.innerText()) === 'Today (2)' && (await todayChip.getAttribute('aria-pressed')) === 'true',
    await todayChip.innerText())
  const names = await rowNames(omar.page)
  check('   his two due leads, overdue first', JSON.stringify(names) === JSON.stringify(['Olly Overdue', 'Sarah Lee']), JSON.stringify(names))
  check("   not his later one, not Maya's", !names.includes('Wes Later') && !names.includes('Mo Maya'))
  check('   and the API agrees: his three, none of Maya\'s', JSON.stringify((await leadsOf(omar)).map((l) => l.name).sort())
    === JSON.stringify(['Olly Overdue', 'Sarah Lee', 'Wes Later']))
  check('   no "Who" chips for a member', (await omar.page.getByRole('group', { name: 'Who' }).count()) === 0)
  await omar.page.screenshot({ path: join(shots, '1-omar-today.png') })

  console.log('\n── quick add ───────────────────────────────────────────')
  const before = (await leadsOf(omar)).length
  await omar.page.getByRole('button', { name: 'New lead' }).click()
  const form = omar.page.getByTestId('quick-add')
  await form.getByLabel('Name').fill('Tina Open')
  await form.getByLabel('Phone').fill('512-555-0199')
  await form.getByLabel('Property').fill('1109 E 24th St')
  await form.getByRole('group', { name: 'How hot' }).getByRole('button', { name: 'Warm' }).click()
  check('   the form says how Jarvis picks the first follow-up', await form.getByText(/tomorrow if hot, in two days if warm/).isVisible())
  await omar.page.screenshot({ path: join(shots, '2-omar-quick-add.png') })
  await form.getByRole('button', { name: 'Add' }).click()
  await form.waitFor({ state: 'detached', timeout: 8000 }).catch(() => {})
  await omar.page.waitForTimeout(800)
  const after = await leadsOf(omar)
  const tina = after.find((l) => l.name === 'Tina Open')
  check('quick add: +1 lead in the ledger', after.length - before === 1, `${before} -> ${after.length}`)
  check(`   Warm -> due ${plus(2)}, his, New`, tina?.next_follow_up === plus(2) && tina?.status === 'New' && tina?.agent === 'Omar',
    JSON.stringify(tina && [tina.next_follow_up, tina.status, tina.agent]))
  check('   not due today, so Today stays (2)', (await todayChip.innerText()) === 'Today (2)', await todayChip.innerText())

  console.log('\n── Log contact ─────────────────────────────────────────')
  const sarahId = after.find((l) => l.name === 'Sarah Lee').lead_id
  const t0 = (await (await omar.api.get(`${BASE}/api/leads/${sarahId}`)).json()).timeline.length
  await omar.page.locator('main').getByRole('button', { name: 'Sarah Lee', exact: true }).click()
  const sheet = omar.page.getByTestId('lead-sheet')
  await sheet.waitFor({ timeout: 8000 })
  check('the lead opens: phone is a tap-to-call link', (await sheet.locator('a[href^="tel:"]').count()) === 1)
  await sheet.getByRole('group', { name: 'What' }).getByRole('button', { name: 'Call' }).click()
  await sheet.getByLabel(/Didn.t reach them/).check()
  await sheet.getByLabel('Note').fill('left a voicemail')
  await sheet.getByRole('button', { name: 'Log contact' }).click()
  const tl = sheet.getByTestId('lead-timeline')
  await tl.getByText('Called · no answer').waitFor({ timeout: 8000 }).catch(() => {})
  check('   the timeline shows "Called · no answer" and the note', await tl.getByText('Called · no answer').isVisible()
    && await tl.getByText('left a voicemail').isVisible())
  await omar.page.screenshot({ path: join(shots, '3-omar-logged-contact.png') })
  const d = await (await omar.api.get(`${BASE}/api/leads/${sarahId}`)).json()
  check(`   API: No Response, next ${plus(7)}, +1 timeline row`, d.lead.status === 'No Response'
    && d.lead.next_follow_up === plus(7) && d.timeline.length - t0 === 1,
    JSON.stringify([d.lead.status, d.lead.next_follow_up, t0, d.timeline.length]))
  await omar.page.reload()
  await goLeads(omar)
  check('   Today drops to (1)', (await omar.page.getByRole('group', { name: 'Show' }).getByRole('button', { name: /^Today/ }).innerText()) === 'Today (1)')

  console.log('\n── the owner ───────────────────────────────────────────')
  await goLeads(dana)
  const who = dana.page.getByRole('group', { name: 'Who' })
  const chips = await who.getByRole('button').allInnerTexts()
  check('the owner gets "Who" chips with each agent\'s due count', chips.includes('All agents')
    && chips.some((c) => /^Omar.* · 1$/.test(c)) && chips.some((c) => /^Maya.* · 1$/.test(c)), JSON.stringify(chips))
  check("   and Today holds both agents' (2)", (await dana.page.getByRole('group', { name: 'Show' }).getByRole('button', { name: /^Today/ }).innerText()) === 'Today (2)')
  await dana.page.screenshot({ path: join(shots, '4-dana-today-who.png') })

  console.log('\n── Settings → Leads import ─────────────────────────────')
  await goSettings(omar)
  check('a member has no Settings → Leads', (await omar.page.locator('main').getByRole('heading', { name: 'Leads', exact: true }).count()) === 0
    && (await omar.page.getByTestId('lead-import-file').count()) === 0)
  check('   (twin) the member\'s Settings did render', (await omar.page.locator('main').getByRole('heading', { name: 'Your Google account' }).count()) === 1)
  await goSettings(dana)
  check('the owner has Settings → Leads', (await dana.page.getByTestId('lead-import-file').count()) === 1)
  const csv = Buffer.from('Lead Name,Phone,Assigned Agent\nCara Csv,512-555-0401,Omar\nBo Blake,512-555-0402,Blake\n')
  const L0 = (await leadsOf(dana)).length
  await dana.page.getByTestId('lead-import-file').setInputFiles({ name: 'Open house.csv', mimeType: 'text/csv', buffer: csv })
  const pv = dana.page.getByTestId('lead-import-preview')
  await pv.waitFor({ timeout: 8000 }).catch(() => {})
  check('preview: 2 found, 2 new', await pv.getByText(/^2 leads found · 2 new · 0 already imported/).isVisible())
  const importBtn = dana.page.getByRole('button', { name: 'Import 2 leads' })
  check('   Import held until "Blake" is answered', await importBtn.isDisabled())
  check('   and nothing written yet', (await leadsOf(dana)).length === L0)
  await dana.page.getByLabel('Agent for Blake').selectOption('__nobody')
  await pv.scrollIntoViewIfNeeded()
  await dana.page.screenshot({ path: join(shots, '5-dana-import-preview.png') })
  await importBtn.click()
  await dana.page.getByText('Imported: 2 new, 0 already here.').waitFor({ timeout: 8000 }).catch(() => {})
  check('import: "2 new"', await dana.page.getByText('Imported: 2 new, 0 already here.').isVisible())
  const L1 = await leadsOf(dana)
  check('   ledger: +2 leads, Cara is Omar\'s', L1.length - L0 === 2 && L1.find((l) => l.name === 'Cara Csv')?.agent === 'Omar',
    `${L0} -> ${L1.length}`)
  await dana.page.screenshot({ path: join(shots, '6-dana-imported.png') })
  await dana.page.getByTestId('lead-import-file').setInputFiles({ name: 'Open house.csv', mimeType: 'text/csv', buffer: csv })
  await pv.waitFor({ timeout: 8000 }).catch(() => {})
  check('the same file again: 0 new, 2 already imported', await pv.getByText(/^2 leads found · 0 new · 2 already imported/).isVisible())
} finally {
  await browser.close()
  server.kill()
}
console.log(`\n${pass}/${total} passed\nshots: ${shots}`)
process.exit(pass === total ? 0 : 1)
