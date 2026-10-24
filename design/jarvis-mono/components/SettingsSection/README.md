# SettingsSection

One card on the Settings screen: a title, one plain sentence about what it does and who can see it, its current status, its rows, and its actions.

**Provide**
- `title` and `description`.
- Optional:
  - `status: { tone, label }`, where `tone` is `done` (blue check), `action` (blue Action needed pill), `error` (red alert icon, `fg` text), `busy` (dots) or `neutral`.
  - `rows`: `[{ id?, title, detail?, action?: { label, onClick, disabled? } }]`.
  - `children`, for a form. Use `Field` inputs in a grid (`grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-jm-2`).
  - `error`, and `actions` (Button elements).

**Google account statuses**
- Connected: `done` (the blue check).
- Needs reauth: `action` (the blue pill), with Connect as primary.
- Never connected or Disconnected: `neutral`.
- Disconnecting…: `busy`.

**Rules**
- Disconnect is two steps: the button becomes "Confirm disconnect" (primary) beside "Cancel" (ghost).
- Show identities as the person's own address only, never a token.
- Passwords are write-only: after saving, list the site and username and nothing else.

**Files.** `SettingsSection.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
