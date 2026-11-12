# Jarvis mobile: Expo app to the App Store

Written 2026-11-04. Goal: one public Jarvis app on the App Store that any
business on the platform signs into, built with Expo, with the logged-in web
version making exactly the same updates.

## The rule that keeps web and app the same

Every behavior lives in the FastAPI backend. The web app and the phone app are
two views of the same API.

- An approval tapped on the phone, a task finished on the web, a chat sent from
  either: each is one API call that writes the same row. The other client sees
  it on its next read. Nothing is decided in a client.
- No new feature ships on one client only. If the phone gets a control, the
  API gets the endpoint first, and the web gets the same control in the same
  release or a written reason why not.
- Role gates (owner, admin, member, operator) stay server-side. Both clients
  only hide what the server already refuses.
- New events (push, sign-in methods, devices) also write the activity feed, so
  the web shows what the phone was told.
- One shared TypeScript package holds the API client and types, generated from
  FastAPI's OpenAPI schema. Web and app import the same calls, so they cannot
  drift on field names or rules.

## What needs an explicit yes before work starts

These are AGENTS.md rule 6 items. The plan below assumes yes; none of it starts
without it.

Decided 2026-11-04 (owner): yes to the new tables, yes to push as a send path,
yes to the Expo project. Testing: TestFlight for the phone app. The server
side of each change is proven on a staging tenant first, then deployed to
Dev 34, where the Dev 34 team uses internal TestFlight builds. Review business:
yes, a separate sandbox business with fake data and test-only outbound mail,
not Dev 34.

| Item | Why |
| --- | --- |
| New tables: `devices`, `sign_in_links`, `push_prefs` | Token sessions, Apple sign-in, and push need somewhere to live. |
| New send path: push notifications | A phone notification is a message to a person. It goes through the same gate and counters as mail. |
| Expo and its npm packages | New `mobile/` project. No pip installs are needed: PyJWT, cryptography, and httpx are already in the venv. |
| Deploying backend changes to testco | testco is stale and Bao is testing on it. Verification needs a testco update, or a separate staging tenant. |
| A new App Review tenant | A sandbox business with a fake mailbox and `.example` contacts. Not Dev 34. |

## Track A: Apple and business setup (people, in parallel)

Starts now. It has the longest waits and none of it is code.

1. Confirm the legal entity that will be the App Store seller. Look up or request its D-U-N-S number.
2. Enroll that entity in the Apple Developer Program as an organization ($99 a year). The Account Holder needs signing authority, a company-domain email, and two-factor authentication.
3. Create an Expo account and an organization for EAS Build. EAS builds on Apple's required Xcode 26 in the cloud, so a local Mac is optional.
4. Reserve the bundle ID, for example `io.yamin.jarvis`. It cannot change after the first upload.
5. Publish a privacy policy and a support page on the company domain. The policy must cover mail, texts, calendar, voice, AI processing by a third party, retention, and deletion.
6. Create the App Store Connect record: name, category (Business or Productivity), age-rating answers, and export compliance (HTTPS only).

## Track B: Backend work both clients use

The phone cannot use the web's cookie sign-in as-is. Everything in this track is
also exposed to the web.

### B1. Token sessions

Today `auth.current_session` accepts only the `jarvis_session` cookie, with a 12-hour life.

- Accept `Authorization: Bearer <token>` in the same dependency, same payload, same `_live()` check. Every route keeps working for both clients, with no second code path.
- The phone gets a short access token plus a device refresh token, stored in the phone's secure keychain (`expo-secure-store`).
- `devices` table: who, which phone, created, last seen, revoked. Signing out revokes it. Disabling a person revokes all of theirs.
- Web Settings gets a "Signed-in phones" list with Sign out.

### B2. Finding the business: the front door

`join.yamin.io` already routes Google sign-in to the right `<slug>.yamin.io`.

- New `join.yamin.io/app/start` opens in the phone's system sign-in sheet (`ASWebAuthenticationSession` through `expo-web-browser`). Google allows this; it blocks sign-in inside an embedded web view.
- After sign-in, the front door shows the businesses this person belongs to. They pick one, and the front door hands a one-time code back to the app through a universal link (`https://join.yamin.io/app/callback`).
- The app trades that code with that business's app for a token from B1. The front door still never holds an account's tokens.
- After that the app talks to `https://<slug>.yamin.io/api/...`, the same address the web uses. A person in two businesses can switch without signing out.
- Publish `apple-app-site-association` on `join.yamin.io` for the universal link.

### B3. Sign in with Apple (required item 1)

A public app that uses Google sign-in for the account has to offer an equal login (Guideline 4.8).

- The phone uses the native Apple button (`expo-apple-authentication`) and sends Apple's identity token to the front door. The front door checks it against Apple's public keys with PyJWT.
- Apple may hide the person's email behind a relay address, so it cannot be matched to an invite by email alone. A person links Apple once:
  - while signed in, from Settings on the web or the app ("Add Sign in with Apple"), or
  - at first Apple sign-in, when Apple shares the real address and it matches their invite.
- `sign_in_links` table: person, provider, Apple's stable user id.
- The web login page gets the same Sign in with Apple button (Apple JS), so a person can use either method anywhere.

### B4. Push notifications (required item 2)

- `POST /api/devices/push` registers the phone's push token; sign-out removes it.
- One server-side function sends pushes, through the Expo push service over httpx. It is called from the same places that already write the feed:
  - an approval is waiting (owner)
  - a task is assigned to you, or new tasks landed on your list
  - Ask finished a reply you are waiting on
  - new mail in Jarvis's inbox (owner and admin)
