/**
 * W1 — the Leads tab, verified by INTERACTING with it.
 *
 * TWO TARGETS, because the screen has two branches and only one of them was
 * ever tested:
 *
 *   --store <url>   app-native tenant. Runs the full interaction suite:
 *                   create, status cycle, temperature cycle, archive. These
 *                   WRITE, so this target must be disposable.
 *   --sheet <url>   sheet-backed tenant. Runs the READ-ONLY branch: the three
 *                   controls must render as inert markup and the create button
 *                   must be absent. Asserts only; writes nothing.
 *
 * WHY THE SPLIT IS NOT OPTIONAL. `canEdit = data.source === 'store'`
 * (screens/Leads.tsx) is the whole gate, and until now every run of this file
 * exercised the true side. The false side is what the LIVE OWNER sees in
 * production. A sandbox cannot stand in for it either: the sheet reader needs
 * outbound network to Google, so a sandboxed sheet tenant renders its 503
 * error state — four filter chips and nothing else — which passes an
 * "absence of controls" test for entirely the wrong reason. So the sheet
 * target is the live app, and the sheet assertions are read-only BY
 * CONSTRUCTION, which is what makes pointing at live acceptable.
 *
 * REFUSALS, both directions:
 *   - the write suite refuses a target reporting source=sheet (it would 409,
 *     and worse, it would mean we thought a live tenant was disposable);
 *   - the read-only suite refuses a target reporting source=store, because
 *     absent controls there would be a real regression, not the branch we
 *     came to test.
 *
 * LOGIN IS W3's. The old version filled a `Passphrase` placeholder that has
 * not existed since per-user auth shipped — this file would have failed on its
 * first line. It now drives the person picker + PIN, and falls back to the
 * break-glass operator only when no PIN file is supplied.
 *
 *   node verifyLeadsUI.mjs --store http://127.0.0.1:8701 \
 *                          --sheet http://127.0.0.1:8700 \
 *                          --pins  /path/to/pins.json
 */
import { chromium } from 'playwright'
import { readFileSync, mkdirSync, existsSync } from 'node:fs'

const argv = process.argv.slice(2)
const arg = (name) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : null }
const STORE = arg('--store')
const SHEET = arg('--sheet')
const PINS_PATH = arg('--pins') || process.env.JARVIS_TEST_PINS || ''
const OUT = new URL('./vis/', import.meta.url).pathname
mkdirSync(OUT, { recursive: true })

if (!STORE && !SHEET) {
  console.error('usage: verifyLeadsUI.mjs [--store <url>] [--sheet <url>] [--pins <file>]')
  process.exit(2)
}

