import SwiftUI
import SimStudioCore

/// The left column: every resource type in the package with its count, each
/// expandable into the groups it spans. Selecting a row filters the table.
struct TypeTree: View {
    let rows: [ResourceRow]
    @Binding var selection: TreeFilter
    /// Plain-words description of a type id, from the daemon's meta tables.
    var describe: (UInt32) -> String? = { _ in nil }

    struct GroupNode: Identifiable {
        let id: UInt32
        let count: Int
    }

    struct TypeNode: Identifiable {
        let id: UInt32
        let name: String
        let count: Int
        let groups: [GroupNode]
    }

    @State private var nodes: [TypeNode] = []

    var body: some View {
        List(selection: $selection) {
            Label("Overview", systemImage: "info.circle")
                .tag(TreeFilter.overview)
            HStack {
                Label("All Resources", systemImage: "shippingbox")
                Spacer()
                countBadge(rows.count)
            }
            .tag(TreeFilter.all)

            Section("Types") {
                ForEach(nodes) { node in
                    DisclosureGroup {
                        ForEach(node.groups) { g in
                            HStack {
                                Text(hex8(g.id)).font(.system(.body, design: .monospaced))
                                if let label = groupLabel(g.id) {
                                    Text(label).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                countBadge(g.count)
                            }
                            .tag(TreeFilter.typeGroup(node.id, g.id))
                        }
                    } label: {
                        HStack(alignment: .firstTextBaseline) {
                            VStack(alignment: .leading, spacing: 1) {
                                Text(node.name)
                                if let d = describe(node.id) {
                                    Text(d).font(.caption).foregroundStyle(.secondary)
                                        .lineLimit(1).help(d)
                                }
                            }
                            Spacer()
                            countBadge(node.count)
                        }
                    }
                    .tag(TreeFilter.type(node.id))
                }
            }
        }
        .listStyle(.sidebar)
        .onAppear { rebuild() }
        .onChange(of: rows.count) { _ in rebuild() }
        .onChange(of: rows) { _ in rebuild() }
    }

    /// The two group ids every package uses; the rest are per-object.
    private func groupLabel(_ g: UInt32) -> String? {
        switch g {
        case 0xFFFFFFFF: return "this package"
        case 0x7FD46CD0: return "global"
        default: return nil
        }
    }

    private func countBadge(_ n: Int) -> some View {
        Text("\(n)")
            .font(.caption)
            .foregroundStyle(.secondary)
            .monospacedDigit()
    }

    /// Grouping 50,000 rows is cheap but not free; do it when rows change,
    /// not on every render.
    private func rebuild() {
        var byType: [UInt32: (name: String, groups: [UInt32: Int])] = [:]
        for r in rows {
            var entry = byType[r.type] ?? (name: r.typeName, groups: [:])
            entry.groups[r.group, default: 0] += 1
            byType[r.type] = entry
        }
        nodes = byType.map { type, entry in
            let groups = entry.groups.map { GroupNode(id: $0.key, count: $0.value) }
                .sorted { $0.id < $1.id }
            return TypeNode(id: type, name: entry.name,
                            count: groups.reduce(0) { $0 + $1.count }, groups: groups)
        }
        .sorted { $0.name.lowercased() < $1.name.lowercased() }
    }
}
