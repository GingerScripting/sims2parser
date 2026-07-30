import Foundation
import SwiftUI

@MainActor
final class DataStore: ObservableObject {
    @Published var hoods: [Hood] = []
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var lastRefreshed: Date?
    /// hood id → changes detected at the last refresh, until inserted/dismissed
    @Published var detectedChanges: [String: [Change]] = [:] {
        didSet { persistChanges() }
    }

    /// Where the extractor script lives. Override with
    /// `defaults write org.macadmins.rebecca.simbrowser extractorPath …`
    var extractorPath: String {
        UserDefaults.standard.string(forKey: "extractorPath")
            ?? NSString(string: "~/Documents/sims_2_project/s2neighborhood.py").expandingTildeInPath
    }

    /// Which interpreter runs the extractor. Not `/usr/bin/env python3`: an app
    /// launched from Finder inherits only /usr/bin:/bin:/usr/sbin:/sbin, so env
    /// finds Apple's python3 (3.9) whatever the user has installed, and the
    /// same app run from a terminal would pick a different one. Prefer a real
    /// install, fall back to the system copy. Override with
    /// `defaults write org.macadmins.rebecca.simbrowser pythonPath …`
    var pythonPath: String {
        if let custom = UserDefaults.standard.string(forKey: "pythonPath") { return custom }
        let candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
            ?? "/usr/bin/python3"
    }

    var dataURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("SimBrowser", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("sims.json")
    }

    private var changesURL: URL {
        dataURL.deletingLastPathComponent().appendingPathComponent("pending_changes.json")
    }

    func loadCachedOrRefresh() {
        if let data = try? Data(contentsOf: changesURL) {
            if let saved = try? JSONDecoder().decode([String: [Change]].self, from: data) {
                detectedChanges = saved
            } else if let legacy = try? JSONDecoder().decode([String: [String]].self, from: data) {
                // Pending lines written before changes carried a household/category.
                detectedChanges = legacy.mapValues { lines in
                    lines.map { Change($0, household: "", category: .misc) }
                }
            }
        }
        if FileManager.default.fileExists(atPath: dataURL.path) {
            loadFromDisk()
        } else {
            refresh()
        }
    }

    func clearChanges(hoodID: String) {
        detectedChanges[hoodID] = nil
    }

    /// Drops the changes just filed into an entry, keeping any left unticked.
    func clearChanges(hoodID: String, ids: Set<UUID>) {
        guard var remaining = detectedChanges[hoodID] else { return }
        remaining.removeAll { ids.contains($0.id) }
        detectedChanges[hoodID] = remaining.isEmpty ? nil : remaining
    }

    private func persistChanges() {
        if detectedChanges.isEmpty {
            try? FileManager.default.removeItem(at: changesURL)
        } else if let data = try? JSONEncoder().encode(detectedChanges) {
            try? data.write(to: changesURL, options: .atomic)
        }
    }

    func loadFromDisk() {
        do {
            let data = try Data(contentsOf: dataURL)
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            let db = try decoder.decode(Database.self, from: data)
            hoods = db.hoods
            errorMessage = nil
            lastRefreshed = (try? dataURL.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        } catch {
            errorMessage = "Could not read sim data: \(error.localizedDescription)"
        }
    }

    /// Re-run the Python extractor against the save files, then reload.
    func refresh() {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        let script = extractorPath
        let python = pythonPath
        let out = dataURL
        let previousHoods = hoods

        Task.detached(priority: .userInitiated) {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: python)
            process.arguments = [script, "--out", out.path]
            let stderrPipe = Pipe()
            process.standardError = stderrPipe
            process.standardOutput = Pipe()

            let failure: String? = {
                do {
                    try process.run()
                    process.waitUntilExit()
                    if process.terminationStatus != 0 {
                        let err = String(data: stderrPipe.fileHandleForReading.readDataToEndOfFile(),
                                         encoding: .utf8) ?? ""
                        // Name the interpreter: the usual cause of a failure
                        // here is the script meeting a python it doesn't like,
                        // and the traceback alone doesn't say which one ran.
                        return "Extractor exited with status \(process.terminationStatus) "
                            + "(ran \(python)). \(err.suffix(400))"
                    }
                    return nil
                } catch {
                    return "Could not run \(python) \(script): \(error.localizedDescription)"
                }
            }()

            await MainActor.run {
                self.isLoading = false
                if let failure {
                    self.errorMessage = failure
                } else {
                    self.loadFromDisk()
                    if !previousHoods.isEmpty {
                        let diff = ChangeDetector.diff(old: previousHoods, new: self.hoods)
                        // Merge with anything still pending from earlier refreshes
                        for (hoodID, changes) in diff {
                            self.detectedChanges[hoodID, default: []].append(contentsOf: changes)
                        }
                    }
                }
            }
        }
    }
}
