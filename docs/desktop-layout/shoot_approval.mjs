import { chromium } from 'playwright'; import { readFileSync } from 'node:fs'
const BASE=process.argv[2], OUT=new URL('./shots/', import.meta.url).pathname
const pins=JSON.parse(readFileSync('/opt/testco/SEEDED_PINS.json','utf8'))
const b=await chromium.launch(); const p=await b.newPage({viewport:{width:1440,height:900}})
await p.goto(BASE,{waitUntil:'networkidle'}); await p.getByRole('button',{name:/^Dana/}).click(); await p.getByPlaceholder('PIN').fill(pins.dana); await p.keyboard.press('Enter')
await p.waitForSelector('ol > li'); await p.getByRole('button',{name:/^Approvals$/}).click(); await p.waitForTimeout(1000)
const card = p.locator('article, li', {hasText:'William Mchenry'}).first(); await card.scrollIntoViewIfNeeded()
const txt = await card.innerText(); console.log(JSON.stringify({hasBody: /second walk-/.test(txt), hasPreviewKey: /preview/i.test(txt), snippet: txt.replace(/\n/g,' | ').slice(0,300)}))
await p.screenshot({path:`${OUT}after-1440-approval-fullbody.png`}); await b.close()
