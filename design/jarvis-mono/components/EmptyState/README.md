# EmptyState

What a screen shows when there is genuinely nothing there: a quiet icon and one sentence about what normally lives in this place.

**Provide**
- `children`: the sentence. It defaults to "Jarvis is watching. Nothing needs you right now."
- Optional:
  - `icon`: the screen's nav glyph, or null for none.
  - `title`.
  - `action`: `{ label, onClick, primary? }`, for example "Clear filters".

**Examples from the app**
- "Nothing waiting on you." (Approvals)
- "Nothing on the list yet." / "No tasks match those filters." (Tasks)
- "No leads in the tracker yet." (Leads)
- "When a new deal starts, Jarvis opens a channel for it." (Channels)

**Rules**
- Use it only when the server answered and the answer was empty. Loading is "Loading…". A failure is an `ErrorState`: a blank list that means "broken" is the failure this exists to prevent.
- Keep it grey: no blue unless there's an action, and that action is usually secondary.

**Files.** `EmptyState.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
