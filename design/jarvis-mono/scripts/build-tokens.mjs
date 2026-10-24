// Writes tokens/tokens.css from tokens/design-tokens.json. Node built-ins only.
// (Not tokens.json: the repo's .gitignore drops token*.json, a rule for OAuth files.)
// Run from anywhere: node design/jarvis-mono/scripts/build-tokens.mjs
//
// Every token becomes a CSS variable named --jm-<token name>, so nothing here
// can collide with the app's own variables. Colours and shadows are declared
// per theme; everything else once on :root.

import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const tokens = JSON.parse(readFileSync(join(root, 'tokens/design-tokens.json'), 'utf8'))
const themes = tokens.color.themes.map((t) => t.id)
const first = themes[0]

const alias = (v) => v.replace(/^\{(.+)\}$/, 'var(--jm-$1)')
const valueFor = (token, theme) =>
  typeof token.value === 'string' ? alias(token.value) : alias(token.value[theme] ?? token.value[first])

// Dark is the default. [data-theme] is the system's own switch; .dark and
// .light let the app's existing class-based theme drive it too.
const selectors = {
  dark: ':root,\n[data-theme="dark"],\n.dark',
  light: '[data-theme="light"],\n.light',
}

const out = [
  '/* Jarvis Mono tokens: generated from design-tokens.json by scripts/build-tokens.mjs. Do not edit by hand. */',
  '',
]

for (const theme of themes) {
  const lines = []
  for (const t of tokens.color.tokens) lines.push(`  --jm-${t.name}: ${valueFor(t, theme)};`)
  for (const t of tokens.shadow.tokens) lines.push(`  --jm-${t.name}: ${valueFor(t, theme)};`)
  out.push(`${selectors[theme] ?? `[data-theme="${theme}"]`} {`, `  color-scheme: ${theme};`, ...lines, '}', '')
}

const rootLines = []
for (const [key, stack] of Object.entries(tokens.type.families)) rootLines.push(`  --jm-font-${key}: ${stack};`)
for (const group of tokens.type.groups) {
  for (const s of group.styles) {
    rootLines.push(`  --jm-type-${s.name}-size: ${s.fontSize};`)
    rootLines.push(`  --jm-type-${s.name}-line: ${s.lineHeight};`)
    rootLines.push(`  --jm-type-${s.name}-weight: ${s.fontWeight};`)
    rootLines.push(`  --jm-type-${s.name}-tracking: ${s.letterSpacing ?? '0'};`)
  }
}
for (const family of ['spacing', 'radius', 'size', 'duration', 'easing', 'opacity']) {
  for (const t of tokens[family].tokens) rootLines.push(`  --jm-${t.name}: ${t.value};`)
}
out.push(':root {', ...rootLines, '}', '')

writeFileSync(join(root, 'tokens/tokens.css'), out.join('\n'))
console.log('wrote tokens/tokens.css')
