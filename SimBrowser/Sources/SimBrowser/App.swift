import SwiftUI
import SimKit
import AppKit

@main
struct SimBrowserApp: App {
    @StateObject private var store = DataStore()

    init() {
        // Needed when launched as a bare SwiftPM executable so the window comes to front.
        NSApplication.shared.setActivationPolicy(.regular)
        DispatchQueue.main.async { NSApplication.shared.activate(ignoringOtherApps: true) }
        GeometryProbe.mark("app init")
    }

    var body: some Scene {
        WindowGroup("Sim Browser") {
            ContentView()
                .environmentObject(store)
                // 340 sidebar + 1 divider + 760 detail minimum. The detail pane
                // cannot compress below 760 (its three-column traits row), so a
                // narrower window makes the whole HStack overflow and centre —
                // sliding the sim's name left, out over the sidebar divider.
                .frame(minWidth: 1101, minHeight: 640)
        }
        .commands {
            CommandGroup(after: .newItem) {
                Button("Refresh from Save Files") { store.refresh() }
                    .keyboardShortcut("r", modifiers: .command)
            }
        }
    }
}

enum SimTypeFilter: String, CaseIterable, Identifiable {
    case all = "Everyone"
    case playable = "Playable"
    case townies = "Townies & NPCs"
    var id: String { rawValue }
}

/// Which way a trait filter is pointing. Traits AND together, so a query like
/// "Romance adults who aren't dead and aren't married" is two `.no` traits
/// alongside the age and aspiration pickers.
enum TraitState: String, CaseIterable, Identifiable {
    case any, yes, no
    var id: String { rawValue }
}

/// How the filter menu groups its traits. Twelve traits in one flat list is a
/// scroll; four labelled runs of two to five is a glance.
enum TraitGroup: String, CaseIterable, Identifiable {
    case household = "Household"
    case career = "Career"
    case social = "Social"
    case business = "Business"
    var id: String { rawValue }
}

/// Attribute filters that AND together (e.g. Married + In College). Each is
/// three-way: unset, must match, or must not match.
enum TraitFilter: String, CaseIterable, Identifiable {
    case married = "Married"
    case hasChildren = "Has Children"
    case inCollege = "In College"
    case inFamilyBin = "In Family Bin"
    case deceased = "Deceased"
    case employed = "Employed"
    case retired = "Retired"
    case inLove = "In Love"
    case hasEnemies = "Has Enemies"
    case ownsBusiness = "Owns a Business"
    case hasBusinessPerks = "Has Business Perks"
    case hasTalentBadges = "Has Talent Badges"
    var id: String { rawValue }

    var group: TraitGroup {
        switch self {
        case .married, .hasChildren, .inCollege, .inFamilyBin, .deceased: return .household
        case .employed, .retired: return .career
        case .inLove, .hasEnemies: return .social
        case .ownsBusiness, .hasBusinessPerks, .hasTalentBadges: return .business
        }
    }

    /// Wording for the excluding side. Written out rather than generated,
    /// because "Not Has Children" is not something to put in a menu — and
    /// because the useful name for not-deceased is "Living".
    var negativeLabel: String {
        switch self {
        case .married: return "Not Married"
        case .hasChildren: return "No Children"
        case .inCollege: return "Not In College"
        case .inFamilyBin: return "Not In Family Bin"
        case .deceased: return "Living"
        case .employed: return "Unemployed"
        case .retired: return "Not Retired"
        case .inLove: return "Not In Love"
        case .hasEnemies: return "No Enemies"
        case .ownsBusiness: return "Owns No Business"
        case .hasBusinessPerks: return "No Business Perks"
        case .hasTalentBadges: return "No Talent Badges"
        }
    }

    func label(for state: TraitState) -> String {
        switch state {
        case .any: return "Any"
        case .yes: return rawValue
        case .no: return negativeLabel
        }
    }

