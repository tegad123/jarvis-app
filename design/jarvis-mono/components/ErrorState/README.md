# ErrorState

What a screen shows when it couldn't load: which thing is unavailable, the plain reason, the technical detail for whoever is helping, and Try again.

**Provide**
- `label`: the screen's own name ("Leads unavailable", "Approvals unavailable"). Never a generic one: a Leads failure labelled "Feed unavailable" is a lie about what broke.
- `message`: one plain sentence, the server's when it has one.
- Optional:
  - `detail`: the HTTP status or the server's detail, in small grey.
  - `onRetry`, with `busy` (the button reads "Trying…").
  - `stale` with `staleMessage`: while the last good data is still on screen, the label reads "Not updating" and says so.

**Rules**
- Red is the alert icon and the card's edge. Everything else is `fg` and grey.
- Never blank the list behind it. Keep what was last loaded and mark it stale.
- "Can’t reach Jarvis right now." is for when the network is down. Anything else is "Jarvis could not read …" with the name of the thing.

**Files.** `ErrorState.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
