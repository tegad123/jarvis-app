import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'
const BASE = process.argv[2], TAG = process.argv[3] || 'now'
const OUT = new URL('./vis/', import.meta.url).pathname
mkdirSync(OUT, { recursive: true })
function pass_() { for (const l of readFileSync('/root/.hermes/.env','utf8').split('\n')) {
  const m=/^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(l); if(m) return m[1].trim().replace(/^["']|["']$/g,'') } }
const b = await chromium.launch({ headless: true })
const ctx = await b.newContext({ viewport:{width:390,height:844}, deviceScaleFactor:2 })
const p = await ctx.newPage()
await p.goto(BASE, { waitUntil:'networkidle' })
await p.getByPlaceholder('Passphrase').fill(pass_())
await p.getByRole('button',{name:'Continue'}).click()
await p.waitForSelector('ol > li',{timeout:20000}); await p.waitForTimeout(1200)
// dark mode to match the mockup
await p.getByRole('button',{name:/Switch to dark mode/}).click(); await p.waitForTimeout(600)
await p.screenshot({ path: `${OUT}${TAG}-feed-dark.png` })
await p.getByRole('button',{name:/^Ask$/i}).click(); await p.waitForTimeout(1200)
await p.screenshot({ path: `${OUT}${TAG}-conversations-dark.png` })
await p.getByRole('button',{name:/Switch to light mode/}).click(); await p.waitForTimeout(600)
await p.screenshot({ path: `${OUT}${TAG}-conversations-light.png` })
await p.getByRole('button',{name:/^Feed$/i}).click(); await p.waitForTimeout(900)
await p.screenshot({ path: `${OUT}${TAG}-feed-light.png` })
await b.close()
console.log('shot', TAG)
