# TodaySummary

One line above the Activity feed that says what Jarvis did today and whether anything needs you.

**Provide**
- `items`: phrases already counted and pluralised, such as `["5 emails filed", "2 tasks created", "1 done"]`. Build them from `/api/feed/summary`, which counts the whole day server-side. Never count the page of events you happen to be showing.
- `needsYou`: how many of today's events need the person.
- `unavailable`: true when the summary call failed.

**States**
- Something needs you: the items, then the blue pill "2 things need you".
- Nothing needs you: the items (or "Nothing yet today."), then a blue check with "All clear."
- The summary failed: "Summary unavailable." with "The feed below is still live." Never fall back to counting the page: numbers that look right and aren't are worse than an honest gap.
- A filter on the feed never changes this line.

On a narrow screen the items truncate. The status on the right always stays whole.

**Files.** `TodaySummary.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
