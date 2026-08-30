/**
 * Phase B verification — attachments in chat, through the tunnel.
 */
import { chromium } from 'playwright'
import { readFileSync, mkdirSync, writeFileSync, statSync, existsSync } from 'node:fs'
import { execSync } from 'node:child_process'
import { deflateSync } from 'node:zlib'

const BASE = process.argv[2]
const SHOTS = new URL('./shotsB/', import.meta.url).pathname
const TMP = new URL('./fixtures/', import.meta.url).pathname
mkdirSync(SHOTS, { recursive: true })
mkdirSync(TMP, { recursive: true })
const LOG = '/root/.hermes/logs/agent.log'

/** Minimal PNG writer — no image libs on this box. */
function writePng(path, w, h, [r, g, b]) {
  const raw = Buffer.concat(
    Array.from({ length: h }, () =>
      Buffer.concat([Buffer.from([0]), Buffer.concat(Array.from({ length: w }, () => Buffer.from([r, g, b])))]),
    ),
  )
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length)
    const td = Buffer.concat([Buffer.from(type), data])
    const crcTable = []
    for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; crcTable[n] = c >>> 0 }
    let crc = 0xffffffff
    for (const byte of td) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8)
    const crcBuf = Buffer.alloc(4); crcBuf.writeUInt32BE((crc ^ 0xffffffff) >>> 0)
    return Buffer.concat([len, td, crcBuf])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4)
  ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0
  writeFileSync(path, Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0)),
  ]))
}

// A phone-sized image (3000x2000). Mostly solid green so the MODEL has an
// unambiguous answer, but with per-pixel noise so it compresses like a real
// photo rather than like a flat swatch.
const PHOTO = TMP + 'photo.png'
writeNoisyPng(PHOTO, 3000, 2000, [20, 160, 90])
const DOC = TMP + 'quote.pdf'
writeFileSync(DOC, Buffer.from('%PDF-1.4\n% phase B fixture\n'))
const BAD = TMP + 'thing.exe'
writeFileSync(BAD, Buffer.from([0x4d, 0x5a, 0x90, 0x00]))
const HUGE = TMP + 'huge.pdf'
writeFileSync(HUGE, Buffer.concat([Buffer.from('%PDF-1.4\n'), Buffer.alloc(11 * 1024 * 1024, 0x20)]))

/** Same PNG writer, with per-pixel jitter so the file compresses realistically. */
function writeNoisyPng(path, w, h, [r, g, b]) {
  let seed = 12345
  const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) % 24) - 12
  const rows = []
  for (let y = 0; y < h; y++) {
    const row = Buffer.alloc(w * 3 + 1)
    for (let x = 0; x < w; x++) {
      row[1 + x * 3] = Math.max(0, Math.min(255, r + rnd()))
      row[2 + x * 3] = Math.max(0, Math.min(255, g + rnd()))
      row[3 + x * 3] = Math.max(0, Math.min(255, b + rnd()))
    }
    rows.push(row)
  }
  const raw = Buffer.concat(rows)
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length)
    const td = Buffer.concat([Buffer.from(type), data])
    const crcTable = []
    for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; crcTable[n] = c >>> 0 }
    let crc = 0xffffffff
    for (const byte of td) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8)
    const crcBuf = Buffer.alloc(4); crcBuf.writeUInt32BE((crc ^ 0xffffffff) >>> 0)
    return Buffer.concat([len, td, crcBuf])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4)
  ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0
  writeFileSync(path, Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0)),
  ]))
}

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
  if (m.type() === 'error' && !(/Failed to load resource/.test(t) && /401|413|422/.test(t))) consoleErrors.push(t)
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

console.log('\n=== 1. OPEN THE DIRECT LINE ===')
await page.goto(BASE, { waitUntil: 'networkidle' })
await page.getByPlaceholder('Passphrase').fill(passphrase())
await page.getByRole('button', { name: 'Continue' }).click()
await page.waitForSelector('ol > li', { timeout: 20000 })
await page.getByRole('button', { name: /^Ask$/ }).click()
await page.waitForTimeout(900)
await page.getByRole('button', { name: /Ask Jarvis/ }).first().click()
await page.waitForTimeout(900)
check('+ button present in composer', await page.getByRole('button', { name: 'Add a photo or file' }).isVisible())

console.log('\n=== 2. PHOTO UPLOAD + CLIENT-SIDE DOWNSCALE ===')
const origBytes = statSync(PHOTO).size
await page.setInputFiles('input[type="file"]', PHOTO)
await page.waitForFunction(() => !!document.querySelector('form img'), null, { timeout: 60000 })
await page.waitForTimeout(600)
check('thumbnail chip appears in composer', (await page.locator('form img').count()) === 1)
const uploaded = await page.evaluate(async () => {
  const r = await fetch('/api/threads/direct/attachments', { credentials: 'include' })
  const j = await r.json()
  return j.attachments[j.attachments.length - 1]
})
console.log(`    original ${(origBytes / 1024 / 1024).toFixed(1)} MB -> stored ${uploaded.size_human} (${uploaded.mime})`)
check('image was downscaled + re-encoded to JPEG', uploaded.mime === 'image/jpeg', uploaded.mime)
const dims = await page.evaluate(
  (url) =>
    new Promise((res) => {
      const im = new Image()
      im.onload = () => res({ w: im.naturalWidth, h: im.naturalHeight })
      im.onerror = () => res({ w: 0, h: 0 })
      im.src = url
    }),
  uploaded.url,
)
console.log(`    stored dimensions: ${dims.w}x${dims.h} (source was 3000x2000)`)
// This is the property we control and that keeps images inside the body cap.
// Byte ratio depends on the source's compressibility, so it is not asserted.
check('longest edge downscaled to <= 2000px', Math.max(dims.w, dims.h) <= 2000 && dims.w > 0, `${dims.w}x${dims.h}`)
check('aspect ratio preserved', Math.abs(dims.w / dims.h - 1.5) < 0.02, `${(dims.w / dims.h).toFixed(3)}`)
check('stored smaller than the original', uploaded.size < origBytes, `${uploaded.size} vs ${origBytes}`)
check('image marked readable (model will see it)', uploaded.readable === true)
await overflowCheck('composer with thumbnail')
await page.screenshot({ path: SHOTS + 'd1-composer-thumb-light.png' })

