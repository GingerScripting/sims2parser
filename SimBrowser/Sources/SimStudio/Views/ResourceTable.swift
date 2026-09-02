import SwiftUI
import SimStudioCore
import AppKit

/// The middle column: one row per resource in the current filter. Selection
/// drives the detail pane; the context menu carries the structural edits.
struct ResourceTable: View {
    @ObservedObject var session: PackageSession
    let rows: [ResourceRow]
    var onNewResource: () -> Void
    var onSplit: ([TGI]) -> Void = { _ in }

    @State private var sortOrder = [KeyPathComparator(\ResourceRow.typeName), KeyPathComparator(\ResourceRow.instance)]
    @State private var renameTarget: TGI?
    @State private var deleteTargets: [TGI] = []

    private var sorted: [ResourceRow] { rows.sorted(using: sortOrder) }

    var body: some View {
        Table(sorted, selection: $session.selectedTGIs, sortOrder: $sortOrder) {
            TableColumn("Type", value: \.typeName) { r in
                Text(r.typeName)
            }
            .width(min: 56, ideal: 72)
            TableColumn("Name", value: \.nameSort) { r in
                Text(r.name ?? "")
                    .lineLimit(1)
                    .help(r.name ?? "This type of resource carries no name")
            }
            .width(min: 120, ideal: 240)
            TableColumn("Group", value: \.group) { r in
                Text(hex8(r.group)).font(.system(.body, design: .monospaced))
            }
            .width(min: 96, ideal: 100)
            TableColumn("Instance", value: \.instance) { r in
                Text(hex8(r.instance)).font(.system(.body, design: .monospaced))
            }
            .width(min: 96, ideal: 100)
            TableColumn("Hi", value: \.instanceHi) { r in
                Text(r.instanceHi == 0 ? "" : hex8(r.instanceHi))
                    .font(.system(.body, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            .width(min: 40, ideal: 60)
            TableColumn("Size", value: \.size) { r in
                Text("\(r.size)").monospacedDigit()
            }
            .width(min: 56, ideal: 70)
            TableColumn("Cmp", value: \.compressedSort) { r in
                Image(systemName: r.compressed ? "checkmark" : "minus")
                    .foregroundStyle(r.compressed ? .primary : .quaternary)
            }
            .width(36)
            TableColumn("Decoder", value: \.decoderSort) { r in
                Text(r.decoderLabel).foregroundStyle(.secondary)
            }
            .width(min: 50, ideal: 60)
        }
        .contextMenu(forSelectionType: TGI.self) { tgis in
            contextMenu(for: tgis)
        }
        .sheet(item: $renameTarget) { tgi in
            RenameSheet(session: session, tgi: tgi)
        }
        .confirmationDialog(
            deleteTargets.count == 1 ? "Delete this resource?" : "Delete \(deleteTargets.count) resources?",
            isPresented: Binding(get: { !deleteTargets.isEmpty }, set: { if !$0 { deleteTargets = [] } }),
            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                let targets = deleteTargets
                deleteTargets = []
                Task { await session.delete(targets) }
            }
        } message: {
            Text("Undo brings it back until the package is saved.")
        }
    }

    @ViewBuilder
    private func contextMenu(for tgis: Set<TGI>) -> some View {
        let list = Array(tgis)
        if list.isEmpty {
            Button("New Resource…", action: onNewResource)
        } else {
            if list.count == 1, let tgi = list.first {
                Button("Rename…") { renameTarget = tgi }
                Button("Copy TGI") { copyTGI(tgi) }
                Divider()
                Button("Export Bytes…") {
                    let name = "\(session.typeName(tgi.type))_\(String(format: "%08X_%08X", tgi.group, tgi.instance)).bin"
                    if let url = SavePanels.chooseExport(suggested: name) {
                        Task { await session.exportBytes(tgi, to: url) }
                    }
                }
                Button("Import Bytes…") {
                    if let url = SavePanels.chooseImport() {
                        Task { await session.importBytes(tgi, from: url) }
                    }
                }
                Divider()
            }
            let anyStored = list.contains { tgi in session.rows.first { $0.tgi == tgi }?.compressed == false }
            Button(anyStored ? "Compress on Save" : "Store Uncompressed") {
                Task { await session.setCompressed(list, anyStored) }
            }
            Button("Split to New Package…") { onSplit(list) }
            Divider()
            Button("Delete", role: .destructive) { deleteTargets = list }
        }
    }

    private func copyTGI(_ tgi: TGI) {
        let text = "\(session.typeName(tgi.type)) \(hex8(tgi.type)) \(hex8(tgi.group)) \(hex8(tgi.instance))"
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
}
