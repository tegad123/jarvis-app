import { chromium } from 'playwright'
import { readFileSync } from 'node:fs'
const BASE = process.argv[2]
function pass_(){for(const l of readFileSync('/root/.hermes/.env','utf8').split('\n')){
  const m=/^\s*(?:export\s+)?APP_PASSPHRASE=(.*)$/.exec(l); if(m)return m[1].trim().replace(/^["']|["']$/g,'')}}
let ok=[]; const ck=(n,c,x='')=>{console.log(`  ${c?'PASS':'FAIL'}  ${n}${x?' — '+x:''}`);ok.push(c)}
const b=await chromium.launch({headless:true})
const errs=[]

async function session(width) {
  // deviceScaleFactor 1: a 2x render flatters hairlines and faint shadows that
  // vanish on an ordinary screen, which is the exact failure being fixed.
  const ctx=await b.newContext({viewport:{width,height:900},deviceScaleFactor:1})
  const p=await ctx.newPage()
  p.on('console',m=>{if(m.type()==='error'&&!/401/.test(m.text()))errs.push(m.text())})
  p.on('pageerror',e=>errs.push(String(e)))
  await p.goto(BASE,{waitUntil:'networkidle'})
  await p.getByPlaceholder('Passphrase').fill(pass_())
  await p.getByRole('button',{name:'Continue'}).click()
  await p.waitForSelector('ol > li',{timeout:20000}); await p.waitForTimeout(1200)
  return {ctx,p}
}
const overflow = (p) => p.evaluate(() => {
  const d=document.documentElement
  const clipped=el=>{for(let q=el.parentElement;q&&q!==document.body;q=q.parentElement)
    if(['hidden','auto','scroll'].includes(getComputedStyle(q).overflowX))return true;return false}
  const bad=[]
  for(const el of document.querySelectorAll('body *')){const r=el.getBoundingClientRect()
    if(r.width>0&&r.right>d.clientWidth+1&&!clipped(el))bad.push(el.tagName)}
  return {sw:d.scrollWidth,cw:d.clientWidth,bad:bad.slice(0,3)}
})

for (const W of [390, 1280]) {
  console.log(`\n=== ${W}px ===`)
  const {ctx,p} = await session(W)
  for (const theme of ['dark','light']) {
    const btn = p.getByRole('button',{name: theme==='dark' ? /Switch to dark mode/ : /Switch to light mode/})
    if (await btn.count()) { await btn.click(); await p.waitForTimeout(500) }
    for (const tab of ['Feed','Ask','Approvals','Leads']) {
      await p.getByRole('button',{name:new RegExp(`^${tab}$`,'i')}).click(); await p.waitForTimeout(800)
      const o = await overflow(p)
      ck(`${theme}/${tab} no overflow`, o.sw<=o.cw && !o.bad.length, `sw=${o.sw} cw=${o.cw} ${o.bad.join(',')}`)
    }
  }
  await p.getByRole('button',{name:/^Feed$/i}).click(); await p.waitForTimeout(700)

  // composition
  const main = await p.locator('main').evaluate(el => {
    const r = el.getBoundingClientRect(); return {w:+r.width.toFixed(0), left:+r.left.toFixed(0), right:+r.right.toFixed(0)} })
  const nav = await p.locator('nav').first().evaluate(el => {
    const r=el.getBoundingClientRect(); const c=getComputedStyle(el)
    return {w:+r.width.toFixed(0), h:+r.height.toFixed(0), left:+r.left.toFixed(0), pos:c.position} })
  if (W === 390) {
    ck('nav stays a bottom bar on mobile', nav.w >= 380 && nav.h < 200, `nav ${nav.w}x${nav.h}`)
  } else {
    ck('nav becomes a left rail on desktop', nav.w <= 96 && nav.h > 500 && nav.left === 0, `nav ${nav.w}x${nav.h} at x=${nav.left}`)
    const contentW = main.w - 88   // minus the rail clearance padding
    ck('content is a reading column, not full-bleed', contentW <= 760 && contentW >= 600, `${contentW}px of ${W}`)
    const centre = (main.left + main.right) / 2, avail = (nav.w + W) / 2
    ck('column is centred in the space beside the rail', Math.abs(centre - avail) < 60, `centre ${centre} vs ${avail}`)
    const hero = await p.locator('section.smoked-hero').evaluate(el => +el.getBoundingClientRect().width.toFixed(0))
    ck('hero is proportioned to the column, not the viewport', hero <= 720, `hero ${hero}px`)
  }

  // light-mode presence, measured
  const lightBtn = p.getByRole('button',{name:/Switch to light mode/})
  if (await lightBtn.count()) { await lightBtn.click(); await p.waitForTimeout(500) }
  const tone = await p.evaluate(() => {
    const parse = c => (c.match(/\d+/g)||[]).slice(0,3).map(Number)
    const lum = ([r,g,bb]) => 0.2126*r + 0.7152*g + 0.0722*bb
    const body = parse(getComputedStyle(document.body).backgroundColor)
    const card = document.querySelector('ol > li article')
    const cs = card ? getComputedStyle(card) : null
    const dot = document.querySelector('ol > li span[class*="rounded-full"]')
    return {
      bodyLum: +lum(body).toFixed(1),
      cardLum: cs ? +lum(parse(cs.backgroundColor)).toFixed(1) : null,
      borderAlpha: cs ? +(cs.borderTopColor.match(/[\d.]+\)$/)?.[0]?.replace(')','') || 1) : null,
      dotPx: dot ? +getComputedStyle(dot).width.replace('px','') : null,
      spine: getComputedStyle(document.querySelector('.wire-spine') || document.body).backgroundColor,
    }
  })
  const lift = tone.cardLum - tone.bodyLum
  ck('white cards visibly lift off the page (light)', lift >= 8, `card ${tone.cardLum} vs page ${tone.bodyLum} = +${lift.toFixed(1)}`)
  ck('hairline borders survive a 1x screen', tone.borderAlpha >= 0.12, `alpha ${tone.borderAlpha}`)
  ck('kind dots are +2px on spec', tone.dotPx >= 15, `${tone.dotPx}px spine dot`)
  ck('the spine carries the ink accent, not neutral grey', /36, 66, 124/.test(tone.spine), tone.spine)
  await ctx.close()
}
ck('no console errors', errs.length===0, errs.slice(0,2).join(' | '))
console.log(`\n${ok.filter(Boolean).length}/${ok.length} passed`)
await b.close(); process.exit(ok.every(Boolean)?0:1)
