import SwiftUI
import AppKit

/// Which tool sheet is up.
enum Tool: Identifiable {
    case clone
    case merge
    case split([TGI])
    case doctor

    var id: String {
        switch self {
        case .clone: return "clone"
        case .merge: return "merge"
        case .split: return "split"
        case .doctor: return "doctor"
        }
    }
}

/// A progress line for the long-running tools.
struct ProgressLine: View {
    let progress: Progress?
    let fallback: String

    var body: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            if let p = progress, p.total > 0 {
                Text("\(p.note.isEmpty ? p.op : p.note) — \(p.done) of \(p.total)")
            } else {
                Text(fallback)
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }
}

// MARK: - Object Workshop

/// SimPE's Object Workshop: give an object a new identity, in place. The
/// usual flow is open a game or donor package, clone, then Save As into
/// Downloads.
struct CloneSheet: View {
    @ObservedObject var session: PackageSession
    @Environment(\.dismiss) private var dismiss

    @State private var objects: [ObjectInfo] = []
    @State private var selected: ObjectInfo?
    @State private var name = ""
    @State private var descriptionText = ""
    @State private var priceText = ""
    @State private var guid: UInt32 = 0
    @State private var autoGuid = true
    @State private var aggressive = false
    @State private var scanning = false
    @State private var scan: ScanResult?
    @State private var result: CloneResult?
    @State private var running = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Clone Object").font(.headline)
            if objects.isEmpty {
                Text("This package holds no object definitions (OBJD).").foregroundStyle(.secondary)
            } else if let result {
                report(result)
            } else {
                form
            }
            HStack {
                if scanning || running {
                    ProgressLine(progress: session.progress, fallback: running ? "Cloning…" : "Scanning Downloads…")
                }
                Spacer()
                if result != nil {
                    Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
                } else {
                    Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                    Button("Clone") { run() }
                        .keyboardShortcut(.defaultAction)
                        .disabled(selected == nil || running || guid == 0 || guid == selected?.guid)
                }
            }
        }
        .padding(20)
        .frame(width: 560)
        .task {
            objects = await session.objects()
            selected = objects.first
            await refreshGuid()
        }
        .onChange(of: name) { _ in if autoGuid { Task { await refreshGuid() } } }
        .onChange(of: selected) { _ in if autoGuid { Task { await refreshGuid() } } }
    }

    private var form: some View {
        Form {
            Picker("Object", selection: $selected) {
                ForEach(objects) { o in
                    Text("\(o.name.isEmpty ? o.filename : o.name)  \(hex8(o.guid))  §\(o.price)").tag(Optional(o))
                }
            }
            TextField("New name", text: $name, prompt: Text(selected?.name ?? ""))
            TextField("Catalog description", text: $descriptionText, prompt: Text("unchanged"))
            TextField("Price", text: $priceText, prompt: Text(selected.map { "\($0.price)" } ?? ""))
                .frame(width: 120)
            LabeledContent("New GUID") {
                HStack {
                    IdOnlyField(value: $guid).disabled(autoGuid)
                    Toggle("Derive from name", isOn: $autoGuid).toggleStyle(.checkbox)
                }
            }
            Toggle("Aggressive: also patch GUID literals at unconfirmed operand slots", isOn: $aggressive)
            HStack {
                Button("Check Downloads for Collisions") { checkCollisions() }
                    .disabled(scanning || guid == 0)
                if let scan {
                    if let hits = scan.collisions[String(guid)], !hits.isEmpty {
                        Label("\(hex8(guid)) is already used by \(hits.joined(separator: ", "))", systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.red)
                    } else {
                        Label("No collision in \(scan.packages) packages (\(scan.guids) GUIDs)", systemImage: "checkmark.circle")
                            .foregroundStyle(.green)
                    }
                }
            }
            Text("The GUID is derived from the name so a rebuild keeps it. It is not a registry allocation: register a range before distributing.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func report(_ r: CloneResult) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Cloned \(hex8(r.sourceGuid)) → \(hex8(r.newGuid)); \(r.changed) of \(r.resourceCount) resources changed.", systemImage: "checkmark.circle")
            if !r.patches.isEmpty {
                Text("GUID literals in behaviour trees").font(.subheadline).fontWeight(.semibold)
                ScrollView {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(r.patches) { p in
                            HStack(alignment: .top, spacing: 6) {
                                Image(systemName: p.applied ? "checkmark" : "exclamationmark.triangle")
                                    .foregroundStyle(p.applied ? .green : .orange)
                                Text("BHAV \(String(format: "0x%04X", p.instance)) “\(p.bhavName)” [\(p.instrIndex)] \(p.opcodeName) +\(p.operandOffset)"
                                     + (p.applied ? "" : " — left alone (unconfirmed layout)"))
                                    .font(.caption)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 160)
            }
            ForEach(r.warnings, id: \.self) { w in
                Label(w, systemImage: "info.circle").font(.caption)
            }
            Text("This is one undo step. Use Save As to write the clone into Downloads.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func refreshGuid() async {
        let seed = name.isEmpty ? (selected?.name ?? "") : name
        guard !seed.isEmpty else { return }
        if let g = await session.deriveGuid(seed: seed + ":" + (selected.map { hex8($0.guid) } ?? "")) {
            guid = g
        }
    }

    private func checkCollisions() {
        scanning = true
        Task {
            scan = await session.scanGuids([guid])
            scanning = false
        }
    }

    private func run() {
        guard let o = selected else { return }
        running = true
        Task {
            result = await session.clone(guid: guid, selectGuid: o.guid,
                                         name: name.isEmpty ? nil : name,
                                         description: descriptionText.isEmpty ? nil : descriptionText,
                                         price: Int(priceText), aggressive: aggressive)
            running = false
        }
    }
}

/// `IdField` without the label, for use inside `LabeledContent`.
struct IdOnlyField: View {
    @Binding var value: UInt32
    @State private var text = ""

    var body: some View {
        TextField("0x00000000", text: $text)
            .font(.system(.body, design: .monospaced))
            .frame(width: 130)
            .onAppear { text = hex8(value) }
            .onChange(of: value) { v in
                if parseNumber(text, hexByDefault: true).map({ UInt32(truncatingIfNeeded: $0) }) != v { text = hex8(v) }
            }
            .onChange(of: text) { t in
                if let n = parseNumber(t, hexByDefault: true), n >= 0, n <= Int(UInt32.max), UInt32(n) != value {
                    value = UInt32(n)
                }
            }
    }
}

// MARK: - Merge

struct MergeSheet: View {
    @ObservedObject var session: PackageSession
    @Environment(\.dismiss) private var dismiss
    @State private var source: URL?
    @State private var replace = false
    @State private var result: MergeResult?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Merge Package Into \(session.title)").font(.headline)
            if let r = result {
                Label("\(r.added) added, \(r.replaced) replaced, \(r.skipped) skipped (same TGI already here). One undo step.",
                      systemImage: "checkmark.circle")
            } else {
                HStack {
                    Text(source?.lastPathComponent ?? "No package chosen").lineLimit(1)
                    Spacer()
                    Button("Choose…") { source = SavePanels.chooseOpen().first }
                }
                Picker("When a TGI already exists here", selection: $replace) {
                    Text("Keep this package's copy").tag(false)
                    Text("Replace it with the merged one").tag(true)
                }
                .pickerStyle(.radioGroup)
            }
            HStack {
                Spacer()
                if result != nil {
                    Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
                } else {
                    Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                    Button("Merge") {
                        guard let u = source else { return }
                        Task { result = await session.merge(u, replace: replace) }
                    }
                    .keyboardShortcut(.defaultAction)
                    .disabled(source == nil)
                }
            }
        }
        .padding(20)
        .frame(width: 480)
    }
}

// MARK: - Split

struct SplitSheet: View {
    @ObservedObject var session: PackageSession
    let tgis: [TGI]
    @Environment(\.dismiss) private var dismiss
    @State private var remove = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Split \(tgis.count) resource\(tgis.count == 1 ? "" : "s") to a New Package").font(.headline)
            Toggle("Remove them from \(session.title) afterwards (undoable)", isOn: $remove)
            Text("The new package keeps each resource's compression choice.").font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Choose Destination…") {
                    let panel = NSSavePanel()
                    panel.allowedContentTypes = [SavePanels.packageType]
                    panel.nameFieldStringValue = "split.package"
                    panel.canCreateDirectories = true
                    guard panel.runModal() == .OK, let url = panel.url else { return }
                    dismiss()
                    Task { _ = await session.split(tgis, to: url, remove: remove) }
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 460)
    }
}