const ok = []
const ck = (n, c, x = '') => { console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${x ? ' — ' + x : ''}`); ok.push(!!c) }

function passphrase() {
  for (const l of readFileSync('/root/.hermes/.env', 'utf8').split('\n')) {
    const m = /^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(l)
    if (m) return m[1].trim().replace(/^["']|["']$/g, '')
  }
  return null
}

const PINS = PINS_PATH && existsSync(PINS_PATH)
  ? JSON.parse(readFileSync(PINS_PATH, 'utf8'))
  : null

async function signInOperator(page, base) {
  await page.goto(base, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  await page.getByRole('button', { name: 'Operator access' }).click()
  await page.getByPlaceholder('Operator passphrase').fill(passphrase())
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.waitForTimeout(2500)
}

async function signIn(page, base) {
  await page.goto(base, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  if (PINS) {
    // The real path: pick a person, type their PIN. Whoever the file lists
    // first will do — the Leads gate keys on the tenant's SOURCE, not on role.
    const who = Object.keys(PINS)[0]
    await page.getByRole('button', { name: new RegExp(who, 'i') }).click()
    await page.getByPlaceholder('PIN').fill(PINS[who])
  } else {
    await page.getByRole('button', { name: 'Operator access' }).click()
    await page.getByPlaceholder('Operator passphrase').fill(passphrase())
  }
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.waitForTimeout(2500)
}

/** Ask the API what the tenant is, rather than inferring it from the DOM. */
async function leadsSource(page) {
  return page.evaluate(async () => {
    const r = await fetch('/api/leads', { credentials: 'include' })
    const j = await r.json().catch(() => ({}))
    return { source: j.source ?? null, count: j.count ?? 0, error: j.error ?? null }
  })
}

const overflow = (page) => page.evaluate(() => {
  const d = document.documentElement
  const clipped = (el) => { for (let q = el.parentElement; q && q !== document.body; q = q.parentElement)
    if (['hidden','auto','scroll'].includes(getComputedStyle(q).overflowX)) return true; return false }
  const bad = []
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect()
    if (r.width > 0 && r.right > d.clientWidth + 1 && !clipped(el)) bad.push(el.tagName)
  }
  return { sw: d.scrollWidth, cw: d.clientWidth, bad: bad.slice(0, 3) }
})

const b = await chromium.launch({ headless: true })

/* ── APP-NATIVE: the interaction suite ─────────────────────────────────── */
if (STORE) {
  console.log(`\n=== APP-NATIVE (writes) — ${STORE} ===`)
  const ctx = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
  const p = await ctx.newPage()
  const errs = []
  p.on('console', (m) => { if (m.type() === 'error' && !/401/.test(m.text())) errs.push(m.text()) })
  p.on('pageerror', (e) => errs.push(String(e)))

  await signIn(p, STORE)
  await p.getByRole('button', { name: /^Leads$/i }).last().click()
  await p.waitForTimeout(1500)

  const src = await leadsSource(p)
  if (src.source !== 'store') {
    console.error(`REFUSING: --store target reports source=${src.source}. The suite below `
      + `CREATES and ARCHIVES leads; running it against a sheet-backed tenant would 409 at `
      + `best and means we mistook a live tenant for a disposable one.`)
    process.exit(3)
  }

  ck('app-native source shows editing controls',
     (await p.getByRole('button', { name: 'New lead' }).count()) > 0)

  const firstStatus = p.getByRole('button', { name: /^Status: / }).first()
  ck('status chip is a control', (await firstStatus.count()) > 0)
  const before = await firstStatus.getAttribute('aria-label')
  await firstStatus.click(); await p.waitForTimeout(1800)
  const after = await p.getByRole('button', { name: /^Status: / }).first().getAttribute('aria-label')
  ck('tapping the status chip persists a change', before !== after, `${before} -> ${after}`)

  const tempBtn = p.getByRole('button', { name: /^Temperature: / }).first()
  const tBefore = await tempBtn.getAttribute('aria-label')
  await tempBtn.click(); await p.waitForTimeout(1800)
  const tAfter = await p.getByRole('button', { name: /^Temperature: / }).first().getAttribute('aria-label')
  ck('temperature cycles too', tBefore !== tAfter, `${tBefore} -> ${tAfter}`)

  await p.getByRole('button', { name: 'New lead' }).click(); await p.waitForTimeout(400)
  await p.getByPlaceholder('Name').fill('Verification Lead')
  await p.getByRole('button', { name: 'Add' }).click(); await p.waitForTimeout(2000)
  ck('create adds a lead', (await p.getByText('Verification Lead').count()) > 0)

  const row = p.locator('ul > li').filter({ hasText: 'Verification Lead' })
  await row.getByRole('button', { name: 'Archive' }).first().click(); await p.waitForTimeout(2000)
  ck('archive removes it from the active list', (await p.getByText('Verification Lead').count()) === 0)

  await p.screenshot({ path: OUT + 'w1-leads-light.png' })
  await p.getByRole('button', { name: /Switch to dark mode/ }).click(); await p.waitForTimeout(600)
  await p.screenshot({ path: OUT + 'w1-leads-dark.png' })

  const o = await overflow(p)
  ck('no horizontal overflow', o.sw <= o.cw && !o.bad.length, `sw=${o.sw} cw=${o.cw} ${o.bad}`)
  ck('no console errors', errs.length === 0, errs.slice(0, 2).join(' | '))
  await ctx.close()

  /* ── OPERATOR CANNOT WRITE, on the SAME app-native tenant ──────────────
     Run here rather than in the sheet section on purpose: on a sheet-backed
     tenant every lead write already 409s on source, so a 403 there would prove
     nothing about identity. The only place "operator may not act" is a real,
     separable claim is a tenant where a PERSON could act. */
  console.log(`\n=== OPERATOR (break-glass) on the same app-native tenant ===`)
  const octx = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
  const op = await octx.newPage()
  await signInOperator(op, STORE)
  await op.getByRole('button', { name: /^Leads$/i }).last().click()
  await op.waitForTimeout(1500)

  const sess = await op.evaluate(async () => {
    const r = await fetch('/api/session', { credentials: 'include' })
    return r.json()
  })
  ck('this really is a break-glass session', sess.role === 'operator' && sess.person_key === null,
     `role=${sess.role} person=${sess.person_key}`)
  ck('operator still READS the tenant\'s leads (W3 scoping unchanged)',
     (await leadsSource(op)).count > 0)

  // THE API IS THE GATE. Called directly, so a hidden button cannot be
  // mistaken for an enforced rule.
  const writes = await op.evaluate(async () => {
    const post = async (url, opts) => (await fetch(url, { credentials: 'include', ...opts })).status
    const first = await (await fetch('/api/leads', { credentials: 'include' })).json()
    const id = (first.leads?.[0] || {}).lead_id
    return {
      create: await post('/api/leads', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'Operator Should Not Create' }) }),
      patch: await post(`/api/leads/${id}`, { method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'Responded' }) }),
      archive: await post(`/api/leads/${id}/archive`, { method: 'POST' }),
      id,
    }
  })
  ck('POST /api/leads -> 403 for an operator', writes.create === 403, `got ${writes.create}`)
  ck('PATCH /api/leads/{id} -> 403 for an operator', writes.patch === 403, `got ${writes.patch}`)
  ck('POST /api/leads/{id}/archive -> 403 for an operator', writes.archive === 403, `got ${writes.archive}`)
  ck('and nothing was created', !(await op.evaluate(async () => {
    const r = await (await fetch('/api/leads', { credentials: 'include' })).json()
    return (r.leads || []).some((l) => l.name === 'Operator Should Not Create')
  })))

  // The UI only reflects it — asserted after the API, never instead of it.
  ck('UI reflects the gate: no create button for an operator',
     (await op.getByRole('button', { name: 'New lead' }).count()) === 0)
  ck('UI reflects the gate: no status/temperature/archive controls',
     (await op.getByRole('button', { name: /^Status: / }).count()) === 0 &&
     (await op.getByRole('button', { name: /^Temperature: / }).count()) === 0 &&
     (await op.getByRole('button', { name: 'Archive' }).count()) === 0)
  ck('CANARY: rows ARE rendered, so those absences mean something',
     (await op.locator('main ul > li').count()) > 0,
     `${await op.locator('main ul > li').count()} rows`)
  await octx.close()
}

/* ── SHEET-BACKED: the read-only branch ───────────────────────────────────
   Everything here is an assertion about ABSENCE, so nothing is clicked and
   nothing is written. This is the branch the live owner uses every day and it
   had no coverage at all. */
if (SHEET) {
  console.log(`\n=== SHEET-BACKED (read-only) — ${SHEET} ===`)
  const ctx = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
  const p = await ctx.newPage()
  const errs = []
  p.on('console', (m) => { if (m.type() === 'error' && !/401/.test(m.text())) errs.push(m.text()) })
  p.on('pageerror', (e) => errs.push(String(e)))

  await signIn(p, SHEET)
  await p.getByRole('button', { name: /^Leads$/i }).last().click()
  await p.waitForTimeout(1500)

  // Google enforces a per-minute read quota; a 503 renders the error state,
  // which has no controls either — and would pass every assertion below for
  // completely the wrong reason. Retry, then refuse rather than score it.
  let src = await leadsSource(p)
  for (let i = 0; i < 5 && (src.error || src.count === 0); i++) {
    await p.waitForTimeout(12000)
    await p.reload({ waitUntil: 'networkidle' })
    await p.getByRole('button', { name: /^Leads$/i }).last().click()
    await p.waitForTimeout(1500)
    src = await leadsSource(p)
  }
  if (src.source !== 'sheet') {
    console.error(`REFUSING: --sheet target reports source=${src.source}. Absent controls on an `
      + `app-native tenant would be a REGRESSION, not the read-only branch.`)
    process.exit(3)
  }
  if (src.error || src.count === 0) {
    console.error(`REFUSING: the sheet never loaded (${src.error || 'no rows'}). Its error state `
      + `has no controls either, so these assertions would pass without testing anything.`)
    process.exit(3)
  }
  console.log(`  (source=sheet, ${src.count} rows loaded)`)

  ck('NO create button on a sheet-backed tenant',
     (await p.getByRole('button', { name: 'New lead' }).count()) === 0)
  ck('the subtitle sends the user to the sheet, not to the app',
     /the sheet stays the place you edit/i.test(await p.locator('main').innerText()))

  // The three controls must still RENDER — the design is identical, only the
  // interactivity is gone. Absence of the markup would be a different bug.
  ck('status still renders (as markup, not a control)',
     (await p.locator('main .chip').count()) > 0 &&
     (await p.getByRole('button', { name: /^Status: / }).count()) === 0,
     `${await p.locator('main .chip').count()} chips, `
     + `${await p.getByRole('button', { name: /^Status: / }).count()} status buttons`)
  ck('temperature still renders, with no control',
     (await p.getByRole('button', { name: /^Temperature: / }).count()) === 0)
  ck('NO archive control on any row',
     (await p.getByRole('button', { name: 'Archive' }).count()) === 0)

  // The one that makes the three above mean something: prove the rows are
  // really there. "No buttons" on an empty list is trivially true.
  const rows = await p.locator('main ul > li').count()
  ck('rows are actually rendered, so the absences are meaningful', rows > 0, `${rows} rows`)

  // CANARY. These are absence assertions, and an absence assertion that can
  // never fail proves nothing — the same trap the r2 shadow check fell into.
  // Prove the locators DO find controls when they exist, by checking the
  // filter chips, which render in both modes.
  ck('CANARY: the button locator finds controls that do exist',
     (await p.getByRole('button', { name: /^(All|Needs touch|Hot|New)$/i }).count()) > 0)

  await p.screenshot({ path: OUT + 'w1-leads-sheet-readonly.png' })
  const o = await overflow(p)
  ck('no horizontal overflow (sheet mode)', o.sw <= o.cw && !o.bad.length, `sw=${o.sw} cw=${o.cw} ${o.bad}`)
  ck('no console errors (sheet mode)', errs.length === 0, errs.slice(0, 2).join(' | '))
  await ctx.close()
}

console.log(`\n${ok.filter(Boolean).length}/${ok.length} passed`)
await b.close()
process.exit(ok.every(Boolean) ? 0 : 1)