    func matches(_ s: Sim) -> Bool {
        switch self {
        case .married: return !s.spouse.isEmpty
        case .hasChildren: return !s.children.isEmpty
        case .inCollege: return s.onCampus
        case .inFamilyBin: return s.isInFamilyBin
        case .deceased: return s.isDead
        case .employed: return !s.career.isEmpty
        case .retired: return !s.retiredCareer.isEmpty
        case .inLove: return !s.loves.isEmpty
        case .hasEnemies: return !s.enemies.isEmpty
        case .ownsBusiness: return s.ownsBusiness
        case .hasBusinessPerks: return s.hasBusinessPerks
        case .hasTalentBadges: return s.hasBadges
        }
    }

    /// Whether a sim passes this trait in the given state.
    func admits(_ s: Sim, _ state: TraitState) -> Bool {
        switch state {
        case .any: return true
        case .yes: return matches(s)
        case .no: return !matches(s)
        }
    }
}

enum ViewMode: String, CaseIterable {
    case sims = "Sims"
    case journal = "Journal"
}

struct ContentView: View {
    @EnvironmentObject var store: DataStore
    @StateObject private var journal = JournalStore()
    @State private var mode: ViewMode = .sims
    @State private var journalSelection: UUID?
    /// Remembered across launches, so the app reopens on the hood you actually
    /// play rather than whichever one sorts first. Empty before the first pick;
    /// `hood` falls back to the first hood whenever this matches nothing, which
    /// also covers a remembered hood that has since been removed or renamed.
    @AppStorage("lastHoodID") private var hoodID: String = ""
    @State private var search = ""
    @State private var ageFilter = "All"
    @State private var typeFilter: SimTypeFilter = .playable
    @State private var genderFilter = "All"
    @State private var aspirationFilter = "All"
    /// Traits left out of the dictionary are unset. Only the ones the user has
    /// actually pointed somewhere are stored.
    @State private var traitStates = [TraitFilter: TraitState]()
    @State private var selection = Set<Sim>()
    @State private var showRandomizer = false
    @State private var showChangeReview = false
    /// Non-nil while the family tree sheet is up. Presented from here rather
    /// than from SimDetailView, which lives inside its own NSHostingView.
    @State private var treeSim: Sim?

    private var activeTraits: [(TraitFilter, TraitState)] {
        TraitFilter.allCases.compactMap { trait in
            guard let state = traitStates[trait], state != .any else { return nil }
            return (trait, state)
        }
    }

    private var activeExtraFilterCount: Int {
        activeTraits.count
            + (genderFilter == "All" ? 0 : 1)
            + (aspirationFilter == "All" ? 0 : 1)
    }

    /// The filters in force, spelled out. A count alone can't show which way a
    /// trait points, and "Married" versus "Not Married" is the whole point.
    private var activeFilterSummary: [String] {
        var parts: [String] = []
        if typeFilter != .playable { parts.append(typeFilter.rawValue) }
        if ageFilter != "All" { parts.append(ageFilter) }
        if genderFilter != "All" { parts.append(genderFilter) }
        if aspirationFilter != "All" { parts.append(aspirationFilter) }
        parts.append(contentsOf: activeTraits.map { $0.0.label(for: $0.1) })
        return parts
    }

    private var anyFilterActive: Bool {
        !activeFilterSummary.isEmpty
    }

    private func clearFilters() {
        traitStates = [:]
        genderFilter = "All"
        aspirationFilter = "All"
        ageFilter = "All"
        typeFilter = .playable
    }

    private func traitBinding(_ trait: TraitFilter) -> Binding<TraitState> {
        Binding(
            get: { traitStates[trait] ?? .any },
            set: { traitStates[trait] = $0 == .any ? nil : $0 }
        )
    }

    private var selectedSim: Sim? {
        selection.count == 1 ? selection.first : nil
    }

    private var hood: Hood? {
        store.hoods.first { $0.id == hoodID } ?? store.hoods.first
    }

