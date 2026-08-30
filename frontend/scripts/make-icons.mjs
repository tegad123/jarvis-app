/**
 * Rasterise the Jarvis asterisk mark into the PNGs the manifest needs.
 *
 * This box has no PIL, ImageMagick or rsvg-convert, so sharp (a devDependency)
 * does the SVG → PNG step. Run once with `npm run icons`; the output is
 * committed under public/icons and does not need regenerating unless the mark
 * or the token colours change.
 *
 * Colours are taken from the design tokens: ink.900 ground, ink.300 spokes —
 * a smoked-hero-flavoured mark that reads on both light and dark home screens.
 */
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import sharp from 'sharp'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(HERE, '../public/icons')

const INK_900 = '#0F1E38'
const INK_800 = '#16294B'
const INK_300 = '#8FB4E8'

/**
 * @param {number} size    canvas size in px
 * @param {number} inset   fraction of the canvas kept clear at the edges.
 *                         Maskable icons need their content inside the safe
 *                         zone (the middle 80%), or launchers crop the spokes.
 * @param {boolean} rounded rounded square for the 'any' icons; maskable stays
 *                         a full bleed square because the launcher masks it.
 */
function svg(size, inset, rounded) {
  const c = size / 2
  const outer = (size / 2) * (1 - inset)
  const inner = outer * 0.26
  const stroke = Math.max(2, size * 0.055)

  const spokes = Array.from({ length: 8 }, (_, i) => {
    const a = (Math.PI / 4) * i
    const x1 = c + Math.cos(a) * inner
    const y1 = c + Math.sin(a) * inner
    const x2 = c + Math.cos(a) * outer
    const y2 = c + Math.sin(a) * outer
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${INK_300}" stroke-width="${stroke}" stroke-linecap="round"/>`
  }).join('')

  const r = rounded ? size * 0.22 : 0

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0.6" y2="1">
      <stop offset="0%" stop-color="${INK_800}"/>
      <stop offset="65%" stop-color="${INK_900}"/>
    </linearGradient>
  </defs>
  <rect width="${size}" height="${size}" rx="${r}" ry="${r}" fill="url(#g)"/>
  ${spokes}
</svg>`
}

async function render(name, size, inset, rounded) {
  const buf = await sharp(Buffer.from(svg(size, inset, rounded))).png().toBuffer()
  await writeFile(resolve(OUT, name), buf)
  console.log(`  ${name}  ${size}x${size}  ${buf.length} bytes`)
}

await mkdir(OUT, { recursive: true })
console.log('Rendering icons →', OUT)
// 0.28 inset on 'any' icons gives the mark breathing room inside the rounded
// square; 0.36 on maskable keeps every spoke inside the 80% safe zone.
await render('icon-192.png', 192, 0.28, true)
await render('icon-512.png', 512, 0.28, true)
await render('icon-maskable-512.png', 512, 0.36, false)

// Also emit the raw SVG so the mark is inspectable and re-renderable.
await writeFile(resolve(OUT, 'icon.svg'), svg(512, 0.28, true))
console.log('  icon.svg')
