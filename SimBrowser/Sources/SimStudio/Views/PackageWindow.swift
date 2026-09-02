import SwiftUI
import SimStudioCore
import AppKit
import SimKit

/// Owns the session for one window. Split from `PackageWindow` so the
/// `@StateObject` is created exactly once per URL.
struct PackageRoot: View {
    @StateObject private var session: PackageSession
    @Environment(\.openWindow) private var openWindow

    init(url: URL) {
        _session = StateObject(wrappedValue: PackageSession(url: url))
        trace("PackageRoot init \(url.absoluteString)")
        if ProcessInfo.processInfo.environment["SIMSTUDIO_TRACE_STACK"] != nil {
            trace(Thread.callStackSymbols.prefix(40).joined(separator: "\n"))
        }
    }

    var body: some View {
        // No `.onDisappear { close }`: the root view appears, disappears, and
        // reappears while the window is first shown, which would restart the
        // daemon. The session shuts it down when the window's state object
        // is released instead.
        PackageWindow(session: session)
            .task { await session.start() }
            .onOpenURL { url in
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { openWindow(value: url) }
            }
    }
}

/// Which slice of the index the table shows, driven by the type tree.
enum TreeFilter: Hashable {
    case overview       // all rows, and the detail pane back on the overview
    case all
    case type(UInt32)
    case typeGroup(UInt32, UInt32)
}

struct PackageWindow: View {
    @ObservedObject var session: PackageSession
    @State private var filter: TreeFilter = .all
    @State private var search = ""
    @State private var showNewResource = false
    @State private var tool: Tool?

    enum Mode: String, CaseIterable, Identifiable {
        case resources = "Resources"
        case sims = "Sims"
        var id: String { rawValue }
    }
    @State private var mode: Mode = .resources

