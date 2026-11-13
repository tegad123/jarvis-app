// swift-tools-version: 6.0
import PackageDescription

// Type-checks the app's SwiftUI sources on Linux against a hand-written
// copy of the SwiftUI declarations they use (Shim/). Run ./check.sh; it
// copies the sources into Generated/ first. This is a check, not a build:
// nothing here ships, and the app's Xcode project does not know about it.
let package = Package(
    name: "JarvisAppTypecheck",
    dependencies: [
        .package(path: "../../JarvisKit"),
    ],
    targets: [
        .target(
            name: "SwiftUI",
            path: "Shim",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .target(
            name: "JarvisAppCheck",
            dependencies: ["SwiftUI", .product(name: "JarvisKit", package: "JarvisKit")],
            path: "Generated",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)
