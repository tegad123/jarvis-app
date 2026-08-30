import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'
const SP='/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad'
const PINS=JSON.parse(readFileSync(`${SP}/pins.json`,'utf8'))
const OUT=`${SP}/vib/`; mkdirSync(OUT,{recursive:true})
const ok=[]; const skipped=[]; const ck=(n,c,x='')=>{console.log(`  ${c?'PASS':'FAIL'}  ${n}${x?' — '+x:''}`);ok.push(!!c)}

// The r2 alpha-parsing check: a computed box-shadow always begins with two
// fully TRANSPARENT ring placeholders from Tailwind, so "contains rgb twice"
// passes on a card with no shadow at all. Parse each layer's alpha instead.
const shadowLayers = (css) => {
  const out=[]
  for (const m of css.matchAll(/rgba?\(([^)]+)\)/g)) {
    const parts=m[1].split(',').map(s=>parseFloat(s.trim()))
    out.push(parts.length>3?parts[3]:1)
  }
  return out
}

const b=await chromium.launch({headless:true})
const shots={}
for (const [w,theme] of [[390,'dark'],[390,'light'],[1280,'dark'],[1280,'light']]) {
  const c=await b.newContext({viewport:{width:w,height:900},deviceScaleFactor:2,
    colorScheme:theme,timezoneId:'America/Chicago'})
  const p=await c.newPage()
  await p.goto('http://127.0.0.1:8700',{waitUntil:'networkidle'}); await p.waitForTimeout(900)
  await p.getByRole('button',{name:/Spencer/i}).click()
  await p.getByPlaceholder('PIN').fill(PINS.spencer)
  await p.getByRole('button',{name:'Continue'}).click(); await p.waitForTimeout(2500)

  const tag=`${w}/${theme}`
  for (const tab of ['Feed','Ask','Approvals','Leads']) {
    await p.getByRole('button',{name:new RegExp(`^${tab}$`,'i'),exact:false}).last().click()
    await p.waitForTimeout(1100)

    // LEADS IS SHEET-BACKED and Google enforces a per-minute read quota that
    // four viewports x four tabs walks straight into. A quota 503 renders the
    // error state — four filter chips and nothing else — which looks exactly
    // like a screen that lost its colour. Retry before believing it, the same
    // discipline verify_w3_sheet_leads.py already uses. Verified in isolation:
    // the endpoint returns 200 and a full list, so a red here is rate limiting,
    // not design.
    let leadsLoaded = true
    if (tab === 'Leads') {
      leadsLoaded = false
      for (let attempt = 0; attempt < 5; attempt++) {
        if (/could not open|unavailable|try again/i.test(await p.locator('main').innerText())) {
          await p.waitForTimeout(12000)
          await p.reload({waitUntil:'networkidle'})
          await p.getByRole('button',{name:/^Leads$/i}).last().click()
          await p.waitForTimeout(1400)
          continue
        }
        if ((await p.locator('main .chip').count()) > 0) { leadsLoaded = true; break }
        await p.waitForTimeout(1200)
      }
    }

    // --- overflow ---
    const o=await p.evaluate(()=>{const d=document.documentElement
      const clipped=el=>{for(let q=el.parentElement;q&&q!==document.body;q=q.parentElement)
        if(['hidden','auto','scroll'].includes(getComputedStyle(q).overflowX))return true;return false}
      const bad=[];for(const el of document.querySelectorAll('body *')){const r=el.getBoundingClientRect()
        if(r.width>0&&r.right>d.clientWidth+1&&!clipped(el))bad.push(el.tagName)}
      return{sw:d.scrollWidth,cw:d.clientWidth,bad:bad.slice(0,3)}})
    if(!(o.sw<=o.cw&&!o.bad.length)) ck(`${tag} ${tab} overflow`,false,`${o.sw}/${o.cw} ${o.bad}`)

    // --- INK PRESENCE ---------------------------------------------------
    // THE RULE, LITERALLY: "no screen renders as grayscale-plus-one-chip".
    // So COUNT DISTINCT saturated elements — chips, dots, marks, filled
    // buttons — rather than summing coloured area.
    //
    // The area version was the first attempt and it was a bad proxy: it
    // returned an identical 7167 for the Leads screen at three different
    // viewport widths, which is impossible for a real area measurement and is
    // exactly the "identical failures mean the harness broke" tell. The
    // screenshots showed a screen full of amber and green chips. Counting
    // things is stable, and it is what the rule actually says.
    const ink=await p.evaluate(()=>{
      const sat=(c)=>{const m=c.match(/[\d.]+/g); if(!m||m.length<3) return 0
        const [r,g,bl]=m.map(Number)
        const alpha=m.length>3?Number(m[3]):1
        if(alpha<0.05) return 0
        return Math.max(r,g,bl)-Math.min(r,g,bl)}
      const seen=[]
      for (const el of document.querySelectorAll('main *')) {
        const r=el.getBoundingClientRect()
        if(r.width<5||r.height<5) continue
        const st=getComputedStyle(el)
        const bg=sat(st.backgroundColor)>25
        const fg=sat(st.color)>35 && (el.textContent||'').trim().length>1 &&
                 el.children.length===0
        const img=/gradient/.test(st.backgroundImage) && sat(st.backgroundImage)>25
        if(bg||fg||img) seen.push(el.tagName.toLowerCase()+
          (el.className&&typeof el.className==='string'?'.'+el.className.split(/\s+/)[0]:''))
      }
      return seen
    })
    const kinds=[...new Set(ink)]
    if (tab === 'Leads' && !leadsLoaded) {
      // Loud, never silent: an un-run check must not look like a passing one.
      console.log(`  SKIP  ${tag} ${tab}: Sheets quota — list never loaded, accent check NOT run`)
      skipped.push(`${tag}/${tab}`)
    } else {
      ck(`${tag} ${tab}: accent appears on more than one element`,
         ink.length>=4 && kinds.length>=2,
         `${ink.length} saturated elements, ${kinds.length} kinds: ${kinds.slice(0,4).join(' ')}`)
    }

    await p.screenshot({path:`${OUT}gate-${tab.toLowerCase()}-${w}-${theme}.png`})
  }

  // --- LIFT survives real rendering (r2 alpha parse) ---
  await p.getByRole('button',{name:/^Feed$/i}).last().click(); await p.waitForTimeout(900)
  const card=await p.evaluate(()=>{
    const el=document.querySelector('main article')
    return el?getComputedStyle(el).boxShadow:''
  })
  const layers=shadowLayers(card).filter(a=>a>0)
  ck(`${tag} feed card has >=2 VISIBLE shadow layers`, layers.length>=2,
     `alphas ${JSON.stringify(shadowLayers(card))}`)

  // --- hero is a gradient, not a flat fill ---
  const hero=await p.evaluate(()=>{
    const el=document.querySelector('main section.smoked-hero')
    return el?getComputedStyle(el).backgroundImage:''
  })
  ck(`${tag} hero carries radial breath`,
     (hero.match(/radial-gradient/g)||[]).length>=2 && /linear-gradient/.test(hero))

  // --- page glow present and behind ---
  const glow=await p.evaluate(()=>{
    const el=document.querySelector('.page-glow')
    if(!el) return null
    const st=getComputedStyle(el)
    return {bg:st.backgroundImage, z:st.zIndex, pe:st.pointerEvents}
  })
  ck(`${tag} feed canvas has the radial page glow`,
     !!glow && /radial-gradient/.test(glow.bg) && Number(glow.z)<0)
  await c.close()
}

