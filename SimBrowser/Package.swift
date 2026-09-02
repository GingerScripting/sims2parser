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
        .executableTarget(name: "SimStudio", dependencies: ["SimKit"], path: "Sources/SimStudio"),
    ]
)
