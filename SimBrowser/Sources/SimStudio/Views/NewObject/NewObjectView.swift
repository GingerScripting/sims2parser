import SwiftUI
import AppKit
import UniformTypeIdentifiers
import SimStudioCore
import SimKit

/// The front door: pick a base object from the catalog, name it, and get a
/// project. Lives in the window that has no document yet.
struct NewObjectView: View {
    @StateObject private var catalog = CatalogService()
    @Environment(\.openWindow) private var openWindow
    @Environment(\.dismiss) private var dismiss

    enum Step { case choose, name }
    @State private var step: Step = .choose
    @State private var selected: CatalogEntry?
    @State private var search = ""
    @State private var category: Int? = nil          // a function-sort bit, or nil for all
    @State private var source: Source = .all
    @State private var identity = ProjectIdentity(name: "", description: "", price: 0, roomFlags: 0, functionFlags: 0)
    @State private var kind: ProjectKind = .object
    @State private var lookName = ""
    @State private var recolourable: Recolourable?
    @State private var swatches: [String: NSImage] = [:]
    // onAppear fires more than once for a window's root view.
    @MainActor private static var autoOpened = false

    enum Source: String, CaseIterable, Identifiable {
        case all = "All", game = "The Sims 2", downloads = "My Downloads"
        var id: String { rawValue }
    }

    private var shown: [CatalogEntry] {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        return catalog.entries.filter { e in
            (source == .all || (source == .game) == e.isGame)
                && (category == nil || e.functionFlags & category! != 0)
                && (q.isEmpty || e.name.lowercased().contains(q) || e.filename.lowercased().contains(q)
                    || e.description.lowercased().contains(q))
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            switch step {
            case .choose: choose
            case .name: nameIt
            }
            Divider()
            footer
        }
        .frame(minWidth: 860, minHeight: 600)
        .task {
            // Opened only to hand a launch URL on? Then it closes in a beat.
            guard Launch.openURL == nil || Self.autoOpened else { return }
            await catalog.load()
            if let g = Launch.pick, let e = catalog.entries.first(where: { $0.guid == g }) {
                selected = e
                proceed()
            }
        }
        .task {
            guard Launch.snapshot != nil, Launch.openURL == nil else { return }
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            if Task.isCancelled { return }
            Launch.takeSnapshot(title: "")
        }
        .onOpenURL { url in open([url]) }
        .onAppear {
            trace("NewObjectView appeared (autoOpened=\(Self.autoOpened))")
            if let url = Launch.openURL, !Self.autoOpened {
                Self.autoOpened = true
                open([url])
            }
        }
        .onDisappear { catalog.close() }
        .alert("Sim Studio", isPresented: Binding(get: { catalog.errorMessage != nil },
                                                   set: { if !$0 { catalog.errorMessage = nil } })) {
            Button("OK") { catalog.errorMessage = nil }
        } message: { Text(catalog.errorMessage ?? "") }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(step == .choose ? "New Object" : "Name It").font(.title2).fontWeight(.semibold)
                Text(step == .choose
                     ? "Start from one of the game's objects, or from custom content in your Downloads."
                     : "How it appears in the catalog. Everything here can be changed later.")
                    .foregroundStyle(.secondary)
            }
            Spacer()
            HStack(spacing: 6) {
                stepDot(1, on: true)
                Rectangle().fill(.quaternary).frame(width: 24, height: 1)
                stepDot(2, on: step == .name)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
    }

    private func stepDot(_ n: Int, on: Bool) -> some View {
        Text("\(n)").font(.caption).fontWeight(.semibold)
            .frame(width: 22, height: 22)
            .background(on ? Color.accentColor : Color.secondary.opacity(0.25), in: Circle())
            .foregroundStyle(on ? .white : .secondary)
    }

    // MARK: Step 1 — choose a base

