# NewChannelSheet

The sheet for naming a new channel or renaming one: one field, one button.

**Provide**
- `mode` (`create` | `rename`), `initialValue` (for rename).
- `onSubmit(name)`, `onClose`.
- `busy`, while saving: the button reads "Working…".
- `error`: the server's sentence.
- Optional: `title`, `submitLabel`, `placeholder` (default "e.g. 128 Alder Ave"), `inline`.

**Behaviour**
- Create reads "New channel" / "Create". Rename reads "Rename channel" / "Save". The name is at most 120 characters, and the button stays disabled while it's empty.
- After creating, drop straight into the new channel: creating one is an intent to use it.
- The direct line can't be renamed. The server refuses it, so don't offer Rename there.
- It's a bottom sheet on a phone and a centred dialog from 640px. It closes on Escape, the close button or the scrim (nothing here is destructive).
- It renders through a portal on `<body>`, so the tab bar and the composer can't cover it.

**Files.** `NewChannelSheet.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