console.log('\n=== 3. JARVIS ACTUALLY SEES THE IMAGE ===')
const logSize = statSync(LOG).size
await page.getByLabel('Message Jarvis').fill('What single colour fills this image? Answer with one word.')
await page.getByRole('button', { name: 'Send' }).click()
await waitIdle()
await page.waitForTimeout(700)
const convo = await page.locator('main').innerText()
const reply = convo.split('JARVIS').pop().trim()
console.log('    reply:', reply.slice(0, 80))
check('MODEL DESCRIBED THE IMAGE CONTENT', /green/i.test(reply), reply.slice(0, 60))
const newLog = readFileSync(LOG, 'utf8').slice(logSize)
const turn = newLog.split('\n').filter((l) => l.includes('agent.turn_context: conversation turn:')).pop() || ''
console.log('    log:', turn.slice(0, 150))
check('served by claude-sonnet-5 / anthropic', /model=claude-sonnet-5/.test(turn) && /provider=anthropic/.test(turn))
check('sent thumbnail renders in the message', (await page.locator('ol img').count()) >= 1)
await overflowCheck('message with image')
await page.screenshot({ path: SHOTS + 'd2-image-answer-light.png' })

console.log('\n=== 4. DOCUMENT: STORED, CHIPPED, NOT SENT ===')
await page.setInputFiles('input[type="file"]', DOC)
await page.waitForTimeout(2500)
let composer = await page.locator('form').innerText()
check('document chip shows "saved, not readable yet"', /saved, not readable yet/i.test(composer), composer.slice(0, 80).replace(/\n/g, ' '))
await page.getByLabel('Message Jarvis').fill('Anything in that file?')
await page.getByRole('button', { name: 'Send' }).click()
await waitIdle()
await page.waitForTimeout(700)
const after = await page.locator('main').innerText()
check('notice says the document was NOT sent', /Not sent to Jarvis/i.test(after), (after.match(/Not sent to Jarvis[^\n]*/) || [''])[0])
check('document chip still rendered in the message', /quote\.pdf/.test(after))
await page.screenshot({ path: SHOTS + 'd3-document-notice-light.png' })

console.log('\n=== 5. DISALLOWED TYPE + OVERSIZED ===')
await page.setInputFiles('input[type="file"]', BAD)
await page.waitForTimeout(2500)
let body = await page.locator('main').innerText()
check('disallowed type produces an honest error', /isn't supported|not supported/i.test(body), (body.match(/[^\n]*supported[^\n]*/) || [''])[0].slice(0, 90))
await page.setInputFiles('input[type="file"]', HUGE)
await page.waitForTimeout(4000)
body = await page.locator('main').innerText()
check('oversized file produces an honest error', /too large|limit/i.test(body), (body.match(/[^\n]*(too large|limit)[^\n]*/) || [''])[0].slice(0, 90))
await page.screenshot({ path: SHOTS + 'd4-errors-light.png' })

console.log('\n=== 6. PERSIST ACROSS APP RESTART ===')
execSync('systemctl restart jarvis-app')
await sleep(5000)
await page.reload({ waitUntil: 'networkidle' })
await page.getByRole('button', { name: /^Ask$/ }).click()
await page.waitForTimeout(900)
await page.getByRole('button', { name: /Ask Jarvis/ }).first().click()
await page.waitForTimeout(1800)
const restored = await page.locator('main').innerText()
check('IMAGE CHIP SURVIVED RESTART', (await page.locator('ol img').count()) >= 1)
check('document chip survived restart', /quote\.pdf/.test(restored))
check('conversation survived restart', /green/i.test(restored))
const imgOk = await page.evaluate(() => {
  const img = document.querySelector('ol img')
  return img ? img.naturalWidth > 0 : false
})
check('stored image still serves its bytes', imgOk)

console.log('\n=== 7. AUTH ON /api/attachments ===')
const attId = uploaded.id
const authed = await page.evaluate((id) => fetch(`/api/attachments/${id}`, { credentials: 'include' }).then((r) => r.status), attId)
check('authenticated fetch serves the file', authed === 200, String(authed))
const anon = execSync(`curl -s -o /dev/null -w '%{http_code}' ${BASE}/api/attachments/${attId}`).toString().trim()
check('UNAUTHENTICATED access is 401', anon === '401', anon)

console.log('\n=== 8. DARK MODE ===')
await page.getByRole('button', { name: /Switch to dark mode/ }).click()
await page.waitForTimeout(500)
check('dark bg', (await page.evaluate(() => getComputedStyle(document.body).backgroundColor)) === 'rgb(12, 14, 18)')
await overflowCheck('interior dark')
await page.screenshot({ path: SHOTS + 'd5-interior-dark.png' })

console.log('\n=== 9. HYGIENE ===')
check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))
check('files on disk under data/attachments', existsSync('/root/jarvis-app/data/attachments/direct'))

console.log(`\n${'='.repeat(52)}`)
console.log(`RESULT: ${pass} passed, ${fail} failed`)
console.log(`screenshots: ${SHOTS}`)
await browser.close()
process.exit(fail === 0 ? 0 : 1)
