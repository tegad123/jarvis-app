# SystemLine

A single line from the app itself, outside any bubble: a receipt, a notice, a request for action, or an error.

**Provide** `children` (the server's own sentence where there is one), `tone` (`neutral` | `done` | `action` | `error`), and optionally `detail` (a second, smaller line) and `action` (`{ label, onClick }`, one small button).

**Map the stream events**
- `verified` with `ok: true`: `done`, a blue check and the receipt.
- `verified` with `ok: false`: `error`. The reply claimed something the store doesn't have.
- `notice`: `neutral`. Something was withheld from the model, such as a document.
- `error`: `error`, plus "Can’t reach Jarvis right now." when there is no network.
- Jarvis waiting on you outside the browser card: `action`, which leads with the blue Action needed pill.

**Rules**
- Every tone has a shape as well as a colour: a grey dot, the blue check, the Action needed pill, or the red alert icon.
- Only the mark is coloured. The sentence stays grey (`neutral`, `done`) or `fg` (`action`, `error`); red is never text.
- Keep it to one or two lines. Anything longer belongs in a card.

**Files.** `SystemLine.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