    private var choose: some View {
        HStack(spacing: 0) {
            List(selection: $category) {
                Section("Categories") {
                    Label("All Objects", systemImage: "square.grid.2x2").tag(Int?.none)
                    ForEach(catalog.functionSort) { s in
                        Label(s.name, systemImage: Self.symbol(for: s.bit)).tag(Int?.some(s.bit))
                    }
                }
            }
            .listStyle(.sidebar)
            .frame(width: 200)
            Divider()
            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    TextField("Search objects", text: $search).textFieldStyle(.roundedBorder)
                    Picker("", selection: $source) {
                        ForEach(Source.allCases) { s in Text(s.rawValue).tag(s) }
                    }
                    .pickerStyle(.segmented).frame(width: 300).labelsHidden()
                }
                .padding(12)
                Divider()
                if catalog.loading && catalog.entries.isEmpty {
                    VStack(spacing: 10) {
                        ProgressView()
                        Text(catalog.progress.map { "Reading \($0.note)… \($0.done) of \($0.total)" }
                             ?? "Reading the game's catalog…").foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    IsolatedPane { grid }
                }
                Divider()
                HStack {
                    Text("\(shown.count) of \(catalog.entries.count) objects").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Rescan") { Task { await catalog.load(refresh: true) } }
                        .controlSize(.small).disabled(catalog.loading)
                        .help("Read the game's objects and Downloads again")
                }
                .padding(.horizontal, 12).padding(.vertical, 6)
            }
        }
    }

    private var grid: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 14)], spacing: 14) {
                ForEach(shown) { e in
                    SwatchTile(entry: e, image: swatches[e.id], selected: selected?.id == e.id)
                        .onTapGesture(count: 2) { selected = e; proceed() }
                        .onTapGesture { selected = e }
                        .task(id: e.id) {
                            if swatches[e.id] == nil, let d = await catalog.swatch(for: e), let img = NSImage(data: d) {
                                swatches[e.id] = img
                            }
                        }
                }
            }
            .padding(14)
        }
    }

    static func symbol(for functionBit: Int) -> String {
        switch functionBit {
        case 0x001: return "chair"
        case 0x002: return "table.furniture"
        case 0x004: return "refrigerator"
        case 0x008: return "tv"
        case 0x010: return "shower"
        case 0x020: return "photo.artframe"
        case 0x040: return "shippingbox"
        case 0x080: return "lamp.desk"
        case 0x100: return "paintpalette"
        case 0x200: return "trophy"
        case 0x400: return "briefcase"
        default: return "cube"
        }
    }

    // MARK: Step 2 — name it

    private var nameIt: some View {
        IsolatedPane {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let e = selected {
                        HStack(spacing: 14) {
                            SwatchImage(image: swatches[e.id], symbol: Self.symbol(for: e.functionFlags), size: 72)
                            VStack(alignment: .leading, spacing: 3) {
                                Text("Based on \(e.name)").font(.headline)
                                Text("from \(e.sourceName) · §\(e.price)" + (e.tiles > 1 ? " · \(e.tiles) tiles" : ""))
                                    .foregroundStyle(.secondary)
                                if !e.description.isEmpty {
                                    Text(e.description).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                                }
                            }
                        }
                    }
                    SectionCard("Make") {
                        VStack(alignment: .leading, spacing: 8) {
                            Picker("", selection: $kind) {
                                Text("A new object based on this").tag(ProjectKind.object)
                                Text("A new colour for this object").tag(ProjectKind.recolour)
                            }
                            .pickerStyle(.radioGroup).labelsHidden()
                            Text(kindNote).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if kind == .object {
                        IdentityForm(identity: $identity, functionSort: catalog.functionSort, roomSort: catalog.roomSort)
                    } else {
                        SectionCard("Colour option") {
                            VStack(alignment: .leading, spacing: 8) {
                                TextField("Name", text: $lookName, prompt: Text("What to call the colour, for you"))
                                    .textFieldStyle(.roundedBorder)
                                Text("The game shows it as another swatch on the original object. You pick the picture or tint on the next screen.")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                .padding(20)
                .frame(maxWidth: 720, alignment: .leading)
            }
        }
        .task(id: selected?.id) {
            // The base may not have scrolled into view on step 1.
            if let e = selected, swatches[e.id] == nil, let d = await catalog.swatch(for: e), let img = NSImage(data: d) {
                swatches[e.id] = img
            }
            if let e = selected { recolourable = await catalog.recolourable(for: e) }
        }
    }

    private var kindNote: String {
        guard let r = recolourable else { return "Checking which parts of this object can be recoloured…" }
        if r.recolourable.isEmpty {
            return "This object's look is fixed by the game: no part can be recoloured. A new object based on it still works."
        }
        let parts = r.recolourable.joined(separator: ", ")
        return "Parts a colour can change: \(parts)." + (r.gameOptions > 0 ? " The game has \(r.gameOptions) colour option\(r.gameOptions == 1 ? "" : "s") already." : "")
    }

    private var canCreate: Bool {
        if catalog.busy { return false }
        if kind == .recolour { return recolourable.map { !$0.recolourable.isEmpty } ?? false }
        return !identity.name.trimmingCharacters(in: .whitespaces).isEmpty
    }

    // MARK: Footer

    private var footer: some View {
        HStack {
            if step == .choose {
                Button("Open Project…") { openExisting() }
                Button("Open Package (Advanced)…") {
                    let urls = SavePanels.chooseOpen()
                    if !urls.isEmpty { open(urls) }
                }
                .foregroundStyle(.secondary)
            } else {
                Button("Back") { step = .choose }
            }
            Spacer()
            if catalog.busy { ProgressView().controlSize(.small) }
            if step == .choose {
                Button("Continue") { proceed() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(selected == nil)
            } else {
                Button("Create…") { create() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!canCreate)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }

    private func proceed() {
        guard let e = selected else { return }
        identity = ProjectIdentity(name: e.name, description: e.description, price: e.price,
                                   roomFlags: e.roomFlags, functionFlags: e.functionFlags)
        lookName = ""
        recolourable = nil
        step = .name
    }

    private func create() {
        guard let e = selected else { return }
        let look = lookName.trimmingCharacters(in: .whitespaces)
        let suggested = kind == .recolour ? "\(e.name) \(look.isEmpty ? "Recolour" : look)" : identity.name
        guard let url = SavePanels.chooseProject(named: suggested) else { return }
        Task {
            if await catalog.createProject(at: url, base: e, identity: identity, kind: kind,
                                           lookName: kind == .recolour ? (look.isEmpty ? "Colour 1" : look) : nil) {
                NSDocumentController.shared.noteNewRecentDocumentURL(url)
                catalog.close()
                open([url])
            }
        }
    }

    private func openExisting() {
        if let url = SavePanels.chooseProjectToOpen() { open([url]) }
    }

    /// Open windows and close this one. Deferred by a beat: calling
    /// openWindow inside the first onAppear (or the launch-time open-document
    /// event) races window creation and yields two windows for one URL.
    private func open(_ urls: [URL]) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            for url in urls { openWindow(value: url) }
            dismiss()
        }
    }
}

/// One object in the grid.
struct SwatchTile: View {
    let entry: CatalogEntry
    let image: NSImage?
    let selected: Bool

    var body: some View {
        VStack(spacing: 6) {
            SwatchImage(image: image, symbol: NewObjectView.symbol(for: entry.functionFlags), size: 120)
                .overlay(alignment: .topTrailing) {
                    if entry.tiles > 1 {
                        Text("\(entry.tiles) tiles").font(.caption2)
                            .padding(.horizontal, 5).padding(.vertical, 2)
                            .background(.thinMaterial, in: Capsule()).padding(5)
                    }
                }
            Text(entry.name).font(.callout).lineLimit(2).multilineTextAlignment(.center)
                .frame(height: 34, alignment: .top)
            Text("§\(entry.price)").font(.caption).foregroundStyle(.secondary)
        }
        .padding(8)
        .frame(maxWidth: .infinity)
        .background(selected ? Color.accentColor.opacity(0.16) : .clear, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(selected ? Color.accentColor : .clear, lineWidth: 2))
        .contentShape(Rectangle())
        .help(entry.description.isEmpty ? entry.name : entry.description)
    }
}

/// A swatch, or a category glyph while there is none.
struct SwatchImage: View {
    let image: NSImage?
    let symbol: String
    let size: CGFloat

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size / 12).fill(.quaternary.opacity(0.5))
            if let image {
                Image(nsImage: image).resizable().interpolation(.medium)
                    .aspectRatio(contentMode: .fill)
                    .frame(width: size, height: size)
                    .clipShape(RoundedRectangle(cornerRadius: size / 12))
            } else {
                Image(systemName: symbol).font(.system(size: size * 0.38)).foregroundStyle(.tertiary)
            }
        }
        .frame(width: size, height: size)
    }
}

/// Name, description, price and the two category groups. Shared by the
/// wizard and the project window's Name & Catalog page.
struct IdentityForm: View {
    @Binding var identity: ProjectIdentity
    let functionSort: [SortBit]
    let roomSort: [SortBit]
    /// Called after a field is committed (text on submit or focus loss,
    /// toggles at once); the project window uses it to apply the change.
    var onCommit: () -> Void = {}
    @FocusState private var focused: Field?
    enum Field { case name, description, price }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionCard("Name & description") {
                VStack(alignment: .leading, spacing: 10) {
                    TextField("Name", text: $identity.name, prompt: Text("What the catalog calls it"))
                        .textFieldStyle(.roundedBorder).focused($focused, equals: .name)
                        .onSubmit(onCommit)
                    TextField("Description", text: $identity.description, prompt: Text("The catalog blurb"), axis: .vertical)
                        .lineLimit(3...6)
                        .textFieldStyle(.roundedBorder).focused($focused, equals: .description)
                        .onSubmit(onCommit)
                    HStack {
                        Text("Price")
                        TextField("", value: $identity.price, format: .number)
                            .textFieldStyle(.roundedBorder).frame(width: 90)
                            .focused($focused, equals: .price)
                            .onSubmit(onCommit)
                        Text("Simoleons").foregroundStyle(.secondary)
                    }
                }
            }
            SectionCard("Buy Mode category") {
                flags($identity.functionFlags, bits: functionSort)
            }
            SectionCard("Room") {
                flags($identity.roomFlags, bits: roomSort)
            }
        }
        .onChange(of: focused) { _ in onCommit() }
    }

    private func flags(_ value: Binding<Int>, bits: [SortBit]) -> some View {
        FlowLayout(spacing: 10) {
            ForEach(bits) { b in
                Toggle(b.name, isOn: Binding(
                    get: { value.wrappedValue & b.bit != 0 },
                    set: { on in
                        value.wrappedValue = on ? value.wrappedValue | b.bit : value.wrappedValue & ~b.bit
                        onCommit()
                    }))
                .toggleStyle(.checkbox)
            }
        }
    }
}
