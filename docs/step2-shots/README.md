# App build step 2 — verification artefacts (2026-08-16)

Captured against the live cloudflared tunnel in headless Chromium at 390×844,
deviceScaleFactor 2, signed in as the real tenant with the real 34dev feed.
**54/54 checks passed.**

## Screenshots

`final-*` are viewport-sized (what a phone actually shows) and are the ones to
look at. The numbered set are from the assertion run; `03`/`04`/`06`/`07` are
`fullPage: true`, so the fixed header and tab bar appear mid-image — that is a
screenshot artefact of `position: fixed`, not a layout bug.

| file | what it shows |
|---|---|
| `final-01-login.png` | Login — glass tier 3 panel on the ink halo, asterisk mark, pill field |
| `final-02-feed-light.png` | Feed, light — smoked hero, KIND/SKILL chips, wire spine |
| `final-03-feed-dark.png` | Feed, dark — same layout, dark palette, hero unchanged |
| `final-04-ask.png` | Ask tab placeholder (glass tier 2) |
| `01-login-light.png` | Login, first paint |
| `02-login-error.png` | Wrong passphrase → styled 401 error |
| `03-feed-light.png` | Feed light, full page |
| `04-feed-dark.png` | Feed dark, full page |
| `05-ask-dark.png` | Ask placeholder in dark |
| `06-feed-needsyou.png` | Hero's second variant — "1 thing needs you" after a seeded `needs_approval` event |
| `07-error-state.png` | `jarvis-app` stopped mid-session: honest error, last-good events retained |

## Scripts

- `verify.mjs` — the 54-check suite. Run:
  `node verify.mjs https://<current-tunnel-host>`
  Reads `APP_PASSPHRASE` straight from `~/.hermes/.env`; never prints it.
  Needs `playwright` installed (`npm i playwright && npx playwright install chromium`).
  It seeds one `needs_approval` event through `notify.py` to test polling —
  **restore the feed afterwards**, or that test event stays in the real feed.
- `screenshots.mjs` — viewport-only capture pass, same arguments.

The tunnel hostname rotates on every restart of `cloudflared-jarvis-app`; read
the current one with:

```
journalctl -u cloudflared-jarvis-app -o cat | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1
```