    private var filteredSims: [Sim] {
        guard let hood else { return [] }
        let q = search.lowercased()
        return hood.sims.filter { s in
            switch typeFilter {
            // Dead sims leave their family (family id 0), so keep them
            // visible in the Playable view rather than vanishing on death.
            case .playable: if !s.isPlayable && !s.isDead { return false }
            case .townies: if s.isPlayable { return false }
            case .all: break
            }
            if ageFilter != "All" && s.age != ageFilter { return false }
            if genderFilter != "All" && s.gender != genderFilter { return false }
            if aspirationFilter != "All" && !s.aspirations.contains(aspirationFilter) { return false }
            for (trait, state) in traitStates where !trait.admits(s, state) { return false }
            if !q.isEmpty {
                let hay = "\(s.fullName) \(s.household) \(s.address) \(s.career) \(s.careerTitle) \(s.major) \(s.bio)".lowercased()
                if !hay.contains(q) { return false }
            }
            return true
        }
    }

    var body: some View {
        // Deliberately NOT NavigationSplitView: on macOS 26 its glass sidebar
        // container re-lays-out 19.5pt off-window once a detail selection
        // exists (measured with GeometryProbe), shifting the whole list left.
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                modePicker
                if mode == .sims {
                    searchField
                    filterBar
                    List(filteredSims, selection: $selection) { sim in
                        SimRow(sim: sim).tag(sim)
                    }
                    .listStyle(.inset)
                    statusBar
                } else {
                    journalList
                }
            }
            .frame(width: 340)
            Divider()
            Group {
                if mode == .journal {
                    journalDetail
                } else if let sim = selectedSim {
                    DetailHost(
                        sim: sim, hood: hood,
                        onSelect: { selection = [$0] },
                        journalMentions: journal.mentions(of: sim.fullName, hoodID: hood?.id ?? ""),
                        onOpenJournal: { entryID in
                            journalSelection = entryID
                            mode = .journal
                        },
                        onShowFamilyTree: { treeSim = sim }
                    )
                    .id(sim.nid)
                } else if selection.count > 1 {
                    multiSelectionView
                } else {
                    placeholder
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .toolbar {
            ToolbarItem(placement: .navigation) {
                Picker("Neighborhood", selection: Binding(
                    get: { hood?.id ?? "" },
                    set: { hoodID = $0; selection = [] }
                )) {
                    ForEach(store.hoods) { h in
                        // Rotational play spans hoods, so a refresh has to say
                        // which of the others have something waiting.
                        let pending = store.detectedChanges[h.id]?.count ?? 0
                        Text(pending > 0 ? "\(h.name) (\(h.id)) — \(pending) new"
                                         : "\(h.name) (\(h.id))")
                            .tag(h.id)
                    }
                }
                .pickerStyle(.menu)
            }
            ToolbarItem {
                Button {
                    showRandomizer.toggle()
                } label: {
                    Label("Randomizer", systemImage: "dice")
                }
                .help("Roll a random event idea")
                .popover(isPresented: $showRandomizer, arrowEdge: .bottom) {
                    RandomizerView()
                }
            }
            ToolbarItem {
                Menu {
                    Button("Entire Neighborhood…") { export(hood?.sims ?? []) }
                    Button("Current List (\(filteredSims.count))…") { export(filteredSims) }
                    Button("Selected Sims (\(selection.count))…") { export(orderedSelection) }
                        .disabled(selection.isEmpty)
                } label: {
                    Label("Export CSV", systemImage: "square.and.arrow.up")
                }
                .help("Export sims to a CSV file")
            }
            ToolbarItem {
                Button {
                    store.refresh()
                } label: {
                    if store.isLoading { ProgressView().controlSize(.small) }
                    else { Label("Refresh", systemImage: "arrow.clockwise") }
                }
                .help("Re-read the game save files (⌘R)")
            }
        }
        .onAppear {
            store.loadCachedOrRefresh()
            GeometryProbe.startIfRequested()
        }
        .sheet(isPresented: $showChangeReview) {
            if let hood, let changes = store.detectedChanges[hood.id], !changes.isEmpty {
                ChangeReviewView(
                    hoodName: hood.name, changes: changes,
                    entries: journalEntries,
                    selectedEntry: journalSelection,
                    newEntryTitle: journal.suggestedTitle(for: hood.id),
                    onInsert: { insertChanges(hoodID: hood.id, changes: $0, into: $1) },
                    onCancel: { showChangeReview = false }
                )
            }
        }
        .sheet(item: $treeSim) { sim in
            if let hood {
                FamilyTreeView(
                    root: sim, hood: hood,
                    onSelect: { selection = [$0] },
                    onClose: { treeSim = nil }
                )
            }
        }
        .alert("Problem loading data", isPresented: Binding(
            get: { store.errorMessage != nil },
            set: { if !$0 { store.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(store.errorMessage ?? "")
        }
    }

    private var modePicker: some View {
        // A pending-change count on the Journal tab, so a refresh made from the
        // Sims list still announces that there is something to write up.
        let pending = store.detectedChanges[hood?.id ?? ""]?.count ?? 0
        return Picker("", selection: $mode) {
            ForEach(ViewMode.allCases, id: \.self) { m in
                Text(m == .journal && pending > 0 ? "\(m.rawValue) (\(pending))" : m.rawValue)
                    .tag(m)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .padding(.horizontal, 10)
        .padding(.top, 10)
    }

    private var journalEntries: [JournalEntry] {
        journal.entries(for: hood?.id ?? "")
    }

    private var journalList: some View {
        VStack(spacing: 0) {
            List(journalEntries, selection: $journalSelection) { entry in
                JournalRow(entry: entry).tag(entry.id)
            }
            .listStyle(.inset)
            Divider()
            HStack {
                Button {
                    guard let hoodID = hood?.id else { return }
                    let entry = journal.addEntry(hoodID: hoodID)
                    journalSelection = entry.id
                } label: {
                    Label("New Entry", systemImage: "plus")
                }
                .buttonStyle(.borderless)
                Spacer()
                Text("\(journalEntries.count) entries")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
        }
        .padding(.top, 8)
    }

    @ViewBuilder
    private var journalDetail: some View {
        VStack(spacing: 0) {
            if let hoodID = hood?.id, let changes = store.detectedChanges[hoodID], !changes.isEmpty {
                changesBanner(hoodID: hoodID, changes: changes)
                Divider()
            }
            journalEditorArea
        }
    }

    private func changesBanner(hoodID: String, changes: [Change]) -> some View {
        let households = ChangeDigest.grouped(changes).count
        return HStack(spacing: 10) {
            Image(systemName: "sparkles")
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 1) {
                Text("\(changes.count) change\(changes.count == 1 ? "" : "s") detected across \(households) household\(households == 1 ? "" : "s")")
                    .font(.callout).fontWeight(.medium)
                Text(changes.prefix(2).map(\.text).joined(separator: " · "))
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            Button("Review…") { showChangeReview = true }
            // Names its target: the entry on screen is not always the season
            // that was just played, and silently guessing put a whole rotation
            // in the wrong season once already.
            Button("Add All to \(insertTargetTitle)") {
                insertChanges(hoodID: hoodID, changes: changes, into: journalSelection)
            }
            Button {
                store.clearChanges(hoodID: hoodID)
            } label: {
                Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .help("Discard detected changes")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.orange.opacity(0.08))
    }

    /// Title of the entry a one-click "Add All" would write into.
    private var insertTargetTitle: String {
        if let sel = journalSelection, let entry = journalEntries.first(where: { $0.id == sel }) {
            return entry.title
        }
        return journal.suggestedTitle(for: hood?.id ?? "")
    }

    /// `destination` nil means "start a new entry for the next season".
    private func insertChanges(hoodID: String, changes: [Change], into destination: UUID?) {
        guard !changes.isEmpty else { return }
        let entryID: UUID
        if let destination, journal.binding(hoodID: hoodID, id: destination) != nil {
            entryID = destination
        } else {
            entryID = journal.addEntry(hoodID: hoodID).id
        }
        journalSelection = entryID
        guard let binding = journal.binding(hoodID: hoodID, id: entryID) else { return }
        var body = binding.wrappedValue.body
        if !body.isEmpty && !body.hasSuffix("\n\n") {
            body += body.hasSuffix("\n") ? "\n" : "\n\n"
        }
        body += ChangeDigest.journalText(changes) + "\n"
        binding.wrappedValue.body = body
        store.clearChanges(hoodID: hoodID, ids: Set(changes.map { $0.id }))
        showChangeReview = false
        mode = .journal
    }

    @ViewBuilder
    private var journalEditorArea: some View {
        if let hoodID = hood?.id,
           let entryID = journalSelection,
           let binding = journal.binding(hoodID: hoodID, id: entryID) {
            IsolatedPane {
                JournalEditorView(entry: binding, onDelete: {
                    journal.deleteEntry(hoodID: hoodID, id: entryID)
                    journalSelection = nil
                })
            }
            .id(entryID)
        } else {
            VStack(spacing: 12) {
                Image(systemName: "book.closed")
                    .font(.system(size: 44)).foregroundStyle(.tertiary)
                Text("Select or create a journal entry")
                    .font(.title3).foregroundStyle(.secondary)
                Text("One entry per season — what happened in \(hood?.name ?? "this neighborhood") this rotation.")
                    .font(.callout).foregroundStyle(.tertiary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var searchField: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField("Name, household, career…", text: $search)
                .textFieldStyle(.plain)
            if !search.isEmpty {
                Button {
                    search = ""
                } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 7))
        .padding(.horizontal, 10)
        .padding(.top, 10)
    }

    private var filterBar: some View {
        HStack(spacing: 8) {
            Picker("", selection: $typeFilter) {
                ForEach(SimTypeFilter.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.menu)
            .fixedSize()
            Picker("", selection: $ageFilter) {
                ForEach(["All", "Baby", "Toddler", "Child", "Teen", "Young Adult", "Adult", "Elder"], id: \.self) {
                    Text($0).tag($0)
                }
            }
            .pickerStyle(.menu)
            .fixedSize()
            Menu {
                Picker("Gender", selection: $genderFilter) {
                    ForEach(["All", "Female", "Male"], id: \.self) { Text($0).tag($0) }
                }
                Picker("Aspiration", selection: $aspirationFilter) {
                    ForEach(["All", "Romance", "Family", "Fortune", "Popularity",
                             "Knowledge", "Pleasure", "Grow Up", "Grilled Cheese"], id: \.self) {
                        Text($0).tag($0)
                    }
                }
                // Each trait is its own submenu of Any / is / is not, the same
                // shape as the two pickers above it.
                ForEach(TraitGroup.allCases) { group in
                    Section(group.rawValue) {
                        ForEach(TraitFilter.allCases.filter { $0.group == group }) { trait in
                            Picker(trait.rawValue, selection: traitBinding(trait)) {
                                ForEach(TraitState.allCases) { state in
                                    Text(trait.label(for: state)).tag(state)
                                }
                            }
                        }
                    }
                }
                Divider()
                Button("Clear Filters") { clearFilters() }
                    .disabled(!anyFilterActive)
            } label: {
                if activeExtraFilterCount > 0 {
                    Label("\(activeExtraFilterCount)", systemImage: "line.3.horizontal.decrease.circle.fill")
                } else {
                    Label("Filters", systemImage: "line.3.horizontal.decrease.circle")
                        .labelStyle(.iconOnly)
                }
            }
            .fixedSize()
            .help("More filters: gender, aspiration, and twelve traits that can each "
                  + "be required or excluded (Married / Not Married, Deceased / Living…)")
            if anyFilterActive {
                Button { clearFilters() } label: {
                    Image(systemName: "xmark.circle.fill")
                }
                .buttonStyle(.borderless)
                .foregroundStyle(.secondary)
                .help("Clear all filters")
            }
            Spacer()
        }
        .controlSize(.small)
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
    }

    private var statusBar: some View {
        HStack {
            Text("\(filteredSims.count) of \(hood?.sims.count ?? 0) sims")
            if anyFilterActive {
                Text(activeFilterSummary.joined(separator: " · "))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .help(activeFilterSummary.joined(separator: " · "))
            }
            Spacer()
            if let d = store.lastRefreshed {
                Text("Read \(d.formatted(date: .abbreviated, time: .shortened))")
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 10)
        .padding(.vertical, 4)
    }

    /// Selection in the list's visible order (so exports keep the current sort).
    private var orderedSelection: [Sim] {
        filteredSims.filter { selection.contains($0) }
    }

    private func export(_ sims: [Sim]) {
        let name = hood?.name ?? "Sims"
        CSVExporter.save(sims: sims, hoodName: name, suggestedName: "\(name) sims.csv")
    }

    private var multiSelectionView: some View {
        VStack(spacing: 14) {
            Image(systemName: "person.3.fill")
                .font(.system(size: 40))
                .foregroundStyle(.tertiary)
            Text("\(selection.count) sims selected")
                .font(.title3).foregroundStyle(.secondary)
            Button("Export Selected as CSV…") { export(orderedSelection) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var placeholder: some View {
        VStack(spacing: 12) {
            Image(systemName: "person.text.rectangle")
                .font(.system(size: 44))
                .foregroundStyle(.tertiary)
            Text("Select a sim").font(.title3).foregroundStyle(.secondary)
            if store.hoods.isEmpty && !store.isLoading {
                Button("Load save files") { store.refresh() }
            }
            if store.isLoading {
                ProgressView("Reading neighborhoods…")
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// Hosts the detail pane in its own NSHostingView so its ScrollView is
/// invisible to the root SwiftUI layout. On macOS 26, a ScrollView in the
/// root hierarchy makes SwiftUI extend the whole layout past the window
/// edges ("concentric" glass insets), sliding the sidebar list 16pt
/// off-window (measured with GeometryProbe).
struct DetailHost: NSViewRepresentable {
    let sim: Sim
    let hood: Hood?
    var onSelect: (Sim) -> Void
    var journalMentions: [JournalMention] = []
    var onOpenJournal: (UUID) -> Void = { _ in }
    var onShowFamilyTree: () -> Void = {}

    private var detailView: SimDetailView {
        SimDetailView(sim: sim, hood: hood, onSelect: onSelect,
                      journalMentions: journalMentions, onOpenJournal: onOpenJournal,
                      onShowFamilyTree: onShowFamilyTree)
    }

    func makeNSView(context: Context) -> NSHostingView<SimDetailView> {
        let v = NSHostingView(rootView: detailView)
        v.setContentHuggingPriority(.defaultLow, for: .horizontal)
        v.setContentHuggingPriority(.defaultLow, for: .vertical)
        return v
    }

    func updateNSView(_ nsView: NSHostingView<SimDetailView>, context: Context) {
        nsView.rootView = detailView
    }
}

struct SimRow: View {
    let sim: Sim

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(sim.fullName).fontWeight(.medium)
                Text(subtitle).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text(sim.age)
                .font(.caption2)
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(ageColor.opacity(0.18), in: Capsule())
                .foregroundStyle(ageColor)
        }
        .padding(.vertical, 1)
    }
    private var subtitle: String {
        var parts: [String] = []
        if !sim.household.isEmpty { parts.append("\(sim.household) household") }
        if !sim.careerDisplay.isEmpty { parts.append(sim.careerDisplay) }
        if sim.isDead { parts.insert("Deceased", at: 0) }
        if parts.isEmpty && sim.isNPC { parts.append("NPC") }
        return parts.joined(separator: " · ")
    }
    private var ageColor: Color { sim.ageColor }
}
