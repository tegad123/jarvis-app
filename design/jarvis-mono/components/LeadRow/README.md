# LeadRow

One lead from the open-house tracker: how warm they are, their status, the property and price, and the last thing that happened.

**Provide** (from `/api/leads`)
- `name`, `property`, `price`, `agent` and `date`.
- `activity`: the follow-up text, else "Nudged {last_nudge}". It defaults to "No activity yet". This is free text: never turn it into a chip.
- `temperature` (`Hot`, `Warm`, `Cold` or '') with `temperatureRank` (3 to 0).
- `status` (`New`, `No Response`, `Responded`, `Not Interested` or '').
- `byJarvis`.
- To edit: `canEdit` with `onChange(fields)` and `onArchive`, plus `busy`.

**Rules**
- Show editing controls only when the leads come from the app's own store and someone is signed in as a person. A sheet-backed tenant gets the same row with nothing to press, because the sheet stays the place you edit.
- Tapping the bars cycles none → Cold → Warm → Hot. Tapping the status cycles New → No Response → Responded → Not Interested. Each tap sends one field.
- Temperature is three rising bars, filled in `fg`, with `border-strong` for empty. It is never coloured.
- No Response is the blue Action needed pill (someone should follow up). Responded is the blue check. New and Not Interested are grey outlines.
- Put rows in `<List>`. When the sheet can't be read, show an `ErrorState`, never an empty list.

**Files.** `LeadRow.tsx` is the component (React 18, Tailwind 3 with `tailwind/preset.cjs`). `preview.html` shows it in a few states: open it in a browser, or see every preview in `previews/index.html`.
