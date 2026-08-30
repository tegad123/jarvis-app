# App build step 3 — verification artefacts (2026-08-16)

Ask tab: streaming chat proxy + conversation UI. **41/41 checks passed** against the
live cloudflared tunnel in headless Chromium at 390×844, signed in as the real
tenant, with real `claude-sonnet-5` calls.

## Screenshots

| file | what it shows |
|---|---|
| `a1-list-light.png` | Conversations list — pinned direct line (glass tier 2), CHANNELS, ghosted 128 Alder Ave teaser |
| `a2-thread-empty-light.png` | Thread empty state + 3 tappable example prompts |
| `a3-thread-answer-light.png` | Real answer — ink user bubble, typography-forward Jarvis reply |
| `a4-rehydrated-light.png` | Conversation restored after a page reload |
| `a5-thread-dark.png` | Thread in dark |
| `a6-list-dark.png` | List in dark |
| `a7-error-dark.png` | Backend stopped mid-conversation — honest inline error |

## Scripts

- `verify3.mjs` — the 41-check suite. `node verify3.mjs https://<tunnel-host>`.
  Reads `APP_PASSPHRASE` from `~/.hermes/.env`; never prints it. Makes ~3 real
  model calls (accepted cost). Stops and restarts `jarvis-app` for the negative
  test. Needs `playwright`.
- `probe-stream.mjs` — instruments assistant-text growth over time. Use this to
  investigate streaming behaviour rather than guessing at it.

## Two things to know before re-running

**Chunk granularity is upstream.** The gateway coalesces model output into very
few SSE content chunks (a 20-line answer measured as exactly 2) and the proxy
forwards them 1:1. Do not assert on the number of deltas — assert that text is
painted while the request is still open, which is what the suite does.

**Playwright footguns this suite already works around:** `waitForFunction(fn,
options)` eats `{timeout}` as the *arg* parameter (pass `null` first), and the
Send button is legitimately disabled after a send (the draft is cleared) — the
composer input's `disabled` state is the real streaming flag.

Current tunnel host:

```
journalctl -u cloudflared-jarvis-app -o cat | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1
```
