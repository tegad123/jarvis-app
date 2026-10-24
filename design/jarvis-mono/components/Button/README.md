# Button

The pill every action in Jarvis Mono is drawn with: primary (the blue `accent` fill with white text, `accent-hover` when hovered or pressed), secondary (`panel-raised` with a hairline) and ghost (text only).

**Provide** `children` (a short verb, sentence case), `variant` (`primary` | `secondary` | `ghost`, default `secondary`), optional `size="sm"`, optional `icon` (an icon name; with no children it becomes a round icon button and needs `aria-label`). Every other prop goes to the `<button>`.

- One primary per decision: "Allow for 30 min" beside a secondary "Not now"; "Approve" beside "Reject".
- Ghost for low-stakes and destructive-but-recoverable row actions ("Remove", "Disconnect", "Cancel").
- Disabled is `opacity-disabled` and nothing else. Prefer hiding a control the person may never use (the API refuses it anyway) over showing it disabled.
- Blue is the only button colour, and only on the primary. Never make a button red: errors are the alert icon, not a destructive red button.

**Files.** `Button.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
