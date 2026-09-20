import SwiftUI
import AppKit
import UniformTypeIdentifiers
import SimStudioCore
import SimKit

/// The Looks page: the object's colour options. Each look colours the parts
/// the game lets a recolour touch, with a picture or a tint; the rest are
/// listed as fixed. Every change is one undo step, applied as it is made.
struct LooksPage: View {
    @ObservedObject var session: PackageSession
    @State private var selected: String?
    @State private var newName = ""
    @State private var confirmRemove = false

    private var inventory: LooksInventory? { session.looks }
    private var looks: LooksInfo { session.project?.looks ?? LooksInfo(items: [], keepGameOptions: true) }
    private var current: Look? { looks.items.first { $0.id == selected } ?? looks.items.first }

    var body: some View {
        HStack(spacing: 0) {
            IsolatedPane { lookList }.frame(width: 250)
            Divider()
            IsolatedPane { editor }.frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .task { if session.looks == nil { _ = await session.loadLooks() } }
        .onChange(of: session.editCount) { _ in
            // The inventory (subsets, game colours) does not change with edits;
            // only the looks do, and those arrive with every summary.
            if selected == nil { selected = looks.items.first?.id }
        }
        .onAppear { selected = looks.items.first?.id }
        .alert("Remove this look?", isPresented: $confirmRemove) {
            Button("Remove", role: .destructive) {
                if let id = current?.id { Task { _ = await session.lookRemove(id: id); selected = nil } }
            }
            Button("Cancel", role: .cancel) {}
        } message: { Text("Its pictures and tints go with it. You can undo.") }
    }

    // MARK: Left: the looks

    private var lookList: some View {
        VStack(spacing: 0) {
            List(selection: $selected) {
                Section(session.project?.isRecolour == true ? "Colour options" : "Looks") {
                    ForEach(looks.items) { look in
                        HStack {
                            Image(systemName: look.isDefault ? "star.fill" : "paintpalette")
                                .foregroundStyle(look.isDefault ? Color.yellow : Color.secondary)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(look.name.isEmpty ? "Untitled" : look.name)
                                Text(summary(of: look)).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .tag(look.id)
                    }
                }
            }
            .listStyle(.sidebar)
            Divider()
            HStack {
                Button { add() } label: { Image(systemName: "plus") }.help("New look")
                Button { confirmRemove = true } label: { Image(systemName: "minus") }
                    .disabled(current == nil).help("Remove this look")
                Spacer()
            }
            .buttonStyle(.borderless)
            .padding(8)
            if session.project?.isRecolour == false, let inv = inventory {
                Divider()
                Toggle(isOn: Binding(get: { looks.keepGameOptions },
                                     set: { on in Task { _ = await session.setKeepGameOptions(on) } })) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Keep the game's colours")
                        Text(inv.gameOptions == 0 ? "The game has none for this object"
                             : "\(inv.gameOptions) colour option\(inv.gameOptions == 1 ? "" : "s") come with the original")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                .disabled(inv.gameOptions == 0)
                .padding(10)
            }
        }
    }

    private func summary(of look: Look) -> String {
        if look.subsets.isEmpty { return "nothing changed yet" }
        let tints = look.subsets.values.filter(\.isTint).count
        let pictures = look.subsets.count - tints
        var parts: [String] = []
        if pictures > 0 { parts.append("\(pictures) picture\(pictures == 1 ? "" : "s")") }
        if tints > 0 { parts.append("\(tints) tint\(tints == 1 ? "" : "s")") }
        return parts.joined(separator: ", ")
    }

    private func add() {
        Task {
            if let id = await session.lookAdd(name: "") { selected = id }
        }
    }

    // MARK: Right: the selected look

    @ViewBuilder
    private var editor: some View {
        if let inv = inventory {
            if inv.recolourable.isEmpty {
                empty("This object's look is fixed by the game",
                      "Its model does not allow colour options, so a recolour cannot change it. Name & Catalog still works, and another base may allow more.")
            } else if let look = current {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        LookHeader(session: session, look: look)
                        ForEach(inv.recolourable) { subset in
                            SubsetCard(session: session, look: look, subset: subset)
                        }
                        if !inv.fixed.isEmpty {
                            VStack(alignment: .leading, spacing: 4) {
                                Label("Fixed by the game: " + inv.fixed.map(\.name).joined(separator: ", "),
                                      systemImage: "lock")
                                    .foregroundStyle(.secondary)
                                Text("The model only lets a colour option change the parts above.")
                                    .font(.caption).foregroundStyle(.tertiary)
                            }
                        }
                        Text("Changes apply as you make them and can be undone. Each look becomes a colour option in the game's design tool.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .padding(20)
                    .frame(maxWidth: 820, alignment: .leading)
                }
            } else {
                empty("No looks yet", "Add a look with the + button, then give each part a picture or a tint.")
            }
        } else {
            VStack(spacing: 8) {
                ProgressView()
                Text("Reading the model…").foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func empty(_ title: String, _ text: String) -> some View {
        VStack(spacing: 8) {
            Text(title).font(.title3).fontWeight(.semibold)
            Text(text).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 420)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// Name and default flag of one look.
struct LookHeader: View {
    @ObservedObject var session: PackageSession
    let look: Look
    @State private var name = ""

    var body: some View {
        HStack(spacing: 14) {
            TextField("Look name", text: $name)
                .textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                .onSubmit(commit)
            if session.project?.isRecolour != true {
                Toggle("Default look", isOn: Binding(
                    get: { look.isDefault },
                    set: { on in Task { _ = await session.lookSet(id: look.id, isDefault: on) } }))
                    .toggleStyle(.checkbox)
                    .help("The colour the object shows when first bought")
            }
            Spacer()
        }
        .onAppear { name = look.name }
        .onChange(of: look.id) { _ in name = look.name }
        .onChange(of: look.name) { new in if new != name { name = new } }
    }

    private func commit() {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, trimmed != look.name else { return }
        Task { _ = await session.lookSet(id: look.id, name: trimmed) }
    }
}

/// One recolourable part: its original, what the look does to it, and the
/// controls. Slider drags preview through the daemon and commit on release.
struct SubsetCard: View {
    @ObservedObject var session: PackageSession
    let look: Look
    let subset: SubsetInfo

    enum Mode: Hashable { case original, picture, tint }
    @State private var mode: Mode = .original
    @State private var color = Color(red: 0.2, green: 0.5, blue: 0.7)
    @State private var strength = 0.8
    @State private var lightness = 0.0
    @State private var preview: NSImage?
    @State private var previewKey = ""
    @State private var dragging = false
    @State private var importing = false

    private var source: LookSource? { look.subsets[subset.name] }
    private var original: NSImage? {
        subset.swatchPngB64.flatMap { Data(base64Encoded: $0) }.flatMap(NSImage.init(data:))
    }

    var body: some View {
        SectionCard(subset.name.capitalized) {
            HStack(alignment: .top, spacing: 16) {
                VStack(spacing: 4) {
                    SwatchImage(image: original, symbol: "square.dashed", size: 96)
                    Text("Original").font(.caption).foregroundStyle(.secondary)
                }
                Image(systemName: "arrow.right").foregroundStyle(.tertiary).padding(.top, 38)
                VStack(spacing: 4) {
                    SwatchImage(image: mode == .original ? original : (preview ?? original), symbol: "paintbrush", size: 96)
                        .overlay {
                            if importing { ProgressView().controlSize(.small) }
                        }
                        .onDrop(of: [.fileURL], isTargeted: nil) { providers in
                            drop(providers)
                        }
                    Text(mode == .original ? "Unchanged" : mode == .picture ? "Your picture" : "Tinted")
                        .font(.caption).foregroundStyle(.secondary)
                }
                VStack(alignment: .leading, spacing: 10) {
                    Picker("", selection: $mode) {
                        Text("Original").tag(Mode.original)
                        Text("Picture").tag(Mode.picture)
                        Text("Tint").tag(Mode.tint)
                    }
                    .pickerStyle(.segmented).labelsHidden().frame(maxWidth: 300)
                    .onChange(of: mode) { new in modeChanged(new) }
                    switch mode {
                    case .original:
                        Text("The game's own texture, \(subset.width)×\(subset.height).")
                            .font(.caption).foregroundStyle(.secondary)
                    case .picture:
                        HStack {
                            Button("Choose Picture…") { choosePicture() }
                            Text("or drop one on the swatch. It is resized to \(subset.width)×\(subset.height).")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    case .tint:
                        HStack(spacing: 12) {
                            ColorPicker("Colour", selection: $color, supportsOpacity: false)
                                .onChange(of: color) { _ in tintChanged(commit: true) }
                        }
                        HStack {
                            Text("Strength").frame(width: 70, alignment: .leading)
                            Slider(value: $strength, in: 0...1) { editing in sliderEdited(editing) }
                                .onChange(of: strength) { _ in tintChanged(commit: false) }
                            Text("\(Int(strength * 100))%").frame(width: 40, alignment: .trailing).font(.caption.monospacedDigit())
                        }
                        HStack {
                            Text("Lightness").frame(width: 70, alignment: .leading)
                            Slider(value: $lightness, in: -0.6...0.6) { editing in sliderEdited(editing) }
                                .onChange(of: lightness) { _ in tintChanged(commit: false) }
                            Text(lightness == 0 ? "0" : String(format: "%+.0f%%", lightness * 100))
                                .frame(width: 40, alignment: .trailing).font(.caption.monospacedDigit())
                        }
                    }
                    if !subset.states.isEmpty && subset.states.count > 1 {
                        Text("Covers its \(subset.states.count) states (clean, dirty…).")
                            .font(.caption).foregroundStyle(.tertiary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .onAppear { sync() }
        .onChange(of: source) { _ in sync() }
        .onChange(of: look.id) { _ in sync() }
        .task(id: previewKey) { await refreshPreview() }
    }

    // MARK: State

    /// Reflect the look's stored source in the controls.
    private func sync() {
        switch source {
        case nil:
            mode = .original
            preview = nil
        case .picture:
            mode = .picture
            previewKey = "picture:\(look.id):\(subset.name):\(session.editCount)"
        case .tint(let hex, let s, let l):
            mode = .tint
            if let c = Self.color(fromHex: hex) { color = c }
            strength = s
            lightness = l
            previewKey = "tint:\(hex):\(s):\(l)"
        }
    }

    private func modeChanged(_ new: Mode) {
        switch new {
        case .original:
            if source != nil { Task { _ = await session.lookSet(id: look.id, subsets: [subset.name: nil]) } }
        case .tint:
            if source?.isTint != true { tintChanged(commit: true) }
        case .picture:
            if case .picture = source {} else { preview = nil }
        }
    }

    private var tintSource: LookSource {
        .tint(color: Self.hex(from: color), strength: strength, lightness: lightness)
    }

    private func tintChanged(commit: Bool) {
        guard mode == .tint else { return }
        if case .tint(let hex, let s, let l) = tintSource {
            previewKey = "tint:\(hex):\(s):\(l)"
        }
        if commit && !dragging { commitTint() }
    }

    private func sliderEdited(_ editing: Bool) {
        dragging = editing
        if !editing { commitTint() }
    }

    private func commitTint() {
        guard mode == .tint, source != tintSource else { return }
        let wanted = tintSource
        Task { _ = await session.lookSet(id: look.id, subsets: [subset.name: wanted]) }
    }

    private func refreshPreview() async {
        guard mode != .original else { return }
        // Let a slider settle before asking the daemon.
        try? await Task.sleep(nanoseconds: 80_000_000)
        if Task.isCancelled { return }
        let src: LookSource? = mode == .tint ? tintSource : source
        guard let src else { return }
        if let img = await session.lookPreview(subset: subset.name, source: src) {
            if !Task.isCancelled { preview = img }
        }
    }

    // MARK: Pictures

    private func choosePicture() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.allowsMultipleSelection = false
        panel.message = "Choose a picture for the \(subset.name)"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        importPicture(url)
    }

    private func drop(_ providers: [NSItemProvider]) -> Bool {
        guard let p = providers.first(where: { $0.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) }) else { return false }
        p.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
            let url: URL?
            if let data = item as? Data { url = URL(dataRepresentation: data, relativeTo: nil) }
            else { url = item as? URL }
            if let url { DispatchQueue.main.async { importPicture(url) } }
        }
        return true
    }

    private func importPicture(_ url: URL) {
        importing = true
        let w = subset.width, h = subset.height
        Task.detached(priority: .userInitiated) {
            let png = ImageNormalizer.rgbaPNG(from: url, width: w, height: h)
            await MainActor.run {
                importing = false
                guard let png else {
                    session.errorMessage = "\(url.lastPathComponent) could not be read as a picture."
                    return
                }
                mode = .picture
                Task { _ = await session.lookSet(id: look.id, pictures: [subset.name: png]) }
            }
        }
    }

    // MARK: Colours

    static func hex(from color: Color) -> String {
        let ns = NSColor(color).usingColorSpace(.sRGB) ?? NSColor(color)
        let r = Int((ns.redComponent * 255).rounded()), g = Int((ns.greenComponent * 255).rounded()), b = Int((ns.blueComponent * 255).rounded())
        return String(format: "#%02X%02X%02X", max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
    }

    static func color(fromHex hex: String) -> Color? {
        var t = hex.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("#") { t.removeFirst() }
        guard t.count == 6, let v = Int(t, radix: 16) else { return nil }
        return Color(.sRGB, red: Double((v >> 16) & 255) / 255, green: Double((v >> 8) & 255) / 255, blue: Double(v & 255) / 255)
    }
}
