import SwiftUI
import SimStudioCore
import SimKit

/// The pages of the sim editor, in the order SimPE's Sim Description plugin
/// shows them. Each form tab claims the daemon fields it lays out; a field
/// no tab claims lands in Other, so a new Python field appears without a
/// Swift change.
enum SimTab: String, CaseIterable, Identifiable {
    case overview, character, skills, interests, career, university, relations, memories, other
    var id: String { rawValue }

    var label: String {
        switch self {
        case .overview: return "Overview"
        case .character: return "Character"
        case .skills: return "Skills"
        case .interests: return "Interests"
        case .career: return "Career"
        case .university: return "University"
        case .relations: return "Relations"
        case .memories: return "Memories"
        case .other: return "Other"
        }
    }

    var symbol: String {
        switch self {
        case .overview: return "person.text.rectangle"
        case .character: return "face.smiling"
        case .skills: return "star"
        case .interests: return "lightbulb"
        case .career: return "briefcase"
        case .university: return "graduationcap"
        case .relations: return "person.2"
        case .memories: return "brain"
        case .other: return "ellipsis.circle"
        }
    }

    /// Relations and Memories have their own views and Apply buttons.
    var hasForm: Bool { self != .relations && self != .memories }

    private var fieldNames: [String] {
        switch self {
        case .overview: return ["nid", "guid", "family_id", "age_stage", "gender", "aspiration_flags", "aspiration_score", "days_left"]
        case .character: return ["zodiac", "pref_female", "pref_male"]
        case .career: return ["career_guid", "career_level", "job_performance", "retired_guid", "retired_level"]
        case .university: return ["major_guid", "semester", "on_campus", "grade"]
        case .other: return ["ghost_flags", "npc_type", "fatness", "body_flags"]
        default: return []
        }
    }

    private var prefixes: [String] {
        switch self {
        case .character: return ["personality.", "genetic."]
        case .skills: return ["skills."]
        case .interests: return ["interests."]
        default: return []
        }
    }

    func claims(_ name: String) -> Bool {
        fieldNames.contains(name) || prefixes.contains { name.hasPrefix($0) }
    }

    static func owner(of name: String) -> SimTab {
        allCases.first { $0.claims(name) } ?? .other
    }

    /// The fields this tab shows, in the daemon's order.
    func fields(in meta: HoodMeta) -> [FieldDef] {
        meta.sdscFields.filter { SimTab.owner(of: $0.name) == self }
    }
}

/// Icon-over-label buttons, one per tab.
struct SimTabStrip: View {
    @Binding var selection: SimTab

    var body: some View {
        HStack(spacing: 4) {
            ForEach(SimTab.allCases) { t in
                Button {
                    selection = t
                } label: {
                    VStack(spacing: 3) {
                        Image(systemName: t.symbol).font(.title2)
                        Text(t.label).font(.caption)
                    }
                    .frame(width: 72, height: 50)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .background(selection == t ? Color.accentColor.opacity(0.18) : .clear,
                            in: RoundedRectangle(cornerRadius: 8))
                .foregroundStyle(selection == t ? Color.accentColor : .primary)
                .help(t.label)
            }
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }
}

/// A label, a segment bar and the exact number.
struct MeterRow: View {
    let label: String
    @Binding var value: Int
    var signed = false
    var unit = 100
    var tint: Color = .green

    var body: some View {
        HStack(spacing: 8) {
            Text(label).lineLimit(1).frame(width: 100, alignment: .trailing)
            SegmentBar(value: $value, unit: unit, signed: signed, tint: tint)
            NumberField(value: $value, width: 56)
        }
    }
}

/// The scrollable body of a form tab: SimPE-style layouts for Character,
/// Skills, Interests and Career, and the generic rows for the rest.
struct SimFieldsTab: View {
    let tab: SimTab
    let detail: SimDetail
    let meta: HoodMeta
    @Binding var draft: [String: Int]

    private func binding(_ name: String) -> Binding<Int> {
        Binding(get: { draft[name] ?? 0 }, set: { draft[name] = $0 })
    }
    private func has(_ name: String) -> Bool { detail.fields[name] != nil }
    private var fields: [FieldDef] { tab.fields(in: meta) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                switch tab {
                case .character: character
                case .skills: meters("Skills", prefix: "skills.", columns: 1)
                case .interests: meters("Interests", prefix: "interests.", columns: 2)
                case .career: career
                case .overview: overview
                default: generic(tab.label, fields)
                }
            }
            .padding(16)
        }
    }

    // MARK: Layouts

    private static let personalityOrder = ["Neat", "Outgoing", "Active", "Playful", "Nice"]

