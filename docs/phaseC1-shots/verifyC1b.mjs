/**
 * TTS playback v1 verification — browser SpeechSynthesis.
 *
 * Headless Chromium genuinely has no speech voices, which is used two ways:
 *   - unstubbed, it exercises the real "no voices" degradation path;
 *   - stubbed, it exercises the happy path AND captures the exact text that
 *     would be spoken, which is how markdown stripping gets asserted.
 */
import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'

const BASE = process.argv[2]
const SHOTS = new URL('./shotsC1b/', import.meta.url).pathname
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

/** Fake voices + an utterance recorder, installed before any app JS runs. */
const STUB = `
window.__spoken = [];
window.__cancels = 0;
const voices = [
  { name: 'Google UK English Female', lang: 'en-GB', localService: false, default: false },
  { name: 'Albert (compact)',         lang: 'en-US', localService: true,  default: false },
  { name: 'Samantha',                 lang: 'en-US', localService: true,  default: true  },
  { name: 'Amelie',                   lang: 'fr-FR', localService: true,  default: false },
];
class FakeUtterance {
  constructor(text) { this.text = text; this.voice = null; this.lang = ''; this.rate = 1; this.pitch = 1; }
}
Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: FakeUtterance, configurable: true });
// window.speechSynthesis is a GETTER-ONLY accessor in Chromium — plain
// assignment silently does nothing, so defineProperty is required.
Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
  speaking: false,
  getVoices: () => voices,
  set onvoiceschanged(fn) { if (fn) setTimeout(fn, 0); },
  get onvoiceschanged() { return null; },
  speak(u) {
    window.__spoken.push({ text: u.text, voice: u.voice && u.voice.name, rate: u.rate });
    this.speaking = true;
    if (u.onstart) u.onstart();
    // finish quickly so the UI returns to idle
    setTimeout(() => { this.speaking = false; if (u.onend) u.onend(); }, 250);
  },
  cancel() { window.__cancels++; this.speaking = false; },
} });
`

const browser = await chromium.launch({ headless: true })

const login = async (page) => {
  await page.goto(BASE, { waitUntil: 'networkidle' })
  await page.getByPlaceholder('Passphrase').fill(passphrase())
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.waitForSelector('ol > li', { timeout: 20000 })
  await page.getByRole('button', { name: /^Ask$/ }).click()
  await page.waitForTimeout(900)
  await page.getByRole('button', { name: /Ask Jarvis/ }).first().click()
  await page.waitForTimeout(900)
}
const waitIdle = (page) =>
  page.waitForFunction(
    () => document.querySelector('input[aria-label="Message Jarvis"]')?.disabled === false,
    null, { timeout: 180000 })

/* ------------------------------------------------------------- happy path */
console.log('\n=== 1. WITH VOICES AVAILABLE (stubbed synthesiser) ===')
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
await ctx.addInitScript(STUB)
const page = await ctx.newPage()
const consoleErrors = []
page.on('console', (m) => {
  const t = m.text()
  if (m.type() === 'error' && !(/Failed to load resource/.test(t) && /401|422/.test(t))) consoleErrors.push(t)
})
page.on('pageerror', (e) => consoleErrors.push(String(e)))
await login(page)

// Ask for a reply containing markdown, so stripping can be asserted end-to-end.
await page.getByLabel('Message Jarvis').fill(
  'Reply with exactly this and nothing else: **Bold thing** and `some_code` and see [the docs](https://example.com/x) done.',
)
await page.getByRole('button', { name: 'Send' }).click()
await waitIdle(page)
await page.waitForTimeout(900)

const speaker = page.getByRole('button', { name: /Read this reply aloud|Turn on reading replies aloud/ })
check('speaker control appears on the Jarvis message', (await speaker.count()) > 0)
check('playback is OFF by default (nothing spoken yet)',
  (await page.evaluate(() => window.__spoken.length)) === 0,
  `${await page.evaluate(() => window.__spoken.length)} utterances`)
await page.screenshot({ path: SHOTS + 'f1-speaker-off-light.png' })

