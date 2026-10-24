# FilterChips

A labelled row of pill chips that narrows a list. "All" comes first, and one chip is selected at a time.

**Provide**
- `label` ("Show", "Kind", "Skill", "Level", "Who", "Quiet").
- `options`: strings, or `[{ value, label }]`.
- `active`: the selected value, or null for All.
- `onChange(value | null)`.
- Optional: `format(value)` to turn raw values into words; `allLabel`.

**Behaviour**
- Selected is the blue tint with a blue label. The rest are grey hairline pills that strengthen on hover.
- Tapping the selected chip again goes back to All. The row scrolls sideways when it's too long.
- Build options from what's actually loaded for open-ended values (kinds, skills, people), so a new one appears without a code change. Use a fixed list for closed vocabularies (status, priority).
- A filter never changes the TodaySummary. When a filter leaves nothing, say so with an `EmptyState` ("No tasks match those filters.") and offer to clear it.

**Files.** `FilterChips.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
