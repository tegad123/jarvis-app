import { chromium } from 'playwright'
import { readFileSync } from 'node:fs'
const BASE = process.argv[2]
const pass = readFileSync('/root/.hermes/.env','utf8').split('\n')
  .map(l=>/^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(l)).find(Boolean)[1]
  .trim().replace(/^["']|["']$/g,'')
const b = await chromium.launch({headless:true})
const ctx = await b.newContext({viewport:{width:390,height:844},deviceScaleFactor:2})
const p = await ctx.newPage()
await p.goto(BASE,{waitUntil:'networkidle'})
await p.screenshot({path:'shots/final-01-login.png'})
await p.getByPlaceholder('Passphrase').fill(pass)
await p.getByRole('button',{name:'Continue'}).click()
await p.waitForSelector('ol > li',{timeout:20000})
await p.waitForTimeout(600)
await p.screenshot({path:'shots/final-02-feed-light.png'})
await p.getByRole('button',{name:/Switch to dark mode/}).click()
await p.waitForTimeout(500)
await p.screenshot({path:'shots/final-03-feed-dark.png'})
await p.getByRole('button',{name:/^Ask$/}).click(); await p.waitForTimeout(400)
await p.screenshot({path:'shots/final-04-ask.png'})
await b.close(); console.log('viewport screenshots written')
