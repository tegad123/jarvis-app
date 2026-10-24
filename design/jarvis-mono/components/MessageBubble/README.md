# MessageBubble

One turn in the chat: yours in a blue bubble (`accent`, white text) on the right, Jarvis's in a dark `panel` bubble on the left, with attachments, the app's buttons and the read-aloud control around it.

**Provide**
- `role` (`user` | `assistant`) and `text` (plain text, always: never render markdown or links from the model).
- Optional:
  - `attachments`: `[{ id, name, size, isImage, src?, href?, readable? }]`. `readable: false` shows "saved, not readable yet".
  - `streaming`: words fade in one by one as `text` grows.
  - `typing`: dots only, before the first word.
  - `partial`: "This answer was cut short."
  - `actions`: `[{ label, onClick }]`, only from the stream's `action` event or saved `actions`; the first is primary.
  - `speech` (`off` | `on` | `speaking` | `unavailable`) with `onSpeak`.
  - `time`.

**Use**
- Keep one assistant bubble per turn and grow its `text` while `streaming`. Turn `streaming` off on `done`.
- Words that arrive together stagger by `dur-word-stagger`. Under reduced motion they appear at once.
- The read-aloud control appears only after the reply finishes.
- Receipts, notices and errors from the server are `SystemLine`s under the bubble, never text inside it.

**Don't** put the model's words in blue, tint the assistant bubble, or draw buttons from model text.

**Files.** `MessageBubble.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
