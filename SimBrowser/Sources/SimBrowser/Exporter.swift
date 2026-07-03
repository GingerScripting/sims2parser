import AppKit
import Foundation
import UniformTypeIdentifiers

enum CSVExporter {
    static let columns: [(String, (Sim, String) -> String)] = [
        ("First", { s, _ in s.first }),
        ("Last", { s, _ in s.last }),
        ("Household", { s, _ in s.household }),
        ("Address", { s, _ in s.address }),
        ("Hood", { _, hood in hood }),
        ("Age", { s, _ in s.age }),
        ("Gender", { s, _ in s.gender }),
        ("Sign", { s, _ in s.zodiac }),
        ("Ambition", { s, _ in s.aspirations.joined(separator: "; ") }),
        ("Major", { s, _ in s.major }),
        ("Career", { s, _ in trackName(s.career) }),
        ("Job Title", { s, _ in s.careerTitle }),
        ("Career Level", { s, _ in s.careerLevel > 0 && !s.career.isEmpty ? String(s.careerLevel) : "" }),
        ("Retired From", { s, _ in s.retiredTitle.isEmpty ? trackName(s.retiredCareer) : s.retiredTitle }),
        ("Orientation", { s, _ in s.orientation }),
        ("Mother", { s, _ in s.mother }),
        ("Father", { s, _ in s.father }),
        ("Spouse", { s, _ in s.spouse }),
        ("Siblings", { s, _ in s.siblings.joined(separator: "; ") }),
        ("Children", { s, _ in s.children.joined(separator: "; ") }),
        ("Best Friend", { s, _ in s.bestFriends.joined(separator: "; ") }),
        ("Loves", { s, _ in s.loves.joined(separator: "; ") }),
        ("Enemies", { s, _ in s.enemies.joined(separator: "; ") }),
        ("Funds", { s, _ in s.household.isEmpty ? "" : String(s.funds) }),
        ("Bio", { s, _ in s.bio }),
    ]

    static func trackName(_ track: String) -> String {
        track
            .replacingOccurrences(of: "Adult - ", with: "")
            .replacingOccurrences(of: "Teen Elder - ", with: "")
            .replacingOccurrences(of: "Pet - ", with: "Pet ")
    }

    static func csv(for sims: [Sim], hoodName: String) -> String {
        var lines = [columns.map { escape($0.0) }.joined(separator: ",")]
        for sim in sims {
            lines.append(columns.map { escape($0.1(sim, hoodName)) }.joined(separator: ","))
        }
        return lines.joined(separator: "\r\n") + "\r\n"
    }

    private static func escape(_ field: String) -> String {
        if field.contains(",") || field.contains("\"") || field.contains("\n") || field.contains("\r") {
            return "\"" + field.replacingOccurrences(of: "\"", with: "\"\"") + "\""
        }
        return field
    }

    /// Show a save panel and write the CSV. Runs on the main actor.
    @MainActor
    static func save(sims: [Sim], hoodName: String, suggestedName: String) {
        guard !sims.isEmpty else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.commaSeparatedText]
        panel.nameFieldStringValue = suggestedName
        panel.title = "Export \(sims.count) sim\(sims.count == 1 ? "" : "s") to CSV"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            // BOM so Excel/Numbers open UTF-8 accents correctly
            var data = Data([0xEF, 0xBB, 0xBF])
            data.append(csv(for: sims, hoodName: hoodName).data(using: .utf8)!)
            try data.write(to: url)
        } catch {
            let alert = NSAlert()
            alert.messageText = "Export failed"
            alert.informativeText = error.localizedDescription
            alert.runModal()
        }
    }
}
