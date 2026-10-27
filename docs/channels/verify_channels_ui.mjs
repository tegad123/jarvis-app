#!/usr/bin/env node
/**
 * Team channels in the real built app, two people at once.
 *
 * Starts its own sandbox (port 8714, box /tmp/claude-0/-root/channels-ui)
 * serving this checkout's static/ bundle, with the gateway pointed at a stub
 * this script runs. Dana (owner) and Omar (member) each get a browser:
 *   - Omar's message reaches Dana's open channel with his name on it;
 *   - Dana's sidebar shows the unread dot, then a mention badge for "@Dana";
 *     opening the channel clears both;
 *   - "@ja" offers @jarvis, Enter puts it in the message; Omar sees "Jarvis is
 *     answering Dana" while the stub is slow, then both see the reply;
 *   - a member's channel row has no Rename/Archive menu, the owner's has;
 *   - Settings → Channels is there for the owner, not for a member.
 * Screenshots go to --shots (default: a temp dir).
 *
 *   node docs/channels/verify_channels_ui.mjs [--shots DIR]
 */
import { createServer } from 'node:http'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

// The same Playwright the repo's other browser checks use (docs/front/verify_front.mjs).
import { chromium } from '/usr/local/lib/hermes-agent/node_modules/playwright/index.mjs'

const args = process.argv.slice(2)
const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
if (repo.startsWith('/root/jarvis-app')) { console.error("REFUSING: this is live's tree"); process.exit(2) }
const shots = args.includes('--shots') ? resolve(args[args.indexOf('--shots') + 1]) : mkdtempSync(join(tmpdir(), 'channels-ui-'))
mkdirSync(shots, { recursive: true })
const PORT = 8714
const BASE = `http://127.0.0.1:${PORT}`
const BOX = '/tmp/claude-0/-root/channels-ui'
const PINS = Object.fromEntries(Object.entries(JSON.parse(readFileSync('/opt/jarvis-verify-fixture/SEEDED_PINS.json', 'utf8')))
  .map(([k, v]) => [k, typeof v === 'object' ? v.pin : v]))
const RUN = Math.random().toString(16).slice(2, 8)

let pass = 0, total = 0
const check = (label, ok, extra = '') => { total++; if (ok) pass++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${!ok && extra ? ` — ${extra}` : ''}`) }

// ── stub gateway: records each call, answers slowly when asked to ──────────
const calls = []
const stub = createServer((req, res) => {
  let raw = ''
  req.on('data', (c) => (raw += c))
  req.on('end', async () => {
    const body = JSON.parse(raw || '{}')
    calls.push(body)
    if (JSON.stringify(body.messages?.at(-1)?.content ?? '').includes('slowly')) await new Promise((r) => setTimeout(r, 4000))
    const lines = body.messages.slice(1).map((m) => (typeof m.content === 'string' ? m.content : '')).join('\n').split('\n').length
    res.writeHead(200, { 'Content-Type': 'text/event-stream' })
    res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: `Stub reply: I read ${lines} lines of this channel.` } }] })}\n\n`)
    res.write(`data: ${JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }], usage: {} })}\n\n`)
    res.end('data: [DONE]\n\n')
  })
})
await new Promise((r) => stub.listen(0, '127.0.0.1', r))
const STUB = stub.address().port

const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^(JARVIS_|TASKS_)/.test(k) && k !== 'HERMES_HOME'))
Object.assign(env, { TASKS_PORT: String(PORT), TASKS_SANDBOX: BOX, HERMES_API_URL: `http://127.0.0.1:${STUB}`, VERIFY_APP_ROOT: repo })
const sandbox = (cmd) => execFileSync('bash', [join(repo, 'docs/tasks/tasks_sandbox.sh'), cmd], { env, encoding: 'utf8' })
try { sandbox('stop') } catch { /* nothing running */ }
console.log(sandbox('start').trim().split('\n').at(-1))
console.log(`channels UI — ${BASE}, stub gateway :${STUB}, shots ${shots}\n`)

const browser = await chromium.launch()
async function person(who) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const r = await ctx.request.post(`${BASE}/api/login`, { data: { person_key: who, pin: PINS[who] } })
  if (!r.ok()) throw new Error(`login ${who}: ${r.status()}`)
  const page = await ctx.newPage()
  await page.goto(BASE)
  return { ctx, page, api: ctx.request }
}

