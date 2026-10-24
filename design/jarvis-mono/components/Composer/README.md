# Composer

Where a person talks to Jarvis: a pill with + (attach), the text field, the mic, and send. It grows to hold attachments, and a strip above it reports the voice note's state.

**Provide**
- `value` and `onChange` (or leave them out for internal state), `onSubmit(text)`.
- Attachments:
  - `attachments` (as in MessageBubble) with `onRemoveAttachment(a)`.
  - `uploading`.
  - `onFiles(fileList)` (renders a hidden file input; set `accept`) or `onAttach`.
- Voice:
  - `onMic`.
  - `voice`, one of `{ phase: 'idle' | 'requesting' | 'recording' | 'uploading' | 'transcribing' | 'denied' | 'error', seconds?, level?, message? }`.
  - `onDismissVoice`, and optional `maxSeconds`.
- `streaming` with `onStop`: send becomes stop, and stopping aborts the chat request.
- Optional: `disabled`, `placeholder` (default "Ask Jarvis…").

**Behaviour**
- Send is disabled until there is text or an attachment. An attachment with no text is a valid turn.
- A transcript lands in the field for editing and is never sent automatically.
- Send is the blue primary circle. The box's edge turns blue while you type.
- Recording shows the live dot, level bars and a timer. Blocked shows the blue "Microphone blocked" pill and a blue edge, with how to allow it. A voice error is the red alert icon with the sentence in `fg`.
- No composer for an operator: they can look, not speak.

**Files.** `Composer.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
