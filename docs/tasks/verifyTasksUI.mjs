/**
 * The Tasks tab, in a real browser, as three different people.
 *
 * WHAT THIS IS FOR. The API suite proves the rules are enforced; this proves
 * the screen tells the truth about them. A member who can see an Archive
 * button they will always be refused, or an operator shown a blank detail line
 * that looks like nobody wrote one, is a screen that lies about the backend
 * even though the backend is right.
 *
 * Run:  node docs/tasks/verifyTasksUI.mjs --base http://127.0.0.1:8711
 */
import { chromium } from '/usr/local/lib/hermes-agent/node_modules/playwright/index.mjs'
import { readFileSync, mkdirSync } from 'node:fs'

const arg = (n, d) => {
  const i = process.argv.indexOf(n)
  return i > -1 ? process.argv[i + 1] : d
}
const BASE = arg('--base', 'http://127.0.0.1:8711')
const PINS = JSON.parse(readFileSync(arg('--pins', '/opt/jarvis-verify-fixture/SEEDED_PINS.json'), 'utf8'))
const pinOf = (who) => (typeof PINS[who] === 'object' ? PINS[who].pin : PINS[who])
const SHOTS = new URL('./vis/', import.meta.url).pathname
mkdirSync(SHOTS, { recursive: true })

