#!/usr/bin/env node
/**
 * Model-written links are never clickable; the connect button is the app's.
 *
 * Renders the real chat components (frontend/src/components/ChatMessage.tsx)
 * with react-dom/server after bundling them with esbuild, then checks:
 *   - assistant text holding a markdown link, a bare connect URL and raw
 *     <a>/<button> HTML renders with no <a and no <button (escaped text);
 *   - the app's connect button renders (presence twin) and its click goes to
 *     /api/google/connect/start, whatever else is around;
 *   - only known action names become a button (a URL or "open_url" does not).
 * --canary swaps in a linkifying AssistantText; the no-link checks must fail.
 *
 *   node docs/frontend/verify_chat_links.mjs --node-modules <frontend>/node_modules [--canary]
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

const work = mkdtempSync(join(tmpdir(), 'chat-links-'))
const entry = join(work, 'entry.tsx')
const component = join(repo, 'frontend/src/components/ChatMessage.tsx')
writeFileSync(entry, `
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import * as real from ${JSON.stringify(component)}
const AssistantText = ${canary
  ? `({ content }) => <p dangerouslySetInnerHTML={{ __html: content.replace(/(https?:\\/\\/\\S+)/g, '<a href="$1">$1</a>') }} />`
  : 'real.AssistantText'}
export function run() {
  const model = 'Tap here: [Connect Google](https://accounts.google.com/o/oauth2/v2/auth?client_id=x) ' +
    'or https://tega.yamin.io/api/google/connect/start ' +
    '<a href="https://evil.example/connect">Connect</a> <button>Connect your Google account</button>'
  const text = renderToStaticMarkup(<AssistantText content={model} />)
  const button = renderToStaticMarkup(<real.ChatActionButton action="connect_google" />)
  const went = []
  real.ChatActionButton({ action: 'connect_google', go: (u) => went.push(u) }).props.onClick()
  const saved = renderToStaticMarkup(<real.SavedActions actions={[
    { action: 'connect_google', label: '<a href="https://evil.example">Connect</a>' },
    { action: 'connect_google', label: 'again' },
    { action: 'open_url', label: 'Open https://evil.example' }]} />)
  return { text, button, went, saved, start: real.CONNECT_GOOGLE_START,
           known: ['connect_google', 'https://evil.example', 'open_url', ''].map(real.isChatAction) }
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
console.log(canary ? 'CANARY: linkifying AssistantText' : 'real components')
check('model text renders no link (<a)', !/<a[\s>]/i.test(r.text))
check('   and no button of its own (<button)', !/<button/i.test(r.text))
check('   its words are still there, as plain text', r.text.includes('https://tega.yamin.io/api/google/connect/start')
      && r.text.includes('&lt;a href='))
check(`the app's connect button renders (${r.button.slice(0, 70)}...)`,
      /<button[^>]*data-action="connect_google"/.test(r.button) && r.button.includes('Connect your Google account'))
check(`   and its click goes to the app's own connect start (${JSON.stringify(r.went)})`,
      r.went.length === 1 && r.went[0] === '/api/google/connect/start' && r.start === r.went[0])
check(`only known actions become a button (${JSON.stringify(r.known)})`,
      JSON.stringify(r.known) === JSON.stringify([true, false, false, false]))
check(`a saved reply draws one app button, the app's own label, nothing from the saved label (${r.saved.slice(0, 60)}...)`,
      (r.saved.match(/<button/g) || []).length === 1 && r.saved.includes('Connect your Google account')
      && !r.saved.includes('evil') && !/<a[\s>]/i.test(r.saved))
console.log(`\n${pass}/${total} passed`)
process.exit(pass === total ? 0 : 1)
