// Pixel-match the mobile (390) screens: pre-change bundle (OLD) vs the new one (NEW),
// shot back-to-back so the live feed data and relative timestamps are identical.
import { chromium } from 'playwright'
import { readFileSync, writeFileSync } from 'node:fs'
import { PNG } from 'pngjs'
const [OLD, NEW] = [process.argv[2], process.argv[3]]
const OUT = new URL('./shots/', import.meta.url).pathname
const pins = JSON.parse(readFileSync('/opt/testco/SEEDED_PINS.json','utf8'))
const ok=[]; const ck=(n,c,x='')=>{console.log(`  ${c?'PASS':'FAIL'}  ${n}${x?' — '+x:''}`);ok.push(c)}
const b=await chromium.launch()
async function shoot(base, tag){
  const ctx=await b.newContext({viewport:{width:390,height:844},deviceScaleFactor:1,colorScheme:'light'}); const p=await ctx.newPage()
  await p.goto(base,{waitUntil:'networkidle'}); await p.evaluate(()=>localStorage.clear()); await p.reload({waitUntil:'networkidle'}); await p.waitForTimeout(300)
  await p.screenshot({path:`${OUT}${tag}-390-login.png`,fullPage:true})
  await p.getByRole('button',{name:/^Dana/}).click(); await p.waitForTimeout(200)
  await p.screenshot({path:`${OUT}${tag}-390-login-selected.png`,fullPage:true})
  await p.getByPlaceholder('PIN').fill(pins.dana); await p.getByRole('button',{name:'Continue'}).click()
  await p.waitForSelector('ol > li',{timeout:20000}); await p.waitForTimeout(1200)
  await p.screenshot({path:`${OUT}${tag}-390-feed.png`})
  // second screen states: a Details row open, and dark mode
  const d = p.locator('ol article button',{hasText:'Details'}).first(); if (await d.count()) { await d.click(); await p.waitForTimeout(250) }
  await p.screenshot({path:`${OUT}${tag}-390-feed-details.png`})
  await p.getByRole('button',{name:/Switch to dark mode/}).click(); await p.waitForTimeout(500)
  await p.screenshot({path:`${OUT}${tag}-390-feed-dark.png`})
  // geometry fingerprint of the shell + first card (independent of data)
  const geo = await p.evaluate(()=>{const r=s=>{const e=document.querySelector(s); const b=e.getBoundingClientRect(); return [b.left,b.top,b.width,b.height].map(v=>+v.toFixed(1))}
    const cs=getComputedStyle(document.querySelector('ol article'))
    return {header:r('header'), h1:r('header h1'), hero:r('.smoked-hero'), nav:r('nav'), navBtn:r('nav button'), chips:r('main .flex.w-full.min-w-0'), spine:r('.wire-spine'), card:[...r('ol article').slice(0,3)], dot:r('ol li > div > span'), pad:cs.padding, radius:cs.borderRadius, shadow:cs.boxShadow}})
  await ctx.close(); return geo
}
const gOld = await shoot(OLD,'old'), gNew = await shoot(NEW,'new')
function diff(a,b_){const A=PNG.sync.read(readFileSync(a)),B=PNG.sync.read(readFileSync(b_)); if(A.width!==B.width||A.height!==B.height) return {err:`size ${A.width}x${A.height} vs ${B.width}x${B.height}`}
  let n=0,minY=1e9,maxY=0,minX=1e9,maxX=0; for(let y=0;y<A.height;y++)for(let x=0;x<A.width;x++){const i=(y*A.width+x)*4; if(Math.abs(A.data[i]-B.data[i])>2||Math.abs(A.data[i+1]-B.data[i+1])>2||Math.abs(A.data[i+2]-B.data[i+2])>2){n++;minY=Math.min(minY,y);maxY=Math.max(maxY,y);minX=Math.min(minX,x);maxX=Math.max(maxX,x)}}
  return {n,pct:100*n/(A.width*A.height),box:n?[minX,minY,maxX,maxY]:null}}
console.log('\n=== 390: pre-change bundle vs new bundle, same data, same minute ===')
for (const s of ['feed','feed-details','feed-dark']) { const d=diff(`${OUT}old-390-${s}.png`,`${OUT}new-390-${s}.png`); ck(`390 ${s} pixel-identical`, !d.err && d.n===0, d.err ?? (d.n? `${d.n} px (${d.pct.toFixed(3)}%) box ${d.box}`:'')) }
for (const s of ['login','login-selected']) { const d=diff(`${OUT}old-390-${s}.png`,`${OUT}new-390-${s}.png`); console.log(`  info  390 ${s}: ${d.err ?? d.n+' px ('+d.pct.toFixed(2)+'%) box '+d.box} — intentional: containment fix`) }
ck('390 shell geometry identical (header, hero, nav, chips, spine, card box/padding/radius/shadow)', JSON.stringify(gOld)===JSON.stringify(gNew), JSON.stringify(gOld)===JSON.stringify(gNew)?'':`old ${JSON.stringify(gOld)}\n        new ${JSON.stringify(gNew)}`)
await b.close(); const pass=ok.filter(Boolean).length; console.log(`\n${pass}/${ok.length} PASS`); process.exit(pass===ok.length?0:1)
