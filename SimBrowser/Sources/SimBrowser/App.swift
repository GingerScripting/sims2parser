import SwiftUI
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
                .frame(minWidth: 1000, minHeight: 640)
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

/// Attribute filters that AND together (e.g. Married + In College).
enum TraitFilter: String, CaseIterable, Identifiable {
    case married = "Married"
    case inCollege = "In College"
    case employed = "Employed"
    case retired = "Retired"
    case hasChildren = "Has Children"
    case inLove = "In Love"
    case hasEnemies = "Has Enemies"
    var id: String { rawValue }

    func matches(_ s: Sim) -> Bool {
        switch self {
        case .married: return !s.spouse.isEmpty
        case .inCollege: return s.onCampus
        case .employed: return !s.career.isEmpty
        case .retired: return !s.retiredCareer.isEmpty
        case .hasChildren: return !s.children.isEmpty
        case .inLove: return !s.loves.isEmpty
        case .hasEnemies: return !s.enemies.isEmpty
        }
    }
}

struct ContentView: View {
    @EnvironmentObject var store: DataStore
    @State private var hoodID: String?
    @State private var search = ""
    @State private var ageFilter = "All"
    @State private var typeFilter: SimTypeFilter = .playable
    @State private var genderFilter = "All"
    @State private var aspirationFilter = "All"
    @State private var traitFilters = Set<TraitFilter>()
    @State private var selection = Set<Sim>()

    private var activeExtraFilterCount: Int {
        traitFilters.count
            + (genderFilter == "All" ? 0 : 1)
            + (aspirationFilter == "All" ? 0 : 1)
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
            case .playable: if !s.isPlayable { return false }
            case .townies: if s.isPlayable { return false }
            case .all: break
            }
            if ageFilter != "All" && s.age != ageFilter { return false }
            if genderFilter != "All" && s.gender != genderFilter { return false }
            if aspirationFilter != "All" && !s.aspirations.contains(aspirationFilter) { return false }
            for trait in traitFilters where !trait.matches(s) { return false }
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
                searchField
                filterBar
                List(filteredSims, selection: $selection) { sim in
                    SimRow(sim: sim).tag(sim)
                }
                .listStyle(.inset)
                statusBar
            }
            .frame(width: 340)
            Divider()
            Group {
                if let sim = selectedSim {
                    DetailHost(sim: sim, hood: hood, onSelect: { selection = [$0] })
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
                        Text("\(h.name) (\(h.id))").tag(h.id)
                    }
                }
                .pickerStyle(.menu)
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
        .alert("Problem loading data", isPresented: Binding(
            get: { store.errorMessage != nil },
            set: { if !$0 { store.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(store.errorMessage ?? "")
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
                Divider()
                ForEach(TraitFilter.allCases) { trait in
                    Toggle(trait.rawValue, isOn: Binding(
                        get: { traitFilters.contains(trait) },
                        set: { on in
                            if on { traitFilters.insert(trait) } else { traitFilters.remove(trait) }
                        }
                    ))
                }
                Divider()
                Button("Clear Filters") {
                    traitFilters = []
                    genderFilter = "All"
                    aspirationFilter = "All"
                    ageFilter = "All"
                }
                .disabled(activeExtraFilterCount == 0 && ageFilter == "All")
            } label: {
                if activeExtraFilterCount > 0 {
                    Label("\(activeExtraFilterCount)", systemImage: "line.3.horizontal.decrease.circle.fill")
                } else {
                    Label("Filters", systemImage: "line.3.horizontal.decrease.circle")
                        .labelStyle(.iconOnly)
                }
            }
            .fixedSize()
            .help("More filters: gender, aspiration, married, in college…")
            Spacer()
        }
        .controlSize(.small)
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
    }

    private var statusBar: some View {
        HStack {
            Text("\(filteredSims.count) of \(hood?.sims.count ?? 0) sims")
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

    func makeNSView(context: Context) -> NSHostingView<SimDetailView> {
        let v = NSHostingView(rootView: SimDetailView(sim: sim, hood: hood, onSelect: onSelect))
        v.setContentHuggingPriority(.defaultLow, for: .horizontal)
        v.setContentHuggingPriority(.defaultLow, for: .vertical)
        return v
    }

    func updateNSView(_ nsView: NSHostingView<SimDetailView>, context: Context) {
        nsView.rootView = SimDetailView(sim: sim, hood: hood, onSelect: onSelect)
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
        if parts.isEmpty && sim.isNPC { parts.append("NPC") }
        return parts.joined(separator: " · ")
    }
    private var ageColor: Color {
        switch sim.age {
        case "Baby", "Toddler": return .pink
        case "Child": return .orange
        case "Teen": return .yellow
        case "Young Adult": return .green
        case "Adult": return .blue
        case "Elder": return .purple
        default: return .gray
        }
    }
}
