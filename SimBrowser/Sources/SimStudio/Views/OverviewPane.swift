import SwiftUI
import SimKit
import SimStudioCore

/// What the package is, in words, with a way into the parts that matter:
/// the page a package opens on and the one the Overview button returns to.
/// The daemon composes it (`overview`); this only lays it out.
struct OverviewPane: View {
    @ObservedObject var session: PackageSession
    /// Select a resource in the table and detail pane, clearing any filter.
    var reveal: (TGI) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let o = session.overview {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(o.headline).font(.title2).fontWeight(.semibold)
                            .fixedSize(horizontal: false, vertical: true)
                        ForEach(o.notes, id: \.self) { n in
                            Label { Text(n).fixedSize(horizontal: false, vertical: true) }
                                  icon: { Image(systemName: "circle.fill").font(.system(size: 5)) }
                                .foregroundStyle(.secondary)
                        }
                    }
                    if !o.objects.isEmpty {
                        SectionCard(o.objects.count == 1 ? "The object" : "Objects (\(o.objects.count))") {
                            ForEach(o.objects) { obj in
                                objectCard(obj)
                                if obj.id != o.objects.last?.id { Divider() }
                            }
                        }
                    }
                    if !o.overrides.isEmpty {
                        SectionCard("Overrides — game resources this package replaces") {
                            ForEach(o.overrides) { g in overrideGroup(g) }
                        }
                    }
                    howTo
                } else {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Reading the package…").foregroundStyle(.secondary)
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task(id: session.editCount) { await session.loadOverview() }
    }

    private func objectCard(_ obj: OverviewObject) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(obj.name).font(.headline)
                if obj.price > 0 { Text("§\(obj.price)").foregroundStyle(.secondary) }
                Spacer()
                Text(String(format: "GUID 0x%08X", obj.guid))
                    .font(.system(.caption, design: .monospaced)).foregroundStyle(.secondary)
            }
            if !obj.description.isEmpty {
                Text(obj.description).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !obj.interactions.isEmpty {
                Text(obj.interactions.count == 1 ? "1 pie-menu entry"
                     : "\(obj.interactions.count) pie-menu entries")
                    .font(.caption).foregroundStyle(.secondary).padding(.top, 2)
                FlowLayout(spacing: 6) {
                    ForEach(Array(obj.interactions.prefix(24).enumerated()), id: \.offset) { _, name in
                        Text(name).font(.caption)
                            .padding(.horizontal, 7).padding(.vertical, 3)
                            .background(Color.accentColor.opacity(0.12), in: Capsule())
                    }
                    if obj.interactions.count > 24 {
                        Text("+\(obj.interactions.count - 24) more").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            HStack(spacing: 8) {
                Button("Object definition") { reveal(obj.tgi) }
                    .help("The OBJD: GUID, price, catalog placement")
                if let t = obj.ttab {
                    Button("Pie menu") { reveal(t) }
                        .help("The TTAB: which entries appear and which behaviour each runs")
                }
            }
            .controlSize(.small)
            .padding(.top, 2)
        }
        .padding(.vertical, 4)
    }

    private func overrideGroup(_ g: OverrideGroup) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("\(g.count) in \(g.label)").font(.subheadline).fontWeight(.medium)
            FlowLayout(spacing: 6) {
                ForEach(g.items) { item in
                    Button {
                        reveal(item.tgi)
                    } label: {
                        Text("\(item.typeName)  \(item.name ?? hex8(item.tgi.instance))")
                            .font(.caption).lineLimit(1)
                    }
                    .buttonStyle(.bordered).controlSize(.small)
                }
                if g.count > g.items.count {
                    Text("+\(g.count - g.items.count) more in the table").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 4)
    }

    private var howTo: some View {
        SectionCard("How this window works") {
            VStack(alignment: .leading, spacing: 6) {
                step("1", "The left column lists what kinds of parts the package holds; the middle lists the parts.")
                step("2", "Select a part to read it here. Decoded is a form, Tree is the code listing, Hex is the raw bytes.")
                step("3", "Change a field and press Apply (⌘↩). The change lives in memory; Undo takes it back.")
                step("4", "Save (⌘S) writes the file. A read-only file needs Save As to a copy somewhere else.")
            }
            .foregroundStyle(.secondary)
        }
    }

    private func step(_ n: String, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(n).font(.caption).monospacedDigit().frame(width: 14, alignment: .trailing)
            Text(text).fixedSize(horizontal: false, vertical: true)
        }
    }
}
