# Jarvis iPhone client

SwiftUI app for one tenant's existing Jarvis API. The web app is unchanged.
This branch is based on `cursor/open-house-lead-tracker-5feb` because lead
detail and contact logging live there and are not on `platform` yet.

## What a Mac with Xcode builds

Store uploads need Xcode 26 or later and the iOS 26 SDK (Apple, since 28 April 2026).
The project itself is an Xcode project with deployment target iOS 17.

```bash
cd ios/JarvisApp
xcodebuild -scheme JarvisApp -destination 'platform=iOS Simulator,name=iPhone 16' -configuration Debug build
```

Signing uses an empty development team and the placeholder bundle id
`com.example.jarvis.development`. Replace both before a device or TestFlight
build. There is no Apple team id in this repo.

The Linux check is the library tests, not an Xcode or simulator build:

```bash
cd ios/JarvisKit
swift test
```

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

## Store login

This build sends `person_key` and `pin` only. Google sign-in remains on the
web app. Omitting it here does not decide App Store guideline 4.8.
