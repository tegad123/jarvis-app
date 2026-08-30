import { chromium } from 'playwright'
import { readFileSync } from 'node:fs'
const [,, mockPath, rendPath, outPath, labelL, labelR] = process.argv
const b64 = p => 'data:image/png;base64,' + readFileSync(p).toString('base64')
const b = await chromium.launch({ headless: true })
const p = await b.newPage({ viewport: { width: 1000, height: 980 } })
await p.setContent(`<html><body style="margin:0;background:#191a1e;font:12px system-ui;color:#9aa1ae">
<div style="display:flex;gap:20px;padding:16px;align-items:flex-start">
  <div><div style="padding:4px 2px 8px">${labelL}</div><img src="${b64(mockPath)}" style="height:900px;display:block;border-radius:6px"></div>
  <div><div style="padding:4px 2px 8px">${labelR}</div><img src="${b64(rendPath)}" style="height:900px;display:block;border-radius:6px"></div>
</div></body></html>`)
await p.waitForTimeout(300)
const el = await p.$('div')
await el.screenshot({ path: outPath })
await b.close()
console.log('wrote', outPath)
