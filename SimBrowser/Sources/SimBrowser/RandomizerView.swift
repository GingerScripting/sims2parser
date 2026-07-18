import SwiftUI

/// Loads the random-event table exported from the planning spreadsheet,
/// falling back to a built-in copy of the same 50 events.
enum RandomEvents {
    /// Where the spreadsheet export lives. Override with
    /// `defaults write org.macadmins.rebecca.simbrowser randomEventsCSV …`
    static var csvPath: String {
        UserDefaults.standard.string(forKey: "randomEventsCSV")
            ?? NSString(string: "~/Documents/The Sims 2/Random Events-Table 1.csv").expandingTildeInPath
    }

    struct Table {
        let events: [String]
        let fromSpreadsheet: Bool
    }

    static func load() -> Table {
        if let text = try? String(contentsOfFile: csvPath, encoding: .utf8) {
            let events = text.split(whereSeparator: \.isNewline).compactMap { line -> String? in
                let fields = parseCSVLine(String(line))
                guard fields.count >= 2, Int(fields[0]) != nil else { return nil }
                let event = fields[1].trimmingCharacters(in: .whitespaces)
                return event.isEmpty ? nil : event
            }
            if !events.isEmpty { return Table(events: events, fromSpreadsheet: true) }
        }
        return Table(events: builtIn, fromSpreadsheet: false)
    }

    /// Minimal CSV field splitter (handles quoted fields with embedded commas).
    private static func parseCSVLine(_ line: String) -> [String] {
        var fields: [String] = []
        var current = ""
        var inQuotes = false
        for ch in line {
            switch ch {
            case "\"": inQuotes.toggle()
            case "," where !inQuotes: fields.append(current); current = ""
            default: current.append(ch)
            }
        }
        fields.append(current)
        return fields
    }

    static let builtIn = [
        "Be left at altar/leave your fiancé at altar",
        "Teen sim gets caught sneaking out",
        "Adopt a child",
        "Make an enemy",
        "Change career",
        "Grow up with low aspiration",
        "Have a horrible date",
        "Asexual sim",
        "Somebody cheats",
        "Get a kitten",
        "Get a puppy",
        "Get to negative aspiration",
        "Change aspiration",
        "Try for a baby",
        "Change a Sim’s orientation",
        "No woohoo",
        "No pausing",
        "Only play one character",
        "Have a baby",
        "Get electrocuted",
        "Become unemployed",
        "Someone dies",
        "Start a romance",
        "Swap sexuality",
        "Go on vacation",
        "Celebrate a seasonal holiday",
        "Quit job",
        "Become a vampire",
        "Delete oven",
        "Try vegetarianism",
        "Love off the land (fishing and gardening)",
        "Keep up a perfect garden",
        "Fire all servicepeople",
        "Start a business",
        "Start a fire",
        "Repo man",
        "Get engaged",
        "Get abducted by aliens",
        "Redecorate",
        "Build another story on your house",
        "Move someone in",
        "Go on a date",
        "Get a skill badge",
        "Make a friend",
        "Make a Plantsim",
        "Play with the walls up",
        "Make a werewolf",
        "Get full face makeup done",
        "Shave someone’s head",
        "Spend all the family’s money",
    ]
}

struct RandomizerView: View {
    @State private var table = RandomEvents.load()
    @State private var rollNumber: Int?
    @State private var dieFace = 5

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("RANDOM EVENT")
                .font(.caption).fontWeight(.semibold)
                .foregroundStyle(.secondary)
                .kerning(0.5)

            VStack(alignment: .leading, spacing: 10) {
                if let n = rollNumber {
                    Text(table.events[n])
                        .font(.title3).fontWeight(.medium)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                    Text("#\(n + 1) of \(table.events.count)")
                        .font(.caption).fontWeight(.medium)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Color.purple.opacity(0.15), in: Capsule())
                        .foregroundStyle(.purple)
                } else {
                    Text("Roll for a gameplay idea")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, minHeight: 84, alignment: .leading)
            .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 10))

            HStack {
                Button {
                    roll()
                } label: {
                    Label(rollNumber == nil ? "Roll" : "Roll Again",
                          systemImage: "die.face.\(dieFace)")
                }
                .keyboardShortcut(.defaultAction)
                Spacer()
                Text(table.fromSpreadsheet ? "From spreadsheet (\(table.events.count) events)"
                                           : "Built-in list (\(table.events.count) events)")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .help(table.fromSpreadsheet ? RandomEvents.csvPath
                                                : "Export “Random Events” from Numbers to \(RandomEvents.csvPath) to use your latest list")
            }
        }
        .padding(16)
        .frame(width: 340)
        .onAppear { table = RandomEvents.load() }
    }

    private func roll() {
        var n = Int.random(in: 0..<table.events.count)
        // Avoid handing back the same event twice in a row.
        if table.events.count > 1, n == rollNumber {
            n = (n + 1) % table.events.count
        }
        rollNumber = n
        dieFace = Int.random(in: 1...6)
    }
}
