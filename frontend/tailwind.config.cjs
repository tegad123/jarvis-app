/**
 * JARVIS — Design Tokens v1 (light-first, dark ships as full variant)
 * Type: Instrument Sans (UI) · IBM Plex Mono (labels, skill names, kinds)
 * Dark mode: class strategy — semantic tokens resolve via CSS vars set on .dark
 *
 * SOURCE OF TRUTH: ~/jarvis-tokens_tailwind_config.js — copied verbatim.
 * The ONLY additions are `content` and `plugins`, which Tailwind requires to
 * run. Nothing under `theme` has been changed, added, or removed. If the
 * tokens file changes, re-copy it; do not hand-edit the theme here.
 */
module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  plugins: [],
  darkMode: 'class',
  theme: {
    fontFamily: {
      sans: ['"Instrument Sans"', 'system-ui', 'sans-serif'],
      mono: ['"IBM Plex Mono"', 'monospace'],
    },
    extend: {
      colors: {
        // Accent — Ink scale (500 = light-mode accent, 300 = dark-mode accent tint, 900 = smoked hero)
        ink: {
          50: '#EDF2FA', 100: '#D9E4F5', 200: '#B5CBEC', 300: '#8FB4E8',
          400: '#5B82C4', 500: '#24427C', 600: '#1B3363', 700: '#142950', 900: '#0F1E38',
        },
        light: {
          // Warm paper, one step DEEPER than the original #FAF8F4. At #FAF8F4
          // a #FFFFFF card differs by ~2% luminance: on a retina mock it looks
          // refined, on an ordinary screen it is simply invisible and every
          // card reads as flat white on near-white. This keeps the warmth (it
          // is still paper, not grey) while giving the surfaces something to
          // lift OFF of. Deepened deliberately, not tinted — the register is
          // Mercury, not Material.
          bg: '#F2EEE6',
          surface: '#FFFFFF',       // opaque cards (feed rows, lead rows, channel lists)
          'surface-raised': 'rgba(255,255,255,0.58)', // glass featured cards (+ blur-glass)
          border: 'rgba(27,30,29,0.13)',       // was .08 — a hairline that survives a non-retina screen
          'border-strong': 'rgba(27,30,29,0.20)',
          'text-primary': '#1B1E1D',
          'text-secondary': '#6E7573',
          'text-muted': '#8A918E',
          accent: '#24427C',        // = ink.500
          info: '#5B82C4',          // lighter tint of accent (= ink.400)
          success: '#1E8A5A',       // semantic green: Done, Hot, Task created
          warning: '#B07A2E',       // Needs approval, Warm
          alert: '#C2453A',         // Alert, Going quiet, errors
          neutral: '#C9CDCB',       // done/past dots
        },
        dark: {
          bg: '#0C0E12',            // near-black
          surface: '#14181F',
          'surface-raised': 'rgba(255,255,255,0.07)',
          border: 'rgba(255,255,255,0.10)',
          'border-strong': 'rgba(255,255,255,0.22)',
          'text-primary': '#F2F3F5',
          'text-secondary': '#9AA1AE',
          'text-muted': '#7E8695',
          accent: '#8FB4E8',        // ink.300 for text/icons; fills stay ink.500
          'accent-fill': '#24427C',
          info: '#7FA3DE',
          success: '#3BAF7C',
          warning: '#D19A4A',
          alert: '#E0655A',
          neutral: '#4A5160',
        },
      },
      fontSize: {
        // name: [size, {lineHeight, letterSpacing, fontWeight}]
        'label-mono': ['11px', { lineHeight: '1.3', letterSpacing: '0.08em', fontWeight: '500' }], // IBM Plex Mono, uppercase
        'caption': ['12px', { lineHeight: '1.35', fontWeight: '500' }],
        'kind': ['12px', { lineHeight: '1.3', fontWeight: '600' }],       // the dot+word
        'body-s': ['13.5px', { lineHeight: '1.5', fontWeight: '400' }],
        'body': ['14.5px', { lineHeight: '1.45', fontWeight: '400' }],
        'chat': ['15px', { lineHeight: '1.55', fontWeight: '400' }],
        'row-title': ['14.5px', { lineHeight: '1.3', fontWeight: '600' }],
        'hero': ['17px', { lineHeight: '1.4', fontWeight: '500' }],
        'screen-title': ['22px', { lineHeight: '1.2', letterSpacing: '-0.01em', fontWeight: '700' }],
        'amount': ['26px', { lineHeight: '1.15', letterSpacing: '-0.01em', fontWeight: '700' }],
      },
      spacing: { // 4px grid; these are the rhythm stops actually used
        '1': '4px', '2': '8px', '3': '12px', '4': '16px', '5': '20px', '6': '24px',
        'row-gap': '18px',       // wire feed vertical rhythm
        'screen-x': '20px',      // screen gutter
        'card-x': '16px',        // card inset
      },
      borderRadius: {
        'chip': '8px',
        'btn': '10px',
        'card': '14px',          // chat action cards, glass panels
        'list': '16px',          // list cards, approval cards
        'hero': '18px',
        'pill': '22px',          // input field
        'screen': '24px',
      },
      backdropBlur: {
        'moment': '10px',        // brand moments (halo disc)
        'panel': '14px',         // featured/glass cards
        'chrome': '18px',        // header + tab bar (pair with saturate(1.5))
      },
      boxShadow: {
        'card': '0 1px 2px rgba(27,30,29,0.06), 0 6px 16px rgba(27,30,29,0.05)',
        'glass': '0 0 0 1px rgba(27,30,29,0.06), 0 10px 28px rgba(36,66,124,0.10)',
        'glass-dark': '0 0 0 1px rgba(255,255,255,0.05), 0 10px 28px rgba(0,0,0,0.40)',
        'hero': 'inset 0 1px 0 rgba(255,255,255,0.16), inset 0 0 48px rgba(91,130,196,0.14), 0 14px 34px rgba(15,30,56,0.22)',
        'accent-btn': '0 4px 12px rgba(36,66,124,0.30)',
      },
    },
  },
};

