# StepList

What Jarvis is doing right now, one step per tool call, each one checking off as it finishes.

**Provide**
- `steps`: `[{ id?, label, detail?, status }]`, where `status` is `running`, `done`, `waiting` or `error`.
- Build it from the stream's `tool` events. `running` adds a step, using the event's label. `completed` marks the matching `toolCallId` done.
- Optional: `title`, and `open` with `onOpenChange` to control collapsing.

**Behaviour**
- While any step runs, the header says "Working" with typing dots, and the running step has a breathing dot.
- A finished step's blue check draws over `dur-tick`, and new steps rise in over `dur-step`.
- When everything is done the header reads "Done · N steps" with the blue check. Collapse it once the reply starts streaming.
- A failed step gets the red alert icon, and the header reads "Stopped" with the same icon. Its label stays `fg`.
- Under reduced motion nothing moves: the dots are still and the checks appear drawn.

**Use** one per turn, placed above the assistant bubble. Label steps in plain words ("Searching email", "Checking the calendar") and put the technical detail in `detail`.

**Files.** `StepList.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
