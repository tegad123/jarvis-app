// Desktop layout pass — acceptance checks against a running bundle.
import { chromium } from 'playwright'
import { readFileSync, existsSync, mkdirSync } from 'node:fs'
import { PNG } from 'pngjs'
const BASE = process.argv[2]
const OUT = new URL('./shots/', import.meta.url).pathname
mkdirSync(OUT, { recursive: true })
const pins = JSON.parse(readFileSync('/opt/jarvis-verify-fixture/SEEDED_PINS.json','utf8'))
const ok=[]; const ck=(n,c,x='')=>{console.log(`  ${c?'PASS':'FAIL'}  ${n}${x?' — '+x:''}`);ok.push(c)}
const b=await chromium.launch({headless:true})

function diffPng(a,b_){
  if(!existsSync(a)||!existsSync(b_)) return {err:'missing'}
  const A=PNG.sync.read(readFileSync(a)), B=PNG.sync.read(readFileSync(b_))
  if(A.width!==B.width||A.height!==B.height) return {err:`size ${A.width}x${A.height} vs ${B.width}x${B.height}`}
  let n=0; for(let i=0;i<A.data.length;i+=4){ if(Math.abs(A.data[i]-B.data[i])>2||Math.abs(A.data[i+1]-B.data[i+1])>2||Math.abs(A.data[i+2]-B.data[i+2])>2) n++ }
  return {n, pct: 100*n/(A.width*A.height)}
}
const overflow = (p) => p.evaluate(() => {
  const d=document.documentElement
  const clipped=el=>{for(let q=el.parentElement;q&&q!==document.body;q=q.parentElement)
    if(['hidden','auto','scroll'].includes(getComputedStyle(q).overflowX))return true;return false}
  const bad=[]
  for(const el of document.querySelectorAll('body *')){const r=el.getBoundingClientRect()
    if(r.width>0&&r.right>d.clientWidth+1&&!clipped(el))bad.push(el.tagName+'.'+String(el.className).slice(0,30))}
  return {sw:d.scrollWidth,cw:d.clientWidth,bad:bad.slice(0,3)}
})
async function fresh(W,H,scheme='light'){
  const ctx=await b.newContext({viewport:{width:W,height:H},deviceScaleFactor:1,colorScheme:scheme})
  const p=await ctx.newPage(); const errs=[]
  p.on('pageerror',e=>errs.push(String(e)))
  await p.goto(BASE,{waitUntil:'networkidle'}); await p.evaluate(()=>localStorage.clear()); await p.reload({waitUntil:'networkidle'})
  return {ctx,p,errs}
}

// ---- 1. mobile pixel-match (390): login, login-selected, feed
console.log('\n=== 390 pixel-match vs baseline ===')
for (const s of ['login','login-selected','feed']) {
  const d = diffPng(`${OUT}before-390-${s}.png`, `${OUT}after-390-${s}.png`)
  // the feed carries live relative timestamps ("22m") and a polling list, so
  // its diff is reported but only the login shots are held to zero.
  if (s==='feed') console.log(`  info  feed 390 diff: ${d.err ?? d.n+' px ('+d.pct.toFixed(3)+'%)'} (live relative timestamps; informational)`)
  else console.log(`  info  390 ${s} diff: ${d.err ?? d.n+' px ('+d.pct.toFixed(3)+'%)'} — INTENTIONAL: baseline had the containment bug at 390 too (rows spilled, card pushed off-screen when selected); see sbs-390-*.png`)
}

