# ConversationRow

One conversation in the sidebar: its name, who spoke last and what they said, when, and whether it's unread or has gone quiet.

**Provide** (from the thread list)
- `name`.
- `preview` and `previewRole` (`assistant` adds "Jarvis: ", `user` adds "You: ").
- `time`, already relative ("12m", "2h", "3d").
- Optional: `unread`; `quiet` (true when `last_message_at` is over 48 hours old); `byJarvis` (`created_by === 'jarvis'`); `direct` (the person's own line); `active`; `onClick`.
- `menu`: `[{ label, onSelect }]` for Rename and Archive on channels. Never give the direct line a menu: it can't be renamed or archived.
- Optional `menuOpen` with `onMenuOpenChange` to control the menu.

**States**
- Selected: the `accent-soft` tint, with a 3px `accent` marker on the left edge. The text stays `fg`.
- Hover: `panel`.
- Unread: a 6px `fg` dot, never coloured.
- Quiet: "Quiet · 3d" in the time slot, grey and not red. Going quiet is information, not an error.
- No preview: "No messages yet". The direct line reads "Your direct line. Always here."
- Operator: pass `bodiesHidden` and the preview reads "Messages hidden for operator access".

The menu opens from the "…" button (shown on hover and focus) or a right-click, and closes on Escape or an outside press.

**Files.** `ConversationRow.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
