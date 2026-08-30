/**
 * Phase C1 verification — push-to-talk, through the tunnel.
 *
 * Chromium's --use-file-for-fake-audio-capture feeds a real WAV into
 * getUserMedia, so MediaRecorder encodes actual speech in the browser and the
 * whole path is exercised: record -> upload -> whisper -> editable composer.
 */
import { chromium } from 'playwright'
import { readFileSync, mkdirSync, readdirSync, existsSync } from 'node:fs'
import { execSync } from 'node:child_process'

const BASE = process.argv[2]
const WAV = '/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad/voice/sample15.wav'
const SPOOL = '/root/jarvis-app/data/tmp-transcribe'
const SHOTS = new URL('./shotsC1/', import.meta.url).pathname
mkdirSync(SHOTS, { recursive: true })

function passphrase() {
  for (const line of readFileSync('/root/.hermes/.env', 'utf8').split('\n')) {
    const m = /^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(line)
    if (m) return m[1].trim().replace(/^["']|["']$/g, '')
  }
  throw new Error('APP_PASSPHRASE not found')
}
const spoolFiles = () => (existsSync(SPOOL) ? readdirSync(SPOOL) : [])

let pass = 0, fail = 0
const check = (n, ok, extra = '') => {
  console.log(`${ok ? '  PASS' : '  FAIL'}  ${n}${extra ? ' — ' + extra : ''}`)
  ok ? pass++ : fail++
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const browser = await chromium.launch({
  headless: true,
  args: [
    '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream',
    `--use-file-for-fake-audio-capture=${WAV}%noloop`,
    '--autoplay-policy=no-user-gesture-required',
  ],
})
const ctx = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  permissions: ['microphone'],
})
const page = await ctx.newPage()
const consoleErrors = []
page.on('console', (m) => {
  const t = m.text()
  if (m.type() === 'error' && !(/Failed to load resource/.test(t) && /401|409|422/.test(t))) consoleErrors.push(t)
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
const waitIdle = () =>
  page.waitForFunction(
    () => document.querySelector('input[aria-label="Message Jarvis"]')?.disabled === false,
    null, { timeout: 180000 })

console.log('\n=== 1. OPEN A CONVERSATION ===')
await page.goto(BASE, { waitUntil: 'networkidle' })
await page.getByPlaceholder('Passphrase').fill(passphrase())
await page.getByRole('button', { name: 'Continue' }).click()
await page.waitForSelector('ol > li', { timeout: 20000 })
await page.getByRole('button', { name: /^Ask$/ }).click()
await page.waitForTimeout(900)
await page.getByRole('button', { name: /Ask Jarvis/ }).first().click()
await page.waitForTimeout(900)
check('mic occupies its reserved slot', await page.getByRole('button', { name: 'Record a voice note' }).isVisible())

console.log('\n=== 2. RECORD REAL SPEECH IN THE BROWSER ===')
console.log(`    feeding ${WAV} into getUserMedia as a fake device`)
const spoolBefore = spoolFiles().length
await page.getByRole('button', { name: 'Record a voice note' }).click()
await page.waitForTimeout(1200)
let strip = await page.locator('form').innerText()
check('recording state is NAMED (not a bare spinner)', /RECORDING/i.test(strip), strip.split('\n')[0])
check('timer visible', /\d:\d\d\s*\/\s*\d:\d\d/.test(strip), (strip.match(/\d:\d\d\s*\/\s*\d:\d\d/) || [''])[0])
check('stop control offered', await page.getByRole('button', { name: 'Stop recording' }).isVisible())
await overflowCheck('recording')
await page.screenshot({ path: SHOTS + 'e1-recording-light.png' })

// let the whole 11s sample play so whisper has real speech
await page.waitForTimeout(11000)
await page.getByRole('button', { name: 'Stop recording' }).click()

console.log('\n=== 3. NAMED WORKING STATES, THEN EDITABLE TRANSCRIPT ===')
let sawWorking = false
for (let i = 0; i < 40; i++) {
  const t = await page.locator('form').innerText().catch(() => '')
  if (/UPLOADING|TRANSCRIBING/i.test(t)) { sawWorking = true; break }
  const v = await page.getByLabel('Message Jarvis').inputValue().catch(() => '')
  if (v) break
  await page.waitForTimeout(250)
}
check('uploading/transcribing state was shown', sawWorking)

await page.waitForFunction(
  () => (document.querySelector('input[aria-label="Message Jarvis"]')?.value || '').length > 5,
  null, { timeout: 120000 })
const transcript = await page.getByLabel('Message Jarvis').inputValue()
console.log('    transcript:', transcript.slice(0, 100))
check('TRANSCRIPT LANDED IN THE COMPOSER', transcript.length > 20)
check('transcript is recognisably the speech', /jarvis/i.test(transcript) && /task/i.test(transcript))
check('composer is EDITABLE (not disabled)',
  (await page.getByLabel('Message Jarvis').isDisabled()) === false)
check('it did NOT auto-send', (await page.locator('ol > li').count()) === 0,
  `${await page.locator('ol > li').count()} messages`)
await page.screenshot({ path: SHOTS + 'e2-transcript-editable-light.png' })

console.log('\n=== 4. NO RETENTION ===')
const spoolAfter = spoolFiles()
console.log(`    spool dir: ${SPOOL}`)
check('audio deleted after transcription', spoolAfter.length === spoolBefore,
  `${spoolBefore} -> ${spoolAfter.length} files ${spoolAfter.join(',')}`)

console.log('\n=== 5. USER EDITS, THEN SENDS -> JARVIS ANSWERS ===')
await page.getByLabel('Message Jarvis').fill(transcript + ' Reply with the single word HEARD.')
await page.getByRole('button', { name: 'Send' }).click()
await waitIdle()
await page.waitForTimeout(700)
// Assert on the ASSISTANT's message specifically. Testing the whole screen
// would match the user's own bubble ("Reply with ... HEARD"), which is a false
// pass — the prompt is not evidence that the model answered.
const assistantText = await page.evaluate(() => {
  const lis = [...document.querySelectorAll('ol > li')]
  for (let i = lis.length - 1; i >= 0; i--) {
    const t = (lis[i].textContent || '').trim()
    if (/^jarvis/i.test(t)) return t.replace(/^jarvis/i, '').trim()
  }
  return ''
})
console.log('    assistant reply:', assistantText.slice(0, 120))
check('an assistant reply exists at all', assistantText.length > 0)
check('full round trip: JARVIS answered the dictated message', /heard/i.test(assistantText),
  assistantText.slice(0, 70))
await overflowCheck('after send')
await page.screenshot({ path: SHOTS + 'e3-roundtrip-light.png' })

console.log('\n=== 6. DARK MODE ===')
await page.getByRole('button', { name: /Switch to dark mode/ }).click()
await page.waitForTimeout(500)
check('dark bg', (await page.evaluate(() => getComputedStyle(document.body).backgroundColor)) === 'rgb(12, 14, 18)')
await page.getByRole('button', { name: 'Record a voice note' }).click()
await page.waitForTimeout(1200)
check('recording state renders in dark', /RECORDING/i.test(await page.locator('form').innerText()))
await overflowCheck('recording dark')
await page.screenshot({ path: SHOTS + 'e4-recording-dark.png' })
await page.getByRole('button', { name: 'Stop recording' }).click()
await page.waitForTimeout(3000)

console.log('\n=== 7. MIC DENIED STATE ===')
const ctx2 = await browser.newContext({
  viewport: { width: 390, height: 844 }, deviceScaleFactor: 2,
  permissions: [],   // no microphone
})
await ctx2.grantPermissions([])
const p2 = await ctx2.newPage()
await p2.goto(BASE, { waitUntil: 'networkidle' })
await p2.getByPlaceholder('Passphrase').fill(passphrase())
await p2.getByRole('button', { name: 'Continue' }).click()
await p2.waitForSelector('ol > li', { timeout: 20000 })
await p2.getByRole('button', { name: /^Ask$/ }).click(); await p2.waitForTimeout(900)
await p2.getByRole('button', { name: /Ask Jarvis/ }).first().click(); await p2.waitForTimeout(900)
// deny at the API level
await p2.evaluate(() => {
  navigator.mediaDevices.getUserMedia = () => {
    const e = new Error('denied'); e.name = 'NotAllowedError'; return Promise.reject(e)
  }
})
await p2.getByRole('button', { name: 'Record a voice note' }).click()
await p2.waitForTimeout(1200)
const denied = await p2.locator('form').innerText()
check('mic-denied renders a designed state', /Microphone blocked/i.test(denied), denied.split('\n')[0])
check('denied state explains what to do', /allow it in the address-bar/i.test(denied))
check('typing still possible while denied', (await p2.getByLabel('Message Jarvis').isDisabled()) === false)
await p2.screenshot({ path: SHOTS + 'e5-mic-denied-light.png' })
await ctx2.close()

console.log('\n=== 8. HYGIENE ===')
check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))

console.log(`\n${'='.repeat(52)}`)
console.log(`RESULT: ${pass} passed, ${fail} failed`)
console.log(`screenshots: ${SHOTS}`)
await browser.close()
process.exit(fail === 0 ? 0 : 1)