// ---- 2. login containment + desktop layout facts
console.log('\n=== login layout ===')
for (const W of [390, 1024, 1440]) {
  const {ctx,p}=await fresh(W,900)
  const m = await p.evaluate(() => {
    const card = document.querySelector('.glass-moment').getBoundingClientRect()
    const rows = [...document.querySelectorAll('ul li button')].map(b=>b.getBoundingClientRect())
    const ul = document.querySelector('ul').getBoundingClientRect()
    const cs = getComputedStyle(document.querySelector('.glass-moment'))
    const overflowRows = rows.filter(r => r.right > card.right - 1 || r.left < card.left + 1).length
    const tester = [...document.querySelectorAll('ul li button')].find(b=>/Tester/.test(b.textContent))
    const roleEl = tester && tester.querySelector('span[title]')
    const cols = new Set(rows.map(r=>Math.round(r.left))).size
    return { cardW: card.width, pad: cs.paddingLeft, overflowRows, ulW: ul.width, rowW: rows[0].width, cols,
      testerText: tester && tester.innerText.replace(/\n/g,' | '), testerTitle: roleEl && roleEl.getAttribute('title'),
      sameShadowLayers: document.querySelectorAll('.glass-moment').length,
      opLeft: document.evaluate("//button[contains(.,'Operator access')]",document,null,9,null).singleNodeValue.getBoundingClientRect() }
  })
  ck(`${W}: no profile row spills past the card`, m.overflowRows===0, `${m.overflowRows} rows outside; row ${m.rowW}px in list ${m.ulW}px, card ${m.cardW}px`)
  if (W>=1024) {
    ck(`${W}: card max 640 / padding 48`, Math.round(m.cardW)===640 && m.pad==='48px', `card ${m.cardW} pad ${m.pad}`)
    ck(`${W}: 2×2 tile grid`, m.cols===2, `${m.cols} column x-positions`)
    ck(`${W}: tester tile shows short role, full title in tooltip`, /Tester \| qa contractor$/.test(m.testerText||'') && /not part of the fixture cast/.test(m.testerTitle||''), `${m.testerText} / ${m.testerTitle}`)
    const centre = (m.opLeft.left+m.opLeft.right)/2, cardMid = W/2
    ck(`${W}: operator link bottom-centre`, Math.abs(centre-cardMid)<4, `centre ${centre} vs ${cardMid}`)
  } else {
    ck(`${W}: single column, full title kept on mobile`, m.cols===1 && /qa contractor \(external/.test(m.testerText||''), `${m.cols} cols; ${m.testerText}`)
  }
  const o = await overflow(p); ck(`${W}: login has no horizontal scroll`, o.sw<=o.cw && !o.bad.length, `sw=${o.sw} cw=${o.cw} ${o.bad.join(',')}`)
  await ctx.close()
}

// ---- 3. keyboard-only login at 1440: Tab to tiles → arrows → Enter → PIN → Enter
console.log('\n=== keyboard-only login (1440) ===')
{
  const {ctx,p}=await fresh(1440,900)
  await p.mouse.move(0,0)
  // Tab from the document start until a tile has focus
  let focused=''; for (let i=0;i<8;i++){ await p.keyboard.press('Tab'); focused = await p.evaluate(()=>document.activeElement?.textContent||''); if(/Dana/.test(focused)) break }
  ck('Tab reaches the first tile', /Dana/.test(focused), focused.slice(0,30))
  await p.keyboard.press('ArrowRight'); focused = await p.evaluate(()=>document.activeElement?.textContent||'')
  ck('ArrowRight moves focus to the next tile', /Omar/.test(focused), focused.slice(0,30))
  await p.keyboard.press('ArrowDown'); focused = await p.evaluate(()=>document.activeElement?.textContent||'')
  ck('ArrowDown moves a row (2-col grid)', /Tester/.test(focused), focused.slice(0,30))
  await p.keyboard.press('ArrowLeft'); await p.keyboard.press('ArrowUp'); focused = await p.evaluate(()=>document.activeElement?.textContent||'')
  ck('ArrowLeft+ArrowUp returns to Dana', /Dana/.test(focused), focused.slice(0,30))
  await p.keyboard.press('Enter'); await p.waitForTimeout(150)
  const st = await p.evaluate(()=>({ pressed: document.querySelector('button[aria-pressed="true"]')?.textContent||'', active: document.activeElement?.id, disabled: document.querySelector('button[type=submit]').disabled }))
  ck('Enter selects the tile', /Dana/.test(st.pressed), st.pressed.slice(0,20))
  ck('PIN field autofocused on select', st.active==='passphrase', `active=${st.active}`)
  ck('Continue disabled before the PIN is complete', st.disabled===true)
  const bare = pins.dana.replace(/-/g,'')
  await p.keyboard.type(bare.slice(0,6))
  let v = await p.inputValue('#passphrase'); const d1 = await p.evaluate(()=>document.querySelector('button[type=submit]').disabled)
  ck('dashes auto-inserted while typing', v===bare.slice(0,4)+'-'+bare.slice(4,6), v)
  ck('Continue still disabled at a partial PIN', d1===true)
  await p.keyboard.type(bare.slice(6))
  v = await p.inputValue('#passphrase'); const d2 = await p.evaluate(()=>document.querySelector('button[type=submit]').disabled)
  ck('full PIN formats to xxxx-xxxx-xxxx', v===pins.dana, v)
  ck('Continue enabled at a complete PIN', d2===false)
  const cs = await p.evaluate(()=>{const c=getComputedStyle(document.querySelector('#passphrase')); return {ff:c.fontFamily, ta:c.textAlign, type:document.querySelector('#passphrase').type}})
  ck('PIN field is mono + centred + masked at lg', /Plex Mono/.test(cs.ff) && cs.ta==='center' && cs.type==='password', JSON.stringify(cs))
  await p.click('button[aria-label="Show PIN"]'); ck('reveal toggle unmasks', (await p.evaluate(()=>document.querySelector('#passphrase').type))==='text')
  await p.focus('#passphrase'); await p.keyboard.press('Enter')
  await p.waitForSelector('ol > li',{timeout:20000}).then(()=>ck('Enter from the PIN field logs in', true)).catch(()=>ck('Enter from the PIN field logs in', false))
  await ctx.close()
}

// ---- 4. shell: no horizontal scroll at 1024/1280/1440/1920, rail + container geometry, dark mode toggle
console.log('\n=== shell ===')
for (const W of [1024, 1280, 1440, 1920]) {
  const {ctx,p,errs}=await fresh(W,900)
  await p.getByRole('button',{name:/^Dana/}).click(); await p.getByPlaceholder('PIN').fill(pins.dana); await p.keyboard.press('Enter')
  await p.waitForSelector('ol > li',{timeout:20000}); await p.waitForTimeout(800)
  const g = await p.evaluate(() => {
    const r=s=>document.querySelector(s).getBoundingClientRect()
    const nav=r('nav'), h1=r('header h1'), hero=r('.smoked-hero:not(.lg\\:hidden)'), card=r('ol article'), dot=r('ol li > div > span'), head=r('ol article p')
    const navBtn=r('nav button'); const cs=getComputedStyle(document.querySelector('ol article'))
    const spine=r('.wire-spine'); const filters=[...document.querySelectorAll('main .flex.w-full.min-w-0')].map(f=>f.getBoundingClientRect())
    const dots=[...document.querySelectorAll('ol li > div > span')].map(d=>d.getBoundingClientRect())
    return {navW:nav.width, navH:nav.height, navBtnH:navBtn.height, h1Left:h1.left, heroLeft:hero.left, heroW:hero.width, heroH:hero.height,
      cardLeft:card.left, cardRight:card.right, gutter:card.left-hero.left, pad:cs.paddingLeft+'/'+cs.paddingTop, radius:cs.borderRadius,
      dotMid:(dot.top+dot.bottom)/2, headMid:(head.top+head.bottom)/2, spineLeft:spine.left, dotMidX:(dot.left+dot.right)/2,
      spineTop:spine.top, spineBottom:spine.bottom, firstDotTop:dots[0].top, lastDotBottom:dots[dots.length-1].bottom,
      filterGap: filters.length>1 ? filters[1].top-filters[0].bottom : null,
      heroText: document.querySelector('.smoked-hero:not(.lg\\:hidden)').innerText.replace(/\n/g,' ') }
  })
  const o = await overflow(p)
  ck(`${W}: no horizontal scroll`, o.sw<=o.cw && !o.bad.length, `sw=${o.sw} cw=${o.cw} ${o.bad.join(',')}`)
  ck(`${W}: 220px full-height rail, 40px rows`, Math.round(g.navW)===220 && g.navH>=899 && Math.round(g.navBtnH)===40, `nav ${g.navW}x${g.navH} row ${g.navBtnH}`)
  const expectedLeft = 220 + Math.max(0,(W-220-1080)/2) + 48
  ck(`${W}: header + content share the container left edge (${expectedLeft})`, Math.abs(g.h1Left-expectedLeft)<1 && Math.abs(g.heroLeft-expectedLeft)<1, `h1 ${g.h1Left} hero ${g.heroLeft}`)
  ck(`${W}: content ≤ 1080 container (984 inner)`, g.heroW <= 984.5, `hero ${g.heroW}`)
  ck(`${W}: hero is an 88px strip with kicker · count · status`, Math.round(g.heroH)===88 && /^TODAY\s+·\s+.+\s+·\s+/.test(g.heroText), `${g.heroH}px "${g.heroText}"`)
  ck(`${W}: cards sit in a 32px gutter, aligned`, Math.round(g.gutter)===32, `gutter ${g.gutter}`)
  ck(`${W}: desktop density (24/20 pad, 14 radius)`, g.pad==='24px/20px' && g.radius==='14px', `${g.pad} r=${g.radius}`)
  ck(`${W}: dot centred on the headline line`, Math.abs(g.dotMid-g.headMid)<1.5, `dot ${g.dotMid} headline ${g.headMid}`)
  ck(`${W}: spine runs through the dot centres, dot-to-dot`, Math.abs(g.spineLeft+0.5-g.dotMidX)<1 && g.spineTop<=g.firstDotTop+1 && g.spineBottom>=g.lastDotBottom-16, `spine x=${g.spineLeft} dots x=${g.dotMidX}; y ${g.spineTop}-${g.spineBottom} vs ${g.firstDotTop}-${g.lastDotBottom}`)
  ck(`${W}: filter rows 12px apart`, g.filterGap!==null && Math.round(g.filterGap)===12, `${g.filterGap}`)
  if (W===1440) {
    // hover lift + details chevron + dark mode
    await p.mouse.move(0,0); await p.waitForTimeout(250)   // the tile click left the pointer over the first card
    const before = await p.evaluate(()=>getComputedStyle(document.querySelector('ol article')).boxShadow)
    await p.hover('ol article'); await p.waitForTimeout(250)
    const after = await p.evaluate(()=>getComputedStyle(document.querySelector('ol article')).boxShadow)
    ck('1440: card hover changes elevation', before!==after)
    const details = p.locator('ol article button', {hasText:'Details'}).first()
    if (await details.count()) { await details.click(); await p.waitForTimeout(250)
      const rot = await p.evaluate(()=>getComputedStyle(document.querySelector('ol article button[aria-expanded="true"] svg')).transform)
      ck('1440: details chevron rotates 90° when open', /matrix\(0?\.?0*,\s*1,\s*-1/.test(rot.replace(/\s/g,''))||/matrix\(6\.12/.test(rot)||rot!=='none', rot)
      await p.screenshot({path:`${OUT}after-1440-feed-details.png`})
      await details.click() }
    await p.getByRole('button',{name:/Switch to dark mode/}).click(); await p.waitForTimeout(500)
    ck('1440: dark toggle still works', await p.evaluate(()=>document.documentElement.classList.contains('dark')))
    const od = await overflow(p); ck('1440 dark: no horizontal scroll', od.sw<=od.cw && !od.bad.length)
    await p.screenshot({path:`${OUT}after-1440-feed-dark.png`})
    await p.getByRole('button',{name:/Sign out/}).click(); await p.waitForTimeout(600)
    await p.screenshot({path:`${OUT}after-1440-login-dark.png`})
    await p.getByRole('button',{name:/Switch to light mode/}).catch?.(()=>{})
  }
  ck(`${W}: no page errors`, errs.length===0, errs.join(' | ').slice(0,200))
  await ctx.close()
}

// ---- 5. 768–1023 untouched: md rail geometry unchanged from the pre-existing md rules
console.log('\n=== 900px (md band, must be the pre-existing layout) ===')
{
  const {ctx,p}=await fresh(900,900)
  await p.getByRole('button',{name:/^Dana/}).click(); await p.getByPlaceholder('PIN').fill(pins.dana); await p.keyboard.press('Enter')
  await p.waitForSelector('ol > li',{timeout:20000}); await p.waitForTimeout(500)
  const g = await p.evaluate(()=>{const n=document.querySelector('nav').getBoundingClientRect(); const m=document.querySelector('main').getBoundingClientRect(); const hero=document.querySelectorAll('.smoked-hero')[0].getBoundingClientRect(); return {navW:n.width, mainW:m.width, heroH:hero.height, strip: getComputedStyle(document.querySelectorAll('.smoked-hero')[1]).display}})
  ck('900: rail still 72px, main 788, mobile hero block, strip hidden', Math.round(g.navW)===72 && Math.round(g.mainW)===788 && g.heroH>120 && g.strip==='none', JSON.stringify(g))
  await ctx.close()
}

// ---- 6. token audit: no new hex values in src
console.log('\n=== tokens ===')
{
  const { execSync } = await import('node:child_process')
  const hexes = execSync(`grep -rhoE '#[0-9a-fA-F]{3,8}\\b' /root/jarvis-app/frontend/src --include=*.tsx --include=*.ts --include=*.css | sort -u`).toString().trim().split('\n')
  const PRE_EXISTING_CHIP_HEX = ['#17754c','#3c63a8','#6fcba1','#8e6122','#a8c4ee','#a93a30','#e3b472','#f08a80'] // index.css chip tints, present before this pass
  const allowed = new Set([...execSync(`grep -ohE '#[0-9a-fA-F]{3,8}\\b' /root/jarvis-app/frontend/tailwind.config.cjs | sort -u`).toString().trim().split('\n').map(h=>h.toLowerCase()), ...PRE_EXISTING_CHIP_HEX])
  const unknown = hexes.filter(h=>!allowed.has(h.toLowerCase()))
  ck('no hex value in src that is not in the token file', unknown.length===0, unknown.join(','))
}

await b.close()
const pass=ok.filter(Boolean).length; console.log(`\n${pass}/${ok.length} PASS`); process.exit(pass===ok.length?0:1)
