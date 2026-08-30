import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'
const SP = '/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad'
const BASE = 'http://127.0.0.1:8701'
const PINS = JSON.parse(readFileSync(`${SP}/pins.json`, 'utf8'))
const PASS = readFileSync('/root/.hermes/.env', 'utf8')
  .split('\n').find((l) => l.startsWith('APP_PASSPHRASE=')).split('=').slice(1).join('=').trim()
const OUT = `${SP}/verify/vis/`; mkdirSync(OUT, { recursive: true })

const ok = []
const ck = (n, c, x = '') => { console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${x ? ' — ' + x : ''}`); ok.push(!!c) }

async function signIn(page, who) {
  await page.goto(BASE, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  if (who === 'operator') {
    await page.getByRole('button', { name: 'Operator access' }).click()
    await page.getByPlaceholder('Operator passphrase').fill(PASS)
  } else {
    await page.getByRole('button', { name: new RegExp(who, 'i') }).click()
    await page.getByPlaceholder('PIN').fill(PINS[who])
  }
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.waitForTimeout(2500)
}

const overflow = (page) => page.evaluate(() => {
  const d = document.documentElement
  const clipped = (el) => { for (let q = el.parentElement; q && q !== document.body; q = q.parentElement)
    if (['hidden','auto','scroll'].includes(getComputedStyle(q).overflowX)) return true; return false }
  const bad = []
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect()
    if (r.width > 0 && r.right > d.clientWidth + 1 && !clipped(el)) bad.push(el.tagName + '.' + el.className.toString().slice(0, 40))
  }
  return { sw: d.scrollWidth, cw: d.clientWidth, bad: bad.slice(0, 3) }
})

const b = await chromium.launch({ headless: true })

for (const [w, theme] of [[390, 'dark'], [390, 'light'], [1280, 'light'], [1280, 'dark']]) {
  const label = `${w}/${theme}`
  console.log(`\n── ${label} ──────────────────────────────────`)

  let ownerPreviews = []
  // ── owner ────────────────────────────────────────────────────────────────
  let ctx = await b.newContext({ viewport: { width: w, height: 900 }, deviceScaleFactor: 1,
    colorScheme: theme })
  let p = await ctx.newPage()
  await signIn(p, 'spencer')
  await p.getByRole('button', { name: 'Ask', exact: true }).click(); await p.waitForTimeout(1200)
  const listText = await p.locator('body').innerText()
  ownerPreviews = await p.evaluate(async () => {
    const r = await fetch('/api/threads', { credentials: 'include' })
    const j = await r.json()
    return j.threads.map((t) => t.preview).filter((x) => x && x.length > 8)
  })
  ck(`${label} owner: channels listed`, /412 Fairmont St/.test(listText))
  ck(`${label} owner: one direct line only`,
     (listText.match(/Ask Jarvis/g) || []).length === 1)
  await p.screenshot({ path: `${OUT}w3-${w}-${theme}-owner-list.png` })

  await p.getByText('412 Fairmont St').first().click(); await p.waitForTimeout(1200)
  const mbtn = p.getByRole('button', { name: /Members of 412 Fairmont St/ })
  ck(`${label} owner: members control on the channel header`, await mbtn.count() === 1)
  await mbtn.click(); await p.waitForTimeout(700)
  const sheet = await p.getByRole('dialog').innerText()
  ck(`${label} owner: sheet names the members`, /Spencer/.test(sheet) && /you/.test(sheet), sheet.split('\n').slice(0,6).join(' | '))
  ck(`${label} owner: can add someone`, /Add someone/.test(sheet))
  ck(`${label} owner: cannot remove themselves`,
     await p.getByRole('dialog').getByRole('button', { name: 'Remove' }).count() === 0)
  await p.getByRole('button', { name: 'Add someone' }).click(); await p.waitForTimeout(400)
  ck(`${label} owner: warned that history is retroactive`,
     /full history/i.test(await p.getByRole('dialog').innerText()))
  await p.screenshot({ path: `${OUT}w3-${w}-${theme}-members.png` })
  let o = await overflow(p)
  ck(`${label} no overflow (members sheet open)`, o.sw <= o.cw && !o.bad.length, `${o.sw}/${o.cw} ${o.bad}`)
  await ctx.close()

  // ── member ───────────────────────────────────────────────────────────────
  ctx = await b.newContext({ viewport: { width: w, height: 900 }, deviceScaleFactor: 1, colorScheme: theme })
  p = await ctx.newPage()
  await signIn(p, 'martin')
  await p.getByRole('button', { name: 'Ask', exact: true }).click(); await p.waitForTimeout(1200)
  const memberList = await p.locator('body').innerText()
  ck(`${label} member: sees no channel they are not in`, !/412 Fairmont St/.test(memberList))
  ck(`${label} member: still has their own direct line`, /Ask Jarvis/.test(memberList))
  await p.getByRole('button', { name: 'Approvals', exact: true }).click(); await p.waitForTimeout(800)
  ck(`${label} member: approvals screen refuses, in words`,
     /owner/i.test(await p.locator('main').innerText()))
  await p.screenshot({ path: `${OUT}w3-${w}-${theme}-member.png` })
  o = await overflow(p)
  ck(`${label} no overflow (member)`, o.sw <= o.cw && !o.bad.length, `${o.sw}/${o.cw} ${o.bad}`)
  await ctx.close()

  // ── operator ─────────────────────────────────────────────────────────────
  ctx = await b.newContext({ viewport: { width: w, height: 900 }, deviceScaleFactor: 1, colorScheme: theme })
  p = await ctx.newPage()
  await signIn(p, 'operator')
  await p.getByRole('button', { name: 'Ask', exact: true }).click(); await p.waitForTimeout(1200)
  const opList = await p.locator('body').innerText()
  ck(`${label} operator: channels listed`, /412 Fairmont St/.test(opList))
  ck(`${label} operator: no direct line`, !/Ask Jarvis/.test(opList))
  // THE PREVIEWS COME FROM THE API, not from scraping the owner's screen.
  // Two earlier versions of this assertion were wrong in opposite ways: one
  // matched the chrome's "Hi there" greeting, the other matched the static
  // labels "Jarvis keeps one channel per deal". Asking the server what the
  // preview strings actually are removes the guesswork.
  const opMain = await p.locator('main').innerText()
  ck(`${label} operator: NO message preview text on the list`,
     ownerPreviews.length > 0 && !ownerPreviews.some((t) => opMain.includes(t)),
     `checked ${ownerPreviews.length} real previews`)
  ck(`${label} operator: rows say so, rather than looking empty`,
     (opMain.match(/No messages yet/g) || []).length >= 1)
  await p.getByText('412 Fairmont St').first().click(); await p.waitForTimeout(1200)
  const interior = await p.locator('main').innerText()
  ck(`${label} operator: told what it can and cannot see`,
     /not what was said/i.test(interior), interior.split('\n')[1]?.slice(0, 60))
  ck(`${label} operator: no composer`,
     await p.getByRole('button', { name: 'Send' }).count() === 0)
  ck(`${label} operator: can still open membership`,
     await p.getByRole('button', { name: /See who is in this channel/ }).count() === 1)
  await p.getByRole('button', { name: /See who is in this channel/ }).click(); await p.waitForTimeout(700)
  const opSheet = await p.getByRole('dialog').innerText()
  ck(`${label} operator: reads membership`, /Spencer/.test(opSheet))
  ck(`${label} operator: cannot add or remove`,
     !/Add someone/.test(opSheet) && !/Remove/.test(opSheet))
  await p.screenshot({ path: `${OUT}w3-${w}-${theme}-operator.png` })
  o = await overflow(p)
  ck(`${label} no overflow (operator)`, o.sw <= o.cw && !o.bad.length, `${o.sw}/${o.cw} ${o.bad}`)
  await ctx.close()
}

console.log(`\n${ok.filter(Boolean).length}/${ok.length} passed`)
await b.close()
process.exit(ok.every(Boolean) ? 0 : 1)
