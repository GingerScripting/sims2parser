import SwiftUI
import SimStudioCore
import AppKit
import SimKit

/// The Sims mode of a neighborhood window: a sim list on the left and the
/// editor on the right. Every write is a `put_resource` of the sim's SDSC,
/// a relationship's SREL, or the hood's NGBH, so undo and Save As behave
/// exactly as they do for any other resource.
struct SimsPane: View {
    @ObservedObject var session: PackageSession
    @State private var search = ""
    @State private var selected: Int? = Launch.sim
    // Held here, not in SimEditor, so the tab survives picking another sim.
    @State private var tab: SimTab = Launch.tab ?? .overview

    private var filtered: [SimRow] {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return session.sims }
        return session.sims.filter {
            $0.fullName.lowercased().contains(q) || $0.career.lowercased().contains(q) || String($0.nid) == q
        }
    }

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                TextField("Search sims", text: $search)
                    .textFieldStyle(.roundedBorder)
                    .padding(8)
                if session.simsLoading {
                    ProgressView("Reading characters…").frame(maxHeight: .infinity)
                } else {
                    List(filtered, selection: $selected) { sim in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(sim.fullName.isEmpty ? "Sim #\(sim.nid)" : sim.fullName)
                                    .fontWeight(.medium)
                                    .foregroundStyle(sim.fullName.isEmpty ? .secondary : .primary)
                                Text([sim.career, sim.careerTitle].filter { !$0.isEmpty }.joined(separator: " · "))
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(sim.age).font(.caption2).foregroundStyle(.secondary)
                        }
                        .tag(sim.nid)
                    }
                    .listStyle(.inset)
                }
                Divider()
                Text("\(filtered.count) of \(session.sims.count) sims")
                    .font(.caption).foregroundStyle(.secondary).padding(6)
            }
            .frame(width: 300)
            Divider()
            Group {
                if let nid = selected {
                    IsolatedPane {
                        SimEditor(session: session, nid: nid, tab: $tab)
                    }
                    .id(nid)
                    .clipped()
                } else {
                    Text("Select a sim").foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .task { await session.loadSims() }
    }
}

/// One sim, laid out like SimPE's Sim Description plugin: a header with the
/// portrait and a Profile button, an icon tab strip, and one page per tab.
/// The draft of the SDSC fields is shared by every form tab, so edits made
/// on Character and Skills go through one Apply.
struct SimEditor: View {
    @ObservedObject var session: PackageSession
    let nid: Int
    @Binding var tab: SimTab

    @State private var detail: SimDetail?
    @State private var draft: [String: Int] = [:]
    @State private var loading = false
    @State private var portrait: NSImage?
    // `SIMSTUDIO_PROFILE=1` opens the Profile sheet at launch, for snapshots.
    @State private var showProfile = ProcessInfo.processInfo.environment["SIMSTUDIO_PROFILE"] != nil

    private var meta: HoodMeta? { session.hoodMeta }
    private var changed: [String: Int] {
        guard let d = detail else { return [:] }
        return draft.filter { d.fields[$0.key] != $0.value }
    }