    var body: some View {
        Group {
            switch session.phase {
            case .opening:
                ProgressView("Opening \(session.title)…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let message):
                VStack(spacing: 12) {
                    Image(systemName: "exclamationmark.triangle").font(.largeTitle)
                    Text(message).multilineTextAlignment(.center).textSelection(.enabled)
                }
                .padding(40)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .ready:
                content
            }
        }
        .frame(minWidth: 1180, minHeight: 640)
        .navigationTitle(session.title)
        .navigationSubtitle(folderLabel)
        .navigationDocument(session.currentURL)
        .background(WindowEditedMarker(isEdited: session.isDirty))
        .focusedSceneValue(\.session, session)
        .alert("Sim Studio", isPresented: Binding(
            get: { session.errorMessage != nil },
            set: { if !$0 { session.errorMessage = nil } })) {
            Button("OK") { session.errorMessage = nil }
        } message: {
            Text(session.errorMessage ?? "")
        }
        .sheet(isPresented: $showNewResource) {
            NewResourceSheet(session: session)
        }
        .sheet(item: $tool) { tool in
            switch tool {
            case .clone: CloneSheet(session: session)
            case .merge: MergeSheet(session: session)
            case .split(let tgis): SplitSheet(session: session, tgis: tgis)
            case .doctor: DoctorSheet(session: session)
            }
        }
    }

    private var content: some View {
        VStack(spacing: 0) {
            if session.isReadonly {
                Banner("Read-only: \(session.summary?.readonlyReason ?? ""). Edits stay in memory; use "
                       + (session.isHood ? "Copy Hood to write a copy of the whole neighborhood elsewhere."
                                         : "Save As to write a copy elsewhere."),
                       systemImage: "lock.fill", tint: .yellow)
            }
            if let check = session.hoodMeta?.check, !check.healthy {
                // Shown in both modes: a hood that will not load is the one
                // thing to know before browsing it.
                Banner(check.summary, systemImage: "exclamationmark.triangle.fill", tint: .red)
            }
            // Deliberately an HStack, not NavigationSplitView — see Sim
            // Browser's ContentView for the macOS 26 measurement behind that.
            if mode == .sims && session.isHood {
                SimsPane(session: session)
            } else {
                HStack(spacing: 0) {
                    TypeTree(rows: session.rows, selection: $filter,
                             describe: { session.typeDescription($0) })
                        .frame(width: 240)
                    Divider()
                    ResourceTable(session: session, rows: filteredRows,
                                  onNewResource: { showNewResource = true },
                                  onSplit: { tool = .split($0) })
                        .frame(minWidth: 460)
                    Divider()
                    DetailPane(session: session, reveal: { tgi in
                        filter = .all
                        session.selectedTGIs = [tgi]
                    })
                        .frame(minWidth: 440, maxWidth: .infinity)
                }
            }
            Divider()
            statusBar
        }
        .toolbar {
            // Nothing in the navigation placement: the title belongs at the
            // leading edge, not squeezed after a run of buttons.
            ToolbarItemGroup(placement: .primaryAction) {
                if session.isHood {
                    Picker("Mode", selection: $mode) {
                        ForEach(Mode.allCases) { m in Text(m.rawValue).tag(m) }
                    }
                    .pickerStyle(.segmented)
                    .help("A neighborhood package: browse its resources, or edit its sims")
                }
                Button { Task { await session.undo() } } label: { Label(session.undoLabel, systemImage: "arrow.uturn.backward") }
                    .disabled(!session.canUndo)
                    .help(session.undoLabel)
                Button { Task { await session.redo() } } label: { Label(session.redoLabel, systemImage: "arrow.uturn.forward") }
                    .disabled(!session.canRedo)
                    .help(session.redoLabel)
                Menu {
                    Button("Clone Object…") { tool = .clone }
                    Button("Merge Package Into This…") { tool = .merge }
                    Button("Split Selection to New Package…") { tool = .split(Array(session.selectedTGIs)) }
                        .disabled(session.selectedTGIs.isEmpty)
                    Divider()
                    Button("Check the Game Folder…") { tool = .doctor }
                } label: {
                    Label("Tools", systemImage: "wrench.and.screwdriver")
                }
                .help("Object Workshop, merge, split, and the doctor")
                Button { showNewResource = true } label: { Label("New Resource", systemImage: "plus") }
                    .help("Add an empty resource")
                Menu {
                    Button("Compress All") { Task { await session.setAllCompressed(true) } }
                    Button("Store All Uncompressed") { Task { await session.setAllCompressed(false) } }
                } label: {
                    Label("Compression", systemImage: "archivebox")
                }
                .help("Applies at the next save")
                Button { Task { await session.save() } } label: { Label("Save", systemImage: "square.and.arrow.down") }
                    .disabled(session.isReadonly || !session.isDirty)
                    .help(session.isReadonly ? "Read-only — use Save As" : "Save")
                Button { SavePanels.saveAs(session) } label: { Label("Save As…", systemImage: "square.and.arrow.down.on.square") }
                    .help("Write a copy elsewhere")
                if session.isHood {
                    Button { SavePanels.hoodSaveAs(session) } label: { Label("Copy Hood…", systemImage: "folder.badge.plus") }
                        .help("Copy the whole neighborhood folder elsewhere with the edits applied")
                }
            }
        }
        .searchable(text: $search, placement: .toolbar, prompt: "Filter by name, type, or hex id")
        .onChange(of: filter) { f in
            if f == .overview { session.selectedTGIs = [] }
        }
    }

    /// The file's folder, with the home directory abbreviated — so a scratch
    /// copy and the real file in Downloads are told apart at a glance.
    private var folderLabel: String {
        let folder = session.currentURL.deletingLastPathComponent().path
        let home = NSHomeDirectory()
        return folder.hasPrefix(home) ? "~" + folder.dropFirst(home.count) : folder
    }

    private var statusBar: some View {
        HStack(spacing: 12) {
            Text(session.summary?.version ?? "")
            Spacer()
            Text("\(filteredRows.count) of \(session.rows.count) resources")
            Text("\(session.summary?.compressedCount ?? 0) compressed")
            if session.busy { ProgressView().controlSize(.small) }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 12)
        .padding(.vertical, 4)
    }

    /// The tree filter, then the search text against the row's name, type
    /// name, and the hex forms of its ids. Sorting is the table's business.
    private var filteredRows: [ResourceRow] {
        let base: [ResourceRow]
        switch filter {
        case .all, .overview: base = session.rows
        case .type(let t): base = session.rows.filter { $0.type == t }
        case .typeGroup(let t, let g): base = session.rows.filter { $0.type == t && $0.group == g }
        }
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return base }
        let needle = q.hasPrefix("0x") ? String(q.dropFirst(2)) : q
        return base.filter { r in
            (r.name?.lowercased().contains(q) ?? false)
                || r.typeName.lowercased().contains(q)
                || String(format: "%08x", r.group).contains(needle)
                || String(format: "%08x", r.instance).contains(needle)
        }
    }
}
