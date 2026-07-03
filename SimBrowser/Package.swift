// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "SimBrowser",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "SimBrowser", path: "Sources/SimBrowser")
    ]
)
