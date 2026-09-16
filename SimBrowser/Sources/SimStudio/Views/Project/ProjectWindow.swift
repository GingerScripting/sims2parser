import SwiftUI
import AppKit
import SimStudioCore
import SimKit

/// An object project: a sidebar of the object's parts, the chosen part in
/// the middle, and Export / Install in the toolbar. The raw resources sit
/// behind the Advanced door.
struct ProjectWindow: View {
    @ObservedObject var session: PackageSession

    enum Page: Hashable { case identity, looks, interactions, resources }
    @State private var page: Page = Launch.page == "resources" ? .resources : .identity
    @State private var filter: TreeFilter = .all
    @State private var search = ""
    @State private var showNewResource = false
    @State private var tool: Tool?
    @State private var notice: String?
    @State private var askReplace = false
    @State private var swatch: NSImage?

    var body: some View {
        VStack(spacing: 0) {
            if let w = session.project?.warnings, !w.isEmpty {
                Banner(w.joined(separator: " "), systemImage: "exclamationmark.triangle", tint: .yellow)
            }
            HStack(spacing: 0) {
                // A sidebar-styled List under a toolbar gets the macOS 26
                // glass treatment and lays out off-window; isolate it.
                IsolatedPane { sidebar }.frame(width: 210)
                Divider()
                content.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            Divider()
            statusBar
        }
        .frame(minWidth: 1180, minHeight: 640)
        .navigationTitle(session.title)
        .navigationSubtitle(session.project?.base.name ?? "")
        .navigationDocument(session.currentURL)
        .background(WindowEditedMarker(isEdited: session.isDirty))
        .focusedSceneValue(\.session, session)
        .task {
            if let d = await session.baseSwatch() {
                swatch = NSImage(data: d)
                trace("project window: swatch \(swatch.map { "\(Int($0.size.width))x\(Int($0.size.height))" } ?? "undecodable") from \(d.count) bytes")
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { Task { await session.undo() } } label: { Label(session.undoLabel, systemImage: "arrow.uturn.backward") }
                    .disabled(!session.canUndo).help(session.undoLabel)
                Button { Task { await session.redo() } } label: { Label(session.redoLabel, systemImage: "arrow.uturn.forward") }
                    .disabled(!session.canRedo).help(session.redoLabel)
                Button { Task { await session.save() } } label: { Label("Save", systemImage: "square.and.arrow.down") }
                    .disabled(!session.isDirty).help("Save the project")
                Button { ProjectActions.export(session) } label: { Label("Export Package…", systemImage: "shippingbox.and.arrow.backward") }
                    .help("Write the .package the game loads")
                Button { install(replace: false) } label: { Label("Install", systemImage: "arrow.down.to.line") }
                    .help("Put the package into the game's Downloads folder")
            }
        }
        .alert("Sim Studio", isPresented: Binding(get: { session.errorMessage != nil },
                                                   set: { if !$0 { session.errorMessage = nil } })) {
            Button("OK") { session.errorMessage = nil }
        } message: { Text(session.errorMessage ?? "") }
        .alert("Installed", isPresented: Binding(get: { notice != nil }, set: { if !$0 { notice = nil } })) {
            Button("OK") { notice = nil }
        } message: { Text(notice ?? "") }
        .alert("Replace the installed copy?", isPresented: $askReplace) {
            Button("Replace") { install(replace: true) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("A package with this name is already in the game's Downloads folder.")
        }
        .sheet(isPresented: $showNewResource) { NewResourceSheet(session: session) }
        .sheet(item: $tool) { tool in
            switch tool {
            case .clone: CloneSheet(session: session)
            case .merge: MergeSheet(session: session)
            case .split(let tgis): SplitSheet(session: session, tgis: tgis)
            case .doctor: DoctorSheet(session: session)
            }
        }
    }

    private var sidebar: some View {
        List(selection: $page) {
            Section("Object") {
                Label("Name & Catalog", systemImage: "tag").tag(Page.identity)
            }
            Section("Coming later") {
                Label("Looks", systemImage: "paintbrush").tag(Page.looks).foregroundStyle(.tertiary)
                Label("Interactions", systemImage: "hand.tap").tag(Page.interactions).foregroundStyle(.tertiary)
            }
            Section("Advanced") {
                Label("Resources", systemImage: "list.bullet.rectangle").tag(Page.resources)
            }
        }
        .listStyle(.sidebar)
    }

    @ViewBuilder
    private var content: some View {
        switch page {
        case .identity:
            IdentityPage(session: session, swatch: swatch)
        case .looks:
            placeholder("Looks", "Recolors and new textures come in the next round.")
        case .interactions:
            placeholder("Interactions", "Pie-menu options built from blocks come after that.")
        case .resources:
            VStack(spacing: 0) {
                HStack {
                    TextField("Filter by name, type, or hex id", text: $search).textFieldStyle(.roundedBorder)
                    Button { showNewResource = true } label: { Label("New Resource", systemImage: "plus") }
                    Menu {
                        Button("Merge Package Into This…") { tool = .merge }
                        Button("Split Selection to New Package…") { tool = .split(Array(session.selectedTGIs)) }
                            .disabled(session.selectedTGIs.isEmpty)
                    } label: { Label("Tools", systemImage: "wrench.and.screwdriver") }
                }
                .padding(10)
                Divider()
                // Three scrollable panes beside a sidebar: isolated as one,
                // or the sidebar slides off-window (macOS 26).
                IsolatedPane {
                    ResourceBrowser(session: session, filter: $filter, search: search,
                                    onNewResource: { showNewResource = true },
                                    onSplit: { tool = .split($0) }, compact: true)
                }
                .clipped()
            }
        }
    }

    private func placeholder(_ title: String, _ text: String) -> some View {
        VStack(spacing: 8) {
            Text(title).font(.title2).fontWeight(.semibold)
            Text(text).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var statusBar: some View {
        HStack(spacing: 12) {
            if let p = session.project {
                Text("Based on \(p.base.name)" + (p.base.isGame ? " from The Sims 2" : ""))
                if let e = p.exported { Text("Exported to \(URL(fileURLWithPath: e).lastPathComponent)") }
                if p.installed != nil { Text("Installed") }
            }
            Spacer()
            Text("\(session.rows.count) resources")
            if session.busy { ProgressView().controlSize(.small) }
        }
        .font(.caption).foregroundStyle(.secondary)
        .padding(.horizontal, 12).padding(.vertical, 4)
    }

    private func install(replace: Bool) {
        Task {
            switch await session.projectInstall(replace: replace) {
            case .success(let url):
                notice = "\(url.lastPathComponent) is in the game's Downloads folder. It appears in the catalog the next time the game starts."
            case .failure(let f):
                if case .remote(let e) = f, e.code == "exists" { askReplace = true }
                else { session.errorMessage = f.localizedDescription }
            case nil: break
            }
        }
    }
}

/// Panels for the project window and the menu.
@MainActor
enum ProjectActions {
    static func export(_ session: PackageSession) {
        guard let p = session.project else { return }
        let suggested = URL(fileURLWithPath: p.exported ?? "").lastPathComponent
        guard let url = SavePanels.chooseExport(suggested: suggested.isEmpty ? p.name + ".package" : suggested) else { return }
        Task { _ = await session.projectExport(to: url) }
    }
}

/// The Name & Catalog page: the identity form, applied as one undo step per
/// committed change.
struct IdentityPage: View {
    @ObservedObject var session: PackageSession
    let swatch: NSImage?
    @State private var draft = ProjectIdentity(name: "", description: "", price: 0, roomFlags: 0, functionFlags: 0)

    private var functionSort: [SortBit] { session.meta?.functionSort ?? [] }
    private var roomSort: [SortBit] { session.meta?.roomSort ?? [] }

    var body: some View {
        IsolatedPane {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let p = session.project {
                        HStack(spacing: 14) {
                            SwatchImage(image: swatch, symbol: NewObjectView.symbol(for: p.identity.functionFlags), size: 88)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(p.identity.name).font(.title2).fontWeight(.semibold)
                                Text("Based on \(p.base.name) from \(p.base.isGame ? "The Sims 2" : URL(fileURLWithPath: p.base.source).lastPathComponent)")
                                    .foregroundStyle(.secondary)
                                Text(String(format: "GUID 0x%08X", p.identity.guid))
                                    .font(.system(.caption, design: .monospaced)).foregroundStyle(.tertiary)
                            }
                        }
                    }
                    IdentityForm(identity: $draft, functionSort: functionSort, roomSort: roomSort, onCommit: commit)
                    Text("Changes apply as you make them and can be undone. Export writes the package; Install puts it where the game looks.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .padding(20)
                .frame(maxWidth: 760, alignment: .leading)
            }
        }
        .onAppear { sync() }
        .onChange(of: session.project?.identity) { _ in sync() }
    }

    private func sync() {
        if let i = session.project?.identity, i != draft { draft = i }
    }

    private func commit() {
        guard let current = session.project?.identity else { return }
        var wanted = draft
        wanted.guid = current.guid
        if wanted.name.trimmingCharacters(in: .whitespaces).isEmpty { wanted.name = current.name }
        guard wanted != current else { return }
        Task { _ = await session.projectSet(wanted) }
    }
}
