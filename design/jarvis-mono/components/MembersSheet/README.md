# MembersSheet

Who is in a channel and, for an owner or admin, who else could be. The roster is the input, never a typed name.

**Provide**
- `channelName`.
- `members`: person keys in the channel.
- `people`: the roster, `[{ id, name, title? }]`, used for names.
- `personKey`: the viewer, marked "you". It is null for an operator.
- `canManage`: owner or admin.
- `onAdd(id)`, `onRemove(id)`, `onClose`, `busy`, `error`.
- Optional: `defaultAdding`, `readOnlyNote`, `inline`.

**Rules**
- Read-only is a first-class mode. A member or an operator sees the same list with nothing to press, and a note saying who can change it.
- Nobody can remove themselves here: an admin who did would lose the channel and need someone else to undo it.
- Before anyone is added, say what joining means: "They will see this channel’s full history, including everything said before they joined."
- A direct line has no members, so don't open this sheet for it.
- Load membership for every role, including operator. It is structure, not content.

**Files.** `MembersSheet.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
