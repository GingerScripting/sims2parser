import SwiftUI
import SimKit

/// The detected-changes review sheet: everything the save diff found, grouped
/// by household the way a rotational season is actually played, with a tick box
/// on each line so the noise can be left behind.
struct ChangeReviewView: View {
    let hoodName: String
    let changes: [Change]
    /// Every entry in this hood, so the season just *played* can be picked —
    /// which is not necessarily the season currently being *read*.
    let entries: [JournalEntry]
    /// Title a brand new entry would get, shown on the "new entry" option.
    let newEntryTitle: String
    /// nil destination means "make a new entry".
    var onInsert: ([Change], UUID?) -> Void
    var onCancel: () -> Void

    @State private var picked: Set<UUID>
    @State private var destination: UUID?

    init(hoodName: String, changes: [Change], entries: [JournalEntry],
         selectedEntry: UUID?, newEntryTitle: String,
         onInsert: @escaping ([Change], UUID?) -> Void, onCancel: @escaping () -> Void) {
        self.hoodName = hoodName
        self.changes = changes
        self.entries = entries
        self.newEntryTitle = newEntryTitle
        self.onInsert = onInsert
        self.onCancel = onCancel
        _picked = State(initialValue: Set(changes.map { $0.id }))
        // Default to the open entry, but only if there is one to default to.
        _destination = State(initialValue: entries.contains { $0.id == selectedEntry }
                             ? selectedEntry : entries.last?.id)
    }

    private var destinationTitle: String {
        entries.first { $0.id == destination }?.title ?? newEntryTitle
    }

    private var groups: [ChangeDigest.Group] { ChangeDigest.grouped(changes) }
    private var selected: [Change] { changes.filter { picked.contains($0.id) } }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            IsolatedPane {
                List {
                    ForEach(groups) { group in
                        Section {
                            ForEach(group.changes) { change in
                                row(change)
                            }
                        } header: {
                            HStack {
                                Text(group.title)
                                Spacer()
                                Button("None") { picked.subtract(group.changes.map { $0.id }) }
                                    .buttonStyle(.link)
                                    .font(.caption)
                                Button("All") { picked.formUnion(group.changes.map { $0.id }) }
                                    .buttonStyle(.link)
                                    .font(.caption)
                            }
                        }
                    }
                }
                .listStyle(.inset)
                .frame(minWidth: 520, minHeight: 320)
            }
            Divider()
            footer
        }
        .frame(width: 620, height: 520)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text("What happened in \(hoodName)")
                    .font(.title2).fontWeight(.semibold)
                Text("\(changes.count) change\(changes.count == 1 ? "" : "s") since the last save read, across \(groups.count) household\(groups.count == 1 ? "" : "s").")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Picker("Write into", selection: $destination) {
                ForEach(entries) { entry in
                    Text(entry.title).tag(Optional(entry.id))
                }
                Divider()
                Text("New entry — \(newEntryTitle)").tag(UUID?.none)
            }
            .pickerStyle(.menu)
            .fixedSize()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
    }

    private func row(_ change: Change) -> some View {
        Toggle(isOn: Binding(
            get: { picked.contains(change.id) },
            set: { on in
                if on { picked.insert(change.id) } else { picked.remove(change.id) }
            }
        )) {
            HStack(spacing: 8) {
                Image(systemName: change.category.symbol)
                    .foregroundStyle(.secondary)
                    .frame(width: 18)
                Text(change.text)
            }
        }
        .toggleStyle(.checkbox)
    }

    private var footer: some View {
        HStack {
            Button("Select All") { picked = Set(changes.map { $0.id }) }
                .buttonStyle(.link)
            Button("Select None") { picked = [] }
                .buttonStyle(.link)
            Spacer()
            Button("Cancel", role: .cancel) { onCancel() }
                .keyboardShortcut(.cancelAction)
            Button("Add \(selected.count) to \(destinationTitle)") {
                onInsert(selected, destination)
            }
            .keyboardShortcut(.defaultAction)
            .disabled(selected.isEmpty)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }
}