    private var character: some View {
        Group {
            SectionCard("Character") {
                VStack(alignment: .leading, spacing: 14) {
                    if let z = fields.first(where: { $0.name == "zodiac" }) {
                        FieldRow(f: z, meta: meta, draft: $draft)
                    }
                    HStack(alignment: .top, spacing: 32) {
                        column("Personality", prefix: "personality.")
                        column("Genetic", prefix: "genetic.")
                    }
                }
            }
            if has("pref_female") || has("pref_male") {
                SectionCard("Gender preference") {
                    VStack(alignment: .leading, spacing: 8) {
                        if has("pref_female") { MeterRow(label: "Woman", value: binding("pref_female"), signed: true, unit: 25, tint: .orange) }
                        if has("pref_male") { MeterRow(label: "Man", value: binding("pref_male"), signed: true, unit: 25, tint: .orange) }
                        Text("Negative is dislike. The game moves these a few points at a time, so the bar shows ±250.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            leftovers(except: ["zodiac", "pref_female", "pref_male"], prefixes: ["personality.", "genetic."])
        }
    }

    private func column(_ title: String, prefix: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.caption).fontWeight(.semibold).foregroundStyle(.secondary)
                .padding(.leading, 108)
            ForEach(Self.personalityOrder, id: \.self) { trait in
                if has(prefix + trait) {
                    MeterRow(label: trait, value: binding(prefix + trait))
                }
            }
        }
    }

    private func meters(_ title: String, prefix: String, columns: Int) -> some View {
        let rows = fields.filter { $0.name.hasPrefix(prefix) }
        return SectionCard(title) {
            if columns == 1 {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(rows) { f in MeterRow(label: f.label, value: binding(f.name)) }
                }
            } else {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), alignment: .leading), count: columns),
                          alignment: .leading, spacing: 8) {
                    ForEach(rows) { f in MeterRow(label: f.label, value: binding(f.name)) }
                }
            }
        }
    }

    private var career: some View {
        SectionCard("Career") {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(fields) { f in
                    FieldRow(f: f, meta: meta, draft: $draft)
                    if f.name == "career_level", let t = meta.careerTitle(guid: draft["career_guid"] ?? 0, level: draft["career_level"] ?? 0) {
                        titleNote(t)
                    }
                    if f.name == "retired_level", let t = meta.careerTitle(guid: draft["retired_guid"] ?? 0, level: draft["retired_level"] ?? 0) {
                        titleNote(t)
                    }
                }
            }
        }
    }

    private func titleNote(_ title: String) -> some View {
        Text(title).font(.caption).foregroundStyle(.secondary).padding(.leading, 4)
    }

    private var overview: some View {
        Group {
            generic("Identity", fields.filter { $0.kind == "id" || $0.name == "family_id" })
            generic("Sim", fields.filter { $0.kind != "id" && $0.name != "family_id" })
            if !detail.bio.isEmpty {
                SectionCard("In-game biography") { Text(detail.bio).textSelection(.enabled) }
            }
        }
    }

    /// Fields this tab claims but the custom layout did not place.
    private func leftovers(except names: [String], prefixes: [String]) -> some View {
        let rest = fields.filter { f in !names.contains(f.name) && !prefixes.contains { f.name.hasPrefix($0) } }
        return Group {
            if !rest.isEmpty { generic("More", rest) }
        }
    }

    private func generic(_ title: String, _ rows: [FieldDef]) -> some View {
        Group {
            if !rows.isEmpty {
                SectionCard(title) {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(rows) { f in FieldRow(f: f, meta: meta, draft: $draft) }
                    }
                }
            }
        }
    }
}

/// One SDSC field as a form row, by kind.
struct FieldRow: View {
    let f: FieldDef
    let meta: HoodMeta
    @Binding var draft: [String: Int]

    private var binding: Binding<Int> {
        Binding(get: { draft[f.name] ?? 0 }, set: { draft[f.name] = $0 })
    }

    var body: some View {
        switch true {
        case f.kind == "id":
            LabeledContent(f.label) {
                Text(f.name == "guid" ? hex8(UInt32(truncatingIfNeeded: binding.wrappedValue)) : "\(binding.wrappedValue)")
                    .font(.system(.body, design: .monospaced)).foregroundStyle(.secondary)
            }
        case f.kind == "meter":
            MeterRow(label: f.label, value: binding)
        case f.kind == "bool":
            Toggle(f.label, isOn: Binding(get: { binding.wrappedValue != 0 }, set: { binding.wrappedValue = $0 ? 1 : 0 }))
        case f.isEnum:
            LabeledContent(f.label) {
                let opts = meta.options(f.table ?? "", from: meta.sdscTables)
                Picker("", selection: binding) {
                    if !opts.contains(where: { $0.value == binding.wrappedValue }) {
                        Text(binding.wrappedValue == 0 ? "none" : String(format: "0x%X (unknown)", binding.wrappedValue))
                            .tag(binding.wrappedValue)
                    }
                    ForEach(opts, id: \.value) { o in Text(o.label).tag(o.value) }
                }
                .labelsHidden()
                .frame(maxWidth: 320)
            }
        case f.isFlags:
            LabeledContent(f.label) {
                let opts = meta.options(f.table ?? "", from: meta.sdscTables).sorted { $0.value < $1.value }
                FlowLayout(spacing: 8) {
                    ForEach(opts, id: \.value) { o in
                        Toggle(o.label, isOn: Binding(
                            get: { binding.wrappedValue & o.value != 0 },
                            set: { binding.wrappedValue = $0 ? binding.wrappedValue | o.value : binding.wrappedValue & ~o.value }))
                        .toggleStyle(.checkbox)
                    }
                }
            }
        default:
            LabeledContent(f.label) { NumberField(value: binding, width: 90) }
        }
    }
}
