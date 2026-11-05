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

## Account deletion

Not implemented. A person shares channels, leads, and sign-in records with the
tenant, and signup creates a whole tenant. There is no API that deletes only
one person without guessing what else goes. That is a release blocker. Settings
says so and does not pretend a button does it.

## Store login

This build sends `person_key` and `pin` only. Google sign-in remains on the
web app. Omitting it here does not decide App Store guideline 4.8.
