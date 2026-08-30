# Jarvis — architecture for testing

For QA working from outside the system. Everything here is observable through the
app or its HTTP API; nothing requires access to the host.

The tenant you are testing is **testco**, a demo brokerage. Every person,
property, and address in it is invented, and every email address ends in
`.example` — a reserved suffix that can never resolve. Nothing you do can reach a
real inbox.

---

## 1. The shape of it

```
  Browser (PWA)
      │  same-origin HTTPS, session cookie
      ▼
  FastAPI backend  ──────────────► agent gateway ──────► skills
   (the /api surface)              (the model)          (email, calendar,
      │                                                  tasks, leads)
      ▼                                                      │
   stores: threads · leads · accounts · approvals            │
      ▲                                                      │
      └────────────── activity feed ◄────────────────────────┘
```

Four things worth knowing before you start:

**The backend is a broker, not a proxy.** The browser never holds a key for the
agent gateway and never talks to it directly. Every model call goes through the
backend, which attaches credentials server-side. If you inspect network traffic
from the browser you will find no upstream credentials — that is by design, and
worth confirming.

**The feed is the audit log, written by the skills.** When a skill files an email
or creates a task, it appends an event. The app *reads* that feed; it is not the
author of most of it. So the feed is where you check that something actually
happened, as opposed to the UI merely claiming it did.

**Only one thing writes on the customer's behalf, and it asks first.** Anything
outbound — sending mail, booking a calendar entry — becomes an approval request
rather than an action. Approving really does execute; what stops it reaching the
outside world is the recipient boundary and capture outbox in §7, not a disabled
switch. Sends are meant to be triggered here.

**Roles are enforced by the API, not the interface.** Hidden buttons are a
courtesy. Every rule in §5 is checked server-side, so a crafted request is
refused rather than quietly served. Please test them that way — see §8.

---

## 2. Getting in

The tenant is reached at a URL your contact sends you. **The URL arrives
separately from the credentials, and neither is in this document.** Sign-in
needs a person and a PIN; pick your name from the list and type the PIN you were
given.

Three accounts, one per role — owner, admin, member. Test the same screen as
each; most of §5 is only visible by comparison.

There is no sign-up, no password reset, and no way to change your own role.
Accounts are created outside the app.

## 3. Sessions and roles

Signing in exchanges a person plus a PIN for a signed, expiring cookie. The
cookie is `HttpOnly`, so page scripts cannot read it; it carries **who you are**
and **what you may do**, and the server re-derives both on every request.

There are four roles.

| Role | Who has it |
|---|---|
| **owner** | the account holder — one per tenant |
| **admin** | a senior team member |
| **member** | everyone else on the team |
| **operator** | break-glass support access, belonging to no person |

The first three are people. **Operator is not** — it is a shared recovery
credential, and the difference drives most of §5. There is no self-service
sign-up and no password reset in the app; accounts are created out of band.

A session expires on its own. When it does, the app returns you to sign-in rather
than showing a half-loaded screen — worth confirming by leaving a tab idle.

---

## 4. The API surface

All paths are same-origin under `/api`. Anything requiring a session returns
**401** without one.

**Open (no session):**

| | | |
|---|---|---|
| `GET` | `/api/ping` | liveness |
| `GET` | `/api/people` | the sign-in roster |
| `POST` | `/api/login` | exchange person + PIN for a session |

> `/api/people` is deliberately open — the sign-in screen needs it before anyone
> is signed in. **It returns names and job titles only**: no email addresses, no
> roles, no account state. That is a boundary worth probing: anything more in
> that response is a finding.

**Session required:**

| | | Extra rule |
|---|---|---|
| `POST` | `/api/logout` | — |
| `GET` | `/api/session` | who am I |
| `GET` | `/api/feed` | scoped by role |
| `GET` | `/api/feed/summary` | scoped by role |
| `GET` | `/api/threads` | scoped by membership |
| `POST` | `/api/threads` | creator becomes a member |
| `PATCH` | `/api/threads/{id}` | must reach the channel |
| `POST` | `/api/threads/{id}/archive` | must reach the channel |
| `GET` | `/api/threads/{id}/members` | must reach the channel |
| `POST` | `/api/threads/{id}/members` | **admin or owner** |
| `DELETE` | `/api/threads/{id}/members/{person}` | **admin or owner** |
| `POST` | `/api/chat` | membership + **not operator** |
| `GET` | `/api/chat/history` | membership + **not operator** |
| `POST` | `/api/threads/{id}/attachments` | membership + **not operator** |
| `GET` | `/api/threads/{id}/attachments` | membership + **not operator** |
| `GET` | `/api/attachments/{id}` | membership + **not operator** |
| `GET` | `/api/approvals` | **owner only** |
| `POST` | `/api/approvals/{id}` | **owner only** |
| `GET` | `/api/leads` | scoped by role |
| `POST` | `/api/leads` | **must be a person** |
| `PATCH` | `/api/leads/{id}` | **must be a person** |
| `POST` | `/api/leads/{id}/archive` | **must be a person** |
| `POST` | `/api/transcribe` | — |
| `GET` | `/api/health` | — |
| `GET` | `/api/health/summary` | owner, admin, operator — usage numbers; member 403 |

