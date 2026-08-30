import { chromium } from 'playwright'
import { readFileSync } from 'node:fs'
const BASE = process.argv[2]
const pass = readFileSync('/root/.hermes/.env','utf8').split('\n')
  .map(l=>/^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(l)).find(Boolean)[1].trim().replace(/^["']|["']$/g,'')
const b = await chromium.launch({headless:true})
const p = await (await b.newContext({viewport:{width:390,height:844}})).newPage()
await p.goto(BASE,{waitUntil:'networkidle'})
await p.getByPlaceholder('Passphrase').fill(pass)
await p.getByRole('button',{name:'Continue'}).click()
await p.waitForSelector('ol > li',{timeout:20000})
await p.getByRole('button',{name:/^Ask$/}).click(); await p.waitForTimeout(600)
await p.getByRole('button',{name:/Ask Jarvis/}).click(); await p.waitForTimeout(400)

// Instrument the ACTUAL assistant paragraph, sampled fast.
await p.getByLabel('Message Jarvis').fill('Count slowly from one to twenty in words, one per line.')
const samples = []
const t0 = Date.now()
const iv = setInterval(async () => {
  try {
    const n = await p.evaluate(() => {
      // the assistant message is the last <li> containing a JARVIS label
      const lis = [...document.querySelectorAll('ol > li')]
      for (let i = lis.length - 1; i >= 0; i--) {
        if (/^jarvis/i.test((lis[i].textContent||'').trim())) return (lis[i].textContent||'').length
      }
      return 0
    })
    samples.push([Date.now()-t0, n])
  } catch {}
}, 100)
await p.getByRole('button',{name:'Send'}).click()
await p.waitForFunction(()=>document.querySelector('input[aria-label="Message Jarvis"]')?.disabled===false,null,{timeout:180000})
clearInterval(iv)
const nz = samples.filter(([,n])=>n>0)
const inc = nz.filter(([,n],i)=> i>0 && n>nz[i-1][1]).length
console.log('assistant-length timeline (ms, chars):')
console.log(nz.map(([t,n])=>`${t}:${n}`).join('  '))
console.log(`\ndistinct increasing observations: ${inc}`)
console.log(`first text at ${nz[0]?.[0]}ms, final length ${nz[nz.length-1]?.[1]}`)
await b.close()