function passphrase() {
  for (const l of readFileSync('/opt/jarvis-verify-fixture/hermes/.env', 'utf8').split('\n')) {
    const m = /^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(l)
    if (m) return m[1].trim().replace(/^["']|["']$/g, '')
  }
  return null
}

const ok = []
const ck = (n, c, x = '') => {
  console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${x ? ' — ' + x : ''}`)
  ok.push(!!c)
}

async function signIn(page, who) {
  await page.goto(BASE, { waitUntil: 'networkidle' })
  await page.waitForTimeout(700)
  if (who === 'operator') {
    await page.getByRole('button', { name: 'Operator access' }).click()
    await page.getByPlaceholder('Operator passphrase').fill(passphrase())
  } else {
    await page.getByRole('button', { name: new RegExp(who, 'i') }).first().click()
    await page.getByPlaceholder('PIN').fill(pinOf(who))
  }
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.waitForTimeout(2200)
}

const openTasks = async (page) => {
  // Scoped to the nav: the Feed screen's skill filter also renders a chip
  // labelled "tasks", and an unscoped lookup matches both.
  await page.locator('nav').getByRole('button', { name: 'Tasks', exact: true }).click()
  await page.waitForTimeout(1200)
}

// A row must never be wider than the column that holds it. This has bitten
// every list screen in this app at least once.
async function overflow(page) {
  // The house gate, verbatim in behaviour: an element only overflows if no
  // ancestor CLIPS it. Without that clause the decorative ambient blobs — which
  // are deliberately larger than the viewport inside an overflow-hidden
  // parent — read as a layout bug on every screen in the app.
  return await page.evaluate(() => {
    const d = document.documentElement
    const clipped = (el) => {
      for (let q = el.parentElement; q && q !== document.body; q = q.parentElement)
        if (['hidden', 'auto', 'scroll'].includes(getComputedStyle(q).overflowX)) return true
      return false
    }
    const bad = []
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect()
      if (r.width > 0 && r.right > d.clientWidth + 1 && !clipped(el))
        bad.push(el.tagName + '.' + String(el.className).slice(0, 40))
    }
    return bad.slice(0, 3)
  })
}

const browser = await chromium.launch()
console.log(`Tasks UI — ${BASE}\n`)

try {
  // ── the owner: the full write surface ──────────────────────────────────────
  for (const [w, theme] of [[390, 'dark'], [1280, 'light']]) {
    const ctx = await browser.newContext({
      viewport: { width: w, height: w === 390 ? 844 : 800 },
      colorScheme: theme,
    })
    const page = await ctx.newPage()
    const errors = []
    // Collected only after sign-in: the app probes /api/session on load and a
    // 401 there is how it LEARNS you are signed out. Counting it would make
    // every screen fail a console gate for working correctly.
    await signIn(page, 'dana')
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
    await openTasks(page)
    ck(`[${w} ${theme}] the Tasks tab exists and opens`,
      await page.getByRole('heading', { name: 'Tasks' }).isVisible())
    await page.screenshot({ path: `${SHOTS}tasks-owner-${w}-${theme}.png`, fullPage: true })

    if (w === 1280) {
      const title = `UI check ${Date.now()}`
      await page.getByRole('button', { name: 'New task' }).click()
      await page.getByPlaceholder('What needs doing').fill(title)
      await page.getByRole('button', { name: 'Add', exact: true }).click()
      await page.waitForTimeout(1800)
      ck('the owner can add a task and see it appear',
        await page.getByText(title, { exact: false }).first().isVisible())

      const chip = page.getByRole('button', { name: /^Status: / }).first()
      const before = await chip.getAttribute('aria-label')
      await chip.click()
      await page.waitForTimeout(1500)
      const after = await page.getByRole('button', { name: /^Status: / }).first().getAttribute('aria-label')
      ck('tapping a status chip moves the task on', before !== after, `${before} -> ${after}`)

      ck('the owner gets the management controls',
        (await page.getByLabel('Assignee').count()) > 0 &&
        (await page.getByRole('button', { name: 'Archive' }).count()) > 0)

      // SCOPED TO OUR OWN ROW. .first() archives whatever sorts to the top —
      // which is someone else's task, since the list puts overdue and
      // high-priority work first and this one is neither. The first version of
      // this archived an unrelated task and then asserted about ours.
      const mine = page.locator('li').filter({ hasText: title })
      await mine.getByRole('button', { name: 'Archive' }).click()
      await page.waitForTimeout(1500)
      ck('archiving removes the row from the default list',
        (await page.locator('li').filter({ hasText: title }).count()) === 0)
    }

    const bad = await overflow(page)
    ck(`[${w} ${theme}] nothing overflows the column`, bad.length === 0, bad.join(', '))
    ck(`[${w} ${theme}] no console errors`, errors.length === 0, errors.slice(0, 1).join(''))
    await ctx.close()
  }

  // ── the member: their own work only ────────────────────────────────────────
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } })
    const page = await ctx.newPage()
    await signIn(page, 'omar')
    await openTasks(page)
    const rows = await page.locator('li').count()
    ck('a member sees the whole team list', rows > 0, `${rows} rows`)
    ck('a member gets no assign control', (await page.getByLabel('Assignee').count()) === 0)
    ck('a member gets no archive control',
      (await page.getByRole('button', { name: 'Archive' }).count()) === 0)
    await page.screenshot({ path: `${SHOTS}tasks-member-1280.png`, fullPage: true })
    await ctx.close()
  }

  // ── the operator: structure, and it says so ────────────────────────────────
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } })
    const page = await ctx.newPage()
    await signIn(page, 'operator')
    await openTasks(page)
    ck('the operator can read the list', (await page.locator('li').count()) > 0)
    ck('and is told the details are withheld, not missing',
      await page.getByText(/Structure only/i).isVisible())
    ck('the operator gets no create control',
      (await page.getByRole('button', { name: 'New task' }).count()) === 0)
    ck('no status control either',
      (await page.getByRole('button', { name: /^Status: / }).count()) === 0)
    const payload = await page.evaluate(async () =>
      (await fetch('/api/tasks', { credentials: 'include' })).json())
    ck('the API agrees the bodies are hidden', payload.bodies_visible === false,
      String(payload.bodies_visible))
    ck('and every detail really is null',
      (payload.tasks || []).every((t) => t.detail === null))
    await page.screenshot({ path: `${SHOTS}tasks-operator-1280.png`, fullPage: true })
    await ctx.close()
  }
} finally {
  await browser.close()
}

console.log(`\n${ok.filter(Boolean).length}/${ok.length} passed`)
process.exit(ok.every(Boolean) ? 0 : 1)
