import { chromium } from 'playwright'
import { readFileSync } from 'node:fs'
const BASE=process.argv[2]; const OUT=new URL('./shots/', import.meta.url).pathname
const pass=()=>{for(const l of readFileSync('/opt/jarvis-verify-fixture/hermes/.env','utf8').split('\n')){const m=/^\s*APP_PASSPHRASE=(.*)$/.exec(l); if(m)return m[1].trim().replace(/^["']|["']$/g,'')}}
const b=await chromium.launch(); const p=await b.newPage({viewport:{width:1440,height:900}})
await p.goto(BASE,{waitUntil:'networkidle'}); await p.evaluate(()=>localStorage.clear()); await p.reload({waitUntil:'networkidle'})
await p.getByRole('button',{name:'Operator access'}).click(); await p.getByPlaceholder('Operator passphrase').fill(pass()); await p.keyboard.press('Enter')
await p.waitForSelector('#outbox-heading',{timeout:20000}); await p.waitForTimeout(800)
await p.locator('#outbox-heading').scrollIntoViewIfNeeded(); await p.waitForTimeout(300)
const n = await p.locator('section[aria-labelledby=outbox-heading] li').count()
await p.locator('section[aria-labelledby=outbox-heading] li button').first().click(); await p.waitForTimeout(300)
await p.screenshot({path:`${OUT}after-1440-operator-outbox.png`})
const p2=await b.newPage({viewport:{width:390,height:844}}); await p2.goto(BASE,{waitUntil:'networkidle'})
await p2.getByRole('button',{name:'Operator access'}).click(); await p2.getByPlaceholder('Operator passphrase').fill(pass()); await p2.keyboard.press('Enter')
await p2.waitForSelector('ol > li',{timeout:20000}); await p2.waitForTimeout(600)
const vis = await p2.locator('#outbox-heading').isVisible().catch(()=>false)
console.log(JSON.stringify({rows:n, hiddenOnMobile: !vis}))
await b.close()
