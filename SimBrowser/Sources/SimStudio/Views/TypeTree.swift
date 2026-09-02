import SwiftUI

/// The left column: every resource type in the package with its count, each
/// expandable into the groups it spans. Selecting a row filters the table.
struct TypeTree: View {
    let rows: [ResourceRow]
    @Binding var selection: TreeFilter

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
                                Spacer()
                                countBadge(g.count)
                            }
                            .tag(TreeFilter.typeGroup(node.id, g.id))
                        }
                    } label: {
                        HStack {
                            Text(node.name)
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