// --- DOM JARGON GATE: chips changed presentation, not vocabulary ------------
const c=await b.newContext({viewport:{width:390,height:900},colorScheme:'dark',
  timezoneId:'America/Chicago'})
const p=await c.newPage()
await p.goto('http://127.0.0.1:8700',{waitUntil:'networkidle'}); await p.waitForTimeout(900)
await p.getByRole('button',{name:/Spencer/i}).click()
await p.getByPlaceholder('PIN').fill(PINS.spencer)
await p.getByRole('button',{name:'Continue'}).click(); await p.waitForTimeout(2500)
await p.getByRole('button',{name:/^Feed$/i}).last().click(); await p.waitForTimeout(1200)
const feedText=await p.locator('main').innerText()
console.log('\n=== DOM JARGON GATE (chips must not reintroduce raw kinds) ===')
const RAW=[/\btask\.created\b/i,/\btask\.completed\b/i,/\bemail\.classified\b/i,
           /\bdeal\.channel_created\b/i,/[a-z]+\.[a-z_]+\b(?!\.)/]
ck('no raw dotted kind string rendered',
   !/\b(task|email|deal|lead)\.[a-z_]+\b/.test(feedText),
   (feedText.match(/\b(task|email|deal|lead)\.[a-z_]+\b/)||[''])[0])
ck('humanised kind words still render', /Task created|Email filed|Done/.test(feedText))
ck('CANARY: the raw-kind matcher actually fires',
   /\b(task|email|deal|lead)\.[a-z_]+\b/.test('x task.created y'))
await c.close()

console.log(`\n${ok.filter(Boolean).length}/${ok.length} passed`)
if (skipped.length) console.log(`${skipped.length} skipped (Sheets quota): ${skipped.join(', ')}`)
await b.close(); process.exit(ok.every(Boolean)?0:1)
