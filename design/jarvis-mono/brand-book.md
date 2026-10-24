# Jarvis Mono — brand book

Jarvis Mono is the interface for Jarvis, the assistant a real estate team works with: it reads email, keeps the calendar, files tasks and leads, asks before anything goes out, and uses a web browser on the team's behalf. The interface is chat-first. One conversation column, a quiet sidebar, black and white everywhere else, and one blue that means *you*: your words, your next action, your selection, what needs you, what is done.

## Principles

- **The conversation is the product.** Every screen is a destination in the `Sidebar`. The chat column is where work starts and where Jarvis reports back.
- **Black and white carry the interface.** `bg`, `panel`, `panel-raised`, `fg` and `fg-secondary` do almost all the work. Hierarchy comes from weight and grey.
- **One blue, used on purpose.** Blue goes only where the Colour rules below put it. Red appears only on real errors, as the alert icon. There are no other colours, gradients or glows.
- **The app's voice is not the model's.** Receipts, notices and errors that come from the server render as a `SystemLine`, outside any bubble. Buttons in the chat come only from the app's `action` stream event, never from model text, and the model's words are plain text: no markdown, no links.
- **Name the state.** "Uploading…", "Transcribing…", "Waiting for your Mac…". Never a bare spinner.

## Content

Plain, calm, second person. In interface copy Jarvis is "Jarvis", in the third person ("Jarvis can’t send a text"). In its own replies it says "I". Sentence case everywhere, buttons and titles included. No exclamation marks, no emoji. Curly apostrophes (’).

- **Say who decides.** "Nothing goes out without your say-so." "For Dana’s browser. Only they can approve it; you can reject it."
- **Say what was not done.** Never imply Jarvis read or finished something it didn't: "saved, not readable yet", "This answer was cut short."
- **Errors.** Show the server's own sentence when there is one ("That request has already been decided."). Otherwise one plain line: "Can’t reach Jarvis right now."
- **Empty states say what normally lives there.** "Nothing waiting on you." "When a new deal starts, Jarvis opens a channel for it."
- **Buttons are short verbs.** "Allow for 30 min", "Not now", "Take over", "I’m done", "Approve", "Reject", "Connect a Mac".
- **People are names.** Show a display name, never an address. The one exception is the operator's Outbox, which shows what was captured exactly as addressed.

## Colour

| Token | Dark | Light | Use |
|---|---|---|---|
| `bg` | #0a0a0a | #ffffff | Page, chat, sidebar |
| `panel` | #161616 | #f5f5f5 | Assistant bubble, composer, cards, lists, sheets, hover |
| `panel-raised` | #1f1f1f | #ebebeb | Secondary button, menu, avatar |
| `border` | #262626 | #e5e5e5 | Hairlines (decorative) |
| `border-strong` | #666666 | #8a8a8a | Edges that carry meaning |
| `fg` | #ffffff | #0a0a0a | Text, including error sentences |
| `fg-secondary` | #8a8a8a | #666666 | Secondary text |
| `accent` | #2563eb | #2563eb | The blue fill |
| `accent-hover` | #1d4ed8 | #1d4ed8 | Hovered and pressed blue |
| `on-accent` | #ffffff | #ffffff | Text on blue |
| `accent-text` | #60a5fa | #2563eb | Blue text, links, the done check |
| `accent-soft` | #2563eb at 14% | #2563eb at 12% | The blue tint |
| `accent-soft-text` | #60a5fa | #1d4ed8 | Text on the tint |
| `error` | #ef4444 | #ef4444 | The alert icon, real errors only |

**Where blue goes, and nowhere else**

- **The fill**, `accent` with `on-accent` text:
  - your own message bubbles;
  - primary pill buttons (Approve, Allow for 30 min, Send, Connect a Mac, Save, Continue, Create);
  - the selected conversation's 3px left marker;
  - the Approvals count badge;
  - the edge of a card that needs you;
  - the focus ring.

  Hovered and pressed fills are `accent-hover`.
- **Blue text**, `accent-text`: links ("Sign in with PIN"), and the done check (finished steps, receipts, Approved, Connected, a paired Mac, All clear).
- **The tint**, `accent-soft`:
  - selected rows: the open conversation, the current destination, the chosen person, the selected filter chip;
  - the **Action needed** pill, the one form for anything that needs you: "2 things need you", an overdue date, a lead with no response, "Microphone blocked".

  Text on the tint is `fg` or `accent-soft-text`.

**Telling blue things apart.** One blue carries several meanings, so shape says which one it is:

