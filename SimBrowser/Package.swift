// swift-tools-version: 5.9
import PackageDescription

// Two apps, one shared library. Sim Browser reads the extracted sims.json;
// Sim Studio edits packages through the s2studio.py daemon. SimKit holds the
// pieces both need — the NSHostingView isolation wrapper, the flow layout,
// and the logic that finds python3 and the bundled scripts.
let package = Package(
    name: "SimBrowser",
    platforms: [.macOS(.v13)],
    targets: [
        .target(name: "SimKit", path: "Sources/SimKit"),
        .executableTarget(name: "SimBrowser", dependencies: ["SimKit"], path: "Sources/SimBrowser"),
        // Sim Studio's bridge and session layer, apart from its views so a
        // second executable can drive it without a window.
        .target(name: "SimStudioCore", dependencies: ["SimKit"], path: "Sources/SimStudioCore"),
        .executableTarget(name: "SimStudio", dependencies: ["SimKit", "SimStudioCore"], path: "Sources/SimStudio"),
        // Walks the editing flow through PackageSession and the real daemon
        // on a scratch copy of a donor: `swift run SimStudioDrive`. Not a
        // test target on purpose — XCTest and Swift Testing both need Xcode,
        // and the README asks only for the Command Line Tools.
        .executableTarget(name: "SimStudioDrive", dependencies: ["SimStudioCore"], path: "Sources/SimStudioDrive"),
    ]
)
