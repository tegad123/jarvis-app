// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "JarvisKit",
    products: [
        .library(name: "JarvisKit", targets: ["JarvisKit"]),
    ],
    targets: [
        .target(
            name: "JarvisKit",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(
            name: "JarvisKitTests",
            dependencies: ["JarvisKit"],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)
