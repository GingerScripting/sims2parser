import SwiftUI
import SimKit

extension Sim {
    /// Shared life-stage palette (list rows and tree boxes stay in step).
    var ageColor: Color {
        switch age {
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

struct FamilyTreeView: View {
    let root: Sim
    let hood: Hood
    /// Called when the user picks a sim, so the main list follows along.
    var onSelect: (Sim) -> Void
    var onClose: () -> Void

    @State private var ego: Sim
    @State private var zoom: CGFloat = 1

    init(root: Sim, hood: Hood, onSelect: @escaping (Sim) -> Void, onClose: @escaping () -> Void) {
        self.root = root
        self.hood = hood
        self.onSelect = onSelect
        self.onClose = onClose
        _ego = State(initialValue: root)
    }

    private var chart: FamilyTree.Chart { FamilyTree.chart(for: ego, in: hood) }

    var body: some View {
        VStack(spacing: 0) {
            toolbar
            Divider()
            if chart.isEmpty {
                emptyState
            } else {
                // The scrolling canvas lives in its own NSHostingView: on macOS 26
                // a ScrollView in a root hierarchy drags its siblings off-window,
                // which here would pull the toolbar above it off the sheet.
                IsolatedPane {
                    FamilyTreeCanvas(chart: chart, zoom: zoom, rootID: root.nid) { sim in
                        onSelect(sim)
                        if sim.nid != ego.nid { ego = sim }
                    }
                }
            }
        }
        .frame(minWidth: 900, idealWidth: 1180, minHeight: 560, idealHeight: 760)
    }

    // MARK: - Chrome

    private var toolbar: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 1) {
                Text("Family tree").font(.caption).foregroundStyle(.secondary).kerning(0.5)
                Text(ego.fullName).font(.title3).fontWeight(.semibold)
            }
            if ego.nid != root.nid {
                Button {
                    ego = root
                } label: {
                    Label("Back to \(root.fullName)", systemImage: "arrow.uturn.backward")
                }
                .controlSize(.small)
            }
            Spacer()
            legend
            HStack(spacing: 4) {
                Button { zoom = max(0.5, zoom - 0.1) } label: { Image(systemName: "minus.magnifyingglass") }
                    .disabled(zoom <= 0.5)
                Button { zoom = 1 } label: { Text("\(Int(zoom * 100))%").monospacedDigit().frame(width: 40) }
                    .help("Reset zoom")
                Button { zoom = min(1.5, zoom + 0.1) } label: { Image(systemName: "plus.magnifyingglass") }
                    .disabled(zoom >= 1.5)
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            Button("Done") { onClose() }
                .keyboardShortcut(.cancelAction)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
    }

    /// Explains the two ways a pair can be joined — without it a dashed line
    /// just reads as a rendering glitch.
    private var legend: some View {
        HStack(spacing: 14) {
            HStack(spacing: 5) {
                Capsule().fill(.pink.opacity(0.85)).frame(width: 16, height: 2)
                Text("married")
            }
            HStack(spacing: 5) {
                Line().stroke(.secondary.opacity(0.6),
                              style: StrokeStyle(lineWidth: 1.5, dash: [3, 3]))
                    .frame(width: 16, height: 2)
                Text("shared children, not married")
            }
            Text("· click any sim to re-centre")
        }
        .font(.caption)
        .foregroundStyle(.tertiary)
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "person.2.slash")
                .font(.system(size: 42)).foregroundStyle(.tertiary)
            Text("\(ego.fullName) has no recorded family ties")
                .font(.title3).foregroundStyle(.secondary)
            Text("Sims created in Create-a-Sim start with no parents, spouse, or children until they marry or have kids in game.")
                .font(.callout).foregroundStyle(.tertiary)
                .multilineTextAlignment(.center).frame(maxWidth: 420)
            if ego.nid != root.nid {
                Button("Back to \(root.fullName)") { ego = root }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

}

// MARK: - Canvas

/// The scrolling chart itself. Split out from `FamilyTreeView` so it can be
/// rendered and inspected without the surrounding chrome.
struct FamilyTreeCanvas: View {
    let chart: FamilyTree.Chart
    var zoom: CGFloat = 1
    let rootID: Int
    var onSelect: (Sim) -> Void

    var body: some View {
        ScrollView([.horizontal, .vertical]) {
            content
                .scaleEffect(zoom, anchor: .topLeading)
                .frame(width: chart.size.width * zoom,
                       height: chart.size.height * zoom,
                       alignment: .topLeading)
                .padding(20)
        }
    }

    /// Nodes are placed in absolute chart coordinates, which line up with the
    /// link layer because both are pinned to the same top-leading origin.
    var content: some View {
        ZStack(alignment: .topLeading) {
            linkLayer
            ForEach(chart.nodes) { node in
                TreeBox(node: node, isRoot: node.id == rootID) {
                    onSelect(node.sim)
                }
                .frame(width: node.frame.width, height: node.frame.height)
                .offset(x: node.frame.minX, y: node.frame.minY)
            }
        }
        .frame(width: chart.size.width, height: chart.size.height, alignment: .topLeading)
    }

    private var linkLayer: some View {
        Canvas { ctx, _ in
            for link in chart.links {
                var path = Path()
                path.move(to: link.from)
                path.addLine(to: link.to)
                switch link.kind {
                case .marriage:
                    ctx.stroke(path, with: .color(.pink.opacity(0.85)),
                               style: StrokeStyle(lineWidth: 2, lineCap: .round))
                case .coparent:
                    ctx.stroke(path, with: .color(.secondary.opacity(0.5)),
                               style: StrokeStyle(lineWidth: 1.5, lineCap: .round, dash: [3, 3]))
                case .descent:
                    ctx.stroke(path, with: .color(.secondary.opacity(0.45)),
                               style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
                }
            }
        }
        .frame(width: chart.size.width, height: chart.size.height)
        .allowsHitTesting(false)
    }
}

/// A single horizontal rule, for the dashed sample in the legend.
private struct Line: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.midY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        return p
    }
}

// MARK: - Node

fileprivate struct TreeBox: View {
    let node: FamilyTree.Node
    let isRoot: Bool
    var onTap: () -> Void

    @State private var hovering = false

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 8) {
                Capsule()
                    .fill(node.sim.ageColor)
                    .frame(width: 3)
                    .opacity(node.sim.isDead ? 0.4 : 1)
                VStack(alignment: .leading, spacing: 2) {
                    Text(node.sim.fullName)
                        .font(.system(size: 12, weight: node.isEgo ? .bold : .medium))
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Text(subtitle)
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 7)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .background(background)
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(borderColor, lineWidth: node.isEgo ? 2 : 1)
            )
            .contentShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .help("\(node.sim.fullName) — \(roleLabel). Click to centre the tree here.")
    }

    private var background: some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(node.isEgo ? Color.accentColor.opacity(0.14)
                             : Color(nsColor: .controlBackgroundColor))
            .shadow(color: .black.opacity(hovering ? 0.16 : 0.07),
                    radius: hovering ? 4 : 2, y: 1)
    }

    private var borderColor: Color {
        if node.isEgo { return .accentColor }
        if isRoot { return .accentColor.opacity(0.5) }
        return hovering ? .secondary.opacity(0.6) : .secondary.opacity(0.25)
    }

    private var subtitle: String {
        var parts: [String] = []
        if node.sim.isDead { parts.append("†") }
        parts.append(node.sim.age)
        if !node.sim.household.isEmpty { parts.append("\(node.sim.household)") }
        return parts.joined(separator: " · ")
    }

    private var roleLabel: String {
        switch node.role {
        case .ego: return "the sim this tree is centred on"
        case .spouse: return "spouse"
        case .parent: return "parent"
        case .grandparent: return "grandparent"
        case .sibling: return "sibling"
        case .siblingSpouse: return "sibling's spouse"
        case .child: return "child"
        case .childSpouse: return "child's spouse"
        case .grandchild: return "grandchild"
        case .grandchildSpouse: return "grandchild's spouse"
        }
    }
}
