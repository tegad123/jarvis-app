// Usage: NODE_PATH=... node shoot.mjs <base> <tag>
// Shoots login + feed at 390 and 1440 (light), plus the feed with a Details row open.
import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'
const BASE = process.argv[2], TAG = process.argv[3] || 'now'
const OUT = new URL('./shots/', import.meta.url).pathname
mkdirSync(OUT, { recursive: true })
const pins = JSON.parse(readFileSync('/opt/testco/SEEDED_PINS.json','utf8'))
const b = await chromium.launch({ headless: true })
for (const [W,H] of [[390,844],[1440,900]]) {
  const ctx = await b.newContext({ viewport:{width:W,height:H}, deviceScaleFactor:1, colorScheme:'light' })
  const p = await ctx.newPage()
  await p.goto(BASE, { waitUntil:'networkidle' })
  await p.evaluate(()=>{localStorage.clear()})
  await p.reload({ waitUntil:'networkidle' }); await p.waitForTimeout(400)
  await p.screenshot({ path: `${OUT}${TAG}-${W}-login.png`, fullPage: W<1000 })
  await p.getByRole('button',{name:/^Dana/}).click(); await p.waitForTimeout(200)
  await p.screenshot({ path: `${OUT}${TAG}-${W}-login-selected.png`, fullPage: W<1000 })
  await p.getByPlaceholder('PIN').fill(pins.dana)
  await p.getByRole('button',{name:'Continue'}).click()
  await p.waitForSelector('ol > li',{timeout:20000}); await p.waitForTimeout(1200)
  await p.screenshot({ path: `${OUT}${TAG}-${W}-feed.png` })
  await ctx.close()
}
await b.close(); console.log('shot', TAG)