Everything else serves the app itself.

**Status codes carry meaning, and one choice is deliberate.** Asking for a
channel you are not in returns **404, not 403** — a 403 would confirm the channel
exists, which is itself a disclosure. So "not found" and "not yours" are
intentionally indistinguishable from outside. **403** means *you exist, this
isn't yours*: an operator reaching for message content, a member reaching for
approvals.

---

## 5. What each role can and cannot do

Written as claims you can falsify. Each is enforced server-side; §8 shows how to
check that directly.

**Everyone signed in**

1. Sees their own direct line to the assistant, pinned at the top of Conversations.
2. Is greeted by *their own* name — never another person's.
3. Sees the activity feed, scoped by the rules below.

**Owner**

4. Sees every channel.
5. Is the **only** role that can read or decide approvals. Admin, member and
   operator all get 403.
6. **Cannot read another person's direct line.** This is the one place seniority
   does not win: a direct line is private to its person, including from the
   owner. The screen calls it *"Your direct line. Always here."* — that sentence
   is treated as a promise the backend has to keep. Try to break it.

**Admin**

7. Sees every channel, like the owner.
8. Can add and remove channel members; member and operator cannot.
9. Cannot read or decide approvals.
10. Cannot read anyone else's direct line.

**Member**

11. Sees **only** channels they have been added to, plus their own direct line.
    A channel they are not in is invisible in the list and 404s if requested.
12. Sees only feed events that belong to them — their own tasks and leads — plus
    whatever the tenant has declared team-wide. **An event whose owner cannot be
    established is hidden from members, not shown.** So a member's feed is
    legitimately much shorter than the owner's; that is the rule working.
13. Sees only leads assigned to them.
14. Can create and edit leads, like any person.
15. Sees **every task on the team list** — not only their own. That is the
    shared-list promise, and it is deliberately not the leads rule; what stays
    scoped is the FEED, where a task event belongs to whoever it is for.
16. Can add a task, and can move a task **assigned to them** between open, in
    progress and done. Editing, assigning and archiving are admin-or-owner.

**Operator (break-glass)**

17. Sees **structure**: the channel list, who is in each channel, metadata,
    timestamps, and the activity feed.
18. Gets **403 on message content** — the transcript, posting a message, and
    attachments. Channel list previews are suppressed too, because a preview
    *is* message text.
19. **Cannot act on tenant data**: no creating or editing leads, no adding
    members, no approvals. Observe, never participate.
20. Has no direct line at all, and sees nobody else's.
21. Reads the task list with every `detail` withheld (`bodies_visible: false`)
    and cannot create, edit, assign, complete or archive one — structure for
    support, never the tenant's own words, and never a change.

**Meetings (any person)**

22. **Can hand the assistant a meeting transcript and get tasks back.** Email the
    tenant's assistant address with a subject starting `Meeting:` and the transcript
    in the body or as a .txt/.vtt attachment, or in the Ask tab say "process this
    meeting" with the transcript pasted or attached. Each spoken commitment becomes
    a task on the shared list — assigned when the name matches someone on the team,
    left **unassigned with an alert naming the person** when it does not, never
    guessed. The feed shows one "Filed N follow-ups from …" line whose Details list
    every task; the transcript is kept inside the tenant so each task's detail can
    quote what was said. The same transcript sent twice files once.