- A filled bubble is your message.
- A filled pill is the action.
- The tint with a left marker is a selection.
- The tint pill with a dot and words is Action needed.
- The check is done.

Keep the check for done only and the pill for requests only. Use one primary per decision: two blue buttons side by side means the screen is asking two things.

**Red.** `error` marks real errors only: a failed send, a refused request, a failed step, something that couldn't load. It is only ever the alert icon, plus the edge of an `ErrorState` card. The sentence beside it is `fg`. Overdue, gone quiet and not interested are not errors.

**Base rules**

- Set the page on `bg`. Raise a surface one step with `panel`, two with `panel-raised`. Never a third.
- `border` hairlines are decorative. When an edge is the only sign of a control (a field, a select, a code cell), use `border-strong`.
- Light is the same system inverted. The blue fill is identical in both themes; only blue text and the tint's text deepen in light. Build in dark, then check light.

**Contrast** (WCAG 2; every pair the components use, both themes)

| Pair | Dark | Light |
|---|---|---|
| `fg` on bg / panel / panel-raised | 19.8 / 18.1 / 16.5 | 19.8 / 18.2 / 16.6 |
| `fg-secondary` on bg / panel / panel-raised | 5.7 / 5.2 / 4.8 | 5.7 / 5.3 / 4.8 |
| `on-accent` on `accent` / `accent-hover` | 5.2 / 6.7 | 5.2 / 6.7 |
| `accent-text` on bg / panel | 7.8 / 7.1 | 5.2 / 4.7 |
| `accent-soft-text` on the tint, over bg / over panel | 7.1 / 6.3 | 5.7 / 5.2 |
| `fg` on the tint, over bg / over panel | 18.0 / 16.1 | 16.7 / 15.4 |
| `fg-secondary` on the tint over bg | 5.2 | 4.9 |
| `accent` as an edge, marker or ring on bg / panel / panel-raised | 3.8 / 3.5 / 3.2 | 5.2 / 4.7 / 4.3 |
| `error` icon on bg / panel / panel-raised | 5.3 / 4.8 / 4.4 | 3.8 / 3.5 / 3.2 |
| `border-strong` on bg / panel | 3.5 / 3.2 | 3.5 / 3.2 |

Text needs 4.5:1. Icons, edges and focus rings need 3:1. Four pairs failed when checked, and the rules above are their fixes:

- `#2563eb` text on the light tint is 4.0–4.4:1, so text on the tint uses `#1d4ed8` in light.
- `#ef4444` as text on light surfaces is 3.2–3.8:1, so red is never text.
- `#2563eb` text on light `panel-raised` is 4.3:1, so there's no blue text on raised surfaces in light.
- `fg-secondary` on the light tint over `panel` is just under 4.5:1, so tinted rows with secondary text sit on `bg`.

## Type

One family: **Geist** (Google Fonts, weights 400, 500 and 600), falling back to the system sans.

| Style | Size / line | Weight | For |
|---|---|---|---|
| `display` | 28 / 34 | 600 | Empty-chat greeting, sign-in title |
| `title` | 20 / 26 | 600 | Screen, conversation and sheet titles |
| `heading` | 16 / 22 | 600 | Card titles, the wordmark |
| `body` | 15 / 22 | 400 | Bubbles and the composer |
| `body-sm` | 14 / 20 | 400 | Everything around the chat, row titles |
| `label` | 14 / 20 | 500 | Anything you press or select |
| `caption` | 12 / 16 | 400 | Time, metadata, helper lines, tags |
| `pairing` | 28 / 36 | 500 | PairingCode only |

- Anything you read in the chat is `body`. What surrounds it is `body-sm`.
- Weight 500 means pressable. 600 is only for `display`, `title` and `heading`.
- No italics, no all-caps, no letter-spaced labels.
- Numbers that change in place (timers, codes, PINs) use tabular figures.

## Space and layout

A 4px grid, `space-1` to `space-12`.

- **Desktop.** `Sidebar` on the left at `size-sidebar` (272px), on `bg` with a `border` edge. The chat column is centred, at most `size-chat` (720px) wide, with `space-6` gutters. The `Composer` is pinned to the bottom of the column, `space-4` off the edge.
- **Phone.** The sidebar becomes a full-height sheet over `scrim`. The chat takes the full width with `space-4` gutters. Rows are at least `size-touch` (44px) tall.
- **Bubbles** are at most `size-bubble` (80%) of the column. The assistant is on the left, you are on the right. Turns are `space-4` apart, and attachments sit `space-2` above their bubble.
- **The ComputerCard** docks above the composer at `size-computer-sm` and opens to `size-computer-lg`.
- **Lists.**
  - Activity and Outbox rows sit straight on `bg` in a `Rows` list.
  - Task and lead rows sit in a `List`, a `panel` card with hairline dividers.
  - Filters go above a list, and the today summary goes above Activity.
