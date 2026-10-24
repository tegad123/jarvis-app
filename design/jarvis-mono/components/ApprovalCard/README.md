# ApprovalCard

A request Jarvis can't act on without a person: what it will do, in readable fields or as a before-and-after, and the decision this person is allowed to make.

**Provide**
- `summary`, `skill`, `time` (relative), `status` (`pending` | `approved` | `rejected`).
- `fields`: the approval's `display` (or `payload`) as an object or `[key, value]` pairs.
- `changes`, for a config change: `[{ key, label?, current, proposed }]`.
- `canApprove` and `canReject`, straight from the server.
- `forPerson`, for someone else's browser.
- `approveLabel`: "Approve for 30 min" for a `browser_task`.
- `onApprove`, `onReject`, `busy`.
- For a decided request: `decidedBy` and `decidedAt`.

**States**
- **Can approve.** The blue Action needed pill, then Approve (blue primary) and Reject (secondary).
- **Can only reject.** Reject alone, with "For Marco’s browser. Only they can approve it; you can reject it." Don't show a disabled Approve.
- **Decided.** No pill and no buttons. The card goes quiet (no fill), and its footer is a blue check with "Approved by Dana · yesterday", or a grey ✕ with "Rejected by …".

**Rules**
- A config change always renders as a diff (old value struck through → new value), never as raw values.
- A 409 means someone else decided it. Show the server's sentence and reload the queue.

**Files.** `ApprovalCard.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
