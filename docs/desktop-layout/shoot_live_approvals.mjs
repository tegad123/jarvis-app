import { chromium } from 'playwright'; import { readFileSync } from 'node:fs'
const OUT=new URL('./shots/', import.meta.url).pathname
const pass=()=>{for(const l of readFileSync('/root/.hermes/.env','utf8').split('\n')){const m=/^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(l); if(m)return m[1].trim().replace(/^["']|["']$/g,'')}}
const b=await chromium.launch(); const p=await b.newPage({viewport:{width:1440,height:900}})
await p.goto('http://127.0.0.1:8700',{waitUntil:'networkidle'})
await p.getByRole('button',{name:'Operator access'}).click(); await p.getByPlaceholder('Operator passphrase').fill(pass()); await p.keyboard.press('Enter')
await p.waitForSelector('nav',{timeout:20000}); await p.getByRole('button',{name:/^Approvals$/}).click(); await p.waitForTimeout(800)
console.log(JSON.stringify({approvalsText: (await p.locator('main').innerText()).replace(/\n/g,' | ').slice(0,160)}))
await p.screenshot({path:`${OUT}live-1440-operator-approvals.png`}); await b.close()
