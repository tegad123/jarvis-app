# Desktop layout pass (v1) — 2026-08-30

One breakpoint: `lg` = 1024px (Tailwind's default). Below it the mobile layout
is the one that shipped; the 768–1023 `md` rail is unchanged too.

## What changed (all `lg:`-gated unless marked)
- **Shell** — `TabBar` becomes a 220px full-height rail (asterisk + "Jarvis" at
  the top, 40px icon+label rows, filled accent pill for the active item, tint
  on hover). `main` and the fixed `Chrome` header share one container:
  max-width 1080px, 48px padding, centred in the space right of the rail — so
  "Hi, Dana" and the content have the same left edge.
- **Feed** — hero becomes an 88px strip `TODAY · N updates · All clear.`
  (`HeroCard` renders both shapes; the breakpoint picks). Filter rows are one
  toolbar line each, 12px apart. Cards: 24/20 padding, 14px radius, 16px
  apart, 24px section gaps, hover lift (`.hover-lift` + `shadow-glass`).
  Timeline: a fixed 32px gutter, spine on its centre line, dot centred on the
  headline (`--dot-top`, index.css), spine running dot-to-dot.
- **Login** — card 640px / 48px inset; profiles as a 2×2 tile grid with hover
  lift; tester tile shows the short role ("qa contractor") with the full
  registry title as a tooltip; PIN field mono + centred + reveal toggle;
  Continue disabled until a profile is picked AND the PIN is complete
  (xxxx-xxxx-xxxx — `accounts.make_pin` guarantees the shape; the operator
  passphrase is free-form and stays unformatted); PIN autofocuses on tile
  select (desktop only); arrow keys move focus across the grid, Enter selects,
  Enter in the PIN submits; "Operator access" bottom-centre; a faint corner
  asterisk on the ground.
- **All sizes (intentional)** — the picker containment bug: the `<ul>` column
  was `auto`-sized by the widest title, so rows spilled past the card (and at
  390 the selected card was shoved off-screen). Column is now
  `minmax(0,1fr)` with `min-w-0` down to the text. Also at all sizes but
  invisible until typed: PIN auto-dashing and complete-PIN gating.
- `prefers-reduced-motion` collapses every transition (index.css, base layer).
- No new colour values: the token audit in `verify.mjs` checks every hex in
  `src/` against `tailwind.config.cjs` (+ the pre-existing chip tints).

## Not shipped
- The `xl` right rail (TODAY card + counter slots from `/api/health/summary`)
  — skipped rather than half-shipped. Follow-up.

## Proof
- `shoot.mjs <base> <tag>` — login / login-selected / feed at 390 and 1440.
- `verify.mjs <base>` — 79 checks: containment at 390/1024/1440, keyboard-only
  login, rail/container/hero/gutter/density/dot/spine geometry at
  1024/1280/1440/1920, no horizontal scroll at each (light + dark), hover
  lift, chevron rotation, dark toggle, md band unchanged at 900, token audit.
- `compare390.mjs <old> <new>` — the pre-change bundle (reconstructed from
  this session's persisted file dumps, built to scratch, served through a
  proxy onto testco's API) vs the new bundle, shot in the same minute: feed,
  feed-with-details, feed-dark **pixel-identical**; shell geometry fingerprint
  identical; login differs only by the containment fix (`sbs-390-*.png`).
- `node_modules` here is a symlink to hermes-agent's (playwright, pngjs).