23. **Owner, admin and the operator can read the tenant's usage numbers; a member
    cannot.** `GET /api/health/summary` carries this month's counters, 7- and 30-day
    trends, response-latency and deadline outcomes, and the sweep failure count —
    aggregates only, never an address, a name or a task title. Once a month one
    "… in numbers:" line lands on the ops feed from the same store. A member asking
    for the summary is refused with a sentence (403): it describes the whole team's
    work. Replying `done 3` or `priority 2 high` to the task digest now changes the
    row on the Tasks tab (the digest positions resolve against the app's own list),
    once per reply even if the mailbox replays it.

> The reasoning behind 17–21, since it explains several otherwise-odd
> behaviours: break-glass is the weakest credential in the system — one shared
> secret, belonging to no person, the one most likely to end up in a support
> ticket. So what it unlocks is deliberately small. Genuine support questions
> are structural ("is the channel there, is she in it, did the event fire").
> Reading actual conversations requires the owner to sign in themselves.

**Adding someone to a channel gives them the whole history**, including messages
sent before they joined. This is intended — a deal channel is a case file, and a
half-visible transcript is worse than none because you cannot tell it is half.
The interface says so before you confirm; check that it does.

---

## 6. How events reach the feed

A skill does something → it appends an event → the app reads and renders it.

Every event carries a timestamp, a kind (`email filed`, `task created`, `done`,
`warning`, `alert`, …), the skill that wrote it, a one-line **headline** in plain
English, and a **detail** line with the technical specifics behind a tap.

Two properties are worth testing directly:

- **The headline is written for a person.** No category letters, no message IDs,
  no exception classes, no raw field names. Those belong on the detail line. A
  headline containing machine vocabulary is a finding.
- **The hero counts today in *your browser's* timezone.** The count above the
  feed and the list below it must agree. If they disagree, say so — that class of
  bug has appeared before.

Feed events are **not** message content, so an operator sees them. They are the
one surface that stays legible when everything else is closed off.

---

### Sweep events

Three timers run every half hour. Each run posts a heartbeat — *Swept 12 tasks — 2 flagged*
or *Swept 12 tasks — nothing needed attention* — with the evaluated count in the detail, so a
sweep whose filter is broken cannot hide behind an empty result. A sweep that fails posts *The
task sweep hit a snag*. Other sentences you will see: *Can't follow up on <task> — the assignee
isn't on the team* (the task stays visible), *Escalating <task> — <Name> hasn't responded to 3
reminders*, and *<Lead> at <property> has gone quiet — nudged <Agent>*. Any address on one of
these that is not a team address is masked (`s***@***.com`).

## 7. Sends you trigger — the capture outbox

**Nothing leaves the test tenant.** Ever. But sends are not disabled either,
because "nothing happened" and "it was blocked" look identical from outside and
you could not tell them apart.

Instead there is a **recipient boundary** and a **capture outbox**.

**The boundary** allows only addresses in reserved space — `*.example` and
`example.com` / `.net` / `.org`, which RFC 2606 sets aside precisely so they can
never belong to anyone. Every address in this tenant is inside it. A send to
anything else is **refused**, and the refusal is announced on the feed as an
alert reading *"Refused to send outside the test tenant"*. It is never a silent
drop — if you trigger a send to a real-looking address, you should SEE the
refusal, and its absence is a bug worth reporting.

Refusals show the blocked address **masked** (`s***@***.com`). That is
deliberate: a refused address is by definition not a test address, and printing
it in full would import a real identity into this tenant's screens. Enough is
shown to confirm what class of thing was refused.

**The outbox** is where allowed sends land. A message to a reserved address is
written to a durable outbox file inside the tenant and announced on the feed as
*"Captured an outbound email to …"* — with recipient and subject — and then
**not handed to any mail transport**. So:

| You do | You should see |
|---|---|
| Approve a draft to a tenant address | a feed event naming recipient + subject, and a new outbox entry |
| Trigger a send to an outside address | an alert on the feed, masked, and **no** outbox entry |
| Look for real delivery | nothing, anywhere — that is correct |

Ask your contact for the outbox path if you need to read it directly; the feed
is the surface intended for testing.

> Worth knowing: capture happens even though the tenant is otherwise
> write-enabled. "It cannot resolve" is a fact about DNS, not about this code —
> capturing means the guarantee does not depend on the outside world behaving.

## 8. Testing the rules directly

The interface hides what you cannot use, which means UI-only testing cannot tell
an enforced rule from a hidden button. Call the API from the browser console
while signed in — the session cookie travels automatically:

```js
// as an operator: message content must be refused
await fetch('/api/chat/history?thread_id=<id>').then(r => r.status)   // 403

// as a member: a channel you are not in must not merely be hidden
await fetch('/api/threads/<id>/members').then(r => r.status)          // 404

// as an admin: approvals are the owner's alone
await fetch('/api/approvals').then(r => r.status)                     // 403

// as an operator: acting on tenant data must be refused
await fetch('/api/leads', {method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({name: 'Should Not Exist'})}).then(r => r.status)  // 403
```

**A hidden control that succeeds when called directly is the most valuable bug
you can find here.** Please look for it.

---

## 9. Error states — what honest failure looks like

The system is built to say what went wrong rather than to look fine. Some of what
follows may read like a bug and is not.

| What you see | What it means |
|---|---|
| **"Summary unavailable. The feed below is still live."** | Today's counts could not be computed. The list is unaffected. It shows a gap rather than counting the visible page, because a number that looks authoritative and is quietly wrong is worse than no number. |
| **"Jarvis could not open the lead tracker."** with the reason | The lead source is unreachable. Never an empty table — an empty table reads as *"you have no leads"*, which would be a lie. |
| **"Email sorting hit a snag on … — filed for review"** | The classifier failed; the email was still handled, generically. This is a warning, and it counts toward "needs you". A quiet fallback once hid a five-day fault, so fallbacks now announce themselves. |
| **"Jarvis is busy with another task — try again in a moment."** | Concurrency limit. Retry. |
| **"That PIN is not right."** | Shown for a wrong PIN, an unknown person, *and* a disabled account — deliberately identical, so the form cannot be used to discover which accounts exist. Confirm all three produce the same message and the same timing. |
| **"Operator access sees channels and membership, not message content."** | The §5 rule, stated rather than a bare 403. |
| **"This answer was cut short."** | A reply ended early. The partial text is kept rather than discarded. |
| **A channel that 404s** | Either it does not exist or you are not in it. Indistinguishable on purpose — see §4. |

Three behaviours that are correct and often reported as bugs:

- **A member's feed and lead list are much shorter than the owner's.** Scoping,
  working. Compare the same screen across the three accounts before filing.
- **Approving a request produces an outbox entry, not a delivery.** The send
  really ran; §7 explains where it went.
- **A refused send shows a masked address.** Not truncation gone wrong — see §7.

---

## 10. Known limits

Not defects — decided, and out of scope for this round.

- **No self-service account management.** No sign-up, no password reset, no
  role editing in the app. Accounts are created out of band.
- **Unread counts and channel badges have no producer.** The design exists; the
  data does not, so those badges never appear.
- **Channels the assistant opens start with no members**, so they are invisible
  to members until an admin adds them.
- **The assistant's "thinking" steps are live-only.** They appear while a reply
  is streaming and are not stored, so they never appear in history. To see them,
  ask a question and watch.
- **Mail cannot leave, but sends are not disabled.** The tenant is
  write-enabled; the recipient boundary and capture outbox (§7) are what keep
  everything inside. Spreadsheet export is off by configuration.
- **Unanswered-email conversion is category-aware only when the classification can be
  matched to the channel by subject.** The router does not yet store a category on the
  thread; a channel with no matching classification is converted anyway and its task says
  "no classification on record". A newsletter that nobody replies to becoming a task is a
  known limit, not a bug.
- **Meetings are ingested from transcripts you send; nothing is pulled automatically.**
  Google Meet, Zoom and Plaud auto-capture are roadmap — the vendor logos describe where
  this is going, not what is wired today. v1 reads a transcript that arrives by email
  (`Meeting:` subject) or in Ask. Extraction is a model pass, so a task's wording can
  vary between runs and a vaguely phrased commitment may be missed; the transcript kept
  inside the tenant is the record, and **the row is the truth**. An unassigned task with
  an alert naming the person is the system working; a name silently mapped to the wrong
  teammate IS a bug worth filing.
- **The usage numbers count what the assistant measured, not what happened.**
  A showing is "answered" when the reply left; its response time runs from the
  showing service's send time to that moment, so a fixture without a date is
  answered but not timed. A task is "before deadline" by calendar day in the
  tenant's timezone. A lead is "nudged back" when someone touched it within
  48 h of the nudge. A month with no showings says "0 showings answered" and no
  median — an honest zero is not a bug. If the store cannot be written, the work
  still goes through and ONE "Usage numbers are not being recorded" line appears
  on the ops feed; a second such line in the same run IS worth filing.
- **Asking for a task in chat works, but is not yet reliably triggered.**
  "Track this: …" in the Ask tab does create a real task — and when it does,
  the confirmation you see is generated from the saved row and carries its id.
  But the assistant does not always route the request to the task system, and
  it has been observed *claiming* to have tracked something it never wrote. The
  app now catches that at the end of the turn: a claim with no row behind it is
  replaced with "No task was created", and the miss is counted. **The row is
  the truth, not the reply** — check the Tasks tab. A chat request that
  silently produces no task is a known limit right now, not a bug worth
  filing; a chat reply that claims success while the Tasks tab stays empty and
  no correction appears IS worth filing.

---

## 11. Out of scope

Please do not test, probe, or report on any of the following. They are either
someone else's system or deliberately not yours to see.

- **The host.** The machine this runs on, its address, its operating system, its
  other services, and anything reachable from it. No port scanning, no attempts
  to reach anything but the tenant URL you were given.
- **Other tenants.** This product is multi-tenant and you have exactly one. If
  you ever see data that does not look like this tenant's — a name, a company,
  an address that is not in the fictional set — **stop and report it
  immediately**. That is the highest-severity bug available here, and it is the
  one thing worth interrupting for.
- **The operator plane.** Provisioning, account creation, billing, deployment,
  and the break-glass support credential are all outside the app and outside
  this engagement.
- **Real delivery.** Do not try to make mail actually leave. If you find a way,
  that is a finding — report it rather than pursuing it.
- **Load and denial of service.** Single-tenant demo hardware; hammering it
  tells nobody anything useful.

Everything in §5 and §8, by contrast, is explicitly in scope — including trying
to get around it.
