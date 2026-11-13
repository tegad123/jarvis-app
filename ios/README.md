# Jarvis iPhone client

A native SwiftUI app for one tenant's existing Jarvis API. It is the web app
(`frontend/`), screen for screen, drawn natively: the same navigation, layout,
colours, type, icons and copy as the web's phone layout. It is not a web view.
The server is unchanged.

## Layout

| Path | What it holds |
| --- | --- |
| `JarvisKit/` | The API client, models, wording and icon paths. Builds and tests on Linux. |
| `JarvisApp/JarvisApp/Theme/JarvisTheme.swift` | The one theme file: every colour, space, radius, size, type step and motion value, copied from `frontend/src/jm/tokens/tokens.css`. |
| `JarvisApp/JarvisApp/Theme/IconView.swift` | Draws the web's own icon paths (`frontend/src/jm/shared/Icon.tsx`). |
| `JarvisApp/JarvisApp/Components/` | Jarvis Mono components (`frontend/src/jm/*`) in SwiftUI. |
| `JarvisApp/JarvisApp/Screens/` | One file per web screen. |
| `JarvisApp/JarvisApp/Platform/Platform.swift` | Everything that needs UIKit: fonts, the system sign-in sheet, pickers, pasteboard. |
| `JarvisApp/JarvisApp/Preview/Previews.swift` | A `#Preview` per reference screenshot, on the same invented rows. |
| `JarvisApp/JarvisApp/Fonts/` | Geist Regular, Medium and SemiBold (SIL Open Font License, `OFL.txt`), the web's typeface. |
| `JarvisApp/JarvisApp/Assets.xcassets` | `AccentColor` (#2563EB, `--jm-accent`) and `AppIcon`. |
| `reference/web/` | A stub server and a headless-Chrome capture of every web screen at phone size; `screens/` holds the pictures. |
| `reference/typecheck/` | A Linux type-check of the app's sources against a hand-written copy of the SwiftUI declarations they use. |

The web app has no logo or favicon in this repo: its mark is the word
"Jarvis" in Geist SemiBold. The app icon is that wordmark's "J" in the web's
foreground white on its background black (#0A0A0A).

## Screen map

Each row is one thing a signed-in team member can see. Compare the SwiftUI
preview named in the last column against the web picture in
`reference/web/screens/`.

| Screen | Web source | SwiftUI source | Web picture | Preview |
| --- | --- | --- | --- | --- |
| Sign in | `frontend/src/screens/Login.tsx`, `jm/SignIn/SignIn.tsx` | `Screens/SignInScreen.swift` | `01-signin`, `02-signin-pin-people`, `03-signin-pin-entry` | 01, 02, 03 |
| Home (the sidebar, full width on a phone) | `frontend/src/components/Shell.tsx`, `jm/Sidebar/Sidebar.tsx`, `jm/ConversationRow` | `Screens/ShellView.swift` | `04-home`, `20-home-light`, `23-home-member` | 04, 20, 23 |
| New channel, rename channel | `jm/NewChannelSheet/NewChannelSheet.tsx` | `Screens/ShellView.swift` (`NewChannelSheet`) | `05-new-channel-sheet` | 05 |
| New category, rename, move a channel | `frontend/src/components/CategorySheet.tsx` | `Screens/ShellView.swift` (`CategorySheet`) | none | 05b |
| Ask Jarvis and channels | `frontend/src/screens/Ask.tsx`, `jm/MessageBubble`, `jm/Composer`, `jm/StepList`, `jm/SystemLine`, `jm/FileChip` | `Screens/ChatScreen.swift` | `06-ask-direct`, `06b-ask-direct-empty`, `07-channel`, `09-channel-empty`, `21-ask-direct-light` | 06, 06b, 07, 09, 21 |
| Channel members | `jm/MembersSheet/MembersSheet.tsx` | `Screens/ChatScreen.swift` (`MembersSheet`) | `08-channel-members-sheet` | 08 |
| Activity | `frontend/src/screens/Feed.tsx`, `jm/ActivityRow`, `jm/TodaySummary`, `jm/FilterChips` | `Screens/ActivityScreen.swift` | `10-activity` | 10 |
| Approvals | `frontend/src/screens/Approvals.tsx`, `jm/ApprovalCard` | `Screens/ApprovalsScreen.swift` | `11-approvals`, `11-approvals-full` | 11 |
| Inbox | `frontend/src/screens/Inbox.tsx` | `Screens/InboxScreen.swift` | `12-inbox` | 12 |
| Tasks | `frontend/src/screens/Tasks.tsx`, `jm/TaskRow` | `Screens/TasksScreen.swift` | `13-tasks`, `13-tasks-full`, `14-tasks-new` | 13, 14 |
| Leads | `frontend/src/screens/Leads.tsx`, `jm/LeadRow` | `Screens/LeadsScreen.swift` | `15-leads`, `16-leads-new`, `16-leads-new-full`, `22-leads-light` | 15, 16, 22 |
| One lead | `frontend/src/components/LeadSheet.tsx` | `Screens/LeadsScreen.swift` (`LeadSheet`) | `17-lead-sheet`, `17-lead-sheet-p2` | 17 |
| Settings: Google account, website logins, iMessage, team, channels | `frontend/src/screens/Settings.tsx`, `jm/SettingsSection`, `jm/PairingCode` | `Screens/SettingsScreen.swift` | `19-settings`, `19-settings-p2`…`p4`, `19-settings-full`, `22b-settings-light` | 19, 22b |
| Page header, empty, error and loading states | `frontend/src/components/Page.tsx`, `jm/EmptyState`, `jm/ErrorState` | `Components/Blocks.swift` | in every picture | "Empty: …", "Components" |

Calendar has no screen of its own on the web: Jarvis reads and books it from
chat, and the Google account it uses is connected in Settings. "Connected
sites" is Settings → Website logins.

Phone only, with no web twin:

| Screen | Why it exists | SwiftUI source | Preview |
| --- | --- | --- | --- |
| Connect | A browser has an address bar; a phone has to be told which Jarvis. | `Screens/RootView.swift` (`ConnectScreen`) | 00 |
| Before you send | The App Store wants a yes before a message goes to an AI service. | `Screens/ChatScreen.swift` (`ConsentSheet`) | 09b |
| Settings: Ask on this phone, your sign-in, delete this team, this phone | The same consent, in-app account closure, and the saved address. | `Screens/PhoneSettings.swift` | 19 |

## What is not the same as the web

- **Operator access** and the operator's outbox are not here. The server never
  signs a phone in on the operator passphrase (`app/devices.py`).
- **Voice notes, read-aloud and Jarvis's browser window** (the mic and speaker
  buttons, the take-over card) are not built.
- **Lead spreadsheet import** and the **Mac app download** stay on the web;
  the iMessage steps say so.
- **Connecting Google** (Settings, the Inbox, and the chat's "Connect your
  Google account") opens the tenant's own page in the browser, where the
  person signs in again. The connect flow is a browser redirect chain that
  ends on a web page.
- **Menus**: the web's hover and right-click menus are a long press; its
  selects are the system menu; its date input is a pill with a calendar under
  it; an attachment opens in Quick Look rather than a new tab.
- **Small drawing differences**: an @mention is tinted without the web's
  rounded corners, streamed words do not fade in one by one, and the mention
  list completes the @word at the end of the draft rather than at the caret.
- **The session is kept in memory.** Closing the app signs the person out.
  Keeping it would mean the Keychain, which is not in this change.

## What a Mac with Xcode builds

Store uploads need Xcode 26 or later and the iOS 26 SDK (Apple, since 28 April 2026).
The project itself is an Xcode project with deployment target iOS 17.

```bash
cd ios/JarvisApp
xcodebuild -scheme JarvisApp -destination 'platform=iOS Simulator,name=iPhone 17' -configuration Debug build
```

Signing uses an empty development team and the placeholder bundle id
`com.example.jarvis.development`. Replace both before a device or TestFlight
build. There is no Apple team id in this repo.

The previews in `Preview/Previews.swift` run without a server. To see the
same rows in the simulator, run the stub and type its address on the Connect
screen. It needs HTTPS, so put it behind a tunnel or a local certificate:

```bash
node ios/reference/web/server.mjs      # the web app and the stub API
node ios/reference/web/capture.mjs     # retake the web pictures
```

## What Linux checks

None of these is an Xcode or simulator build.

```bash
cd ios/JarvisKit && swift test         # the client, wording, icons, fixtures
ios/reference/typecheck/check.sh       # the app's sources against the shim
```

`check.sh` compiles every file under `JarvisApp/JarvisApp` against
`reference/typecheck/Shim`, a hand-written copy of the SwiftUI declarations
the app uses. It catches wrong names, labels and types. It cannot catch what
the copy gets wrong about SwiftUI, it does not see the UIKit half of
`Platform.swift` or the `UIColor` branch of the theme, and it draws nothing.

## Delete the team

The owner, and only the owner, can `POST /api/account/tenant-deletion`.
It requires the owner's current session, the owner's PIN, the phrase
`delete <tenant slug>`, a one-time nonce, and the header
`X-Jarvis-Confirm: delete-tenant`. GET does not delete. A member or admin
receives 403.

That removes the records this process stores: accounts, leads, tasks,
channels and messages, local files under the tenant home, and this app's
copy of Google tokens. It then refuses every sign-in. It does not delete
the Gmail mailbox, backups, host environment files, or prompts Anthropic
already has. If the Postgres role cannot delete the append-only audit log,
those rows stay and the response says so. The per-person request above is
unchanged and is not this action.

## Sign-in off, deletion still pending

`POST /api/account/deletion-request` with the caller's PIN and the words
`request-account-deletion` disables that person, clears the PIN, and writes a
`deletion_requests` row whose status is `pending`. The JSON `deleted` field is
false. Leads, tasks, and messages stay. The only owner is refused. This is not
account deletion. Removing the person and their shared history is still a
release blocker because those records belong to the tenant, not only to them.

## Privacy policy

`site/privacy.md` describes the Yamin service at yamin.io. The phone does not
link to it, because the address the person typed may be a different tenant.

## Sign-in

The sign-in screen is the web's: Continue with Google first, PIN sign-in
behind a link. Google runs in the system sign-in sheet against the tenant's
own `/app/start` page and comes back with a one-time code that the app trades
for this phone's tokens (`app/devices.py`). PIN sign-in posts `person_key`
and `pin` to `/api/login`, as the web does. A Google-only tenant shows no PIN
link, as on the web (`/api/auth/methods`). App Store guideline 4.8 (offering
another sign-in next to Google) is not decided here.
