# ActivityRow

One event in the Activity feed: what kind of thing happened, which skill did it, when, and one plain sentence about it, with technical details behind a chevron.

**Provide** (from a feed event)
- `kind`: the kind's word ("Email filed", "Task created", "Done").
- `tone`:
  - `needs-you`: the blue pill.
  - `done`: the blue check.
  - `error`: the red alert icon. Only for real failures.
  - `neutral`.
- `skill`.
- `time` (relative) with `timeTitle` (absolute) and `dateTime`.
- `headline`: the event's headline, or its `message` for events older than headlines.
- Optional:
  - `detail`, shown on "Details".
  - `attachments`: short labels already picked from the event's attachments. Drop any that repeat the headline, and never show the raw email subject the headline has already cleaned up.
  - `defaultOpen`.

**Use**
- Rows sit straight on `bg` in `<Rows>`, divided by hairlines, newest first.
- The kind is the answer to "what happened". The skill is provenance, quieter, after it.
- Deadlines and things waiting on the person are `needs-you`, not `error`.
- The detail is for the curious: grey, on a `panel` ground, never needed to understand the row.

**Files.** `ActivityRow.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
