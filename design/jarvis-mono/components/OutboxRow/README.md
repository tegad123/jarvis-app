# OutboxRow

One mail the outbound boundary caught instead of sending, shown to the operator: who it was addressed to, the subject, when, and the body behind a chevron.

**Provide** (from `/api/operator/outbox`)
- `to` and `cc`: arrays of addresses.
- `subject`, `body`.
- `time` (relative) with `timeTitle` (absolute) and `dateTime`.
- Optional: `defaultOpen`.

**Use**
- Operator only, and desktop only. It is where support checks a tester's run, and on a test tenant it is the only evidence a send happened.
- Head the list "Outbox", with "Captured, not sent · N" beside it. When the boundary is off, say "Boundary off — nothing is captured here".
- Rows sit on `bg` in `<Rows>`, newest first, each with the grey "Captured" tag.
- This is the one place addresses appear: it shows what was captured exactly as it was addressed.
- The body is plain text on a `panel` ground. It scrolls past 320px and is never rendered as HTML.

**Files.** `OutboxRow.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
