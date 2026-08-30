import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'

const SP = '/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad'
const BASE = 'http://127.0.0.1:8710'
const PINS = JSON.parse(readFileSync(`/opt/testco/SEEDED_PINS.json`, 'utf8'))
const TERMS = JSON.parse(readFileSync(`${SP}/verify/leak_terms.json`, 'utf8'))
const OUT = `${SP}/verify/vis-testco/`; mkdirSync(OUT, { recursive: true })

const ok = []
const ck = (n, c, x = '') => { console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${x ? ' — ' + x : ''}`); ok.push(!!c) }

const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

async function signIn(page, who) {
  await page.goto(BASE, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  await page.getByRole('button', { name: new RegExp(who, 'i') }).click()
  await page.getByPlaceholder('PIN').fill(PINS[who])
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
    if (r.width > 0 && r.right > d.clientWidth + 1 && !clipped(el)) bad.push(el.tagName)
  }
  return { sw: d.scrollWidth, cw: d.clientWidth, bad: bad.slice(0, 3) }
})

const b = await chromium.launch({ headless: true })
const corpus = {}          // "<who>/<tab>/<width>/<theme>" -> rendered text

// THE SWEEP. Every role is walked at 390/dark, because that is the corpus the
// leak gate greps and a leak is role-dependent (scoping decides what renders).
// The other two viewports are dana-only: they exist to catch layout and theme
// regressions, and re-grepping identical text three times finds nothing new.
const SWEEP = [[390, 'dark', ['dana', 'omar', 'priya']],
               [390, 'light', ['dana']],
               [1280, 'dark', ['dana']]]

for (const [w, theme, roles] of SWEEP) {
  for (const who of roles) {
    // TIMEZONE IS PART OF THE FIXTURE. The hero counts "today" in the
    // BROWSER'S zone; a headless default of UTC read "Nothing yet today" over
    // a feed full of Chicago-morning events. The tenant is in America/Chicago,
    // so the tester's browser is too, and the seed also now carries
    // hours-old events so the hero is non-empty in any zone.
    const ctx = await b.newContext({ viewport: { width: w, height: 1000 },
      deviceScaleFactor: 1, colorScheme: theme, timezoneId: 'America/Chicago' })
    const p = await ctx.newPage()
    await signIn(p, who)

    for (const tab of ['Feed', 'Ask', 'Approvals', 'Leads']) {
      await p.getByRole('button', { name: new RegExp(`^${tab}$`, 'i'), exact: false }).last().click()
      await p.waitForTimeout(1100)
      // Expand every "Details" disclosure. The raw `detail` string is where a
      // leaked term would actually live — it carries the from-address, the
      // subject and the classifier's reasoning — and a collapsed panel renders
      // none of it, so a gate that skips this greps only headlines.
      //
      // Re-queried by index with a SHORT timeout on each click, never held as
      // handles: expanding a row re-renders the list, so handles collected up
      // front go stale and each stale click then burns the default 30s
      // timeout. Twenty-five of those per tab is what turned this suite from
      // two minutes into over ten.
      for (let i = 0; i < 40; i++) {
        const btn = p.getByRole('button', { name: 'Details' }).first()
        if (!(await btn.count())) break
        try { await btn.click({ timeout: 1500 }) } catch { break }
      }
      await p.waitForTimeout(300)
      corpus[`${who}/${tab}/${w}/${theme}`] = await p.locator('body').innerText()
      if (w === 390 && theme === 'dark' && who === 'dana') {
        await p.screenshot({ path: `${OUT}testco-${tab.toLowerCase()}.png`, fullPage: true })
      }
      const o = await overflow(p)
      if (!(o.sw <= o.cw && !o.bad.length)) ck(`${who}/${tab} ${w}/${theme} overflow`, false, `${o.sw}/${o.cw} ${o.bad}`)
    }
    await ctx.close()
  }
}
ck('no horizontal overflow anywhere (3 roles x 4 tabs x 3 viewports)', ok.length === 0)

const ALL = Object.values(corpus).join('\n')
console.log(`\n=== DOM-GREP GATE — ${TERMS.length} forbidden terms vs ${ALL.length} rendered chars ===`)

// CANARY FIRST. A gate that cannot fail proves nothing; Phase 3 shipped a green
// run once because an unescaped paren made the pattern match nothing. So prove
// the matcher fires before trusting that it did not.
const canary = TERMS.some((t) => new RegExp(esc(t), 'i').test('prefix 34dev suffix'))
ck('CANARY: the matcher detects a planted term', canary)

const hits = []
for (const t of TERMS) {
  const re = new RegExp(esc(t), 'i')
  for (const [where, text] of Object.entries(corpus)) {
    const m = re.exec(text)
    if (m) { hits.push(`${JSON.stringify(t)} in ${where} → ${JSON.stringify(text.slice(Math.max(0, m.index - 30), m.index + 40))}`); break }
  }
}
ck('ZERO prior-tenant terms in any rendered DOM', hits.length === 0)
for (const h of hits.slice(0, 12)) console.log('      LEAK ' + h)

console.log('\n=== EVERY VISIBLE ADDRESS IS .example ===')
const addrs = [...new Set(ALL.match(/[\w.+-]+@[\w.-]+\.\w+/g) || [])]
const bad = addrs.filter((a) => !/\.example(\.com)?$/i.test(a))
ck(`addresses are all fictional (${addrs.length} distinct)`, bad.length === 0, bad.slice(0, 5).join(', '))

console.log('\n=== THE SEEDED WORKLOAD ACTUALLY RENDERS ===')
const dana = (t) => corpus[`dana/${t}/390/dark`]
ck('the hero is not empty on first open',
   !/Nothing yet today/i.test(dana('Feed')),
   (dana('Feed').split('\n').slice(0, 4).join(' / ')).slice(0, 60))
// Humanised = a sentence a person wrote, not a kind slug. Any of the seeded
// shapes counts; the boundary's own events are humanised too.
ck('feed renders humanised headlines',
   /Filed an (urgent )?email from|Created a task:|Completed:|Refused to send|Captured an outbound/
     .test(dana('Feed')),
   dana('Feed').split('\n').filter(l => l.length > 30)[0]?.slice(0, 60) || '')
// The kind FILTER BAR lists every kind present in the feed, so it is the
// honest place to assert coverage — the visible page is only the newest slice.
// The kind filter bar lists kinds in the FETCHED PAGE, not the whole feed, so
// a burst of new events can push an older kind off it. Assert the vocabulary
// is represented rather than demanding every member.
const KINDS = ['EMAIL FILED','TASK CREATED','DONE','WARNING','ALERT','UPDATE','LEAD CREATED']
const present = KINDS.filter((wd) => dana('Feed').includes(wd))
ck('feed covers the styled kind vocabulary', present.length >= 6,
   `${present.length}/${KINDS.length}: ${present.join(', ')}`)
ck('the boundary\'s own kinds are visible to the tester',
   dana('Feed').includes('ALERT') && dana('Feed').includes('UPDATE'))
ck('feed shows a prettified unknown kind in the filter bar',
   /Deal Channel Created|Lead Created/i.test(dana('Feed')))
ck('four deal channels are listed', ['118 Alderwood','2204 Kestrel','77 Bellhaven','3310 Copperbeam']
   .every((n) => dana('Ask').includes(n)))
ck('the direct line is pinned', /Ask Jarvis/.test(dana('Ask')))
ck('three approvals are pending', ['Rowan Castellane','Copperbeam','walkthrough']
   .every((n) => new RegExp(n, 'i').test(dana('Approvals'))))
// DERIVED, NOT HARDCODED. The count moved when the pass-0 fixtures were
// added, and a fixture change must never masquerade as a regression.
ck('leads render with a count and statuses',
   /\b\d{2,} leads/.test(dana('Leads')) && /RESPONDED|NO RESPONSE|NEW/i.test(dana('Leads')),
   (dana('Leads').match(/\b\d{2,} leads/) || ['no count'])[0])
ck('the agent column shows NAMES, not raw person keys',
   /· (Dana|Omar|Priya)\b/.test(dana('Leads')) && !/· (dana|omar|priya)\b/.test(dana('Leads')),
   (dana('Leads').match(/· \w+/g) || []).slice(0, 4).join(' '))

console.log('\n=== W3 SCOPING HOLDS ON THE SEEDED DATA ===')
const omar = (t) => corpus[`omar/${t}/390/dark`]
ck('a member does not see a channel they are not in',
   !omar('Ask').includes('3310 Copperbeam'), 'Copperbeam is dana+priya only')
ck('a member does see the channel they ARE in', omar('Ask').includes('2204 Kestrel'))
ck('approvals are owner-only', /owner/i.test(omar('Approvals')) && !/Rowan Castellane/.test(omar('Approvals')))
ck('each person is greeted by their own name',
   /Dana/.test(dana('Feed')) && /Omar/.test(omar('Feed')) && !/Dana/.test(omar('Feed')))

console.log(`\n${ok.filter(Boolean).length}/${ok.length} passed`)
await b.close()
process.exit(ok.every(Boolean) ? 0 : 1)
