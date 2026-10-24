# SignIn

The sign-in screen: Continue with Google first, a PIN for people on the team's roster, and operator access tucked away at the bottom for support.

**Provide**
- `people`: the roster from `/api/people`, as `[{ id, name, title? }]`. It holds names and titles only; never show an address here. A title's bracketed note ("Agent (part-time, …)") is cut from the row and kept as its tooltip.
- `onGoogle`: go to the Google sign-in.
- `onSubmit({ person, secret, operator })`: sign in with a PIN, or with the operator passphrase when `operator` is true.
- `busy`, while the request is in flight. The button reads "Checking…".
- `error`, the sentence to show:
  - "That PIN is not right."
  - "That passphrase didn’t work."
  - "Too many attempts. Wait a few minutes and try again."
  - "Something went wrong. Try again."
- Optional: `onModeChange(mode)` (use it to clear `error`), `defaultMode` (`start` | `pin` | `operator`), `defaultPerson`, `brand`.

**Behaviour**
- One person on the roster is chosen for you. With nobody on it, the screen opens on operator access.
- The PIN is shaped as it's typed: three groups of four, lowercase, with dashes added (`abcd-1234-wxyz`). Pasted dashes are kept and anything else is dropped. Continue unlocks only when the PIN is complete and someone is chosen.
- One primary at a time. Once the PIN form is open, its Continue is the blue button and Continue with Google steps down to secondary.
- The chosen person gets the blue tint, a blue edge and a check.
- The PIN field is a large pill with tabular figures and a show/hide eye. A wrong PIN clears the field and shows the error beside the red alert icon.
- "Operator access" is a quiet grey link at the bottom, on purpose. It is for support and recovery, and offering it first would teach people to share one secret.

**Files.** `SignIn.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