try {
  const dana = await person('dana')
  const omar = await person('omar')
  const name = `Deal ${RUN}`
  const ch = (await (await dana.api.post(`${BASE}/api/threads`, { data: { name } })).json()).thread.thread_id
  await dana.api.post(`${BASE}/api/threads/${ch}/members`, { data: { person_key: 'omar' } })
  const people = (await (await dana.api.get(`${BASE}/api/team/directory`)).json()).people
  const N = Object.fromEntries(people.map((p) => [p.person_key, p.name]))
  for (const p of [dana.page, omar.page]) await p.reload()

  const row = (p) => p.locator('nav').getByRole('button', { name: new RegExp(`^${name}`) }).first()
  console.log('── people talk ─────────────────────────────────────────')
  await row(omar.page).click()
  await row(dana.page).click()
  const box = (p) => p.getByRole('combobox', { name: `Message ${name}` })
  await box(omar.page).fill(`hi team, inspection is Thursday ${RUN}`)
  await box(omar.page).press('Enter')
  const seen = dana.page.getByText(`hi team, inspection is Thursday ${RUN}`)
  await seen.waitFor({ timeout: 8000 }).catch(() => {})
  check("Omar's message appears in Dana's open channel", await seen.isVisible())
  check('   under his name', await dana.page.getByText(N.omar, { exact: true }).first().isVisible())
  check('Omar sees no Jarvis reply to a plain message', calls.length === 0, `${calls.length} calls`)
  await dana.page.screenshot({ path: join(shots, '1-dana-sees-omar.png') })

  console.log('\n── unread and mentions in the sidebar ──────────────────')
  await dana.page.locator('nav').getByRole('button', { name: /^Ask Jarvis/ }).first().click()
  await box(omar.page).fill(`one more thing ${RUN}`)
  await box(omar.page).press('Enter')
  const dot = row(dana.page).getByRole('img', { name: 'Unread' })
  await dot.waitFor({ timeout: 15000 }).catch(() => {})
  check("Dana's sidebar shows the channel unread", await dot.isVisible())
  await box(omar.page).fill(`@${N.dana} can you check the seller's disclosure?`)
  await box(omar.page).press('Enter')
  const badge = row(dana.page).getByText(/mention of you/)
  await badge.waitFor({ state: 'attached', timeout: 15000 }).catch(() => {})
  check('   then a mention badge for @Dana', (await badge.count()) === 1)
  await dana.page.screenshot({ path: join(shots, '2-dana-sidebar-mention.png') })
  await row(dana.page).click()
  await dana.page.waitForTimeout(1500)
  check('opening the channel clears both', (await row(dana.page).getByRole('img', { name: 'Unread' }).count()) === 0
        && (await row(dana.page).getByText(/mention of you/).count()) === 0)
  check('   and @Dana is highlighted as hers', (await dana.page.locator('[data-mention="self"]').count()) >= 1)

  console.log('\n── @jarvis ─────────────────────────────────────────────')
  await box(dana.page).click()
  await box(dana.page).pressSequentially('@ja')
  const opt = dana.page.getByRole('option', { name: /@jarvis/ })
  await opt.waitFor({ timeout: 3000 }).catch(() => {})
  check('"@ja" offers @jarvis', await opt.isVisible())
  await dana.page.screenshot({ path: join(shots, '3-dana-mention-list.png') })
  await box(dana.page).press('Enter')
  check('   Enter puts it in the message', (await box(dana.page).inputValue()) === '@jarvis ')
  await box(dana.page).pressSequentially('what did Omar need, slowly please')
  await box(dana.page).press('Enter')
  const busy = omar.page.getByText(`Jarvis is answering ${N.dana}`)
  await busy.waitFor({ state: 'attached', timeout: 6000 }).catch(() => {})
  check(`Omar sees "Jarvis is answering ${N.dana}" while it works`, (await busy.count()) === 1)
  await omar.page.screenshot({ path: join(shots, '4-omar-sees-jarvis-working.png') })
  const reply = (p) => p.getByText(/^Stub reply: I read \d+ lines/)
  await reply(dana.page).waitFor({ timeout: 10000 }).catch(() => {})
  await reply(omar.page).waitFor({ timeout: 10000 }).catch(() => {})
  check('both see the reply', (await reply(dana.page).count()) === 1 && (await reply(omar.page).count()) === 1)
  const ctx = calls.at(-1)?.messages?.slice(1).map((m) => m.content).join('\n') ?? ''
  check("Jarvis was sent Omar's lines by name", calls.length === 1 && ctx.includes(`${N.omar}: hi team, inspection is Thursday ${RUN}`), `${calls.length} calls`)
  await dana.page.screenshot({ path: join(shots, '5-dana-jarvis-reply.png') })
  await omar.page.screenshot({ path: join(shots, '6-omar-jarvis-reply.png') })

  console.log('\n── who may change a channel ────────────────────────────')
  check("Omar's channel row has no options menu", (await omar.page.getByRole('button', { name: `Options for ${name}` }).count()) === 0)
  check('   Dana\'s has one (presence twin)', (await dana.page.getByRole('button', { name: `Options for ${name}` }).count()) === 1)
  await dana.page.locator('nav').getByRole('button', { name: 'Settings' }).click()
  const radio = dana.page.getByRole('radio', { name: /Only when someone types @jarvis/ })
  await radio.waitFor({ timeout: 5000 }).catch(() => {})
  check('Settings → Channels for the owner, set to @jarvis only', await radio.isChecked().catch(() => false))
  await dana.page.locator('main').getByRole('heading', { name: 'Channels' }).scrollIntoViewIfNeeded().catch(() => {})
  await dana.page.screenshot({ path: join(shots, '7-dana-settings-channels.png') })
  await omar.page.locator('nav').getByRole('button', { name: 'Settings' }).click()
  await omar.page.waitForTimeout(1500)
  // In the page itself: the sidebar has a "Channels" heading of its own.
  check('   and not for a member', (await omar.page.locator('main').getByRole('heading', { name: 'Channels' }).count()) === 0
        && (await omar.page.locator('main').getByRole('heading', { name: 'Your Google account' }).count()) === 1)
} finally {
  await browser.close()
  stub.close()
  try { sandbox('stop') } catch { /* already down */ }
}
console.log(`\n${pass}/${total} passed\nshots: ${shots}`)
process.exit(pass === total ? 0 : 1)
