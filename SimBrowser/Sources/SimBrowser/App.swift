import SwiftUI
import AppKit

@main
struct SimBrowserApp: App {
    @StateObject private var store = DataStore()

    init() {
        // Needed when launched as a bare SwiftPM executable so the window comes to front.
        NSApplication.shared.setActivationPolicy(.regular)
        DispatchQueue.main.async { NSApplication.shared.activate(ignoringOtherApps: true) }
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

struct ContentView: View {
    @EnvironmentObject var store: DataStore
    @State private var hoodID: String?
    @State private var search = ""
    @State private var ageFilter = "All"
    @State private var typeFilter: SimTypeFilter = .playable
    @State private var selection = Set<Sim>()

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
            if !q.isEmpty {
                let hay = "\(s.fullName) \(s.household) \(s.address) \(s.career) \(s.careerTitle) \(s.major) \(s.bio)".lowercased()
                if !hay.contains(q) { return false }
            }
            return true
        }
    }

    var body: some View {
        NavigationSplitView {
            VStack(spacing: 0) {
                filterBar
                List(filteredSims, selection: $selection) { sim in
                    SimRow(sim: sim).tag(sim)
                }
                .listStyle(.inset)
                statusBar
            }
            .navigationSplitViewColumnWidth(min: 300, ideal: 340)
        } detail: {
            if let sim = selectedSim {
                SimDetailView(sim: sim, hood: hood, onSelect: { selection = [$0] })
                    .id(sim.nid)
            } else if selection.count > 1 {
                multiSelectionView
            } else {
                placeholder
            }
        }
        .searchable(text: $search, placement: .sidebar, prompt: "Name, household, career…")
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
        .onAppear { store.loadCachedOrRefresh() }
        .alert("Problem loading data", isPresented: Binding(
            get: { store.errorMessage != nil },
            set: { if !$0 { store.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(store.errorMessage ?? "")
        }
    }

    private var filterBar: some View {
        HStack(spacing: 8) {
            Picker("", selection: $typeFilter) {
                ForEach(SimTypeFilter.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.menu)
            .fixedSize()
            Picker("", selection: $ageFilter) {
                ForEach(["All", "Baby", "Toddler", "Child", "Teen", "Adult", "Elder"], id: \.self) {
                    Text($0).tag($0)
                }
            }
            .pickerStyle(.menu)
            .fixedSize()
            Spacer()
        }
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
        case "Adult": return .blue
        case "Elder": return .purple
        default: return .gray
        }
    }
}
