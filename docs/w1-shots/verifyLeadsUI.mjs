import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'
const BASE = process.argv[2]
const OUT = new URL('./vis/', import.meta.url).pathname; mkdirSync(OUT,{recursive:true})
function pass_(){for(const l of readFileSync('/root/.hermes/.env','utf8').split('\n')){
  const m=/^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(l); if(m)return m[1].trim().replace(/^["']|["']$/g,'')}}
let ok=[]; const ck=(n,c,x='')=>{console.log(`  ${c?'PASS':'FAIL'}  ${n}${x?' — '+x:''}`);ok.push(c)}
const b=await chromium.launch({headless:true})
const ctx=await b.newContext({viewport:{width:390,height:844},deviceScaleFactor:2})
const p=await ctx.newPage()
const errs=[]; p.on('console',m=>{if(m.type()==='error'&&!/401/.test(m.text()))errs.push(m.text())})
p.on('pageerror',e=>errs.push(String(e)))
await p.goto(BASE,{waitUntil:'networkidle'})
await p.getByPlaceholder('Passphrase').fill(pass_())
await p.getByRole('button',{name:'Continue'}).click()
await p.waitForSelector('ol > li',{timeout:20000}); await p.waitForTimeout(1000)
await p.getByRole('button',{name:/^Leads$/i}).click(); await p.waitForTimeout(1500)

ck('app-native source shows editing controls', await p.getByRole('button',{name:'New lead'}).count()>0)
const firstStatus = p.getByRole('button',{name:/^Status: /}).first()
ck('status chip is a control', await firstStatus.count()>0)
const before = await firstStatus.getAttribute('aria-label')
await firstStatus.click(); await p.waitForTimeout(1800)
const after = await p.getByRole('button',{name:/^Status: /}).first().getAttribute('aria-label')
ck('tapping the status chip persists a change', before !== after, `${before} -> ${after}`)

const tempBtn = p.getByRole('button',{name:/^Temperature: /}).first()
const tBefore = await tempBtn.getAttribute('aria-label')
await tempBtn.click(); await p.waitForTimeout(1800)
const tAfter = await p.getByRole('button',{name:/^Temperature: /}).first().getAttribute('aria-label')
ck('temperature cycles too', tBefore !== tAfter, `${tBefore} -> ${tAfter}`)

// create
const countBefore = (await p.locator('ul > li').count())
await p.getByRole('button',{name:'New lead'}).click(); await p.waitForTimeout(400)
await p.getByPlaceholder('Name').fill('Verification Lead')
await p.getByRole('button',{name:'Add'}).click(); await p.waitForTimeout(2000)
ck('create adds a lead', (await p.getByText('Verification Lead').count())>0)

// archive it again
const row = p.locator('ul > li').filter({hasText:'Verification Lead'})
await row.getByRole('button',{name:'Archive'}).first().click(); await p.waitForTimeout(2000)
ck('archive removes it from the active list', (await p.getByText('Verification Lead').count())===0)

await p.screenshot({path:OUT+'w1-leads-light.png'})
await p.getByRole('button',{name:/Switch to dark mode/}).click(); await p.waitForTimeout(600)
await p.screenshot({path:OUT+'w1-leads-dark.png'})
const o=await p.evaluate(()=>{const d=document.documentElement
  const clipped=el=>{for(let q=el.parentElement;q&&q!==document.body;q=q.parentElement)
    if(['hidden','auto','scroll'].includes(getComputedStyle(q).overflowX))return true;return false}
  const bad=[];for(const el of document.querySelectorAll('body *')){const r=el.getBoundingClientRect()
    if(r.width>0&&r.right>d.clientWidth+1&&!clipped(el))bad.push(el.tagName)}
  return{sw:d.scrollWidth,cw:d.clientWidth,bad:bad.slice(0,3)}})
ck('no horizontal overflow', o.sw<=o.cw&&!o.bad.length, `sw=${o.sw} cw=${o.cw} ${o.bad}`)
ck('no console errors', errs.length===0, errs.slice(0,2).join(' | '))
console.log(`\n${ok.filter(Boolean).length}/${ok.length} passed`)
await b.close(); process.exit(ok.every(Boolean)?0:1)