- Text stays short and generic, for example "1 approval waiting" or "Task assigned: 212 E 24th". No email bodies, text messages, or client details (Guideline 4.5.4). Operator sessions never get pushes.
- Tapping a notification opens that approval or task.
- `push_prefs`: per person, per kind, on or off. Changed from web Settings or the app. The app works fully with push off.
- Counters: `push_sent`, `push_failed`, `push_skipped_pref`. A failed push is logged, never silent.

### B5. Account deletion and AI consent

- "Delete my account" in app and web Settings: the person is removed from that business, their tokens and devices are revoked, and Apple sign-in is revoked through Apple's API. The owner of a business gets a confirm step, because it is the business's data.
- A one-time consent screen before first use of Ask or mail reading, saying their content is processed by a third-party AI model. Recorded server-side, so web and app share it.

### B6. API cleanup the phone needs

- Generate OpenAPI types into `packages/shared`, then move the web's `frontend/src/lib/api.ts` onto that shared client.
- Feed, tasks, and inbox lists get cursor paging, so a phone does not load 150 tasks at once.
- Chat already streams from `POST /api/chat`. Expo's `expo/fetch` reads streamed responses, so no new endpoint is needed.
- Voice: the phone records with `expo-audio` and posts to the existing `/api/transcribe`.

## Track C: The Expo app

### C1. Project

- `mobile/` in this repo: Expo, TypeScript, and Expo Router. `packages/shared` holds the API client and types.
- Builds with EAS Build. TestFlight uploads with EAS Submit.
- Dark theme matching the web.

### C2. Screens in version 1

| Screen | What it does | Web twin |
| --- | --- | --- |
| Sign in | Pick a business, then Google or Apple | Login page plus front door |
| Approvals | List, details, Approve or Reject | Approvals tab |
| Tasks | Newest first; done, add, assign, deadline | Tasks tab |
| Ask | Chat with streaming replies, voice in, attachments | Ask |
| Activity | The feed, pull to refresh | Feed |
| Settings | Notifications, linked sign-ins, phones, sign out, delete account, privacy policy | Settings |

Version 1.1: Inbox (owner and admin), Leads, Team invite, and channels.

Left out on the phone: iMessage pairing, which stays on the Mac helper (the phone says "Set up on your Mac"), and the remote browser takeover.

### C3. Behavior rules

- No queued writes while offline. An approval or send only happens with a live server answer, and the phone shows the result the server returned.
- Lists can show the last copy offline, labeled "As of 3:12 PM", read-only.
- On returning to the foreground, refresh the open screen.
- When the server returns 401 or 403, the app shows the server's sentence and does not retry silently.

## Track D: Proving web and app stay the same

This follows the AGENTS.md definition of done, on testco or a staging tenant, never live.

- `docs/mobile/verify_parity.py`:
  - Finish a task with a bearer token, then read it with a web cookie. Same status, same `completed_at`.
  - Approve with a cookie, then read the same approval with a token. Executed once, with one ledger row.
  - A member token is refused on an owner-only route. Then the same check runs with an owner token, so the refusal is shown to be real.
  - Revoke a device, and its token gets 401 on the next call.
- `docs/mobile/verify_push.py`: filing an approval produces exactly one push row and one feed event. A push turned off in preferences produces zero sends and one skip counter, and turning it back on produces the send.
- `docs/mobile/verify_apple_signin.py`: a forged Apple token is refused; a valid test token links one account; a second link for the same Apple id is refused.
- Paste the ledger before and after (Postgres approvals and actions, the tasks database, counters) in each report.

## Track E: App Store (required items 3 and 4)

### E1. Item 3: the daily screens

The C2 screens with native lists and controls, not a web view. That is what keeps the app clear of the "repackaged website" rejection (Guideline 4.2).

### E2. Item 4: the review business

- A sandbox business, for example `review.yamin.io`, with fake tasks, approvals, calendar, and a fake mailbox. Outbound limited to `.example` addresses.
- A reviewer account that cannot expire and does not need a phone code. The login goes in App Review notes. Allow Apple's reviewer to use Sign in with Apple or a demo login on that tenant only.
- The backend stays up for the whole review.

### E3. Release steps

1. Internal TestFlight with the Dev 34 team (up to 100 people, no review wait).
2. Fix what they hit. Run the parity checks again.
3. External TestFlight for a second business (first build goes through beta review).
4. Metadata: screenshots (iPhone Dynamic Island size; skip iPad in version 1 by building for iPhone only), description, keywords, privacy policy and support URLs.
5. Privacy labels: name and email, emails and text messages, audio, user ID, device ID (push), and data processed by a third-party AI, all for app functionality and linked to the user. No tracking.
6. Review notes: what Jarvis is, the demo login, that businesses sign up on the web, and that no purchase happens in the app.
7. Submit for public release.

### E4. Payments

Businesses sign up and pay on the web, not in the app. The app has no prices, sign-up for a new business, or purchase buttons. That matches Apple's rule for services sold directly to organizations for their staff (Guideline 3.1.3(c)) and avoids in-app purchase.

## Order of work

1. **Now:** Track A starts, and the yes items above get answered.
2. **Backend first:** B1 token sessions and B6 shared client, with D parity checks on staging. Then B2 front-door sign-in for the app.
3. **App skeleton:** C1 plus sign-in, Tasks, and Approvals, on internal TestFlight as soon as sign-in works.
4. **Required items:** B3 Sign in with Apple, B4 push, and B5 deletion and consent, each landing on web and app together.
5. **Finish version 1:** Ask, Activity, and Settings. E2 review business.
6. **Store:** E3 release steps, then version 1.1 screens.

## Not changing

- Live `jarvis-app` on :8700 and `/root/jarvis-app` are not touched by any of this.
- The website keeps working the whole time. Nothing is removed from it.
- iMessage stays on the Mac helper. Mail, calendar, the daily digest, and Approvals keep running on the server for both clients.