console.log('\n--- tap to turn on + speak ---')
await speaker.first().click()
await page.waitForTimeout(600)
const spoken = await page.evaluate(() => window.__spoken)
check('tapping speaks the reply', spoken.length >= 1, `${spoken.length} utterance(s)`)
const said = spoken[0]?.text || ''
console.log('    spoken text:', said.slice(0, 110))
check('markdown stripped: no ** or backticks', !/\*\*|`/.test(said), said.slice(0, 60))
check('link became its label, URL not spelled out', !/https?:\/\//.test(said))
console.log('    chose voice:', spoken[0]?.voice)
check('picked a high-quality en voice (not the compact one)',
  spoken[0]?.voice === 'Google UK English Female', String(spoken[0]?.voice))

console.log('\n--- preference persists ---')
const pref = await page.evaluate(() => localStorage.getItem('jarvis-speak-replies'))
check('preference persisted client-side', pref === '1', `localStorage=${pref}`)

console.log('\n--- tap while speaking stops it ---')
await page.evaluate(() => { window.__cancels = 0; window.speechSynthesis.speaking = true })
await page.waitForTimeout(300)
const stopBtn = page.getByRole('button', { name: /Stop reading aloud|Read this reply aloud/ })
await stopBtn.first().click()
await page.waitForTimeout(300)
check('cancel() was called', (await page.evaluate(() => window.__cancels)) >= 1,
  `${await page.evaluate(() => window.__cancels)} cancels`)

console.log('\n--- auto-speak on a NEW completed reply ---')
await page.evaluate(() => { window.__spoken = [] })
await page.getByLabel('Message Jarvis').fill('Reply with exactly: All clear.')
await page.getByRole('button', { name: 'Send' }).click()
await waitIdle(page)
await page.waitForTimeout(1200)
const auto = await page.evaluate(() => window.__spoken)
check('new reply auto-spoken while preference is on', auto.length >= 1, `${auto.length} utterance(s)`)
console.log('    auto-spoken:', (auto[0]?.text || '').slice(0, 60))

console.log('\n--- navigation stops speech ---')
await page.evaluate(() => { window.__cancels = 0; window.speechSynthesis.speaking = true })
await page.getByRole('button', { name: 'Back to conversations' }).click()
await page.waitForTimeout(600)
check('leaving the thread cancels speech', (await page.evaluate(() => window.__cancels)) >= 1,
  `${await page.evaluate(() => window.__cancels)} cancels`)

console.log('\n=== 2. COUNTERS RECORDED (not in the activity feed) ===')
const counters = await page.evaluate(async () =>
  (await (await fetch('/api/counters', { credentials: 'include' })).json()).counters)
console.log('   ', JSON.stringify(counters))
check('tts_toggle_on recorded', (counters.tts_toggle_on?.value ?? 0) >= 1)
check('tts_utterance recorded', (counters.tts_utterance?.value ?? 0) >= 2)
const feedKinds = await page.evaluate(async () => {
  const d = await (await fetch('/api/feed?limit=50', { credentials: 'include' })).json()
  return [...new Set(d.events.map((e) => e.kind))]
})
check('activity feed NOT polluted with tts events',
  !feedKinds.some((k) => /tts|speak|voice/i.test(k)), feedKinds.join(','))
const inHealth = await page.evaluate(async () => {
  const r = await fetch('/api/health', { credentials: 'include' })
  const d = await r.json()
  return !!d.counters
})
check('counters readable via /api/health', inHealth)

console.log('\n=== 3. DARK MODE ===')
await page.getByRole('button', { name: /Ask Jarvis/ }).first().click()
await page.waitForTimeout(900)
await page.getByRole('button', { name: /Switch to dark mode/ }).click()
await page.waitForTimeout(500)
check('dark bg', (await page.evaluate(() => getComputedStyle(document.body).backgroundColor)) === 'rgb(12, 14, 18)')
check('speaker still rendered in dark',
  (await page.getByRole('button', { name: /reading|Read this reply/ }).count()) > 0)
await page.screenshot({ path: SHOTS + 'f2-speaker-on-dark.png' })

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
await ctx.close()

/* --------------------------------------------- genuine no-voices degradation */
console.log('\n=== 4. NO VOICES AVAILABLE (real headless Chromium, unstubbed) ===')
const ctx2 = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
const p2 = await ctx2.newPage()
let alerted = ''
p2.on('dialog', async (d) => { alerted = d.message(); await d.dismiss() })
await login(p2)
const realVoices = await p2.evaluate(() => ('speechSynthesis' in window ? window.speechSynthesis.getVoices().length : -1))
console.log(`    real voices in this browser: ${realVoices}`)
await p2.getByLabel('Message Jarvis').fill('Reply with exactly: Understood.')
await p2.getByRole('button', { name: 'Send' }).click()
await waitIdle(p2)
await p2.waitForTimeout(1500)
const unavailable = p2.getByRole('button', { name: 'Read aloud unavailable' })
// loadVoices() waits up to 2s for onvoiceschanged before concluding there are
// none, so the control appears after a short delay rather than immediately.
// Counting straight away raced that; wait for it instead.
let degradedShown = true
try {
  await unavailable.first().waitFor({ state: 'visible', timeout: 8000 })
} catch {
  degradedShown = false
}
check('degraded control rendered (not a silent no-op)', degradedShown)
await unavailable.first().click()
await p2.waitForTimeout(400)
check('it EXPLAINS why instead of doing nothing', /no speech voices/i.test(alerted), alerted.slice(0, 70))
await p2.screenshot({ path: SHOTS + 'f3-no-voices-light.png' })
await ctx2.close()

console.log(`\n${'='.repeat(52)}`)
console.log(`RESULT: ${pass} passed, ${fail} failed`)
console.log(`screenshots: ${SHOTS}`)
await browser.close()
process.exit(fail === 0 ? 0 : 1)
