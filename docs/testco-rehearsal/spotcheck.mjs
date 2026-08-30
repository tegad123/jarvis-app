import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'
const INV = JSON.parse(readFileSync('/opt/testco/fixtures/PASS0_INVENTORY.json','utf8'))
const OUT = '/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad/spot/'
mkdirSync(OUT, { recursive: true })
const B = 'http://127.0.0.1:8710'
const b = await chromium.launch({ headless: true })

for (const who of ['dana','priya','omar']) {
  const pin = INV.accounts[who].pin, role = INV.accounts[who].role
  const ctx = await b.newContext({ viewport:{width:390,height:1100}, deviceScaleFactor:2,
    colorScheme:'dark', timezoneId:'America/Chicago' })
  const p = await ctx.newPage()
  await p.goto(B,{waitUntil:'networkidle'}); await p.waitForTimeout(900)
  await p.getByRole('button',{name:new RegExp(who,'i')}).click()
  await p.getByPlaceholder('PIN').fill(pin)
  await p.getByRole('button',{name:'Continue'}).click(); await p.waitForTimeout(2600)

  console.log(`\n########## ${who} (${role}) ##########`)
  for (const tab of ['Feed','Ask','Approvals','Leads']) {
    await p.getByRole('button',{name:new RegExp(`^${tab}$`,'i'),exact:false}).last().click()
    await p.waitForTimeout(1300)
    await p.screenshot({ path:`${OUT}${who}-${tab.toLowerCase()}.png` })
    const txt = await p.locator('main').innerText()
    const head = txt.split('\n').filter(l=>l.trim()).slice(0,5).join(' · ')
    console.log(`  [${tab}] ${head.slice(0,150)}`)
  }
  await ctx.close()
}
await b.close()
console.log('\nshots -> ' + OUT)
