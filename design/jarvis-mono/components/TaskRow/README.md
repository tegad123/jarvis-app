# TaskRow

One task on the shared list: its status (tap to move it along), title, details, who has it, when it's due, how long it's been open, and the owner's tools.

**Provide** (from `/api/tasks`; every computed value comes from the server, in the tenant's timezone)
- Content:
  - `title` and `detail`. `detail` is null for an operator, because the server withheld it; show nothing rather than a blank.
  - `assigneeName` ('' reads "Unassigned").
  - `dueLabel`, `overdue` and `openFor`.
  - `stale` (shows "Gone quiet").
  - `priority` (`high` shows a "High" tag).
  - `byJarvis` (the sparkle).
- Status:
  - `status`: `open`, `in_progress`, `done` or `archived`.
  - `canStatus` with `onStatus(next)`. Tapping cycles Open → In progress → Done.
- Owner tools:
  - `canManage` with `people` (`[{ id, name }]`), `assignee`, `onAssign(id | null)` and `onArchive`.
- `busy` while a change saves.

**Rules**
- The whole team sees the whole list. What each person may do is enforced by the API. Show the controls only as far as it allows:
  - owners and admins manage everything;
  - a person can move the status of their own task;
  - an operator sees structure only;
  - a tenant on the legacy list gets a sentence, not rows.
- Status marks: an empty ring for open, a ring with a dot for in progress, the blue check for done (and the title struck through).
- An overdue date is the blue Action needed pill, not red: it needs someone, it isn't an error.
- Archive is a decision, not a step. It never sits in the status cycle.
- Show the server's own sentence when a change is refused.
- Put rows in `<List>`.

**Files.** `TaskRow.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
