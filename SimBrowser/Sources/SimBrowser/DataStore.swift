import Foundation
import SwiftUI

@MainActor
final class DataStore: ObservableObject {
    @Published var hoods: [Hood] = []
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var lastRefreshed: Date?

    /// Where the extractor script lives. Override with `defaults write com.rebecca.SimBrowser extractorPath …`
    var extractorPath: String {
        UserDefaults.standard.string(forKey: "extractorPath")
            ?? NSString(string: "~/Documents/sims_2_project/s2neighborhood.py").expandingTildeInPath
    }

    var dataURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("SimBrowser", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("sims.json")
    }

    func loadCachedOrRefresh() {
        if FileManager.default.fileExists(atPath: dataURL.path) {
            loadFromDisk()
        } else {
            refresh()
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
        let out = dataURL

        Task.detached(priority: .userInitiated) {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            process.arguments = ["python3", script, "--out", out.path]
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
                        return "Extractor exited with status \(process.terminationStatus). \(err.suffix(400))"
                    }
                    return nil
                } catch {
                    return "Could not run extractor at \(script): \(error.localizedDescription)"
                }
            }()

            await MainActor.run {
                self.isLoading = false
                if let failure {
                    self.errorMessage = failure
                } else {
                    self.loadFromDisk()
                }
            }
        }
    }
}
