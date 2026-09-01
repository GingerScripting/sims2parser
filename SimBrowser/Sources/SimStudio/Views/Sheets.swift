import SwiftUI

/// A small hex/decimal entry field for u32 ids. Shows hex, accepts either.
struct IdField: View {
    let label: String
    @Binding var value: UInt32
    @State private var text: String = ""
    @State private var bad = false

    var body: some View {
        LabeledContent(label) {
            TextField("0x00000000", text: $text)
                .font(.system(.body, design: .monospaced))
                .frame(width: 130)
                .onAppear { text = hex8(value) }
                .onChange(of: value) { v in
                    if parseNumber(text, hexByDefault: true).map({ UInt32(truncatingIfNeeded: $0) }) != v { text = hex8(v) }
                }
                .onChange(of: text) { t in
                    if let n = parseNumber(t, hexByDefault: true), n >= 0, n <= Int(UInt32.max) {
                        value = UInt32(n)
                        bad = false
                    } else {
                        bad = !t.isEmpty
                    }
                }
                .foregroundStyle(bad ? .red : .primary)
        }
    }
}

/// Add an empty resource (or one whose bytes come from a file).
struct NewResourceSheet: View {
    @ObservedObject var session: PackageSession
    @Environment(\.dismiss) private var dismiss

    @State private var type: UInt32 = 0x53545223   // STR#
    @State private var group: UInt32 = 0xFFFFFFFF
    @State private var instance: UInt32 = 0x1000
    @State private var instanceHi: UInt32 = 0
    @State private var sourceURL: URL?
    @State private var sizeText = "0"

    private var tgi: TGI { TGI(type: type, group: group, instance: instance, instanceHi: instanceHi) }
    private var exists: Bool { session.rows.contains { $0.tgi == tgi } }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("New Resource").font(.headline)
            Form {
                Picker("Type", selection: $type) {
                    ForEach(session.meta?.knownTypes ?? [], id: \.id) { t in
                        Text("\(t.name)  \(hex8(t.id))").tag(t.id)
                    }
                }
                IdField(label: "Type id", value: $type)
                IdField(label: "Group", value: $group)
                IdField(label: "Instance", value: $instance)
                IdField(label: "Instance (hi)", value: $instanceHi)
                LabeledContent("Bytes") {
                    HStack {
                        if let u = sourceURL {
                            Text(u.lastPathComponent).lineLimit(1)
                            Button("Clear") { sourceURL = nil }
                        } else {
                            TextField("zero bytes", text: $sizeText).frame(width: 80)
                            Text("zeros, or").foregroundStyle(.secondary)
                            Button("From File…") { sourceURL = SavePanels.chooseImport() }
                        }
                    }
                }
            }
            if exists {
                Label("A resource with this TGI already exists.", systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Add") { add() }.keyboardShortcut(.defaultAction).disabled(exists)
            }
        }
        .padding(20)
        .frame(width: 460)
    }

    private func add() {
        var bytes: [UInt8] = []
        if let u = sourceURL, let d = try? Data(contentsOf: u) {
            bytes = [UInt8](d)
        } else if let n = Int(sizeText), n >= 0, n < 16 * 1024 * 1024 {
            bytes = [UInt8](repeating: 0, count: n)
        }
        let target = tgi
        dismiss()
        Task { await session.addResource(target, bytes: bytes) }
    }
}

/// Re-key a resource.
struct RenameSheet: View {
    @ObservedObject var session: PackageSession
    let tgi: TGI
    @Environment(\.dismiss) private var dismiss

    @State private var type: UInt32 = 0
    @State private var group: UInt32 = 0
    @State private var instance: UInt32 = 0
    @State private var instanceHi: UInt32 = 0

    private var newTGI: TGI { TGI(type: type, group: group, instance: instance, instanceHi: instanceHi) }
    private var collides: Bool { newTGI != tgi && session.rows.contains { $0.tgi == newTGI } }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Rename \(session.typeName(tgi.type)) \(tgi.description)").font(.headline)
            Form {
                IdField(label: "Type id", value: $type)
                IdField(label: "Group", value: $group)
                IdField(label: "Instance", value: $instance)
                IdField(label: "Instance (hi)", value: $instanceHi)
            }
            if collides {
                Label("Another resource already has this TGI.", systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Rename") {
                    let target = newTGI
                    dismiss()
                    Task { await session.rename(tgi, to: target) }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(collides || newTGI == tgi)
            }
        }
        .padding(20)
        .frame(width: 420)
        .onAppear {
            type = tgi.type; group = tgi.group; instance = tgi.instance; instanceHi = tgi.instanceHi
        }
    }
}
