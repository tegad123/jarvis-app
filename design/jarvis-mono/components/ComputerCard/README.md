# ComputerCard

Jarvis's own web browser inside the chat: a live still of the page, what state it's in, and the controls to let Jarvis continue, take over, or hand it back.

**Provide**
- `state`:
  - `starting`, `working` (Jarvis is using it).
  - `needs-you` (Action needed: an approval, a sign-in or a code).
  - `yours` (you have taken over).
- `screenshot`: an image URL. The app's picture is a JPEG still refreshed every few seconds, not a stream.
- `title`, `url`.
- Optional:
  - `asks`: pending browser approvals, `[{ id, kind, summary, onAllow, onNotNow }]`. `kind: 'task'` reads "Allow for 30 min", anything else "Allow once".
  - `reason`: for `needs-you` without an approval, such as "The site wants a one-time code."
  - `expanded`, with `onToggleExpand`.
  - Handlers: `onHide`, `onTakeOver`, `onDone` ("I’m done" hands the browser back), `onClose`.
  - `onInput(event)`, which receives `{ kind: 'click', x, y }` (x and y as 0 to 1 fractions of the picture: multiply by the screen's width and height), `type`, `open`, `key`, `scroll` and `back`, matching `BrowserInput`.
  - `busy`, `error`, and `floating` (adds `shadow-float` while docked over the transcript).

**Use**
- Dock it above the Composer at `size-computer-sm`. Tapping the picture opens it to `size-computer-lg`.
- In `needs-you` the card takes a blue `accent` edge, and the blue Action needed pill sits on the picture's corner. The state pill (Live, Action needed, You have the browser) always sits there, on a solid `bg` ground so it reads over any page.
- When you take over, the typing and address fields and the key pills appear, with the line "What you type goes to the page, not the chat."
- Never show it to an operator. The server refuses them anyway.

**Files.** `ComputerCard.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