// MARK: - Doctor

/// s2doctor's report, in a window instead of a terminal.
struct DoctorSheet: View {
    @ObservedObject var session: PackageSession
    @Environment(\.dismiss) private var dismiss
    @State private var downloadsOnly = true
    @State private var hashFiles = false
    @State private var running = false
    @State private var result: DoctorResult?
    @State private var showInfo = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Check the Game Folder").font(.headline)
                Spacer()
                Toggle("Downloads only", isOn: $downloadsOnly).disabled(running)
                Toggle("Find duplicate files (hashes every package)", isOn: $hashFiles).disabled(running)
                Button(result == nil ? "Run" : "Run Again") { run() }.disabled(running)
            }
            if running {
                ProgressLine(progress: session.progress, fallback: "Scanning…")
            }
            if let r = result {
                let shown = r.findings.filter { showInfo || $0.severity != "info" }
                HStack {
                    Text("\(r.packages) packages under \(r.root)").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Toggle("Show info", isOn: $showInfo).font(.caption)
                }
                if shown.isEmpty {
                    Text("Nothing to report.").foregroundStyle(.secondary)
                }
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(shown) { f in
                            VStack(alignment: .leading, spacing: 3) {
                                HStack(spacing: 6) {
                                    Image(systemName: icon(f.severity)).foregroundStyle(color(f.severity))
                                    Text(f.title).fontWeight(.semibold)
                                    Text(f.code).font(.caption).foregroundStyle(.tertiary)
                                }
                                ForEach(f.detail, id: \.self) { line in
                                    Text(line).font(.caption).foregroundStyle(.secondary).padding(.leading, 22)
                                }
                                if let fix = f.fix {
                                    Text("Fix: \(fix)").font(.caption).padding(.leading, 22)
                                }
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                }
            }
            HStack {
                Spacer()
                Button("Close") { dismiss() }.keyboardShortcut(.cancelAction)
            }
        }
        .padding(20)
        .frame(width: 720, height: 520)
        .onAppear { if result == nil { run() } }
    }

    private func icon(_ s: String) -> String {
        s == "critical" ? "xmark.octagon.fill" : (s == "warning" ? "exclamationmark.triangle.fill" : "info.circle")
    }

    private func color(_ s: String) -> Color {
        s == "critical" ? .red : (s == "warning" ? .orange : .secondary)
    }

    private func run() {
        running = true
        Task {
            result = await session.doctor(downloadsOnly: downloadsOnly, hashFiles: hashFiles)
            running = false
        }
    }
}