/* ---------------------------------------------------------------------------
GLASS SURFACE DEFINITIONS (three tiers — do not invent a fourth)

1. CHROME (tab bar, headers): backdrop blur 18px + saturate(1.5).
   Light: header bg rgba(250,248,244,.68), tab bar rgba(255,255,255,.62);
          border rgba(255,255,255,.55–.6) on the lit edge + 1px hairline
          shadow rgba(27,30,29,.05) on the content side.
   Dark:  header rgba(16,19,26,.70), tab bar rgba(18,22,30,.65);
          border rgba(255,255,255,.10); hairline rgba(0,0,0,.45).
   Content scrolls beneath chrome; chrome is never opaque.

2. FEATURED CARDS (approval/summary cards, inline action card, thinking
   panel, pinned direct-line card): backdrop blur 14px.
   Light: bg rgba(255,255,255,.50–.58); border 1px rgba(255,255,255,.65–.7)
          (catches light) + outer hairline rgba(27,30,29,.05–.06);
          optional 1px top highlight gradient (transparent→white .9→transparent).
   Dark:  bg rgba(255,255,255,.06–.08); border rgba(255,255,255,.14).

3. BRAND MOMENTS (empty states, login, thinking atmosphere): blur 10–12px
   disc/panel over a radial ink-300 halo (rgba(143,180,232,.5) light,
   .18 dark); inset top highlight rgba(255,255,255,.8) light / .15 dark.

SMOKED HERO (its own thing, not glass-translucent): opaque gradient
   160deg ink-800 #16294B → ink-900 #0F1E38 65%; border rgba(143,180,232,.28);
   shadow 'hero' token; 1px top edge highlight gradient; identical both modes.

BODY CONTENT IS OPAQUE: feed rows, lead rows, channel list — light.surface /
   dark.surface. Glass is for chrome and featured cards only. Ambient blobs:
   two blurred (30px) radial fields per screen, rgba(36,66,124,.14) and
   rgba(143,180,232,.18) light; rgba(70,110,180,.28) / rgba(143,180,232,.13) dark.

ICON STYLE: 24px grid, stroke-only, stroke-width 2 (2.2 for small emphasis
   icons), no fills except: kind dots (7px, 13px on wire spine with 3px bg
   ring), spark mark (filled 4-point star, ink-300), Jarvis asterisk mark
   (8-spoke stroke). Rendered sizes 11 / 16 / 20px.

KIND COLOR SEMANTICS (dot + word, never dot alone):
   info    = filings, updates        (Email filed, Update)
   success = completions, hot leads  (Task created, Done word uses text-secondary, Hot)
   warning = waiting on the user     (Needs approval, Pending, Warm)
   alert   = deadlines, failures     (Alert, Going quiet, connection errors)
   neutral = past/settled dots
--------------------------------------------------------------------------- */
