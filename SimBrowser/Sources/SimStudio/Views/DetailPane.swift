import SwiftUI
import SimStudioCore
import SimKit

/// The right column: header, a tab strip, and the chosen view of the
/// selected resource. Everything scrollable lives inside an IsolatedPane.
struct DetailPane: View {
    @ObservedObject var session: PackageSession

    enum Tab: String, CaseIterable, Identifiable {
        case decoded = "Decoded"
        case bhav = "Tree"
        case preview = "Preview"
        case hex = "Hex"
        var id: String { rawValue }
    }

    @State private var tab: Tab = .hex
    @State private var lastTGI: TGI?

    var body: some View {
        Group {
            if let d = session.detail {
                VStack(spacing: 0) {
                    header(d)
                    Divider()
                    IsolatedPane {
                        body(for: d)
                    }
                    .id(d.tgi)
                    .clipped()
                }
                .onAppear { pickTab(for: d) }
                .onChange(of: d.tgi) { _ in pickTab(for: d) }
            } else if session.detailLoading {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                VStack(spacing: 10) {
                    Image(systemName: "shippingbox").font(.largeTitle).foregroundStyle(.tertiary)
                    Text("Select a resource").font(.headline)
                    Text("Pick a type on the left, then a row in the middle. Decoded opens an editor "
                         + "when the toolkit has one; Hex always works. Edits stay in memory until you "
                         + "Save, or Save As for a read-only file.")
                        .font(.callout).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: 380)
                }
                .padding()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }

    private func header(_ d: ResourceDetail) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(d.row.name ?? d.row.typeName).font(.title3).fontWeight(.semibold)
                    .lineLimit(1).textSelection(.enabled)
                if d.row.name != nil {
                    Text(d.row.typeName).font(.callout).foregroundStyle(.secondary)
                }
                Text(hex8(d.row.type)).font(.system(.caption, design: .monospaced)).foregroundStyle(.secondary)
                if let what = session.typeDescription(d.row.type) {
                    Text(what).font(.callout).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                Picker("", selection: $tab) {
                    ForEach(tabs(for: d)) { t in Text(t.rawValue).tag(t) }
                }
                .pickerStyle(.segmented)
                .frame(width: 220)
            }
            HStack(spacing: 14) {
                Text(d.tgi.description).font(.system(.callout, design: .monospaced))
                Text("\(d.row.size) bytes").font(.callout).foregroundStyle(.secondary)
                if d.row.compressed {
                    Label("compressed", systemImage: "archivebox").font(.callout).foregroundStyle(.secondary)
                }
                if session.detailLoading { ProgressView().controlSize(.small) }
            }
            .textSelection(.enabled)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    @ViewBuilder
    private func body(for d: ResourceDetail) -> some View {
        switch tab {
        case .decoded:
            if d.row.decodable {
                EditorHost(session: session, detail: d)
            } else {
                HexView(bytes: d.bytes)
            }
        case .bhav:
            if let b = d.bhav { BhavTextView(render: b) } else { HexView(bytes: d.bytes) }
        case .preview:
            PreviewPane(session: session, detail: d)
        case .hex:
            HexView(bytes: d.bytes)
        }
    }

    private func tabs(for d: ResourceDetail) -> [Tab] {
        var t: [Tab] = []
        if d.row.decodable { t.append(.decoded) }
        if d.bhav != nil { t.append(.bhav) }
        if d.row.hasPreview { t.append(.preview) }
        t.append(.hex)
        return t
    }

    /// Land on the richest view the resource supports when the selection
    /// changes, but keep the user's choice while it stays valid.
    private func pickTab(for d: ResourceDetail) {
        let available = tabs(for: d)
        if lastTGI != d.tgi || !available.contains(tab) {
            tab = available.first ?? .hex
        }
        lastTGI = d.tgi
    }
}

/// Holds the working copy of a decoded resource and the Apply/Revert pair.
/// The daemon re-decodes after every Apply, so the draft is reset from the
/// fresh decode rather than trusted as-is.
struct EditorHost: View {
    @ObservedObject var session: PackageSession
    let detail: ResourceDetail
    @State private var draft: JSONValue

    init(session: PackageSession, detail: ResourceDetail) {
        self.session = session
        self.detail = detail
        _draft = State(initialValue: detail.decoded ?? .null)
    }

    private var original: JSONValue { detail.decoded ?? .null }
    private var changed: Bool { draft != original }

    var body: some View {
        VStack(spacing: 0) {
            if let err = detail.decodeError {
                VStack(alignment: .leading, spacing: 8) {
                    Label("This \(detail.row.typeName) could not be decoded", systemImage: "exclamationmark.triangle")
                        .font(.headline)
                    Text(err).textSelection(.enabled)
                    Text("The bytes are still editable in the Hex tab and pass through a save untouched.")
                        .foregroundStyle(.secondary)
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                Divider()
                HexView(bytes: detail.bytes)
            } else {
                if draft.typeName == "BhavRes" {
                    // Owns its own table and scroll areas.
                    BhavEditor(draft: $draft, session: session)
                } else {
                    ScrollView {
                        editor
                            .padding(16)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                Divider()
                HStack {
                    if changed {
                        Text("Unapplied changes").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Revert") { draft = original }
                        .disabled(!changed)
                    Button("Apply") { Task { await session.putDecoded(detail.tgi, draft) } }
                        .keyboardShortcut(.return, modifiers: .command)
                        .disabled(!changed || session.busy)
                        .help("Send the edit to the package in memory (⌘↩). Save writes it to disk.")
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
            }
        }
        .onChange(of: detail.decoded) { new in draft = new ?? .null }
    }

    @ViewBuilder
    private var editor: some View {
        switch draft.typeName {
        case "StrResource": StrEditor(draft: $draft, meta: session.meta)
        case "Objd": ObjdEditor(draft: $draft, meta: session.meta)
        case "Bcon": BconEditor(draft: $draft)
        case "Glob": GlobEditor(draft: $draft)
        case "Objf": ObjfEditor(draft: $draft, meta: session.meta)
        case "Ttab": TtabEditor(draft: $draft, meta: session.meta)
        default: GenericEditor(draft: $draft)
        }
    }
}

/// The decompiler's output for a BHAV: the flat listing and the branch tree.
struct BhavTextView: View {
    let render: BhavRender
    @State private var showTree = true

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                if let name = render.name {
                    Text(name).font(.headline)
                }
                if let f = render.format {
                    Text(String(format: "format 0x%04X", f)).foregroundStyle(.secondary)
                }
                if let n = render.count {
                    Text("\(n) instructions").foregroundStyle(.secondary)
                }
                Spacer()
                Picker("", selection: $showTree) {
                    Text("Tree").tag(true)
                    Text("Flat").tag(false)
                }
                .pickerStyle(.segmented)
                .frame(width: 140)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            Divider()
            if let err = render.error {
                Text(err).padding(16)
            } else {
                ScrollView([.horizontal, .vertical]) {
                    Text((showTree ? render.tree : render.flat) ?? "")
                        .font(.system(.body, design: .monospaced))
                        .textSelection(.enabled)
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }
}
