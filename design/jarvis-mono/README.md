# Jarvis Mono

The Jarvis design system: chat-first, black and white, with one blue that means *you*. This folder holds all of it:

- tokens (JSON and CSS variables for both themes);
- a Tailwind preset;
- the Geist font;
- 22 React and Tailwind components, each with guidelines and a preview;
- the brand book.

It is self-contained. Nothing in the app imports it yet, and no `package.json` changed.

- **Brand book:** [`brand-book.md`](brand-book.md). Start there. It holds the principles, wording rules, colour rules with contrast, type, motion, roles, and how each screen of the app maps onto the components.
- **See it:** open [`previews/index.html`](previews/index.html) in a browser. It shows the cover and every component, with one switch for the dark and light themes. Each `components/<Name>/preview.html` also opens on its own.

## What's here

```
design/jarvis-mono/
├── README.md               this file
├── brand-book.md           the brand book
├── tokens/
│   ├── design-tokens.json  the source of every value (not tokens.json: the repo ignores token*.json)
│   └── tokens.css          generated CSS variables (--jm-*), dark and light
├── tailwind/preset.cjs     Tailwind 3 preset: every token as a jm-* utility
├── fonts/                  Geist (variable, latin + latin-ext), fonts.css, OFL.txt
├── components/
│   ├── index.ts            exports every component, plus List, Rows, Field and Icon
│   ├── shared/             Icon.tsx and the shared primitives
│   └── <Name>/             <Name>.tsx · README.md (guidelines) · preview.html
├── previews/               index.html (gallery), cover.html, kit.js (theme switch)
├── build/                  generated: the components bundled for the previews
└── scripts/                build-tokens.mjs, build.sh, shims and the preview Tailwind config
```

The 22 components:

- **Chat:** `Button`, `Sidebar`, `ConversationRow`, `MessageBubble`, `SystemLine`, `StepList`, `Composer`, `ComputerCard`, `ApprovalCard`.
- **Settings:** `SettingsSection`, `PairingCode`.
- **Sign-in:** `SignIn`.
- **Lists:** `TodaySummary`, `ActivityRow`, `TaskRow`, `LeadRow`, `FilterChips`, `OutboxRow`.
- **Sheets and states:** `NewChannelSheet`, `MembersSheet`, `EmptyState`, `ErrorState`.

## Using it in the app

The components are written for the app's own stack: React 18, Tailwind 3.4, strict TypeScript, and the `react-jsx` transform. Wiring them in takes three steps, all outside this folder, so none is done here.

1. **Tailwind:** add the preset, and add the components to `content`, in `frontend/tailwind.config.cjs`:
   ```js
   presets: [require('../design/jarvis-mono/tailwind/preset.cjs')],
   content: ['./index.html', './src/**/*.{ts,tsx}', '../design/jarvis-mono/components/**/*.tsx'],
   ```
2. **CSS:** load the font and the tokens once, for example at the top of `frontend/src/index.css`:
   ```css
   @import '../../design/jarvis-mono/fonts/fonts.css';
   @import '../../design/jarvis-mono/tokens/tokens.css';
   ```
3. **Components:** import them with `import { MessageBubble, Composer } from '../../design/jarvis-mono/components'` (a Vite alias would shorten the path). The folder sits outside `frontend/`, so Vite's dev server may need it in `server.fs.allow`.

**Naming.** Everything is namespaced, so it can't collide with the current theme:

- CSS variables are `--jm-<token>`.
- Tailwind utilities are `bg-jm-panel`, `text-jm-fg`, `text-jm-body`, `rounded-jm-lg`, `p-jm-4`, `shadow-jm-focus`, `animate-jm-dot`, and so on.

**Theme.** Dark is the default. `data-theme="light"` on `<html>` switches to light, and so does a `.light` class. The app's existing `.dark` class keeps it dark, so the current `applyTheme` can drive it once it also sets `light`.

## Rebuilding

`tokens/tokens.css` and `build/` are generated. After changing `tokens/design-tokens.json` or a component, run:

```sh
bash design/jarvis-mono/scripts/build.sh
```

The script has three steps:

1. It runs `scripts/build-tokens.mjs`, which uses only Node built-ins.
2. It bundles `components/` for the previews with esbuild 0.24.2.
3. It builds the preview CSS with Tailwind 3.4.17, the app's version.

Both tools run through `npx` at pinned versions, so nothing is added to the app's dependencies. The previews load React 18.3.1 from jsDelivr, so they need a network connection.

## Checked

- Every component passes a strict TypeScript check against React 18's types.
- Every preview renders without errors in both themes.
- An automated check of 1,006 text elements in those renders found every one at WCAG AA or better.
- The brand book's colour section lists the contrast of each token pair, and the four pairs that needed a fix.

## Fonts and assets

- **Geist.** The only typeface. It is vendored as variable WOFF2 files (weights 100 to 900, latin and latin-ext) under the SIL Open Font License 1.1; see `fonts/OFL.txt`. The system uses weights 400, 500 and 600.
- **Logo.** There is no logo file: the wordmark is "Jarvis" set in type.
- **Icons.** They are inline SVG in `components/shared/Icon.tsx`.
- **Cover.** The cover is `previews/cover.html`.
