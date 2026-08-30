import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'
const BASE = process.argv[2], TAG = process.argv[3] || 'now', W = +(process.argv[4] || 1280)
const OUT = new URL('./vis/', import.meta.url).pathname; mkdirSync(OUT,{recursive:true})
function pass_(){for(const l of readFileSync('/root/.hermes/.env','utf8').split('\n')){
  const m=/^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(l); if(m)return m[1].trim().replace(/^["']|["']$/g,'')}}
const b=await chromium.launch({headless:true})
// deviceScaleFactor 1 on purpose for the light-mode check: a 2x retina render
// flatters hairlines and faint shadows that vanish on an ordinary screen.
const ctx=await b.newContext({viewport:{width:W,height:900},deviceScaleFactor:1})
const p=await ctx.newPage()
await p.goto(BASE,{waitUntil:'networkidle'})
await p.getByPlaceholder('Passphrase').fill(pass_())
await p.getByRole('button',{name:'Continue'}).click()
await p.waitForSelector('ol > li',{timeout:20000}); await p.waitForTimeout(1200)
for (const theme of ['light','dark']) {
  const want = theme==='dark' ? /Switch to dark mode/ : /Switch to light mode/
  const btn = p.getByRole('button',{name:want})
  if (await btn.count()) { await btn.click(); await p.waitForTimeout(600) }
  await p.screenshot({path:`${OUT}${TAG}-${W}-feed-${theme}.png`})
  await p.getByRole('button',{name:/^Ask$/i}).click(); await p.waitForTimeout(900)
  await p.screenshot({path:`${OUT}${TAG}-${W}-conv-${theme}.png`})
  await p.getByRole('button',{name:/^Feed$/i}).click(); await p.waitForTimeout(700)
}
await b.close(); console.log('shot', TAG, W)