    var body: some View {
        VStack(spacing: 0) {
            if let d = detail {
                header(d)
                SimTabStrip(selection: $tab)
                Divider()
                switch tab {
                case .relations: RelationshipsView(session: session, detail: d, meta: meta)
                case .memories: TokensView(session: session, detail: d, meta: meta)
                default:
                    if let meta {
                        SimFieldsTab(tab: tab, detail: d, meta: meta, draft: $draft)
                        Divider()
                        footer(d)
                    } else {
                        ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                }
            } else if loading {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                Text("Could not load this sim.").foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .task(id: nid) { await load() }
        .sheet(isPresented: $showProfile) {
            if let d = detail { SimProfileSheet(detail: d, portrait: portrait) }
        }
    }

    private func load() async {
        loading = true
        detail = await session.sim(nid)
        draft = detail?.fields ?? [:]
        loading = false
        if let data = await session.simPortrait(nid) { portrait = NSImage(data: data) }
    }

    private func header(_ d: SimDetail) -> some View {
        HStack(alignment: .top, spacing: 12) {
            PortraitView(image: portrait, size: 56)
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(d.fullName.isEmpty ? "Sim #\(d.nid)" : d.fullName).font(.title3).fontWeight(.semibold)
                    if let h = d.household, !h.isEmpty {
                        Text("· \(h) household").foregroundStyle(.secondary)
                    }
                }
                Text("nid \(d.nid) · GUID \(hex8(UInt32(truncatingIfNeeded: d.fields["guid"] ?? 0)))")
                    .font(.system(.caption, design: .monospaced)).foregroundStyle(.secondary)
                if !d.charFile.isEmpty {
                    Text(d.charFile).font(.caption).foregroundStyle(.tertiary)
                }
            }
            Spacer()
            Button {
                showProfile = true
            } label: {
                Label("Profile", systemImage: "text.book.closed")
            }
            .help("The sim's portrait and a written profile")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private func footer(_ d: SimDetail) -> some View {
        HStack {
            if !changed.isEmpty {
                Text("\(changed.count) unapplied change\(changed.count == 1 ? "" : "s")")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button("Revert") { draft = d.fields }.disabled(changed.isEmpty)
            Button("Apply") {
                let edits = changed
                Task {
                    if await session.putSim(nid, fields: edits) { await load() }
                }
            }
            .keyboardShortcut(.return, modifiers: .command)
            .disabled(changed.isEmpty || session.busy)
            .help("Send the edit to the package in memory (⌘↩). Copy Hood writes it to disk.")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }
}

/// The sim's outgoing relationship records, editable in place.
struct RelationshipsView: View {
    @ObservedObject var session: PackageSession
    let detail: SimDetail
    let meta: HoodMeta?
    @State private var drafts: [Int: [String: Int]] = [:]
    @State private var selected: Int?

    private var rels: [Relationship] { detail.relationships }

    var body: some View {
        HStack(spacing: 0) {
            Table(rels, selection: $selected) {
                TableColumn("Sim") { r in Text(r.name.isEmpty ? "#\(r.target)" : r.name) }
                TableColumn("Daily") { r in Text("\(r.fields["daily"] ?? 0)").monospacedDigit() }.width(56)
                TableColumn("Lifetime") { r in Text("\(r.fields["lifetime"] ?? 0)").monospacedDigit() }.width(64)
                TableColumn("Flags") { r in Text(flagLabels(r.fields["flags"] ?? 0)).font(.caption) }
            }
            .frame(minWidth: 300)
            Divider()
            ScrollView {
                if let t = selected, let r = rels.first(where: { $0.target == t }) {
                    relationshipForm(r)
                        .padding(14)
                } else {
                    Text("Select a relationship").foregroundStyle(.secondary).padding()
                }
            }
            .frame(minWidth: 240)
        }
    }

    private func flagLabels(_ flags: Int) -> String {
        guard let meta else { return String(flags) }
        return meta.options("relationship", from: meta.srelTables)
            .filter { flags & $0.value != 0 }.map(\.label).joined(separator: ", ")
    }

    private func relationshipForm(_ r: Relationship) -> some View {
        let draft = Binding<[String: Int]>(
            get: { drafts[r.target] ?? r.fields },
            set: { drafts[r.target] = $0 })
        let changed = draft.wrappedValue != r.fields
        return VStack(alignment: .leading, spacing: 10) {
            Text(r.name.isEmpty ? "Sim #\(r.target)" : r.name).font(.headline)
            if let meta {
                ForEach(meta.srelFields) { f in
                    if r.fields[f.name] != nil {
                        let b = Binding<Int>(get: { draft.wrappedValue[f.name] ?? 0 },
                                             set: { draft.wrappedValue[f.name] = $0 })
                        if f.isFlags {
                            LabeledContent(f.label) {
                                FlowLayout(spacing: 8) {
                                    ForEach(meta.options(f.table ?? "", from: meta.srelTables).sorted { $0.value < $1.value }, id: \.value) { o in
                                        Toggle(o.label, isOn: Binding(
                                            get: { b.wrappedValue & o.value != 0 },
                                            set: { b.wrappedValue = $0 ? b.wrappedValue | o.value : b.wrappedValue & ~o.value }))
                                        .toggleStyle(.checkbox)
                                    }
                                }
                            }
                        } else if f.isEnum {
                            LabeledContent(f.label) {
                                let opts = meta.options(f.table ?? "", from: meta.srelTables)
                                Picker("", selection: b) {
                                    if !opts.contains(where: { $0.value == b.wrappedValue }) {
                                        Text("none").tag(b.wrappedValue)
                                    }
                                    ForEach(opts, id: \.value) { o in Text(o.label).tag(o.value) }
                                }
                                .labelsHidden()
                            }
                        } else if f.kind == "bool" {
                            Toggle(f.label, isOn: Binding(get: { b.wrappedValue != 0 }, set: { b.wrappedValue = $0 ? 1 : 0 }))
                        } else {
                            LabeledContent(f.label) { NumberField(value: b, width: 90) }
                        }
                    }
                }
            }
            Text("Daily and lifetime run −100…100. This is the record \(detail.fullName.isEmpty ? "this sim" : detail.fullName) holds; the other sim's record is separate.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Revert") { drafts[r.target] = nil }.disabled(!changed)
                Button("Apply") {
                    let edits = draft.wrappedValue.filter { r.fields[$0.key] != $0.value }
                    Task {
                        if await session.putRelationship(owner: detail.nid, target: r.target, fields: edits) {
                            drafts[r.target] = nil
                        }
                    }
                }
                .disabled(!changed || session.busy)
            }
        }
    }
}

/// The sim's token group: memories, badges, and everything else the game
/// files under the sim. Two lists, kept apart because the store keeps them
/// apart; memories are the tokens whose owner slot is this sim.
struct TokensView: View {
    @ObservedObject var session: PackageSession
    let detail: SimDetail
    let meta: HoodMeta?
    @State private var first: [SimToken] = []
    @State private var second: [SimToken] = []
    @State private var selection: String?
    @State private var onlyMemories = true

    private var changed: Bool { first != detail.tokens.first || second != detail.tokens.second }

    private func isMemory(_ t: SimToken) -> Bool {
        guard let slot = meta?.memoryOwnerSlot else { return false }
        return t.values.indices.contains(slot) && t.values[slot] == detail.nid
    }

    var body: some View {
        VStack(spacing: 0) {
            if let err = detail.tokens.error {
                Label("This token store cannot be rebuilt faithfully, so it is read-only: \(err)", systemImage: "lock")
                    .font(.caption).padding(8)
            }
            HStack {
                Toggle("Only this sim's memories", isOn: $onlyMemories)
                Spacer()
                Text("\(first.count) + \(second.count) tokens").font(.caption).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 6)
            Divider()
            HStack(spacing: 0) {
                List(selection: $selection) {
                    tokenSection("first", title: "First list", items: first)
                    tokenSection("second", title: "Second list", items: second)
                }
                .frame(minWidth: 300)
                Divider()
                ScrollView {
                    if let sel = selection, let (list, i) = parse(sel) {
                        tokenForm(list: list, index: i).padding(14)
                    } else {
                        Text("Select a token").foregroundStyle(.secondary).padding()
                    }
                }
                .frame(minWidth: 240)
            }
            Divider()
            HStack {
                Button("Add Token") {
                    second.append(SimToken(guid: 0, name: "", raw: String(repeating: "00", count: 10),
                                           values: Array(repeating: 0, count: (meta?.memorySubjectSlot ?? 12) + 1)))
                    selection = "second/\(second.count - 1)"
                    onlyMemories = false
                }
                .disabled(!detail.tokens.editable)
                Button("Remove") {
                    if let sel = selection, let (list, i) = parse(sel) {
                        if list == "first" { first.remove(at: i) } else { second.remove(at: i) }
                        selection = nil
                    }
                }
                .disabled(selection == nil || !detail.tokens.editable)
                Spacer()
                Button("Revert") { first = detail.tokens.first; second = detail.tokens.second }.disabled(!changed)
                Button("Apply") {
                    Task { _ = await session.putTokens(detail.nid, first: first, second: second) }
                }
                .disabled(!changed || session.busy || !detail.tokens.editable)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
        }
        .onAppear { first = detail.tokens.first; second = detail.tokens.second }
        .onChange(of: detail.tokens.first) { first = $0 }
        .onChange(of: detail.tokens.second) { second = $0 }
    }

    private func tokenSection(_ list: String, title: String, items: [SimToken]) -> some View {
        Section(title) {
            ForEach(items.indices, id: \.self) { i in
                if !onlyMemories || isMemory(items[i]) {
                    tokenRow(items[i]).tag("\(list)/\(i)")
                }
            }
        }
    }

    private func tokenRow(_ t: SimToken) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 1) {
                Text(t.name.isEmpty ? hex8(t.guid) : t.name).lineLimit(1)
                Text(subjectLine(t)).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if isMemory(t) { Image(systemName: "brain").foregroundStyle(.secondary) }
        }
    }

    private func subjectLine(_ t: SimToken) -> String {
        guard let s = meta?.memorySubjectSlot, t.values.indices.contains(s) else { return "\(t.values.count) values" }
        let subject = t.values[s]
        let name = session.sims.first { $0.nid == subject }?.fullName
        return "about \(name ?? "#\(subject)") · \(t.values.count) values"
    }

    private func parse(_ sel: String) -> (String, Int)? {
        let parts = sel.split(separator: "/")
        guard parts.count == 2, let i = Int(parts[1]) else { return nil }
        let list = String(parts[0])
        let count = list == "first" ? first.count : second.count
        return i < count ? (list, i) : nil
    }

    private func tokenForm(list: String, index: Int) -> some View {
        let binding = Binding<SimToken>(
            get: { list == "first" ? first[index] : second[index] },
            set: { if list == "first" { first[index] = $0 } else { second[index] = $0 } })
        return VStack(alignment: .leading, spacing: 10) {
            Text(binding.wrappedValue.name.isEmpty ? "Token" : binding.wrappedValue.name).font(.headline)
            LabeledContent("GUID") {
                IdOnlyField(value: binding.guid)
            }
            LabeledContent("Header bytes") {
                RawBytesField(bytes: Binding(get: { [UInt8](hex: binding.wrappedValue.raw) ?? [] },
                                             set: { binding.wrappedValue.raw = $0.hexString }), expected: 10)
            }
            EditorHeading("\(binding.wrappedValue.values.count) values")
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 130), spacing: 8)], alignment: .leading, spacing: 6) {
                ForEach(binding.wrappedValue.values.indices, id: \.self) { i in
                    HStack(spacing: 4) {
                        Text(slotLabel(i)).font(.caption).foregroundStyle(.secondary).frame(width: 52, alignment: .trailing)
                        NumberField(value: Binding(get: { binding.wrappedValue.values[i] },
                                                   set: { binding.wrappedValue.values[i] = $0 }), width: 64)
                    }
                }
            }
            HStack {
                Button("Add Value") { binding.wrappedValue.values.append(0) }
                Button("Remove Last") { if !binding.wrappedValue.values.isEmpty { binding.wrappedValue.values.removeLast() } }
                    .disabled(binding.wrappedValue.values.isEmpty)
            }
            Text("Slot \(meta?.memoryOwnerSlot ?? 4) is the owner and slot \(meta?.memorySubjectSlot ?? 12) the subject of a memory; the rest is per-token.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func slotLabel(_ i: Int) -> String {
        if i == meta?.memoryOwnerSlot { return "owner" }
        if i == meta?.memorySubjectSlot { return "subject" }
        return "\(i)"
    }
}
