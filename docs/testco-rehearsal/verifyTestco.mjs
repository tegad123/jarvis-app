import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'
const BASE='http://127.0.0.1:8710'
const ENV='/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad/testco-clone/hermes/.env'
const OUT=new URL('./vis/',import.meta.url).pathname; mkdirSync(OUT,{recursive:true})
const pass=(()=>{for(const l of readFileSync(ENV,'utf8').split('\n')){
  const m=/^APP_PASSPHRASE=(.*)$/.exec(l); if(m)return m[1].trim()}})()
let ok=[]; const ck=(n,c,x='')=>{console.log(`  ${c?'PASS':'FAIL'}  ${n}${x?' — '+x:''}`);ok.push(c)}
const b=await chromium.launch({headless:true})
const p=await (await b.newContext({viewport:{width:390,height:844}})).newPage()
await p.goto(BASE,{waitUntil:'networkidle'})
await p.getByPlaceholder('Passphrase').fill(pass)
await p.getByRole('button',{name:'Continue'}).click()
await p.waitForTimeout(2500)
ck('login succeeds as testco', !(await p.getByPlaceholder('Passphrase').count()))

// Walk every tab and collect the RENDERED text.
const seen = {}
for (const tab of ['Feed','Ask','Approvals','Leads']) {
  await p.getByRole('button',{name:new RegExp(`^${tab}$`,'i')}).click(); await p.waitForTimeout(1200)
  seen[tab] = await p.locator('body').innerText()
  await p.screenshot({path:`${OUT}testco-${tab.toLowerCase()}.png`})
}
const all = Object.values(seen).join('\n')

console.log('\n=== NO PRIOR-TENANT DATA IN ANY RENDERED PAGE ===')
const leaks = {
  '34dev': /34dev/i, 'cqhouston': /cqhouston/i, 'spencerhuck': /spencerhuck/i,
  'Spencer (owner name)': /\bSpencer\b/, 'Cassandra': /\bCassandra\b/,
  'real lead names': /Joaquin|Maldonado|Mchenry/i,
  'deal channels': /Fairmont|Larkspur|Brook Ct|Tiel Way/i,
}
for (const [name, re] of Object.entries(leaks)) {
  const hit = re.exec(all)
  ck(`no ${name}`, hit === null, hit ? `FOUND ${JSON.stringify(hit[0])}` : '')
}
console.log('\n=== TESTCO IDENTITY IS THE ONE IN PLAY ===')
ck('greets the testco owner', /Dana/.test(seen.Feed), (seen.Feed.split('\n')[0]||'').slice(0,40))
ck('feed is empty for a fresh tenant', /Nothing yet today|No activity|Summary unavailable/i.test(seen.Feed) || !/Filed an email/.test(seen.Feed))
ck('leads empty', !/Joaquin|lead/i.test(seen.Leads) || /No leads|nothing/i.test(seen.Leads), seen.Leads.split('\n').slice(0,3).join(' / ').slice(0,70))
ck('only the seeded direct line exists', /Ask Jarvis/.test(seen.Ask) && !/Fairmont|Brook/.test(seen.Ask))
console.log(`\n${ok.filter(Boolean).length}/${ok.length} passed`)
await b.close(); process.exit(ok.every(Boolean)?0:1)
