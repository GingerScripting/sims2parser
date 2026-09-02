import Foundation

/// Where the Python side lives, for both apps.
///
/// The scripts ride along inside each app bundle (`make_app.sh` copies them
/// into `Contents/Resources`), so a packaged build has no dependency on
/// where the repo was cloned. The checkout path is the fallback for
/// `swift run` during development, where there is no bundle to read from.
///
/// The interpreter is deliberately not `/usr/bin/env python3`: an app launched
/// from Finder inherits only /usr/bin:/bin:/usr/sbin:/sbin, so env finds
/// Apple's python3 (3.9) whatever the user has installed, and the same app run
/// from a terminal would pick a different one. Prefer a real install, fall
/// back to the system copy. Both can be overridden per app with
/// `defaults write <bundle id> pythonPath …` / `… <scriptKey> …`.
public enum PythonLocator {
    public static let checkoutDirectory =
        NSString(string: "~/Documents/sims_2_project").expandingTildeInPath

    /// The python3 to run the scripts with.
    public static func interpreter(defaultsKey: String = "pythonPath") -> String {
        if let custom = UserDefaults.standard.string(forKey: defaultsKey) { return custom }
        let candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
            ?? "/usr/bin/python3"
    }

    /// Full path of a script, in falling priority: an explicit override in
    /// UserDefaults, the copy bundled inside the app, then a source checkout.
    public static func script(_ name: String, defaultsKey: String? = nil) -> String {
        if let key = defaultsKey, let custom = UserDefaults.standard.string(forKey: key) { return custom }
        // `S2_TOOLKIT_DIR` names the folder holding the scripts — how the
        // drive executable points at the checkout it was built from.
        if let dir = ProcessInfo.processInfo.environment["S2_TOOLKIT_DIR"] {
            return (dir as NSString).appendingPathComponent(name)
        }
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent(name).path,
           FileManager.default.isReadableFile(atPath: bundled) {
            return bundled
        }
        return (checkoutDirectory as NSString).appendingPathComponent(name)
    }
}
