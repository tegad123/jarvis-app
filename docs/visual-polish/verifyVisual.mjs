import { chromium } from 'playwright'
import { readFileSync } from 'node:fs'
const BASE = process.argv[2]
function pass_(){for(const l of readFileSync('/root/.hermes/.env','utf8').split('\n')){
  const m=/^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(l); if(m) return m[1].trim().replace(/^["']|["']$/g,'')}}
let ok=[]; const ck=(n,c,x='')=>{console.log(`  ${c?'PASS':'FAIL'}  ${n}${x?' — '+x:''}`);ok.push(c)}
const b = await chromium.launch({headless:true})
const ctx = await b.newContext({viewport:{width:390,height:844},deviceScaleFactor:2})
const p = await ctx.newPage()
const errs=[]; p.on('console',m=>{if(m.type()==='error'&&!/401/.test(m.text()))errs.push(m.text())})
p.on('pageerror',e=>errs.push(String(e)))
await p.goto(BASE,{waitUntil:'networkidle'})
await p.getByPlaceholder('Passphrase').fill(pass_())
await p.getByRole('button',{name:'Continue'}).click()
await p.waitForSelector('ol > li',{timeout:20000}); await p.waitForTimeout(1200)

const overflow = async () => p.evaluate(() => {
  const d=document.documentElement
  const clipped=el=>{for(let q=el.parentElement;q&&q!==document.body;q=q.parentElement)
    if(['hidden','auto','scroll'].includes(getComputedStyle(q).overflowX))return true;return false}
  const bad=[]
  for(const el of document.querySelectorAll('body *')){const r=el.getBoundingClientRect()
    if(r.width>0&&r.right>d.clientWidth+1&&!clipped(el))bad.push(el.tagName+'.'+(el.className||'').toString().slice(0,28))}
  return {sw:d.scrollWidth,cw:d.clientWidth,bad:bad.slice(0,3)}
})

console.log('=== OVERFLOW, every tab, both themes (390px) ===')
for (const theme of ['dark','light']) {
  if (theme==='dark') { await p.getByRole('button',{name:/Switch to dark mode/}).click(); await p.waitForTimeout(500) }
  else { await p.getByRole('button',{name:/Switch to light mode/}).click(); await p.waitForTimeout(500) }
  for (const tab of ['Feed','Ask','Approvals','Leads']) {
    await p.getByRole('button',{name:new RegExp(`^${tab}$`,'i')}).click(); await p.waitForTimeout(900)
    const o = await overflow()
    ck(`${theme} / ${tab}`, o.sw<=o.cw && o.bad.length===0, `sw=${o.sw} cw=${o.cw} ${o.bad.join(' ')}`)
  }
}

console.log('\n=== TOKEN-SPEC CHECKS ===')
await p.getByRole('button',{name:/^Feed$/i}).click(); await p.waitForTimeout(900)
const hero = await p.locator('section').first().innerText()
ck('hero grammar is pluralised, not raw kinds',
   /\d+ emails? filed/.test(hero) && !/\d+ email filed · \d+ task created/.test(hero.replace(/\d+ email filed/,'X')),
   hero.split('\n').filter(Boolean)[1])
ck('hero uses the smoked treatment',
   await p.locator('section.smoked-hero').count() > 0)
const card = p.locator('ol > li article').first()
const sh = await card.evaluate(el => getComputedStyle(el).boxShadow)
// Count layers with a NON-ZERO alpha. Tailwind always emits two fully
// transparent ring placeholders first, so "contains rgb twice" passes on a
// card with no shadow at all — which is exactly what an earlier version of
// this check did, while truncating the string before the real layers.
const alphas = [...sh.matchAll(/rgba?\(([^)]*)\)/g)]
  .map(m => { const parts = m[1].split(',').map(v => v.trim()); return parts.length === 4 ? parseFloat(parts[3]) : 1 })
const realLayers = alphas.filter(a => a > 0).length
ck('feed cards carry real elevation (not a bare border)', realLayers >= 2,
   `${realLayers} visible shadow layer(s) of ${alphas.length}`)
ck('expand affordance is a control, not a mono label',
   await p.getByRole('button',{name:/^Details$/}).count() > 0)
const detailsLabel = await p.getByRole('button',{name:/^Details$/}).first().evaluate(el => getComputedStyle(el).fontFamily)
ck('affordance uses sans, not the metadata mono', !/Plex/i.test(detailsLabel), detailsLabel.slice(0,30))

await p.getByRole('button',{name:/^Ask$/i}).click(); await p.waitForTimeout(1000)
ck('channels render as ONE divided container, not separate cards',
   await p.locator('ul.divide-y').count() > 0)
const rows = await p.locator('ul.divide-y > li').count()
ck('all seeded channels visible in one viewport (density)', rows >= 4, `${rows} rows`)
ck('no console errors', errs.length===0, errs.slice(0,2).join(' | '))
console.log(`\n${ok.filter(Boolean).length}/${ok.length} passed`)
await b.close(); process.exit(ok.every(Boolean)?0:1)
