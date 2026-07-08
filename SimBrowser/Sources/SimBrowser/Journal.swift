import AppKit
import SwiftUI

struct JournalEntry: Codable, Identifiable, Hashable {
    var id = UUID()
    var title: String
    var created = Date()
    var modified = Date()
    var body = ""
}

struct JournalMention: Identifiable {
    var id: UUID  // entry id
    var title: String
    var line: String
}

@MainActor
final class JournalStore: ObservableObject {
    @Published var entriesByHood: [String: [JournalEntry]] = [:]
    private var saveTask: Task<Void, Never>?

    private var fileURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("SimBrowser", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("journal.json")
    }

    init() {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        if let data = try? Data(contentsOf: fileURL),
           let decoded = try? decoder.decode([String: [JournalEntry]].self, from: data) {
            entriesByHood = decoded
        }
    }

    func entries(for hoodID: String) -> [JournalEntry] {
        entriesByHood[hoodID] ?? []
    }

    /// "01 Spring" → "01 Summer" → … → "01 Winter" → "02 Spring"
    func suggestedTitle(for hoodID: String) -> String {
        let seasons = ["Spring", "Summer", "Fall", "Winter"]
        guard let last = entries(for: hoodID).last else { return "01 Spring" }
        let parts = last.title.split(separator: " ")
        if parts.count == 2, let year = Int(parts[0]),
           let idx = seasons.firstIndex(of: String(parts[1])) {
            if idx == seasons.count - 1 {
                return String(format: "%02d %@", year + 1, seasons[0])
            }
            return String(format: "%02d %@", year, seasons[idx + 1])
        }
        return "New Entry"
    }

    @discardableResult
    func addEntry(hoodID: String) -> JournalEntry {
        let entry = JournalEntry(title: suggestedTitle(for: hoodID))
        entriesByHood[hoodID, default: []].append(entry)
        scheduleSave()
        return entry
    }

    func deleteEntry(hoodID: String, id: UUID) {
        entriesByHood[hoodID]?.removeAll { $0.id == id }
        scheduleSave()
    }

    func binding(hoodID: String, id: UUID) -> Binding<JournalEntry>? {
        guard let idx = entriesByHood[hoodID]?.firstIndex(where: { $0.id == id }) else { return nil }
        return Binding(
            get: { self.entriesByHood[hoodID]?[safe: idx] ?? JournalEntry(title: "") },
            set: { newValue in
                var e = newValue
                e.modified = Date()
                self.entriesByHood[hoodID]?[idx] = e
                self.scheduleSave()
            }
        )
    }

    /// Entries in this hood whose text mentions the sim by full name.
    func mentions(of fullName: String, hoodID: String) -> [JournalMention] {
        guard fullName.count > 3 else { return [] }
        return entries(for: hoodID).compactMap { entry in
            guard let range = entry.body.range(of: fullName, options: .caseInsensitive) else { return nil }
            let lineRange = entry.body.lineRange(for: range)
            var line = entry.body[lineRange].trimmingCharacters(in: .whitespacesAndNewlines)
            if line.count > 140 { line = String(line.prefix(140)) + "…" }
            return JournalMention(id: entry.id, title: entry.title, line: line)
        }
    }

    private func scheduleSave() {
        saveTask?.cancel()
        saveTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 500_000_000)
            guard let self, !Task.isCancelled else { return }
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            encoder.dateEncodingStrategy = .iso8601
            if let data = try? encoder.encode(self.entriesByHood) {
                try? data.write(to: self.fileURL, options: .atomic)
            }
        }
    }
}

extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}

// MARK: - Views

struct JournalRow: View {
    let entry: JournalEntry
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(entry.title).fontWeight(.medium)
            Text(snippet.isEmpty ? "Empty entry" : snippet)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .padding(.vertical, 2)
    }
    private var snippet: String {
        entry.body.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "\n", with: " · ")
    }
}

struct JournalEditorView: View {
    @Binding var entry: JournalEntry
    var onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                TextField("Entry title", text: $entry.title)
                    .textFieldStyle(.plain)
                    .font(.system(size: 26, weight: .bold))
                Spacer()
                Button(role: .destructive) {
                    onDelete()
                } label: {
                    Image(systemName: "trash")
                }
                .buttonStyle(.borderless)
                .help("Delete this entry")
            }
            .padding(.horizontal, 28)
            .padding(.top, 24)

            Text("Started \(entry.created.formatted(date: .abbreviated, time: .omitted)) · edited \(entry.modified.formatted(date: .abbreviated, time: .shortened))")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .padding(.horizontal, 28)
                .padding(.top, 2)

            TextEditor(text: $entry.body)
                .font(.system(size: 14))
                .lineSpacing(3)
                .scrollContentBackground(.hidden)
                .padding(.horizontal, 22)
                .padding(.vertical, 12)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

/// Generic isolation wrapper: hosts content in its own NSHostingView so
/// scrollable views inside it can't trigger the macOS 26 root-layout
/// extension bug (see DetailHost / GeometryProbe history).
struct IsolatedPane<Content: View>: NSViewRepresentable {
    let content: Content
    init(@ViewBuilder _ content: () -> Content) { self.content = content() }

    func makeNSView(context: Context) -> NSHostingView<Content> {
        NSHostingView(rootView: content)
    }
    func updateNSView(_ nsView: NSHostingView<Content>, context: Context) {
        nsView.rootView = content
    }
}
