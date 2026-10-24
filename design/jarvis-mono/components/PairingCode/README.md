# PairingCode

A one-time code a person types on their Mac to connect the iMessage helper, shown large and copyable, with its expiry and a live "Waiting for your Mac…" until the device appears.

**Provide**
- `code`, from `newMacCode`. A dash or space in it becomes a gap.
- `expires`: a formatted time, shown as "Works once, until 3:42 PM."
- `paired`: true once a device shows up while polling, with `deviceName`.
- Optional: `instruction`, `waitingLabel`, `onCopy`.

**Behaviour**
- Each character sits in its own `border-strong` cell, in the `pairing` style with tabular figures. The full code is also read out to screen readers.
- Copy reports what happened: "Copied" or "Select and copy". It never claims success it didn't get.
- When paired, the code goes away and the line becomes the blue check with "Connected · Dana’s MacBook Air".
- Place it inside the iMessage `SettingsSection`, in place of the "Connect a Mac" button.

**Files.** `PairingCode.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