- **Sheets and dialogs** are at most `size-sheet` (480px). They are bottom sheets on a phone and centred dialogs from 640px.
- **Sign-in** is one centred column, `size-signin` (360px) wide.

## Shape and depth

- **Radii.**
  - `radius-xl` for bubbles (with `radius-tail` on the speaker's bottom corner) and sheets.
  - `radius-lg` for cards and lists.
  - `radius-md` for rows, menus and chips.
  - `radius-sm` for anything inside a card.
  - `radius-pill` for every button, tag, chip, select and field.
- **The composer.** Its radius is half of `size-composer`, so it is a pill at one line and a soft box when attachments make it taller.
- **Depth.** Flat. A card in the flow is `panel` plus a `border` hairline, with no shadow. Only what floats over the transcript gets `shadow-float`: menus, sheets, the docked ComputerCard. Sheets sit on `scrim`.

## Motion

| Moment | Timing | Reduced motion |
|---|---|---|
| Typing dots | Three 6px dots, `dur-dots` (1.2s) loop, `dur-dots-offset` (160ms) apart, `ease-in-out`; each lifts 2px and brightens | Still dots at three opacities, with the label kept |
| Word-by-word streaming | Each new word fades in over `dur-word` (160ms), `ease-out`; words arriving in one chunk are `dur-word-stagger` (28ms) apart, capped at 12 | Words appear as they arrive |
| Step list | A new step rises 4px and fades in over `dur-step` (180ms); on completion the blue check draws over `dur-tick` (180ms), `ease-out` | No rise; the check appears drawn |
| Live dot | Opacity breathes over `dur-live` (2s) while the browser is in use or the mic records | Steady |
| Press and hover | Colour over `dur-press` (100ms) | Instant |
| Sheets, menus | Slide in over `dur-sheet` (240ms), `ease-out`, over a fading scrim; out with `ease-exit` | Fade only |

- Loop only what is waiting on something.
- Never animate the height of the transcript.
- Everything respects `prefers-reduced-motion: reduce`: every component carries its own `motion-reduce:` fallback.

## States

- **Hover.** Rows, chips and ghost buttons fill with `panel` or strengthen their edge to `border-strong`. Primary buttons turn `accent-hover`.
- **Selected.** The `accent-soft` tint.
  - The open conversation also gets the 3px `accent` marker and keeps `fg` text.
  - The current destination and a selected filter chip turn their label `accent-soft-text`.
  - A chosen person at sign-in gets an `accent` edge and a check.
- **Focus.** `focus-ring` on `:focus-visible`, on everything focusable: a 2px `bg` gap, then 2px of `accent`.
- **Disabled.** `opacity-disabled` (0.35), and nothing else changes.
- **Busy.** Say what is happening ("Saving…", "Checking…", "Working…", "Trying…"), or use typing dots with a label.

## Iconography

- **Drawing.** Inline SVG on a 24px grid, 1.75px stroke, round caps and joins, `currentColor`. No fills except the stop square and dots. 14 to 16px in rows and tags, 18 to 20px in buttons.
- **The set.** Plus, mic, send arrow, stop, check, close, chevron, more, speaker on and off, file, compose, globe, expand and shrink, copy, alert, eye and eye-off, refresh, inbox, sun and moon, sign out, a sparkle outline, and the nav glyphs. It is drawn for Jarvis Mono and lives in `components/shared/Icon.tsx`.
- **The sparkle.** An outline sparkle in `fg-secondary` means Jarvis made this: opened a channel, filed a task, added a lead. It has no other use.
- **Coloured icons.** The only ones are the blue check (done) and the red alert (error).
- **No emoji.**
- **Wordmark.** There is no logo file: the wordmark is "Jarvis" set in `heading` (`display` at sign-in). The app draws an eight-spoke asterisk mark in code. If it stays, copy its path exactly and draw it in `fg`, never tinted.

## A turn in the chat

1. Your bubble, blue on the right, with its attachments above it.
2. A `StepList` while Jarvis works: "Working" with typing dots, and steps checking off as the stream's `tool` events complete. Before any step arrives, an assistant bubble with typing dots stands in.
3. The assistant bubble, on the left, streaming word by word. The read-aloud control appears under it once the reply is done.
4. A `SystemLine` for each thing the server checked: a receipt with the blue check, a notice that a document wasn't read, Action needed, or an error with the red alert.
5. App buttons from `action` events, primary first ("Connect your Google account").
6. While the browser is open, a `ComputerCard` docked above the `Composer`. When Jarvis needs a decision in this conversation, an `ApprovalCard` inline; the Approvals screen lists all of them.

## Roles

The interface shows what the API allows. Every hidden control has a server-side twin.

- **Operator** (break-glass access) sees structure, never message bodies.
  - Conversation rows read "Messages hidden for operator access", and the chat shows one `SystemLine` saying why.
  - There is no `Composer` and no `ComputerCard`, and Settings is not in the sidebar.
  - Task rows show no details and no controls.
  - The operator alone sees the Outbox.
- **Owner and admin** manage channel members and assign or archive tasks. **Members** see the member list read-only, and change status only on their own tasks.
- **Sheet-backed leads** are read-only: the rows show the same signals with nothing to press, and the subtitle says the sheet stays the place you edit.
- **An approval for someone else's browser** shows only Reject, with the sentence that says so. Show the Approve and Reject buttons only as far as `can_approve` and `can_reject` allow.

## The app, screen by screen

| Screen | In Jarvis Mono |
|---|---|
| Sign in | `SignIn`: "Continue with Google" (primary), "Sign in with PIN" (link), the people picker, the PIN field, and "Operator access" as a quiet link at the bottom. |
| Conversations | `Sidebar` (the direct line, then channels as `ConversationRow`s) and the chat column (`MessageBubble`, `StepList`, `SystemLine`, `ComputerCard`, `ApprovalCard`, `Composer`). The conversation header has the `title` and a members button with the count, which opens `MembersSheet`. The compose button and the row menu's Rename open `NewChannelSheet`. |
| Activity | `TodaySummary`, then `FilterChips` (Kind, Skill), then `ActivityRow`s. Operators also get the `OutboxRow` list on desktop. |
| Approvals | `ApprovalCard`s, pending first. Decided ones are below a "Decided" label and stay quiet. Empty: `EmptyState`, "Nothing waiting on you." |
| Tasks | `FilterChips` (Show, Level, Who, Quiet), then `TaskRow`s in a `List`. |
| Leads | `FilterChips` (Needs touch, Hot, New), then `LeadRow`s in a `List`. |
| Settings | `SettingsSection`s: your Google account, website logins, iMessage (with `PairingCode`). |
| Any screen | `EmptyState` when there is nothing. `ErrorState` when loading failed, naming the screen ("Leads unavailable"), and "Not updating" while stale data stays visible. |

## Intentional additions

- **`Button`.** The brief asks for pill buttons, and almost every component uses them. Exporting it keeps one definition.

## Using the components

- **Files.**
  - Tokens: `tokens/design-tokens.json` is the source, and `tokens/tokens.css` holds the generated CSS variables for both themes.
  - Tailwind: the preset is `tailwind/preset.cjs`.
  - Fonts: Geist is in `fonts/`.
  - Components: `components/` has one folder each, with the `.tsx`, its guidelines and a preview.
- **Names.** A token called `panel` in this book is `--jm-panel` in CSS, and `bg-jm-panel`, `text-jm-panel` or `border-jm-panel` in Tailwind. The rest follow the same pattern:
  - type styles: `text-jm-body`, `text-jm-label`, …;
  - radii: `rounded-jm-lg`;
  - spacing: `p-jm-4`;
  - sizes: `w-jm-sidebar`, `max-w-jm-chat`;
  - shadows: `shadow-jm-float`, and `shadow-jm-focus` for the focus ring;
  - motion: `duration-jm-press`, `ease-jm-out`, `animate-jm-dot`.
- **Load.** Load `fonts/fonts.css` and `tokens/tokens.css` once. Add the preset to Tailwind, and add `components/**/*.tsx` to its `content`.
- **Theme.** Set `data-theme="dark"` or `"light"` on `<html>`, or use the app's `.dark` class (and `.light`). Dark is the default.
- **Lists.** Row components render `<li>`. Put `TaskRow` and `LeadRow` in `<List>`, and `ActivityRow` and `OutboxRow` in `<Rows>`.
- **Fields.** A text input outside a component is `<Field>`; use `large` for sign-in and sheets.
- **Sheets.** They render over the page through a portal. Pass `inline` to show one in place, as the previews do.
