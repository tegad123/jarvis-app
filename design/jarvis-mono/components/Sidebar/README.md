# Sidebar

The left column that holds the whole app: the wordmark, a new-channel button, the destinations, the direct line, every channel, and who is signed in.

**Provide**
- `nav`: `[{ id, label, icon, count?, attention? }]`, already filtered by role (an operator gets no Settings; Settings shows for owner, admin and member).
- `active`: the id of the open destination or thread.
- `direct`: the person's own line, as `ConversationRow` props plus `id`. Find it by `is_direct` on the thread list, never by a hard-coded id.
- `channels`: `ConversationRow` props plus `id`, with archived threads left out.
- `person`: `{ name, role }`, the display name and role from the session.
- Handlers: `onSelect(id)`, `onNewChat`, `onToggleTheme` (with `theme`), `onSignOut`.
- A container with a height. The sidebar fills it and scrolls its middle.

**Use**
- Set `attention: true` only on Approvals while something is pending. The count then shows as the solid blue badge with white figures. Other counts stay grey.
- The current destination takes the blue tint with a blue label.
- On a phone, render it inside a full-width sheet over `scrim`.

**Don't** colour nav icons other than the current one, add a second scrolling area, or show the channel list to someone who can't open channels.

**Files.** `Sidebar.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
