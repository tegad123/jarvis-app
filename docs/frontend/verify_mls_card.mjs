#!/usr/bin/env node
/**
 * The MLS results card draws the stored run and nothing clickable.
 *
 * Renders the real MlsResults (frontend/src/components/MlsCard.tsx) with
 * react-dom/server after bundling it with esbuild, then checks:
 *   - a done run draws one row per listing with its address, price, beds and
 *     $/sq ft, and the summary's note and suggested range (presence);
 *   - listing text holding <a>/<script> HTML renders escaped: no <a, no
 *     <script, no <button anywhere in the card;
 *   - an expired run says "These results have expired" and draws no table;
 *   - a stopped run draws its reason and the site's choices, no table;
 *   - a run that is not the reader's (null) draws nothing.
 * --canary swaps in a card that drops the expiry check; the expired checks
 * must fail.
 *
 *   node docs/frontend/verify_mls_card.mjs --node-modules <frontend>/node_modules [--canary]
 */
import { createRequire } from 'node:module'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const args = process.argv.slice(2)
const nm = resolve(args[args.indexOf('--node-modules') + 1] || 'frontend/node_modules')
const canary = args.includes('--canary')
const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const require = createRequire(join(nm, 'noop.js'))
const esbuild = require('esbuild')

const work = mkdtempSync(join(tmpdir(), 'mls-card-'))
const entry = join(work, 'entry.tsx')
const component = join(repo, 'frontend/src/components/MlsCard.tsx')
writeFileSync(entry, `
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import * as real from ${JSON.stringify(component)}
const MlsResults = ${canary ? `({ run }) => real.MlsResults({ run: run && run !== 'loading' ? { ...run, expired: false } : run })` : 'real.MlsResults'}
const L = (o) => ({ address: '', status: null, list_price: null, sold_price: null, list_date: null, pending_date: null,
  sold_date: null, beds: null, baths: null, sqft: null, year_built: null, property_type: null, area: null,
  zip_code: null, days_on_market: null, mls_number: null, distance_miles: null, price_per_sqft: null, ...o })
export function run() {
  const done = { run_id: 7, state: 'done', ok: true, page_loads: 3, query: { area: 'Heights' }, count: 3,
    truncated: true, unverified: 1,
    summary: { by_status: { sold: 3 }, median_price: 502000, price_low: 494000, price_high: 510000,
               median_price_per_sqft: 279, suggested_low: 494000, suggested_high: 510000,
               note: '3 sold listings. Prices are sold prices. Suggested range from 3 sales. Not an appraisal.' },
    listings: [
      L({ address: '1300 Sample Ave', status: 'sold', sold_price: 502000, beds: 3, baths: 2.5, sqft: 1800,
          sold_date: '2026-09-14', price_per_sqft: 279, mls_number: 'F-1' }),
      L({ address: '<a href="https://evil.example">1410 Sample Ave</a>', status: 'sold', sold_price: 510000,
          mls_number: 'F-2' }),
      L({ address: '<script>alert(1)</script>48 Invented Ln', status: 'sold', sold_price: 494000, mls_number: 'F-3' }),
    ] }
  const expired = { run_id: 8, state: 'done', ok: false, page_loads: 2, query: {}, expired: true,
    detail: 'These results have expired.' }
  const stopped = { run_id: 9, state: 'ambiguous', ok: false, page_loads: 2, query: {},
    detail: 'Matrix matched more than one area.', choices: ['Heights (Houston)', 'Heights North', 'Height Park'] }
  return {
    done: renderToStaticMarkup(<MlsResults run={done} />),
    expired: renderToStaticMarkup(<MlsResults run={expired} />),
    stopped: renderToStaticMarkup(<MlsResults run={stopped} />),
    none: renderToStaticMarkup(<MlsResults run={null} />),
  }
}
`)
const out = join(work, 'bundle.cjs')
await esbuild.build({ entryPoints: [entry], bundle: true, format: 'cjs', platform: 'node', outfile: out,
                      jsx: 'automatic', nodePaths: [nm], logLevel: 'error' })
const { run } = createRequire(out)(out)
const r = run()
rmSync(work, { recursive: true, force: true })

let pass = 0, total = 0
const check = (label, ok) => { total++; if (ok) pass++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}`) }
const rows = (html) => (html.match(/data-testid="mls-row"/g) || []).length
console.log(canary ? 'CANARY: a card that ignores expiry' : 'real component')
check(`a done run draws one row per listing (${rows(r.done)})`, rows(r.done) === 3 && /data-state="done"/.test(r.done))
check('   with its address, sold price, beds and $/sq ft',
      r.done.includes('1300 Sample Ave') && r.done.includes('$502,000') && r.done.includes('$279'))
check('   and the summary note and suggested range',
      r.done.includes('Not an appraisal.') && r.done.includes('Suggested range $494,000–$510,000'))
check('   and says more results were on Matrix, and one row could not be checked',
      r.done.includes('More results were on Matrix') && r.done.includes('1 row could not be checked'))
check('listing text renders escaped: no <a, no <script, no <button',
      !/<a[\s>]/i.test(r.done) && !/<script/i.test(r.done) && !/<button/i.test(r.done)
      && r.done.includes('&lt;a href=') && r.done.includes('&lt;script&gt;'))
check('an expired run says the results have expired',
      /data-state="expired"/.test(r.expired) && r.expired.includes('These results have expired'))
check('   and draws no table', !/<table/.test(r.expired) && rows(r.expired) === 0)
check('a stopped run draws its reason and the choices, no table',
      /data-state="ambiguous"/.test(r.stopped) && r.stopped.includes('Heights North') && !/<table/.test(r.stopped))
check("a run that is not the reader's draws nothing", r.none === '')
console.log(`\n${pass}/${total} passed`)
process.exit(pass === total ? 0 : 1)
