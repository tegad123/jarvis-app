/**
 * Jarvis Mono. Every colour, type style, radius, size and motion value comes
 * from the preset, which points at the CSS variables in src/jm/tokens/tokens.css
 * — the theme switches by the class on <html>, not by Tailwind's dark: variant.
 */
/** @type {import('tailwindcss').Config} */
module.exports = {
  presets: [require('./src/jm/preset.cjs')],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  plugins: [],
}
