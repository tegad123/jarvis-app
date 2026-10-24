/**
 * Jarvis Mono — Tailwind CSS 3 preset.
 *
 * Every value points at a CSS variable from ../tokens/tokens.css, so the
 * tokens stay the single source and the theme switches without Tailwind's
 * dark: variant. Everything is namespaced `jm` so it cannot shadow the app's
 * existing theme: bg-jm-panel, text-jm-fg, rounded-jm-lg, text-jm-body.
 *
 * Use it with:
 * The app's copy of design/jarvis-mono/tailwind/preset.cjs; the components,
 * tokens and fonts beside it are copies of that folder too. Loaded by
 * frontend/tailwind.config.cjs; src/index.css loads fonts.css and tokens.css.
 */

const v = (name) => `var(--jm-${name})`

const colors = [
  'bg', 'panel', 'panel-raised', 'border', 'border-strong',
  'fg', 'fg-secondary', 'on-fg',
  'accent', 'accent-hover', 'on-accent', 'accent-text', 'accent-soft', 'accent-soft-text',
  'error', 'scrim', 'bubble-assistant', 'bubble-user', 'on-bubble-user',
]

const typeStyles = ['display', 'title', 'heading', 'body', 'body-sm', 'label', 'caption', 'pairing']

const sizes = [
  'sidebar', 'chat', 'bubble', 'composer', 'control', 'control-sm',
  'touch', 'thumb', 'computer-sm', 'computer-lg', 'sheet', 'signin',
]

const spacing = {}
for (const n of [1, 2, 3, 4, 5, 6, 8, 12]) spacing[`jm-${n}`] = v(`space-${n}`)
for (const s of sizes) spacing[`jm-${s}`] = v(`size-${s}`)

module.exports = {
  theme: {
    extend: {
      colors: {
        jm: Object.fromEntries(colors.map((c) => [c, v(c)])),
      },
      fontFamily: {
        jm: v('font-sans'),
      },
      fontSize: Object.fromEntries(
        typeStyles.map((s) => [
          `jm-${s}`,
          [
            v(`type-${s}-size`),
            {
              lineHeight: v(`type-${s}-line`),
              fontWeight: v(`type-${s}-weight`),
              letterSpacing: v(`type-${s}-tracking`),
            },
          ],
        ]),
      ),
      spacing,
      maxWidth: { ...spacing },
      minWidth: { ...spacing },
      minHeight: { ...spacing },
      borderRadius: {
        'jm-tail': v('radius-tail'),
        'jm-sm': v('radius-sm'),
        'jm-md': v('radius-md'),
        'jm-lg': v('radius-lg'),
        'jm-xl': v('radius-xl'),
        'jm-pill': v('radius-pill'),
        // The composer stays a pill at one line and a soft box when it grows.
        'jm-composer': `calc(${v('size-composer')} / 2)`,
      },
      boxShadow: {
        'jm-float': v('shadow-float'),
        'jm-focus': v('focus-ring'),
      },
      opacity: {
        'jm-disabled': v('opacity-disabled'),
      },
      transitionDuration: {
        'jm-press': v('dur-press'),
        'jm-sheet': v('dur-sheet'),
      },
      transitionTimingFunction: {
        'jm-out': v('ease-out'),
        'jm-in-out': v('ease-in-out'),
        'jm-exit': v('ease-exit'),
      },
      keyframes: {
        'jm-dot': {
          '0%, 60%, 100%': { opacity: '0.3', transform: 'translateY(0)' },
          '30%': { opacity: '1', transform: 'translateY(-2px)' },
        },
        'jm-word': { from: { opacity: '0' }, to: { opacity: '1' } },
        'jm-tick': { from: { strokeDashoffset: '20' }, to: { strokeDashoffset: '0' } },
        'jm-breathe': { '0%, 100%': { opacity: '1' }, '50%': { opacity: '0.3' } },
        'jm-rise': { from: { opacity: '0', transform: 'translateY(4px)' }, to: { opacity: '1', transform: 'none' } },
        'jm-sheet': { from: { opacity: '0', transform: 'translateY(8px)' }, to: { opacity: '1', transform: 'none' } },
        'jm-fade': { from: { opacity: '0' }, to: { opacity: '1' } },
      },
      animation: {
        'jm-dot': `jm-dot ${v('dur-dots')} ${v('ease-in-out')} infinite`,
        'jm-word': `jm-word ${v('dur-word')} ${v('ease-out')} both`,
        'jm-tick': `jm-tick ${v('dur-tick')} ${v('ease-out')} both`,
        'jm-live': `jm-breathe ${v('dur-live')} ${v('ease-in-out')} infinite`,
        'jm-rise': `jm-rise ${v('dur-step')} ${v('ease-out')} both`,
        'jm-sheet': `jm-sheet ${v('dur-sheet')} ${v('ease-out')} both`,
        'jm-fade': `jm-fade ${v('dur-sheet')} ${v('ease-out')} both`,
      },
    },
  },
}
